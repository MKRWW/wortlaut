"""Unit (Spec 0081, AC2–AC4): CLI-Subcommand ``serve``.

Keine Live-Netz-/DB-Calls: ``uvicorn.run`` und ``wortlaut.cli.upgrade_head``
werden patcht; die WORM-Store-Naht wird durch das Fehlen von ENV-Werten
erreicht (serve konstruiert keinen Store).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from wortlaut.cli import main


@pytest.fixture
def serve_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pflicht-ENV fuer ``serve`` (Dummy-Werte, keine Secrets).

    Kein ``yield``: ``monkeypatch`` raeumt selbst auf, ein Teardown gibt es nicht.
    """
    monkeypatch.setenv("WORTLAUT_DB_DSN", "postgresql+asyncpg://user@host/db")
    monkeypatch.setenv("WORTLAUT_WORM_ENDPOINT", "minio.example.com")
    monkeypatch.setenv("WORTLAUT_WORM_ACCESS_KEY", "ak")
    monkeypatch.setenv("WORTLAUT_WORM_SECRET_KEY", "sk")


def test_serve_passes_import_string_and_factory(
    monkeypatch: pytest.MonkeyPatch, serve_env: None
) -> None:
    """AC3: ``uvicorn.run`` 1x mit dem Import-String, ``factory=True`` und
    Default-HOST/PORT/WORKERS; ``main`` liefert 0."""
    calls: list[tuple[Any, ...]] = []
    kwargs: list[dict[str, Any]] = []

    def fake_run(app: object, **kw: Any) -> None:
        calls.append((app,))
        kwargs.append(kw)

    monkeypatch.setattr("wortlaut.cli.uvicorn.run", fake_run)

    assert main(["serve"]) == 0
    assert len(calls) == 1
    assert calls[0][0] == "wortlaut.serving.asgi:create_asgi_app"
    assert kwargs[0]["factory"] is True
    assert kwargs[0]["host"] == "0.0.0.0"
    assert kwargs[0]["port"] == 8000
    assert kwargs[0]["workers"] == 1


def test_serve_reads_host_port_workers_from_env(
    monkeypatch: pytest.MonkeyPatch, serve_env: None
) -> None:
    """AC3: ``WORTLAUT_API_*``-ENV wirken (keine Flags — ENV-only)."""
    calls: list[tuple[Any, ...]] = []
    kwargs: list[dict[str, Any]] = []

    def fake_run(app: object, **kw: Any) -> None:
        calls.append((app,))
        kwargs.append(kw)

    monkeypatch.setattr("wortlaut.cli.uvicorn.run", fake_run)
    monkeypatch.setenv("WORTLAUT_API_HOST", "1.2.3.4")
    monkeypatch.setenv("WORTLAUT_API_PORT", "9999")
    monkeypatch.setenv("WORTLAUT_API_WORKERS", "3")

    assert main(["serve"]) == 0
    assert len(calls) == 1
    assert calls[0][0] == "wortlaut.serving.asgi:create_asgi_app"
    assert kwargs[0]["factory"] is True
    assert kwargs[0]["host"] == "1.2.3.4"
    assert kwargs[0]["port"] == 9999
    assert kwargs[0]["workers"] == 3


def test_serve_skips_bootstrap(monkeypatch: pytest.MonkeyPatch, serve_env: None) -> None:
    """AC2: ``upgrade_head`` 0x und kein Minio-Worm-Store (also ``ensure_bucket`` 0x)
    — serve macht kein Bootstrap (§4.4, read-only)."""
    upgrade_calls: list[None] = []
    store_constructions: list[None] = []
    run_calls: list[None] = []

    def fake_run(*a: Any, **kw: Any) -> None:
        run_calls.append(None)

    with (
        patch("wortlaut.cli.upgrade_head", side_effect=lambda *a: upgrade_calls.append(None)),
        patch(
            "wortlaut.cli.MinioWormStore",
            side_effect=lambda *a, **kw: store_constructions.append(None),
        ),
        patch("wortlaut.cli.uvicorn.run", side_effect=fake_run),
    ):
        assert main(["serve"]) == 0

    assert upgrade_calls == []
    assert store_constructions == []
    assert run_calls == [None]


