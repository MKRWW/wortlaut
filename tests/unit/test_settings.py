"""Unit (AC-Vorbereitung): DbSettings liest den DSN aus der Umgebung."""

import pytest

from wortlaut.store.settings import DbSettings


def test_dbsettings_parses_dsn_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORTLAUT_DB_DSN", "postgresql+asyncpg://u:p@h:5432/db")
    settings = DbSettings()
    assert settings.dsn == "postgresql+asyncpg://u:p@h:5432/db"
