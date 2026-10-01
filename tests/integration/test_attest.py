"""Integration (Spec 0124): Attestierung gegen echtes Postgres + MinIO.

AC1 (die DB erzwingt die Hash-Gleichheit), AC2 (append-only + UNIQUE je
``(source_id, archiver)``), AC3 (abgeleitete Auswahl, stabil, mit limit),
AC12 (Ende-zu-Ende: genau eine Zeile bei nachgewiesener SHA-256-Gleichheit,
Idempotenz des zweiten Laufs).

Die Quellen entstehen wie in ``test_reparse.py`` per ``ingest_source`` mit
Fixture-Adaptern — ``fetch`` bleibt hermetisch (R-TEST-03); der Lookup ist ein
Fake, der die Fixture-Bytes liefert.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from wortlaut.archive.wayback_lookup import SnapshotCandidate, snapshot_url
from wortlaut.evidence.hashing import content_hash
from wortlaut.ingest.adapter import RawSource, SourceRef, SpanDraft
from wortlaut.ingest.dip import DipPlenarprotokollAdapter
from wortlaut.ingest.settings import DipSettings
from wortlaut.pipeline.attest import attest_source
from wortlaut.pipeline.ingest import IngestOutcome, PipelineDeps, ingest_source
from wortlaut.store.attestations import list_sources_without_attestation
from wortlaut.store.migrations import upgrade_head
from wortlaut.store.worm import WormStore

pytestmark = pytest.mark.integration

_FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "dip" / "plenarprotokoll_zweispaltig.pdf"
)
_ORIGIN = "https://dserver.bundestag.de/btp/21/21042/2104200.pdf"
_SNAPSHOT_TS = "20260805170741"
_SNAPSHOT_AT = datetime(2026, 8, 5, 17, 7, 41, tzinfo=UTC)


def _dip_settings() -> DipSettings:
    return DipSettings(
        api_key="test-key",
        api_base_url="https://search.dip.bundestag.de/api/v1",
        pdf_host="dserver.bundestag.de",
    )


class _FixtureDipAdapter(DipPlenarprotokollAdapter):
    """Realer DIP-Adapter (echtes normalize/parse aus #41), nur ``fetch`` liefert
    die Fixture-Bytes — kein Netz."""

    def __init__(self, raw: RawSource) -> None:
        super().__init__(_dip_settings())
        self._fixture = raw

    async def fetch(self, ref: SourceRef) -> RawSource:
        return self._fixture


class _EmptyDipAdapter(_FixtureDipAdapter):
    """Wie ``_FixtureDipAdapter``, aber ``parse`` liefert ``[]``."""

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        return []


class _OkArchiver:
    """Archiver-Fake: liefert eine feste Snapshot-URL (kein Live-Call)."""

    def __init__(self, url: str) -> None:
        self._url = url

    async def archive(self, origin_url: str) -> str:
        return self._url


class _FakeLookup:
    """WaybackLookup-Fake: liefert die Fixture-Bytes (kein Live-Call, R-TEST-03)."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self.candidate = SnapshotCandidate(_SNAPSHOT_TS, _ORIGIN)
        self.candidates_calls = 0
        self.fetch_calls = 0

    async def candidates(self, origin_url: str, *, sha1_b32: str) -> list[SnapshotCandidate]:
        self.candidates_calls += 1
        return [self.candidate]

    async def fetch(self, candidate: SnapshotCandidate) -> bytes | None:
        self.fetch_calls += 1
        return self._data

    async def aclose(self) -> None:
        pass


def _raw(raw_bytes: bytes, origin: str) -> RawSource:
    return RawSource(
        origin_url=origin,
        source_type="plenarprotokoll",
        raw_bytes=raw_bytes,
        mime_type="application/pdf",
        retrieved_at=datetime.now(UTC),
    )


async def _fresh(dsn: str) -> tuple[async_sessionmaker[AsyncSession], AsyncEngine]:
    """Migrierte, frische DB: Sessionmaker + Engine (Dispose obliegt dem Aufrufer)."""
    from wortlaut.store.db import create_async_engine_from, make_sessionmaker
    from wortlaut.store.settings import DbSettings

    await upgrade_head(dsn)
    engine = create_async_engine_from(DbSettings(dsn=dsn))
    return make_sessionmaker(engine), engine


async def _seed_adapter(session: AsyncSession, name: str) -> None:
    await session.execute(
        text(
            "INSERT INTO ingest_adapter (name, version, trust_level) "
            "VALUES (:n, :v, CAST(:t AS trust_level)) ON CONFLICT (name, version) DO NOTHING"
        ),
        {"n": name, "v": "1.0.0", "t": "verified_primary"},
    )
    await session.commit()


async def _ingest(
    sessions: async_sessionmaker[AsyncSession],
    worm: WormStore,
    raw_bytes: bytes,
    *,
    origin: str,
    with_spans: bool = False,
) -> IngestOutcome:
    """Quelle per ``ingest_source`` mit Fixture-Adaptern (Muster ``test_reparse``)."""
    adapter_cls = _FixtureDipAdapter if with_spans else _EmptyDipAdapter
    adapter = adapter_cls(_raw(raw_bytes, origin))
    deps = PipelineDeps(
        adapter=adapter,
        wayback=_OkArchiver("https://web.archive.org/snap"),
        archive_today=_OkArchiver("https://archive.ph/snap"),
        worm=worm,
    )
    ref = SourceRef(origin_url=origin, source_type="plenarprotokoll", hint={})
    # SSRF-Check gemockt: keine echte DNS-Auflösung im Test (R-TEST-03, hermetisch).
    with patch("wortlaut.archive.archiver.assert_url_allowed"):
        async with sessions() as session:
            await _seed_adapter(session, adapter.name)
            return await ingest_source(
                ref,
                deps=deps,
                session=session,
                rights_basis="amtliches_werk_p5",
            )


def _archive_params(source_id: str, origin: str, verified_sha256: str) -> dict[str, object]:
    return {
        "s": source_id,
        "u": f"https://web.archive.org/web/{_SNAPSHOT_TS}/{origin}",
        "t": _SNAPSHOT_AT,
        "h": verified_sha256,
    }


_ARCHIVE_INSERT = (
    "INSERT INTO source_archive "
    "(source_id, archiver, snapshot_url, snapshot_at, verified_sha256) "
    "VALUES (:s, 'wayback', :u, :t, :h)"
)


# ── AC1: die Datenbank erzwingt die Hash-Gleichheit ──────────────────────


async def test_insert_requires_equal_hash(fresh_pg_dsn: str, worm_store: WormStore) -> None:
    """AC1: direkter Insert mit ``verified_sha256 != content_hash`` wird von der
    DB verweigert; mit gleichem Hash gelingt er."""
    raw = b"ac1 rohbytes der quelle"
    origin = "https://dserver.bundestag.de/ac1.pdf"
    sessions, engine = await _fresh(fresh_pg_dsn)
    try:
        outcome = await _ingest(sessions, worm_store, raw, origin=origin)
        assert outcome.status == "inserted"
        source_id = outcome.source_id
        assert source_id is not None
        digest = content_hash(raw)

        insert_sql = text(_ARCHIVE_INSERT)
        hash_error = "verified_sha256 passt nicht zu source.content_hash"
        wrong = _archive_params(str(source_id), origin, "0" * 64)
        async with sessions() as session:
            with pytest.raises(DBAPIError, match=hash_error):
                await session.execute(insert_sql, wrong)
            await session.rollback()

        ok = _archive_params(str(source_id), origin, digest)
        async with sessions() as session:
            await session.execute(insert_sql, ok)
            await session.commit()

        async with sessions() as session:
            count = await session.scalar(
                text("SELECT count(*) FROM source_archive WHERE source_id = :s"),
                {"s": str(source_id)},
            )
    finally:
        await engine.dispose()
    assert count == 1


# ── AC2: append-only (UPDATE/DELETE scheitern am Trigger) ────────────────


async def test_source_archive_is_append_only(fresh_pg_dsn: str, worm_store: WormStore) -> None:
    """AC2: UPDATE und DELETE auf ``source_archive`` scheitern am
    Immutability-Trigger; die Zeile bleibt unverändert."""
    raw = b"ac2 rohbytes der quelle"
    origin = "https://dserver.bundestag.de/ac2.pdf"
    sessions, engine = await _fresh(fresh_pg_dsn)
    try:
        outcome = await _ingest(sessions, worm_store, raw, origin=origin)
        source_id = outcome.source_id
        assert source_id is not None

        insert_sql = text(_ARCHIVE_INSERT)
        params = _archive_params(str(source_id), origin, content_hash(raw))
        async with sessions() as session:
            await session.execute(insert_sql, params)
            await session.commit()

        update_sql = text("UPDATE source_archive SET snapshot_url = :u WHERE source_id = :s")
        delete_sql = text("DELETE FROM source_archive WHERE source_id = :s")
        update_params = {"s": str(source_id), "u": "x"}
        delete_params = {"s": str(source_id)}
        async with sessions() as session:
            with pytest.raises(DBAPIError, match="append-only"):
                await session.execute(update_sql, update_params)
            await session.rollback()
            with pytest.raises(DBAPIError, match="append-only"):
                await session.execute(delete_sql, delete_params)
            await session.rollback()

        async with sessions() as session:
            count = await session.scalar(
                text("SELECT count(*) FROM source_archive WHERE source_id = :s"),
                {"s": str(source_id)},
            )
    finally:
        await engine.dispose()
    assert count == 1


# ── AC2: UNIQUE je (source_id, archiver) ─────────────────────────────────


async def test_unique_per_archiver(fresh_pg_dsn: str, worm_store: WormStore) -> None:
    """AC2: ein zweiter Insert für dieselbe ``(source_id, archiver)`` scheitert
    an UNIQUE, auch wenn die Hash-Gleichheit stimmt."""
    raw = b"ac2b rohbytes der quelle"
    origin = "https://dserver.bundestag.de/ac2b.pdf"
    sessions, engine = await _fresh(fresh_pg_dsn)
    try:
        outcome = await _ingest(sessions, worm_store, raw, origin=origin)
        source_id = outcome.source_id
        assert source_id is not None

        insert_sql = text(_ARCHIVE_INSERT)
        params = _archive_params(str(source_id), origin, content_hash(raw))
        async with sessions() as session:
            await session.execute(insert_sql, params)
            await session.commit()
            with pytest.raises(IntegrityError):
                await session.execute(insert_sql, params)
            await session.rollback()

        async with sessions() as session:
            count = await session.scalar(
                text("SELECT count(*) FROM source_archive WHERE source_id = :s"),
                {"s": str(source_id)},
            )
    finally:
        await engine.dispose()
    assert count == 1


# ── AC3: Auswahl — abgeleitet, stabil, mit limit ─────────────────────────


async def test_list_sources_without_attestation(fresh_pg_dsn: str, worm_store: WormStore) -> None:
    """AC3: liefert genau die Quellen ohne ``source_archive``-Zeile, stabil nach
    ``created_at, id``, mit ``limit``."""
    sessions, engine = await _fresh(fresh_pg_dsn)
    try:
        a = await _ingest(
            sessions, worm_store, b"ac3 quelle a", origin="https://dserver.bundestag.de/ac3-a.pdf"
        )
        b = await _ingest(
            sessions, worm_store, b"ac3 quelle b", origin="https://dserver.bundestag.de/ac3-b.pdf"
        )
        c = await _ingest(
            sessions, worm_store, b"ac3 quelle c", origin="https://dserver.bundestag.de/ac3-c.pdf"
        )
        assert a.status == "inserted" and b.status == "inserted" and c.status == "inserted"
        assert a.source_id is not None and b.source_id is not None and c.source_id is not None

        async with sessions() as session:
            pending = await list_sources_without_attestation(session)
        assert [p.source_id for p in pending] == [a.source_id, b.source_id, c.source_id]

        lookup = _FakeLookup(b"ac3 quelle a")
        async with sessions() as session:
            attested = await attest_source(
                pending[0], session=session, worm=worm_store, lookup=lookup
            )
        assert attested.status == "attested"

        async with sessions() as session:
            remaining = await list_sources_without_attestation(session)
        assert [p.source_id for p in remaining] == [b.source_id, c.source_id]

        async with sessions() as session:
            limited = await list_sources_without_attestation(session, limit=1)
    finally:
        await engine.dispose()
    assert [p.source_id for p in limited] == [b.source_id]


# ── AC12: Ende-zu-Ende + Idempotenz ──────────────────────────────────────


async def test_attest_end_to_end_and_idempotent(fresh_pg_dsn: str, worm_store: WormStore) -> None:
    """AC12: erfasste Quelle + Fake-Lookup mit den Fixture-Bytes → genau eine
    ``source_archive``-Zeile mit ``verified_sha256 = content_hash``; die Quelle
    verschwindet aus der Auswahl; ein zweiter Lauf schreibt nichts."""
    fixture = _FIXTURE.read_bytes()
    sessions, engine = await _fresh(fresh_pg_dsn)
    try:
        outcome = await _ingest(sessions, worm_store, fixture, origin=_ORIGIN, with_spans=True)
        assert outcome.status == "inserted"
        source_id = outcome.source_id
        assert source_id is not None
        digest = content_hash(fixture)

        async with sessions() as session:
            pending = await list_sources_without_attestation(session)
        assert [p.source_id for p in pending] == [source_id]

        lookup = _FakeLookup(fixture)
        async with sessions() as session:
            att = await attest_source(pending[0], session=session, worm=worm_store, lookup=lookup)
        assert att.status == "attested"
        assert att.snapshot_url == snapshot_url(lookup.candidate)
        assert lookup.candidates_calls == 1
        assert lookup.fetch_calls == 1

        async with sessions() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT archiver, snapshot_url, snapshot_at, verified_sha256 "
                        "FROM source_archive WHERE source_id = :s"
                    ),
                    {"s": str(source_id)},
                )
            ).all()
        assert len(rows) == 1
        assert rows[0].archiver == "wayback"
        assert rows[0].snapshot_url == snapshot_url(lookup.candidate)
        assert rows[0].snapshot_at == _SNAPSHOT_AT
        assert rows[0].verified_sha256 == digest

        async with sessions() as session:
            assert await list_sources_without_attestation(session) == []

        # Zweiter Lauf: die Quelle ist nicht mehr ausgewählt; ein direkter
        # Doppel-Insert (UNIQUE-Race) schreibt nichts Neues.
        async with sessions() as session:
            second = await attest_source(
                pending[0], session=session, worm=worm_store, lookup=lookup
            )
        assert second.status == "attested"

        async with sessions() as session:
            count = await session.scalar(
                text("SELECT count(*) FROM source_archive WHERE source_id = :s"),
                {"s": str(source_id)},
            )
    finally:
        await engine.dispose()
    assert count == 1
