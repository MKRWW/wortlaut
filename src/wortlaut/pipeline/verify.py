"""/verify-Fundament (#8): Rohbytes aus WORM neu hashen, gegen source.content_hash prüfen.

Read-only. Die reine Hash-Rechnung bleibt in ``evidence`` (#3, ``content_hash``);
hier nur I/O-Orchestrierung (source laden, WORM lesen). Öffentlich nachrechenbar:
dieselbe deterministische SHA-256 wie beim Ingest → jeder kann ``expected`` gegen
``actual`` prüfen (Threat T2, Security §3.6). Kein LLM, keine Ausgabe-Glättung.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from wortlaut.evidence.hashing import content_hash
from wortlaut.store.sources import get_source_by_id
from wortlaut.store.worm import WormStore

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


async def verify_source(source_id: UUID, *, session: AsyncSession, worm: WormStore) -> VerifyReport:
    """Rechnet die Integrität einer ``source`` nach: WORM-Rohbytes neu hashen.

    Statusmatrix (nie ein falsches ``ok``): source fehlt → ``source_not_found``;
    WORM-Objekt fehlt/``get`` wirft → ``worm_missing``; Hash ≠ → ``hash_mismatch``;
    alles passt → ``ok``.
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
    )
