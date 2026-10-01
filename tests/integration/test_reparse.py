"""Integration (Spec 0118/#126): reparse_source — Auswahl, Gleichheit,
Fehlerpfad, Idempotenz, Nebenläufigkeit.

Echtes Postgres (``fresh_pg_dsn``) + MinIO (``worm_store``); Archiver gemockt
(R-TEST-03). „Quelle ohne Spans“ entsteht per ``ingest_source`` (seither ohne
Span-Erzeugung, #126) mit Adapter-Unterklassen; ``reparse_source`` wählt nur
noch attestierte Quellen (ADR-0009) — die Tests attestieren daher jede Quelle
nach dem Ingest. ``reparse_source`` läuft mit dem echten Fixture-Adapter
(echtes normalize/parse aus #41) — ``fetch`` bleibt unberührt.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, async_sessionmaker

from wortlaut.ingest.adapter import RawSource, SourceRef, SpanDraft
from wortlaut.ingest.dip import DipPlenarprotokollAdapter
from wortlaut.ingest.settings import DipSettings
from wortlaut.pipeline.ingest import IngestOutcome, PipelineDeps, ingest_source
from wortlaut.pipeline.reparse import ReparseOutcome, reparse_source
from wortlaut.store.migrations import upgrade_head
from wortlaut.store.reparse import list_sources_without_spans, lock_source_if_spanless
from wortlaut.store.worm import WormStore

pytestmark = pytest.mark.integration

SeedAttestation = Callable[[AsyncSession | AsyncConnection, UUID | str], Awaitable[None]]

_FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "dip" / "plenarprotokoll_zweispaltig.pdf"
)
_ORIGIN = "https://dserver.bundestag.de/btp/21/21042/2104200.pdf"

# ADR-0006: digest-gepinnt. Zweiter, eigener Container für AC2: dieselbe Fixture
# hat denselben content_hash — eine geteilte DB würde beim zweiten Ingest dedupen.
PG_IMAGE = (
    "pgvector/pgvector@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb"
)


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
    """Wie ``_FixtureDipAdapter``, aber ``parse`` liefert ``[]`` → Quelle ohne Spans."""

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        return []


class _OtherAdapter(_EmptyDipAdapter):
    """Spanloser Adapter mit fremdem Namen (AC1: andere Adapter werden nicht gewählt)."""

    name = "other-adapter"


class _OkArchiver:
    """Archiver-Fake: liefert eine feste Snapshot-URL (kein Live-Call)."""

    def __init__(self, url: str) -> None:
        self._url = url

    async def archive(self, origin_url: str) -> str:
        return self._url


def _raw(raw_bytes: bytes) -> RawSource:
    return RawSource(
        origin_url=_ORIGIN,
        source_type="plenarprotokoll",
        raw_bytes=raw_bytes,
        mime_type="application/pdf",
        retrieved_at=datetime.now(UTC),
    )


def _deps(adapter: DipPlenarprotokollAdapter, worm: WormStore) -> PipelineDeps:
    return PipelineDeps(
        adapter=adapter,
        wayback=_OkArchiver("https://web.archive.org/snap"),
        archive_today=_OkArchiver("https://archive.ph/snap"),
        worm=worm,
    )


@pytest.fixture
def second_pg_dsn() -> Iterator[str]:
    """Zweiter, frischer Container (AC2: zwei unabhängige DBs)."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer(PG_IMAGE, driver="asyncpg") as pg:
        yield pg.get_connection_url()


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


async def _ingest_without_spans(
    sessions: async_sessionmaker[AsyncSession],
    worm: WormStore,
    raw_bytes: bytes,
    adapter_cls: type[_FixtureDipAdapter],
) -> IngestOutcome:
    """Ingest per ``parse → []`` (Quelle mit gespeichertem Text, ohne Spans)."""
    adapter = adapter_cls(_raw(raw_bytes))
    ref = SourceRef(origin_url=_ORIGIN, source_type="plenarprotokoll", hint={})
    # SSRF-Check gemockt: keine echte DNS-Auflösung im Test (R-TEST-03, hermetisch).
    with patch("wortlaut.archive.archiver.assert_url_allowed"):
        async with sessions() as session:
            await _seed_adapter(session, adapter.name)
            return await ingest_source(
                ref, deps=_deps(adapter, worm), session=session, rights_basis="amtliches_werk_p5"
            )


async def _span_count(sessions: async_sessionmaker[AsyncSession], source_id: UUID) -> int:
    async with sessions() as session:
        count = await session.scalar(
            text("SELECT count(*) FROM span WHERE source_id = CAST(:s AS uuid)"),
            {"s": str(source_id)},
        )
    assert count is not None
    return int(count)


