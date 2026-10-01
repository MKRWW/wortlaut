"""Span-Nachzug im Store: abgeleitete Auswahl spanloser Quellen + Zeilensperre (#118).

„Ohne Spans“ ist **abgeleitet** (keine ``span``-Zeile) — kein Status-Flag, kein
UPDATE; dasselbe Muster wie der Zeitstempel-Rückstand aus #76
(``list_sources_without_timestamp``). ``span`` ist per Trigger append-only
(``trg_span_immutable``, R-DATA-01) und hat keinen UNIQUE-Schlüssel, der
Duplikate verhindern würde — deshalb prüft ``lock_source_if_spanless`` die
Span-Losigkeit per ``SELECT … FOR UPDATE`` in derselben Transaktion: der zweite
gleichzeitige Lauf wartet auf die Sperre, sieht danach die Spans und überspringt.
Kein Commit/Rollback in der Funktion — die umgebende Transaktion entscheidet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from wortlaut.store.models import Source, Span


@dataclass(frozen=True)
class SpanlessSource:
    """Eine ``source`` ohne Spans — Kandidat des Span-Nachzugs (abgeleitet)."""

    source_id: UUID
    content_hash: str
    raw_bytes_ref: str
    origin_url: str
    source_type: str
    mime_type: str
    retrieved_at: datetime
    normalized_text: str | None


async def list_sources_without_spans(
    session: AsyncSession, *, adapter_name: str, limit: int | None = None
) -> list[SpanlessSource]:
    """Alle ``source`` ohne eine ``span``-Zeile mit dem übergebenen ``adapter_name``.

    „Ohne Spans“ ist abgeleitet (keine Zeile). Stabil sortiert nach
    ``created_at, id``; optionales ``limit``. Kein UPDATE/DELETE — nur Read.
    """
    span_exists = exists().where(Span.source_id == Source.id)
    stmt = (
        select(
            Source.id,
            Source.content_hash,
            Source.raw_bytes_ref,
            Source.origin_url,
            Source.source_type,
            Source.mime_type,
            Source.retrieved_at,
            Source.normalized_text,
        )
        .where(~span_exists, Source.adapter_name == adapter_name)
        .order_by(Source.created_at, Source.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return [
        SpanlessSource(
            source_id=r.id,
            content_hash=r.content_hash,
            raw_bytes_ref=r.raw_bytes_ref,
            origin_url=r.origin_url,
            source_type=r.source_type,
            mime_type=r.mime_type,
            retrieved_at=r.retrieved_at,
            normalized_text=r.normalized_text,
        )
        for r in result.all()
    ]


async def lock_source_if_spanless(session: AsyncSession, source_id: UUID) -> bool:
    """Sperre die ``source``-Zeile exklusiv; ``True`` nur ohne span-Zeile.

    ``SELECT … FOR UPDATE`` löst keinen UPDATE-Trigger aus (der Append-only-
    Trigger bleibt unberührt). Die Prüfung „existiert eine span-Zeile?“ läuft in
    derselben Transaktion nach der Sperre. Kein Commit, kein Rollback.
    """
    await session.execute(select(Source.id).where(Source.id == source_id).with_for_update())
    has = await session.scalar(select(Span.id).where(Span.source_id == source_id).limit(1))
    return has is None
