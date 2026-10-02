"""Integration (#42/#126): Phase-1-Ingest erfasst die Quelle; die Spans
entstehen per attest + reparse (ADR-0009) mit amtlicher Zuordnung.

Echtes Postgres + MinIO (Testcontainers); seit #132 (ADR-0009) kein Archiver im
Ingest. Fixture = die zweispaltige Protokoll-PDF aus #41 (AfD + SPD + Präsident).
Jeder Test bekommt eine FRISCHE DB (``fresh_pg_dsn``) — dieselbe Fixture hat
denselben content_hash, eine geteilte DB würde beim zweiten Ingest dedupen.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from wortlaut.ingest.adapter import RawSource, SourceRef, SpanDraft
from wortlaut.ingest.dip import DipPlenarprotokollAdapter
from wortlaut.ingest.settings import DipSettings
from wortlaut.pipeline.ingest import IngestOutcome, PipelineDeps, ingest_source
from wortlaut.pipeline.reparse import reparse_source
from wortlaut.store.migrations import upgrade_head
from wortlaut.store.reparse import list_sources_without_spans
from wortlaut.store.spans import resolve_or_create_speaker
from wortlaut.store.worm import WormStore

pytestmark = pytest.mark.integration

SeedAttestation = Callable[[AsyncSession | AsyncConnection, UUID | str], Awaitable[None]]

_FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "dip" / "plenarprotokoll_zweispaltig.pdf"
)
_ORIGIN = "https://dserver.bundestag.de/btp/21/21042/2104200.pdf"


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


class _CountingFixtureDipAdapter(_FixtureDipAdapter):
    """``_FixtureDipAdapter`` mit Parse-Zähler (AC4: parse wird beim Ingest
    nicht aufgerufen)."""

    def __init__(self, raw: RawSource) -> None:
        super().__init__(raw)
        self.parse_calls = 0

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        self.parse_calls += 1
        return super().parse(raw, normalized)


def _raw(raw_bytes: bytes) -> RawSource:
    return RawSource(
        origin_url=_ORIGIN,
        source_type="plenarprotokoll",
        raw_bytes=raw_bytes,
        mime_type="application/pdf",
        retrieved_at=datetime.now(UTC),
    )


def _deps(adapter: DipPlenarprotokollAdapter, worm: WormStore) -> PipelineDeps:
    """Kein Archivar mehr im Ingest (#132, ADR-0009)."""
    return PipelineDeps(adapter=adapter, worm=worm)


@pytest.fixture
async def fresh_sessions(fresh_pg_dsn: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Migrierte, frische DB je Test (Isolation gegen content_hash-Dedup)."""
    from wortlaut.store.db import create_async_engine_from, make_sessionmaker
    from wortlaut.store.settings import DbSettings

    await upgrade_head(fresh_pg_dsn)
    engine = create_async_engine_from(DbSettings(dsn=fresh_pg_dsn))
    try:
        yield make_sessionmaker(engine)
    finally:
        await engine.dispose()


async def _seed_adapter(session: AsyncSession) -> None:
    await session.execute(
        text(
            "INSERT INTO ingest_adapter (name, version, trust_level) "
            "VALUES (:n, :v, CAST(:t AS trust_level)) ON CONFLICT (name, version) DO NOTHING"
        ),
        {"n": "dip-api", "v": "1.0.0", "t": "verified_primary"},
    )
    await session.commit()


async def _ingest(
    sessions: async_sessionmaker[AsyncSession],
    worm: WormStore,
    raw_bytes: bytes,
    adapter: DipPlenarprotokollAdapter | None = None,
) -> IngestOutcome:
    if adapter is None:
        adapter = _FixtureDipAdapter(_raw(raw_bytes))
    ref = SourceRef(origin_url=_ORIGIN, source_type="plenarprotokoll", hint={})
    async with sessions() as session:
        await _seed_adapter(session)
        return await ingest_source(
            ref, deps=_deps(adapter, worm), session=session, rights_basis="amtliches_werk_p5"
        )


# ── AC4 (#126): Ingest ohne Spans ────────────────────────────────────────


async def test_ingest_creates_no_spans(
    fresh_sessions: async_sessionmaker[AsyncSession],
    worm_store: WormStore,
) -> None:
    """AC4: ingest erzeugt keine Spans — 0 Zeilen in ``span`` für die Quelle,
    ``normalized_text`` gesetzt, ``parse`` wird nicht aufgerufen."""
    fixture = _FIXTURE.read_bytes()
    adapter = _CountingFixtureDipAdapter(_raw(fixture))
    outcome = await _ingest(fresh_sessions, worm_store, fixture, adapter=adapter)
    assert outcome.status == "inserted"
    source_id = outcome.source_id
    assert source_id is not None

    async with fresh_sessions() as session:
        span_count = await session.scalar(
            text("SELECT count(*) FROM span WHERE source_id = CAST(:s AS uuid)"),
            {"s": str(source_id)},
        )
        normalized = await session.scalar(
            text("SELECT normalized_text FROM source WHERE id = CAST(:s AS uuid)"),
            {"s": str(source_id)},
        )
    # #126: ingest erzeugt keine Spans mehr (ADR-0009)
    assert span_count == 0
    assert normalized is not None
    assert adapter.parse_calls == 0


# ── AC5 (#126): der neue Weg zu Spans — ingest → attest → reparse
# (2 Spans, Präsident ausgeschlossen) ────────────────────────────────────


async def test_phase1_ingest_creates_spans(
    fresh_sessions: async_sessionmaker[AsyncSession],
    worm_store: WormStore,
    seed_attestation: SeedAttestation,
) -> None:
    """AC5: ingest (0 Spans) → Attestierung → reparse erzeugt genau so viele
    Spans, wie der Parser für die Fixture liefert (2)."""
    fixture = _FIXTURE.read_bytes()
    outcome = await _ingest(fresh_sessions, worm_store, fixture)
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
    assert re_outcome.span_count == 2  # AC8: Präsidiums-Marker liefert KEINEN Span

    async with fresh_sessions() as session:
        # AC1: source.normalized_text gesetzt
        normalized = await session.scalar(
            text("SELECT normalized_text FROM source WHERE id = CAST(:s AS uuid)"),
            {"s": str(source_id)},
        )
        assert normalized is not None and "Mustermann" in normalized

        rows = (
            await session.execute(
                text(
                    "SELECT verbatim_text, text_start, text_end, span_hash "
                    "FROM span WHERE source_id = CAST(:s AS uuid) ORDER BY text_start"
                ),
                {"s": str(source_id)},
            )
        ).all()
    assert len(rows) == 2  # AC1: genau N Span-Zeilen

    for verbatim, start, end, span_hash_val in rows:
        # AC5 (beweiskritisch): Offset gegen den GESPEICHERTEN Text
        assert normalized[start:end] == verbatim
        # AC4: span_hash == SHA-256(verbatim_text)
        assert span_hash_val == hashlib.sha256(verbatim.encode("utf-8")).hexdigest()


# ── AC3: Partei + verification=official + visibility=public ───────────────


async def test_official_verification_and_party(
    fresh_sessions: async_sessionmaker[AsyncSession],
    worm_store: WormStore,
    seed_attestation: SeedAttestation,
) -> None:
    fixture = _FIXTURE.read_bytes()
    outcome = await _ingest(fresh_sessions, worm_store, fixture)
    assert outcome.status == "inserted"
    source_id = outcome.source_id
    assert source_id is not None

    async with fresh_sessions() as session:
        await seed_attestation(session, source_id)
        await session.commit()
        pending = await list_sources_without_spans(session, adapter_name="dip-api")
    assert len(pending) == 1

    async with fresh_sessions() as session:
        re_outcome = await reparse_source(
            pending[0],
            session=session,
            worm=worm_store,
            adapter=_FixtureDipAdapter(_raw(fixture)),
        )
    assert re_outcome.status == "reparsed"

    async with fresh_sessions() as session:
        row = (
            await session.execute(
                text(
                    "SELECT m.party, st.verification, st.visibility "
                    "FROM span s "
                    "JOIN mandate m ON m.id = s.mandate_id "
                    "JOIN span_state st ON st.span_id = s.id "
                    "WHERE s.source_id = CAST(:s AS uuid) AND m.party = 'AfD'"
                ),
                {"s": str(source_id)},
            )
        ).first()
    assert row is not None
    party, verification, visibility = row
    assert party == "AfD"
    assert verification == "official"  # verified_primary → official (R-DATA-05)
    assert visibility == "public"


# ── AC2: Sprecher get-or-create ist idempotent ───────────────────────────


async def test_speaker_get_or_create_idempotent(
    fresh_sessions: async_sessionmaker[AsyncSession],
    worm_store: WormStore,
) -> None:
    async with fresh_sessions() as session:
        first = await resolve_or_create_speaker(session, "Dr. Testname")
        second = await resolve_or_create_speaker(session, "Dr. Testname")
        await session.commit()
        assert first == second  # eine Identität
        count = await session.scalar(
            text("SELECT count(*) FROM speaker WHERE full_name = 'Dr. Testname'")
        )
    assert count == 1  # genau eine speaker-Zeile


# ── AC6: normalize/parse-Fehler blockiert die Provenienz nicht ────────────


async def test_broken_pdf_still_inserts_source_no_spans(
    fresh_sessions: async_sessionmaker[AsyncSession],
    worm_store: WormStore,
) -> None:
    outcome = await _ingest(fresh_sessions, worm_store, b"%PDF- kaputt, kein echtes PDF")
    assert outcome.status == "inserted"  # Provenienz gesichert

    async with fresh_sessions() as session:
        span_count = await session.scalar(
            text("SELECT count(*) FROM span WHERE source_id = CAST(:s AS uuid)"),
            {"s": str(outcome.source_id)},
        )
        normalized = await session.scalar(
            text("SELECT normalized_text FROM source WHERE id = CAST(:s AS uuid)"),
            {"s": str(outcome.source_id)},
        )
    assert span_count == 0
    assert normalized is None  # normalize gescheitert → kein Text eingefroren


# ── AC7: span immutabel + Re-Ingest dupliziert keine Spans ────────────────


async def test_span_immutable_and_reingest_no_duplicate(
    fresh_sessions: async_sessionmaker[AsyncSession],
    worm_store: WormStore,
    seed_attestation: SeedAttestation,
) -> None:
    fixture = _FIXTURE.read_bytes()
    first = await _ingest(fresh_sessions, worm_store, fixture)
    assert first.status == "inserted"
    source_id = first.source_id
    assert source_id is not None

    async with fresh_sessions() as session:
        await seed_attestation(session, source_id)
        await session.commit()
        pending = await list_sources_without_spans(session, adapter_name="dip-api")
    assert len(pending) == 1

    async with fresh_sessions() as session:
        re_outcome = await reparse_source(
            pending[0],
            session=session,
            worm=worm_store,
            adapter=_FixtureDipAdapter(_raw(fixture)),
        )
    assert re_outcome.status == "reparsed"
    assert re_outcome.span_count == 2  # AC8: Präsidiums-Marker liefert KEINEN Span

    # Re-Ingest derselben Bytes → dedup, keine neuen Spans
    again = await _ingest(fresh_sessions, worm_store, fixture)
    assert again.status == "skipped_duplicate"

    async with fresh_sessions() as session:
        total_spans = await session.scalar(text("SELECT count(*) FROM span"))
        assert total_spans == 2  # Re-Ingest hat keine Spans dupliziert
        span_id = await session.scalar(
            text("SELECT id FROM span WHERE source_id = CAST(:s AS uuid) LIMIT 1"),
            {"s": str(source_id)},
        )
    # nur EINE werfende Invocation je raises-Block (S5778): Parameter vorab bauen
    upd_params = {"i": str(span_id)}
    del_params = {"i": str(span_id)}

    # AC7: UPDATE auf span wirft (Append-only-Trigger #40) — eigene Transaktion,
    # da eine gescheiterte Anweisung die PG-Transaktion aborted (Lektion #40).
    async with fresh_sessions() as session:
        upd = text("UPDATE span SET permalink = 'x' WHERE id = CAST(:i AS uuid)")
        with pytest.raises(DBAPIError):
            await session.execute(upd, upd_params)

    # AC7: DELETE auf span wirft ebenfalls — wieder eigene Transaktion.
    async with fresh_sessions() as session:
        dele = text("DELETE FROM span WHERE id = CAST(:i AS uuid)")
        with pytest.raises(DBAPIError):
            await session.execute(dele, del_params)