async def _span_set(
    sessions: async_sessionmaker[AsyncSession], source_id: UUID
) -> set[tuple[Any, ...]]:
    """Menge der Span-Ausgaben (beweisrelevant, AC2): Offsets, span_hash, Datum,
    Locator, Permalink, Sprecher, Partei — als Tupel, damit der Vergleich stabil ist."""
    async with sessions() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT s.text_start, s.text_end, s.span_hash, s.spoken_at, s.locator, "
                    "s.permalink, sp.full_name, m.party "
                    "FROM span s "
                    "JOIN speaker sp ON sp.id = s.speaker_id "
                    "LEFT JOIN mandate m ON m.id = s.mandate_id "
                    "WHERE s.source_id = CAST(:s AS uuid) "
                    "ORDER BY s.text_start"
                ),
                {"s": str(source_id)},
            )
        ).all()
    return {
        (
            text_start,
            text_end,
            span_hash,
            spoken_at,
            tuple(sorted((str(k), str(v)) for k, v in locator.items())),
            permalink,
            full_name,
            party,
        )
        for text_start, text_end, span_hash, spoken_at, locator, permalink, full_name, party in rows
    }


# ── AC1: Auswahl (abgeleitet, gefiltert nach Adapter) ────────────────────


async def test_list_sources_without_spans_selects_only_spanless_same_adapter(
    fresh_pg_dsn: str,
    worm_store: WormStore,
    seed_attestation: SeedAttestation,
) -> None:
    """AC1: A ohne Spans (dip-api, attestiert), B mit Spans (dip-api, per
    attest + reparse), C ohne Spans (fremder Adapter, attestiert) → Auswahl für
    dip-api ist genau [A]."""
    fixture = _FIXTURE.read_bytes()
    sessions, engine = await _fresh(fresh_pg_dsn)
    try:
        a = await _ingest_without_spans(sessions, worm_store, b"ac1-source-a", _EmptyDipAdapter)
        b = await _ingest_without_spans(sessions, worm_store, fixture, _FixtureDipAdapter)
        c = await _ingest_without_spans(sessions, worm_store, b"ac1-source-c", _OtherAdapter)
        assert a.status == "inserted"
        assert a.span_count == 0
        assert b.status == "inserted"
        assert b.span_count == 0
        assert c.status == "inserted"
        assert c.span_count == 0
        assert a.source_id is not None
        assert b.source_id is not None
        assert c.source_id is not None

        async with sessions() as session:
            await seed_attestation(session, a.source_id)
            await seed_attestation(session, b.source_id)
            await seed_attestation(session, c.source_id)
            await session.commit()
            pending = await list_sources_without_spans(session, adapter_name="dip-api")
        assert {s.source_id for s in pending} == {a.source_id, b.source_id}

        target = next(s for s in pending if s.source_id == b.source_id)
        async with sessions() as session:
            re_b = await reparse_source(
                target,
                session=session,
                worm=worm_store,
                adapter=_FixtureDipAdapter(_raw(fixture)),
            )
        assert re_b.status == "reparsed"

        async with sessions() as session:
            result = await list_sources_without_spans(session, adapter_name="dip-api")
    finally:
        await engine.dispose()
    assert [s.source_id for s in result] == [a.source_id]


# ── AC2: Gleichheit mit ingest (zwei DBs) ────────────────────────────────


