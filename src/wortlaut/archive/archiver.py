"""Fremdarchiv-Client — Wayback Machine + archive.today mit Snapshot-Validierung.

Importiert nur wortlaut.archive.ssrf, httpx, stdlib (R-ARCH-02).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin, urlparse

import httpx

from wortlaut.archive.ssrf import assert_url_allowed

logger = logging.getLogger(__name__)

# ── Konstanten ──────────────────────────────────────────────────────────

WAYBACK_SAVE_URL = "https://web.archive.org/save/"
ARCHIVE_TODAY_SUBMIT_URL = "https://archive.ph/submit/"
WAYBACK_HOST = "web.archive.org"
ARCHIVE_TODAY_HOST = "archive.ph"


# ── Datenstrukturen ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class ArchiveResult:
    """Ergebnis von archive_all — Snapshot-URLs pro Dienst und Fehlerbericht."""

    wayback_url: str | None
    archive_today_url: str | None
    errors: dict[str, str]  # {'wayback': '...', 'archive_today': '...'} bei Fehlern


class Archiver(Protocol):
    """Prototyp-Schnittstelle für Fremdarchiv-Implementierungen."""

    async def archive(self, origin_url: str) -> str: ...


# ── Wayback ─────────────────────────────────────────────────────────────


class WaybackArchiver:
    """Wayback Machine 'Save Page Now' — archiviert origin_url und liefert Snapshot-URL."""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def _client_or_create(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                follow_redirects=False,
                timeout=httpx.Timeout(30.0),
            )
        return self._client

    async def archive(self, origin_url: str) -> str:
        """Löst `GET /save/<origin_url>` ab und extrahiert die Snapshot-URL."""
        save_url = f"{WAYBACK_SAVE_URL}{origin_url}"
        response = await self._client_or_create().get(save_url)

        snapshot_url: str | None = None

        # Content-Location Header (relativ → mit Basis prefixen)
        content_location = response.headers.get("content-location", "")
        if content_location:
            if content_location.startswith("http"):
                snapshot_url = content_location
            else:
                snapshot_url = urljoin(f"https://{WAYBACK_HOST}", content_location)

        # Fallback: Redirect → Location Header
        if not snapshot_url and response.is_redirect:
            location = response.headers.get("location", "")
            if location:
                if location.startswith("http"):
                    snapshot_url = location
                else:
                    snapshot_url = urljoin(f"https://{WAYBACK_HOST}", location)

        if not snapshot_url:
            raise ValueError(f"no snapshot url from wayback for {origin_url}")

        # Validierung: Schema + Host
        _validate_snapshot_url(snapshot_url, WAYBACK_HOST)

        return snapshot_url

    async def aclose(self) -> None:
        """Schließt den internen httpx-Client, falls einer erzeugt wurde."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ── archive.today ───────────────────────────────────────────────────────


class ArchiveTodayArchiver:
    """archive.today — POST /submit/ mit einmaligem Retry bei Timeout/5xx."""

    def __init__(self, *, retry_delay: float = 2.0) -> None:
        self._retry_delay = retry_delay
        self._client: httpx.AsyncClient | None = None

    def _client_or_create(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                follow_redirects=False,
                timeout=httpx.Timeout(30.0),
            )
        return self._client

    async def archive(self, origin_url: str) -> str:
        """POST /submit/ mit url=<origin_url> und einmaligem Backoff."""
        last_exc: Exception | None = None

        for attempt in range(2):
            try:
                response = await self._client_or_create().post(
                    ARCHIVE_TODAY_SUBMIT_URL,
                    data={"url": origin_url},
                )
            except (httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
                last_exc = exc
                if attempt == 0:
                    logger.info(
                        "archive.today attempt 1 failed, retrying in %.1fs", self._retry_delay
                    )
                    await asyncio.sleep(self._retry_delay)
                    continue
                raise ValueError(
                    f"archive.today failed after retry for {origin_url}: {exc}"
                ) from exc

            # 3xx Redirect → Location
            if response.is_redirect or 300 <= response.status_code < 400:
                location: str = response.headers.get("location", "")
                if location:
                    _validate_snapshot_url(location, ARCHIVE_TODAY_HOST)
                    return location

            # Refresh Header: "0; url=<snapshot>"
            refresh: str = response.headers.get("refresh", "")
            if refresh and "url=" in refresh:
                parts = refresh.split("url=")
                if len(parts) == 2:
                    snapshot_url = parts[1].strip()
                    _validate_snapshot_url(snapshot_url, ARCHIVE_TODAY_HOST)
                    return snapshot_url

            # 5xx → Retry
            if response.status_code >= 500:
                last_exc = ValueError(f"archive.today status {response.status_code}")
                if attempt == 0:
                    logger.info("archive.today 5xx, retrying in %.1fs", self._retry_delay)
                    await asyncio.sleep(self._retry_delay)
                    continue
                raise last_exc

            if response.status_code != 200:
                raise ValueError(
                    f"archive.today unexpected status {response.status_code} for {origin_url}"
                )

            # 200 ohne Snapshot-URL → Error
            raise ValueError(f"archive.today returned 200 but no snapshot url for {origin_url}")

        # Unreachable, but satisfy type checker
        raise last_exc if last_exc else ValueError("archive.today failed")

    async def aclose(self) -> None:
        """Schließt den internen httpx-Client, falls einer erzeugt wurde."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ── Snapshot-Validierung (AC7) ──────────────────────────────────────────


def _validate_snapshot_url(snapshot_url: str, expected_host: str) -> None:
    """Prüft Schema https + Host; wirft ValueError bei Fremder Host."""
    parsed = urlparse(snapshot_url)
    if parsed.scheme.lower() != "https":
        raise ValueError(f"snapshot url scheme '{parsed.scheme}' is not https: {snapshot_url!r}")
    actual_host = (parsed.hostname or "").lower()
    if actual_host != expected_host.lower():
        raise ValueError(
            f"snapshot url host '{actual_host}' != expected '{expected_host}': {snapshot_url!r}"
        )


# ── archive_all ─────────────────────────────────────────────────────────


async def archive_all(
    origin_url: str,
    *,
    wayback: Archiver,
    archive_today: Archiver,
) -> ArchiveResult:
    """SSRF-Check auf origin_url, dann beide Dienste anstoßen.

    Teil-Fehlschlag toleriert (Redundanz) und in .errors protokolliert.
    """
    # 1) SSRF-Check — blockiert sofort, kein HTTP-Call
    assert_url_allowed(origin_url)

    errors: dict[str, str] = {}
    wayback_url: str | None = None
    archive_today_url: str | None = None

    # 2) Wayback
    try:
        wayback_url = await wayback.archive(origin_url)
    except Exception as exc:
        errors["wayback"] = str(exc)

    # 3) archive.today
    try:
        archive_today_url = await archive_today.archive(origin_url)
    except Exception as exc:
        errors["archive_today"] = str(exc)

    return ArchiveResult(
        wayback_url=wayback_url,
        archive_today_url=archive_today_url,
        errors=errors,
    )
