"""Stempel-Pass: RFC-3161-Zeitstempel für bereits archivierte Quellen (Spec 0076).

Nachlauf-Pass (entscheidung b, §0b): ``fetch→hash→dedup→archiv→WORM→insert`` (R-CORE-02)
wird hier **nicht** angefasst — dieser Pfad liest nur WORM, rechnet den Hash gegen
den Ledger nach, stempelt (falls passend), legt das Token in WORM ab und schreibt
eine append-only DB-Zeile. Ein TSA-Ausfall verzögert den Ingest um nichts (§0b).

Hash-Gegenprüfung VOR dem Stempeln (AC10, §4.2): wir beglaubigen nie Bytes, deren
Bindung an den Ledger wir nicht gerade selbst nachgerechnet haben.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from wortlaut.evidence.hashing import content_hash
from wortlaut.store.timestamps import (
    NewSourceTimestamp,
    PendingSource,
    insert_source_timestamp,
)
from wortlaut.store.worm import WormStore
from wortlaut.timestamp.errors import TimestampError
from wortlaut.timestamp.tsa import TimeStamper

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TimestampOutcome:
    """Ergebnis des Stempel-Passes für eine Quelle (Spec 0076 §3.4)."""

    status: Literal["stamped", "hash_mismatch", "worm_missing", "tsa_failed"]
    source_id: UUID
    tsa_name: str | None = None
    failures: tuple[str, ...] = ()  # label()s der gescheiterten/übersprungenen TSAs


async def timestamp_source(
    pending: PendingSource,
    *,
    session: AsyncSession,
    worm: WormStore,
    stamper: TimeStamper,
) -> TimestampOutcome:
    """WORM lesen → Hash gegenprüfen → stempeln → Token in WORM → Zeile schreiben."""
    # 1. WORM lesen. Jeder Fehler → worm_missing (Muster pipeline/verify.py).
    try:
        raw = await worm.get(pending.raw_bytes_ref)
    except Exception:
        logger.warning("WORM-Read fehlgeschlagen beim Stempeln für source %s", pending.source_id)
        return TimestampOutcome("worm_missing", pending.source_id)

    # 2. Hash gegen Ledger nachrechnen (AC10). Nie stempeln, was nicht gerade
    #    selbst gegen source.content_hash gerechnet wurde — und der Stamper
    #    wird in diesem Fall NICHT aufgerufen.
    if content_hash(raw) != pending.content_hash:
        logger.error(
            "hash_mismatch beim Stempeln: source %s (WORM-Bytes passen nicht zum Ledger-Hash)",
            pending.source_id,
        )
        return TimestampOutcome("hash_mismatch", pending.source_id)

    # 3. Stempeln (alle TSAs tot) → tsa_failed.
    try:
        result = await stamper.stamp(raw, content_hash=pending.content_hash)
    except TimestampError as exc:
        failures: tuple[str, ...] = getattr(stamper, "failures", ()) or (exc.label(),)
        logger.warning("tsa_failed für source %s: %s", pending.source_id, ",".join(failures))
        return TimestampOutcome("tsa_failed", pending.source_id, failures=failures)

    # 4. Token in WORM (content-adressiert, je TSA kollisionsfrei, §4.11).
    token_ref = await worm.put(
        f"{pending.content_hash}.{result.tsa_name}.tsr",
        result.token_der,
        content_type="application/timestamp-reply",
    )

    # 5. Zeile schreiben (append-only). UNIQUE-Race: parallelster Lauf war
    #    schneller — das Ergebnis ist dasselbe → trotzdem stamped (AC12, §4.10).
    try:
        await insert_source_timestamp(
            session,
            NewSourceTimestamp(
                source_id=pending.source_id,
                tsa_name=result.tsa_name,
                token_ref=token_ref,
            ),
        )
    except IntegrityError:
        await session.rollback()

    # 6. Erfolg. failures = Labels der übersprungenen (vorher gescheiterten) TSAs.
    return TimestampOutcome(
        "stamped",
        pending.source_id,
        result.tsa_name,
        failures=getattr(stamper, "failures", ()),
    )
