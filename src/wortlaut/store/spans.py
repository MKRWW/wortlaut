"""Span-Seite des Stores: Sprecher-/Mandat-Resolution + Span-/State-Insert (#42).

Kern-intern (store); kein Serving, kein LLM (R-SEC-04). get-or-create ist
idempotent per exaktem ``full_name`` je Parlament bzw. ``(speaker, parliament, party)`` — MVP,
kein Fuzzy-Matching. ``party`` bleibt freies Feld (Neutralität, R-CORE-03).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from wortlaut.store.models import Mandate, Span, SpanState, Speaker


async def resolve_or_create_speaker(
    session: AsyncSession,
    full_name: str,
    *,
    parliament: str,
    external_ids: dict[str, object] | None = None,
) -> UUID:
    """get-or-create per ``full_name`` je Parlament: wiederverwendet wird nur ein
    Sprecher mit Mandat in ``parliament`` oder ganz ohne Mandat (#143)."""
    has_mandate_here = exists().where(
        Mandate.speaker_id == Speaker.id, Mandate.parliament == parliament
    )
    has_any_mandate = exists().where(Mandate.speaker_id == Speaker.id)
    stmt = (
        select(Speaker.id)
        .where(Speaker.full_name == full_name, or_(has_mandate_here, ~has_any_mandate))
        .order_by(Speaker.created_at, Speaker.id)
        .limit(1)
    )
    existing = await session.scalar(stmt)
    if existing is not None:
        return existing
    speaker = Speaker(full_name=full_name, external_ids=external_ids or {})
    session.add(speaker)
    await session.flush()
    return speaker.id


@dataclass(frozen=True)
class Chamber:
    """Parlament und Rolle eines Mandats (#143)."""

    parliament: str
    role: str


async def resolve_or_create_mandate(
    session: AsyncSession,
    *,
    speaker_id: UUID,
    party: str | None,
    active_from: date,
    chamber: Chamber,
) -> UUID:
    """get-or-create per ``(speaker, parliament, role, party)``; ``party`` frei (R-CORE-03)."""
    stmt = select(Mandate.id).where(
        Mandate.speaker_id == speaker_id,
        Mandate.parliament == chamber.parliament,
        Mandate.role == chamber.role,
    )
    stmt = stmt.where(Mandate.party.is_(None) if party is None else Mandate.party == party)
    existing = await session.scalar(stmt)
    if existing is not None:
        return existing
    mandate = Mandate(
        speaker_id=speaker_id,
        role=chamber.role,
        parliament=chamber.parliament,
        party=party,
        active_from=active_from,
    )
    session.add(mandate)
    await session.flush()
    return mandate.id


@dataclass(frozen=True)
class NewSpan:
    """Einzufügende span-Zeile (``fts`` ist generiert und wird NICHT gesetzt)."""

    source_id: UUID
    speaker_id: UUID
    mandate_id: UUID | None
    verbatim_text: str
    text_start: int
    text_end: int
    spoken_at: date
    locator: dict[str, object]
    permalink: str
    span_hash: str


async def insert_span(session: AsyncSession, span: NewSpan) -> UUID:
    """Fügt eine span-Zeile ein (append-only, #40-Trigger); liefert die id."""
    row = Span(
        source_id=span.source_id,
        speaker_id=span.speaker_id,
        mandate_id=span.mandate_id,
        verbatim_text=span.verbatim_text,
        text_start=span.text_start,
        text_end=span.text_end,
        spoken_at=span.spoken_at,
        locator=span.locator,
        permalink=span.permalink,
        span_hash=span.span_hash,
    )
    session.add(row)
    await session.flush()
    return row.id


async def init_span_state(
    session: AsyncSession,
    *,
    span_id: UUID,
    verification: str,
    visibility: str,
) -> None:
    """Initialer (mutabler) ``span_state`` 1:1 zum span."""
    session.add(SpanState(span_id=span_id, verification=verification, visibility=visibility))
    await session.flush()
