"""SSRF-Prüfung — Egress-Allowlist + interne-IP-Blocklist (R-SEC-05).

Rein (bis auf DNS-Auflösung); kein wortlaut-Import.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class SsrfBlocked(Exception):
    """URL wurde durch SSRF-Schutz blockiert (interne IP, verbotenes Schema, Allowlist)."""


def assert_url_allowed(url: str, *, allow_hosts: frozenset[str] | None = None) -> None:
    """Wirft SsrfBlocked, wenn url auf private/loopback/link-local/ULA/Metadata-IP
    (169.254.169.254) auflöst, ein Nicht-http(s)-Schema hat, oder (falls allow_hosts
    gesetzt) der Host nicht in der Allowlist ist. DNS wird aufgelöst und geprüft.
    """
    parsed = urlparse(url)

    # 1) Schema prüfen — nur http/https
    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise SsrfBlocked(f"schema '{scheme}' not allowed; only http/https permitted")

    # 2) Hostname vorhanden
    hostname = parsed.hostname
    if not hostname:
        raise SsrfBlocked(f"url has no hostname: {url!r}")

    # 3) Allowlist (falls gesetzt)
    if allow_hosts is not None:
        if hostname.lower() not in {h.lower() for h in allow_hosts}:
            raise SsrfBlocked(f"host '{hostname}' not in allowlist")

    # 4) DNS auflösen
    try:
        addrinfos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise SsrfBlocked(f"dns resolution failed for '{hostname}': {exc}") from exc

    # 5) Jede aufgelöste Adresse prüfen — fail closed: mindestens eine IP muss geprüft sein
    checked = 0
    for _, _, _, _, sock_addr in addrinfos:
        ip_str = sock_addr[0]
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        checked += 1
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            raise SsrfBlocked(f"hostname '{hostname}' resolves to blocked IP {ip_str}")
    if checked == 0:
        raise SsrfBlocked(f"no checkable IP address for '{hostname}' — fail closed")
