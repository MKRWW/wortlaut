"""Persistenz der RFC-3161-Zeitstempel (Spec 0076 §3.3).

Append-only: INSERT ja, UPDATE/DELETE nein (``source_timestamp`` hat ihren eigenen
DB-Trigger, R-DATA-01). Das Token selbst lebt als WORM-Objekt; die Zeile hält nur
die Ref. Kein ``timestamp_pending``-Flag — „pending“ ist abgeleitet (keine Zeile).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from wortlaut.store.models import Source, SourceTimestamp


@dataclass(frozen=True)
class NewSourceTimestamp:
    """Einzufügende source_timestamp-Zeile (append-only)."""

    source_id: UUID
    tsa_name: str
    token_ref: str  # s3://bucket/key?versionId=...


@dataclass(frozen=True)
class PendingSource:
    """Eine ``source`` ohne Zeitstempel — Kandidat des Stempel-Passes (abgeleitet)."""

    source_id: UUID
    content_hash: str
    raw_bytes_ref: str


@dataclass(frozen=True)
class SourceTimestampRow:
    """Eine gespeicherte source_timestamp-Zeile (Ref + TSA + Zeitstempel-Zeit)."""

    tsa_name: str
    token_ref: str
    created_at: datetime


async def insert_source_timestamp(session: AsyncSession, row: NewSourceTimestamp) -> UUID:
    """Fügt die Zeile ein und committet; liefert die erzeugte id.

    ``IntegrityError`` (UNIQUE ``(source_id, tsa_name)``, FK) propagiert an den
    Aufrufer (Muster :func:`wortlaut.store.sources.insert_source`).
    """
    zeile = SourceTimestamp(
        source_id=row.source_id,
        tsa_name=row.tsa_name,
        token_ref=row.token_ref,
    )
    session.add(zeile)
    await session.flush()
    await session.commit()
    return zeile.id


async def list_sources_without_timestamp(
    session: AsyncSession, *, limit: int | None = None
) -> list[PendingSource]:
    """Alle ``source`` ohne eine ``source_timestamp``-Zeile (abgeleitet „pending“).

    Stabil sortiert nach ``created_at, id``; optionales ``limit``. Kein
    UPDATE/DELETE — nur Read.
    """
    ts_exists = exists().where(SourceTimestamp.source_id == Source.id)
    stmt = (
        select(Source.id, Source.content_hash, Source.raw_bytes_ref)
        .where(~ts_exists)
        .order_by(Source.created_at, Source.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return [
        PendingSource(
            source_id=r.id,
            content_hash=r.content_hash,
            raw_bytes_ref=r.raw_bytes_ref,
        )
        for r in result.all()
    ]


async def get_timestamps_for_source(
    session: AsyncSession, source_id: UUID
) -> list[SourceTimestampRow]:
    """Alle ``source_timestamp``-Zeilen einer Quelle, sortiert nach ``created_at``."""
    stmt = (
        select(SourceTimestamp)
        .where(SourceTimestamp.source_id == source_id)
        .order_by(SourceTimestamp.created_at)
    )
    result = await session.execute(stmt)
    return [
        SourceTimestampRow(
            tsa_name=r.tsa_name,
            token_ref=r.token_ref,
            created_at=r.created_at,
        )
        for r in result.scalars().all()
    ]
