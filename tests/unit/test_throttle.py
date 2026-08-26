"""Unit: Drosselung + Stilllegung — RateLimiter, DisableAfterFailures (Spec 0073, AC9/AC11).

Rein: Uhr (`monotonic`) und `sleep` werden injiziert — deterministisch, ohne
reale Wartezeit (R-TEST-03).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from wortlaut.archive.errors import ArchiveError
from wortlaut.archive.throttle import DisableAfterFailures, RateLimiter


class _FakeClock:
    """Simulierte Monotone-Uhr.

    `monotonic` liefert einen vom Test kontrollierten Zeitpunkt; `sleep`
    REKORDIERT die Wartezeit, bewegt die Uhr aber NICHT (reiner Recorder).
    Damit modellieren wir „3 Calls unmittelbar hintereinander" — kein Zeit
    vergeht zwischen den Calls, jede Wartezeit ist der volle Mindestabstand.
    """

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.t

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _limiter(interval: float) -> tuple[RateLimiter, _FakeClock]:
    clock = _FakeClock()
    limiter = RateLimiter(interval, monotonic=clock.monotonic, sleep=clock.sleep)
    return limiter, clock


# ── AC9 ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_limiter_spaces_calls() -> None:
    """AC9: 3× acquire unmittelbar hintereinander → 2× geschlafen, jeweils
    ≈5 s (der volle Mindestabstand); der erste Call wartet nicht."""
    limiter, clock = _limiter(5.0)

    await limiter.acquire()  # Call 1: kein Warten (letzter_call noch None)
    await limiter.acquire()  # Call 2: 0 s vergangen → voller 5-s-Abstand
    await limiter.acquire()  # Call 3: 0 s vergangen → voller 5-s-Abstand

    assert len(clock.sleeps) == 2  # genau 2× geschlafen (der erste Call wartet nicht)
    assert clock.sleeps[0] == pytest.approx(5.0)
    assert clock.sleeps[1] == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_rate_limiter_partial_wait_when_time_elapsed() -> None:
    """Ergänzend: wenn zwischen zwei Calls bereits Zeit vergangen ist, wird
    nur der RESTE des Mindestabstands gewartet (Backoff + Abstand addieren,
    es entsteht kein Burst)."""
    limiter, clock = _limiter(5.0)

    await limiter.acquire()  # Call 1: kein Warten
    clock.advance(3.0)  # 3 s vergehen
    await limiter.acquire()  # Call 2: nur 2 s warten (5 − 3)

    assert clock.sleeps == [pytest.approx(2.0)]


# ── AC11 ────────────────────────────────────────────────────────────────


class _FailingArchiver:
    """Innerer Archiver, dessen `archive` `ArchiveError` wirft; Call-Count zählt."""

    def __init__(self) -> None:
        self.calls = 0

    async def archive(self, origin_url: str) -> str:
        self.calls += 1
        raise ArchiveError("archive_today", "http_status", status_code=429, transient=True)


@pytest.mark.asyncio
async def test_disable_after_failures_stops_calling_inner() -> None:
    """AC11: nach `limit`=3 Fehlern in Folge wirft `archive`
    ArchiveError(reason='disabled', transient=False) OHNE HTTP-Call am inneren
    Archiver (Call-Count bleibt konstant)."""
    inner = _FailingArchiver()
    disabled = DisableAfterFailures(inner, service="archive_today", limit=3)

    for _ in range(3):
        with pytest.raises(ArchiveError) as exc_info:
            await disabled.archive("https://example.com/")
        assert exc_info.value.reason == "http_status"
    assert inner.calls == 3

    # Vierter Aufruf: sofort 'disabled', innerer Archiver NICHT aufgerufen
    with pytest.raises(ArchiveError) as exc_info:
        await disabled.archive("https://example.com/")
    assert exc_info.value.service == "archive_today"
    assert exc_info.value.reason == "disabled"
    assert exc_info.value.transient is False
    assert inner.calls == 3  # Call-Count bleibt konstant


@pytest.mark.asyncio
async def test_disable_after_failures_resets_on_success() -> None:
    """Ergänzend: ein Erfolg setzt den Fehlerzähler zurück — ein einzelner
    Fehler stilllegt den Dienst NICHT."""
    calls = {"n": 0}

    class _FlakyArchiver:
        async def archive(self, origin_url: str) -> str:
            calls["n"] += 1
            if calls["n"] % 2 == 1:
                raise ArchiveError("archive_today", "http_status", status_code=429)
            return "https://archive.ph/ok"

    disabled = DisableAfterFailures(_FlakyArchiver(), service="archive_today", limit=3)

    with pytest.raises(ArchiveError):
        await disabled.archive("https://example.com/")  # Fail 1 (Zähler=1)
    assert (
        await disabled.archive("https://example.com/") == "https://archive.ph/ok"
    )  # Erfolg → Reset
    # Nach Reset ist der Zähler wieder 0: der nächste Fehler ist Fail 1, nicht 3
    with pytest.raises(ArchiveError) as exc_info:
        await disabled.archive("https://example.com/")
    assert exc_info.value.reason == "http_status"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_disable_after_failures_delegates_aclose() -> None:
    """`aclose` delegiert an den inneren Archiver (getattr-Prüfung)."""
    inner = MagicMock()
    inner.aclose = AsyncMock()
    disabled = DisableAfterFailures(inner, service="archive_today", limit=3)

    await disabled.aclose()
    inner.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_disable_after_failures_aclose_noop_without_inner_aclose() -> None:
    """Kein `aclose` am inneren Archiver → NO-OP, keine Exception."""
    inner = MagicMock(spec=[])
    disabled = DisableAfterFailures(inner, service="archive_today", limit=3)
    await disabled.aclose()  # darf nicht werfen
