"""Unit (Spec 0145, AC10): Konformität des Landtag-Sachsen-Anhalt-Adapters.

Offline verdrahtet: ``httpx.MockTransport`` hinter dem injizierten Client
(Aufbau wie ``test_landtag_st_adapter.py``), Fixture-PDF relativ zu ``__file__``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

from wortlaut.ingest.adapter import SourceRef
from wortlaut.ingest.conformance import ConformanceSamples, assert_conformant
from wortlaut.ingest.landtag_st import LandtagSachsenAnhaltAdapter
from wortlaut.ingest.settings import LandtagStSettings

_Handler = Callable[[httpx.Request], httpx.Response]

_BASE = "https://padoka.landtag.sachsen-anhalt.de/files/plenum"
_PDF = Path(__file__).resolve().parents[1] / "fixtures" / "landtag_st" / "protokoll.pdf"


def _handler(request: httpx.Request) -> httpx.Response:
    """HEAD wp8/001–003 → 200, sonst 404; GET der drei URLs → Bytes der Fixture-PDF."""
    parts = request.url.path.split("/")
    wp = int(parts[-2].removeprefix("wp"))
    number = int(parts[-1][:3])
    if request.method == "GET":
        if wp == 8 and 1 <= number <= 3:
            return httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=_PDF.read_bytes(),
            )
        return httpx.Response(404)
    if wp == 8 and 1 <= number <= 3:
        return httpx.Response(200)
    return httpx.Response(404)


async def test_conformance_landtag_st() -> None:  # AC10
    settings = LandtagStSettings(
        enabled=True,
        base_url=_BASE,
        wahlperiode=8,
        lookback=3,
        contact="https://github.com/MKRWW/wortlaut",
    )
    adapter = LandtagSachsenAnhaltAdapter(settings)
    adapter._client = adapter._new_client(transport=httpx.MockTransport(_handler))

    samples = ConformanceSamples(
        since=datetime(2026, 1, 1, tzinfo=UTC),
        failing_ref=SourceRef("https://fremd.example/x.pdf", "plenarprotokoll", {}),
        min_spans=3,
    )
    await assert_conformant(adapter, samples)
