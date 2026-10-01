"""Wayback-Lookup: Snapshot-Suche (CDX) und exakter ``id_``-Abruf (Spec 0124).

Rein lesend: ``candidates`` fragt den CDX-Index mit der exakten URL und dem
SHA-1-Digest ab (nur Vorfilter, nie Beweis — bewiesen wird über SHA-256 gegen
``content_hash``), ``fetch`` lädt die Rohbytes eines Kandidaten per
``id_``-Playback. Ein 3xx wird **nie** gefolgt (Umleitung = anderer Snapshot,
§0d); eine Größengrenze schützt vor unkontrollierten Antworten (§4.3).
**Kein** Capture, **keine** Zugangsdaten.

Eigene Host-Konstante, kein Import aus ``archiver.py``/``spn2.py`` (dort lebt
der Capture-Pfad; Spec 0124 §4.5). R-SEC-05: alle Anfragen laufen über den
gepinnten Transport auf ``web.archive.org``; R-SEC-07: Antworttexte und
Bytes werden nie geloggt.
"""

from __future__ import annotations

import asyncio
import email.utils
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlsplit

import httpx

# ``SsrfBlocked`` kommt direkt aus ``ssrf`` (Muster ``archiver.py``); ``pinned``
# liefert nur den Transport. Spec 0124 §11 verbietet Importe aus ``archiver.py``
# und ``spn2.py`` (Capture-Pfad) — ``ssrf`` ist der R-SEC-05-Wächter, kein Capture.
from wortlaut.archive.errors import ArchiveError
from wortlaut.archive.pinned import pinned_client
from wortlaut.archive.retry import with_retry
from wortlaut.archive.ssrf import SsrfBlocked
from wortlaut.archive.throttle import RateLimiter

_HOST = "web.archive.org"
_CDX_PATH = "/cdx/search/cdx"
_CDX_FIELDS = "timestamp,original,statuscode,digest,mimetype"
# "-" = deduplizierter Revisit: die Bytes sind im Archiv, der Capture selbst
# wurde vom Internet Archive zusammengefasst (§0c). Ein Status-Filter auf 200
# allein würde genau diese Snapshots verwerfen.
_OK_STATUS: frozenset[str] = frozenset({"200", "-"})


@dataclass(frozen=True)
class SnapshotCandidate:
    """Ein CDX-Snapshot: 14-stelliger Zeitstempel plus Original-URL laut CDX."""

    timestamp: str  # 14 Ziffern, YYYYMMDDhhmmss
    original: str  # URL laut CDX


class WaybackLookup(Protocol):
    """Öffentliche Schnittstelle für die Attestierungs-Suche (nur lesend)."""

    async def candidates(self, origin_url: str, *, sha1_b32: str) -> list[SnapshotCandidate]: ...

    async def fetch(self, candidate: SnapshotCandidate) -> bytes | None: ...

    async def aclose(self) -> None: ...


def snapshot_url(candidate: SnapshotCandidate) -> str:
    """Menschliche Snapshot-Adresse (ohne ``id_``) — wird in ``source_archive`` festgehalten."""
    return f"https://{_HOST}/web/{candidate.timestamp}/{candidate.original}"


def _is_timestamp(value: str) -> bool:
    """Genau 14 ASCII-Ziffern (YYYYMMDDhhmmss), sonst verworfen (Spec 0124 §11)."""
    return len(value) == 14 and value.isascii() and value.isdigit()


def _url_matches(original: str, origin_url: str) -> bool:
    """``original`` und ``origin_url`` gleich in Host, Pfad und Query?

    Schema ``http``/``https`` darf abweichen, der Host ist case-insensitiv
    (Spec 0124 §4.2); jede andere Abweichung → kein Kandidat.
    """
    a = urlsplit(original)
    b = urlsplit(origin_url)
    if a.scheme not in ("http", "https") or b.scheme not in ("http", "https"):
        return False
    if (a.hostname or "").lower() != (b.hostname or "").lower():
        return False
    if a.path != b.path:
        return False
    if a.query != b.query:
        return False
    return True


