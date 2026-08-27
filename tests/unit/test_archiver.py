"""Unit: Fremdarchiv-Client — archive.today + archive_all.

Rein: httpx wird via unittest.mock gemockt; kein Live-Call. Spec 0073:
Status-Gate (Snapshot nur aus Erfolgsantwort), strukturierter `failures`-Report
(ArchiveError), Retry via `with_retry` (eigene Tests in test_archive_retry.py).

Die Wayback-Tests des Browser-Pfads entfallen mit #108 — der Wayback-Pfad ist
SPN2 (POST /save + Status-Polling) und hat seine eigene Testdatei
(test_archiver_spn2.py). archive.today und archive_all bleiben unverändert.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import httpx
import pytest

from wortlaut.archive.archiver import (
    ARCHIVE_TODAY_HOST,
    WAYBACK_HOST,
    ArchiveResult,
    ArchiveTodayArchiver,
    WaybackArchiver,
    archive_all,
)
from wortlaut.archive.errors import ArchiveError
from wortlaut.archive.ssrf import SsrfBlocked


def _mock_response(
    status_code: int, headers: dict[str, str] | None = None, *, is_redirect: bool = False
) -> MagicMock:
    """Hilfsfunktion: baut ein mock-Response-Objekt."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.is_redirect = is_redirect
    resp.headers = headers if headers is not None else {}
    resp.content = b""
    return resp


def _spn2_success_payloads() -> list[dict[str, object]]:
    """SPN2-Fluss (Wayback seit #108): POST → job_id, Status → pending,
    Status → success. Der Mock-Client antwortet auf POST mit der Job-ID."""
    return [
        {"status": "pending"},
        {"status": "success", "timestamp": "20260101120000", "original_url": "https://example.com/"},
    ]


def _wayback_spn2_client() -> AsyncMock:
    """Wayback-Mock für archive_all-Tests: POST liefert die Job-ID,
    die Status-Abrufe liefern den SPN2-Erfolgsfluss (kein echter Transport)."""
    mock_client = AsyncMock()
    mock_client.post.return_value = _mock_response(200)
    mock_client.post.return_value.json.return_value = {
        "url": "https://example.com/",
        "job_id": "spn2-abc",
    }
    mock_client.get.side_effect = [
        httpx.Response(
            200,
            json=payload,
            request=httpx.Request("GET", "https://web.archive.org/save/status/spn2-abc"),
        )
        for payload in _spn2_success_payloads()
    ]
    return mock_client


# ── archive.today Snapshot-URL ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_archive_today_snapshot_url() -> None:
    """archive.today gibt Redirect mit Snapshot-URL → archive_today_url gesetzt."""
    archiver = ArchiveTodayArchiver()

    mock_resp = _mock_response(
        302,
        headers={"location": "https://archive.ph/abcd1234"},
        is_redirect=True,
    )

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    archiver._client = mock_client

    result = await archiver.archive("https://example.com/")

    assert result == "https://archive.ph/abcd1234"


@pytest.mark.asyncio
async def test_archive_today_snapshot_url_from_refresh() -> None:
    """archive.today 200 mit Refresh-Header → Snapshot-URL extrahiert."""
    archiver = ArchiveTodayArchiver()

    mock_resp = _mock_response(
        200,
        headers={"refresh": "0; url=https://archive.ph/efgh5678"},
    )

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    archiver._client = mock_client

    result = await archiver.archive("https://example.com/")

    assert result == "https://archive.ph/efgh5678"


# ── SSRF blockiert — kein HTTP-Call ─────────────────────────────────────


@pytest.mark.asyncio
async def test_archive_all_ssrf_blocked_no_call() -> None:
    """SSRF-Blockierung → kein HTTP-Call abgesetzt (Mock-Client: 0 Aufrufe)."""
    wayback = WaybackArchiver()
    mock_client_wb = AsyncMock()
    wayback._client = mock_client_wb

    atoday = ArchiveTodayArchiver()
    mock_client_at = AsyncMock()
    atoday._client = mock_client_at

    with pytest.raises(SsrfBlocked):
        await archive_all("http://127.0.0.1/x", wayback=wayback, archive_today=atoday)

    # Keine HTTP-Call abgesetzt
    assert mock_client_wb.post.call_count == 0
    assert mock_client_at.post.call_count == 0


