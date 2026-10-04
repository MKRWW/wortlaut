"""Span-Erzeugung aus kanonischem Text, gemeinsam genutzt von ``ingest`` und ``reparse`` (#118).

Umzug aus ``pipeline/ingest.py::_ingest_spans`` (Logik unverändert, Spec 0118 §0b):
``reparse`` braucht dieselbe Span-Logik, darf ``pipeline/ingest.py`` aber nicht
importieren — das würde es indirekt an ``wortlaut.archive`` hängen (import-linter
wertet bei forbidden-Contracts auch indirekte Ketten).
"""

from __future__ import annotations

import logging
from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from wortlaut.evidence.hashing import span_hash
from wortlaut.ingest.adapter import IngestAdapter, RawSource
from wortlaut.store.spans import (
    Chamber,
    NewSpan,
    init_span_state,
    insert_span,
    resolve_or_create_mandate,
    resolve_or_create_speaker,
)

logger = logging.getLogger(__name__)


async def write_spans(
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
    chamber = Chamber(parliament=adapter.parliament, role=adapter.mandate_role)
    count = 0
    for draft in drafts:
        if not draft.spoken_at:  # fail-loud: kein Datum → kein Span (nie Falsch-Datum)
            logger.warning("Span ohne spoken_at übersprungen (source=%s)", source_id)
            continue
        spoken = date.fromisoformat(draft.spoken_at)
        party_raw = draft.speaker_hint.get("party")
        party = str(party_raw) if party_raw else None
        speaker_id = await resolve_or_create_speaker(
            session, str(draft.speaker_hint["name"]), parliament=adapter.parliament
        )
        mandate_id = await resolve_or_create_mandate(
            session,
            speaker_id=speaker_id,
            party=party,
            active_from=spoken,
            chamber=chamber,
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
