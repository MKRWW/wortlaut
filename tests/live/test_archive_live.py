"""Live: Wayback (SPN2) gegen den echten Dienst (AC1, #108).

Nur manuell via `pytest -m live`; in CI deselektiert. Benötigt echte
Internet-Archive-S3-Schlüssel in der Umgebung (`WORTLAUT_ARCHIVE_IA_ACCESS_KEY`
/ `WORTLAUT_ARCHIVE_IA_SECRET`) — ohne sie wird der Test übersprungen
(SPN2 lehnt anonyme Aufrufe mit 401 ab, §0). Die Werte kommen über die
Settings-Klasse rein (SecretStr), nie als Klartext im Code.

Ziel-URL ist `https://dserver.bundestag.de/btp/21/21089.pdf` (gemessen in
§0a): `https://example.com/` schlägt seit dem Tageslimit (5 Captures/URL/Tag,
global) systematisch fehl und wäre eine permanente Falsch-Rot-Quelle.
"""

from __future__ import annotations

import pytest

from wortlaut.archive.archiver import WaybackArchiver
from wortlaut.archive.settings import ArchiveSettings
from wortlaut.archive.spn2 import IaCredentials

pytestmark = pytest.mark.live

TARGET_URL = "https://dserver.bundestag.de/btp/21/21089.pdf"


def _credentials() -> IaCredentials | None:
    """Zugangsdaten aus der Umgebung (via SecretStr); ``None`` wenn nicht beide
    gesetzt — der Test springt dann über (AC14/§0b: keine Bewertung, nur
    Vorhandensein)."""
    settings = ArchiveSettings()
    if settings.ia_access_key is None or settings.ia_secret is None:
        return None
    return IaCredentials(
        access_key=settings.ia_access_key.get_secret_value(),
        secret=settings.ia_secret.get_secret_value(),
    )


@pytest.mark.asyncio
async def test_archive_live_real_snapshot() -> None:
    """Echt: SPN2-Capture von `TARGET_URL` → exakte Snapshot-URL
    (`/web/<14stelliger-Stempel>/<original_url>`), gebaut aus der
    Erfolgsantwort — nicht aus einem Header."""
    credentials = _credentials()
    if credentials is None:
        pytest.skip(
            "keine Internet-Archive-Zugangsdaten in der Umgebung "
            "(WORTLAUT_ARCHIVE_IA_ACCESS_KEY / WORTLAUT_ARCHIVE_IA_SECRET)"
        )

    wayback = WaybackArchiver(credentials=credentials)
    try:
        wayback_url = await wayback.archive(TARGET_URL)
    finally:
        await wayback.aclose()

    assert wayback_url.startswith("https://web.archive.org/web/")
    assert wayback_url.endswith(TARGET_URL)
