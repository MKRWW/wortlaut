"""Persistenz der Capture-Anfragen (Spec 0130, ADR-0009 §3).

Append-only: INSERT ja, UPDATE/DELETE nein (``capture_request`` hat ihren
eigenen DB-Trigger, R-DATA-01). Die Tabelle ist das Gedächtnis gegen
Doppel-Captures (Spec 0130 §0b): ein Snapshot ist erst Stunden bis Tage
später im CDX-Index abrufbar (#124 §0d), und in dieser Zeit darf kein
neuer Capture pro Lauf ausgelöst werden. Die Auswahl ist abgeleitet
(keine Flag-Spalte): keine ``source_archive``-Zeile **und** die letzte
Anfrage fehlt oder ist entsprechend der Abkühlzeit abgekühlt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from wortlaut.store.models import CaptureRequest, Source, SourceArchive


@dataclass(frozen=True)
class CaptureCandidate:
    """Eine ``source`` ohne byte-gleichen Snapshot und abgekühlte letzte Anfrage."""

    source_id: UUID
    content_hash: str
    raw_bytes_ref: str
    origin_url: str


@dataclass(frozen=True)
class NewCaptureRequest:
    """Einzufügende capture_request-Zeile (append-only).

    ``captured`` trägt die Snapshot-URL, ``failed`` den strukturierten Grund
    (``ArchiveError.label()``) — die Kombination erzwingt
    ``chk_capture_outcome`` in der Datenbank (AC1).
    """

    source_id: UUID
    archiver: str
    outcome: Literal["captured", "failed"]
    snapshot_url: str | None
    reason: str | None


async def list_sources_needing_capture(
    session: AsyncSession,
    *,
    now: datetime,
    captured_cooldown: timedelta,
    failed_cooldown: timedelta,
    limit: int | None = None,
) -> list[CaptureCandidate]:
    """Alle Quellen, für die **alle** gelten (Spec 0130 §4.2):

    1. keine ``source_archive``-Zeile (unattestiert),
    2. die letzte ``capture_request``-Zeile fehlt, **oder** sie ist
       ``captured`` und älter als ``captured_cooldown``, **oder** sie ist
       ``failed`` und älter als ``failed_cooldown``.

    ``now`` wird übergeben (testbar, keine versteckte Uhr); die Abkühlzeiten
    sind gebundene Parameter. Stabil sortiert nach ``created_at, id``;
    optionales ``limit``. Nur Read.
    """
    # Letzte ``capture_request``-Zeile je Quelle (``DISTINCT ON``, nach
    # ``requested_at`` absteigend; bei Gleichstand die neuere Zeile).
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
    attested = exists().where(SourceArchive.source_id == Source.id)
    # Schwellen in Python rechnen und direkt gegen die timestamptz-Spalte vergleichen:
    # ``requested_at + interval < :now`` liesse SQLAlchemy ``now`` als timestamp OHNE
    # Zeitzone binden (DateTime + Interval -> DateTime), und asyncpg lehnt das ab.
    captured_cutoff = now - captured_cooldown
    failed_cutoff = now - failed_cooldown
    captured_cooled = (last.c.outcome == "captured") & (last.c.requested_at < captured_cutoff)
    failed_cooled = (last.c.outcome == "failed") & (last.c.requested_at < failed_cutoff)
    statement = (
        select(
            Source.id,
            Source.content_hash,
            Source.raw_bytes_ref,
            Source.origin_url,
        )
        .outerjoin(last, last.c.source_id == Source.id)
        .where(~attested)
        .where(last.c.source_id.is_(None) | captured_cooled | failed_cooled)
        .order_by(Source.created_at, Source.id)
    )
    if limit is not None:
        statement = statement.limit(limit)
    result = await session.execute(statement)
    return [
        CaptureCandidate(
            source_id=r.id,
            content_hash=r.content_hash,
            raw_bytes_ref=r.raw_bytes_ref,
            origin_url=r.origin_url,
        )
        for r in result.all()
    ]


async def insert_capture_request(session: AsyncSession, row: NewCaptureRequest) -> UUID:
    """Fügt die Zeile ein und committet; liefert die erzeugte id.

    ``IntegrityError`` (``chk_capture_outcome``, FK) propagiert an den
    Aufrufer (Muster :func:`wortlaut.store.attestations.insert_source_archive`).
    """
    zeile = CaptureRequest(
        source_id=row.source_id,
        archiver=row.archiver,
        outcome=row.outcome,
        snapshot_url=row.snapshot_url,
        reason=row.reason,
    )
    session.add(zeile)
    await session.flush()
    await session.commit()
    return zeile.id
