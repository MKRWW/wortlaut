"""IP-gepinnter httpx-Transport — schließt das DNS-Rebinding-Fenster (#36, R-SEC-05).

Der Connect geht auf die vorab validierte IP; Host-Header und TLS-SNI
(request.extensions['sni_hostname']) bleiben auf dem Original-Host, damit die
Zertifikatsprüfung gegen den Hostnamen läuft. Importiert nur ssrf + httpx + stdlib.
"""

from __future__ import annotations

import httpx

from wortlaut.archive.ssrf import SsrfBlocked, resolve_and_check


class PinnedHostTransport(httpx.AsyncBaseTransport):
    """Transport, der GENAU EINEN Host bedient und dessen Connect auf eine geprüfte IP pinnt."""

    def __init__(
        self, host: str, pinned_ip: str, *, inner: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.host = host.lower()
        self.pinned_ip = pinned_ip
        self._inner = inner if inner is not None else httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        # Fail closed: fremder Host auf diesem Transport ist ein Programmierfehler/Angriff
        if (request.url.host or "").lower() != self.host:
            raise SsrfBlocked(
                f"transport ist auf '{self.host}' gepinnt, Request ging an '{request.url.host}'"
            )
        request.extensions["sni_hostname"] = self.host
        request.headers["Host"] = self.host
        request.url = request.url.copy_with(host=self.pinned_ip)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


def pinned_client(host: str, *, timeout_seconds: float = 30.0) -> httpx.AsyncClient:
    """AsyncClient, dessen Connects auf eine jetzt validierte IPv4 von host gepinnt sind.

    follow_redirects=False (Redirect-Guard bleibt Sache der Aufrufer, #25/#33).
    """
    ips = resolve_and_check(f"https://{host}/")
    ipv4 = next((ip for ip in ips if ":" not in ip), None)
    if ipv4 is None:
        raise SsrfBlocked(f"keine validierte IPv4 für '{host}' — IPv6-Pinning nicht unterstützt")
    return httpx.AsyncClient(
        transport=PinnedHostTransport(host, ipv4),
        follow_redirects=False,
        timeout=httpx.Timeout(timeout_seconds),
    )
