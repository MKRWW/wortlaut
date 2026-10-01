"""Ingest-Pipeline (Phase 1): fetch→hash→dedup→archiv→WORM→insert source→spans.

Erzwingt die Reihenfolge (Provenienz vor Verarbeitung, R-CORE-02). ``normalize``
läuft VOR dem source-Insert und friert ``source.normalized_text`` ein (Option A,
#42) — die Span-Offsets zeigen damit versions-robust in den gespeicherten Text
(Grundlage Anti-Halluzination, R-DATA-06). Parsing-Fehler dürfen die Provenienz
nie blockieren (AC6): die source wird auch bei kaputtem PDF gesichert.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from wortlaut.archive.archiver import Archiver, archive_all
from wortlaut.evidence.hashing import content_hash
from wortlaut.ingest.adapter import IngestAdapter, RawSource, SourceRef
from wortlaut.pipeline.spans import write_spans
from wortlaut.store.sources import NewSource, insert_source, source_exists
from wortlaut.store.worm import WormStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineDeps:
    """Die komponierten Bausteine als ein Abhängigkeits-Bündel (R-ARCH-04: ≤5 Params)."""

    adapter: IngestAdapter
    wayback: Archiver
    archive_today: Archiver
    worm: WormStore


@dataclass(frozen=True)
class IngestOutcome:
    status: Literal["inserted", "skipped_duplicate", "archive_failed"]
    source_id: UUID | None
    content_hash: str
    span_count: int = 0
    archive_failures: tuple[str, ...] = ()  # ArchiveError.label() je Dienst


async def ingest_source(
    ref: SourceRef,
    *,
    deps: PipelineDeps,
    session: AsyncSession,
    rights_basis: str,
) -> IngestOutcome:
    """Bringt eine Quelle in den Ledger und erzeugt ihre Spans (Provenienz zuerst)."""
    # 1. fetch · 2. hash über Rohbytes (R-DATA-02) · 3. dedup
    raw = await deps.adapter.fetch(ref)
    h = content_hash(raw.raw_bytes)
    if await source_exists(session, h):
        return IngestOutcome("skipped_duplicate", None, h)

    # 4./5. fremdarchivieren — Wayback ist der Pflicht-Anker (Q2): ohne
    #    wayback_url kein Insert, unabhängig von archive.today.
    res = await archive_all(raw.origin_url, wayback=deps.wayback, archive_today=deps.archive_today)
    # Observability: JEDE Failure wird geloggt — auch bei Erfolg (Soft-Failure).
    for dienst, fehler in res.failures.items():
        logger.warning("archive %s fehlgeschlagen (%s): %s", dienst, raw.origin_url, fehler)
    if res.wayback_url is None:
        return IngestOutcome(
            "archive_failed",
            None,
            h,
            archive_failures=tuple(f.label() for f in res.failures.values()),
        )

    # 6. WORM-put (content-adressiert, Key = Hash)
    raw_bytes_ref = await deps.worm.put(h, raw.raw_bytes, content_type=raw.mime_type)

    # 7. normalize VOR dem Insert (Option A): Text einfrieren. Scheitert normalize,
    #    bleibt normalized_text NULL — die source wird trotzdem gesichert (AC6).
    normalized = _safe_normalize(deps.adapter, raw)
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
        normalized_text=normalized,
    )

    # 8. insert source mit differenziertem Fehlerfang (Race → skipped_duplicate)
    try:
        source_id = await insert_source(session, row)
    except IntegrityError:
        await session.rollback()
        if await source_exists(session, h):
            return IngestOutcome("skipped_duplicate", None, h)
        raise

    # 9. Spans nur, wenn ein kanonischer Text existiert (sonst source-only, AC6).
    #    Soft-Failures (z.B. archive.today) werden trotzdem nach oben gereicht.
    span_count = 0
    if normalized is not None:
        span_count = await write_spans(
            session,
            adapter=deps.adapter,
            raw=raw,
            normalized=normalized,
            source_id=source_id,
        )
    return IngestOutcome(
        "inserted",
        source_id,
        h,
        span_count,
        tuple(f.label() for f in res.failures.values()),
    )


def _safe_normalize(adapter: IngestAdapter, raw: RawSource) -> str | None:
    """normalize, aber Fehler blockieren die Provenienz nie (AC6, R-SEC-06)."""
    try:
        return adapter.normalize(raw)
    except Exception:  # untrusted PDF-Parsing darf die Provenienz nie brechen (AC6)
        logger.warning("normalize fehlgeschlagen (%s) — source ohne Spans", raw.origin_url)
        return None
