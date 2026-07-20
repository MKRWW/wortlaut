"""Unit: IP-gepinnter Transport — DNS-Rebinding-Fenster schliessen (#36, R-SEC-05).

Rein: getaddrinfo wird gemockt; kein Live-Call.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from wortlaut.archive.pinned import PinnedHostTransport, pinned_client
from wortlaut.archive.ssrf import SsrfBlocked, resolve_and_check


class RecordingTransport(httpx.AsyncBaseTransport):
    """Zeichnet den finalen Request auf, liefert 200."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, request=request)


@pytest.mark.asyncio
async def test_connect_goes_to_pinned_ip() -> None:
    """Request-URL-Host wird auf pinned_ip gesetzt, Host-Header und SNI bleiben original."""
    recorder = RecordingTransport()
    transport = PinnedHostTransport(
        host="web.archive.org",
        pinned_ip="93.184.216.34",
        inner=recorder,
    )
    client = httpx.AsyncClient(transport=transport)

    resp = await client.get("https://web.archive.org/save/x")
    await client.aclose()

    assert resp.status_code == 200
    assert len(recorder.requests) == 1
    req = recorder.requests[0]
    assert req.url.host == "93.184.216.34"
    assert req.headers["Host"] == "web.archive.org"
    assert req.extensions["sni_hostname"] == "web.archive.org"


@pytest.mark.asyncio
async def test_rebinding_after_check_is_ineffective() -> None:
    """DNS-Rebinding nach dem Check: Transport bleibt auf gepinnter IP, kein zweiter Lookup."""
    # Phase 1: resolve_and_check liefert gültige IP
    good_addrinfos = [(2, 1, 6, "", ("93.184.216.34", 80))]
    with patch("wortlaut.archive.ssrf.socket.getaddrinfo", return_value=good_addrinfos):
        ips = resolve_and_check("https://web.archive.org/")

    pinned_ip = ips[0]  # "93.184.216.34"
    recorder = RecordingTransport()
    transport = PinnedHostTransport(
        host="web.archive.org",
        pinned_ip=pinned_ip,
        inner=recorder,
    )

    # Phase 2: DNS tauscht jetzt auf interne IP (Simulationsangriff)
    bad_addrinfos = [(2, 1, 6, "", ("10.0.0.1", 80))]
    with patch("wortlaut.archive.ssrf.socket.getaddrinfo", return_value=bad_addrinfos):
        # Transport macht KEINEN neuen DNS-Lookup — er nutzt die gepinnte IP
        client = httpx.AsyncClient(transport=transport)
        resp = await client.get("https://web.archive.org/save/y")
        await client.aclose()

    assert resp.status_code == 200
    assert len(recorder.requests) == 1
    assert recorder.requests[0].url.host == pinned_ip


@pytest.mark.asyncio
async def test_foreign_host_rejected_fail_closed() -> None:
    """Fremder Host auf gepinntem Transport => SsrfBlocked, Recorder leer."""
    recorder = RecordingTransport()
    transport = PinnedHostTransport(
        host="web.archive.org",
        pinned_ip="93.184.216.34",
        inner=recorder,
    )
    client = httpx.AsyncClient(transport=transport)

    with pytest.raises(SsrfBlocked):
        await client.get("https://evil.example.com/")

    assert recorder.requests == []
    await client.aclose()


@pytest.mark.asyncio
async def test_pinned_client_prefers_ipv4_and_configures_transport() -> None:
    """pinned_client wählt IPv4 aus gemischter Rückgabe und pinnt sie."""
    with (
        patch(
            "wortlaut.archive.pinned.resolve_and_check",
            return_value=["2606:2800::1", "93.184.216.34"],
        ),
        # Echten AsyncHTTPTransport mocken — reale Transport-/SSL-Objekte zerlegen
        # auf der Windows-Konsole den pytest-Prozess (lokaler Fallstrick).
        patch("wortlaut.archive.pinned.httpx.AsyncHTTPTransport", return_value=AsyncMock()),
    ):
        client = pinned_client("web.archive.org")
        transport = client._transport
        assert isinstance(transport, PinnedHostTransport)
        assert transport.pinned_ip == "93.184.216.34"
        assert transport.host == "web.archive.org"
        await client.aclose()


@pytest.mark.asyncio
async def test_pinned_client_no_ipv4_blocks() -> None:
    """Nur IPv6-Auflösung => SsrfBlocked (IPv6-Pinning nicht unterstützt)."""
    with (
        patch(
            "wortlaut.archive.pinned.resolve_and_check",
            return_value=["2606:2800:220:1:248:1893:25c8:1946"],
        ),
        pytest.raises(SsrfBlocked),
    ):
        pinned_client("ipv6only.example.com")
