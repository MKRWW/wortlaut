"""Integration: CLI `ingest` end-to-end gegen echtes Postgres + MinIO (AC9/AC10).

Archiver + DIP-Adapter sind Fakes (R-TEST-03 — kein Live-Netz); Postgres und MinIO
sind echte Testcontainer. Prueft die CLI-Verdrahtung: Bootstrap (migrate + bucket +
adapter-seed) -> discover -> ingest_source -> source + span + WORM + verify.
"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from unittest.mock import patch

import pytest
from sqlalchemy import text

from wortlaut.archive.errors import ArchiveError
from wortlaut.cli import _run
from wortlaut.ingest.adapter import RawSource, SourceRef, SpanDraft
from wortlaut.pipeline.verify import verify_source
from wortlaut.store.adapters import ensure_ingest_adapter
from wortlaut.store.db import create_async_engine_from, make_sessionmaker
from wortlaut.store.migrations import upgrade_head
from wortlaut.store.settings import DbSettings, WormSettings
from wortlaut.store.worm import MinioWormStore

pytestmark = pytest.mark.integration

# ADR-0006: digest-gepinnt (repository@sha256, ohne Tag).
MINIO_IMAGE = "minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e"

_NORMALIZED = "Guten Tag."


class _FakeArchiver:
    """Archiver-Fake (kein Live-Netz).

    Nimmt beliebige Konstruktor-Argumente an, weil der Composition-Root den echten
    Archivern Limiter/Retry-Parameter injiziert (#73).
    """

    def __init__(self, *_a: object, **_kw: object) -> None:
        pass

    async def archive(self, origin_url: str) -> str:
        return "https://web.archive.org/snap"

    async def aclose(self) -> None:
        pass


class _FakeCliAdapter:
    """DIP-Adapter-Ersatz: discover -> 1 Ref, fetch -> RawSource, parse -> 1 valider Span."""

    name = "cli-int-adapter"
    version = "1.0.0"
    trust_level = "verified_primary"

    def __init__(self, *_a: object, **_kw: object) -> None:
        pass

    async def discover(self, since: datetime) -> Sequence[SourceRef]:
        return [SourceRef("https://example.com/p1", "rede", {})]

    async def fetch(self, ref: SourceRef) -> RawSource:
        return RawSource(
            origin_url="https://example.com/p1",
            source_type="rede",
            raw_bytes=b"%PDF-1.4 wortlaut-cli-int",
            mime_type="application/pdf",
            retrieved_at=datetime(2024, 1, 15),
        )

    def normalize(self, raw: RawSource) -> str:
        return _NORMALIZED

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        return [
            SpanDraft(
                verbatim_text=_NORMALIZED,
                text_start=0,
                text_end=len(_NORMALIZED),
                speaker_hint={"name": "Test Redner", "party": "X"},
                spoken_at="2024-01-15",
                locator={"tagesordnungspunkt": "TOP 1"},
                permalink="https://example.com/p1#s1",
            )
        ]

    async def aclose(self) -> None:
        pass


@pytest.fixture
def minio_config() -> Iterator[dict[str, str]]:
    from testcontainers.minio import MinioContainer

    with MinioContainer(MINIO_IMAGE) as c:
        yield c.get_config()


def _set_env(monkeypatch: pytest.MonkeyPatch, dsn: str, cfg: dict[str, str]) -> None:
    monkeypatch.setenv("WORTLAUT_DB_DSN", dsn)
    monkeypatch.setenv("WORTLAUT_WORM_ENDPOINT", cfg["endpoint"])
    monkeypatch.setenv("WORTLAUT_WORM_ACCESS_KEY", cfg["access_key"])
    monkeypatch.setenv("WORTLAUT_WORM_SECRET_KEY", cfg["secret_key"])
    monkeypatch.setenv("WORTLAUT_WORM_BUCKET", "wortlaut-worm")
    monkeypatch.setenv("WORTLAUT_WORM_SECURE", "false")
    monkeypatch.setenv("WORTLAUT_DIP_API_KEY", "dummy-key")


def _ingest_args(*, no_preflight: bool = False) -> Namespace:
    """Wie argparse es liefert — inklusive ``no_preflight`` (#77).

    Default ist ``False``: der Pre-Flight-Probe läuft mit, damit der
    End-to-End-Test belegt, dass er einen gesunden Lauf nicht stört.
    """
    return Namespace(
        since=datetime(2024, 1, 1),
        rights_basis="amtliches_werk_p5",
        limit=None,
        no_migrate=False,  # CLI migriert die frische DB selbst (Bootstrap-Test)
        dry_run=False,
        no_preflight=no_preflight,
    )


async def test_end_to_end_single_source(
    fresh_pg_dsn: str,
    minio_config: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC9: CLI ingest -> 1 source + >=1 span, verify=ok, WORM haelt die Rohbytes."""
    _set_env(monkeypatch, fresh_pg_dsn, minio_config)

    with (
        patch("wortlaut.cli.DipPlenarprotokollAdapter", _FakeCliAdapter),
        patch("wortlaut.cli.WaybackArchiver", _FakeArchiver),
        patch("wortlaut.cli.ArchiveTodayArchiver", _FakeArchiver),
    ):
        rc = await _run(_ingest_args())

    assert rc == 0

    # Verifikation ueber eine eigene Session/WORM-Instanz gegen dieselbe DB/MinIO.
    engine = create_async_engine_from(DbSettings(dsn=fresh_pg_dsn))
    worm = MinioWormStore(
        WormSettings(
            endpoint=minio_config["endpoint"],
            access_key=minio_config["access_key"],
            secret_key=minio_config["secret_key"],
            bucket="wortlaut-worm",
            secure=False,
        )
    )
    try:
        sessions = make_sessionmaker(engine)
        async with sessions() as session:
            row = (await session.execute(text("SELECT id, raw_bytes_ref FROM source"))).one()
            source_id, raw_ref = row[0], row[1]
            span_count = await session.scalar(
                text("SELECT count(*) FROM span WHERE source_id = :s"),
                {"s": source_id},
            )
            assert span_count is not None
            assert int(span_count) >= 1

            report = await verify_source(source_id, session=session, worm=worm)
            assert report.ok
            assert report.status == "ok"

        assert await worm.get(raw_ref) == b"%PDF-1.4 wortlaut-cli-int"
    finally:
        await engine.dispose()


@dataclass
class _WaybackState:
    """Test-LOKALER Zustand des Wayback-Fakes — kein globaler/Klassen-Zustand.

    Klassenattribute wuerden zwischen Tests lecken, wenn ein Test mittendrin
    fehlschlaegt und das Zuruecksetzen nie erreicht wird.
    """

    fail: bool = True
    calls: int = 0


class _ControllableWayback:
    """Wayback-Fake, dessen Verhalten der Test ueber sein `state`-Objekt steuert (#73/AC10)."""

    def __init__(self, state: _WaybackState) -> None:
        self._state = state

    async def archive(self, origin_url: str) -> str:
        self._state.calls += 1
        if self._state.fail:
            raise ArchiveError("wayback", "http_status", status_code=503, transient=True)
        return "https://web.archive.org/snap-0073-resume"

    async def aclose(self) -> None:
        pass


async def test_archive_failed_retried_on_rerun(
    fresh_pg_dsn: str,
    minio_config: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#73/AC10 (Resumability): archive_failed wird nachgeholt, Erfolg danach deduped.

    Lauf 1 (Wayback aus)  -> archive_failed, 0 Zeilen.
    Lauf 2 (Wayback da)   -> inserted, 1 Zeile.
    Lauf 3 (Wayback da)   -> skipped_duplicate, OHNE erneuten Archiv-Call
                             (Dedup greift vor der Archivierung).
    """
    _set_env(monkeypatch, fresh_pg_dsn, minio_config)
    state = _WaybackState(fail=True)

    async def _run_once() -> int:
        with (
            patch("wortlaut.cli.DipPlenarprotokollAdapter", _FakeCliAdapter),
            patch("wortlaut.cli.WaybackArchiver", return_value=_ControllableWayback(state)),
            patch("wortlaut.cli.ArchiveTodayArchiver", _FakeArchiver),
        ):
            # Pre-Flight aus (#77): Lauf 1 fährt ABSICHTLICH mit totem Wayback, um
            # Resumability zu beweisen. Genau diesen Lauf würde der Pre-Flight in
            # Produktion (korrekt) schon vorher abbrechen — bliebe er an, prüfte
            # dieser Test die Schleife darunter nie wieder. Das Endergebnis ist
            # identisch: 0 Zeilen, Nachholen im nächsten Lauf.
            return await _run(_ingest_args(no_preflight=True))

    engine = create_async_engine_from(DbSettings(dsn=fresh_pg_dsn))
    try:
        sessions = make_sessionmaker(engine)

        async def _source_count() -> int:
            async with sessions() as session:
                value = await session.scalar(text("SELECT count(*) FROM source"))
                assert value is not None
                return int(value)

        # Lauf 1 — Archiv aus: die Quelle darf NICHT gespeichert werden.
        assert await _run_once() == 0
        assert await _source_count() == 0
        assert state.calls == 1

        # Lauf 2 — Archiv zurueck: derselbe Re-Run holt die Quelle nach.
        state.fail = False
        assert await _run_once() == 0
        assert await _source_count() == 1
        assert state.calls == 2

        # Lauf 3 — bereits archiviert: Dedup greift VOR dem Archiv-Call.
        assert await _run_once() == 0
        assert await _source_count() == 1
        assert state.calls == 2, "Dedup muss vor der Archivierung greifen"
    finally:
        await engine.dispose()


async def test_ensure_adapter_idempotent(fresh_pg_dsn: str) -> None:
    """AC10: ensure_ingest_adapter 2x (gleiche name+version) -> genau 1 Zeile."""
    await upgrade_head(fresh_pg_dsn)
    engine = create_async_engine_from(DbSettings(dsn=fresh_pg_dsn))
    try:
        sessions = make_sessionmaker(engine)
        async with sessions() as session:
            await ensure_ingest_adapter(
                session, name="dup-adapter", version="1.0.0", trust_level="verified_primary"
            )
            await ensure_ingest_adapter(
                session, name="dup-adapter", version="1.0.0", trust_level="verified_primary"
            )
            await session.commit()
            count = await session.scalar(
                text("SELECT count(*) FROM ingest_adapter WHERE name = :n"),
                {"n": "dup-adapter"},
            )
            assert count == 1
    finally:
        await engine.dispose()
