"""Live: archive_all gegen echte Dienste (AC8).

Nur manuell via `pytest -m live`; in CI deselektiert.
"""

from __future__ import annotations

import pytest

from wortlaut.archive.archiver import ArchiveTodayArchiver, WaybackArchiver, archive_all

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_archive_all_live_real_snapshot() -> None:
    """Echt: archive_all gegen https://example.com/ → >=1 Snapshot-URL nicht None.

    Hinweis: archive.today ist bot-hostil (Captcha/Rate-Limit);
    nur Wayback muss reliably funktionieren. Testet nur >=1 Erfolg.
    """
    wayback = WaybackArchiver()
    atoday = ArchiveTodayArchiver(retry_delay=0.0)

    try:
        result = await archive_all(
            "https://example.com/",
            wayback=wayback,
            archive_today=atoday,
        )
    finally:
        await wayback.aclose()
        await atoday.aclose()

    # Mindestens einer der Dienste liefert eine Snapshot-URL
    assert (result.wayback_url is not None) or (result.archive_today_url is not None), (
        f"no snapshot url from either service; errors: {result.errors}"
    )
