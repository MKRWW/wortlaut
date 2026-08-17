"""Integration (Spec 0076, AC11-AC15): Stempel-Pass + Verify gegen echtes Postgres/MinIO.

Der Trick, der den ``ok``-Fall überhaupt aussagekräftig macht: die Quelle bekommt als
Rohbytes **genau** den Inhalt von ``tests/fixtures/tsa/message.bin``. Damit ist
``source.content_hash`` byte-identisch mit dem ``messageImprint`` der echten
Fixture-Tokens — die Bindung Token↔Quelle wird also gegen ein **reales**
TSA-Token geprüft, ohne je das Netz anzufassen (R-TEST-03).

⚠️ ``source.content_hash`` ist UNIQUE und der Postgres-Container ist session-weit
geteilt — die Fixture-Bytes dürfen deshalb **genau einmal** im ganzen Modul als
Quelle angelegt werden (im ``ok``-Fall unten). Alle übrigen Fälle brauchen keine
echte Bindung und bekommen eigene Bytes; sonst scheitert der zweite Test an
``source_content_hash_key``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from wortlaut.evidence.hashing import content_hash
from wortlaut.pipeline.timestamp import timestamp_source
from wortlaut.pipeline.verify import verify_source
from wortlaut.store.migrations import upgrade_head
from wortlaut.store.sources import NewSource, insert_source
from wortlaut.store.timestamps import (
    NewSourceTimestamp,
    insert_source_timestamp,
    list_sources_without_timestamp,
)
from wortlaut.store.worm import WormStore
from wortlaut.timestamp.tsa import StampResult

pytestmark = pytest.mark.integration

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "tsa"


def _fixture_token(name: str) -> bytes:
    return (_FIXTURES / f"{name}.tsr").read_bytes()


def _message() -> bytes:
    return (_FIXTURES / "message.bin").read_bytes()


class _FakeStamper:
    """Liefert ein ECHTES Fixture-Token zurück — ohne Netz (R-TEST-03)."""

    def __init__(self, token: bytes, *, tsa_name: str = "freetsa") -> None:
        self._token = token
        self._tsa_name = tsa_name
        self.calls = 0
        self.failures: tuple[str, ...] = ()

    async def stamp(self, raw: bytes, *, content_hash: str) -> StampResult:
        self.calls += 1
        return StampResult(self._tsa_name, self._token, datetime.now(UTC))


async def _seed_source(
    sessions: async_sessionmaker[AsyncSession],
    worm: WormStore,
    raw: bytes,
    *,
    origin: str,
) -> tuple[UUID, str]:
    """Legt eine source mit WORM-Objekt an; liefert (source_id, content_hash)."""
    digest = content_hash(raw)
    ref = await worm.put(digest, raw, content_type="application/pdf")
    async with sessions() as session:
        await session.execute(
            text(
                "INSERT INTO ingest_adapter (name, version, trust_level) "
                "VALUES (:n, :v, CAST(:t AS trust_level)) ON CONFLICT (name, version) DO NOTHING"
            ),
            {"n": "dip-api", "v": "1.0.0", "t": "verified_primary"},
        )
        await session.commit()
        source_id = await insert_source(
            session,
            NewSource(
                content_hash=digest,
                raw_bytes_ref=ref,
                archive_wayback="https://web.archive.org/snap",
                archive_today=None,
                origin_url=origin,
                source_type="plenarprotokoll",
                rights_basis="amtliches_werk_p5",
                adapter_name="dip-api",
                adapter_version="1.0.0",
                byte_size=len(raw),
                mime_type="application/pdf",
                retrieved_at=datetime.now(UTC),
            ),
        )
    return source_id, digest


async def test_stamp_persists_and_is_idempotent(
    pg_dsn: str,
    db_engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
    worm_store: WormStore,
) -> None:
    """AC11 + AC12: Token landet in WORM + genau einer Zeile; zweiter Lauf stempelt nicht erneut."""
    await upgrade_head(pg_dsn)
    # Eigene Bytes: dieser Test prüft Persistenz/Idempotenz, nicht die Token-Bindung
    # (die prüft der ok-Fall unten mit den Fixture-Bytes).
    raw = b"ac11 rohbytes fuer persistenz und idempotenz"
    source_id, digest = await _seed_source(
        sessions, worm_store, raw, origin="https://dserver.bundestag.de/ac11.pdf"
    )

    # vor dem Stempeln: die Quelle ist "pending" (abgeleitet, kein Flag)
    async with sessions() as session:
        pending = await list_sources_without_timestamp(session)
    assert source_id in [p.source_id for p in pending]
    target = next(p for p in pending if p.source_id == source_id)

    stamper = _FakeStamper(_fixture_token("freetsa"))
    async with sessions() as session:
        outcome = await timestamp_source(target, session=session, worm=worm_store, stamper=stamper)

    # AC11: gestempelt, Token als WORM-Objekt unter {content_hash}.{tsa}.tsr
    assert outcome.status == "stamped"
    assert outcome.tsa_name == "freetsa"
    async with sessions() as session:
        rows = (
            await session.execute(
                text("SELECT tsa_name, token_ref FROM source_timestamp WHERE source_id = :s"),
                {"s": str(source_id)},
            )
        ).all()
    assert len(rows) == 1
    assert rows[0].tsa_name == "freetsa"
    assert f"{digest}.freetsa.tsr" in rows[0].token_ref
    assert await worm_store.get(rows[0].token_ref) == _fixture_token("freetsa")

    # AC12: zweiter Lauf findet die Quelle nicht mehr als pending, kein TSA-Call
    async with sessions() as session:
        pending_again = await list_sources_without_timestamp(session)
    assert source_id not in [p.source_id for p in pending_again]
    assert stamper.calls == 1


async def test_verify_reports_ok_mismatch_and_missing(
    pg_dsn: str,
    db_engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
    worm_store: WormStore,
) -> None:
    """AC13 + AC14: ok / mismatch / missing — und ein fehlender Stempel degradiert NICHTS."""
    await upgrade_head(pg_dsn)

    # (a) Quelle, deren Rohbytes zum Fixture-Token passen → timestamp_status "ok"
    raw_ok = _message()
    id_ok, digest_ok = await _seed_source(
        sessions, worm_store, raw_ok, origin="https://dserver.bundestag.de/ac13-ok.pdf"
    )
    ref_ok = await worm_store.put(
        f"{digest_ok}.freetsa.tsr",
        _fixture_token("freetsa"),
        content_type="application/timestamp-reply",
    )
    async with sessions() as session:
        await insert_source_timestamp(
            session, NewSourceTimestamp(source_id=id_ok, tsa_name="freetsa", token_ref=ref_ok)
        )

    # (b) Quelle mit ANDEREN Bytes, der dasselbe Token untergeschoben wird → "mismatch"
    raw_other = b"voellig andere rohbytes - das Token bindet hier nicht"
    id_bad, digest_bad = await _seed_source(
        sessions, worm_store, raw_other, origin="https://dserver.bundestag.de/ac13-bad.pdf"
    )
    ref_bad = await worm_store.put(
        f"{digest_bad}.freetsa.tsr",
        _fixture_token("freetsa"),
        content_type="application/timestamp-reply",
    )
    async with sessions() as session:
        await insert_source_timestamp(
            session, NewSourceTimestamp(source_id=id_bad, tsa_name="freetsa", token_ref=ref_bad)
        )

    # (c) Quelle ganz ohne Stempel → "missing"
    id_none, _ = await _seed_source(
        sessions,
        worm_store,
        b"ungestempelte quelle",
        origin="https://dserver.bundestag.de/ac14.pdf",
    )

    # (d) Zeile existiert, WORM-Token NICHT lesbar → "unreadable", NICHT "missing".
    #     Sonst wäre der zerstörte Nachweis von „nie gestempelt" ununterscheidbar —
    #     und die Quelle käme nie wieder in den Pass (Zeile existiert ja).
    id_gone, _ = await _seed_source(
        sessions,
        worm_store,
        b"quelle mit zerstoertem nachweis",
        origin="https://dserver.bundestag.de/ac13-gone.pdf",
    )
    async with sessions() as session:
        await insert_source_timestamp(
            session,
            NewSourceTimestamp(
                source_id=id_gone,
                tsa_name="freetsa",
                token_ref="s3://wortlaut-worm/gibtsnicht.freetsa.tsr?versionId=weg",
            ),
        )

    async with sessions() as session:
        report_ok = await verify_source(id_ok, session=session, worm=worm_store)
        report_bad = await verify_source(id_bad, session=session, worm=worm_store)
        report_none = await verify_source(id_none, session=session, worm=worm_store)
        report_gone = await verify_source(id_gone, session=session, worm=worm_store)

    # AC13: die drei Zustände sind unterscheidbar, gen_time NUR im ok-Fall
    assert report_ok.timestamp_status == "ok"
    assert report_ok.timestamp_tsa == "freetsa"
    assert report_ok.timestamp_gen_time is not None

    assert report_bad.timestamp_status == "mismatch"
    assert report_bad.timestamp_gen_time is None

    assert report_none.timestamp_status == "missing"
    assert report_none.timestamp_tsa is None
    assert report_none.timestamp_gen_time is None

    # (d) zerstörter Nachweis ist NICHT dasselbe wie "nie gestempelt"
    assert report_gone.timestamp_status == "unreadable"
    assert report_gone.timestamp_tsa == "freetsa"  # welche TSA es war, bleibt sichtbar
    assert report_gone.timestamp_gen_time is None

    # AC14 (kein Gate): der Hash stimmt in ALLEN vier Fällen — ok/status bleiben unberührt,
    # auch dort, wo der Zeitstempel fehlt, nicht bindet oder unlesbar ist.
    for report in (report_ok, report_bad, report_none, report_gone):
        assert report.ok is True
        assert report.status == "ok"


async def test_source_timestamp_is_append_only(
    pg_dsn: str,
    db_engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
    worm_store: WormStore,
) -> None:
    """AC15 (R-DATA-01): UPDATE und DELETE auf source_timestamp scheitern am Trigger."""
    await upgrade_head(pg_dsn)
    raw = b"ac15 rohbytes fuer den append-only-trigger"
    source_id, digest = await _seed_source(
        sessions, worm_store, raw, origin="https://dserver.bundestag.de/ac15.pdf"
    )
    ref = await worm_store.put(
        f"{digest}.freetsa.tsr",
        _fixture_token("freetsa"),
        content_type="application/timestamp-reply",
    )
    async with sessions() as session:
        await insert_source_timestamp(
            session, NewSourceTimestamp(source_id=source_id, tsa_name="freetsa", token_ref=ref)
        )

    # Der BEFORE-Trigger feuert schon beim execute — genau ein werfender Aufruf
    # steht im raises-Block (Sonar S5778).
    for statement in (
        "UPDATE source_timestamp SET token_ref = 's3://gefaelscht' WHERE source_id = :s",
        "DELETE FROM source_timestamp WHERE source_id = :s",
    ):
        sql = text(statement)
        params = {"s": str(source_id)}
        async with sessions() as session:
            with pytest.raises(Exception, match="append-only"):
                await session.execute(sql, params)
            await session.rollback()
