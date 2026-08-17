"""Fremdarchiv-Client — Wayback Machine + archive.today mit Snapshot-Validierung.

Importiert nur wortlaut.archive, httpx, stdlib (R-ARCH-02).

Spec 0073: Eine Snapshot-URL wird NUR aus einer Erfolgsantwort (2xx/3xx)
akzeptiert — der Status wird vor den Snapshot-Headers geprüft (Beweis-Anker,
R-CORE-02). Transiente Fehler (429/408/5xx, Timeout, Transport) werden
gedrosselt und wiederholt; permanente Fehler werfen sofort.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urljoin, urlparse

import httpx

from wortlaut.archive.errors import ArchiveError
from wortlaut.archive.pinned import pinned_client
from wortlaut.archive.retry import with_retry
from wortlaut.archive.ssrf import SsrfBlocked, assert_url_allowed
from wortlaut.archive.throttle import RateLimiter

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
    failures: dict[str, ArchiveError]  # {'wayback': ..., 'archive_today': ...} bei Fehlern


class Archiver(Protocol):
    """Prototyp-Schnittstelle für Fremdarchiv-Implementierungen."""

    async def archive(self, origin_url: str) -> str: ...


# ── Status-Gate (Spec 0073 §4.1) ────────────────────────────────────────


def _snapshot_or_error(
    response: httpx.Response,
    *,
    service: str,
    extract: Callable[[httpx.Response], str | None],
) -> str | ArchiveError:
    """Status-Gate ZUERST, danach dienstspezifische Snapshot-Extraktion.

    Tabelle (identisch für beide Dienste):
      2xx/3xx mit verwertbarem Header   → Snapshot-URL (schema-/host-validiert)
      2xx/3xx ohne verwertbaren Header  → ArchiveError('no_snapshot_url', permanent)
      429 · 408 · 5xx                   → ArchiveError('http_status', transient)
      sonstige 4xx                      → ArchiveError('http_status', permanent)
    Ein ungültiger Snapshot-URL wird vom Extractor als
    ArchiveError('invalid_snapshot_url') gemeldet und hier durchgereicht.
    """
    status = response.status_code
    if status == 429 or status == 408 or 500 <= status <= 599:
        return ArchiveError(service, "http_status", status_code=status, transient=True)
    if 400 <= status <= 499:
        return ArchiveError(service, "http_status", status_code=status, transient=False)
    if not 200 <= status <= 399:
        return ArchiveError(service, "http_status", status_code=status, transient=False)

    try:
        snapshot_url = extract(response)
    except ArchiveError as exc:
        return exc
    if snapshot_url is None:
        return ArchiveError(service, "no_snapshot_url")
    return snapshot_url


# ── Snapshot-Extraktion (dienstspezifische Header-Quelle) ───────────────


def _snapshot_from_wayback(response: httpx.Response) -> str | None:
    """Extrahiert die Snapshot-URL: Content-Location (relativ → gegen
    ``https://web.archive.org`` auflösen), sonst Location bei Redirect.

    None, wenn die Antwort keinen verwertbaren Header trägt; gültige URLs
    werden gegen den Wayback-Host validiert (ArchiveError bei Fremd-Host/Scheme).
    """
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

    if snapshot_url is None:
        return None
    _validate_snapshot_url(snapshot_url, service="wayback", host=WAYBACK_HOST)
    return snapshot_url


def _snapshot_from_archive_today(response: httpx.Response) -> str | None:
    """Extrahiert die Snapshot-URL: Location bei Redirect, sonst Refresh-Header
    der Form ``0; url=<snapshot>`` (der Regelfall ist 200 + Refresh).

    None, wenn die Antwort keine Snapshot-URL trägt; gefundene URLs werden
    gegen den archive.today-Host validiert (ArchiveError bei Fremd-Host/Scheme).
    """
    # 3xx Redirect → Location
    if response.is_redirect or 300 <= response.status_code < 400:
        location: str = response.headers.get("location", "")
        if location:
            _validate_snapshot_url(location, service="archive_today", host=ARCHIVE_TODAY_HOST)
            return location

    # Refresh Header: "0; url=<snapshot>"
    refresh: str = response.headers.get("refresh", "")
    if refresh and "url=" in refresh:
        parts = refresh.split("url=")
        if len(parts) == 2:
            snapshot_url = parts[1].strip()
            _validate_snapshot_url(snapshot_url, service="archive_today", host=ARCHIVE_TODAY_HOST)
            return snapshot_url

    return None


def _validate_snapshot_url(snapshot_url: str, *, service: str, host: str) -> None:
    """Prüft Schema https + Host; wirft ArchiveError('invalid_snapshot_url').

    Die Meldung nennt den beanstandeten Wert — ein verworfener Snapshot muss
    diagnostizierbar sein (Observability-Ziel des Increments). Archiv-URLs sind
    öffentlich und tragen keine Secrets (R-SEC-01).
    """
    parsed = urlparse(snapshot_url)
    if parsed.scheme.lower() != "https":
        logger.warning("verworfener Snapshot (%s): Schema nicht https: %r", service, snapshot_url)
        raise ArchiveError(service, "invalid_snapshot_url")
    actual_host = (parsed.hostname or "").lower()
    if actual_host != host.lower():
        logger.warning(
            "verworfener Snapshot (%s): Host %r != %r: %r",
            service,
            actual_host,
            host.lower(),
            snapshot_url,
        )
        raise ArchiveError(service, "invalid_snapshot_url")


# ── Wayback ─────────────────────────────────────────────────────────────


class WaybackArchiver:
    """Wayback Machine 'Save Page Now' — Status-Gate, Drosselung, Retry (Spec 0073)."""

    def __init__(
        self,
        *,
        limiter: RateLimiter | None = None,
        attempts: int = 3,
        base_delay_seconds: float = 2.0,
    ) -> None:
        self._limiter = limiter
        self._attempts = attempts
        self._base_delay_seconds = base_delay_seconds
        self._client: httpx.AsyncClient | None = None

    def _client_or_create(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = pinned_client(WAYBACK_HOST)
        return self._client

    async def _attempt(self, origin_url: str) -> str:
        """Ein Versuch: optional drosseln, HTTP-Call, Status-Gate auf die Antwort."""
        if self._limiter is not None:
            await self._limiter.acquire()
        save_url = f"{WAYBACK_SAVE_URL}{origin_url}"
        # SsrfBlocked beim Client-Aufbau betrifft UNSEREN konstanten Archiv-Host
        # (z.B. DNS-Aussetzer) — Infrastruktur, kein Angriff: retrybar statt Lauf-Abbruch.
        try:
            client = self._client_or_create()
        except SsrfBlocked as exc:
            raise ArchiveError("wayback", "transport", transient=True) from exc
        try:
            response = await client.get(save_url)
        except httpx.TimeoutException as exc:
            raise ArchiveError("wayback", "timeout", transient=True) from exc
        except httpx.TransportError as exc:
            raise ArchiveError("wayback", "transport", transient=True) from exc

        result = _snapshot_or_error(response, service="wayback", extract=_snapshot_from_wayback)
        if isinstance(result, ArchiveError):
            raise result
        return result

    async def archive(self, origin_url: str) -> str:
        """Löst ``GET /save/<origin_url>`` ab; Snapshot NUR aus Erfolgsantwort (§4.1)."""
        return await with_retry(
            lambda: self._attempt(origin_url),
            attempts=self._attempts,
            base_delay_seconds=self._base_delay_seconds,
        )

    async def aclose(self) -> None:
        """Schließt den internen httpx-Client, falls einer erzeugt wurde."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ── archive.today ───────────────────────────────────────────────────────


class ArchiveTodayArchiver:
    """archive.today — POST /submit/ mit Drosselung und Retry (Spec 0073).

    429 wird als transient erkannt und gedrosselt wiederholt; der
    handgeschriebene Retry aus Spec 0004 entfällt zugunsten von ``with_retry``.
    """

    def __init__(
        self,
        *,
        limiter: RateLimiter | None = None,
        attempts: int = 2,
        base_delay_seconds: float = 2.0,
    ) -> None:
        self._limiter = limiter
        self._attempts = attempts
        self._base_delay_seconds = base_delay_seconds
        self._client: httpx.AsyncClient | None = None

    def _client_or_create(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = pinned_client(ARCHIVE_TODAY_HOST)
        return self._client

    async def _attempt(self, origin_url: str) -> str:
        """Ein Versuch: optional drosseln, POST /submit/, Status-Gate auf die Antwort."""
        if self._limiter is not None:
            await self._limiter.acquire()
        # Siehe WaybackArchiver._attempt: SsrfBlocked auf dem eigenen Host ist Infrastruktur.
        try:
            client = self._client_or_create()
        except SsrfBlocked as exc:
            raise ArchiveError("archive_today", "transport", transient=True) from exc
        try:
            response = await client.post(
                ARCHIVE_TODAY_SUBMIT_URL,
                data={"url": origin_url},
            )
        except httpx.TimeoutException as exc:
            raise ArchiveError("archive_today", "timeout", transient=True) from exc
        except httpx.TransportError as exc:
            raise ArchiveError("archive_today", "transport", transient=True) from exc

        result = _snapshot_or_error(
            response, service="archive_today", extract=_snapshot_from_archive_today
        )
        if isinstance(result, ArchiveError):
            raise result
        return result

    async def archive(self, origin_url: str) -> str:
        """POST ``/submit/`` mit ``url=<origin_url>``; Snapshot NUR aus Erfolgsantwort."""
        return await with_retry(
            lambda: self._attempt(origin_url),
            attempts=self._attempts,
            base_delay_seconds=self._base_delay_seconds,
        )

    async def aclose(self) -> None:
        """Schließt den internen httpx-Client, falls einer erzeugt wurde."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ── archive_all ─────────────────────────────────────────────────────────


async def archive_all(
    origin_url: str,
    *,
    wayback: Archiver,
    archive_today: Archiver,
) -> ArchiveResult:
    """SSRF-Check auf origin_url, dann beide Dienste anstoßen.

    Reiner Reporter (Spec 0004 §3): Teil-Fehlschlag wird in ``failures`` als
    ``ArchiveError`` strukturiert — die Hard/Soft-Entscheidung trifft der
    Aufrufer (pipeline). ``SsrfBlocked`` fliegt unverändert durch (Security-Stopp,
    kein Archiv-Fehler); jede andere Implementierungs-Exception wird zu
    ``ArchiveError(reason='unexpected')`` gewrappt, nie durchgelassen.
    """
    # 1) SSRF-Check — blockiert sofort, kein HTTP-Call; AUSSERHALB jedes try
    assert_url_allowed(origin_url)

    failures: dict[str, ArchiveError] = {}
    wayback_url: str | None = None
    archive_today_url: str | None = None

    # 2) Wayback
    try:
        wayback_url = await wayback.archive(origin_url)
    except SsrfBlocked:
        raise  # Security-Stopp: NIEMALS in ArchiveError degradieren
    except ArchiveError as exc:
        failures["wayback"] = exc
    except Exception as exc:  # Implementation-Defekt: strukturiert, nie durchgelassen
        wrapped = ArchiveError("wayback", "unexpected")
        wrapped.__cause__ = exc
        failures["wayback"] = wrapped

    # 3) archive.today
    try:
        archive_today_url = await archive_today.archive(origin_url)
    except SsrfBlocked:
        raise  # Security-Stopp: NIEMALS in ArchiveError degradieren
    except ArchiveError as exc:
        failures["archive_today"] = exc
    except Exception as exc:  # Implementation-Defekt: strukturiert, nie durchgelassen
        wrapped = ArchiveError("archive_today", "unexpected")
        wrapped.__cause__ = exc
        failures["archive_today"] = wrapped

    return ArchiveResult(
        wayback_url=wayback_url,
        archive_today_url=archive_today_url,
        failures=failures,
    )
