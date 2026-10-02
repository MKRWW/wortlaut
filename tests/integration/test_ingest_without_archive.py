"""Integration (Spec 0132): Migration 0008 + Ende-zu-Ende ohne Archiv.

AC2: ``chk_archive`` ist nach ``0008`` weg (Insert ohne beide Archiv-URLs
gelingt), vor ``0008`` (auf ``0007`` zurückgefahren) scheitert er daran;
``downgrade`` → ``upgrade`` ist wiederholbar, auch wenn zwischendurch
Quellen ohne Archiv-URL angelegt wurden (``NOT VALID``).

AC3: *Given* echte Postgres/MinIO, Fixture-Adapter. *When* ``ingest`` →
Attestierung (Fixture ``seed_attestation``) → ``reparse``. *Then* die Quelle
hat Spans, ohne dass im ganzen Ablauf ein Archivar gebaut wurde (ADR-0009).
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
)

from wortlaut.ingest.adapter import RawSource, SourceRef
from wortlaut.ingest.dip import DipPlenarprotokollAdapter
from wortlaut.ingest.settings import DipSettings
from wortlaut.pipeline.ingest import PipelineDeps, ingest_source
from wortlaut.pipeline.reparse import reparse_source
from wortlaut.store.db import create_async_engine_from, make_sessionmaker
from wortlaut.store.migrations import downgrade_to, upgrade_head
from wortlaut.store.reparse import list_sources_without_spans
from wortlaut.store.settings import DbSettings
from wortlaut.store.worm import WormStore

pytestmark = pytest.mark.integration

SeedAttestation = Callable[[AsyncSession | AsyncConnection, UUID | str], Awaitable[None]]

_REV_BEFORE_0008 = "0007"

_FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "dip" / "plenarprotokoll_zweispaltig.pdf"
)
_ORIGIN = "https://dserver.bundestag.de/btp/21/21042/2104200.pdf"

_ADAPTER_PARAMS = {"n": "dip-api", "v": "1.0.0", "t": "verified_primary"}
_ADAPTER_INSERT = text(
    "INSERT INTO ingest_adapter (name, version, trust_level) "
    "VALUES (:n, :v, CAST(:t AS trust_level)) ON CONFLICT (name, version) DO NOTHING"
)

# Beide Archiv-URLs NULL — vor 0008 eine chk_archive-Verletzung, danach erlaubt.
_SOURCE_INSERT = text(
    "INSERT INTO source (source_type, rights_basis, adapter_name, adapter_version, "
    "origin_url, content_hash, byte_size, mime_type, retrieved_at, raw_bytes_ref, "
    "archive_wayback, archive_today) VALUES ("
    "CAST(:source_type AS source_type), CAST(:rights_basis AS rights_basis), "
    "CAST(:adapter_name AS text), :adapter_version, :origin_url, :content_hash, "
    ":byte_size, :mime_type, :retrieved_at, :raw_bytes_ref, :archive_wayback, :archive_today)"
)


def _source_params(content_hash: str, *, archive_wayback: str | None = None) -> dict[str, object]:
    return {
        "source_type": "plenarprotokoll",
        "rights_basis": "amtliches_werk_p5",
        "adapter_name": "dip-api",
        "adapter_version": "1.0.0",
        "origin_url": _ORIGIN,
        "content_hash": content_hash,
        "byte_size": 1,
        "mime_type": "application/pdf",
        "retrieved_at": datetime(2026, 10, 2, tzinfo=UTC),
        "raw_bytes_ref": "worm://test",
        "archive_wayback": archive_wayback,
        "archive_today": None,
    }


# ── AC2: chk_archive weg / wieder da / NOT VALID-Roundtrip ───────────────


async def _chk_archive_is_valid(dsn: str, engine: AsyncEngine) -> bool | None:
    """``convalidated`` von ``chk_archive``; ``None`` = Constraint fehlt (gedroppt)."""
    async with engine.connect() as conn:
        result = await conn.scalar(
            text("SELECT convalidated FROM pg_constraint WHERE conname = 'chk_archive'")
        )
    return None if result is None else bool(result)


async def test_chk_archive_dropped(fresh_pg_dsn: str) -> None:
    """AC2: nach ``0008`` gelingt ein ``source``-Insert ohne beide
    Archiv-URLs; zurückgefahren auf ``0007`` scheitert derselbe Insert an
    ``chk_archive``."""
    await upgrade_head(fresh_pg_dsn)
    engine = create_async_engine_from(DbSettings(dsn=fresh_pg_dsn))
    try:
        # Head (0008): Constraint ist weg — Insert ohne Archiv-URLs geht durch.
        async with engine.connect() as conn:
            trans = await conn.begin()
            await conn.execute(_ADAPTER_INSERT, _ADAPTER_PARAMS)
            await conn.execute(_SOURCE_INSERT, _source_params("a" * 64))
            await trans.commit()
        assert await _chk_archive_is_valid(fresh_pg_dsn, engine) is None

        # Zurück auf 0007: chk_archive ist wieder da (NOT VALID) und
        # verwirft neue Zeilen ohne Archiv-URL.
        await downgrade_to(fresh_pg_dsn, _REV_BEFORE_0008)
        assert await _chk_archive_is_valid(fresh_pg_dsn, engine) is False

        params = _source_params("b" * 64)
        async with engine.connect() as conn:
            trans = await conn.begin()
            with pytest.raises(DBAPIError, match="chk_archive"):
                await conn.execute(_SOURCE_INSERT, params)
            await trans.rollback()
    finally:
        await engine.dispose()


async def test_downgrade_not_valid_roundtrip(fresh_pg_dsn: str) -> None:
    """AC2: ``downgrade`` → ``upgrade`` ist wiederholbar, auch wenn
    zwischendurch Quellen ohne Archiv-URL angelegt wurden: die Re-Addition
    ist ``NOT VALID`` (bestehende Zeilen unangetastet), neue Zeilen werden
    aber weiterhin geprüft."""
    await upgrade_head(fresh_pg_dsn)
    engine = create_async_engine_from(DbSettings(dsn=fresh_pg_dsn))
    try:
        # Quelle A (ohne Archiv-URL) entsteht vor dem Downgrade (legal bei Head).
        async with engine.connect() as conn:
            trans = await conn.begin()
            await conn.execute(_ADAPTER_INSERT, _ADAPTER_PARAMS)
            await conn.execute(_SOURCE_INSERT, _source_params("c" * 64))
            await trans.commit()

        # 1. Roundtrip: Downgrade scheitert NICHT an Quelle A (NOT VALID),
        # neuer Insert ohne Archiv-URL wird verworfen, mit Archiv-URL akzeptiert.
        await downgrade_to(fresh_pg_dsn, _REV_BEFORE_0008)
        assert await _chk_archive_is_valid(fresh_pg_dsn, engine) is False

        params_invalid = _source_params("d" * 64)
        async with engine.connect() as conn:
            trans = await conn.begin()
            with pytest.raises(DBAPIError, match="chk_archive"):
                await conn.execute(_SOURCE_INSERT, params_invalid)
            await trans.rollback()

        async with engine.connect() as conn:
            trans = await conn.begin()
            await conn.execute(
                _SOURCE_INSERT,
                _source_params("e" * 64, archive_wayback="https://web.archive.org/snap"),
            )
            await trans.commit()

        # Upgrade entfernt die Constraint wieder; beide Quellen bleiben erhalten.
        await upgrade_head(fresh_pg_dsn)
        assert await _chk_archive_is_valid(fresh_pg_dsn, engine) is None
        async with engine.connect() as conn:
            count: int = await conn.scalar(text("SELECT count(*) FROM source"))
        assert count == 2

        # 2. Roundtrip: wiederholbar — Downgrade erneut mit NOT VALID, Upgrade
        # erneut, Bestand bleibt unangetastet.
        await downgrade_to(fresh_pg_dsn, _REV_BEFORE_0008)
        assert await _chk_archive_is_valid(fresh_pg_dsn, engine) is False
        await upgrade_head(fresh_pg_dsn)
        assert await _chk_archive_is_valid(fresh_pg_dsn, engine) is None
        async with engine.connect() as conn:
            count2: int = await conn.scalar(text("SELECT count(*) FROM source"))
        assert count2 == 2
    finally:
        await engine.dispose()


# ── AC3: Ende-zu-Ende ohne Archiv (ingest → attest → reparse) ────────────


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


def _raw(raw_bytes: bytes) -> RawSource:
    return RawSource(
        origin_url=_ORIGIN,
        source_type="plenarprotokoll",
        raw_bytes=raw_bytes,
        mime_type="application/pdf",
        retrieved_at=datetime.now(UTC),
    )


async def _seed_adapter(session: AsyncSession) -> None:
    await session.execute(_ADAPTER_INSERT, {"n": "dip-api", "v": "1.0.0", "t": "verified_primary"})
    await session.commit()


@pytest.fixture
async def fresh_sessions(fresh_pg_dsn: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Migrierte, frische DB je Test (Isolation gegen content_hash-Dedup)."""
    await upgrade_head(fresh_pg_dsn)
    engine = create_async_engine_from(DbSettings(dsn=fresh_pg_dsn))
    try:
        yield make_sessionmaker(engine)
    finally:
        await engine.dispose()


