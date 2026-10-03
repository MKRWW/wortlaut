"""Unit (Spec 0098, AC4): der DIP-Adapter besteht das Konformitäts-Testkit — offline.

Rein: ``httpx.MockTransport`` hinter dem injizierten Client — kein Netz; die
Fixture liefert immer denselben Cursor, worauf ``discover`` stoppt.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import httpx

from wortlaut.ingest.adapter import SourceRef
from wortlaut.ingest.conformance import ConformanceSamples, check_adapter
from wortlaut.ingest.dip import DipPlenarprotokollAdapter
from wortlaut.ingest.settings import DipSettings

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "dip"


def _adapter(pdf_bytes: bytes, pdf_type: str) -> DipPlenarprotokollAdapter:
    adapter = DipPlenarprotokollAdapter(
        DipSettings(
            api_key="test-key",
            api_base_url="https://search.dip.bundestag.de/api/v1",
            pdf_host="dserver.bundestag.de",
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "search.dip.bundestag.de":
            return httpx.Response(
                200,
                content=(_FIXTURES / "discover_plenarprotokoll.json").read_bytes(),
                headers={"content-type": "application/json"},
            )
        if request.url.host == "dserver.bundestag.de":
            return httpx.Response(200, content=pdf_bytes, headers={"content-type": pdf_type})
        return httpx.Response(404)

    adapter._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    )
    return adapter


_SAMPLES = ConformanceSamples(
    since=datetime(2023, 1, 1),
    failing_ref=SourceRef("https://fremd.example/x.pdf", "plenarprotokoll", {}),
    min_spans=2,
)


async def test_dip_adapter_conforms() -> None:
    """AC4: konformer Adapter gegen die Kontext-Fixture → keine Verletzungen."""
    adapter = _adapter((_FIXTURES / "plenarprotokoll_kontext.pdf").read_bytes(), "application/pdf")
    assert await check_adapter(adapter, _SAMPLES) == []


async def test_dip_adapter_with_html_is_reported() -> None:
    """AC4: HTML statt PDF → der DIP-Adapter lehnt ab, das Kit meldet ``fetch``."""
    adapter = _adapter(b"<html>kein pdf</html>", "text/html")
    violations = await check_adapter(adapter, _SAMPLES)
    assert "fetch" in {v.check for v in violations}
