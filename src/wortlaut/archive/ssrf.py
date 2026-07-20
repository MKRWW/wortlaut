"""SSRF-Prüfung — Egress-Allowlist + interne-IP-Blocklist (R-SEC-05).

Rein (bis auf DNS-Auflösung); kein wortlaut-Import.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class SsrfBlocked(Exception):
    """URL wurde durch SSRF-Schutz blockiert (interne IP, verbotenes Schema, Allowlist)."""


def resolve_and_check(url: str, *, allow_hosts: frozenset[str] | None = None) -> list[str]:
    """Prüft die URL (Schema/Host/Allowlist), löst DNS auf und validiert JEDE IP.

    Liefert die validierten IP-Strings (Reihenfolge wie aufgelöst) für IP-Pinning
    (#36). Wirft SsrfBlocked wie bisher; fail closed, wenn keine IP prüfbar war.
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
    seen: set[str] = set()
    valid_ips: list[str] = []
    for _, _, _, _, sock_addr in addrinfos:
        ip_str = str(sock_addr[0])
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        checked += 1
        # is_global deckt ab: private, loopback, link-local, reserved, multicast,
        # unspecified UND CGNAT 100.64.0.0/10 — ein einzelner Treffer reicht zum Block.
        if not addr.is_global:
            raise SsrfBlocked(f"hostname '{hostname}' resolves to non-global IP {ip_str}")
        if ip_str not in seen:
            seen.add(ip_str)
            valid_ips.append(ip_str)

    if checked == 0:
        raise SsrfBlocked(f"no checkable IP address for '{hostname}' — fail closed")

    return valid_ips


def assert_url_allowed(url: str, *, allow_hosts: frozenset[str] | None = None) -> None:
    """Wie resolve_and_check, verwirft aber das Ergebnis (reiner Guard)."""
    resolve_and_check(url, allow_hosts=allow_hosts)
