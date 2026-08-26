"""TSA-Client: stempelt Rohbytes und verifiziert das Token SOFORT vor der Rückgabe.

Eine gefälschte oder nicht bindende TSA-Antwort wird damit **nie persistiert**
(Spec 0076 §4.3, 🔴 adversariale Kern): ``Rfc3161Tsa.stamp`` liefert ein Token
nur zurück, wenn es vorher vollständig verifiziert wurde — und zwar mit derselben
:func:`wortlaut.timestamp.verify.verify_token` wie der spätere Verify-Pfad (doppelte
Prüfung, an beiden Enden).

``FallbackTimeStamper`` probiert die TSAs der Reihe nach; erst wenn ALLE scheitern,
fliegt der letzte Fehler (Spec 0076 §4.7: eine TSA, ein Versuch — der Pass ist der
Retry, es gibt keine zweite Retry-Maschinerie).

Nur stdlib + httpx + cryptography + rfc3161_client; kein pydantic (R-ARCH-02),
kein anderer wortlaut-Layer (AC19). Die TSA-URLs sind Konstanten aus dem Profil
(§4.6/§0c), keine Fremdeingabe.
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import httpx
from rfc3161_client import HashAlgorithm, TimestampRequestBuilder

from wortlaut.timestamp.errors import TimestampError
from wortlaut.timestamp.profiles import TsaProfile
from wortlaut.timestamp.verify import verify_token

# Max. Body-Größe eines legitimen Tokens liegt bei 1–7 KiB (§4.5); 64 KiB Deckel.
_MAX_BODY_BYTES = 65536
_EXPECTED_CONTENT_TYPE = "application/timestamp-reply"


@dataclass(frozen=True)
class StampResult:
    """Erfolgreich gestempelte Quelle: Name, volles Token-DER, gen_time aus dem Token."""

    tsa_name: str
    token_der: bytes  # volle TimeStampResp-DER (resp.as_bytes()), wie empfangen
    gen_time: datetime


class TimeStamper(Protocol):
    """Öffentliche Naht (R-ARCH-01) — eine TSA oder eine Kette davon."""

    async def stamp(self, raw: bytes, *, content_hash: str) -> StampResult: ...


class Rfc3161Tsa:
    """Eine TSA: POST, Antwort-Härtung, SOFORTIGE Verifikation vor der Rückgabe."""

    def __init__(self, profile: TsaProfile, *, timeout_seconds: float = 10.0) -> None:
        self._profile = profile
        self._timeout_seconds = timeout_seconds
        self._client: httpx.AsyncClient | None = None

    def _client_or_create(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_seconds, follow_redirects=False)
        return self._client

    async def stamp(self, raw: bytes, *, content_hash: str) -> StampResult:
        """POST-Timestamp-Query, Härtung, Sofort-Verifikation; ``StampResult`` nur wenn ok.

        Gestempelt werden die **Rohbytes** (§4.1: der Imprint im Token ist dann
        byte-identisch mit ``content_hash``) — niemals der Hex-String.
        """
        name = self._profile.name
        req = (
            TimestampRequestBuilder()
            .data(raw)
            .hash_algorithm(HashAlgorithm.SHA256)
            .nonce(nonce=True)
            .cert_request(cert_request=True)
            .build()
        )
        body = req.as_bytes()
        client = self._client_or_create()
        try:
            response = await client.post(
                self._profile.url,
                content=body,
                headers={"Content-Type": "application/timestamp-query"},
            )
        except httpx.TimeoutException as exc:
            raise TimestampError(name, "timeout") from exc
        except httpx.TransportError as exc:
            raise TimestampError(name, "transport") from exc

        # Antwort-Härtung (§4.5) in fester Reihenfolge — Statuscode zuerst.
        if not 200 <= response.status_code < 300:
            raise TimestampError(name, "http_status", status_code=response.status_code)
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type != _EXPECTED_CONTENT_TYPE:
            raise TimestampError(name, "content_type")
        if len(response.content) > _MAX_BODY_BYTES:
            raise TimestampError(name, "oversize")

        # SOFORTIGE Verifikation mit derselben Funktion wie beim späteren Verify (§4.3).
        verdict = verify_token(response.content, content_hash=content_hash, tsa_name=name)
        if verdict.status != "ok":
            raise TimestampError(name, verdict.status)

        # Gespeichert wird ``response.content`` (volle TimeStampResp-DER, wie
        # empfangen) — exakt das, was ``decode_timestamp_response`` später wieder liest.
        gen_time = verdict.gen_time
        if gen_time is None:  # pragma: no cover — status „ok" trägt immer ein gen_time
            raise TimestampError(name, "malformed")
        return StampResult(name, response.content, gen_time)

    async def aclose(self) -> None:
        """Schließt den internen httpx-Client, falls einer erzeugt wurde."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class FallbackTimeStamper:
    """Probiert die TSAs der Reihe nach; erst wenn ALLE scheitern, fliegt der letzte Fehler."""

    def __init__(self, stampers: Sequence[Rfc3161Tsa]) -> None:
        # Leere Kette ist Fehlkonfiguration (z.B. WORTLAUT_TSA_PROFILES=""), kein
        # Laufzeit-Zustand: hier hart scheitern, damit der Composition-Root sie als
        # Konfigurationsfehler (Exit 2) meldet statt spaeter je Quelle zu stolpern.
        if not stampers:
            raise ValueError("FallbackTimeStamper braucht mindestens eine TSA")
        self._stampers = list(stampers)
        self.failures: tuple[str, ...] = ()  # label()s des letzten stamp-Aufrufs

    async def stamp(self, raw: bytes, *, content_hash: str) -> StampResult:
        self.failures = ()  # bei jedem Aufruf neu — kein akkumulierender Zustand
        collected: list[str] = []
        last: TimestampError | None = None
        for stamper in self._stampers:
            try:
                result = await stamper.stamp(raw, content_hash=content_hash)
                self.failures = tuple(collected)  # Labels der vorher gescheiterten TSAs
                return result
            except TimestampError as exc:
                last = exc
                collected.append(exc.label())
        self.failures = tuple(collected)
        if last is None:  # pragma: no cover — __init__ garantiert >= 1 Stamper
            raise TimestampError("fallback", "unexpected")
        raise last

    async def aclose(self) -> None:
        """Schließt alle inneren Stamper einzeln; ein Fehler darf die anderen nicht stoppen."""
        for stamper in self._stampers:
            with contextlib.suppress(Exception):
                await stamper.aclose()
