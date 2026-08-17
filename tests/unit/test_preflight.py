"""Unit-Tests für die Pre-Flight-Archiv-Health-Check (Spec 0077, #77).

Rein: Archiver als Fake, keine Netz-Calls (R-TEST-03). Deckt die Neutrale-URL-
Eigenschaft (AC3) und die unveränderte Fehler-Propagation (AC1, Einheit) ab.
"""

from __future__ import annotations

import pytest

from wortlaut.archive.errors import ArchiveError
from wortlaut.archive.preflight import PROBE_URL, probe_archive


class RecordingArchiver:
    """Archiver-Fake, der jeden ``archive``-Aufruf (URL) aufzeichnet."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.result: str | None = "https://web.archive.org/snapshot/xyz"
        self.error: ArchiveError | None = None

    async def archive(self, origin_url: str) -> str:
        self.calls.append(origin_url)
        if self.error is not None:
            raise self.error
        if self.result is None:
            raise AssertionError("Archiver ohne Ergebnis und ohne Fehler konfiguriert")
        return self.result


async def test_probe_returns_snapshot_url() -> None:
    """AC3: Der Probe ruft ``archive`` genau 1× mit exakt ``settings.preflight_url``
    auf und liefert die Snapshot-URL weiter. ``settings.preflight_url`` ist
    Default = ``PROBE_URL`` — eine konstante, neutrale Domain, die NICHT eine
    der zu ingestierenden ``origin_url``s ist."""
    archiver = RecordingArchiver()
    # settings.preflight_url hat als Default PROBE_URL (settings.py).
    snapshot = await probe_archive(archiver, probe_url=PROBE_URL)

    assert archiver.calls == [PROBE_URL]  # genau 1×, exakt die konfigurierte URL
    assert snapshot == "https://web.archive.org/snapshot/xyz"
    # Neutralität: die Probe-URL ist keine echte Quelle, keine unserer Quellen.
    assert PROBE_URL not in ("http://a/p1.pdf", "https://dserver.bundestag.de/x.pdf")


async def test_probe_uses_custom_preflight_url() -> None:
    """AC3 (überschreibbar): ``probe_url`` wird 1:1 an ``archive`` durchgereicht."""
    archiver = RecordingArchiver()
    await probe_archive(archiver, probe_url="https://custom.example/")
    assert archiver.calls == ["https://custom.example/"]


async def test_probe_propagates_archive_error() -> None:
    """AC1 (Einheit): ``probe_archive`` fängt nichts — der ``ArchiveError`` des
    Archivers (mit Grund + Statuscode) fliegt unverändert nach oben."""
    archiver = RecordingArchiver()
    archiver.error = ArchiveError("wayback", "http_status", status_code=503, transient=True)

    with pytest.raises(ArchiveError) as excinfo:
        await probe_archive(archiver, probe_url=PROBE_URL)

    err = excinfo.value
    assert err.service == "wayback"
    assert err.reason == "http_status"
    assert err.status_code == 503
    assert str(err) == "wayback: http_status 503 (transient)"
    assert archiver.calls == [PROBE_URL]  # der Call wurde abgesetzt, dann geworfen
