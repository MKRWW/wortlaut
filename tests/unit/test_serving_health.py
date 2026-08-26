"""Unit (Spec 0081, AC5–AC6): Health-Endpunkte der Read-API.

Keine Netz-/DB-Calls: App per ``create_app(fake_sessionmaker, fake_worm)`` bauen
und ueber ``httpx.ASGITransport`` befragen (Muster aus
``tests/integration/test_serving_api.py``, ohne Container).

``_FakeSessionmaker`` ist ein echtes ``async_sessionmaker[AsyncSession]``
(subklassiert, ``__call__`` liefert den Fake) und ``_FakeSession`` ein echtes
``AsyncSession``-Doppel, damit ``create_app``s Signatur ohne Cast/type-ignore
erfuellt ist. ``execute`` ist je Mode kontrolliert; ``close`` ist no-op (kein
echter ORM-State/``sync_session``).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast

import httpx
from sqlalchemy.engine import Result
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from wortlaut.serving.app import create_app
from wortlaut.store.worm import WormStore


class _FakeSession(AsyncSession):
    """``AsyncSession``-Doppel mit kontrollierbarer ``execute`` (kein echter DB-Zugriff).

    ``mode`` ``ok`` liefert einen Wert, ``error`` wirft ``SQLAlchemyError``,
    ``sleep`` blockiert 30 s (laenger als der 2s-Readiness-Timeout).
    """

    mode: str
    execute_calls: list[str]

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.execute_calls = []

    async def close(self) -> None:
        # no-op: der Fake haelt keinen ``sync_session`` (kein ORM-State)
        return None

    async def execute(self, statement: object, *args: Any, **kwargs: Any) -> Result[Any]:
        self.execute_calls.append(str(statement))
        if self.mode == "error":
            raise SQLAlchemyError("connection refused to internal-db:5432 (canary-1d7b)")
        if self.mode == "sleep":
            await asyncio.sleep(30)
        return cast("Result[Any]", object())


class _FakeSessionmaker(async_sessionmaker[AsyncSession]):
    """``async_sessionmaker``, der je Request einen ``_FakeSession`` liefert."""

    mode: str
    sessions: list[_FakeSession]

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.sessions = []

    def __call__(self, **local_kw: Any) -> _FakeSession:
        s = _FakeSession(self.mode)
        self.sessions.append(s)
        return s


class _FakeWorm(WormStore):
    async def ensure_bucket(self) -> None:
        raise AssertionError("wird im Health-Pfad nicht erwartet")

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        raise AssertionError("wird im Health-Pfad nicht erwartet")

    async def get(self, ref: str) -> bytes:
        raise AssertionError("wird im Health-Pfad nicht erwartet")


async def _client(mode: str) -> tuple[httpx.AsyncClient, _FakeSessionmaker]:
    sm = _FakeSessionmaker(mode)
    app = create_app(sm, _FakeWorm(), allowed_origins=["https://wortlaut.io"])
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test"), sm


async def test_healthz_ok_without_db() -> None:
    """AC5: Liveness antwortet 200 ``{"status":"ok"}``, die Session wird 0x
    benutzt (kein DB-Zugriff)."""
    client, sm = await _client("error")  # Session wirft — duerfe hier nie gebraucht werden
    async with client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert sm.sessions == []  # Session-Dependency wurde 0x benutzt


async def test_readyz_503_on_db_error() -> None:
    """AC6: DB-Fehler -> 503 ``{"detail":"not_ready"}`` (nicht ``{"status":...}``);
    der Exception-Text bleibt DRAUSSEN (Body-Inhalt prueft
    ``test_readyz_body_has_no_internals``)."""
    client, sm = await _client("error")
    async with client:
        resp = await client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json() == {"detail": "not_ready"}
    assert len(sm.sessions) == 1
    assert sm.sessions[0].execute_calls == ["SELECT 1"]


async def test_readyz_body_has_no_internals() -> None:
    """AC6: Der 503-Body enthaelt weder DSN/Host/Passwort noch den
    Exception-Text (R-SEC-01)."""
    client, _sm = await _client("error")
    async with client:
        resp = await client.get("/readyz")
    assert resp.status_code == 503
    body = resp.text
    assert "not_ready" in body
    assert "internal-db" not in body
    assert "canary-1d7b" not in body
    assert "connection refused" not in body
    assert "5432" not in body


async def test_readyz_times_out_instead_of_hanging() -> None:
    """AC6: blockierende DB (30 s) -> ``/readyz`` antwortet in < 5 s mit 503
    (Timeout greift, der Probe haengt nicht)."""
    client, sm = await _client("sleep")
    async with client:
        start = time.monotonic()
        resp = await client.get("/readyz")
        elapsed = time.monotonic() - start
    assert resp.status_code == 503
    assert resp.json() == {"detail": "not_ready"}
    assert elapsed < 5
    assert len(sm.sessions) == 1


async def test_cors_header_bei_erlaubtem_origin() -> None:
    """AC3 (Spec 0086): erlaubter ``Origin`` -> Antwort traegt den
    ``access-control-allow-origin``-Header (httpx normalisiert die Namen —
    case-insensitiv geprueft)."""
    client, _sm = await _client("ok")
    async with client:
        resp = await client.get("/healthz", headers={"Origin": "https://wortlaut.io"})
    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "https://wortlaut.io"


async def test_kein_cors_header_bei_fremdem_origin() -> None:
    """AC4 (Spec 0086): fremder ``Origin`` -> kein
    ``access-control-allow-origin``-Header in der Antwort."""
    client, _sm = await _client("ok")
    async with client:
        resp = await client.get("/healthz", headers={"Origin": "https://fremd.example"})
    assert resp.status_code == 200
    assert "access-control-allow-origin" not in resp.headers
