"""Datenbank-Einstellungen aus der Umgebung (Prefix ``WORTLAUT_DB_``)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class DbSettings(BaseSettings):
    """Verbindungsdaten. Der DSN kommt aus ENV/Secret, nie aus dem Code (R-SEC-01)."""

    model_config = SettingsConfigDict(env_prefix="WORTLAUT_DB_")

    dsn: str
