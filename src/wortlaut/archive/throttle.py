"""Drosselung und Lauf-Stillegung für Fremdarchiv-Calls (Spec 0073 §11).

`RateLimiter` erzwingt einen Mindestabstand zwischen zwei Calls (kein Burst);
`DisableAfterFailures` legt einen optionalen Dienst nach `limit` Fehlern im
Lauf still. Beide sind rein und injizieren Uhr/Sleep, damit Unit-Tests nie
real warten (R-TEST-03).

`Archiver` wird nur als Typ-Referenz genutzt (``TYPE_CHECKING``) — so bleibt
dieses Modul frei von der Runtime-Abhängigkeit auf ``archiver`` (kein
zirkulärer Import); die strukturelle Protokol-Konformität genügt.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from wortlaut.archive.errors import ArchiveError

if TYPE_CHECKING:
    from wortlaut.archive.archiver import Archiver


class RateLimiter:
    """Erzwingt einen Mindestabstand zwischen zwei Calls (kein Burst)."""

    def __init__(
        self,
        min_interval_seconds: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._min_interval = min_interval_seconds
        self._monotonic = monotonic
        self._sleep = sleep
        self._last_call: float | None = None

    async def acquire(self) -> None:
        """Wartet, bis der Mindestabstand zum letzten Call eingehalten ist.

        Der erste Call wartet nie. Danach: `wartezeit = min_interval - (jetzt -
        letzter_call)`; ist sie > 0, wird gewartet. Danach `letzter_call`
        auf `monotonic()` setzen.
        """
        now = self._monotonic()
        if self._last_call is not None:
            wait = self._min_interval - (now - self._last_call)
            if wait > 0:
                await self._sleep(wait)
                now = self._monotonic()
        self._last_call = now


class DisableAfterFailures:
    """Archiver-Dekorator: legt einen OPTIONALEN Dienst nach `limit` Fehlern im Lauf still.

    Danach wirft `archive` sofort `ArchiveError(reason='disabled', transient=False)`,
    ohne HTTP-Call. Verhindert, dass ein dauerhaft 429-blockierter Dienst jeden
    Ingest um attempts×Backoff verlängert. Erfüllt das Archiver-Protokoll.
    """

    def __init__(self, inner: Archiver, *, service: str, limit: int) -> None:
        self._inner = inner
        self._service = service
        self._limit = limit
        self._consecutive_failures = 0

    async def archive(self, origin_url: str) -> str:
        if self._consecutive_failures >= self._limit:
            raise ArchiveError(self._service, "disabled")
        try:
            result = await self._inner.archive(origin_url)
        except ArchiveError:
            self._consecutive_failures += 1
            raise
        self._consecutive_failures = 0
        return result

    async def aclose(self) -> None:
        """Delegiert an `inner.aclose`, falls vorhanden — sonst NO-OP."""
        aclose = getattr(self._inner, "aclose", None)
        if aclose is not None:
            await aclose()
