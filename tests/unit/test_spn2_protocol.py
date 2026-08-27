"""Unit: SPN2-Protokollschicht (Spec 0108, #108) — reines Protokoll.

Kein httpx, kein Netz, keine Fakes (R-TEST-03): Die Funktionen von
``wortlaut.archive.spn2`` bekommen bereits dekodierte Payloads. Deckt die
Fehler-Taxonomie (status_ext-Allowlist, §4.3), die Job-ID-Behandlung
(keine Formatprüfung, §0a (1)), den Stempel-Check, die Redaktion und den
User-Status-Log ab.
"""

from __future__ import annotations

import pytest

from wortlaut.archive.errors import ArchiveError
from wortlaut.archive.spn2 import (
    TRANSIENT_STATUS_EXT,
    IaCredentials,
    capture_status_from_payload,
    job_id_from_payload,
    snapshot_url,
    user_status_summary,
)


def _error_payload(status_ext: str) -> dict[str, object]:
    """Ein typischer SPN2-Fehler-Body — kommt mit HTTP 200 (§0a (2))."""
    return {
        "status": "error",
        "status_ext": status_ext,
        "message": "The same snapshot had been made 2 minutes ago.",
    }


@pytest.mark.parametrize(
    ("status_ext", "expected"),
    [
        # permanent — nicht auf der Allowlist, wird nicht wiederholt (§4.3)
        ("error:too-many-daily-captures", False),  # AC6
        ("error:brandneu-unbekannt", False),  # AC8: unbekannt ⇒ permanent
    ],
    ids=["permanent-tag-limit", "permanent-unbekannt"],
)
def test_status_ext_transienz(status_ext: str, expected: bool) -> None:
    """Fehler im Capture-Payload: der Code ist der Grund, Transienz aus
    der Allowlist (§4.3). ``message`` (Fremdtext) landet NICHT im Fehler."""
    payload = _error_payload(status_ext)
    with pytest.raises(ArchiveError) as excinfo:
        job_id_from_payload(payload)
    err = excinfo.value
    assert err.service == "wayback"
    assert err.reason == status_ext
    assert err.transient is expected


@pytest.mark.parametrize(
    "status_ext",
    [
        "error:no-browsers-available",  # AC7
        "error:too-many-requests",
        "error:bad-gateway",
        "error:browsing-timeout",
        "error:cannot-fetch",
        "error:capture-location-error",
        "error:celery",
        "error:gateway-timeout",
        "error:internal-server-error",
        "error:invalid-server-response",
        "error:job-failed",
        "error:protocol-error",
        "error:proxy-error",
        "error:read-timeout",
        "error:service-unavailable",
        "error:soft-time-limit-exceeded",
        "error:user-session-limit",
    ],
    ids=lambda s: s.removeprefix("error:"),
)
def test_status_ext_allowlist_codes_sind_transient(status_ext: str) -> None:
    """Alle 17 Allowlist-Codes sind transient (§4.3) — auch im
    Status-Abfruf-Pfad (``capture_status_from_payload``)."""
    assert status_ext in TRANSIENT_STATUS_EXT  # die Allowlist ist vollständig
    payload = _error_payload(status_ext)
    with pytest.raises(ArchiveError) as excinfo:
        capture_status_from_payload(payload)
    assert excinfo.value.reason == status_ext
    assert excinfo.value.transient is True


def test_status_ext_fehlender_wird_unknown() -> None:
    """Fehlendes ``status_ext`` → Grund ``unknown`` (permanent)."""
    with pytest.raises(ArchiveError) as excinfo:
        capture_status_from_payload({"status": "error"})
    assert excinfo.value.reason == "unknown"
    assert excinfo.value.transient is False


