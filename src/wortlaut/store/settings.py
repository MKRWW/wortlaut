"""Datenbank-Einstellungen aus der Umgebung (Prefix ``WORTLAUT_DB_``)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class DbSettings(BaseSettings):
    """Verbindungsdaten. Der DSN kommt aus ENV/Secret, nie aus dem Code (R-SEC-01)."""

    model_config = SettingsConfigDict(env_prefix="WORTLAUT_DB_")

    dsn: str


class WormSettings(BaseSettings):
    """MinIO WORM-Storage-Zugangsdaten (R-SEC-01)."""

    model_config = SettingsConfigDict(env_prefix="WORTLAUT_WORM_")

    endpoint: str
    access_key: str
    secret_key: str
    bucket: str = "wortlaut-worm"
    secure: bool = True