@pytest.mark.asyncio
async def test_archive_all_ssrf_blocked_reraised_not_wrapped() -> None:
    """SsrfBlocked, das TROTZDEM aus einem Archiver fliegt, wird unverändert
    durchgereicht — nie in ArchiveError gewrappt (Security-Stopp)."""
    wayback = WaybackArchiver()
    mock_client_wb = AsyncMock()
    wayback._client = mock_client_wb

    atoday = ArchiveTodayArchiver()
    mock_client_at = AsyncMock()
    atoday._client = mock_client_at

    with patch(
        "wortlaut.archive.archiver.assert_url_allowed",
        side_effect=SsrfBlocked("blocked"),
    ):
        with pytest.raises(SsrfBlocked):
            await archive_all("https://example.com/", wayback=wayback, archive_today=atoday)

    assert mock_client_wb.post.call_count == 0
    assert mock_client_at.post.call_count == 0


# ── Partielles / totaler Fehlschlag (failures-Report, Spec 0073) ────────


@pytest.mark.asyncio
async def test_partial_failure_tolerated() -> None:
    """Wayback Transport-Fehler, archive.today OK → nur wayback_url None,
    ArchiveError('wayback', 'transport') in .failures."""
    wayback = WaybackArchiver(attempts=1)
    mock_client_wb = AsyncMock()
    mock_client_wb.post.side_effect = httpx.RemoteProtocolError("connection refused")
    wayback._client = mock_client_wb

    atoday = ArchiveTodayArchiver()
    mock_client_at = AsyncMock()
    mock_client_at.post.return_value = _mock_response(
        302,
        headers={"location": "https://archive.ph/success"},
        is_redirect=True,
    )
    atoday._client = mock_client_at

    with patch("wortlaut.archive.archiver.assert_url_allowed"):
        result = await archive_all("https://example.com/", wayback=wayback, archive_today=atoday)

    assert result.wayback_url is None
    assert result.archive_today_url == "https://archive.ph/success"
    assert "wayback" in result.failures
    assert result.failures["wayback"].reason == "transport"
    assert "archive_today" not in result.failures


@pytest.mark.asyncio
async def test_total_failure_reported() -> None:
    """Beide Dienste werfen → beide URLs None, beide Keys in .failures."""
    wayback = WaybackArchiver(attempts=1)
    mock_client_wb = AsyncMock()
    mock_client_wb.post.side_effect = httpx.RemoteProtocolError("connection refused")
    wayback._client = mock_client_wb

    atoday = ArchiveTodayArchiver(attempts=1)
    mock_client_at = AsyncMock()
    mock_client_at.post.side_effect = httpx.RemoteProtocolError("connection refused")
    atoday._client = mock_client_at

    with patch("wortlaut.archive.archiver.assert_url_allowed"):
        result = await archive_all("https://example.com/", wayback=wayback, archive_today=atoday)

    assert result.wayback_url is None
    assert result.archive_today_url is None
    assert "wayback" in result.failures
    assert "archive_today" in result.failures


# ── AC12: beide Dienste fehlschlagen → strukturierte failures ───────────


