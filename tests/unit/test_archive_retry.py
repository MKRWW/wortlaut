"""Unit: In-Run-Retry + Status-Gate für WaybackArchiver (Spec 0073, AC1–AC3;
portiert auf den SPN2-Fluss in #108 §14.1).

Rein: httpx-Client gemockt, `sleep` in `with_retry` injiziert UND der
Polling-Sleep in den Archiver-Konstruktor — zwei getrennte Recorder, damit
keine reale Wartezeit entsteht und die Backoff-Zusicherung bleibt wertbeständig
(R-TEST-03). Der Capture-Request ist jetzt ein POST, der Status-Abruf ein
GET; gezählt werden die POSTs (Capture-Versuche), nicht die Status-Abrufe.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from wortlaut.archive.archiver import WaybackArchiver, WaybackTuning
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


def _client_with_responders(post_responders: list[Any]) -> AsyncMock:
    """Mock-Client: `post_responders` = Rückgabetypen/Exceptions je
    Capture-Request (POST /save); die Status-Abrufe (GET) liefern einen
    festen SPN2-Erfolg — ein Poll genügt (job_id aus dem POST)."""
    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.post.side_effect = list(post_responders)
    mock_client.get.return_value = httpx.Response(
        200,
        json={
            "status": "success",
            "timestamp": "20260101120000",
            "original_url": "https://example.com/",
        },
        request=httpx.Request("GET", "https://web.archive.org/save/status/spn2-abc"),
    )
    return mock_client


def _attempt_with_sleep(
    wayback: WaybackArchiver,
    origin_url: str,
    sleep: Callable[[float], Awaitable[None]],
) -> Awaitable[str]:
    """`wayback._attempt` in `with_retry` — injizierte Retry-Backoff-Sleep.

    Der Polling-Sleep ist ein anderer: er läuft über den Konstruktor
    (eigener Rekorder). Wer beide auf dieselbe Liste legt, macht die
    Backoff-Zusicherung wertlos (§14.1).
    """

    async def _operation() -> str:
        return await wayback._attempt(origin_url)

    return with_retry(
        _operation,
        attempts=wayback._tuning.attempts,
        base_delay_seconds=wayback._tuning.base_delay_seconds,
        sleep=sleep,
    )


def _wb_request(url: str = "https://web.archive.org/save/") -> httpx.Request:
    return httpx.Request("GET", url)


@pytest.mark.asyncio
async def test_wayback_429_then_success() -> None:
    """AC1: 429 auf den Capture-Request, danach 200 mit job_id und
    Status-success → Snapshot-URL; der Retry-Backoff (`sleep`) wurde
    genau 1× mit `base_delay_seconds` aufgerufen, kein ArchiveError
    verlässt die Methode."""
    sleep_calls: list[float] = []

    async def _sleep(delay: float) -> None:
        sleep_calls.append(delay)

    poll_calls: list[float] = []

    async def _poll_sleep(delay: float) -> None:
        poll_calls.append(delay)

    client = _client_with_responders(
        [
            httpx.Response(429, request=_wb_request()),
            httpx.Response(
                200,
                json={"url": "https://example.com/", "job_id": "spn2-abc"},
                request=_wb_request(),
            ),
        ]
    )
    wayback = WaybackArchiver(sleep=_poll_sleep)
    wayback._client = client

    result = await _attempt_with_sleep(wayback, "https://example.com/", _sleep)

    assert result == "https://web.archive.org/web/20260101120000/https://example.com/"
    assert sleep_calls == [2.0]  # genau 1× base_delay_seconds (Retry-Backoff)
    assert poll_calls == [3.0]  # genau ein Status-Poll, eigener Rekorder
    assert client.post.call_count == 2


@pytest.mark.asyncio
async def test_wayback_timeout_backoff_sequence() -> None:
    """AC2: 2× Timeout auf den Capture-Request, dann 200 mit job_id und
    Status-success → Snapshot-URL; Retry-Backoff-Sleep-Aufrufe
    = [base, base*2] (exponentieller Backoff)."""
    sleep_calls: list[float] = []

    async def _sleep(delay: float) -> None:
        sleep_calls.append(delay)

    poll_calls: list[float] = []

    async def _poll_sleep(delay: float) -> None:
        poll_calls.append(delay)

    client = _client_with_responders(
        [
            httpx.ReadTimeout("timeout 1", request=_wb_request("https://x/")),
            httpx.ReadTimeout("timeout 2", request=_wb_request("https://x/")),
            httpx.Response(
                200,
                json={"url": "https://example.com/", "job_id": "spn2-abc"},
                request=_wb_request(),
            ),
        ]
    )
    wayback = WaybackArchiver(sleep=_poll_sleep)
    wayback._client = client

    result = await _attempt_with_sleep(wayback, "https://example.com/", _sleep)

    assert result == "https://web.archive.org/web/20260101120000/https://example.com/"
    assert sleep_calls == [2.0, 4.0]  # base, base*2 — exponentiell
    assert poll_calls == [3.0]  # Erfolg im ersten Poll, eigener Rekorder
    assert client.post.call_count == 3


@pytest.mark.asyncio
async def test_archive_retries_through_public_api_and_throttles_each_attempt() -> None:
    """AC1/§4.2 an der ECHTEN Naht: `archive()` selbst (nicht `_attempt` +
    manuell zusammengesetztes `with_retry`) wiederholt den transienten 429 —
    und der RateLimiter wird VOR JEDEM Versuch gezogen, Retries
    eingeschlossen.

    Ohne diesen Test würde eine falsch verdrahtete `archive()` (Retry
    vergessen, Limiter außerhalb der wiederholten Operation) von AC1–AC3
    nicht bemerkt, weil die dort den Retry im Test selbst bauen.

    Der Polling-Sleep wird dem Konstruktor injiziert (§14.1): der Test ruft
    `archive()` echt auf und würde sonst real warten (R-TEST-03).
    """
    limiter = _CountingLimiter()
    poll_calls: list[float] = []

    async def _poll_sleep(delay: float) -> None:
        poll_calls.append(delay)

    client = _client_with_responders(
        [
            httpx.Response(429, request=_wb_request()),
            httpx.Response(
                200,
                json={"url": "https://example.com/", "job_id": "spn2-abc"},
                request=_wb_request(),
            ),
        ]
    )
    # base_delay_seconds=0.0 → echter asyncio.sleep, aber ohne reale Wartezeit.
    wayback = WaybackArchiver(
        limiter=limiter,
        tuning=WaybackTuning(base_delay_seconds=0.0),
        sleep=_poll_sleep,
    )
    wayback._client = client

    result = await wayback.archive("https://example.com/")

    assert result == "https://web.archive.org/web/20260101120000/https://example.com/"
    assert client.post.call_count == 2
    assert poll_calls == [3.0]  # kein realer Poll-Sleep (R-TEST-03)
    assert limiter.acquires == 2, "Drosselung muss INNERHALB der wiederholten Operation liegen"


@pytest.mark.asyncio
async def test_wayback_404_no_retry() -> None:
    """AC3: 404 auf den Capture-Request → ArchiveError(service='wayback',
    reason='http_status', status_code=404, transient=False) und genau 1
    HTTP-Call (permanent ⇒ kein Retry)."""
    sleep_calls: list[float] = []

    async def _sleep(delay: float) -> None:
        sleep_calls.append(delay)

    poll_calls: list[float] = []

    async def _poll_sleep(delay: float) -> None:
        poll_calls.append(delay)

    client = _client_with_responders([httpx.Response(404, request=_wb_request())])
    wayback = WaybackArchiver(sleep=_poll_sleep)
    wayback._client = client

    with pytest.raises(ArchiveError) as exc_info:
        await _attempt_with_sleep(wayback, "https://example.com/", _sleep)

    assert exc_info.value.service == "wayback"
    assert exc_info.value.reason == "http_status"
    assert exc_info.value.status_code == 404
    assert exc_info.value.transient is False
    assert client.post.call_count == 1
    assert client.get.call_count == 0  # kein Status-Abruf hinter permanentem Fehler
    assert sleep_calls == []
    assert poll_calls == []
