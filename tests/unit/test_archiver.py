"""Unit: Fremdarchiv-Client — Wayback, archive.today, archive_all (AC1-AC7).

Rein: httpx wird via unittest.mock gemockt; kein Live-Call.
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
from wortlaut.archive.ssrf import SsrfBlocked


def _mock_response(
    status_code: int, headers: dict[str, str] | None = None, *, is_redirect: bool = False
) -> MagicMock:
    """Hilfsfunktion: baut ein mock-Response-Objekt."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.is_redirect = is_redirect
    resp.headers = headers or {}
    resp.content = b""
    return resp


# ── AC1: Wayback Snapshot-URL ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_wayback_snapshot_url() -> None:
    """Wayback gibt Content-Location mit Snapshot-URL → wayback_url gesetzt."""
    wayback = WaybackArchiver()

    mock_resp = _mock_response(
        200,
        headers={"content-location": "/20260101120000/https://example.com/"},
    )

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp
    wayback._client = mock_client

    result = await wayback.archive("https://example.com/")

    assert result == "https://web.archive.org/20260101120000/https://example.com/"


@pytest.mark.asyncio
async def test_wayback_snapshot_url_from_redirect() -> None:
    """Wayback Redirect (3xx) mit Location-Header → Snapshot-URL."""
    wayback = WaybackArchiver()

    mock_resp = _mock_response(
        302,
        headers={"location": "https://web.archive.org/web/20260101/https://example.com/"},
        is_redirect=True,
    )

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp
    wayback._client = mock_client

    result = await wayback.archive("https://example.com/")

    assert result == "https://web.archive.org/web/20260101/https://example.com/"


# ── AC2: archive.today Snapshot-URL ─────────────────────────────────────


@pytest.mark.asyncio
async def test_archive_today_snapshot_url() -> None:
    """archive.today gibt Redirect mit Snapshot-URL → archive_today_url gesetzt."""
    archiver = ArchiveTodayArchiver(retry_delay=0.0)

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
    archiver = ArchiveTodayArchiver(retry_delay=0.0)

    mock_resp = _mock_response(
        200,
        headers={"refresh": "0; url=https://archive.ph/efgh5678"},
    )

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    archiver._client = mock_client

    result = await archiver.archive("https://example.com/")

    assert result == "https://archive.ph/efgh5678"


# ── AC3: SSRF blockiert — kein HTTP-Call ────────────────────────────────


@pytest.mark.asyncio
async def test_archive_all_ssrf_blocked_no_call() -> None:
    """SSRF-Blockierung → kein HTTP-Call abgesetzt (Mock-Client: 0 Aufrufe)."""
    wayback = WaybackArchiver()
    mock_client_wb = AsyncMock()
    wayback._client = mock_client_wb

    atoday = ArchiveTodayArchiver(retry_delay=0.0)
    mock_client_at = AsyncMock()
    atoday._client = mock_client_at

    with pytest.raises(SsrfBlocked):
        await archive_all("http://127.0.0.1/x", wayback=wayback, archive_today=atoday)

    # Keine HTTP-Call abgesetzt
    assert mock_client_wb.get.call_count == 0
    assert mock_client_at.post.call_count == 0


# ── AC5: Partielles Fehlschlagstoleranz ─────────────────────────────────


@pytest.mark.asyncio
async def test_partial_failure_tolerated() -> None:
    """Wayback 500/Exception, archive.today OK → nur wayback_url None, Fehler im .errors."""
    wayback = WaybackArchiver()
    mock_client_wb = AsyncMock()
    mock_client_wb.get.side_effect = httpx.RemoteProtocolError("connection refused")
    wayback._client = mock_client_wb

    atoday = ArchiveTodayArchiver(retry_delay=0.0)
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
    assert "wayback" in result.errors