def test_job_id_ohne_formatpruefung() -> None:
    """§0a (1): ``job_id`` ist ``spn2-<40 hex>`` — keine UUID, keine
    Formatprüfung. Die gemessene Wirklichkeit geht durch; ein zweiter
    POST auf dieselbe URL liefert dieselbe ID (kein Sonderfall)."""
    job_id = "spn2-dea4b01e0839fb6ccf689dc93f5b2f5a4a4f8335"
    assert job_id_from_payload({"url": "https://example.com/", "job_id": job_id}) == job_id
    # Kurz, lang, anders — alles geht, solange nicht-leerer String.
    assert job_id_from_payload({"job_id": "spn2-abc"}) == "spn2-abc"
    assert job_id_from_payload({"job_id": "uuid-1234"}) == "uuid-1234"
    # Fehlend / leer / kein String → no_job_id.
    for payload in ({}, {"job_id": ""}, {"job_id": 42}):
        with pytest.raises(ArchiveError) as excinfo:
            job_id_from_payload(payload)
        assert excinfo.value.reason == "no_job_id"


def test_capture_status_success_pending_und_fehlend() -> None:
    """Success trägt Timestamp + URL; jeder andere Status (inklusive fehlendem)
    ist ``pending`` mit None-Feldern."""
    ok = capture_status_from_payload(
        {
            "status": "success",
            "timestamp": "20260827142259",
            "original_url": "https://example.com/x",
        }
    )
    assert ok.state == "success"
    assert ok.timestamp == "20260827142259"
    assert ok.original_url == "https://example.com/x"

    for payload in (
        {"status": "pending"},
        {},
        {"status": "irgendwas"},
    ):
        state = capture_status_from_payload(payload)
        assert state.state == "pending"
        assert state.timestamp is None
        assert state.original_url is None

    # Success OHNE eines der beiden Felder → no_snapshot_url.
    for payload in (
        {"status": "success", "original_url": "https://example.com/x"},
        {"status": "success", "timestamp": "20260827142259"},
    ):
        with pytest.raises(ArchiveError) as excinfo:
            capture_status_from_payload(payload)
        assert excinfo.value.reason == "no_snapshot_url"


def test_snapshot_url_baut_exakte_url() -> None:
    """14-stelliger Stempel → exakt die in §0a gemessene Form."""
    url = snapshot_url("20260827142259", "https://dserver.bundestag.de/btp/21/21089.pdf")
    assert (
        url
        == "https://web.archive.org/web/20260827142259/https://dserver.bundestag.de/btp/21/21089.pdf"
    )


def test_ungueltiger_zeitstempel_wirft() -> None:
    """AC11: kein 14-stelliger Stempel → ``invalid_snapshot_url``, keine
    URL zurück — auch nicht teilweise/angrenzend (``fullmatch``)."""
    for ts in ("2026", "202608271422591", "2026082714225", "20260827a42259", ""):
        with pytest.raises(ArchiveError) as excinfo:
            snapshot_url(ts, "https://example.com/x")
        assert excinfo.value.reason == "invalid_snapshot_url"


def test_zugangsdaten_nicht_in_repr_und_str() -> None:
    """AC12: ``repr`` UND ``str`` von ``IaCredentials`` redigieren beide
    Felder (R-SEC-01). Kein ``__str__`` → Python fällt auf ``__repr__``
    zurück; beide Ausdrücke müssen sauber sein."""
    # Zusammengesetzt statt literal (S6698: keine ausschreibenden
    # Zugangsdaten-artigen Literale in Tests).
    access = "A" * 16
    secret = "S" * 16
    creds = IaCredentials(access_key=access, secret=secret)
    for rendered in (repr(creds), str(creds)):
        assert access not in rendered
        assert secret not in rendered
    assert repr(creds) == "IaCredentials(access_key=<redacted>, secret=<redacted>)"
    assert creds.authorization_header() == f"LOW {access}:{secret}"


def test_user_status_zusammenfassung() -> None:
    """AC14 (Teil): die Log-Zeile trägt alle vier Felder, inkl. des
    Limits — und bewertet nichts (§0b)."""
    line = user_status_summary(
        {
            "processing": 0,
            "available": 3,
            "daily_captures": 0,
            "daily_captures_limit": 30000,
        }
    )
    assert line == "available=3 processing=0 daily_captures=0/30000"
    # Fehlende Felder → „?", keine Exception.
    assert user_status_summary({}) == "available=? processing=? daily_captures=?/?"