@pytest.mark.asyncio
async def test_archive_all_failures_structured() -> None:
    """AC12: archive_all, beide Dienste schlagen fehl → beide URLs None und
    `failures` enthält für BEIDE Dienste ein ArchiveError mit gesetztem reason."""
    wayback = WaybackArchiver(attempts=1)
    mock_client_wb = AsyncMock()
    mock_client_wb.post.return_value = _mock_response(404)
    wayback._client = mock_client_wb

    atoday = ArchiveTodayArchiver(attempts=1)
    mock_client_at = AsyncMock()
    mock_client_at.post.return_value = _mock_response(429)
    atoday._client = mock_client_at

    with patch("wortlaut.archive.archiver.assert_url_allowed"):
        result = await archive_all("https://example.com/", wayback=wayback, archive_today=atoday)

    assert result.wayback_url is None
    assert result.archive_today_url is None
    assert set(result.failures) == {"wayback", "archive_today"}
    wayback_failure = result.failures["wayback"]
    atoday_failure = result.failures["archive_today"]
    assert isinstance(wayback_failure, ArchiveError)
    assert isinstance(atoday_failure, ArchiveError)
    assert wayback_failure.reason == "http_status"
    assert wayback_failure.status_code == 404
    assert wayback_failure.transient is False
    assert atoday_failure.reason == "http_status"
    assert atoday_failure.status_code == 429
    assert atoday_failure.transient is True


# ── Snapshot-Redirect auf fremden Host / falsches Scheme ────────────────


@pytest.mark.asyncio
async def test_snapshot_redirect_offhost_rejected_archive_today() -> None:
    """archive.today antwortet mit Snapshot-URL auf fremdem Host →
    ArchiveError('invalid_snapshot_url')."""
    archiver = ArchiveTodayArchiver()

    mock_resp = _mock_response(
        302,
        headers={"location": "https://evil.com/phishing"},
        is_redirect=True,
    )

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    archiver._client = mock_client

    with pytest.raises(ArchiveError, match="invalid_snapshot_url"):
        await archiver.archive("https://example.com/")


# ── archive.today Retry (jetzt via with_retry + ArchiveError) ───────────


@pytest.mark.asyncio
async def test_archive_today_retry_then_success() -> None:
    """archive.today erst Transport-Fehler, dann Erfolg → 2 Aufrufe,
    Snapshot gesetzt."""
    archiver = ArchiveTodayArchiver()

    mock_client = AsyncMock()
    mock_client.post.side_effect = [
        httpx.RemoteProtocolError("connection refused"),
        _mock_response(
            302,
            headers={"location": "https://archive.ph/retry_ok"},
            is_redirect=True,
        ),
    ]
    archiver._client = mock_client

    result = await archiver.archive("https://example.com/")

    assert result == "https://archive.ph/retry_ok"
    assert mock_client.post.call_count == 2


# ── archive_all Happy Path ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_archive_all_both_success() -> None:
    """Beide Dienste liefern Snapshot-URL → ArchiveResult mit beiden URLs,
    leeres failures."""
    wayback = WaybackArchiver()
    wayback._client = _wayback_spn2_client()

    atoday = ArchiveTodayArchiver()
    mock_client_at = AsyncMock()
    mock_client_at.post.return_value = _mock_response(
        302,
        headers={"location": "https://archive.ph/ok"},
        is_redirect=True,
    )
    atoday._client = mock_client_at

    with patch("wortlaut.archive.archiver.assert_url_allowed"):
        result = await archive_all("https://example.com/", wayback=wayback, archive_today=atoday)

    assert isinstance(result, ArchiveResult)
    assert result.wayback_url == "https://web.archive.org/web/20260101120000/https://example.com/"
    assert result.archive_today_url == "https://archive.ph/ok"
    assert result.failures == {}


# ── Unglückliche Pfade (Fehlerbehandlung, kein falscher Link) ───────────


