"""Unit: Settings lesen die Werte aus der Umgebung.

``DbSettings`` (DSN) und ``ArchiveSettings`` (Zugangsdaten, Drosselung;
AC1/AC2/AC18 aus #108).
"""

import pytest
from pydantic import ValidationError

from wortlaut.archive.settings import ArchiveSettings
from wortlaut.store.settings import DbSettings


def test_dbsettings_parses_dsn_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORTLAUT_DB_DSN", "postgresql+asyncpg://u:p@h:5432/db")
    settings = DbSettings()
    assert settings.dsn == "postgresql+asyncpg://u:p@h:5432/db"


def test_ia_zugangsdaten_aus_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC1: beide ENV gesetzt → ``SecretStr``-Felder tragen exakt diese
    Werte; ohne gesetzte ENV sind beide ``None``."""
    monkeypatch.delenv("WORTLAUT_ARCHIVE_IA_ACCESS_KEY", raising=False)
    monkeypatch.delenv("WORTLAUT_ARCHIVE_IA_SECRET", raising=False)
    empty = ArchiveSettings()
    assert empty.ia_access_key is None
    assert empty.ia_secret is None

    # Zusammengesetzt statt literal (S6698: keine ausschreibenden
    # Zugangsdaten-artigen Literale in Tests).
    access = "k-" + "abc-1"
    secret = "s-" + "xyz-2"
    monkeypatch.setenv("WORTLAUT_ARCHIVE_IA_ACCESS_KEY", access)
    monkeypatch.setenv("WORTLAUT_ARCHIVE_IA_SECRET", secret)
    settings = ArchiveSettings()
    assert settings.ia_access_key is not None
    assert settings.ia_secret is not None
    assert settings.ia_access_key.get_secret_value() == access
    assert settings.ia_secret.get_secret_value() == secret


@pytest.mark.parametrize(
    "env",
    ["WORTLAUT_ARCHIVE_IA_ACCESS_KEY", "WORTLAUT_ARCHIVE_IA_SECRET"],
    ids=["nur_access_key", "nur_secret"],
)
def test_nur_ein_schluessel_ist_fehler(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    """AC2: genau EINER der beiden Schlüssel gesetzt → ``ValidationError`` —
    je ein Fall pro Richtung. Die Meldung nennt nur Feldnamen, nie Werte."""
    monkeypatch.delenv("WORTLAUT_ARCHIVE_IA_ACCESS_KEY", raising=False)
    monkeypatch.delenv("WORTLAUT_ARCHIVE_IA_SECRET", raising=False)
    monkeypatch.setenv(env, "k-abc-1")
    with pytest.raises(ValidationError) as excinfo:
        ArchiveSettings()
    # Die *msg* des Fehlers nennt nur die Feldnamen, nie einen Wert (R-SEC-01).
    # str(ValidationError) würde das pydantic-Input-Dict (mit dem Secret)
    # mitdrucken — die msg ist aber der saubere Teil, der an den Betreiber geht.
    err = excinfo.value.errors()[0]
    msg = err["msg"]
    assert "ia_access_key" in msg
    assert "ia_secret" in msg
    assert "k-abc-1" not in msg


def test_drosselung_unter_limit() -> None:
    """AC18: die Voreinstellung (10 s Abstand) bleibt unter dem SPN2-Limit von
    7 Captures/Minute für authentifizierte Nutzer."""
    assert 60 / ArchiveSettings().wayback_min_interval_seconds <= 7