# ── AC6: Totaler Fehlschlag ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_total_failure_reported() -> None:
    """Beide Dienste werfen → beide URLs None, beide Keys in .errors."""
    wayback = WaybackArchiver()
    mock_client_wb = AsyncMock()
    mock_client_wb.get.side_effect = httpx.RemoteProtocolError("connection refused")
    wayback._client = mock_client_wb

    atoday = ArchiveTodayArchiver(retry_delay=0.0)
    mock_client_at = AsyncMock()
    mock_client_at.post.side_effect = httpx.RemoteProtocolError("connection refused")
    atoday._client = mock_client_at

    with patch("wortlaut.archive.archiver.assert_url_allowed"):
        result = await archive_all("https://example.com/", wayback=wayback, archive_today=atoday)

    assert result.wayback_url is None
    assert result.archive_today_url is None
    assert "wayback" in result.errors
    assert "archive_today" in result.errors


# ── AC7: Snapshot-Redirect auf fremden Host ─────────────────────────────


@pytest.mark.asyncio
async def test_snapshot_redirect_offhost_rejected_wayback() -> None:
    """Wayback antwortet mit Snapshot-URL auf fremdem Host → ValueError."""
    wayback = WaybackArchiver()

    mock_resp = _mock_response(
        302,
        headers={"location": "https://evil.com/phishing"},
        is_redirect=True,
    )

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_resp
    wayback._client = mock_client

    with pytest.raises(ValueError, match="evil.com"):
        await wayback.archive("https://example.com/")


@pytest.mark.asyncio
async def test_snapshot_redirect_offhost_rejected_archive_today() -> None:
    """archive.today antwortet mit Snapshot-URL auf fremdem Host → ValueError."""
    archiver = ArchiveTodayArchiver(retry_delay=0.0)

    mock_resp = _mock_response(
        302,
        headers={"location": "https://evil.com/phishing"},
        is_redirect=True,
    )

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    archiver._client = mock_client

    with pytest.raises(ValueError, match="evil.com"):
        await archiver.archive("https://example.com/")


# ── Retry-Verhalten ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_archive_today_retry_then_success() -> None:
    """archive.today erst Timeout, dann Erfolg → 2 Aufrufe, Snapshot gesetzt."""
    archiver = ArchiveTodayArchiver(retry_delay=0.0)

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
    """Beide Dienste liefern Snapshot-URL → ArchiveResult mit beiden URLs."""
    wayback = WaybackArchiver()
    mock_client_wb = AsyncMock()
    mock_client_wb.get.return_value = _mock_response(
        200,
        headers={"content-location": "/20260101/https://example.com/"},
    )
    wayback._client = mock_client_wb

    atoday = ArchiveTodayArchiver(retry_delay=0.0)
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
    assert result.wayback_url == "https://web.archive.org/20260101/https://example.com/"
    assert result.archive_today_url == "https://archive.ph/ok"
    assert result.errors == {}


# ── Unglückliche Pfade (Fehlerbehandlung, kein falscher Link) ───────────


@pytest.mark.asyncio
async def test_archive_today_5xx_retry_then_success() -> None:
    """archive.today erst 5xx, dann Erfolg → genau 2 Aufrufe, Snapshot gesetzt."""
    archiver = ArchiveTodayArchiver(retry_delay=0.0)
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
    """archive.today zweimal 5xx → ValueError nach genau einem Retry."""
    archiver = ArchiveTodayArchiver(retry_delay=0.0)
    mock_client = AsyncMock()
    mock_client.post.side_effect = [_mock_response(503), _mock_response(503)]
    archiver._client = mock_client

    with pytest.raises(ValueError, match="503"):
        await archiver.archive("https://example.com/")
    assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_archive_today_timeout_twice_raises() -> None:
    """archive.today zweimal Timeout → ValueError nach genau einem Retry."""
    archiver = ArchiveTodayArchiver(retry_delay=0.0)
    mock_client = AsyncMock()
    mock_client.post.side_effect = [
        httpx.TimeoutException("timeout 1"),
        httpx.TimeoutException("timeout 2"),
    ]
    archiver._client = mock_client

    with pytest.raises(ValueError, match="failed after retry"):
        await archiver.archive("https://example.com/")
    assert mock_client.post.call_count == 2