@pytest.mark.asyncio
async def test_archive_today_5xx_retry_then_success() -> None:
    """archive.today erst 5xx, dann Erfolg → genau 2 Aufrufe, Snapshot gesetzt."""
    archiver = ArchiveTodayArchiver()
    mock_client = AsyncMock()
    mock_client.post.side_effect = [
        _mock_response(503),
        _mock_response(302, headers={"location": "https://archive.ph/after5xx"}, is_redirect=True),
    ]
    archiver._client = mock_client

    result = await archiver.archive("https://example.com/")

    assert result == "https://archive.ph/after5xx"
    assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_archive_today_5xx_twice_raises() -> None:
    """archive.today zweimal 5xx → ArchiveError(http_status, 503) nach genau
    einem Retry."""
    archiver = ArchiveTodayArchiver()
    mock_client = AsyncMock()
    mock_client.post.side_effect = [_mock_response(503), _mock_response(503)]
    archiver._client = mock_client

    with pytest.raises(ArchiveError) as exc_info:
        await archiver.archive("https://example.com/")
    assert exc_info.value.service == "archive_today"
    assert exc_info.value.reason == "http_status"
    assert exc_info.value.status_code == 503
    assert exc_info.value.transient is True
    assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_archive_today_timeout_twice_raises() -> None:
    """archive.today zweimal Timeout → ArchiveError(timeout, transient=True)
    nach genau einem Retry."""
    archiver = ArchiveTodayArchiver()
    mock_client = AsyncMock()
    mock_client.post.side_effect = [
        httpx.ReadTimeout("timeout 1", request=httpx.Request("POST", "https://archive.ph/")),
        httpx.ReadTimeout("timeout 2", request=httpx.Request("POST", "https://archive.ph/")),
    ]
    archiver._client = mock_client

    with pytest.raises(ArchiveError) as exc_info:
        await archiver.archive("https://example.com/")
    assert exc_info.value.service == "archive_today"
    assert exc_info.value.reason == "timeout"
    assert exc_info.value.transient is True
    assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_archive_today_unexpected_status_raises() -> None:
    """archive.today 404 → ArchiveError(http_status, 404, permanent), kein Retry."""
    archiver = ArchiveTodayArchiver()
    mock_client = AsyncMock()
    mock_client.post.return_value = _mock_response(404)
    archiver._client = mock_client

    with pytest.raises(ArchiveError) as exc_info:
        await archiver.archive("https://example.com/")
    assert exc_info.value.service == "archive_today"
    assert exc_info.value.reason == "http_status"
    assert exc_info.value.status_code == 404
    assert exc_info.value.transient is False
    assert mock_client.post.call_count == 1


@pytest.mark.asyncio
async def test_archive_today_200_without_snapshot_raises() -> None:
    """archive.today 200 ohne Location/Refresh → ArchiveError(no_snapshot_url)
    (kein falscher Link)."""
    archiver = ArchiveTodayArchiver()
    mock_client = AsyncMock()
    mock_client.post.return_value = _mock_response(200)
    archiver._client = mock_client

    with pytest.raises(ArchiveError, match="no_snapshot_url"):
        await archiver.archive("https://example.com/")


@pytest.mark.asyncio
async def test_http_snapshot_rejected() -> None:
    """Snapshot-URL mit http statt https → ArchiveError(invalid_snapshot_url)
    (Downgrade wird verworfen)."""
    archiver = ArchiveTodayArchiver()
    mock_client = AsyncMock()
    mock_client.post.return_value = _mock_response(
        302,
        headers={"location": "http://archive.ph/downgrade"},
        is_redirect=True,
    )
    archiver._client = mock_client

    with pytest.raises(ArchiveError, match="invalid_snapshot_url"):
        await archiver.archive("https://example.com/")


@pytest.mark.asyncio
async def test_client_lifecycle_create_and_aclose() -> None:
    """_client_or_create erzeugt lazy genau einen Client; aclose schließt und
    leert ihn."""
    wayback = WaybackArchiver()
    atoday = ArchiveTodayArchiver()

    with patch("wortlaut.archive.archiver.pinned_client", return_value=AsyncMock()) as factory:
        wb_client = wayback._client_or_create()
        at_client = atoday._client_or_create()

        assert wayback._client_or_create() is wb_client
        assert atoday._client_or_create() is at_client
        assert factory.call_count == 2
        assert factory.call_args_list == [
            call(WAYBACK_HOST),
            call(ARCHIVE_TODAY_HOST),
        ]

    await wayback.aclose()
    await atoday.aclose()

    assert wayback._client is None
    assert atoday._client is None
