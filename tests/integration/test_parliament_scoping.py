"""Integration (#143, AC2–AC5): Mandat und Sprecher je Parlament gegen echtes Postgres.

Frische DB je Test (``fresh_pg_dsn`` aus ``tests/integration/conftest.py``, Fixture wie in
``test_span_ingest.py``). Die Probe-Quelle wird ohne WORM direkt als source-Zeile angelegt
(Muster ``_seed_source`` aus ``tests/integration/test_timestamp_store.py``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, async_sessionmaker

from wortlaut.evidence.hashing import content_hash
from wortlaut.ingest.adapter import AdapterError, RawSource, SourceRef, SpanDraft
from wortlaut.pipeline.spans import write_spans
from wortlaut.store.migrations import upgrade_head
from wortlaut.store.sources import NewSource, insert_source
from wortlaut.store.spans import Chamber, resolve_or_create_mandate, resolve_or_create_speaker

pytestmark = pytest.mark.integration

SeedAttestation = Callable[[AsyncSession | AsyncConnection, UUID | str], Awaitable[None]]


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


class _LandtagProbeAdapter:
    """Eigenständiger Fake-Adapter (AC2): ``normalize`` dekodiert UTF-8, ``parse`` liefert
    genau einen ``SpanDraft`` über den ganzen Text."""

    name = "landtag-probe"
    version = "1.0.0"
    trust_level = "secondary"
    parliament = "landtag-brandenburg"
    mandate_role = "MdL"
    rights_basis = "amtliches_werk_p5"

    def normalize(self, raw: RawSource) -> str:
        return raw.raw_bytes.decode("utf-8")

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        return [
            SpanDraft(
                verbatim_text=normalized,
                text_start=0,
                text_end=len(normalized),
                speaker_hint={"name": "Dr. Gleichname", "party": "SPD"},
                spoken_at="2026-06-18",
                locator={"sitzung": "35"},
                permalink="https://example.org/35.docx",
            )
        ]

    async def discover(self, since: datetime) -> Sequence[SourceRef]:
        return []

    async def fetch(self, ref: SourceRef) -> RawSource:
        raise AdapterError(f"Fake-Adapter fetcht nicht: {ref.origin_url}")

    async def aclose(self) -> None:
        return None


async def _write_probe_span(session: AsyncSession, seed_attestation: SeedAttestation) -> UUID:
    """Gemeinsamer Aufbau (AC2): ingest_adapter-Zeile, source, Attestierung, write_spans;
    liefert die source_id."""
    raw_bytes = b"Das ist ein Satz."
    adapter = _LandtagProbeAdapter()
    await session.execute(
        text(
            "INSERT INTO ingest_adapter (name, version, trust_level) "
            "VALUES ('landtag-probe', '1.0.0', CAST('secondary' AS trust_level))"
        )
    )
    await session.commit()
    source_id = await insert_source(
        session,
        NewSource(
            content_hash=content_hash(raw_bytes),
            raw_bytes_ref="probe-ref",
            archive_wayback=None,
            archive_today=None,
            origin_url="https://example.org/35.docx",
            source_type="plenarprotokoll",
            rights_basis="amtliches_werk_p5",
            adapter_name="landtag-probe",
            adapter_version="1.0.0",
            byte_size=len(raw_bytes),
            mime_type="text/plain",
            retrieved_at=datetime.now(UTC),
            normalized_text="Das ist ein Satz.",
        ),
    )
    await seed_attestation(session, source_id)
    await session.commit()
    raw = RawSource(
        origin_url="https://example.org/35.docx",
        source_type="plenarprotokoll",
        raw_bytes=raw_bytes,
        mime_type="text/plain",
        retrieved_at=datetime.now(UTC),
    )
    written = await write_spans(
        session,
        adapter=adapter,
        raw=raw,
        normalized="Das ist ein Satz.",
        source_id=source_id,
    )
    assert written == 1
    return source_id


async def test_same_name_other_parliament_gets_new_speaker(
    fresh_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """AC3: gleichnamiger Sprecher mit Bundestagsmandat wird für ein anderes Parlament nicht
    wiederverwendet → andere id."""
    async with fresh_sessions() as session:
        first = await resolve_or_create_speaker(session, "Dr. Gleichname", parliament="bundestag")
        await resolve_or_create_mandate(
            session,
            speaker_id=first,
            party=None,
            active_from=date(2024, 1, 1),
            chamber=Chamber(parliament="bundestag", role="MdB"),
        )
        await session.commit()
        other = await resolve_or_create_speaker(
            session, "Dr. Gleichname", parliament="landtag-brandenburg"
        )
        assert other != first


async def test_same_parliament_reuses_speaker(
    fresh_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """AC3: zweiter Aufruf mit demselben Parlament, dazwischen Mandat angelegt → dieselbe id."""
    async with fresh_sessions() as session:
        brandenburg = await resolve_or_create_speaker(
            session, "Dr. Gleichname", parliament="landtag-brandenburg"
        )
        await resolve_or_create_mandate(
            session,
            speaker_id=brandenburg,
            party=None,
            active_from=date(2024, 1, 1),
            chamber=Chamber(parliament="landtag-brandenburg", role="MdL"),
        )
        await session.commit()
        again = await resolve_or_create_speaker(
            session, "Dr. Gleichname", parliament="landtag-brandenburg"
        )
        assert again == brandenburg


async def test_speaker_without_mandate_is_reused(
    fresh_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """AC4: zweimal derselbe Name und dasselbe Parlament ohne Mandat → dieselbe id, genau
    eine speaker-Zeile."""
    async with fresh_sessions() as session:
        first = await resolve_or_create_speaker(
            session, "Dr. Gleichname", parliament="landtag-brandenburg"
        )
        second = await resolve_or_create_speaker(
            session, "Dr. Gleichname", parliament="landtag-brandenburg"
        )
        assert second == first
        count = await session.scalar(
            text("SELECT count(*) FROM speaker WHERE full_name = :n"), {"n": "Dr. Gleichname"}
        )
        assert count == 1


async def test_bundestag_speaker_reused(
    fresh_sessions: async_sessionmaker[AsyncSession],
) -> None:
    """AC5: Sprecher mit Bundestagsmandat wird bei parliament='bundestag' wiederverwendet."""
    async with fresh_sessions() as session:
        first = await resolve_or_create_speaker(session, "Dr. Gleichname", parliament="bundestag")
        await resolve_or_create_mandate(
            session,
            speaker_id=first,
            party=None,
            active_from=date(2024, 1, 1),
            chamber=Chamber(parliament="bundestag", role="MdB"),
        )
        await session.commit()
        again = await resolve_or_create_speaker(session, "Dr. Gleichname", parliament="bundestag")
        assert again == first


async def test_write_spans_uses_adapter_parliament(
    fresh_sessions: async_sessionmaker[AsyncSession],
    seed_attestation: SeedAttestation,
) -> None:
    """AC2: write_spans nutzt parliament und mandate_role des Adapters — ausdrücklich nicht
    bundestag/MdB."""
    async with fresh_sessions() as session:
        source_id = await _write_probe_span(session, seed_attestation)
        result = await session.execute(
            text(
                "SELECT m.parliament, m.role FROM span s "
                "JOIN mandate m ON m.id = s.mandate_id WHERE s.source_id = CAST(:sid AS uuid)"
            ),
            {"sid": str(source_id)},
        )
        row = result.first()
        assert row is not None
        assert row.parliament == "landtag-brandenburg"
        assert row.parliament != "bundestag"
        assert row.role == "MdL"
        assert row.role != "MdB"


async def test_write_spans_does_not_merge_with_bundestag_speaker(
    fresh_sessions: async_sessionmaker[AsyncSession],
    seed_attestation: SeedAttestation,
) -> None:
    """AC3 über write_spans: vorher ein Bundestag-Sprecher, dann derselbe Ablauf wie im
    AC2-Test — der Span gehört nicht dem Bundestag-Sprecher."""
    async with fresh_sessions() as session:
        bundestag_speaker = await resolve_or_create_speaker(
            session, "Dr. Gleichname", parliament="bundestag"
        )
        await resolve_or_create_mandate(
            session,
            speaker_id=bundestag_speaker,
            party=None,
            active_from=date(2024, 1, 1),
            chamber=Chamber(parliament="bundestag", role="MdB"),
        )
        await session.commit()
        source_id = await _write_probe_span(session, seed_attestation)
        span_speaker = await session.scalar(
            text("SELECT s.speaker_id FROM span s WHERE s.source_id = CAST(:sid AS uuid)"),
            {"sid": str(source_id)},
        )
        assert span_speaker != bundestag_speaker
