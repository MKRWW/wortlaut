"""Attestierungs-Pass: Wayback-Attestierung für Quellen ohne Attestierung (Spec 0124).

Nur lesend: WORM lesen → Hash gegen ``content_hash`` nachrechnen → CDX-Suche
(SHA-1 nur als Vorfilter) → exakter ``id_``-Abruf → **SHA-256** gegen den
Ledger. Eine ``source_archive``-Zeile entsteht nur bei nachgewiesener
Gleichheit; die Datenbank erzwingt die Gleichheit zusätzlich per Trigger
(AC1). Kein Capture, keine Zugangsdaten, keine Umleitungen (ADR-0009 §2/§3,
Spec 0124 §4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from wortlaut.archive.errors import ArchiveError
from wortlaut.archive.wayback_lookup import SnapshotCandidate, WaybackLookup, snapshot_url
from wortlaut.evidence.hashing import content_hash, sha1_base32
from wortlaut.store.attestations import NewSourceArchive, PendingAttestation, insert_source_archive
from wortlaut.store.worm import WormStore

logger = logging.getLogger(__name__)

# Die Registry ist Code, nicht Konfiguration: ein neuer Archivar kostet einen
# Review (ADR-0009 §3).
ATTESTING_ARCHIVERS: tuple[str, ...] = ("wayback",)


@dataclass(frozen=True)
class AttestOutcome:
    """Ergebnis der Attestierung für eine Quelle (Spec 0124 §3)."""

    status: Literal[
        "attested",
        "no_matching_snapshot",
        "snapshot_unavailable",
        "bytes_mismatch",
        "hash_mismatch",
        "worm_missing",
        "error",
    ]
    source_id: UUID
    snapshot_url: str | None = None


def _candidate_at(candidate: SnapshotCandidate) -> datetime:
    """Zeitstempel des Kandidaten als UTC-Datum (YYYYMMDDhhmmss → aware datetime)."""
    return datetime.strptime(candidate.timestamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)


def _ordered_candidates(
    candidates: list[SnapshotCandidate], retrieved_at: datetime
) -> list[SnapshotCandidate]:
    """Nach absolutem Abstand des Zeitstempels zu ``retrieved_at``, nächster zuerst."""
    return sorted(
        candidates,
        key=lambda c: abs((_candidate_at(c) - retrieved_at).total_seconds()),
    )


async def attest_source(
    pending: PendingAttestation,
    *,
    session: AsyncSession,
    worm: WormStore,
    lookup: WaybackLookup,
    max_candidates: int = 3,
) -> AttestOutcome:
    """Reihenfolge (Spec 0124 §4.4): WORM → Hash → CDX → Fetch → SHA-256 → Zeile.

    Jede Stufe vor dem Netzaufruf entscheidet zuerst: ``hash_mismatch`` und
    ``worm_missing`` bedeuten, dass der Lookup **nicht** aufgerufen wird.
    """
    # 1. WORM lesen; jeder Fehler → worm_missing.
    try:
        raw = await worm.get(pending.raw_bytes_ref)
    except Exception:
        logger.warning("Attestierung: WORM-Read fehlgeschlagen für source %s", pending.source_id)
        return AttestOutcome("worm_missing", pending.source_id)

    # 2. Hash gegen Ledger nachrechnen VOR dem Netzaufruf.
    if content_hash(raw) != pending.content_hash:
        logger.error(
            "hash_mismatch bei der Attestierung: source %s (WORM-Bytes passen nicht zum "
            "Ledger-Hash)",
            pending.source_id,
        )
        return AttestOutcome("hash_mismatch", pending.source_id)

    # 3. CDX-Suche; SHA-1 ist nur der Vorfilter, nie der Beweis.
    try:
        candidates = await lookup.candidates(pending.origin_url, sha1_b32=sha1_base32(raw))
    except ArchiveError as exc:
        logger.warning(
            "Attestierung: Kandidatensuche fehlgeschlagen für source %s: %s",
            pending.source_id,
            exc.label(),
        )
        return AttestOutcome("error", pending.source_id)

    # 4. Keine Kandidaten: Befund über die Quelle, kein technischer Fehler.
    if not candidates:
        return AttestOutcome("no_matching_snapshot", pending.source_id)

    # 5. Nächste Kandidaten zuerst, höchstens ``max_candidates`` Abrufe.
    had_sha256_mismatch = False
    for candidate in _ordered_candidates(candidates, pending.retrieved_at)[:max_candidates]:
        try:
            fetched = await lookup.fetch(candidate)
        except ArchiveError as exc:
            # Kein Antworttext im Log (R-SEC-07) — nur der strukturierte Grund.
            logger.warning(
                "Attestierung: Snapshot-Abruf fehlgeschlagen für source %s: %s",
                pending.source_id,
                exc.label(),
            )
            return AttestOutcome("error", pending.source_id)

        if fetched is None:
            continue

        # Beweis ist der SHA-256 über die geladenen Bytes.
        fetched_hash = content_hash(fetched)
        if fetched_hash == pending.content_hash:
            try:
                await insert_source_archive(
                    session,
                    NewSourceArchive(
                        source_id=pending.source_id,
                        archiver="wayback",
                        snapshot_url=snapshot_url(candidate),
                        snapshot_at=_candidate_at(candidate),
                        verified_sha256=fetched_hash,
                    ),
                )
            except IntegrityError:
                # UNIQUE-Race: ein paralleler Lauf war schneller — das Ergebnis
                # ist dasselbe → trotzdem attested (Rollback der eigenen Zeile).
                await session.rollback()
            return AttestOutcome(
                "attested", pending.source_id, snapshot_url=snapshot_url(candidate)
            )

        # SHA-1 passte, SHA-256 nicht: merken, weiter mit dem nächsten Kandidaten.
        had_sha256_mismatch = True
        logger.warning(
            "bytes_mismatch bei der Attestierung: source %s (SHA-1 passt, SHA-256 nicht) — "
            "Snapshot %s",
            pending.source_id,
            candidate.timestamp,
        )

    # 6. Kein Kandidat attestiert: mindestens ein SHA-256-Fehlschlag →
    #    bytes_mismatch; sonst war kein Snapshot exakt abrufbar.
    if had_sha256_mismatch:
        return AttestOutcome("bytes_mismatch", pending.source_id)
    return AttestOutcome("snapshot_unavailable", pending.source_id)
