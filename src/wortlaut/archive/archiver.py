"""Fremdarchiv-Client — Wayback (SPN2-API) + archive.today mit Snapshot-Validierung.

Importiert nur wortlaut.archive, httpx, stdlib (R-ARCH-02).

Wayback spricht die SPN2-API (Spec 0108): authentifizierter ``POST /save``
liefert eine Auftragsnummer, ``GET /save/status/<job_id>`` wird gepollt, die
Snapshot-URL kommt aus ``timestamp`` + ``original_url`` der Erfolgsantwort.
Die Signatur ``archive(origin_url) -> str`` bleibt unverändert (Spec 0108 §0a).
Fehler kommen auch mit HTTP 200 (``status: error``) — die Auswertung läuft an
beiden Stellen (§0a (2)). Transiente Fehler (429/408/5xx, Timeout, Transport,
status_ext-Allowlist) werden gedrosselt und wiederholt; permanente Fehler
werfen sofort.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

import httpx

from wortlaut.archive.errors import ArchiveError
from wortlaut.archive.pinned import pinned_client
from wortlaut.archive.retry import with_retry
from wortlaut.archive.spn2 import (
    SPN2_SAVE_URL,
    SPN2_STATUS_URL,
    SPN2_USER_STATUS_URL,
    CaptureStatus,
    IaCredentials,
    capture_status_from_payload,
    job_id_from_payload,
    snapshot_url,
    user_status_summary,
)
from wortlaut.archive.ssrf import SsrfBlocked, assert_url_allowed
from wortlaut.archive.throttle import RateLimiter

logger = logging.getLogger(__name__)

# ── Konstanten ──────────────────────────────────────────────────────────

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


# ── Status-Gate (Spec 0073 §4.1, aufgeteilt in Spec 0108 §4.2) ──────────


def _http_error_or_none(
    response: httpx.Response, *, service: str
) -> ArchiveError | None:
    """Reine HTTP-Statusbewertung — EINE Wahrheit für beide Dienste.

    Tabelle:
      401                              → ArchiveError('unauthorized', permanent)
      429 · 408 · 5xx                  → ArchiveError('http_status', transient)
      sonstige 4xx                     → ArchiveError('http_status', permanent)
      alles sonst (2xx/3xx)            → None
    401 hat einen eigenen Grund, damit die Meldung den Betreiber direkt zur
    Ursache führt (SPN2 lehnt anonyme/ungültige Zugangsdaten mit 401 ab).
    """
    status = response.status_code
    if status == 401:
        return ArchiveError(service, "unauthorized", status_code=401, transient=False)
    if status == 429 or status == 408 or 500 <= status <= 599:
        return ArchiveError(service, "http_status", status_code=status, transient=True)
    if not 200 <= status <= 399:
        return ArchiveError(service, "http_status", status_code=status, transient=False)
    return None


def _snapshot_or_error(
    response: httpx.Response,
    *,
    service: str,
    extract: Callable[[httpx.Response], str | None],
) -> str | ArchiveError:
    """Status-Gate ZUERST, danach dienstspezifische Snapshot-Extraktion.

    Die Statusbewertung liegt in ``_http_error_or_none`` (eine einzige
    Wahrheit darüber, welcher Statuscode transient ist, §4.2). Hier hängt die
    dienstspezifische Extraktion daran:

      2xx/3xx mit verwertbarem Header   → Snapshot-URL (schema-/host-validiert)
      2xx/3xx ohne verwertbaren Header  → ArchiveError('no_snapshot_url', permanent)
      401 / 429 · 408 · 5xx / sonstige  → wie ``_http_error_or_none``
    Ein ungültiger Snapshot-URL wird vom Extractor als
    ArchiveError('invalid_snapshot_url') gemeldet und hier durchgereicht.
    """
    error = _http_error_or_none(response, service=service)
    if error is not None:
        return error

    try:
        snapshot_url = extract(response)
    except ArchiveError as exc:
        return exc
    if snapshot_url is None:
        return ArchiveError(service, "no_snapshot_url")
    return snapshot_url


# ── Snapshot-Extraktion (dienstspezifische Header-Quelle) ───────────────


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


# ── Wayback (SPN2) ──────────────────────────────────────────────────────


class WaybackArchiver:
    """Wayback Machine 'Save Page Now' — SPN2-API (Spec 0108).

    Asynchroner Fluss (gemessen, §0a): ``POST /save`` liefert eine
    Auftragsnummer, ``GET /save/status/<job_id>`` wird gepollt, bis
    ``status: success``; die Snapshot-URL wird aus ``timestamp`` und
    ``original_url`` der Erfolgsantwort gebaut und host-validiert. Ein
    frischer Snapshot ist bis zum CDX-Index-Nachziehen nicht exakt
    auflösbar — deshalb keine Auflösung als Gate im Ingest-Pfad (§0a (3)).
    Drosselung und Retry wie #73; die Pflicht auf Zugangsdaten liegt am
    Composition-Root (§4.5), nicht hier.
    """

    def __init__(
        self,
        *,
        credentials: IaCredentials | None = None,
        limiter: RateLimiter | None = None,
        attempts: int = 3,
        base_delay_seconds: float = 2.0,
        poll_interval_seconds: float = 3.0,
        poll_timeout_seconds: float = 180.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._credentials = credentials
        self._limiter = limiter
        self._attempts = attempts
        self._base_delay_seconds = base_delay_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._poll_timeout_seconds = poll_timeout_seconds
        self._sleep = sleep
        self._client: httpx.AsyncClient | None = None

    def _client_or_create(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = pinned_client(WAYBACK_HOST)
        return self._client

    def _headers(self) -> dict[str, str]:
        """Accept-Header plus Authorization NUR mit gesetzten Zugangsdaten.

        Der Header wird nie geloggt (R-SEC-01, AC13) — auch nicht im
        Fehlerpfad.
        """
        headers = {"Accept": "application/json"}
        if self._credentials is not None:
            headers["Authorization"] = self._credentials.authorization_header()
        return headers

    def _json_or_error(self, response: httpx.Response) -> Mapping[str, object]:
        """Dekodiert die JSON-Antwort; alles andere ist ein permanenter
        ``invalid_response``-Fehler.

        Den Antworttext bewusst NICHT ins Log schreiben — er ist Fremdinhalt
        (R-SEC-07).
        """
        try:
            payload: object = response.json()
        except Exception as exc:
            raise ArchiveError("wayback", "invalid_response") from exc
        if not isinstance(payload, dict):
            raise ArchiveError("wayback", "invalid_response")
        return payload

    async def _attempt(self, origin_url: str) -> str:
        """Ein Versuch: drosseln, Capture-Request, Status-Polling, Snapshot-URL."""
        if self._limiter is not None:
            await self._limiter.acquire()
        # SsrfBlocked beim Client-Aufbau betrifft UNSEREN konstanten Archiv-Host
        # (z.B. DNS-Aussetzer) — Infrastruktur, kein Angriff: retrybar statt Lauf-Abbruch.
        try:
            client = self._client_or_create()
        except SsrfBlocked as exc:
            raise ArchiveError("wayback", "transport", transient=True) from exc

        # 1) Capture-Request — asynchron: die Antwort trägt nur eine Job-ID.
        try:
            response = await client.post(
                SPN2_SAVE_URL,
                data={"url": origin_url},
                headers=self._headers(),
            )
        except httpx.TimeoutException as exc:
            raise ArchiveError("wayback", "timeout", transient=True) from exc
        except httpx.TransportError as exc:
            raise ArchiveError("wayback", "transport", transient=True) from exc

        error = _http_error_or_none(response, service="wayback")
        if error is not None:
            raise error
        payload = self._json_or_error(response)
        job_id = job_id_from_payload(payload)

        # 2) Polling, bis success oder das Versuchslimit (§4.4: Versuche, nicht
        #    Sekunden — mit der injizierten Sleep deterministisch testbar).
        max_polls = max(1, int(self._poll_timeout_seconds // self._poll_interval_seconds))
        status = await self._poll_until_success(client, job_id, max_polls)

        # 3) Snapshot-URL aus der Erfolgsantwort; 14-stelliger Stempel,
        #    dann Host-Validierung. Kein Lösungs-Check (§0a (3)).
        timestamp: object = status.timestamp
        original_url: object = status.original_url
        if not isinstance(timestamp, str) or not isinstance(original_url, str):
            raise ArchiveError("wayback", "invalid_snapshot_url")
        url = snapshot_url(timestamp, original_url)
        _validate_snapshot_url(url, service="wayback", host=WAYBACK_HOST)
        return url

    async def _poll_until_success(
        self, client: httpx.AsyncClient, job_id: str, max_polls: int
    ) -> CaptureStatus:
        """Pollt ``GET /save/status/<job_id>`` bis ``success``; danach Timeout.

        Fehler werden auch mit HTTP 200 erkannt (``status: error``, §0a (2)) —
        ``capture_status_from_payload`` entscheidet über Transienz via der
        status_ext-Allowlist. Ein ``capture_timeout`` ist permanent: SPN2
        begrenzt die Capture-Dauer selbst auf 2 Minuten; wer nach dem
        Timeout-Limit nicht fertig ist, wird es beim zweiten Anlauf auch
        nicht (§4.4).
        """
        for _poll in range(max_polls):
            await self._sleep(self._poll_interval_seconds)
            try:
                response = await client.get(f"{SPN2_STATUS_URL}{job_id}", headers=self._headers())
            except httpx.TimeoutException as exc:
                raise ArchiveError("wayback", "timeout", transient=True) from exc
            except httpx.TransportError as exc:
                raise ArchiveError("wayback", "transport", transient=True) from exc

            error = _http_error_or_none(response, service="wayback")
            if error is not None:
                raise error
            status = capture_status_from_payload(self._json_or_error(response))
            if status.state == "success":
                return status
        raise ArchiveError("wayback", "capture_timeout", transient=False)

    async def archive(self, origin_url: str) -> str:
        """SPN2-Capture für ``origin_url``; Snapshot NUR aus Erfolgsantwort (§4.1)."""
        return await with_retry(
            lambda: self._attempt(origin_url),
            attempts=self._attempts,
            base_delay_seconds=self._base_delay_seconds,
        )

    async def user_status(self) -> str:
        """Pre-Flight-Call (Spec 0108 §0b): ``GET /save/status/user``.

        Belegt in einem Call, dass die Zugangsdaten akzeptiert werden und der
        Dienst antwortet — ohne Capture-Kontingent, ohne Wartezeit. **Kein**
        Retry, **keine** Bewertung von ``available``/``daily_captures``
        (Sekundenzustand, nur Log, §0b).
        """
        try:
            client = self._client_or_create()
        except SsrfBlocked as exc:
            raise ArchiveError("wayback", "transport", transient=True) from exc
        # Cache-Buster gemäß API-Doku; Header wie bei allen SPN2-Calls.
        url = f"{SPN2_USER_STATUS_URL}?_t={time.time_ns()}"
        try:
            response = await client.get(url, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise ArchiveError("wayback", "timeout", transient=True) from exc
        except httpx.TransportError as exc:
            raise ArchiveError("wayback", "transport", transient=True) from exc

        error = _http_error_or_none(response, service="wayback")
        if error is not None:
            raise error
        return user_status_summary(self._json_or_error(response))

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
