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
from wortlaut.ingest.settings import DipSettings

logger = logging.getLogger(__name__)


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
        """DIP-Metadaten-Endpoint → je Protokoll ein SourceRef mit dem PDF-URL."""
        url = f"{self._settings.api_base_url}/plenarprotokoll"
        params = {
            "apikey": self._settings.api_key,
            "f.datum.start": since.strftime("%Y-%m-%d"),
            "f.zuordnung": "BT",
            "format": "json",
        }
        response = await self._client_or_create().get(url, params=params)
        response.raise_for_status()
        data = response.json()

        refs: list[SourceRef] = []
        for doc in data.get("documents", []):
            fundstelle = doc.get("fundstelle", {})
            pdf_url = fundstelle.get("pdf_url", "")
            if not pdf_url:
                logger.warning("DIP document %s has no fundstelle.pdf_url, skipping", doc.get("id"))
                continue
            refs.append(
                SourceRef(
                    origin_url=pdf_url,
                    source_type="plenarprotokoll",
                    hint={
                        "dokumentnummer": doc.get("dokumentnummer", ""),
                        "datum": doc.get("datum", ""),
                        "wahlperiode": doc.get("wahlperiode", ""),
                        "dip_id": doc.get("id", ""),
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
        """PDF→Text ist Phase 1."""
        raise NotImplementedError("PDF→Text ist Phase 1")

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        """Parse-to-span ist Phase 1."""
        raise NotImplementedError("parse-to-span ist Phase 1")

    async def aclose(self) -> None:
        """Schließt den internen httpx-Client, falls einer erzeugt wurde."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
