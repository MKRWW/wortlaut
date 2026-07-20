"""Unit: DIP fetch PDF-Validierung + Redirect-Guard (#25).

Rein: httpx wird via unittest.mock gemockt, KEIN Live-Call.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wortlaut.ingest.adapter import RawSource, SourceRef
from wortlaut.ingest.dip import DipFetchError, DipPlenarprotokollAdapter
from wortlaut.ingest.settings import DipSettings


def _settings() -> DipSettings:
    return DipSettings(
        api_key="test-key",
        api_base_url="https://search.dip.bundestag.de/api/v1",
        pdf_host="dserver.bundestag.de",
    )


def _ref() -> SourceRef:
    return SourceRef(
        origin_url="https://dserver.bundestag.de/btp/21/21800/21800.pdf",
        source_type="plenarprotokoll",
        hint={},
    )


# ── AC1: Redirect wird abgelehnt ───────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_rejects_redirect() -> None:
    mock_response = MagicMock()
    mock_response.is_redirect = True
    mock_response.status_code = 302
    mock_response.headers = {"location": "https://example.com/x"}
    mock_response.content = b""

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        MockClient.return_value = client_instance
        client_instance.get.return_value = mock_response

        adapter = DipPlenarprotokollAdapter(_settings())

        with pytest.raises(DipFetchError):
            await adapter.fetch(_ref())


# ── AC2: Nicht-200 Status wird abgelehnt ───────────────────────────────


@pytest.mark.asyncio
async def test_fetch_rejects_non_200() -> None:
    mock_response = MagicMock()
    mock_response.is_redirect = False
    mock_response.status_code = 500
    mock_response.headers = {}
    mock_response.content = b"%PDF-1.7"

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        MockClient.return_value = client_instance
        client_instance.get.return_value = mock_response

        adapter = DipPlenarprotokollAdapter(_settings())

        with pytest.raises(DipFetchError):
            await adapter.fetch(_ref())


# ── AC3: Nicht-PDF-Bytes werden abgelehnt ──────────────────────────────


@pytest.mark.asyncio
async def test_fetch_rejects_non_pdf_bytes() -> None:
    mock_response = MagicMock()
    mock_response.is_redirect = False
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "text/html"}
    mock_response.content = b"<html>captcha</html>"

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        MockClient.return_value = client_instance
        client_instance.get.return_value = mock_response

        adapter = DipPlenarprotokollAdapter(_settings())

        with pytest.raises(DipFetchError):
            await adapter.fetch(_ref())


# ── AC4: Falscher Content-Type wird abgelehnt ─────────────────────────


@pytest.mark.asyncio
async def test_fetch_rejects_wrong_content_type() -> None:
    mock_response = MagicMock()
    mock_response.is_redirect = False
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/json"}
    mock_response.content = b"%PDF-1.7 body"

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        MockClient.return_value = client_instance
        client_instance.get.return_value = mock_response

        adapter = DipPlenarprotokollAdapter(_settings())

        with pytest.raises(DipFetchError):
            await adapter.fetch(_ref())


# ── AC5: Gueltiges PDF wird angenommen ─────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_accepts_valid_pdf() -> None:
    content = b"%PDF-1.7\nminimal"
    mock_response = MagicMock()
    mock_response.is_redirect = False
    mock_response.status_code = 200
    mock_response.headers = {"content-type": "application/pdf"}
    mock_response.content = content

    with patch("httpx.AsyncClient") as MockClient:
        client_instance = AsyncMock()
        MockClient.return_value = client_instance
        client_instance.get.return_value = mock_response

        adapter = DipPlenarprotokollAdapter(_settings())

        raw = await adapter.fetch(_ref())

    assert isinstance(raw, RawSource)
    assert raw.raw_bytes == content
    assert raw.mime_type == "application/pdf"


# ── AC6: Client: follow_redirects=False + Timeout ─────────────────────


def test_client_no_follow_redirects_and_timeout() -> None:
    with patch("httpx.AsyncClient") as MockClient:
        adapter = DipPlenarprotokollAdapter(_settings())
        adapter._client_or_create()

        MockClient.assert_called_once()
        _, kwargs = MockClient.call_args
        assert kwargs["follow_redirects"] is False
        assert "timeout" in kwargs