def _candidate_from_row(row: object, origin_url: str, sha1_b32: str) -> SnapshotCandidate | None:
    """CDX-Zeile → Kandidat, wenn Digest, Status und URL passen (sonst verworfen)."""
    if not isinstance(row, list) or len(row) != 5:
        return None
    values: list[str] = []
    for value in row:
        if not isinstance(value, str):
            return None
        values.append(value)
    timestamp, original, statuscode, digest = values[0], values[1], values[2], values[3]
    if not _is_timestamp(timestamp):
        return None
    if digest != sha1_b32:
        return None
    if statuscode not in _OK_STATUS:
        return None
    if not _url_matches(original, origin_url):
        return None
    return SnapshotCandidate(timestamp=timestamp, original=original)


def _memento_matches(header: str, timestamp: str) -> bool:
    """RFC-1123-``Memento-Datetime`` exakt gleich dem Kandidaten-Zeitstempel (UTC)?"""
    try:
        moment = email.utils.parsedate_to_datetime(header)
    except (TypeError, ValueError):
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    expected = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    return moment == expected


async def _read_limited(response: httpx.Response, max_bytes: int) -> bytes:
    """Gestreamtes Lesen; gelesen werden höchstens ``max_bytes + 1`` Bytes.

    Wird die Grenze überschritten, ist die Antwort zu groß:
    ``ArchiveError(reason='too_large')`` (permanent) statt des restlichen Downloads.
    """
    parts: list[bytes] = []
    total = 0
    limit = max_bytes + 1
    async for chunk in response.aiter_raw():
        take = min(len(chunk), limit - total)
        parts.append(chunk[:take])
        total += take
        if total >= limit:
            break
    data = b"".join(parts)
    if len(data) > max_bytes:
        raise ArchiveError("wayback", "too_large", transient=False)
    return data


async def _evaluate_fetch(
    response: httpx.Response, candidate: SnapshotCandidate, max_bytes: int
) -> bytes | None:
    """Status- und Memento-Gate (Spec 0124 §4.3) für eine bereits empfangene Antwort.

    200 + passendes ``Memento-Datetime`` → Rohbytes (mit Größengrenze);
    200 + fehlendes/abweichendes ``Memento-Datetime`` → ``None``;
    3xx (Umleitung = anderer Snapshot) → ``None``, nie folgen;
    404 → ``None``; 429/408/5xx → transient; sonst → permanent.
    """
    status = response.status_code
    if status == 200:
        memento = response.headers.get("memento-datetime")
        if memento is None:
            return None
        if not _memento_matches(memento, candidate.timestamp):
            return None
        return await _read_limited(response, max_bytes)
    if 300 <= status <= 399 or status == 404:
        return None
    if status == 429 or status == 408 or 500 <= status <= 599:
        raise ArchiveError("wayback", "http_status", status_code=status, transient=True)
    raise ArchiveError("wayback", "http_status", status_code=status, transient=False)