async def test_reparse_yields_same_spans_as_ingest(
    fresh_pg_dsn: str,
    second_pg_dsn: str,
    worm_store: WormStore,
    seed_attestation: SeedAttestation,
) -> None:
    """AC2: DB1 = ingest + attest + reparse mit funktionierendem Parser; DB2 =
    ingest + attest + reparse mit Parser, der ``[]`` liefert → gleiche, nicht
    leere Mengen von (text_start, text_end, span_hash, spoken_at, locator,
    permalink, speaker.full_name, mandate.party) in beiden DBs (reparse vs.
    reparse, #126)."""
    fixture = _FIXTURE.read_bytes()
    ref = SourceRef(origin_url=_ORIGIN, source_type="plenarprotokoll", hint={})
    sessions1, engine1 = await _fresh(fresh_pg_dsn)
    sessions2, engine2 = await _fresh(second_pg_dsn)
    try:
        with patch("wortlaut.archive.archiver.assert_url_allowed"):
            async with sessions1() as session:
                await _seed_adapter(session, "dip-api")
                outcome1 = await ingest_source(
                    ref,
                    deps=_deps(_FixtureDipAdapter(_raw(fixture)), worm_store),
                    session=session,
                    rights_basis="amtliches_werk_p5",
                )
            assert outcome1.status == "inserted"
            assert outcome1.source_id is not None
            assert outcome1.span_count == 0  # #126: ingest erzeugt keine Spans mehr (ADR-0009)

            async with sessions1() as session:
                await seed_attestation(session, outcome1.source_id)
                await session.commit()
                pending1 = await list_sources_without_spans(session, adapter_name="dip-api")
            assert len(pending1) == 1

            async with sessions1() as session:
                re_outcome1 = await reparse_source(
                    pending1[0],
                    session=session,
                    worm=worm_store,
                    adapter=_FixtureDipAdapter(_raw(fixture)),
                )
            assert re_outcome1.status == "reparsed"

            async with sessions2() as session:
                await _seed_adapter(session, "dip-api")
                outcome2 = await ingest_source(
                    ref,
                    deps=_deps(_EmptyDipAdapter(_raw(fixture)), worm_store),
                    session=session,
                    rights_basis="amtliches_werk_p5",
                )
            assert outcome2.status == "inserted"
            assert outcome2.source_id is not None
            assert outcome2.span_count == 0

            async with sessions2() as session:
                await seed_attestation(session, outcome2.source_id)
                await session.commit()
                pending = await list_sources_without_spans(session, adapter_name="dip-api")
            assert len(pending) == 1
            assert pending[0].source_id == outcome2.source_id

            async with sessions2() as session:
                re_outcome = await reparse_source(
                    pending[0],
                    session=session,
                    worm=worm_store,
                    adapter=_FixtureDipAdapter(_raw(fixture)),
                )
            assert re_outcome.status == "reparsed"
            assert re_outcome.span_count == re_outcome1.span_count

        db1 = await _span_set(sessions1, outcome1.source_id)
        db2 = await _span_set(sessions2, outcome2.source_id)
    finally:
        await engine1.dispose()
        await engine2.dispose()

    assert db1  # nicht leer
    assert db1 == db2


# ── AC4: alles oder nichts (Fehler beim zweiten Span) ───────────────────


async def test_failure_mid_source_leaves_no_spans(
    fresh_pg_dsn: str,
    worm_store: WormStore,
    seed_attestation: SeedAttestation,
) -> None:
    """AC4: init_span_state wirft beim zweiten Aufruf → Status error, 0 Spans
    (Rollback), Quelle erscheint erneut in list_sources_without_spans."""
    from wortlaut.store.spans import init_span_state as real_init

    fixture = _FIXTURE.read_bytes()
    sessions, engine = await _fresh(fresh_pg_dsn)
    try:
        outcome = await _ingest_without_spans(sessions, worm_store, fixture, _EmptyDipAdapter)
        assert outcome.status == "inserted"
        assert outcome.source_id is not None

        async with sessions() as session:
            await seed_attestation(session, outcome.source_id)
            await session.commit()

        async with sessions() as session:
            pending = await list_sources_without_spans(session, adapter_name="dip-api")
        assert len(pending) == 1

        calls = 0

        # Erster Aufruf echt, zweiter wirft RuntimeError (Spec 0118 §11/AC4).
        async def flaky_init(
            session: AsyncSession, *, span_id: UUID, verification: str, visibility: str
        ) -> None:
            nonlocal calls
            calls += 1
            if calls > 1:
                raise RuntimeError("simulierter Fehler beim zweiten Span")
            await real_init(
                session, span_id=span_id, verification=verification, visibility=visibility
            )

        with patch("wortlaut.pipeline.spans.init_span_state", side_effect=flaky_init):
            async with sessions() as session:
                re_outcome = await reparse_source(
                    pending[0],
                    session=session,
                    worm=worm_store,
                    adapter=_FixtureDipAdapter(_raw(fixture)),
                )
        assert calls == 2  # beide Spans erreicht, zweiter State-Write wirft
        assert re_outcome.status == "error"
        assert re_outcome.span_count == 0

        assert await _span_count(sessions, pending[0].source_id) == 0
        async with sessions() as session:
            again = await list_sources_without_spans(session, adapter_name="dip-api")
    finally:
        await engine.dispose()
    assert [s.source_id for s in again] == [pending[0].source_id]


# ── AC5: Idempotenz (zweiter Lauf ist ein No-Op) ─────────────────────────


