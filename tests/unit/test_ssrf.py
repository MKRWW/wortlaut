"""Unit: SSRF-Schutz — interne IPs blockieren, öffentliche durchlassen (AC3/AC4).

Rein: socket.getaddrinfo wird gemockt; kein einziger echter DNS-Lookup.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from wortlaut.archive.ssrf import SsrfBlocked, assert_url_allowed, resolve_and_check

# ── AC3: Interne IPs / verbotene Schemata blockieren ────────────────────


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1/x",
        "http://10.0.0.1/x",
        "http://169.254.169.254/latest/meta-data",
        "http://localhost/x",
        "http://100.64.0.1/x",
    ],
)
def test_internal_ip_blocked(bad_url: str) -> None:
    """Literale interne IPs/localhost → SsrfBlocked (kein Netz)."""
    with pytest.raises(SsrfBlocked):
        assert_url_allowed(bad_url)


@pytest.mark.parametrize(
    "bad_url",
    [
        "file:///etc/passwd",
        "ftp://example.org/x",
    ],
)
def test_non_http_schema_blocked(bad_url: str) -> None:
    """Nicht-http(s)-Schemata → SsrfBlocked (Schema-Check vor DNS)."""
    with pytest.raises(SsrfBlocked):
        assert_url_allowed(bad_url)


def test_dns_based_ssrf_blocked() -> None:
    """Hostname, dessen gemocktes getaddrinfo 10.0.0.1 liefert → SsrfBlocked (DNS-SSRF)."""
    mock_addrinfos = [(2, 1, 6, "", ("10.0.0.1", 80))]
    with (
        patch("wortlaut.archive.ssrf.socket.getaddrinfo", return_value=mock_addrinfos),
        pytest.raises(SsrfBlocked),
    ):
        assert_url_allowed("http://evil.internal.example.com/x")


def test_dns_resolution_failure_blocked() -> None:
    """DNS-Auflösungsfehler (gaierror) → SsrfBlocked (fail closed)."""
    import socket

    with (
        patch(
            "wortlaut.archive.ssrf.socket.getaddrinfo",
            side_effect=socket.gaierror("Name or service not known"),
        ),
        pytest.raises(SsrfBlocked),
    ):
        assert_url_allowed("http://nonexistent.invalid/x")


# ── AC4: Öffentliche URL durchlassen ────────────────────────────────────


def test_public_url_allowed() -> None:
    """Öffentliche URL (gemockte Auflösung auf 93.184.216.34) → kein Fehler."""
    mock_addrinfos = [(2, 1, 6, "", ("93.184.216.34", 80))]
    with patch("wortlaut.archive.ssrf.socket.getaddrinfo", return_value=mock_addrinfos):
        assert_url_allowed("http://example.org/page")  # keine Exception


# ── Allowlist ───────────────────────────────────────────────────────────


def test_allowlist_host_not_in_list_blocked() -> None:
    """Host nicht in allow_hosts → SsrfBlocked (noch vor DNS)."""
    allow_hosts = frozenset({"allowed.example.com"})
    with pytest.raises(SsrfBlocked):
        assert_url_allowed("http://not-allowed.example.com/x", allow_hosts=allow_hosts)


def test_url_without_hostname_blocked() -> None:
    """URL ohne Hostname → SsrfBlocked."""
    with pytest.raises(SsrfBlocked):
        assert_url_allowed("http:///pfad-ohne-host")


def test_empty_resolution_fails_closed() -> None:
    """getaddrinfo liefert keine prüfbare Adresse → SsrfBlocked (fail closed)."""
    with (
        patch("wortlaut.archive.ssrf.socket.getaddrinfo", return_value=[]),
        pytest.raises(SsrfBlocked),
    ):
        assert_url_allowed("http://weird.example.com/x")


def test_resolve_and_check_returns_validated_ips() -> None:
    """Zwei Einträge (IPv4 + IPv6) => beide IPs in Rückgabe."""
    mock_addrinfos = [
        (2, 1, 6, "", ("93.184.216.34", 80)),
        (30, 1, 6, "", ("2606:2800:220:1:248:1893:25c8:1946", 80)),
    ]
    with patch("wortlaut.archive.ssrf.socket.getaddrinfo", return_value=mock_addrinfos):
        ips = resolve_and_check("http://example.org/page")
    assert "93.184.216.34" in ips
    assert "2606:2800:220:1:248:1893:25c8:1946" in ips


def test_resolve_and_check_blocks_mixed_resolution() -> None:
    """Eine gültige IP + eine interne IP => SsrfBlocked."""
    mock_addrinfos = [
        (2, 1, 6, "", ("93.184.216.34", 80)),
        (2, 1, 6, "", ("10.0.0.1", 80)),
    ]
    with (
        patch("wortlaut.archive.ssrf.socket.getaddrinfo", return_value=mock_addrinfos),
        pytest.raises(SsrfBlocked),
    ):
        resolve_and_check("http://evil.internal.example.com/x")
