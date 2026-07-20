"""Integration: ingest_source Pipeline gegen echtes Postgres + MinIO.

Archiver IMMER Fakes (R-TEST-03). Jede Test-Funktion erzeugt eindeutige raw_bytes
und filtert Assertions auf content_hash — nie global zaehlen.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from wortlaut.ingest.adapter import RawSource, SourceRef, SpanDraft
from wortlaut.pipeline.ingest import IngestOutcome, PipelineDeps, ingest_source
from wortlaut.store.migrations import upgrade_head
from wortlaut.store.worm import WormStore

pytestmark = pytest.mark.integration


# ── Fakes ──────────────────────────────────────────────────────────────


class CountingArchiver:
    """Archiver-Fake mit Zaeler. Urspuengliche Archive-URL oder Exception."""

    def __init__(self, url: str | None, *, fail: bool = False) -> None:
        self.url = url
        self.fail = fail
        self.calls = 0

    async def archive(self, origin_url: str) -> str:
        self.calls += 1
        if self.fail:
            raise RuntimeError("fake archive failure")
        if self.url is None:
            raise RuntimeError("no url")
        return self.url


class CountingWorm:
    """WormStore-Fake mit Zaeler."""

    put_calls = 0

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        self.put_calls += 1
        return f"s3://test/{key}?versionId=1"

    async def ensure_bucket(self) -> None:
        pass

    async def get(self, ref: str) -> bytes:
        raise AssertionError("get must not be called")


class FakeIngestAdapter:
    """IngestAdapter-Fake: fetch liefert festes RawSource."""

    name = "fake-adapter"
    version = "1.0.0"
    trust_level = "verified_primary"

    def __init__(self, raw: RawSource) -> None:
        self._raw = raw

    async def fetch(self, ref: SourceRef) -> RawSource:
        return self._raw

    async def discover(self, since: datetime) -> Sequence[SourceRef]:
        raise AssertionError("discover must not be called")

    def normalize(self, raw: RawSource) -> str:
        raise AssertionError("normalize must not be called in Phase 0")

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        raise AssertionError("parse must not be called in Phase 0")


# ── Helper ─────────────────────────────────────────────────────────────


async def _seed_adapter(session: AsyncSession, adapter_name: str, adapter_version: str) -> None:
    """Adapter-Zeile in ingest_adapter seeden (raw SQL + CAST-Muster).

    Idempotent (ON CONFLICT DO NOTHING) — die Session-DB ist testübergreifend geteilt,
    und dieser Seed wird committet.
    """
    await session.execute(
        text(
            "INSERT INTO ingest_adapter (name, version, trust_level, description, created_at) "
            "VALUES (:name, :version, CAST(:trust_level AS trust_level), "
            ":description, CAST(:created_at AS timestamptz)) "
            "ON CONFLICT (name, version) DO NOTHING"
        ),
        {
            "name": adapter_name,
            "version": adapter_version,
            "trust_level": "verified_primary",
            "description": "Integration-Test-Adapter",
            "created_at": datetime.now(UTC),
        },
    )
    await session.commit()


async def _count_for_hash(session: AsyncSession, content_hash: str) -> int:
    """Anzahl Zeilen mit dem content_hash zurueckgeben."""
    result = await session.scalar(
        text("SELECT count(*) FROM source WHERE content_hash = CAST(:h AS text)"),
        {"h": content_hash},
    )
    assert result is not None
    return int(result)


# ── AC2 ────────────────────────────────────────────────────────────────


async def test_new_source_inserted(
    pg_dsn: str,
    db_engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
    worm_store: WormStore,
) -> None:
    """AC2: Neue Quelle wird eingefuegt, WORM-Ref + Archivlinks korrekt, Roundtrip bestaetigt."""
    await upgrade_head(pg_dsn)

    raw_bytes = b"wortlaut-0007-ac2"
    raw = RawSource(
        origin_url="https://example.com/ac2",
        source_type="rede",
        raw_bytes=raw_bytes,
        mime_type="application/pdf",
        retrieved_at=datetime.now(UTC),
    )
    adapter = FakeIngestAdapter(raw)
    wayback = CountingArchiver(url="https://web.archive.org/snap-ac2")
    atoday = CountingArchiver(url="https://archive.ph/snap-ac2")
    deps = PipelineDeps(
        adapter=adapter,
        wayback=wayback,
        archive_today=atoday,
        worm=worm_store,
    )
    ref = SourceRef(origin_url="https://example.com/ac2", source_type="rede", hint={})

    async with sessions() as session:
        await _seed_adapter(session, adapter.name, adapter.version)

        outcome = await ingest_source(
            ref, deps=deps, session=session, rights_basis="amtliches_werk_p5"
        )

    assert outcome.status == "inserted"
    assert outcome.source_id is not None

    async with sessions() as session:
        count = await _count_for_hash(session, outcome.content_hash)
        assert count == 1

        row = await session.execute(
            text(
                "SELECT raw_bytes_ref, archive_wayback, archive_today FROM source "
                "WHERE content_hash = CAST(:h AS text)"
            ),
            {"h": outcome.content_hash},
        )
        first = row.first()
        assert first is not None
        row_dict = dict(first._mapping)

    assert row_dict["raw_bytes_ref"].startswith("s3://")
    assert "?versionId=" in row_dict["raw_bytes_ref"]
    assert row_dict["archive_wayback"] == "https://web.archive.org/snap-ac2"
    assert row_dict["archive_today"] == "https://archive.ph/snap-ac2"

    # Bonus: Kette Rohbyte <-> WORM geschlossen
    roundtrip = await worm_store.get(row_dict["raw_bytes_ref"])
    assert roundtrip == raw_bytes


# ── AC3 ────────────────────────────────────────────────────────────────


async def test_duplicate_skipped_no_side_effects(
    pg_dsn: str,
    db_engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
    worm_store: WormStore,
) -> None:
    """AC3: Zweiter Ingest mit gleichen Bytes -> skipped_duplicate, keine Seiteneffekte."""
    await upgrade_head(pg_dsn)

    raw_bytes = b"wortlaut-0007-ac3"
    raw = RawSource(
        origin_url="https://example.com/ac3",
        source_type="rede",
        raw_bytes=raw_bytes,
        mime_type="application/pdf",
        retrieved_at=datetime.now(UTC),
    )
    adapter = FakeIngestAdapter(raw)
    wayback = CountingArchiver(url="https://web.archive.org/snap-ac3")
    atoday = CountingArchiver(url="https://archive.ph/snap-ac3")
    worm = worm_store
    deps = PipelineDeps(
        adapter=adapter,
        wayback=wayback,
        archive_today=atoday,
        worm=worm,
    )
    ref = SourceRef(origin_url="https://example.com/ac3", source_type="rede", hint={})

    # Erster Ingest (inserted)
    async with sessions() as session:
        await _seed_adapter(session, adapter.name, adapter.version)
        outcome1 = await ingest_source(
            ref, deps=deps, session=session, rights_basis="amtliches_werk_p5"
        )

    assert outcome1.status == "inserted"

    # Zweiter Ingest mit zählenden Fakes
    wayback2 = CountingArchiver(url="https://web.archive.org/snap-ac3b")
    atoday2 = CountingArchiver(url="https://archive.ph/snap-ac3b")
    worm2 = CountingWorm()
    deps2 = PipelineDeps(
        adapter=adapter,
        wayback=wayback2,
        archive_today=atoday2,
        worm=worm2,
    )

    async with sessions() as session:
        outcome2 = await ingest_source(
            ref, deps=deps2, session=session, rights_basis="amtliches_werk_p5"
        )

    assert outcome2.status == "skipped_duplicate"
    assert wayback2.calls == 0
    assert atoday2.calls == 0
    assert worm2.put_calls == 0

    async with sessions() as session:
        count = await _count_for_hash(session, outcome1.content_hash)
        assert count == 1


# ── AC5 ────────────────────────────────────────────────────────────────


async def test_partial_archive_inserts(
    pg_dsn: str,
    db_engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
    worm_store: WormStore,
) -> None:
    """AC5: Wayback OK, archive.today fehlerhaft -> inserted, archive_today IS NULL."""
    await upgrade_head(pg_dsn)

    raw_bytes = b"wortlaut-0007-ac5"
    raw = RawSource(
        origin_url="https://example.com/ac5",
        source_type="rede",
        raw_bytes=raw_bytes,
        mime_type="application/pdf",
        retrieved_at=datetime.now(UTC),
    )
    adapter = FakeIngestAdapter(raw)
    wayback = CountingArchiver(url="https://web.archive.org/snap-ac5")
    atoday = CountingArchiver(url=None, fail=True)
    deps = PipelineDeps(
        adapter=adapter,
        wayback=wayback,
        archive_today=atoday,
        worm=worm_store,
    )
    ref = SourceRef(origin_url="https://example.com/ac5", source_type="rede", hint={})

    async with sessions() as session:
        await _seed_adapter(session, adapter.name, adapter.version)
        outcome = await ingest_source(
            ref, deps=deps, session=session, rights_basis="amtliches_werk_p5"
        )

    assert outcome.status == "inserted"
    assert outcome.source_id is not None

    async with sessions() as session:
        row = await session.execute(
            text(
                "SELECT archive_wayback, archive_today FROM source "
                "WHERE content_hash = CAST(:h AS text)"
            ),
            {"h": outcome.content_hash},
        )
        first = row.first()
        assert first is not None
        row_dict = dict(first._mapping)

    assert row_dict["archive_wayback"] == "https://web.archive.org/snap-ac5"
    assert row_dict["archive_today"] is None


# ── AC6 ────────────────────────────────────────────────────────────────


async def test_concurrent_ingest_unique_race(
    pg_dsn: str,
    db_engine: AsyncEngine,
    sessions: async_sessionmaker[AsyncSession],
    worm_store: WormStore,
) -> None:
    """AC6: Zwei Sessions, gleiche Bytes, asyncio.gather -> genau 1 Zeile,
    Outcomes als Multiset {inserted, skipped_duplicate}."""
    await upgrade_head(pg_dsn)

    raw_bytes = b"wortlaut-0007-ac6"
    raw = RawSource(
        origin_url="https://example.com/ac6",
        source_type="rede",
        raw_bytes=raw_bytes,
        mime_type="application/pdf",
        retrieved_at=datetime.now(UTC),
    )
    adapter = FakeIngestAdapter(raw)
    wayback = CountingArchiver(url="https://web.archive.org/snap-ac6")
    atoday = CountingArchiver(url="https://archive.ph/snap-ac6")
    worm = worm_store
    deps = PipelineDeps(
        adapter=adapter,
        wayback=wayback,
        archive_today=atoday,
        worm=worm,
    )
    ref = SourceRef(origin_url="https://example.com/ac6", source_type="rede", hint={})

    async with sessions() as session:
        await _seed_adapter(session, adapter.name, adapter.version)

    # Zwei unabhängige Sessions — eine je Task (eine Session kann nicht parallel arbeiten):
    async def _ingest_task() -> IngestOutcome:
        async with sessions() as session:
            return await ingest_source(
                ref, deps=deps, session=session, rights_basis="amtliches_werk_p5"
            )

    outcome_a, outcome_b = await asyncio.gather(_ingest_task(), _ingest_task())

    # Multiset-Prüfung
    statuses = {outcome_a.status, outcome_b.status}
    assert statuses == {"inserted", "skipped_duplicate"}

    async with sessions() as session:
        count = await _count_for_hash(session, outcome_a.content_hash)
        assert count == 1
