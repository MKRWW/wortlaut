"""In-Run-Retry mit exponentiellem Backoff für Archiv-Operationen (Spec 0073 §11).

Nur `ArchiveError` wird gefangen: transient=False fliegt sofort durch, der
letzte Fehler wird weitergereicht. `sleep` ist injizierbar, damit Unit-Tests
nie real warten (R-TEST-03).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from wortlaut.archive.errors import ArchiveError


async def with_retry(
    operation: Callable[[], Awaitable[str]],
    *,
    attempts: int = 3,
    base_delay_seconds: float = 2.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Wiederholt `operation` NUR bei `ArchiveError` mit `transient=True`.

    Exponentieller Backoff: `base_delay_seconds * 2 ** (versuch_index)` —
    erster Backoff = `base_delay_seconds`. Permanente Fehler werfen sofort.
    Andere Exceptions werden nie gefangen.
    """
    for attempt in range(attempts):
        try:
            return await operation()
        except ArchiveError as exc:
            if not exc.transient or attempt == attempts - 1:
                raise
            await sleep(base_delay_seconds * (2**attempt))
    raise AssertionError("unreachable")  # pragma: no cover
