"""Read-Query-Layer (#43): FTS-Suche, Span-/Source-Detail, Kontext-Bündel.

Liefert NUR öffentlich zitierfähige Spans — harter Server-Filter (datamodel §9)
plus Anti-Halluzinations-Gate pro Zeile: der Offset-Slice des GESPEICHERTEN Textes
muss dem verbatim_text entsprechen (R-DATA-06), als SQL-Bedingung (kein Hashen je
Query). Optionale Filter als ``(:p IS NULL OR …)`` → feste SQL-Strings, kein
dynamischer Aufbau aus Eingaben (Injection-frei). Kern-intern (store).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Harter Server-Filter (nicht optional): nur zitierfähig + öffentlich + nicht
# redigiert + geklärte Rechte, UND Anti-Halluzination (Slice == verbatim gegen den
# gespeicherten normalized_text; NULL/Manipulation ⇒ Zeile fällt raus, fail-safe).
_PUBLIC_FILTER = (
    "st.verification IN ('official','human_verified') "
    "AND st.redacted = false "
    "AND st.visibility = 'public' "
    "AND src.rights_basis <> 'ungeklaert' "
    "AND substr(src.normalized_text, sp.text_start + 1, sp.text_end - sp.text_start) "
    "= sp.verbatim_text"
)

_FROM_JOINS = (
    "FROM span sp "
    "JOIN span_state st ON st.span_id = sp.id "
    "JOIN source src ON src.id = sp.source_id "
    "JOIN speaker spk ON spk.id = sp.speaker_id "
    "LEFT JOIN mandate m ON m.id = sp.mandate_id"
)

_SPAN_COLS = (
    "sp.id AS span_id, sp.verbatim_text, sp.text_start, sp.text_end, sp.spoken_at, "
    "sp.span_hash, sp.permalink, sp.locator, sp.source_id, "
    "spk.full_name AS speaker_name, m.party, m.role, m.parliament, "
    "src.source_type, src.origin_url AS source_permalink, src.archive_wayback, "
    "src.archive_today, src.content_hash, src.rights_basis, st.verification"
)

# Optionale Filter immer gebunden (None ⇒ no-op) → fester SQL-String.
_SEARCH_WHERE = (
    f"{_PUBLIC_FILTER} "
    "AND sp.fts @@ to_tsquery('german', :q) "
    "AND (:party IS NULL OR m.party = :party) "
    "AND (:speaker IS NULL OR spk.full_name = :speaker) "
    "AND (:date_from IS NULL OR sp.spoken_at >= CAST(:date_from AS date)) "
    "AND (:date_to IS NULL OR sp.spoken_at <= CAST(:date_to AS date))"
)

_SEARCH_COUNT_SQL = text(f"SELECT count(*) {_FROM_JOINS} WHERE {_SEARCH_WHERE}")
_SEARCH_ROWS_SQL = text(
    f"SELECT {_SPAN_COLS} {_FROM_JOINS} WHERE {_SEARCH_WHERE} "
    "ORDER BY sp.spoken_at DESC, sp.id LIMIT :limit OFFSET :offset"
)
_SPAN_BY_ID_SQL = text(
    f"SELECT {_SPAN_COLS} {_FROM_JOINS} WHERE {_PUBLIC_FILTER} AND sp.id = CAST(:span_id AS uuid)"
)
# Kontext-Bündel: öffentliche Nachbar-Beiträge desselben TOP, nach Position geordnet.
_CONTEXT_SQL = text(
    "SELECT sp.id AS span_id, sp.verbatim_text, sp.text_start, sp.text_end, "
    "spk.full_name AS speaker_name, m.party "
    f"{_FROM_JOINS} WHERE {_PUBLIC_FILTER} "
    "AND sp.source_id = CAST(:source_id AS uuid) "
    "AND sp.locator->>'tagesordnungspunkt' IS NOT DISTINCT FROM :top "
    "ORDER BY sp.text_start LIMIT :limit"
)
_SOURCE_SQL = text(
    "SELECT id AS source_id, source_type, origin_url, content_hash, rights_basis, "
    "archive_wayback, archive_today, byte_size, mime_type, retrieved_at "
    "FROM source WHERE id = CAST(:source_id AS uuid) AND rights_basis <> 'ungeklaert'"
)

CONTEXT_MAX = 50  # Deckel gegen riesige TOPs (kein silent cap: Detail bleibt vollständig ladbar)


@dataclass(frozen=True)
class SpanRow:
    """Eine ausgelieferte, anti-halluzinations-geprüfte Span-Zeile."""

    span_id: UUID
    verbatim_text: str
    text_start: int
    text_end: int
    spoken_at: date
    span_hash: str
    permalink: str
    locator: dict[str, object]
    source_id: UUID
    speaker_name: str
    party: str | None
    role: str | None
    parliament: str | None
    source_type: str
    source_permalink: str
    archive_wayback: str | None
    archive_today: str | None
    content_hash: str
    rights_basis: str
    verification: str


@dataclass(frozen=True)
class ContextRow:
    """Ein Nachbar-Beitrag desselben TOP (Kontext-Bündel, offset-verifizierbar)."""

    span_id: UUID
    verbatim_text: str
    text_start: int
    text_end: int
    speaker_name: str
    party: str | None


@dataclass(frozen=True)
class SearchCriteria:
    """Suchkriterien (gebündelt, R-ARCH-04 ≤5 Params)."""

    q: str
    party: str | None = None
    speaker: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    limit: int = 20
    offset: int = 0


@dataclass(frozen=True)
class SourceRow:
    """Quell-Beleg (Hash/Archiv/Permalink) — keine Roh-/WORM-Interna."""

    source_id: UUID
    source_type: str
    origin_url: str
    content_hash: str
    rights_basis: str
    archive_wayback: str | None
    archive_today: str | None
    byte_size: int
    mime_type: str
    retrieved_at: date


def _span_row(m: Any) -> SpanRow:
    return SpanRow(
        span_id=m["span_id"],
        verbatim_text=m["verbatim_text"],
        text_start=m["text_start"],
        text_end=m["text_end"],
        spoken_at=m["spoken_at"],
        span_hash=m["span_hash"],
        permalink=m["permalink"],
        locator=m["locator"],
        source_id=m["source_id"],
        speaker_name=m["speaker_name"],
        party=m["party"],
        role=m["role"],
        parliament=m["parliament"],
        source_type=m["source_type"],
        source_permalink=m["source_permalink"],
        archive_wayback=m["archive_wayback"],
        archive_today=m["archive_today"],
        content_hash=m["content_hash"],
        rights_basis=m["rights_basis"],
        verification=m["verification"],
    )


def clamp_limit(limit: int, *, maximum: int = 100, default: int = 20) -> int:
    """Begrenzt limit auf [1, maximum] (Default bei <=0). total bleibt die echte Zahl."""
    if limit <= 0:
        return default
    return min(limit, maximum)


async def search_spans(
    session: AsyncSession, criteria: SearchCriteria
) -> tuple[list[SpanRow], int]:
    """FTS-Suche über span.fts + harte Filter; liefert (Treffer, Gesamtzahl)."""
    params: dict[str, object] = {
        "q": criteria.q,
        "party": criteria.party,
        "speaker": criteria.speaker,
        "date_from": criteria.date_from.isoformat() if criteria.date_from else None,
        "date_to": criteria.date_to.isoformat() if criteria.date_to else None,
    }
    total = await session.scalar(_SEARCH_COUNT_SQL, params)
    result = await session.execute(
        _SEARCH_ROWS_SQL,
        {**params, "limit": clamp_limit(criteria.limit), "offset": max(criteria.offset, 0)},
    )
    rows = [_span_row(m) for m in result.mappings()]
    return rows, int(total or 0)


async def get_span(session: AsyncSession, span_id: UUID) -> SpanRow | None:
    """Ein öffentlicher, anti-halluzinations-geprüfter Span per id (sonst None)."""
    result = await session.execute(_SPAN_BY_ID_SQL, {"span_id": str(span_id)})
    m = result.mappings().first()
    return _span_row(m) if m is not None else None


async def get_context(
    session: AsyncSession, source_id: UUID, tagesordnungspunkt: str | None
) -> list[ContextRow]:
    """Öffentliche Nachbar-Beiträge desselben TOP, nach Position geordnet (gedeckelt)."""
    result = await session.execute(
        _CONTEXT_SQL,
        {"source_id": str(source_id), "top": tagesordnungspunkt, "limit": CONTEXT_MAX},
    )
    return [
        ContextRow(
            span_id=m["span_id"],
            verbatim_text=m["verbatim_text"],
            text_start=m["text_start"],
            text_end=m["text_end"],
            speaker_name=m["speaker_name"],
            party=m["party"],
        )
        for m in result.mappings()
    ]


async def get_source(session: AsyncSession, source_id: UUID) -> SourceRow | None:
    """Quell-Beleg per id (keine Roh-/WORM-Interna); None wenn unbekannt/ungeklärt."""
    result = await session.execute(_SOURCE_SQL, {"source_id": str(source_id)})
    m = result.mappings().first()
    if m is None:
        return None
    return SourceRow(
        source_id=m["source_id"],
        source_type=m["source_type"],
        origin_url=m["origin_url"],
        content_hash=m["content_hash"],
        rights_basis=m["rights_basis"],
        archive_wayback=m["archive_wayback"],
        archive_today=m["archive_today"],
        byte_size=m["byte_size"],
        mime_type=m["mime_type"],
        retrieved_at=m["retrieved_at"],
    )
