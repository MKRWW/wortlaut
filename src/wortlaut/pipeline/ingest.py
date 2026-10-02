"""Ingest-Pipeline (Phase 1): fetch → hash → dedup → WORM → normalize → insert source.

Erzwingt die Reihenfolge (Provenienz vor Verarbeitung, R-CORE-02). ``normalize``
läuft VOR dem source-Insert und friert ``source.normalized_text`` ein (Option A,
#42) — die Span-Offsets zeigen damit versions-robust in den gespeicherten Text
(Grundlage Anti-Halluzination, R-DATA-06). Parsing-Fehler dürfen die Provenienz
nie blockieren (AC6): die source wird auch bei kaputtem PDF gesichert.
Fremdbezeugung liegt seit #132 nicht mehr im Ingest, sondern in ``capture``/
``attest`` (ADR-0009): ein Archiv-Ausfall kostet einen Wiederholungslauf, kein
Dokument. Spans entstehen seit #126 nicht mehr beim Ingest — nur per ``reparse``
nach ``attest`` (ADR-0009).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from wortlaut.evidence.hashing import content_hash
from wortlaut.ingest.adapter import IngestAdapter, RawSource, SourceRef
from wortlaut.store.sources import NewSource, insert_source, source_exists
from wortlaut.store.worm import WormStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineDeps:
    """Die komponierten Bausteine als ein Abhängigkeits-Bündel (R-ARCH-04: ≤5 Params)."""

    adapter: IngestAdapter
    worm: WormStore


@dataclass(frozen=True)
class IngestOutcome:
    status: Literal["inserted", "skipped_duplicate"]
    source_id: UUID | None
    content_hash: str


async def ingest_source(
    ref: SourceRef,
    *,
    deps: PipelineDeps,
    session: AsyncSession,
    rights_basis: str,
) -> IngestOutcome:
    """Bringt eine Quelle in den Ledger (Provenienz zuerst); Spans nur per
    ``reparse`` nach ``attest`` (ADR-0009, #126)."""
    # 1. fetch · 2. hash über Rohbytes (R-DATA-02) · 3. dedup
    raw = await deps.adapter.fetch(ref)
    h = content_hash(raw.raw_bytes)
    if await source_exists(session, h):
        return IngestOutcome("skipped_duplicate", None, h)

    # 4. WORM-put (content-adressiert, Key = Hash)
    raw_bytes_ref = await deps.worm.put(h, raw.raw_bytes, content_type=raw.mime_type)

    # 5. normalize VOR dem Insert (Option A): Text einfrieren. Scheitert normalize,
    #    bleibt normalized_text NULL — die source wird trotzdem gesichert (AC6).
    normalized = _safe_normalize(deps.adapter, raw)
    row = NewSource(
        content_hash=h,
        raw_bytes_ref=raw_bytes_ref,
        archive_wayback=None,
        archive_today=None,
        origin_url=raw.origin_url,
        source_type=raw.source_type,
        rights_basis=rights_basis,
        adapter_name=deps.adapter.name,
        adapter_version=deps.adapter.version,
        byte_size=len(raw.raw_bytes),
        mime_type=raw.mime_type,
        retrieved_at=raw.retrieved_at,
        normalized_text=normalized,
    )

    # 6. insert source mit differenziertem Fehlerfang (Race → skipped_duplicate)
    try:
        source_id = await insert_source(session, row)
    except IntegrityError:
        await session.rollback()
        if await source_exists(session, h):
            return IngestOutcome("skipped_duplicate", None, h)
        raise

    return IngestOutcome("inserted", source_id, h)


def _safe_normalize(adapter: IngestAdapter, raw: RawSource) -> str | None:
    """normalize, aber Fehler blockieren die Provenienz nie (AC6, R-SEC-06)."""
    try:
        return adapter.normalize(raw)
    except Exception:  # untrusted PDF-Parsing darf die Provenienz nie brechen (AC6)
        logger.warning("normalize fehlgeschlagen (%s) — source ohne Spans", raw.origin_url)
        return None
