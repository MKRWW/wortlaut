"""Unit: Pre-Flight-Archiv-Health-Check (Spec 0077, überarbeitet in #108).

Rein: Archiver als Fake (User-Status statt Capture-Probe), keine Netz-Calls
(R-TEST-03). Deckt das neue Messobjekt ab: ``probe_archive`` ruft genau
``user_status`` einmal ab, bewertet nichts (§0b), und der ``ArchiveError``
des Archivers fliegt unverändert weiter.
"""

from __future__ import annotations

import pytest

from wortlaut.archive.errors import ArchiveError
from wortlaut.archive.preflight import probe_archive


class UserStatusHealth:
    """Archiver-Fake, der nur ``user_status`` kennt — die neue Probe (AC14/AC15).

    ``user_status`` liefert die Log-Zeile oder wirft; Capture-Aufrufe werden
    gezählt, damit belegt ist, dass die Probe KEINEN Capture absetzt.
    """

    def __init__(self) -> None:
        self.user_status_calls = 0
        self.archive_calls = 0
        self.summary: str = "available=3 processing=0 daily_captures=0/30000"
        self.error: ArchiveError | None = None

    async def user_status(self) -> str:
        self.user_status_calls += 1
        if self.error is not None:
            raise self.error
        return self.summary

    async def archive(self, origin_url: str) -> str:
        # Bewusst vorhanden (der Archiver kann beides) — die Probe darf es
        # NICHT aufrufen.
        self.archive_calls += 1
        raise AssertionError("die Pre-Flight-Probe darf keinen Capture absetzen")


async def test_probe_liest_user_status() -> None:
    """AC14: ``probe_archive`` liefert die User-Status-Zusammenfassung
    (available=3, daily_captures=0/30000) weiter — und es wird KEIN Capture
    abgesetzt. Der User-Status-Call geht genau einmal raus."""
    health = UserStatusHealth()

    result = await probe_archive(health)

    assert result == "available=3 processing=0 daily_captures=0/30000"
    assert health.user_status_calls == 1
    assert health.archive_calls == 0  # kein Capture, kein Capture-Kontingent


async def test_probe_401_wirft() -> None:
    """AC15: ungültige Zugangsdaten (401) → ``ArchiveError`` mit
    ``reason == "unauthorized"`` fliegt unverändert nach oben — der Pre-Flight
    ist damit exakt der Check, der kaputte Zugangsdaten VOR dem Lauf fängt."""
    health = UserStatusHealth()
    health.error = ArchiveError("wayback", "unauthorized", status_code=401, transient=False)

    with pytest.raises(ArchiveError) as excinfo:
        await probe_archive(health)

    err = excinfo.value
    assert err.service == "wayback"
    assert err.reason == "unauthorized"
    assert err.status_code == 401
    assert err.transient is False
    assert health.user_status_calls == 1  # der Call wurde abgesetzt, dann geworfen
