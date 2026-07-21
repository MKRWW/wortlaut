"""Antwort-Schemas des Read-API (#43). Nur wörtliche DB-Felder, kein generierter Text.

Ausgabe = wörtlicher Span + Beleg (R-CORE-01). Bewusst NICHT enthalten:
raw_bytes_ref/WORM-Pfade/Pipeline-Interna/machine-Spans (datamodel §9).
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field


class SearchParams(BaseModel):
    """Query-Parameter der Suche (als Modell → schlanke Endpoint-Signatur, R-ARCH-04)."""

    q: str
    party: str | None = None
    speaker: str | None = None
    date_from: date | None = Field(default=None, alias="from")
    date_to: date | None = Field(default=None, alias="to")
    limit: int = 20
    offset: int = 0


class SpeakerInfo(BaseModel):
    name: str
    party: str | None
    role: str | None
    parliament: str | None


class SourceInfo(BaseModel):
    type: str
    permalink: str
    archive_wayback: str | None
    archive_today: str | None
    content_hash: str
    rights_basis: str


class LocatorInfo(BaseModel):
    protokoll: str | None = None
    sitzung: str | None = None
    tagesordnungspunkt: str | None = None


class MatchInfo(BaseModel):
    start: int
    end: int


class SpanResult(BaseModel):
    """Ein Treffer: VOLLER Redebeitrag (nie gecroppt) + Beleg + Kontext-Anker."""

    span_id: UUID
    verbatim_text: str
    speaker: SpeakerInfo
    spoken_at: date
    source: SourceInfo
    span_hash: str
    verification: str
    locator: LocatorInfo
    match: MatchInfo | None = None


class ContextItem(BaseModel):
    """Ein Nachbar-Beitrag desselben TOP (offset-verifizierbar, für lokales Zoomen)."""

    span_id: UUID
    speaker_name: str
    party: str | None
    text_start: int
    text_end: int
    verbatim_text: str


class SpanDetail(SpanResult):
    """Span-Detail = Treffer + Kontext-Bündel des enclosing Tagesordnungspunkts."""

    context: list[ContextItem]


class SearchResponse(BaseModel):
    results: list[SpanResult]
    total: int


class VerifyResult(BaseModel):
    """Ergebnis der Integritäts-Nachrechnung (reuse verify_source, #8)."""

    ok: bool
    status: str
    content_hash_expected: str | None
    content_hash_actual: str | None
    span_in_source: bool
    archive_wayback: str | None
    archive_today: str | None


class SourceEvidence(BaseModel):
    """Quell-Beleg: Hash/Archiv/Permalink — keine Roh-/WORM-Interna."""

    source_id: UUID
    type: str
    permalink: str
    content_hash: str
    rights_basis: str
    archive_wayback: str | None
    archive_today: str | None
    byte_size: int
    mime_type: str
    retrieved_at: date
