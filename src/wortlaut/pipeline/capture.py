"""Capture-Schritt: Bezeugungs-Anforderung für unattestierte Quellen (Spec 0130).

Reihenfolge je Quelle (Spec 0130 §4.3): WORM lesen → Hash gegen das Ledger
nachrechnen → **zuerst** CDX nachfragen (nur lesend wie ``attest``) → erst
wenn kein byte-gleicher Snapshot steht, einen Save-Page-Now-Auftrag an
Wayback auslösen. Jede Stufe vor dem Netz entscheidet zuerst:
``worm_missing`` und ``hash_mismatch`` bedeuten, dass weder Lookup noch
Capture passieren. ``already_archived`` und ``error`` schreiben **keine**
Zeile (die Bezeugung ist Sache von ``attest``); ``captured`` und ``failed``
protokollieren sich in ``capture_request`` (AC11). archive.today ist kein
Anker (ADR-0009 §3) und wird im Capture-Pfad nie aufgerufen (AC10).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from wortlaut.archive.archiver import Archiver
from wortlaut.archive.errors import ArchiveError
from wortlaut.archive.wayback_lookup import WaybackLookup
from wortlaut.evidence.hashing import content_hash, sha1_base32
from wortlaut.store.captures import CaptureCandidate, NewCaptureRequest, insert_capture_request
from wortlaut.store.worm import WormStore

logger = logging.getLogger(__name__)

# Einziges Archiv im Capture-Pfad (Spec 0130 §0c): archive.today ist kein Anker.
ARCHIVER = "wayback"


@dataclass(frozen=True)
class CaptureOutcome:
    """Ergebnis des Capture-Schritts für eine Quelle (Spec 0130 §3).

    ``failed`` ist ein normales Ergebnis (protokolliert + abgekühlt), kein
    Programmfehler; nur ``hash_mismatch`` und ``error`` sind Alarm.
    """

    status: Literal[
        "captured",
        "already_archived",
        "failed",
        "hash_mismatch",
        "worm_missing",
        "error",
    ]
    source_id: UUID
    snapshot_url: str | None = None
    reason: str | None = None


async def capture_source(
    candidate: CaptureCandidate,
    *,
    session: AsyncSession,
    worm: WormStore,
    lookup: WaybackLookup,
    wayback: Archiver,
) -> CaptureOutcome:
    """Eine Quelle per Spec 0130 §4.3: WORM → Hash → CDX → ggf. Capture.

    Die WORM-Bytes werden **vor** jedem Netzaufruf gegen das Ledger geprüft;
    der CDX-Check (SHA-1 nur als Vorfilter) geht dem Capture voraus — ein
    byte-gleicher Snapshot macht den Capture überflüssig (Gast-Verhalten,
    Spec 0130 §0a). Andere Ausnahmen als ``ArchiveError`` werden nicht
    abgefangen und propagieren an den Aufrufer.
    """
    # 1. WORM lesen; jeder Fehler → worm_missing. Kein Netz.
    try:
        raw = await worm.get(candidate.raw_bytes_ref)
    except Exception:
        logger.warning("Capture: WORM-Read fehlgeschlagen für source %s", candidate.source_id)
        return CaptureOutcome("worm_missing", candidate.source_id)

    # 2. Hash gegen das Ledger nachrechnen VOR dem Netzaufruf.
    if content_hash(raw) != candidate.content_hash:
        logger.error(
            "hash_mismatch beim Capture: source %s (WORM-Bytes passen nicht zum Ledger-Hash)",
            candidate.source_id,
        )
        return CaptureOutcome("hash_mismatch", candidate.source_id)

    # 3. Zuerst CDX (nur lesend): ein byte-gleicher Snapshot ist bereits da →
    #    kein Capture, keine Zeile (attest übernimmt).
    try:
        candidates = await lookup.candidates(candidate.origin_url, sha1_b32=sha1_base32(raw))
    except ArchiveError as exc:
        logger.warning(
            "Capture: Kandidatensuche fehlgeschlagen für source %s: %s",
            candidate.source_id,
            exc.label(),
        )
        return CaptureOutcome("error", candidate.source_id)
    if candidates:
        return CaptureOutcome("already_archived", candidate.source_id)

    # 4. Kein Snapshot mit unserem Digest: genau ein Save-Page-Now-Auftrag.
    try:
        url = await wayback.archive(candidate.origin_url)
    except ArchiveError as exc:
        await insert_capture_request(
            session,
            NewCaptureRequest(
                source_id=candidate.source_id,
                archiver=ARCHIVER,
                outcome="failed",
                snapshot_url=None,
                reason=exc.label(),
            ),
        )
        return CaptureOutcome("failed", candidate.source_id, reason=exc.label())

    await insert_capture_request(
        session,
        NewCaptureRequest(
            source_id=candidate.source_id,
            archiver=ARCHIVER,
            outcome="captured",
            snapshot_url=url,
            reason=None,
        ),
    )
    return CaptureOutcome("captured", candidate.source_id, snapshot_url=url)
