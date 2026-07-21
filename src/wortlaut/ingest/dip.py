"""DIP-Plenarprotokoll-Adapter — erster konkreter IngestAdapter.

Entdeckt Plenarprotokolle via DIP-Metadaten-Endpoint und holt das amtliche PDF.
Importiert nur ``wortlaut.ingest.adapter`` und ``wortlaut.ingest.settings``.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

from wortlaut.ingest.adapter import RawSource, SourceRef, SpanDraft
from wortlaut.ingest.protokoll_parse import (
    extract_text,
    parse_header,
    segment_speeches,
)
from wortlaut.ingest.settings import DipSettings

logger = logging.getLogger(__name__)

_MAX_PAGES = 1000  # Fail-loud-Wächter gegen Endlos-Pagination


class DipFetchError(Exception):
    """Fetch lieferte etwas anderes als ein direkt geliefertes, gueltiges PDF."""


class DipPlenarprotokollAdapter:
    """DIP-Plenarprotokoll-Adapter (erfüllt :class:`IngestAdapter`)."""

    name = "dip-api"
    version = "1.0.0"
    trust_level = "verified_primary"

    def __init__(self, settings: DipSettings) -> None:
        self._settings = settings
        self._client: httpx.AsyncClient | None = None
        parsed = urlparse(settings.api_base_url)
        self._api_host = parsed.hostname or ""

    def _client_or_create(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                follow_redirects=False,
                timeout=httpx.Timeout(30.0),
            )
        return self._client

    async def discover(self, since: datetime) -> Sequence[SourceRef]:
        """DIP-Metadaten-Endpoint → je Protokoll ein SourceRef (folgt der Cursor-Pagination)."""
        url = f"{self._settings.api_base_url}/plenarprotokoll"
        base_params: dict[str, str] = {
            "f.datum.start": since.strftime("%Y-%m-%d"),
            "f.zuordnung": "BT",
            "format": "json",
        }
        headers = {"Authorization": f"ApiKey {self._settings.api_key}"}
        refs: list[SourceRef] = []
        cursor: str | None = None
        for _ in range(_MAX_PAGES):
            params = dict(base_params)
            if cursor is not None:
                params["cursor"] = cursor
            response = await self._client_or_create().get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
            docs: list[dict[str, object]] = data.get("documents", [])
            refs.extend(self._refs_from_documents(docs))
            new_cursor: str | None = data.get("cursor")
            if not new_cursor or new_cursor == cursor:
                return refs
            cursor = new_cursor
        raise DipFetchError(f"pagination did not terminate after {_MAX_PAGES} pages")

    def _refs_from_documents(self, documents: list[dict[str, object]]) -> list[SourceRef]:
        """Baut SourceRefs aus DIP-documents (überspringt Einträge ohne pdf_url, mit Warnung)."""
        refs: list[SourceRef] = []
        for doc in documents:
            fundstelle = doc.get("fundstelle", {})
            if not isinstance(fundstelle, dict):
                fundstelle = {}
            pdf_url = fundstelle.get("pdf_url", "")
            if not pdf_url:
                logger.warning("DIP document %s has no fundstelle.pdf_url, skipping", doc.get("id"))
                continue
            refs.append(
                SourceRef(
                    origin_url=str(pdf_url),
                    source_type="plenarprotokoll",
                    hint={
                        "dokumentnummer": str(doc.get("dokumentnummer", "")),
                        "datum": str(doc.get("datum", "")),
                        "wahlperiode": str(doc.get("wahlperiode", "")),
                        "dip_id": str(doc.get("id", "")),
                    },
                )
            )
        return refs

    async def fetch(self, ref: SourceRef) -> RawSource:
        """Holt die PDF-Bytes von ref.origin_url (host-pinned)."""
        parsed = urlparse(ref.origin_url)
        host = parsed.hostname or ""
        allowed_hosts = {self._api_host, self._settings.pdf_host}

        if host not in allowed_hosts:
            raise ValueError(
                f"Host '{host}' is not in the allowed set {allowed_hosts} — refusing fetch"
            )

        response = await self._client_or_create().get(ref.origin_url)

        if response.is_redirect or 300 <= response.status_code < 400:
            location = response.headers.get("location", "")
            logger.warning(
                "DIP fetch got redirect (%s) to %s for %s",
                response.status_code,
                location,
                ref.origin_url,
            )
            raise DipFetchError(f"unexpected redirect {response.status_code} for {ref.origin_url}")

        if response.status_code != 200:
            raise DipFetchError(f"unexpected status {response.status_code} for {ref.origin_url}")

        content = response.content
        if not content.startswith(b"%PDF-"):
            raise DipFetchError(f"response body is not a PDF (no %PDF- magic) for {ref.origin_url}")

        content_type = response.headers.get("content-type", "")
        if content_type and "application/pdf" not in content_type.lower():
            raise DipFetchError(f"unexpected content-type '{content_type}' for {ref.origin_url}")

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
        """Kanonischer Text → je Redebeitrag ein SpanDraft (Offset-invariant, #41).

        Der header-weite locator (protokoll/sitzung) wird PRO SPAN kopiert und um
        den Tagesordnungspunkt der jeweiligen Position ergänzt (#51) — der geteilte
        Basis-Dict wird nie mutiert.
        """
        spoken_at, base_locator = parse_header(normalized)
        return [
            SpanDraft(
                verbatim_text=seg.verbatim_text,
                text_start=seg.text_start,
                text_end=seg.text_end,
                speaker_hint={"name": seg.name, "party": seg.party},
                spoken_at=spoken_at,
                locator={**base_locator, "tagesordnungspunkt": seg.tagesordnungspunkt},
                permalink=raw.origin_url,
            )
            for seg in segment_speeches(normalized)
        ]

    async def aclose(self) -> None:
        """Schließt den internen httpx-Client, falls einer erzeugt wurde."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