async def test_second_run_is_noop(
    fresh_pg_dsn: str,
    worm_store: WormStore,
    seed_attestation: SeedAttestation,
) -> None:
    """AC5: Nach erfolgreichem reparse (n > 0 Spans) wird die Quelle nicht mehr
    ausgewählt; ein weiterer direkter Lauf ist skipped_has_spans, Span-Zahl bleibt n."""
    fixture = _FIXTURE.read_bytes()
    sessions, engine = await _fresh(fresh_pg_dsn)
    try:
        outcome = await _ingest_without_spans(sessions, worm_store, fixture, _EmptyDipAdapter)
        assert outcome.source_id is not None

        async with sessions() as session:
            await seed_attestation(session, outcome.source_id)
            await session.commit()

        async with sessions() as session:
            pending = await list_sources_without_spans(session, adapter_name="dip-api")
        assert len(pending) == 1

        async with sessions() as session:
            first = await reparse_source(
                pending[0],
                session=session,
                worm=worm_store,
                adapter=_FixtureDipAdapter(_raw(fixture)),
            )
        assert first.status == "reparsed"
        n = first.span_count
        assert n > 0

        async with sessions() as session:
            remaining = await list_sources_without_spans(session, adapter_name="dip-api")
        assert remaining == []  # nicht mehr ausgewählt

        async with sessions() as session:
            second = await reparse_source(
                pending[0],
                session=session,
                worm=worm_store,
                adapter=_FixtureDipAdapter(_raw(fixture)),
            )
        assert second.status == "skipped_has_spans"
        assert second.span_count == 0

        assert await _span_count(sessions, pending[0].source_id) == n
    finally:
        await engine.dispose()


# ── AC6: Nebenläufigkeit (zwei Sessions, ein Schreiblauf) ────────────────


async def test_concurrent_runs_write_once(
    fresh_pg_dsn: str,
    worm_store: WormStore,
    seed_attestation: SeedAttestation,
) -> None:
    """AC6: Zwei getrennte Sessions per asyncio.gather → genau ein Schreiblauf:
    ein Ergebnis reparsed, das andere skipped_has_spans; die Quelle hat genau
    so viele Spans wie nach einem einzelnen Lauf."""
    fixture = _FIXTURE.read_bytes()
    sessions, engine = await _fresh(fresh_pg_dsn)
    try:
        outcome = await _ingest_without_spans(sessions, worm_store, fixture, _EmptyDipAdapter)
        assert outcome.source_id is not None

        async with sessions() as session:
            await seed_attestation(session, outcome.source_id)
            await session.commit()

        async with sessions() as session:
            pending = await list_sources_without_spans(session, adapter_name="dip-api")
        assert len(pending) == 1
        source = pending[0]

        async def _run() -> ReparseOutcome:
            async with sessions() as session:
                return await reparse_source(
                    source,
                    session=session,
                    worm=worm_store,
                    adapter=_FixtureDipAdapter(_raw(fixture)),
                )

        first, second = await asyncio.gather(_run(), _run())
        assert {first.status, second.status} == {"reparsed", "skipped_has_spans"}

        # Fixture aus #41: genau 2 Spans (Präsidiums-Marker liefert keinen)
        assert await _span_count(sessions, source.source_id) == 2
    finally:
        await engine.dispose()


# ── AC6 (#126): Auswahl/Sperre nur für attestierte Quellen ────────────────


async def test_reparse_selects_only_attested(
    fresh_pg_dsn: str,
    worm_store: WormStore,
    seed_attestation: SeedAttestation,
) -> None:
    """AC6: zwei spanlose Quellen desselben Adapters, eine attestiert, eine
    nicht → ``list_sources_without_spans`` liefert nur die attestierte;
    ``lock_source_if_spanless`` liefert für die unattestierte False."""
    sessions, engine = await _fresh(fresh_pg_dsn)
    try:
        a = await _ingest_without_spans(
            sessions, worm_store, b"ac6 quelle attestiert", _EmptyDipAdapter
        )
        b = await _ingest_without_spans(
            sessions, worm_store, b"ac6 quelle unattestiert", _EmptyDipAdapter
        )
        assert a.status == "inserted"
        assert b.status == "inserted"
        assert a.source_id is not None
        assert b.source_id is not None

        async with sessions() as session:
            await seed_attestation(session, a.source_id)
            await session.commit()

        async with sessions() as session:
            result = await list_sources_without_spans(session, adapter_name="dip-api")
        assert [s.source_id for s in result] == [a.source_id]

        async with sessions() as session:
            locked_b = await lock_source_if_spanless(session, b.source_id)
        assert locked_b is False

        async with sessions() as session:
            locked_a = await lock_source_if_spanless(session, a.source_id)
        assert locked_a is True
    finally:
        await engine.dispose()
