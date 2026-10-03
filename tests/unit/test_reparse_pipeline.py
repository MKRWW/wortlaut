"""Unit (Spec 0118): ``reparse_source`` — WORM-/Hash-Gegenprüfung, Status, Netzfreiheit.

Rein: ``WormStore``, Session und Adapter sind schlanke Fakes (Aufrufe zählen) —
kein MinIO, keine DB. AC3 (hash_mismatch/worm_missing), AC7 (still_empty/no_text),
AC10 (kein fetch/discover/normalize).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from wortlaut.ingest.adapter import RawSource, SourceRef, SpanDraft
from wortlaut.pipeline.reparse import reparse_source
from wortlaut.store.reparse import SpanlessSource

_ORIGIN = "https://dserver.bundestag.de/btp/21/21042/2104200.pdf"


class _FakeWorm:
    """WormStore-Fake: get liefert feste Bytes oder wirft; put wird nie gebraucht."""

    def __init__(self, data: bytes, *, fail: bool = False) -> None:
        self._data = data
        self._fail = fail
        self.get_calls = 0

    async def ensure_bucket(self) -> None:
        raise AssertionError("not used")

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        raise AssertionError("not used")

    async def get(self, ref: str) -> bytes:
        self.get_calls += 1
        if self._fail:
            raise RuntimeError("WORM nicht erreichbar")
        return self._data


class _FakeAdapter:
    """IngestAdapter-Fake: fetch/discover/normalize werfen (AC10), parse liefert
    feste Drafts und zählt die Aufrufe (AC3)."""

    name = "dip-api"
    version = "1.0.0"
    trust_level = "verified_primary"

    def __init__(self, drafts: list[SpanDraft]) -> None:
        self._drafts = drafts
        self.fetch_calls = 0
        self.discover_calls = 0
        self.normalize_calls = 0
        self.parse_calls = 0

    async def discover(self, since: datetime) -> Sequence[SourceRef]:
        self.discover_calls += 1
        raise AssertionError("fetch/discover dürfen in reparse nicht aufgerufen werden")

    async def fetch(self, ref: SourceRef) -> RawSource:
        self.fetch_calls += 1
        raise AssertionError("fetch/discover dürfen in reparse nicht aufgerufen werden")

    def normalize(self, raw: RawSource) -> str:
        self.normalize_calls += 1
        raise AssertionError("reparse darf nicht neu normalisieren")

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        self.parse_calls += 1
        return self._drafts

    async def aclose(self) -> None:
        return None


def _draft(text: str = "Zitat aus dem Protokoll") -> SpanDraft:
    return SpanDraft(
        verbatim_text=text,
        text_start=0,
        text_end=len(text),
        speaker_hint={"name": "Testperson", "party": "AfD"},
        spoken_at="2026-01-01",
        locator={"protokoll": "21/90"},
        permalink=_ORIGIN,
    )


def _spanless(
    content_hash: str, normalized_text: str | None = "gespeicherter text"
) -> SpanlessSource:
    return SpanlessSource(
        source_id=UUID(int=7),
        content_hash=content_hash,
        raw_bytes_ref="s3://bucket/raw?versionId=1",
        origin_url=_ORIGIN,
        source_type="plenarprotokoll",
        mime_type="application/pdf",
        retrieved_at=datetime(2026, 8, 5, tzinfo=UTC),
        normalized_text=normalized_text,
    )


def _session() -> AsyncMock:
    """AsyncSession-Fake: attestierte Quelle ohne span-Zeile (#126: Attestierung
    vorausgesetzt), aufrufsequenzielle scalar-Ergebnisse; ``add`` synchron,
    damit keine dangling Coroutine entsteht."""
    session = AsyncMock()
    # Reihenfolge: source_archive (Zeile vorhanden) → span (keine) → speaker →
    # mandate (jeweils get-or-create in write_spans).
    session.scalar = AsyncMock(side_effect=[UUID(int=99), None, None, None])
    session.add = MagicMock()
    return session


# ── AC3: Hash-Gegenprüfung ───────────────────────────────────────────────


async def test_hash_mismatch_never_parses() -> None:
    """AC3: WORM-Bytes passen NICHT zum content_hash → hash_mismatch, Parser 0×,
    kein Span."""
    raw = b"manipulierte rohbytes"
    worm = _FakeWorm(raw)
    adapter = _FakeAdapter([_draft()])

    outcome = await reparse_source(
        _spanless("0" * 64), session=_session(), worm=worm, adapter=adapter
    )

    assert outcome.status == "hash_mismatch"
    assert outcome.span_count == 0
    assert adapter.parse_calls == 0  # Parser NICHT aufgerufen
    assert adapter.fetch_calls == 0
    assert adapter.discover_calls == 0
    assert worm.get_calls == 1


async def test_worm_missing() -> None:
    """AC3: WORM wirft → worm_missing, kein Span, Parser 0×."""
    raw = b"rohbytes"
    worm = _FakeWorm(raw, fail=True)
    adapter = _FakeAdapter([_draft()])

    outcome = await reparse_source(
        _spanless(hashlib.sha256(raw).hexdigest()),
        session=_session(),
        worm=worm,
        adapter=adapter,
    )

    assert outcome.status == "worm_missing"
    assert outcome.span_count == 0
    assert adapter.parse_calls == 0
    assert worm.get_calls == 1


# ── AC7: leer bleibt sichtbar; kein Text → kein WORM-Read ────────────────


async def test_empty_parse_is_still_empty(caplog: pytest.LogCaptureFixture) -> None:
    """AC7: Parser liefert [] → still_empty, WARNING-Log mit der source_id,
    kein Span, Rollback."""
    raw = b"rohbytes"
    worm = _FakeWorm(raw)
    adapter = _FakeAdapter([])
    session = _session()

    source = _spanless(hashlib.sha256(raw).hexdigest())
    with caplog.at_level("WARNING", logger="wortlaut.pipeline.reparse"):
        outcome = await reparse_source(source, session=session, worm=worm, adapter=adapter)

    assert outcome.status == "still_empty"
    assert outcome.span_count == 0
    assert adapter.parse_calls == 1
    assert session.add.call_count == 0  # kein Speaker/Mandat/Span/State geschrieben
    assert session.rollback.await_count == 1
    assert any(
        str(source.source_id) in record.getMessage()
        for record in caplog.records
        if record.levelname == "WARNING"
    )


async def test_no_text_skips_worm() -> None:
    """AC7: normalized_text ist None → no_text, kein WORM-Read."""
    worm = _FakeWorm(b"rohbytes")
    adapter = _FakeAdapter([_draft()])

    outcome = await reparse_source(
        _spanless("0" * 64, normalized_text=None),
        session=_session(),
        worm=worm,
        adapter=adapter,
    )

    assert outcome.status == "no_text"
    assert outcome.span_count == 0
    assert worm.get_calls == 0
    assert adapter.parse_calls == 0


# ── AC10: kein Netz (fetch/discover/normalize nie) ───────────────────────


async def test_never_fetches_or_discovers() -> None:
    """AC10: fetch/discover/normalize werfen — der reparse-Happy-Path läuft ohne
    Netz; der import-linter-Contract (Spec 0118 §4.4) sichert das strukturell."""
    raw = b"rohbytes"
    worm = _FakeWorm(raw)
    adapter = _FakeAdapter([_draft()])
    session = _session()

    outcome = await reparse_source(
        _spanless(hashlib.sha256(raw).hexdigest()),
        session=session,
        worm=worm,
        adapter=adapter,
    )

    assert outcome.status == "reparsed"
    assert outcome.span_count == 1
    assert adapter.fetch_calls == 0
    assert adapter.discover_calls == 0
    assert adapter.normalize_calls == 0
    assert adapter.parse_calls == 1
    assert session.commit.await_count == 1
