"""Persistenz der Attestierung (Spec 0124, ADR-0009 §2).

Append-only: INSERT ja, UPDATE/DELETE nein (``source_archive`` hat ihren
eigenen DB-Trigger, R-DATA-01). Die Gleichheit von ``verified_sha256`` mit
``source.content_hash`` erzwingt zusätzlich ein BEFORE-INSERT-Trigger — die
Datenbank verweigert einen ungleichen Insert (AC1), unabhängig vom
Anwendungscode. „Unattestiert“ ist abgeleitet (keine Zeile), kein Flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from wortlaut.store.models import Source, SourceArchive


@dataclass(frozen=True)
class PendingAttestation:
    """Eine ``source`` ohne Attestierung — Kandidat des Attestierungs-Passes (abgeleitet)."""

    source_id: UUID
    content_hash: str
    raw_bytes_ref: str
    origin_url: str
    retrieved_at: datetime


@dataclass(frozen=True)
class NewSourceArchive:
    """Einzufügende source_archive-Zeile (append-only)."""

    source_id: UUID
    archiver: str
    snapshot_url: str
    snapshot_at: datetime
    verified_sha256: str


@dataclass(frozen=True)
class SourceArchiveRow:
    """Eine gespeicherte source_archive-Zeile (Archivar + Snapshot + geprüfter Hash)."""

    archiver: str
    snapshot_url: str
    snapshot_at: datetime
    verified_sha256: str


async def list_sources_without_attestation(
    session: AsyncSession, *, limit: int | None = None
) -> list[PendingAttestation]:
    """Alle ``source`` ohne eine ``source_archive``-Zeile (abgeleitet „pending“).

    Stabil sortiert nach ``created_at, id``; optionales ``limit``. Kein
    UPDATE/DELETE — nur Read.
    """
    attested = exists().where(SourceArchive.source_id == Source.id)
    stmt = (
        select(
            Source.id,
            Source.content_hash,
            Source.raw_bytes_ref,
            Source.origin_url,
            Source.retrieved_at,
        )
        .where(~attested)
        .order_by(Source.created_at, Source.id)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    result = await session.execute(stmt)
    return [
        PendingAttestation(
            source_id=r.id,
            content_hash=r.content_hash,
            raw_bytes_ref=r.raw_bytes_ref,
            origin_url=r.origin_url,
            retrieved_at=r.retrieved_at,
        )
        for r in result.all()
    ]


async def insert_source_archive(session: AsyncSession, row: NewSourceArchive) -> UUID:
    """Fügt die Zeile ein und committet; liefert die erzeugte id.

    ``IntegrityError`` (UNIQUE ``(source_id, archiver)``, FK) propagiert an den
    Aufrufer (Muster :func:`wortlaut.store.timestamps.insert_source_timestamp`).
    Die Trigger-Verweigerung ungleicher Hashes ist eine andere Ausnahme und
    wird hier nicht abgefangen.
    """
    zeile = SourceArchive(
        source_id=row.source_id,
        archiver=row.archiver,
        snapshot_url=row.snapshot_url,
        snapshot_at=row.snapshot_at,
        verified_sha256=row.verified_sha256,
    )
    session.add(zeile)
    await session.flush()
    await session.commit()
    return zeile.id


async def get_attestations_for_source(
    session: AsyncSession, source_id: UUID
) -> list[SourceArchiveRow]:
    """Alle ``source_archive``-Zeilen einer Quelle, sortiert nach ``created_at, id``.

    Nur Read (Spec 0128 §0a): die Attestierung wird ausgewiesen, nicht abgerufen —
    kein Netzzugriff, keine WORM-Lesung.
    """
    stmt = (
        select(SourceArchive)
        .where(SourceArchive.source_id == source_id)
        .order_by(SourceArchive.created_at, SourceArchive.id)
    )
    result = await session.execute(stmt)
    return [
        SourceArchiveRow(
            archiver=r.archiver,
            snapshot_url=r.snapshot_url,
            snapshot_at=r.snapshot_at,
            verified_sha256=r.verified_sha256,
        )
        for r in result.scalars().all()
    ]
