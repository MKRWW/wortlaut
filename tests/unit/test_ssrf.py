"""Unit: SSRF-Schutz — interne IPs blockieren, öffentliche durchlassen (AC3/AC4).

Rein: socket.getaddrinfo wird gemockt; kein einziger echter DNS-Lookup.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from wortlaut.archive.ssrf import SsrfBlocked, assert_url_allowed

# ── AC3: Interne IPs / verbotene Schemata blockieren ────────────────────


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://127.0.0.1/x",
        "http://10.0.0.1/x",
        "http://169.254.169.254/latest/meta-data",
        "http://localhost/x",
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
    with pytest.raises(SsrfBlocked):
        assert_url_allowed(
            "http://not-allowed.example.com/x",
            allow_hosts=frozenset({"allowed.example.com"}),
        )
