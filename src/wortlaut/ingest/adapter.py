"""Ingest-Adapter-Interface (datamodel §7).

Drei frozen Data-Model-Klassen und ein runtime_checkable Protocol, das
jede Quell-Adapter-Implementierung erfüllen muss.

Importiert ausschließlich stdlib + typing — kein wortlaut-Eigenimport.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class SourceRef:
    """Referenz auf eine entdeckte Quelle — Ergebnis von ``IngestAdapter.discover``."""

    origin_url: str
    source_type: str
    hint: dict[str, object]


@dataclass(frozen=True)
class RawSource:
    """Rohe Bytes einer Quelle — Ergebnis von ``IngestAdapter.fetch``."""

    origin_url: str
    source_type: str
    raw_bytes: bytes
    mime_type: str
    retrieved_at: datetime


@dataclass(frozen=True)
class SpanDraft:
    """Ein unverifizierter Zitat-Span — Ergebnis von ``IngestAdapter.parse``."""

    verbatim_text: str
    text_start: int
    text_end: int
    speaker_hint: dict[str, object]
    spoken_at: str
    locator: dict[str, object]
    permalink: str


@runtime_checkable
class IngestAdapter(Protocol):
    """Interface für alle Ingest-Adapter.

    Jeder Adapter erfüllt dieses Protocol — der Rest des Kerns (Hashing,
    Archivierung, WORM-Storage, Indexierung) kennt nur diese Naht.
    """

    name: str
    version: str
    trust_level: str  # 'verified_primary' | 'secondary' | 'low'

    async def discover(self, since: datetime) -> Sequence[SourceRef]: ...
    async def fetch(self, ref: SourceRef) -> RawSource: ...
    def normalize(self, raw: RawSource) -> str: ...
    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]: ...
