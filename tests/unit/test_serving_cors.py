"""Unit (Spec 0086, AC1/AC2/AC5/AC8): CORS-Origins-Settings aus der Umgebung.

``ApiSettings`` wird direkt gegen ``monkeypatch.setenv`` bzw. ``delenv`` geprüft —
keine App, kein DB-/Netz-Zugriff. Die fünf Fehlformen von AC5 werfen je eine
``ValidationError``: eine falsch gesetzte Variable bricht den Start ab (Exit 2 im
``cli._run_serve``-Pfad), statt still eine Allowlist zu bauen, die nichts matcht.

AC8 (nur ``GET``, kein ``allow_credentials``) wird über die *verhaltensseitig*
prüfbare CORS-Middleware getestet (Preflight-``OPTIONS`` + normaler ``GET`` via
``ASGITransport``) — bewusst nicht über die Starlette-Interna ``allow_methods``/
``allow_credentials``, die keine stabile API sind. Fake-Infrastruktur wie in
``tests/unit/test_serving_health.py`` (keine neue Doppel-Infrastruktur).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from wortlaut.serving.app import create_app
from wortlaut.serving.settings import ApiSettings
from wortlaut.store.worm import WormStore

_CORIG = "WORTLAUT_API_CORS_ORIGINS"


def test_default_ohne_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC1: ungesetzte Variable -> Produktion-Default, unverändertes Verhalten."""
    monkeypatch.delenv(_CORIG, raising=False)
    assert ApiSettings().cors_origins == ["https://wortlaut.io"]


def test_kommagetrennt_mit_leerraum(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC2: zwei kommagetrennte Origins mit Leerraum -> beide getrimmt."""
    monkeypatch.setenv(_CORIG, "https://wortlaut.io, http://localhost:8080")
    assert ApiSettings().cors_origins == ["https://wortlaut.io", "http://localhost:8080"]


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("", "leer"),
        (" , , ", "nur Kommas"),
        ("wortlaut.io", "ohne Schema"),
        ("https://wortlaut.io/", "Trailing Slash"),
        ('["https://a"]', "JSON-Form"),
    ],
)
def test_ungueltige_env_wirft(monkeypatch: pytest.MonkeyPatch, value: str, reason: str) -> None:
    """AC5: jede der fünf Fehlformen wirft eine ``ValidationError`` (-> Exit 2).

    Die JSON-Form wird bewusst abgelehnt statt still zerlegt (Abschnitt 4.1):
    ohne Prüfung ergäbe sie eine Allowlist, die nichts matcht.
    """
    monkeypatch.setenv(_CORIG, value)
    with pytest.raises(ValidationError):
        ApiSettings()


class _FakeSession(AsyncSession):
    """``AsyncSession``-Doppel, dessen ``__call__``-Träger keine DB anfassen darf
    (AC8 prüft die CORS-Middleware, nicht die Routen) — kopiert aus
    ``test_serving_health.py``."""

    def __init__(self) -> None:
        return

    async def close(self) -> None:
        return None


class _FakeSessionmaker(async_sessionmaker[AsyncSession]):
    def __call__(self, **local_kw: Any) -> _FakeSession:
        return _FakeSession()


class _FakeWorm(WormStore):
    async def ensure_bucket(self) -> None:
        raise AssertionError("wird im CORS-Pfad nicht erwartet")

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        raise AssertionError("wird im CORS-Pfad nicht erwartet")

    async def get(self, ref: str) -> bytes:
        raise AssertionError("wird im CORS-Pfad nicht erwartet")


async def _client(origins: list[str] | None = None) -> httpx.AsyncClient:
    app = create_app(
        _FakeSessionmaker(), _FakeWorm(), allowed_origins=origins or ["https://wortlaut.io"]
    )
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_nicht_default_origin_wird_wirklich_benutzt() -> None:
    """AC3b: ``create_app`` **benutzt** ``allowed_origins`` — es reicht nicht, sie
    entgegenzunehmen.

    Der Test baut die App bewusst mit einem Origin, der **nicht** der frueher
    hartkodierte ist, und prueft beide Richtungen. Ein Rueckfall auf
    ``allow_origins=["https://wortlaut.io"]`` in ``create_app`` faellt hier sofort
    auf — ein Test, der nur den Default verwendet, kann 'durchgereicht' nicht von
    'hartkodiert' unterscheiden (im Mutationstest belegt).
    """
    client = await _client(["http://localhost:8080"])
    async with client:
        erlaubt = await client.get("/healthz", headers={"Origin": "http://localhost:8080"})
        frueherer_default = await client.get("/healthz", headers={"Origin": "https://wortlaut.io"})

    assert erlaubt.headers["access-control-allow-origin"] == "http://localhost:8080"
    assert "access-control-allow-origin" not in frueherer_default.headers


async def test_methods_bleiben_get_only() -> None:
    """AC8: die CORS-Middleware erlaubt ausschließlich ``GET`` (kein Schreibpfad, §2).

    Die Ablehnung steckt im **Status**, nicht in einem fehlenden Header: Starlette
    beantwortet einen Preflight fuer eine unerlaubte Methode mit ``400`` und sendet
    dabei weiterhin ``access-control-allow-methods`` — es wirbt mit dem, was erlaubt
    *ist*. Geprueft wird deshalb beides: der abgelehnte Status **und** dass die
    beworbene Methodenliste genau ``GET`` ist.
    """
    client = await _client()
    async with client:
        preflight_post = await client.options(
            "/healthz",
            headers={
                "Origin": "https://wortlaut.io",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert preflight_post.status_code == 400
        assert preflight_post.headers["access-control-allow-methods"] == "GET"

        allowed_preflight = await client.options(
            "/healthz",
            headers={
                "Origin": "https://wortlaut.io",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert allowed_preflight.status_code == 200
        assert allowed_preflight.headers["access-control-allow-methods"] == "GET"


async def test_kein_allow_credentials() -> None:
    """AC8: die API kennt keine Cookies/Sessions — ein erlaubter ``GET`` trägt
    keinen ``access-control-allow-credentials``-Header (§2: kein
    ``allow_credentials``)."""
    client = await _client()
    async with client:
        resp = await client.get("/healthz", headers={"Origin": "https://wortlaut.io"})
    assert resp.status_code == 200
    assert "access-control-allow-credentials" not in resp.headers