class HttpWaybackLookup:
    """Erfüllt ``WaybackLookup``: CDX-Suche und exakter ``id_``-Abruf, nur lesend.

    Client lazy über den gepinnten Transport (``pinned_client``); ``client`` ist
    eine Test-Seam (z.B. ``httpx.MockTransport``). Vor jeder Anfrage wird der
    ``RateLimiter`` gezogen; transiente Fehler laufen über ``with_retry``.
    """

    # Signatur ist in Spec 0124 §3 festgelegt; alle Parameter sind optionale
    # Keyword-Argumente (kein R-ARCH-04-Verstoß im Sinne einer wachsenden API).
    def __init__(  # noqa: PLR0913
        self,
        *,
        limiter: RateLimiter | None = None,
        max_bytes: int = 100 * 1024 * 1024,
        attempts: int = 3,
        base_delay_seconds: float = 2.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._limiter = limiter
        self._max_bytes = max_bytes
        self._attempts = attempts
        self._base_delay_seconds = base_delay_seconds
        self._sleep = sleep
        self._client = client

    def _client_or_create(self) -> httpx.AsyncClient:
        """Lazy Client über den gepinnten Transport; ``aclose()`` schließt ihn."""
        if self._client is None:
            # SsrfBlocked beim Client-Aufbau betrifft unseren konstanten
            # Archiv-Host (z.B. DNS-Aussetzer) — Infrastruktur, kein Angriff:
            # retrybar statt Lauf-Abbruch (Muster archiver.py).
            try:
                self._client = pinned_client(_HOST)
            except SsrfBlocked as exc:
                raise ArchiveError("wayback", "transport", transient=True) from exc
        return self._client

    async def candidates(self, origin_url: str, *, sha1_b32: str) -> list[SnapshotCandidate]:
        """CDX mit exakter URL; Kandidaten nach §4.2 (Digest, Status, URL-Gleichheit)."""
        return await with_retry(
            lambda: self._candidates_attempt(origin_url, sha1_b32),
            attempts=self._attempts,
            base_delay_seconds=self._base_delay_seconds,
            sleep=self._sleep,
        )

    async def _candidates_attempt(self, origin_url: str, sha1_b32: str) -> list[SnapshotCandidate]:
        if self._limiter is not None:
            await self._limiter.acquire()
        client = self._client_or_create()
        try:
            response = await client.get(
                f"https://{_HOST}{_CDX_PATH}",
                params={"url": origin_url, "output": "json", "fl": _CDX_FIELDS},
            )
        except httpx.TimeoutException as exc:
            raise ArchiveError("wayback", "timeout", transient=True) from exc
        except httpx.TransportError as exc:
            raise ArchiveError("wayback", "transport", transient=True) from exc

        status = response.status_code
        if status == 429 or status == 408 or 500 <= status <= 599:
            raise ArchiveError("wayback", "http_status", status_code=status, transient=True)
        if status != 200:
            raise ArchiveError("wayback", "http_status", status_code=status, transient=False)

        try:
            payload: object = response.json()
        except Exception as exc:
            # Antworttext ist Fremdinhalt und wird nie geloggt (R-SEC-07).
            raise ArchiveError("wayback", "invalid_response", transient=False) from exc
        if not isinstance(payload, list):
            raise ArchiveError("wayback", "invalid_response", transient=False)

        # Leere Antwort oder nur Kopfzeile → keine Kandidaten.
        candidates: list[SnapshotCandidate] = []
        for row in payload[1:]:
            candidate = _candidate_from_row(row, origin_url, sha1_b32)
            if candidate is not None:
                candidates.append(candidate)
        return candidates

    async def fetch(self, candidate: SnapshotCandidate) -> bytes | None:
        """Exakter ``id_``-Abruf; ``None``, wenn der Snapshot nicht exakt abrufbar ist."""
        return await with_retry(
            lambda: self._fetch_attempt(candidate),
            attempts=self._attempts,
            base_delay_seconds=self._base_delay_seconds,
            sleep=self._sleep,
        )

    async def _fetch_attempt(self, candidate: SnapshotCandidate) -> bytes | None:
        if self._limiter is not None:
            await self._limiter.acquire()
        client = self._client_or_create()
        # ``original`` ist Fremdinhalt und bleibt Pfadteil hinter dem gepinnten
        # Host — der Transport verweigert jeden anderen Host (R-SEC-05).
        url = f"https://{_HOST}/web/{candidate.timestamp}id_/{candidate.original}"
        try:
            # identity: gehasht werden die unkomprimierten Rohbytes — auch falls der
            # Dienst kuenftig komprimiert ausliefert (httpx bietet sonst gzip an).
            async with client.stream(
                "GET", url, headers={"Accept-Encoding": "identity"}
            ) as response:
                return await _evaluate_fetch(response, candidate, self._max_bytes)
        except httpx.TimeoutException as exc:
            raise ArchiveError("wayback", "timeout", transient=True) from exc
        except httpx.TransportError as exc:
            raise ArchiveError("wayback", "transport", transient=True) from exc

    async def aclose(self) -> None:
        """Schließt den httpx-Client, falls einer erzeugt wurde."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