async def test_end_to_end_without_archive(
    fresh_sessions: async_sessionmaker[AsyncSession],
    worm_store: WormStore,
    seed_attestation: SeedAttestation,
) -> None:
    """AC3: ingest (ohne jeglichen Archivar) → Attestierung → reparse ergibt
    die Spans der Fixture (2) — die Beweiskette schließt, ohne dass im
    ganzen Ablauf ein Archivar gebaut wurde (ADR-0009)."""
    fixture = _FIXTURE.read_bytes()
    adapter = _FixtureDipAdapter(_raw(fixture))
    deps = PipelineDeps(adapter=adapter, worm=worm_store)  # kein Archivar
    ref = SourceRef(origin_url=_ORIGIN, source_type="plenarprotokoll", hint={})

    async with fresh_sessions() as session:
        await _seed_adapter(session)
        outcome = await ingest_source(
            ref, deps=deps, session=session, rights_basis="amtliches_werk_p5"
        )
    assert outcome.status == "inserted"
    source_id = outcome.source_id
    assert source_id is not None

    async with fresh_sessions() as session:
        await seed_attestation(session, source_id)
        await session.commit()
        pending = await list_sources_without_spans(session, adapter_name="dip-api")
    assert len(pending) == 1
    assert pending[0].source_id == source_id

    async with fresh_sessions() as session:
        re_outcome = await reparse_source(
            pending[0],
            session=session,
            worm=worm_store,
            adapter=_FixtureDipAdapter(_raw(fixture)),
        )
    assert re_outcome.status == "reparsed"
    assert re_outcome.span_count == 2

    async with fresh_sessions() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT verbatim_text, text_start, text_end, span_hash "
                    "FROM span WHERE source_id = CAST(:s AS uuid) ORDER BY text_start"
                ),
                {"s": str(source_id)},
            )
        ).all()
        normalized = await session.scalar(
            text("SELECT normalized_text FROM source WHERE id = CAST(:s AS uuid)"),
            {"s": str(source_id)},
        )
    assert normalized is not None
    assert len(rows) == 2
    for verbatim, start, end, span_hash_value in rows:
        assert normalized[start:end] == verbatim
        assert span_hash_value == hashlib.sha256(verbatim.encode("utf-8")).hexdigest()
