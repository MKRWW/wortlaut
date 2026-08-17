"""/verify-Fundament (#8): Rohbytes aus WORM neu hashen, gegen source.content_hash prüfen.

Read-only. Die reine Hash-Rechnung bleibt in ``evidence`` (#3, ``content_hash``);
hier nur I/O-Orchestrierung (source laden, WORM lesen). Öffentlich nachrechenbar:
dieselbe deterministische SHA-256 wie beim Ingest → jeder kann ``expected`` gegen
``actual`` prüfen (Threat T2, Security §3.6). Kein LLM, keine Ausgabe-Glättung.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from wortlaut.evidence.hashing import content_hash
from wortlaut.store.models import Source
from wortlaut.store.sources import get_source_by_id
from wortlaut.store.timestamps import get_timestamps_for_source
from wortlaut.store.worm import WormStore
from wortlaut.timestamp.verify import verify_token

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class VerifyReport:
    """Ergebnis von :func:`verify_source` — macht expected vs. actual explizit."""

    ok: bool
    source_id: UUID
    status: Literal["ok", "hash_mismatch", "source_not_found", "worm_missing"]
    content_hash_expected: str | None
    content_hash_actual: str | None
    archive_wayback: str | None
    archive_today: str | None
    # NEU (Spec 0076, additiv ans Ende): RFC-3161-Zeitstempel. Ein fehlender oder
    # kaputter Stempel ändert ok/status NICHT (kein Gate, §2/§3.4).
    timestamp_status: Literal[
        "ok", "mismatch", "untrusted", "malformed", "missing", "unreadable"
    ] = "missing"
    timestamp_tsa: str | None = None
    timestamp_gen_time: datetime | None = None


async def verify_source(source_id: UUID, *, session: AsyncSession, worm: WormStore) -> VerifyReport:
    """Rechnet die Integrität einer ``source`` nach: WORM-Rohbytes neu hashen.

    Statusmatrix (nie ein falsches ``ok``): source fehlt → ``source_not_found``;
    WORM-Objekt fehlt/``get`` wirft → ``worm_missing``; Hash ≠ → ``hash_mismatch``;
    alles passt → ``ok``. Der RFC-3161-Zeitstempel wird additiv geprüft (Spec 0076)
    und ändert ``ok``/``status`` **nicht** — ein fehlender Stempel degradiert nichts.
    """
    source = await get_source_by_id(session, source_id)
    if source is None:
        return VerifyReport(False, source_id, "source_not_found", None, None, None, None)

    expected = source.content_hash
    try:
        raw = await worm.get(source.raw_bytes_ref)
    except Exception:  # jeder WORM-Read-Fehler → worm_missing, NIE ein falsches ok (T2)
        logger.warning("WORM-Read fehlgeschlagen für source %s", source_id)
        return VerifyReport(
            False,
            source_id,
            "worm_missing",
            expected,
            None,
            source.archive_wayback,
            source.archive_today,
        )

    actual = content_hash(raw)
    matches = actual == expected
    status: Literal["ok", "hash_mismatch"] = "ok" if matches else "hash_mismatch"
    return VerifyReport(
        matches,
        source_id,
        status,
        expected,
        actual,
        source.archive_wayback,
        source.archive_today,
        *await _timestamp_fields(source_id, session, worm, source),
    )


_TimestampStatus = Literal["ok", "mismatch", "untrusted", "malformed", "missing", "unreadable"]


async def _timestamp_fields(
    source_id: UUID, session: AsyncSession, worm: WormStore, source: Source
) -> tuple[_TimestampStatus, str | None, datetime | None]:
    """Prüft den (ersten) RFC-3161-Zeitstempel der Quelle (Spec 0076 §3.4).

    Liefert ``(timestamp_status, timestamp_tsa, timestamp_gen_time)``. Der
    Nachweis ist **rein additiv** und ändert ``ok``/``status`` **nicht** (kein
    Gate, §2/§3.4). ``gen_time`` wird NUR aus dem Token gelesen.

    ``missing`` und ``unreadable`` werden bewusst **getrennt** (Spec §11-Nachtrag):
    „nie gestempelt" und „Zeile da, Token unlesbar" sind verschiedene Befunde, und
    die Verwechslung wäre still und dauerhaft — ``list_sources_without_timestamp``
    geht nach Zeilen-Existenz, die Quelle ist also nicht mehr „pending" und würde
    **nie wieder** gestempelt, während ``/verify`` „nie gestempelt" meldete. Ein
    zerstörter Nachweis muss als zerstörter Nachweis sichtbar sein.
    """
    rows = await get_timestamps_for_source(session, source_id)
    if not rows:
        return ("missing", None, None)
    row = rows[0]  # die erste Zeile (nach created_at)
    try:
        token = await worm.get(row.token_ref)
    except Exception:
        logger.warning(
            "Zeitstempel-Token nicht lesbar (unreadable) für source %s, ref %s",
            source_id,
            row.token_ref,
        )
        return ("unreadable", row.tsa_name, None)
    verdict = verify_token(token, content_hash=source.content_hash, tsa_name=row.tsa_name)
    return (verdict.status, verdict.tsa_name, verdict.gen_time)
