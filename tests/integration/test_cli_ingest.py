"""Integration: CLI `ingest` end-to-end gegen echtes Postgres + MinIO (AC9/AC10).

Der DIP-Adapter ist ein Fake (R-TEST-03 — kein Live-Netz); Postgres und MinIO
sind echte Testcontainer. Seit #132 (ADR-0009) gibt es im Ingest keinen Archiver
mehr — der Lauf spricht nicht mit dem Internet Archive. Prueft die CLI-Verdrahtung:
Bootstrap (migrate + bucket + adapter-seed) -> discover -> ingest_source -> source
(ohne Spans, #126) + WORM + verify.
"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import cast
from unittest.mock import patch

import pytest
from sqlalchemy import text

from wortlaut.cli import _run
from wortlaut.ingest.adapter import IngestAdapter, RawSource, SourceRef, SpanDraft
from wortlaut.ingest.registry import DEFAULT_ADAPTER, AdapterEntry, AdapterRegistry
from wortlaut.pipeline.verify import verify_source
from wortlaut.store.adapters import ensure_ingest_adapter
from wortlaut.store.db import create_async_engine_from, make_sessionmaker
from wortlaut.store.migrations import upgrade_head
from wortlaut.store.settings import DbSettings, WormSettings
from wortlaut.store.worm import MinioWormStore

pytestmark = pytest.mark.integration

# ADR-0006: digest-gepinnt (repository@sha256, ohne Tag — sonst pullt docker-py nicht).
# Unveraenderte Kopie von minio/minio:RELEASE.2025-09-07T16-13-09Z.
# Upstream ist nicht mehr abrufbar (#119).
MINIO_IMAGE = (
    "ghcr.io/mkrww/minio@sha256:a16cad481969d7ddb6fcd2f1c58284af3d1266190b9bedc3ef0801aeb05a93a9"
)

_NORMALIZED = "Guten Tag."


class _FakeCliAdapter:
    """DIP-Adapter-Ersatz: discover -> 1 Ref, fetch -> RawSource, parse -> 1 valider Span."""

    name = "cli-int-adapter"
    version = "1.0.0"
    trust_level = "verified_primary"
    rights_basis = "amtliches_werk_p5"

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
    # Seit #132 (ADR-0009) braucht `ingest` keine Internet-Archive-Zugangsdaten
    # mehr — die gehören zu `capture` (tests/integration/test_capture.py).


def _ingest_args() -> Namespace:
    """Wie argparse es liefert (``--no-preflight`` entfiel am ingest, #132)."""
    return Namespace(
        since=datetime(2024, 1, 1),
        adapter="dip-api",
        rights_basis="amtliches_werk_p5",
        limit=None,
        no_migrate=False,  # CLI migriert die frische DB selbst (Bootstrap-Test)
        dry_run=False,
    )


def _registry_with(adapter: object) -> AdapterRegistry:
    """Registry, deren Default-Eintrag genau ``adapter`` liefert."""
    typed = cast(IngestAdapter, adapter)
    registry = AdapterRegistry()
    registry.register(
        AdapterEntry(
            name=DEFAULT_ADAPTER,
            version=typed.version,
            trust_level=typed.trust_level,
            rights_basis=getattr(adapter, "rights_basis", None),
            create=lambda: typed,
        )
    )
    return registry


async def test_end_to_end_single_source(
    fresh_pg_dsn: str,
    minio_config: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC9: CLI ingest -> 1 source + 0 Spans (#126), verify=ok, WORM haelt die Rohbytes."""
    _set_env(monkeypatch, fresh_pg_dsn, minio_config)

    with patch("wortlaut.cli.registry_from_env", return_value=_registry_with(_FakeCliAdapter())):
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
            # #126: ingest erzeugt keine Spans mehr (ADR-0009)
            assert int(span_count) == 0

            report = await verify_source(source_id, session=session, worm=worm)
            assert report.ok
            assert report.status == "ok"

        assert await worm.get(raw_ref) == b"%PDF-1.4 wortlaut-cli-int"
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


class _PerSourceRightsAdapter:
    """Adapter ohne Default-Rechtsgrundlage (#97): jede ``SourceRef`` bringt
    ihre eigene Angabe mit; ``fetch`` liefert je Quelle verschiedene Rohbytes."""

    name = "per-source-adapter"
    version = "1.0.0"
    trust_level = "verified_primary"
    rights_basis: str | None = None

    def __init__(self, *_a: object, **_kw: object) -> None:
        pass

    async def discover(self, since: datetime) -> Sequence[SourceRef]:
        return [
            SourceRef("https://example.com/qs1", "drucksache", {}, rights_basis="lizenz"),
            SourceRef("https://example.com/qs2", "drucksache", {}, rights_basis="ungeklaert"),
        ]

    async def fetch(self, ref: SourceRef) -> RawSource:
        if ref.origin_url == "https://example.com/qs1":
            raw = b"%PDF-1.4 per-source qs1"
        else:
            raw = b"%PDF-1.4 per-source qs2"
        return RawSource(
            origin_url=ref.origin_url,
            source_type=ref.source_type,
            raw_bytes=raw,
            mime_type="application/pdf",
            retrieved_at=datetime(2024, 1, 15),
        )

    def normalize(self, raw: RawSource) -> str:
        return _NORMALIZED

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        return []

    async def aclose(self) -> None:
        pass


async def test_rights_basis_per_source_end_to_end(
    fresh_pg_dsn: str,
    minio_config: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC12: ohne ``--rights-basis`` gilt die je-Quelle-Angabe; beide Werte
    landen unveraendert in ``source`` (lizenz / ungeklaert)."""
    _set_env(monkeypatch, fresh_pg_dsn, minio_config)
    args = _ingest_args()
    args.rights_basis = None

    registry = _registry_with(_PerSourceRightsAdapter())
    with patch("wortlaut.cli.registry_from_env", return_value=registry):
        rc = await _run(args)

    assert rc == 0

    engine = create_async_engine_from(DbSettings(dsn=fresh_pg_dsn))
    try:
        sessions = make_sessionmaker(engine)
        async with sessions() as session:
            rows = (
                await session.execute(
                    text("SELECT origin_url, rights_basis FROM source ORDER BY origin_url")
                )
            ).all()
        actual: list[tuple[str, str]] = [(row[0], row[1]) for row in rows]
        expected = [
            ("https://example.com/qs1", "lizenz"),
            ("https://example.com/qs2", "ungeklaert"),
        ]
        assert actual == expected
    finally:
        await engine.dispose()
