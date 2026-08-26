"""Unit (Spec 0081, AC1): ASGI-Composition-Root — ``create_asgi_app`` verdrahtet
``create_app`` aus ENV (``WORTLAUT_DB_DSN`` / ``WORTLAUT_WORM_*``).

Keine Netz-/DB-Calls: ``create_app`` und ``MinioWormStore`` werden patcht,
``create_async_engine`` nur *gelesen* (spy). Dass beim Bau keine Verbindung
passiert, ist dadurch belegt, dass der Test ohne laufende DB/MinIO durchlaeuft
und ``create_async_engine`` genau 1x (nur zum lazy Engine-Bau) angefasst wird.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

import wortlaut.serving.asgi as asgi_mod
from wortlaut.serving.asgi import create_asgi_app
from wortlaut.store.settings import DbSettings, WormSettings


@pytest.fixture
def worm_env(monkeypatch: pytest.MonkeyPatch) -> WormSettings:
    """Pflicht-ENV: DSN + WORM-Zugangsdaten (Dummy-Werte, keine Secrets)."""
    monkeypatch.setenv("WORTLAUT_DB_DSN", "postgresql+asyncpg://user@dbhost:5432/wortlaut")
    monkeypatch.setenv("WORTLAUT_WORM_ENDPOINT", "minio.example.com:9000")
    monkeypatch.setenv("WORTLAUT_WORM_ACCESS_KEY", "ak")
    monkeypatch.setenv("WORTLAUT_WORM_SECRET_KEY", "sk")
    monkeypatch.setenv("WORTLAUT_API_CORS_ORIGINS", "https://wortlaut.io,http://localhost:8080")
    return WormSettings()


def test_factory_wires_from_env(monkeypatch: pytest.MonkeyPatch, worm_env: WormSettings) -> None:
    """AC1: ``create_app`` genau 1x, 1. Arg ein ``async_sessionmaker`` (aus
    ``DbSettings.dsn``), 2. Arg ``MinioWormStore``-Fake, der die ``WormSettings``
    (endpoint/access_key/bucket) aus der ENV festhaelt — keine hartkodierte Config."""
    from fastapi import FastAPI

    calls: list[tuple[object, object]] = []

    def fake_create_app(sessionmaker: object, worm: object, *, allowed_origins: object) -> FastAPI:
        calls.append((sessionmaker, worm))
        return FastAPI(title="fake")

    captured: dict[str, object] = {}

    def fake_worm_store(settings: object) -> object:
        captured["settings"] = settings
        return object()

    monkeypatch.setattr(asgi_mod, "create_app", fake_create_app)
    monkeypatch.setattr(asgi_mod, "MinioWormStore", fake_worm_store)

    app = create_asgi_app()

    assert isinstance(app, FastAPI)
    assert len(calls) == 1
    sessionmaker, _worm = calls[0]
    assert isinstance(sessionmaker, async_sessionmaker)
    settings = captured["settings"]
    assert isinstance(settings, WormSettings)
    assert settings == worm_env  # endpoint/access_key/secret_key/bucket/secure aus ENV


def test_factory_uses_dsn_from_env(monkeypatch: pytest.MonkeyPatch, worm_env: WormSettings) -> None:
    """AC1: die Engine wird aus dem ENV-DSN gebaut (kein zweiter Config-Kanal).
    ``create_async_engine`` wird nur *gelesen*; der an sie übergebene DSN muss
    exakt dem aus ``DbSettings.dsn`` entsprechen."""
    import wortlaut.store.db as db_mod

    sentinel: object = object()
    seen_urls: list[object] = []

    def fake_create_async_engine(url: object) -> object:
        seen_urls.append(url)
        return sentinel

    monkeypatch.setattr(db_mod, "create_async_engine", fake_create_async_engine, raising=False)
    monkeypatch.setattr(asgi_mod, "create_app", lambda sm, worm, **kw: sentinel, raising=False)
    monkeypatch.setattr(asgi_mod, "MinioWormStore", lambda s: object(), raising=False)

    assert create_asgi_app() is sentinel
    assert seen_urls == [DbSettings().dsn]
    assert DbSettings().dsn == "postgresql+asyncpg://user@dbhost:5432/wortlaut"


def test_factory_no_connection_on_build(
    monkeypatch: pytest.MonkeyPatch, worm_env: WormSettings
) -> None:
    """AC1: beim Bauen wird NICHTS verbunden — ``create_async_engine`` genau 1x
    (nur lazy Engine-Bau aus ``DbSettings.dsn``), ``create_app`` 1x, Store 1x.
    Ohne laufende DB/MinIO laeuft der Test trotzdem: die Engine ist lazy."""
    from fastapi import FastAPI

    import wortlaut.store.db as db_mod

    sentinel_engine: object = object()
    create_app_calls: list[object] = []
    store_calls: list[object] = []
    engine_urls: list[object] = []

    def fake_create_async_engine(url: object) -> object:
        engine_urls.append(url)
        return sentinel_engine

    def fake_create_app(sessionmaker: object, worm: object, *, allowed_origins: object) -> FastAPI:
        create_app_calls.append(sessionmaker)
        return FastAPI(title="fake")

    def fake_worm_store(settings: object) -> object:
        store_calls.append(settings)
        return object()

    monkeypatch.setattr(db_mod, "create_async_engine", fake_create_async_engine, raising=False)
    monkeypatch.setattr(asgi_mod, "create_app", fake_create_app)
    monkeypatch.setattr(asgi_mod, "MinioWormStore", fake_worm_store)

    create_asgi_app()

    # Genau eine Engine (lazy, nur gebaut) aus dem ENV-DSN.
    assert engine_urls == [DbSettings().dsn]
    assert len(create_app_calls) == 1
    assert isinstance(create_app_calls[0], async_sessionmaker)
    assert store_calls == [worm_env]  # WormStore aus exakt diesen WormSettings


def test_origins_durchgereicht(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC7 (Spec 0086): gesetzte ``WORTLAUT_API_CORS_ORIGINS`` -> ``create_app``
    bekommt exakt die Origins aus ``ApiSettings`` als ``allowed_origins``."""
    from fastapi import FastAPI

    monkeypatch.setenv("WORTLAUT_DB_DSN", "postgresql+asyncpg://user@dbhost:5432/wortlaut")
    monkeypatch.setenv("WORTLAUT_WORM_ENDPOINT", "minio.example.com:9000")
    monkeypatch.setenv("WORTLAUT_WORM_ACCESS_KEY", "ak")
    monkeypatch.setenv("WORTLAUT_WORM_SECRET_KEY", "sk")
    monkeypatch.setenv(
        "WORTLAUT_API_CORS_ORIGINS", "https://staging.wortlaut.io, http://localhost:8080"
    )

    captured: dict[str, Sequence[str]] = {}

    def fake_create_app(
        sessionmaker: object, worm: object, *, allowed_origins: Sequence[str]
    ) -> FastAPI:
        captured["allowed_origins"] = allowed_origins
        return FastAPI(title="fake")

    monkeypatch.setattr(asgi_mod, "create_app", fake_create_app)
    monkeypatch.setattr(asgi_mod, "MinioWormStore", lambda s: object())

    create_asgi_app()

    assert list(captured["allowed_origins"]) == [
        "https://staging.wortlaut.io",
        "http://localhost:8080",
    ]
