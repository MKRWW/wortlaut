"""Archiv-Einstellungen aus der Umgebung (Prefix ``WORTLAUT_ARCHIVE_``).

Nur im Composition-Root (CLI) gelesen und als einfache Werte in die Archiver
injiziert (DI) — der Archiver-Layer selbst bleibt pydantic-frei (R-ARCH-02).
"""

from pydantic_settings import BaseSettings, SettingsConfigDict

from wortlaut.archive.preflight import PROBE_URL


class ArchiveSettings(BaseSettings):
    """Drosselung, Retry und Breaker-Limits für die Fremdarchivierung."""

    model_config = SettingsConfigDict(env_prefix="WORTLAUT_ARCHIVE_")

    wayback_min_interval_seconds: float = 5.0
    archive_today_min_interval_seconds: float = 15.0
    retry_attempts: int = 3
    retry_base_delay_seconds: float = 2.0
    optional_failure_limit: int = 3  # archive.today im Lauf stilllegen
    consecutive_failure_limit: int = 5  # Circuit-Breaker für den ganzen Lauf
    preflight_enabled: bool = True  # ENV WORTLAUT_ARCHIVE_PREFLIGHT_ENABLED
    preflight_url: str = PROBE_URL  # ENV WORTLAUT_ARCHIVE_PREFLIGHT_URL