def test_serve_missing_env_exits_2(
    monkeypatch: pytest.MonkeyPatch, serve_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC4: fehlendes ``WORTLAUT_WORM_ACCESS_KEY`` -> rc 2, ``uvicorn.run`` 0x,
    keine ENV-Werte in der Ausgabe (R-SEC-01)."""
    run_calls: list[None] = []

    def fake_run(*a: Any, **kw: Any) -> None:
        run_calls.append(None)

    monkeypatch.delenv("WORTLAUT_WORM_ACCESS_KEY", raising=False)
    monkeypatch.setattr("wortlaut.cli.uvicorn.run", fake_run)

    assert main(["serve"]) == 2
    assert run_calls == []
    out = capsys.readouterr()
    assert "Konfiguration fehlgeschlagen" in out.err
    # Der fehlende Feldname MUSS genannt werden — sonst raet der Betreiber.
    assert "access_key" in out.err


def test_serve_error_leaks_no_secret(
    monkeypatch: pytest.MonkeyPatch, serve_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC4: kein ENV-WERT in der Fehlermeldung (R-SEC-01).

    Regressionstest gegen einen im Review gemessenen Leak: ``str(ValidationError)``
    haengt das komplette Eingabe-Dict an — bei ``WormSettings`` also den Wert von
    ``WORTLAUT_WORM_SECRET_KEY`` (``input_value={..., 'secret_key': 'TOPSECRET123'}``).
    Der landete damit woertlich auf stderr und in jedem Container-Log. Geprueft
    werden deshalb BEIDE Kanaele: das DSN-Passwort und der WORM-Secret-Wert.
    """
    run_calls: list[None] = []

    def fake_run(*a: Any, **kw: Any) -> None:
        run_calls.append(None)

    # Die Canary-Werte werden zusammengesetzt statt als Literal-URI geschrieben:
    # ein ausgeschriebenes "scheme://user:pw@host" ist fuer Secret-Scanner (Sonar
    # S6698, gitleaks) ununterscheidbar von einem echten Fund. Der Test prueft
    # denselben Pfad, ohne dem Scanner ein Pseudo-Secret unterzuschieben.
    db_canary = "canary-db-" + "8f2c"
    worm_canary = "canary-worm-" + "3ae1"
    monkeypatch.setenv("WORTLAUT_DB_DSN", f"postgresql+asyncpg://u:{db_canary}@h/db")
    monkeypatch.setenv("WORTLAUT_WORM_SECRET_KEY", worm_canary)
    monkeypatch.setenv("WORTLAUT_WORM_ENDPOINT", "minio.internal.example")
    monkeypatch.delenv("WORTLAUT_WORM_ACCESS_KEY", raising=False)
    monkeypatch.setattr("wortlaut.cli.uvicorn.run", fake_run)

    assert main(["serve"]) == 2
    assert run_calls == []
    out = capsys.readouterr()
    combined = out.out + out.err
    assert worm_canary not in combined  # <- der eigentliche Leak
    assert db_canary not in combined
    assert "minio.internal.example" not in combined
    assert "input_value" not in combined  # kein pydantic-Eingabe-Dict im Log


def test_kaputte_cors_env_exit_2(
    monkeypatch: pytest.MonkeyPatch, serve_env: None, capsys: pytest.CaptureFixture[str]
) -> None:
    """AC6 (Spec 0086): kaputte ``WORTLAUT_API_CORS_ORIGINS`` -> rc 2, uvicorn
    startet NICHT, kein ENV-Wert in der Ausgabe (nur Feldnamen, R-SEC-01)."""
    run_calls: list[None] = []

    def fake_run(*a: Any, **kw: Any) -> None:
        run_calls.append(None)

    monkeypatch.setenv("WORTLAUT_API_CORS_ORIGINS", "https://wortlaut.io/")
    monkeypatch.setattr("wortlaut.cli.uvicorn.run", fake_run)

    assert main(["serve"]) == 2
    assert run_calls == []  # uvicorn wurde NICHT gestartet
    out = capsys.readouterr()
    assert "Konfiguration fehlgeschlagen" in out.err
    assert "cors_origins" in out.err  # Feldname wird genannt
    assert "https://wortlaut.io/" not in out.err  # der Wert bleibt draussen
