"""Ingest-Pipeline: fetch → hash → dedup → archiv → WORM → insert.

Erzwingt die Reihenfolge (Provenienz vor Verarbeitung, R-CORE-02).
Kein normalize/parse — Phase 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from wortlaut.archive.archiver import Archiver, archive_all
from wortlaut.evidence.hashing import content_hash
from wortlaut.ingest.adapter import IngestAdapter, SourceRef
from wortlaut.store.sources import NewSource, insert_source, source_exists
from wortlaut.store.worm import WormStore


@dataclass(frozen=True)
class PipelineDeps:
    """Die komponierten Bausteine #3–#6 als ein Abhängigkeits-Bündel (R-ARCH-04: ≤5 Params)."""

    adapter: IngestAdapter
    wayback: Archiver
    archive_today: Archiver
    worm: WormStore


@dataclass(frozen=True)
class IngestOutcome:
    status: Literal["inserted", "skipped_duplicate", "archive_failed"]
    source_id: UUID | None
    content_hash: str


async def ingest_source(
    ref: SourceRef,
    *,
    deps: PipelineDeps,
    session: AsyncSession,
    rights_basis: str,
) -> IngestOutcome:
    """Bringt eine entdeckte Quelle in den Ledger — streng in Provenienz-Reihenfolge."""
    # 1. fetch
    raw = await deps.adapter.fetch(ref)

    # 2. content_hash über Rohbytes (R-DATA-02)
    h = content_hash(raw.raw_bytes)

    # 3. dedup — existiert schon? → sofort beenden
    if await source_exists(session, h):
        return IngestOutcome("skipped_duplicate", None, h)

    # 4. fremdarchivieren (≥1 Archiv reicht)
    res = await archive_all(
        raw.origin_url,
        wayback=deps.wayback,
        archive_today=deps.archive_today,
    )

    # 5. beide None → archive_failed, kein worm/insert
    if res.wayback_url is None and res.archive_today_url is None:
        return IngestOutcome("archive_failed", None, h)

    # 6. WORM-put (content-adressiert, Key = Hash)
    raw_bytes_ref = await deps.worm.put(h, raw.raw_bytes, content_type=raw.mime_type)

    # 7. NewSource bauen
    row = NewSource(
        content_hash=h,
        raw_bytes_ref=raw_bytes_ref,
        archive_wayback=res.wayback_url,
        archive_today=res.archive_today_url,
        origin_url=raw.origin_url,
        source_type=raw.source_type,
        rights_basis=rights_basis,
        adapter_name=deps.adapter.name,
        adapter_version=deps.adapter.version,
        byte_size=len(raw.raw_bytes),
        mime_type=raw.mime_type,
        retrieved_at=raw.retrieved_at,
    )

    # 8. insert mit differenziertem Fehlerfang
    try:
        source_id = await insert_source(session, row)
    except IntegrityError:
        await session.rollback()
        if await source_exists(session, h):
            return IngestOutcome("skipped_duplicate", None, h)
        raise
    return IngestOutcome("inserted", source_id, h)
