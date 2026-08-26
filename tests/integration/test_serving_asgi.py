"""Integration (Spec 0081, AC7): Read-API ueber die ASGI-Composition-Root.

Echtes Postgres (Testcontainers, ``fresh_pg_dsn`` + ``upgrade_head``); ENV zeigt
darauf. WORM-Werte sind Dummy (MinIO wird auf diesen Pfaden nicht angefasst —
der Store wird beim Bau konstruiert, aber erst ``/v1/spans/{id}/verify``
beziehen wuerde ihn). App via ``create_asgi_app()`` (kein manueller
``create_app``).
"""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from wortlaut.serving.asgi import create_asgi_app
from wortlaut.store.migrations import upgrade_head

pytestmark = pytest.mark.integration


async def test_endpoints_via_composition_root(
    fresh_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC7: ueber ``create_asgi_app()`` gebaut -> ``/readyz`` 200,
    ``/v1/search`` 200 mit ``total``, unbekannter Span -> 404."""
    await upgrade_head(fresh_pg_dsn)
    monkeypatch.setenv("WORTLAUT_DB_DSN", fresh_pg_dsn)
    monkeypatch.setenv("WORTLAUT_WORM_ENDPOINT", "minio.example.com:9000")
    monkeypatch.setenv("WORTLAUT_WORM_ACCESS_KEY", "dummy")
    monkeypatch.setenv("WORTLAUT_WORM_SECRET_KEY", "dummy")
    # MinIO wird auf diesen Pfaden NICHT angefasst (kein Call).

    app = create_asgi_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        ready = await client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json() == {"status": "ready"}

        live = await client.get("/healthz")
        assert live.status_code == 200
        assert live.json() == {"status": "ok"}

        search = await client.get("/v1/search", params={"q": "test"})
        assert search.status_code == 200
        assert "total" in search.json()

        missing = await client.get(f"/v1/spans/{uuid4()}")
        assert missing.status_code == 404
