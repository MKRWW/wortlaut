"""Unit: In-Run-Retry + Status-Gate für WaybackArchiver (Spec 0073, AC1–AC3).

Rein: httpx-Client gemockt, `sleep` in `with_retry` injiziert — kein Live-Netz,
keine reale Wartezeit (R-TEST-03).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from wortlaut.archive.archiver import WaybackArchiver
from wortlaut.archive.errors import ArchiveError
from wortlaut.archive.retry import with_retry
from wortlaut.archive.throttle import RateLimiter


class _CountingLimiter(RateLimiter):
    """RateLimiter, der seine acquire-Aufrufe zählt (Drosselung im Retry-Pfad, §4.2)."""

    def __init__(self) -> None:
        super().__init__(0.0)
        self.acquires = 0

    async def acquire(self) -> None:
        self.acquires += 1
        await super().acquire()


def _client_with_responders(responders: list[Any]) -> AsyncMock:
    """Mock-Client: `responders` = Rückgabetypen/Exceptions je GET-Call."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.side_effect = list(responders)
    return mock_client


def _attempt_with_sleep(
    wayback: WaybackArchiver,
    origin_url: str,
    sleep: Callable[[float], Awaitable[None]],
) -> Awaitable[str]:
    """`wayback._attempt` in `with_retry` — mit injizierter Sleep-Funktion."""

    async def _operation() -> str:
        return await wayback._attempt(origin_url)

    return with_retry(
        _operation,
        attempts=wayback._attempts,
        base_delay_seconds=wayback._base_delay_seconds,
        sleep=sleep,
    )


def _wb_request(url: str = "https://web.archive.org/save/") -> httpx.Request:
    return httpx.Request("GET", url)


@pytest.mark.asyncio
async def test_wayback_429_then_success() -> None:
    """AC1: 429, danach 200 mit content-location → Snapshot-URL, `sleep` wurde
    genau 1× mit `base_delay_seconds` aufgerufen, kein ArchiveError verlässt
    die Methode."""
    sleep_calls: list[float] = []

    async def _sleep(delay: float) -> None:
        sleep_calls.append(delay)

    client = _client_with_responders(
        [
            httpx.Response(429, request=_wb_request()),
            httpx.Response(
                200,
                headers={"content-location": "/20260101120000/https://example.com/"},
                request=_wb_request(),
            ),
        ]
    )
    wayback = WaybackArchiver()
    wayback._client = client

    result = await _attempt_with_sleep(wayback, "https://example.com/", _sleep)

    assert result == "https://web.archive.org/20260101120000/https://example.com/"
    assert sleep_calls == [2.0]  # genau 1× base_delay_seconds
    assert client.get.call_count == 2


@pytest.mark.asyncio
async def test_wayback_timeout_backoff_sequence() -> None:
    """AC2: 2× Timeout, dann 302 mit Location → Snapshot-URL; sleep-Aufrufe
    = [base, base*2] (exponentieller Backoff)."""
    sleep_calls: list[float] = []

    async def _sleep(delay: float) -> None:
        sleep_calls.append(delay)

    client = _client_with_responders(
        [
            httpx.ReadTimeout("timeout 1", request=_wb_request("https://x/")),
            httpx.ReadTimeout("timeout 2", request=_wb_request("https://x/")),
            httpx.Response(
                302,
                headers={"location": "https://web.archive.org/web/20260101/https://example.com/"},
                request=_wb_request(),
            ),
        ]
    )
    wayback = WaybackArchiver()
    wayback._client = client

    result = await _attempt_with_sleep(wayback, "https://example.com/", _sleep)

    assert result == "https://web.archive.org/web/20260101/https://example.com/"
    assert sleep_calls == [2.0, 4.0]  # base, base*2 — exponentiell
    assert client.get.call_count == 3


@pytest.mark.asyncio
async def test_archive_retries_through_public_api_and_throttles_each_attempt() -> None:
    """AC1/§4.2 an der ECHTEN Naht: `archive()` selbst (nicht `_attempt` + manuell
    zusammengesetztes `with_retry`) wiederholt den transienten 429 — und der
    RateLimiter wird VOR JEDEM Versuch gezogen, Retries eingeschlossen.

    Ohne diesen Test würde eine falsch verdrahtete `archive()` (Retry vergessen,
    Limiter außerhalb der wiederholten Operation) von AC1-AC3 nicht bemerkt,
    weil die dort den Retry im Test selbst bauen.
    """
    limiter = _CountingLimiter()
    client = _client_with_responders(
        [
            httpx.Response(429, request=_wb_request()),
            httpx.Response(
                200,
                headers={"content-location": "/20260101120000/https://example.com/"},
                request=_wb_request(),
            ),
        ]
    )
    # base_delay_seconds=0.0 → echter asyncio.sleep, aber ohne reale Wartezeit.
    wayback = WaybackArchiver(limiter=limiter, attempts=3, base_delay_seconds=0.0)
    wayback._client = client

    result = await wayback.archive("https://example.com/")

    assert result == "https://web.archive.org/20260101120000/https://example.com/"
    assert client.get.call_count == 2
    assert limiter.acquires == 2, "Drosselung muss INNERHALB der wiederholten Operation liegen"


@pytest.mark.asyncio
async def test_wayback_404_no_retry() -> None:
    """AC3: 404 → ArchiveError(service='wayback', reason='http_status',
    status_code=404, transient=False) und genau 1 HTTP-Call (permanent ⇒ kein
    Retry)."""
    sleep_calls: list[float] = []

    async def _sleep(delay: float) -> None:
        sleep_calls.append(delay)

    client = _client_with_responders([httpx.Response(404, request=_wb_request())])
    wayback = WaybackArchiver()
    wayback._client = client

    with pytest.raises(ArchiveError) as exc_info:
        await _attempt_with_sleep(wayback, "https://example.com/", _sleep)

    assert exc_info.value.service == "wayback"
    assert exc_info.value.reason == "http_status"
    assert exc_info.value.status_code == 404
    assert exc_info.value.transient is False
    assert client.get.call_count == 1
    assert sleep_calls == []