@pytest.mark.asyncio
async def test_archive_today_unexpected_status_raises() -> None:
    """archive.today 404 → ValueError, kein Retry."""
    archiver = ArchiveTodayArchiver(retry_delay=0.0)
    mock_client = AsyncMock()
    mock_client.post.return_value = _mock_response(404)
    archiver._client = mock_client

    with pytest.raises(ValueError, match="unexpected status 404"):
        await archiver.archive("https://example.com/")
    assert mock_client.post.call_count == 1


@pytest.mark.asyncio
async def test_archive_today_200_without_snapshot_raises() -> None:
    """archive.today 200 ohne Location/Refresh → ValueError (kein falscher Link)."""
    archiver = ArchiveTodayArchiver(retry_delay=0.0)
    mock_client = AsyncMock()
    mock_client.post.return_value = _mock_response(200)
    archiver._client = mock_client

    with pytest.raises(ValueError, match="no snapshot url"):
        await archiver.archive("https://example.com/")


@pytest.mark.asyncio
async def test_wayback_absolute_content_location() -> None:
    """Wayback mit absoluter Content-Location → unverändert übernommen."""
    wayback = WaybackArchiver()
    mock_client = AsyncMock()
    mock_client.get.return_value = _mock_response(
        200,
        headers={"content-location": "https://web.archive.org/web/1/https://example.com/"},
    )
    wayback._client = mock_client

    result = await wayback.archive("https://example.com/")

    assert result == "https://web.archive.org/web/1/https://example.com/"


@pytest.mark.asyncio
async def test_wayback_relative_redirect_location() -> None:
    """Wayback Redirect mit relativer Location → mit Wayback-Basis absolutiert."""
    wayback = WaybackArchiver()
    mock_client = AsyncMock()
    mock_client.get.return_value = _mock_response(
        302,
        headers={"location": "/web/20260101/https://example.com/"},
        is_redirect=True,
    )
    wayback._client = mock_client

    result = await wayback.archive("https://example.com/")

    assert result == "https://web.archive.org/web/20260101/https://example.com/"


@pytest.mark.asyncio
async def test_wayback_without_snapshot_raises() -> None:
    """Wayback 200 ohne Content-Location/Redirect → ValueError."""
    wayback = WaybackArchiver()
    mock_client = AsyncMock()
    mock_client.get.return_value = _mock_response(200)
    wayback._client = mock_client

    with pytest.raises(ValueError, match="no snapshot url"):
        await wayback.archive("https://example.com/")


@pytest.mark.asyncio
async def test_http_snapshot_rejected() -> None:
    """Snapshot-URL mit http statt https → ValueError (Downgrade wird verworfen)."""
    archiver = ArchiveTodayArchiver(retry_delay=0.0)
    mock_client = AsyncMock()
    mock_client.post.return_value = _mock_response(
        302,
        headers={"location": "http://archive.ph/downgrade"},
        is_redirect=True,
    )
    archiver._client = mock_client

    with pytest.raises(ValueError, match="not https"):
        await archiver.archive("https://example.com/")


@pytest.mark.asyncio
async def test_client_lifecycle_create_and_aclose() -> None:
    """_client_or_create erzeugt lazy genau einen Client; aclose schließt und leert ihn."""
    wayback = WaybackArchiver()
    atoday = ArchiveTodayArchiver(retry_delay=0.0)

    with patch("wortlaut.archive.archiver.pinned_client", return_value=AsyncMock()) as factory:
        wb_client = wayback._client_or_create()
        at_client = atoday._client_or_create()

        assert wayback._client_or_create() is wb_client
        assert atoday._client_or_create() is at_client
        assert factory.call_count == 2
        assert factory.call_args_list == [call(WAYBACK_HOST), call(ARCHIVE_TODAY_HOST)]

    await wayback.aclose()
    await atoday.aclose()

    assert wayback._client is None
    assert atoday._client is None
