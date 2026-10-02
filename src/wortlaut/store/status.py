"""Rückstand der Erfassungs-Pipeline (Spec 0132 §4.4).

Seit #132 läuft der Betrieb in fester Reihenfolge: ``ingest`` → ``timestamp``
→ ``capture`` → ``attest`` → ``reparse``. ``status`` macht den Rückstand je
Stufe sichtbar. Alle Zustände sind abgeleitet (keine Flag-Spalte) — das
Muster von ``source_timestamp`` (#76) und ``source_archive`` (#124): eine
Quelle ohne Zeile ist in der jeweiligen Stufe offen. „Letzte
Capture-Anfrage“ heißt wie in :mod:`wortlaut.store.captures`: ``DISTINCT ON``
nach ``requested_at`` absteigend, bei Gleichstand die neuere Zeile.

Nur Read: keine Migration, kein Netz, kein Write.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Select, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from wortlaut.store.models import CaptureRequest, Source, SourceArchive, SourceTimestamp, Span


@dataclass(frozen=True)
class BacklogCounts:
    """Rückstand je Pipeline-Stufe (Spec 0132 §4.4).

    ``unattested_capture_failed`` zählt die unattestierten Quellen, deren
    LETZTE ``capture_request`` ``failed`` ist — die Wiederholungskandidaten
    des nächsten ``capture``-Laufs.
    """

    sources: int
    unstamped: int
    unattested: int
    unattested_capture_failed: int
    attested_without_spans: int


async def _count(session: AsyncSession, statement: Select[tuple[int]]) -> int:
    """``count(*)`` als int — ``scalar`` ist für mypy ``int | None``, ein count nie None."""
    return int(await session.scalar(statement) or 0)


async def backlog_counts(session: AsyncSession) -> BacklogCounts:
    """Zählt die Quellen je Zustand (Spec 0132 §4.4). Nur Read."""
    stamped = exists().where(SourceTimestamp.source_id == Source.id)
    attested = exists().where(SourceArchive.source_id == Source.id)
    has_span = exists().where(Span.source_id == Source.id)

    # Letzte ``capture_request``-Zeile je Quelle (Muster ``store/captures.py``).
    # Quellen ohne Anfrage haben ``outcome`` NULL und fallen aus.
    last = (
        select(
            CaptureRequest.source_id,
            CaptureRequest.outcome,
            CaptureRequest.requested_at,
        )
        .order_by(
            CaptureRequest.source_id,
            CaptureRequest.requested_at.desc(),
            CaptureRequest.id.desc(),
        )
        .distinct(CaptureRequest.source_id)
        .subquery()
    )

    sources = await _count(session, select(func.count()).select_from(Source))
    unstamped = await _count(session, select(func.count()).select_from(Source).where(~stamped))
    unattested = await _count(session, select(func.count()).select_from(Source).where(~attested))
    unattested_capture_failed = await _count(
        session,
        select(func.count())
        .select_from(Source)
        .outerjoin(last, last.c.source_id == Source.id)
        .where(~attested)
        .where(last.c.outcome == "failed"),
    )
    attested_without_spans = await _count(
        session, select(func.count()).select_from(Source).where(attested).where(~has_span)
    )
    return BacklogCounts(
        sources=sources,
        unstamped=unstamped,
        unattested=unattested,
        unattested_capture_failed=unattested_capture_failed,
        attested_without_spans=attested_without_spans,
    )
