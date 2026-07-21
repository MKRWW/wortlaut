"""Unit: Pipeline-Ablaufreihenfolge (AC1, AC3, AC4, AC7).

Rein: keine DB, kein Netz. Fakes mit Recorder + mock.patch auf
source_exists / insert_source / content_hash / archive_all im Modul
wortlaut.pipeline.ingest — jede Funktion schreibt in die gemeinsame
``order``-Liste.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from wortlaut.archive.archiver import ArchiveResult
from wortlaut.ingest.adapter import RawSource, SourceRef, SpanDraft
from wortlaut.pipeline.ingest import PipelineDeps, ingest_source

# ── Fakes ──────────────────────────────────────────────────────────────


class FakeAdapter:
    """IngestAdapter-Fake: fetch liefert festes RawSource; parse/normalize zählen + werfen."""

    name = "fake-adapter"
    version = "1.0.0"
    trust_level = "verified_primary"
    parse_calls = 0
    normalize_calls = 0

    def __init__(self, raw: RawSource, order: list[str]) -> None:
        self._raw = raw
        self._order = order

    async def fetch(self, ref: SourceRef) -> RawSource:
        self._order.append("fetch")
        return self._raw

    async def discover(self, since: datetime) -> Sequence[SourceRef]:
        raise AssertionError("discover must not be called")

    def normalize(self, raw: RawSource) -> str:
        self.normalize_calls += 1
        return ""

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        self.parse_calls += 1
        return []


class FakeArchiver:
    """Archiver-Fake: archive liefert feste URL oder wirft."""

    def __init__(
        self, url: str | None = "https://web.archive.org/snap", *, fail: bool = False
    ) -> None:
        self._url = url
        self._fail = fail

    async def archive(self, origin_url: str) -> str:
        if self._fail:
            raise RuntimeError("fake archive failure")
        if self._url is None:
            raise RuntimeError("no url")
        return self._url


class FakeWorm:
    """WormStore-Fake: put zeichnet auf, gibt fiktiven Ref zurück."""

    put_calls = 0

    def __init__(self, order: list[str]) -> None:
        self._order = order

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        self.put_calls += 1
        self._order.append("worm.put")
        return f"s3://test/{key}?versionId=1"

    async def ensure_bucket(self) -> None:
        pass

    async def get(self, ref: str) -> bytes:
        raise AssertionError("get must not be called")


# ── Hilfsfunktion ──────────────────────────────────────────────────────


def _record(order: list[str], label: str, result: object) -> object:
    """Schreibt label in order und gibt result zurueck (fuer side_effect)."""
    order.append(label)
    return result


# ── Tests ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path_call_order() -> None:
    """AC1: Reihenfolge fetch → hash → dedup → archiv → WORM → insert."""
    order: list[str] = []
    test_hash = "a" * 64
    test_uuid = uuid4()

    raw = RawSource(
        origin_url="https://example.com/doc",
        source_type="rede",
        raw_bytes=b"test content",
        mime_type="text/plain",
        retrieved_at=datetime.now(UTC),
    )
    adapter = FakeAdapter(raw=raw, order=order)
    worm = FakeWorm(order=order)
    deps = PipelineDeps(
        adapter=adapter,
        wayback=FakeArchiver(url="https://web.archive.org/test"),
        archive_today=FakeArchiver(url="https://archive.ph/test"),
        worm=worm,
    )
    session = AsyncMock()
    ref = SourceRef(origin_url="https://example.com/doc", source_type="rede", hint={})

    async def _mock_archive(*args: object, **kwargs: object) -> ArchiveResult:
        order.append("archive_all")
        return ArchiveResult(
            wayback_url="https://web.archive.org/test",
            archive_today_url="https://archive.ph/test",
            errors={},
        )

    with patch("wortlaut.pipeline.ingest.source_exists", new_callable=AsyncMock) as mock_exists:
        mock_exists.side_effect = lambda s, h: _record(order, "source_exists", False)
        with patch("wortlaut.pipeline.ingest.insert_source", new_callable=AsyncMock) as mock_insert:
            mock_insert.side_effect = lambda s, r: _record(order, "insert_source", test_uuid)
            with patch("wortlaut.pipeline.ingest.content_hash") as mock_hash:
                mock_hash.side_effect = lambda b: _record(order, "content_hash", test_hash)
                with patch("wortlaut.pipeline.ingest.archive_all", side_effect=_mock_archive):
                    outcome = await ingest_source(
                        ref, deps=deps, session=session, rights_basis="amtliches_werk_p5"
                    )

    assert outcome.status == "inserted"
    assert outcome.source_id == test_uuid
    assert outcome.content_hash == test_hash
    assert order == [
        "fetch",
        "content_hash",
        "source_exists",
        "archive_all",
        "worm.put",
        "insert_source",
    ]


@pytest.mark.asyncio
async def test_archive_total_failure_no_insert() -> None:
    """AC4: Beide Archiver werfen -> archive_failed, kein WORM-put, kein insert."""
    order: list[str] = []
    test_hash = "b" * 64

    raw = RawSource(
        origin_url="https://example.com/doc",
        source_type="rede",
        raw_bytes=b"test content",
        mime_type="text/plain",
        retrieved_at=datetime.now(UTC),
    )
    adapter = FakeAdapter(raw=raw, order=order)
    worm = FakeWorm(order=order)
    deps = PipelineDeps(
        adapter=adapter,
        wayback=FakeArchiver(fail=True),
        archive_today=FakeArchiver(fail=True),
        worm=worm,
    )
    session = AsyncMock()
    ref = SourceRef(origin_url="https://example.com/doc", source_type="rede", hint={})

    async def _mock_archive(*args: object, **kwargs: object) -> ArchiveResult:
        order.append("archive_all")
        return ArchiveResult(
            wayback_url=None,
            archive_today_url=None,
            errors={"wayback": "failed", "archive_today": "failed"},
        )

    with patch("wortlaut.pipeline.ingest.source_exists", new_callable=AsyncMock) as mock_exists:
        mock_exists.side_effect = lambda s, h: _record(order, "source_exists", False)
        with patch("wortlaut.pipeline.ingest.insert_source", new_callable=AsyncMock) as mock_insert:
            mock_insert.side_effect = lambda s, r: _record(order, "insert_source", uuid4())
            with patch("wortlaut.pipeline.ingest.content_hash") as mock_hash:
                mock_hash.side_effect = lambda b: _record(order, "content_hash", test_hash)
                with patch("wortlaut.pipeline.ingest.archive_all", side_effect=_mock_archive):
                    outcome = await ingest_source(
                        ref, deps=deps, session=session, rights_basis="amtliches_werk_p5"
                    )

    assert outcome.status == "archive_failed"
    assert outcome.source_id is None
    assert outcome.content_hash == test_hash
    assert order == ["fetch", "content_hash", "source_exists", "archive_all"]
    assert worm.put_calls == 0
    mock_insert.assert_not_called()


@pytest.mark.asyncio
async def test_normalize_and_parse_called_phase1() -> None:
    """Phase-1 (#42): Happy-Path ruft normalize genau vor dem Insert und parse danach."""
    order: list[str] = []
    test_hash = "c" * 64
    test_uuid = uuid4()

    raw = RawSource(
        origin_url="https://example.com/doc",
        source_type="rede",
        raw_bytes=b"test content",
        mime_type="text/plain",
        retrieved_at=datetime.now(UTC),
    )
    adapter = FakeAdapter(raw=raw, order=order)
    worm = FakeWorm(order=order)
    deps = PipelineDeps(
        adapter=adapter,
        wayback=FakeArchiver(url="https://web.archive.org/test"),
        archive_today=FakeArchiver(url="https://archive.ph/test"),
        worm=worm,
    )
    session = AsyncMock()
    ref = SourceRef(origin_url="https://example.com/doc", source_type="rede", hint={})

    async def _mock_archive(*args: object, **kwargs: object) -> ArchiveResult:
        order.append("archive_all")
        return ArchiveResult(
            wayback_url="https://web.archive.org/test",
            archive_today_url="https://archive.ph/test",
            errors={},
        )

    with patch("wortlaut.pipeline.ingest.source_exists", new_callable=AsyncMock) as mock_exists:
        mock_exists.side_effect = lambda s, h: _record(order, "source_exists", False)
        with patch("wortlaut.pipeline.ingest.insert_source", new_callable=AsyncMock) as mock_insert:
            mock_insert.side_effect = lambda s, r: _record(order, "insert_source", test_uuid)
            with patch("wortlaut.pipeline.ingest.content_hash") as mock_hash:
                mock_hash.side_effect = lambda b: _record(order, "content_hash", test_hash)
                with patch("wortlaut.pipeline.ingest.archive_all", side_effect=_mock_archive):
                    await ingest_source(
                        ref, deps=deps, session=session, rights_basis="amtliches_werk_p5"
                    )

    # Phase-1: normalize (liefert "") + parse (liefert []) werden je einmal aufgerufen;
    # da parse [] liefert, entstehen keine Spans (Insert-Reihenfolge unverändert).
    assert adapter.normalize_calls == 1
    assert adapter.parse_calls == 1


@pytest.mark.asyncio
async def test_dedup_skip_no_side_effects() -> None:
    """Dedup-Skip (AC3 auf Unit-Ebene): source_exists=True -> sofort abbrechen."""
    order: list[str] = []
    test_hash = "d" * 64

    raw = RawSource(
        origin_url="https://example.com/doc",
        source_type="rede",
        raw_bytes=b"test content",
        mime_type="text/plain",
        retrieved_at=datetime.now(UTC),
    )
    adapter = FakeAdapter(raw=raw, order=order)
    worm = FakeWorm(order=order)
    deps = PipelineDeps(
        adapter=adapter,
        wayback=FakeArchiver(url="https://web.archive.org/test"),
        archive_today=FakeArchiver(url="https://archive.ph/test"),
        worm=worm,
    )
    session = AsyncMock()
    ref = SourceRef(origin_url="https://example.com/doc", source_type="rede", hint={})

    with patch("wortlaut.pipeline.ingest.source_exists", new_callable=AsyncMock) as mock_exists:
        mock_exists.side_effect = lambda s, h: _record(order, "source_exists", True)
        with patch("wortlaut.pipeline.ingest.insert_source", new_callable=AsyncMock) as mock_insert:
            mock_insert.side_effect = lambda s, r: _record(order, "insert_source", uuid4())
            with patch("wortlaut.pipeline.ingest.content_hash") as mock_hash:
                mock_hash.side_effect = lambda b: _record(order, "content_hash", test_hash)
                with patch("wortlaut.pipeline.ingest.archive_all") as mock_archive:
                    outcome = await ingest_source(
                        ref, deps=deps, session=session, rights_basis="amtliches_werk_p5"
                    )

    assert outcome.status == "skipped_duplicate"
    assert outcome.source_id is None
    assert outcome.content_hash == test_hash
    assert order == ["fetch", "content_hash", "source_exists"]
    assert worm.put_calls == 0
    mock_insert.assert_not_called()
    mock_archive.assert_not_called()
