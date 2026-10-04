"""Landtag Sachsen-Anhalt — Adapter für Stenografische Berichte (#145).

Entdeckt die jüngsten Protokolle über die Nummer (HEAD, exponentiell dann binär,
Spec 0145 §0b.5) und holt das PDF. Abruf nur, wenn ``enabled`` freigegeben ist.
Importiert nur ``wortlaut.ingest.adapter``, ``wortlaut.ingest.settings``,
``wortlaut.ingest.protokoll_parse`` und ``wortlaut.ingest.landtag_st_parse``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

from wortlaut.ingest.adapter import AdapterError, RawSource, SourceRef, SpanDraft
from wortlaut.ingest.landtag_st_parse import parse_header_st, segment_speeches_st
from wortlaut.ingest.protokoll_parse import extract_text
from wortlaut.ingest.settings import LandtagStSettings

logger = logging.getLogger(__name__)

_MAX_NUMBER = 1000  # drei Stellen im Dateinamen: Nummern < 1000


class LandtagStError(AdapterError):
    """Der Adapter kann eine Quelle (oder die Entdeckung) gerade nicht liefern."""


class LandtagStDisabled(LandtagStError):
    """Abruf nicht freigegeben (Schalter ``enabled``, Spec 0145 §0b.1)."""


class LandtagSachsenAnhaltAdapter:
    """Landtag Sachsen-Anhalt (erfüllt :class:`IngestAdapter`)."""

    name = "landtag-st"
    version = "1.0.0"
    trust_level = "secondary"
    parliament = "landtag-sachsen-anhalt"
    mandate_role = "MdL"
    # Plenarprotokolle: amtliches Werk, § 5 UrhG (docs/legal.md §2).
    rights_basis = "amtliches_werk_p5"

    def __init__(self, settings: LandtagStSettings) -> None:
        self._settings = settings
        self._client: httpx.AsyncClient | None = None
        parsed = urlparse(settings.base_url)
        self._host = parsed.hostname or ""

    @classmethod
    def from_env(cls) -> LandtagSachsenAnhaltAdapter:
        """Baut den Adapter aus seinen eigenen ENV-Einstellungen (``WORTLAUT_LANDTAG_ST_*``)."""
        return cls(LandtagStSettings())

    def _new_client(self, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
        """Neuer Client mit erkennbarem User-Agent (Spec 0145 §0b.6); Tests setzen transport."""
        return httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(30.0),
            headers={"User-Agent": f"wortlaut-ingest/1.0 (+{self._settings.contact})"},
            transport=transport,
        )

    def _client_or_create(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = self._new_client()
        return self._client

    def _url(self, wp: int, n: int) -> str:
        return f"{self._settings.base_url}/wp{wp}/{n:03d}stzg.pdf"

    async def _exists(self, url: str) -> bool:
        """HEAD: 200 → True, 404 → False, alles andere (inkl. 3xx, Netzfehler) → Fehler."""
        try:
            response = await self._client_or_create().head(url)
        except httpx.TransportError as exc:  # schliesst TimeoutException ein
            raise LandtagStError(f"network error for {url}: {type(exc).__name__}") from exc
        if response.status_code == 200:
            return True
        if response.status_code == 404:
            return False
        raise LandtagStError(f"unexpected status {response.status_code} for {url}")

    async def _highest(self, wp: int) -> int:
        """Höchste vorhandene Nummer: exponentiell, dann binär (≤ 21 Anfragen je WP)."""
        if not await self._exists(self._url(wp, 1)):
            return 0
        hi = 1
        while hi * 2 <= _MAX_NUMBER - 1 and await self._exists(self._url(wp, hi * 2)):
            hi *= 2
        best = hi
        lo, top = hi, min(hi * 2, _MAX_NUMBER)
        while lo < top:
            mid = (lo + top) // 2
            if await self._exists(self._url(wp, mid)):
                best = mid
                lo = mid + 1
            else:
                top = mid
        return best

    def _refs_for(self, wp: int, top: int) -> list[SourceRef]:
        """Refs für die letzten ``lookback`` Protokolle der Wahlperiode ``wp`` (aufsteigend)."""
        first = max(1, top - self._settings.lookback + 1)
        return [
            SourceRef(
                origin_url=self._url(wp, n),
                source_type="plenarprotokoll",
                hint={"wahlperiode": str(wp), "sitzung": str(n)},
            )
            for n in range(first, top + 1)
        ]

    async def discover(self, since: datetime) -> Sequence[SourceRef]:
        """Nummernsuche je WP (aktuelle und folgende) → Refs der letzten ``lookback`` Protokolle.

        ``since`` wird nicht ausgewertet — die Quelle nennt das Datum erst im PDF;
        Doppelte fängt der Kern über den Inhalts-Hash ab. Ohne Schalter wirft ``discover``
        sofort und stellt keine Anfrage.
        """
        if not self._settings.enabled:
            raise LandtagStDisabled("Abruf nicht freigegeben (WORTLAUT_LANDTAG_ST_ENABLED)")
        logger.info(
            "landtag-st: Entdeckung über die Sitzungsnummer; since=%s wird nicht ausgewertet",
            since.date().isoformat(),
        )
        refs: list[SourceRef] = []
        for wp in (self._settings.wahlperiode, self._settings.wahlperiode + 1):
            refs.extend(self._refs_for(wp, await self._highest(wp)))
        return refs

    async def fetch(self, ref: SourceRef) -> RawSource:
        """Holt die PDF-Bytes von ref.origin_url (Host-Pin, keine Redirects, PDF-Magic)."""
        host = urlparse(ref.origin_url).hostname or ""
        if host != self._host:
            raise LandtagStError(
                f"Host '{host}' ist nicht der erwartete Host '{self._host}' — refusing fetch"
            )

        try:
            response = await self._client_or_create().get(ref.origin_url)
        except httpx.TransportError as exc:  # schliesst TimeoutException ein
            message = f"network error for {ref.origin_url}: {type(exc).__name__}"
            raise LandtagStError(message) from exc

        if response.is_redirect or 300 <= response.status_code < 400:
            logger.warning(
                "Landtag-St fetch got redirect (%s) for %s", response.status_code, ref.origin_url
            )
            raise LandtagStError(f"unexpected redirect {response.status_code} for {ref.origin_url}")

        if response.status_code != 200:
            raise LandtagStError(f"unexpected status {response.status_code} for {ref.origin_url}")

        content = response.content
        if not content.startswith(b"%PDF-"):
            message = f"response body is not a PDF (no %PDF- magic) for {ref.origin_url}"
            raise LandtagStError(message)

        content_type = response.headers.get("content-type", "")
        if "application/pdf" not in content_type.lower():
            raise LandtagStError(f"unexpected content-type '{content_type}' for {ref.origin_url}")

        return RawSource(
            origin_url=ref.origin_url,
            source_type=ref.source_type,
            raw_bytes=content,
            mime_type="application/pdf",
            retrieved_at=datetime.now(UTC),
        )

    def normalize(self, raw: RawSource) -> str:
        """PDF-Bytes → deterministischer, spaltenbewusster kanonischer Klartext (#41)."""
        return extract_text(raw.raw_bytes)

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        """Kanonischer Text → je Redebeitrag ein SpanDraft (Rolle je Beitrag, #145)."""
        spoken_at, base_locator = parse_header_st(normalized)
        drafts: list[SpanDraft] = []
        for seg in segment_speeches_st(normalized):
            hint: dict[str, object] = {"name": seg.name, "party": seg.party}
            if seg.role:
                hint["role"] = seg.role
            drafts.append(
                SpanDraft(
                    verbatim_text=seg.verbatim_text,
                    text_start=seg.text_start,
                    text_end=seg.text_end,
                    speaker_hint=hint,
                    spoken_at=spoken_at,
                    locator={**base_locator, "tagesordnungspunkt": seg.tagesordnungspunkt},
                    permalink=raw.origin_url,
                )
            )
        return drafts

    async def aclose(self) -> None:
        """Schließt den internen httpx-Client, falls einer erzeugt wurde."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
