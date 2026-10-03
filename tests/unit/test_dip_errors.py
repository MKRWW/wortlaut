"""Unit (Spec 0095, AC8): DIP-Adapter übersetzt Netz-/Status-/JSON-Fehler
nach ``DipFetchError``; der SSRF-Host-Check wirft ``DipHostNotAllowed``.

Rein: ``httpx.MockTransport`` hinter dem injizierten Client — kein Netz,
keinerlei Antworttext in den Meldungen (R-SEC-07).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import httpx
import pytest

from wortlaut.ingest.adapter import SourceRef
from wortlaut.ingest.dip import DipFetchError, DipHostNotAllowed, DipPlenarprotokollAdapter
from wortlaut.ingest.settings import DipSettings

_Handler = Callable[[httpx.Request], httpx.Response]

_BODY_500 = b"ANTWORTTEXT-DARF-NIE-LECKEN-500"
_BODY_INVALID_JSON = b"ANTWORTTEXT-DARF-NIE-LECKEN-JSON"


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


def _since() -> datetime:
    return datetime(2024, 1, 1, tzinfo=UTC)


def _adapter(handler: _Handler) -> DipPlenarprotokollAdapter:
    adapter = DipPlenarprotokollAdapter(_settings())
    adapter._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )
    return adapter


def _network_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused")


def _status_500_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, content=_BODY_500)


def _invalid_json_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=_BODY_INVALID_JSON)


# ── AC8: fetch ──────────────────────────────────────────────────────────


async def test_fetch_transport_error() -> None:
    """AC8: Netzfehler bei ``fetch`` → ``DipFetchError``; weder der Antwort-
    noch der Rohfehler-Text stehen in der Meldung."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    ref = _ref()
    adapter = _adapter(handler)

    with pytest.raises(DipFetchError, match="network error") as excinfo:
        await adapter.fetch(ref)

    assert "connection refused" not in str(excinfo.value)


# ── AC8: discover ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("handler", "expected_match", "leak_body"),
    [
        pytest.param(_network_handler, "DIP network error", b"connection refused", id="network"),
        pytest.param(_status_500_handler, "DIP status 500", _BODY_500, id="status-500"),
        pytest.param(
            _invalid_json_handler,
            "DIP returned invalid JSON",
            _BODY_INVALID_JSON,
            id="invalid-json",
        ),
    ],
)
async def test_discover_errors_translated(
    handler: _Handler, expected_match: str, leak_body: bytes
) -> None:
    """AC8: Netzfehler, HTTP 500 und kaputtes JSON bei ``discover`` werden
    als ``DipFetchError`` weitergereicht; der Antworttext steht nie in der
    Meldung (R-SEC-07)."""
    since = _since()
    adapter = _adapter(handler)

    with pytest.raises(DipFetchError, match=expected_match) as excinfo:
        await adapter.discover(since)

    assert leak_body.decode() not in str(excinfo.value)


# ── AC8: SSRF-Host-Check ────────────────────────────────────────────────


async def test_host_not_allowed_type() -> None:
    """AC8: nicht erlaubter Host → ``DipHostNotAllowed`` (SSRF-Check)."""
    ref = SourceRef(
        origin_url="https://evil.example.com/x.pdf",
        source_type="plenarprotokoll",
        hint={},
    )
    adapter = DipPlenarprotokollAdapter(_settings())

    with pytest.raises(DipHostNotAllowed, match="not in the allowed set"):
        await adapter.fetch(ref)
