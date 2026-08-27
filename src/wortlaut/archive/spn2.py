"""SPN2-API-Protokollschicht (Save Page Now 2, #108) — reines Protokoll, nur stdlib.

Kein httpx, kein ``await``: Die Funktionen hier bekommen bereits dekodierte
JSON-Payloads (``Mapping[str, object]``) und liefern Werte oder werfen
``ArchiveError``. Damit bleibt die gesamte Fehler-Taxonomie ohne Netz und
ohne Mock-Client testbar; den HTTP-Verkehr, das Polling und den
Client-Besitz behält ``archiver.py`` (Spec 0108 §4.1).

Gemessene SPN2-Eigenheiten (Spec 0108 §0a):
  * ``job_id`` ist ``spn2-<40 hex>``, deterministisch pro URL — **keine**
    Formatprüfung (eine UUID-Prüfung wäre sofort rot).
  * Fehler kommen mit HTTP 200 — als ``{"status": "error", "status_ext": …}``
    schon im Capture-Request und im Status-Abruf.
  * Frische Snapshots sind nicht sofort auflösbar (CDX-Index) — die
    Snapshot-URL wird deshalb **nie** im Ingest-Pfad gegengeprüft.
  * ``available``/``daily_captures`` im User-Status werden nur geloggt,
    nie bewertet (§0b).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from wortlaut.archive.errors import ArchiveError

# ── Endpunkte (alle auf web.archive.org — ein einziger gepinnter Transport, #36) ──

SPN2_SAVE_URL = "https://web.archive.org/save"
SPN2_STATUS_URL = "https://web.archive.org/save/status/"
SPN2_USER_STATUS_URL = "https://web.archive.org/save/status/user"

_SNAPSHOT_BASE = "https://web.archive.org/web/"
_TIMESTAMP_RE = re.compile(r"\d{14}")

# ── Transienz (Spec 0108 §4.3) ───────────────────────────────────────────

# Allowlist: nur diese status_ext-Codes sind transient. Alles andere — auch
# unbekannte, künftig hinzukommende Codes — ist permanent und wird nicht
# wiederholt: Ein fälschlich permanenter Fehler kostet einen
# archive_failed-Outcome, den ein späterer Lauf nachholt; ein fälschlich
# transienter lässt uns gegen einen Dienst hämmern, der uns gerade gesagt
# hat, dass es keinen Zweck hat.
TRANSIENT_STATUS_EXT: frozenset[str] = frozenset(
    {
        "error:bad-gateway",
        "error:browsing-timeout",
        "error:cannot-fetch",
        "error:capture-location-error",
        "error:celery",
        "error:gateway-timeout",
        "error:internal-server-error",
        "error:invalid-server-response",
        "error:job-failed",
        "error:no-browsers-available",
        "error:protocol-error",
        "error:proxy-error",
        "error:read-timeout",
        "error:service-unavailable",
        "error:soft-time-limit-exceeded",
        "error:too-many-requests",
        "error:user-session-limit",
    }
)


# ── Zugangsdaten ─────────────────────────────────────────────────────────


@dataclass(frozen=True, repr=False)
class IaCredentials:
    """Internet-Archive-Zugangsdaten — werden nie dargestellt (R-SEC-01).

    ``repr``/``str`` redigieren beide Felder; einzige Wert-Operation ist
    ``authorization_header`` (baut den Header — der wird nie geloggt).
    Bewusst kein ``__str__``: Python fällt auf ``__repr__`` zurück.
    """

    access_key: str
    secret: str

    def __repr__(self) -> str:
        return "IaCredentials(access_key=<redacted>, secret=<redacted>)"

    def authorization_header(self) -> str:
        """``LOW <access_key>:<secret>`` — die einzige Form des Authorization-Headers."""
        return f"LOW {self.access_key}:{self.secret}"


# ── Statuswerte ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CaptureStatus:
    """Dekodierter SPN2-Status — reine Daten, keine Bewertung (die bleibt
    Sache von ``archiver.py``)."""

    state: Literal["pending", "success"]
    timestamp: str | None
    original_url: str | None


# ── Payload → Wert / Fehler ──────────────────────────────────────────────


def _error_from_payload(payload: Mapping[str, object]) -> ArchiveError:
    """``ArchiveError`` aus einem ``status: error``-Payload.

    Der **Grund ist der Code selbst** (``status_ext``, fehlt es: ``"unknown"``) —
    damit trägt ``ArchiveError.label()`` ihn bis in die Summary. Das Feld
    ``message`` wird bewusst **nicht** übernommen: Es ist Fremdtext unbekannter
    Länge und würde in aggregierten Labels landen.
    """
    status_ext: object = payload.get("status_ext", "unknown")
    code = status_ext if isinstance(status_ext, str) else "unknown"
    return ArchiveError("wayback", code, transient=code in TRANSIENT_STATUS_EXT)


def job_id_from_payload(payload: Mapping[str, object]) -> str:
    """Job-ID aus einer Capture-Antwort.

    ``status: error`` → ``ArchiveError`` mit dem status_ext-Code (§0a (2):
    Fehler kommen mit HTTP 200). Sonst muss ``job_id`` als nicht-leerer String
    existieren, sonst ``no_job_id``. **Keine** Formatprüfung: ``spn2-<40 hex>``
    ist keine UUID (§0a (1)).
    """
    if payload.get("status") == "error":
        raise _error_from_payload(payload)
    job_id: object = payload.get("job_id")
    if not isinstance(job_id, str) or not job_id:
        raise ArchiveError("wayback", "no_job_id")
    return job_id


def capture_status_from_payload(payload: Mapping[str, object]) -> CaptureStatus:
    """Status-Antwort dekodieren: ``success`` (mit Snapshot-Daten) oder ``pending``.

    ``status: error`` → ``ArchiveError`` mit dem status_ext-Code. Bei
    ``success`` müssen ``timestamp`` und ``original_url`` beide vorhanden sein,
    sonst ``no_snapshot_url``. Jeder andere Wert (inklusive ``"pending"`` und
    fehlendem ``status``) → ``pending``.
    """
    if payload.get("status") == "error":
        raise _error_from_payload(payload)
    if payload.get("status") == "success":
        timestamp: object = payload.get("timestamp")
        original_url: object = payload.get("original_url")
        if not isinstance(timestamp, str) or not isinstance(original_url, str):
            raise ArchiveError("wayback", "no_snapshot_url")
        return CaptureStatus("success", timestamp, original_url)
    return CaptureStatus("pending", None, None)


def snapshot_url(timestamp: str, original_url: str) -> str:
    """Snapshot-URL aus dem 14-stelligen Stempel der Erfolgsantwort.

    Die URL wird **nicht** nachträglich gegengeprüft (kein HEAD/GET, kein
    CDX): Bis der CDX-Index nachzieht, löst sie auf den nächstgelegenen
    älteren Snapshot auf (Spec 0108 §0a (3)) — ein Auflösungs-Gate wäre für
    jeden frischen Capture falsch rot. Die zeitgleiche Drittbezeugung des
    Hashes leistet der RFC-3161-Zeitstempel (#76), nicht der Archiv-Index.
    """
    if _TIMESTAMP_RE.fullmatch(timestamp) is None:
        raise ArchiveError("wayback", "invalid_snapshot_url")
    return f"{_SNAPSHOT_BASE}{timestamp}/{original_url}"


def user_status_summary(payload: Mapping[str, object]) -> str:
    """User-Status-Felder als Log-Zeile: ``available=… processing=…
    daily_captures=…/…``.

    Reines Logging, **keine** Bewertung: ``available == 0`` ist ein
    Sekundenzustand; daraus einen Abbruch zu machen, würde den Falsch-Rot-
    Modus durch die Hintertür wieder einbauen (§0b). Fehlende Felder werden
    als ``?`` gerendert.
    """
    available = "?" if payload.get("available") is None else str(payload.get("available"))
    processing = "?" if payload.get("processing") is None else str(payload.get("processing"))
    daily_captures = (
        "?" if payload.get("daily_captures") is None else str(payload.get("daily_captures"))
    )
    limit = (
        "?"
        if payload.get("daily_captures_limit") is None
        else str(payload.get("daily_captures_limit"))
    )
    return f"available={available} processing={processing} daily_captures={daily_captures}/{limit}"
