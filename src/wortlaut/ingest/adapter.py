"""Ingest-Adapter-Interface (datamodel §7).

Drei frozen Data-Model-Klassen und ein runtime_checkable Protocol, das
jede Quell-Adapter-Implementierung erfüllen muss.

Der Adapter deklariert die Rechtsgrundlage seiner Quellen (``rights_basis``, #97).

Der Adapter nennt sein Parlament und die Rolle seiner Redner (#143).

Adapter melden Fehler, mit denen sie eine Quelle (oder die Entdeckung)
gerade nicht liefern, als ``AdapterError`` (oder einer Unterklasse).

Importiert ausschließlich stdlib + typing — kein wortlaut-Eigenimport.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable


class AdapterError(Exception):
    """Ein Adapter kann eine Quelle (oder die Entdeckung) gerade nicht liefern.

    Der Kern behandelt das als „diese Quelle überspringen" (bei fetch) bzw. als
    Abbruch der Entdeckung (bei discover) — nie als Programmfehler.
    """


@dataclass(frozen=True)
class SourceRef:
    """Referenz auf eine entdeckte Quelle — Ergebnis von ``IngestAdapter.discover``."""

    origin_url: str
    source_type: str
    hint: dict[str, object]
    # Rechtsgrundlage dieser Quelle; übersteuert ``IngestAdapter.rights_basis`` (#97).
    rights_basis: str | None = None


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
    parliament: str  # stabiler Kurzname, z. B. 'bundestag', 'landtag-brandenburg'
    mandate_role: str  # Rolle der Redner, z. B. 'MdB', 'MdL'

    @property
    def rights_basis(self) -> str | None:
        """Rechtsgrundlage aller Quellen dieses Adapters (Wert aus ``RIGHTS_BASES``);
        ``None`` heißt: jede ``SourceRef`` bringt ihre eigene mit. Ohne beides
        verweigert der Kern die Erfassung (#97)."""
        ...

    async def discover(self, since: datetime) -> Sequence[SourceRef]: ...
    async def fetch(self, ref: SourceRef) -> RawSource: ...
    def normalize(self, raw: RawSource) -> str: ...
    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]: ...

    async def aclose(self) -> None:
        """Wird vom Kern genau einmal am Ende eines Laufs aufgerufen, auch
        wenn der Lauf mit einem Fehler endet; muss idempotent sein und darf
        nicht werfen, wenn nichts zu schließen ist."""
