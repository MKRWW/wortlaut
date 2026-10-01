"""Span-Nachzug: Spans für Quellen ohne Spans aus dem gespeicherten Text (Spec 0118).

Seit #126 ist ``reparse`` der reguläre Weg zu Spans: ``ingest`` erzeugt keine
mehr, und ``reparse`` wählt nur noch Quellen mit Attestierung (ADR-0009).

Schließt die Lücke von #93/AC4: eine Quelle, die **vor** einer Parser-Korrektur
erfasst wurde, hat ``normalized_text``, aber keine Spans; ein erneuter ``ingest``
meldet ``skipped_duplicate``, bevor irgendetwas geparst wird. Dieser Pass lädt
die Rohbytes **immer** aus WORM und rechnet den ``content_hash`` nach, **bevor**
geparst wird — Spans entstehen nur aus einer Quelle, deren Bindung an den Ledger
gerade selbst nachgerechnet wurde (Muster ``pipeline/timestamp.py``, #76/AC10).
Das eingefrorene ``normalized_text`` ist die Grundlage (R-DATA-06) — kein
erneutes normalize, kein Fetch, keine Fremdarchivierung: weder ``fetch`` noch
``discover`` wird aufgerufen (§4.4), und Quellen mit ≥ 1 Span werden nie
angefasst (append-only, R-DATA-01).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from wortlaut.evidence.hashing import content_hash
from wortlaut.ingest.adapter import IngestAdapter, RawSource
from wortlaut.pipeline.spans import write_spans
from wortlaut.store.reparse import SpanlessSource, lock_source_if_spanless
from wortlaut.store.worm import WormStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReparseOutcome:
    """Ergebnis des Span-Nachzugs für eine Quelle (Spec 0118 §3)."""

    status: Literal[
        "reparsed",
        "still_empty",
        "no_text",
        "skipped_has_spans",
        "hash_mismatch",
        "worm_missing",
        "error",
    ]
    source_id: UUID
    span_count: int = 0


async def reparse_source(
    source: SpanlessSource,
    *,
    session: AsyncSession,
    worm: WormStore,
    adapter: IngestAdapter,
) -> ReparseOutcome:
    """WORM lesen → Hash gegenprüfen → Sperre → Spans aus gespeichertem Text.

    Eine Transaktion pro Quelle (§4.3): die Zeilensperre mit Nachprüfung schützt
    gegen Doppel-Läufe; ``write_spans`` committet selbst. Fehler ab der Sperre
    → Rollback, die Quelle bleibt spanlos und wird beim nächsten Lauf gewählt.
    """
    # 1. Kein eingefrorener Text → nichts zu parsen. Kein WORM-Read.
    if source.normalized_text is None:
        return ReparseOutcome("no_text", source.source_id)

    # 2. Rohbytes immer aus WORM. Jeder Fehler → worm_missing.
    try:
        raw_bytes = await worm.get(source.raw_bytes_ref)
    except Exception:
        logger.warning("WORM-Read fehlgeschlagen beim Reparse für source %s", source.source_id)
        return ReparseOutcome("worm_missing", source.source_id)

    # 3. Hash gegen Ledger nachrechnen VOR dem Parsing. Der Parser wird in diesem
    #    Fall NICHT aufgerufen (Muster pipeline/timestamp.py).
    if content_hash(raw_bytes) != source.content_hash:
        logger.error(
            "hash_mismatch beim Reparse: source %s (WORM-Bytes passen nicht zum Ledger-Hash)",
            source.source_id,
        )
        return ReparseOutcome("hash_mismatch", source.source_id)

    raw = RawSource(
        origin_url=source.origin_url,
        source_type=source.source_type,
        raw_bytes=raw_bytes,
        mime_type=source.mime_type,
        retrieved_at=source.retrieved_at,
    )

    try:
        # 4. Sperre + Nachprüfung in derselben Transaktion.
        if not await lock_source_if_spanless(session, source.source_id):
            await session.rollback()
            return ReparseOutcome("skipped_has_spans", source.source_id)

        # 5. Spans mit exakt der Ingest-Logik (committet selbst).
        span_count = await write_spans(
            session,
            adapter=adapter,
            raw=raw,
            normalized=source.normalized_text,
            source_id=source.source_id,
        )

        # 6. Parser lieferte nichts → bleibt spanlos, aber sichtbar.
        if span_count == 0:
            await session.rollback()
            logger.warning("reparse: source %s bleibt ohne Spans (still_empty)", source.source_id)
            return ReparseOutcome("still_empty", source.source_id)
        return ReparseOutcome("reparsed", source.source_id, span_count)
    except Exception:
        # 7. Alles oder nichts: Fehler ab der Sperre → Rollback, die Quelle hat
        #    weiterhin null Spans und wird beim nächsten Lauf wieder ausgewählt.
        await session.rollback()
        logger.exception("reparse: Fehler bei source %s — Quelle bleibt spanlos", source.source_id)
        return ReparseOutcome("error", source.source_id)
