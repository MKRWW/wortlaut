"""Unit (#42): span_hash-Korrektheit (AC4) + normalize-Robustheit (AC6-Basis).

Rein: keine DB, kein Netz.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime

from wortlaut.evidence.hashing import content_hash, span_hash
from wortlaut.ingest.adapter import RawSource, SourceRef, SpanDraft
from wortlaut.pipeline.ingest import _safe_normalize


def test_span_hash_matches_sha256_of_utf8() -> None:  # AC4
    text = "Die Würde des Menschen ist unantastbar"
    expected = hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert span_hash(text) == expected
    assert len(span_hash(text)) == 64


def test_span_hash_deterministic_and_reuses_content_hash() -> None:  # AC4
    text = "Sehr geehrter Herr Präsident"
    first = span_hash(text)
    second = span_hash(text)
    assert first == second  # deterministisch
    assert first == content_hash(text.encode("utf-8"))  # Wiederverwendung #3


class _RaisingAdapter:
    """Adapter, dessen normalize immer wirft (kaputtes PDF)."""

    name = "raising"
    version = "1.0.0"
    trust_level = "verified_primary"

    async def fetch(self, ref: SourceRef) -> RawSource:
        raise AssertionError("fetch not used")

    async def discover(self, since: datetime) -> Sequence[SourceRef]:
        raise AssertionError("discover not used")

    def normalize(self, raw: RawSource) -> str:
        raise ValueError("kaputtes PDF")

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        return []


def test_safe_normalize_swallows_errors() -> None:  # AC6-Basis: Fehler → None, kein Crash
    raw = RawSource(
        origin_url="https://dserver.bundestag.de/x.pdf",
        source_type="plenarprotokoll",
        raw_bytes=b"kein-pdf",
        mime_type="application/pdf",
        retrieved_at=datetime.now(UTC),
    )
    assert _safe_normalize(_RaisingAdapter(), raw) is None
