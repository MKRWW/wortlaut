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
from datetime import date
from typing import Literal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from wortlaut.archive.archiver import Archiver, archive_all
from wortlaut.evidence.hashing import content_hash, span_hash
from wortlaut.ingest.adapter import IngestAdapter, RawSource, SourceRef
from wortlaut.store.sources import NewSource, insert_source, source_exists
from wortlaut.store.spans import (
    NewSpan,
    init_span_state,
    insert_span,
    resolve_or_create_mandate,
    resolve_or_create_speaker,
)
from wortlaut.store.worm import WormStore

logger = logging.getLogger(__name__)

_PARLIAMENT = "bundestag"  # MVP: DIP-Bundestag; Landtage später


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

    # 4./5. fremdarchivieren (≥1 Archiv reicht, sonst kein Insert)
    res = await archive_all(raw.origin_url, wayback=deps.wayback, archive_today=deps.archive_today)
    if res.wayback_url is None and res.archive_today_url is None:
        return IngestOutcome("archive_failed", None, h)

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

    # 9. Spans nur, wenn ein kanonischer Text existiert (sonst source-only, AC6)
    span_count = 0
    if normalized is not None:
        span_count = await _ingest_spans(
            session,
            adapter=deps.adapter,
            raw=raw,
            normalized=normalized,
            source_id=source_id,
        )
    return IngestOutcome("inserted", source_id, h, span_count)


def _safe_normalize(adapter: IngestAdapter, raw: RawSource) -> str | None:
    """normalize, aber Fehler blockieren die Provenienz nie (AC6, R-SEC-06)."""
    try:
        return adapter.normalize(raw)
    except Exception:  # untrusted PDF-Parsing darf die Provenienz nie brechen (AC6)
        logger.warning("normalize fehlgeschlagen (%s) — source ohne Spans", raw.origin_url)
        return None


async def _ingest_spans(
    session: AsyncSession,
    *,
    adapter: IngestAdapter,
    raw: RawSource,
    normalized: str,
    source_id: UUID,
) -> int:
    """parse → je Redebeitrag Sprecher/Mandat auflösen + span + span_state schreiben."""
    try:
        drafts = list(adapter.parse(raw, normalized))
    except Exception:  # Parsing-Fehler blockieren die Provenienz nie (AC6)
        logger.warning("parse fehlgeschlagen (source=%s) — keine Spans", source_id)
        return 0

    verification = "official" if adapter.trust_level == "verified_primary" else "machine"
    count = 0
    for draft in drafts:
        if not draft.spoken_at:  # fail-loud: kein Datum → kein Span (nie Falsch-Datum)
            logger.warning("Span ohne spoken_at übersprungen (source=%s)", source_id)
            continue
        spoken = date.fromisoformat(draft.spoken_at)
        party_raw = draft.speaker_hint.get("party")
        party = str(party_raw) if party_raw else None
        speaker_id = await resolve_or_create_speaker(session, str(draft.speaker_hint["name"]))
        mandate_id = await resolve_or_create_mandate(
            session,
            speaker_id=speaker_id,
            party=party,
            active_from=spoken,
            parliament=_PARLIAMENT,
        )
        span_id = await insert_span(
            session,
            NewSpan(
                source_id=source_id,
                speaker_id=speaker_id,
                mandate_id=mandate_id,
                verbatim_text=draft.verbatim_text,
                text_start=draft.text_start,
                text_end=draft.text_end,
                spoken_at=spoken,
                locator=draft.locator,
                permalink=draft.permalink,
                span_hash=span_hash(draft.verbatim_text),
            ),
        )
        await init_span_state(
            session, span_id=span_id, verification=verification, visibility="public"
        )
        count += 1
    await session.commit()
    return count
