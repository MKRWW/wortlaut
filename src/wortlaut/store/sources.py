"""Source-Dedup, Einfuegen und Eingabe-Struktur (Phase 0).

Dedup-Vorabcheck (source_exists), Eingabe-Dataclass (NewSource) und
Insert mit flush → commit (insert_source).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wortlaut.store.models import Source


async def source_exists(session: AsyncSession, content_hash: str) -> bool:
    """True, wenn bereits eine source mit diesem content_hash existiert (Dedup-Vorabcheck)."""
    result = await session.scalar(select(Source.id).where(Source.content_hash == content_hash))
    return result is not None


@dataclass(frozen=True)
class NewSource:
    """Einzufügende source-Zeile.

    ``normalized_text`` wird beim Insert gesetzt (Option A, #42) — nicht per
    späterem UPDATE (der Append-only-Trigger verbietet das). Bei Creation gesetzt
    friert es den kanonischen Text ein, gegen den die Span-Offsets zeigen
    (Grundlage Anti-Halluzination, R-DATA-06). ``None``, wenn normalize scheitert.
    """

    content_hash: str
    raw_bytes_ref: str
    archive_wayback: str | None
    archive_today: str | None
    origin_url: str
    source_type: str
    rights_basis: str
    adapter_name: str
    adapter_version: str
    byte_size: int
    mime_type: str
    retrieved_at: datetime
    normalized_text: str | None = None


async def insert_source(session: AsyncSession, row: NewSource) -> UUID:
    """Fügt die Zeile ein und committet; liefert die erzeugte id.

    IntegrityError (UNIQUE content_hash, FK) propagiert an den Aufrufer.
    """
    quelle = Source(
        source_type=row.source_type,
        rights_basis=row.rights_basis,
        adapter_name=row.adapter_name,
        adapter_version=row.adapter_version,
        origin_url=row.origin_url,
        content_hash=row.content_hash,
        byte_size=row.byte_size,
        mime_type=row.mime_type,
        retrieved_at=row.retrieved_at,
        raw_bytes_ref=row.raw_bytes_ref,
        archive_wayback=row.archive_wayback,
        archive_today=row.archive_today,
        warc_ref=None,
        normalized_text=row.normalized_text,
    )
    session.add(quelle)
    await session.flush()
    await session.commit()
    return quelle.id
