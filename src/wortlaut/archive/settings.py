"""Archiv-Einstellungen aus der Umgebung (Prefix ``WORTLAUT_ARCHIVE_``).

Nur im Composition-Root (CLI) gelesen und als einfache Werte in die Archiver
injiziert (DI) — der Archiver-Layer selbst bleibt pydantic-frei (R-ARCH-02).
"""

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ArchiveSettings(BaseSettings):
    """Drosselung, Retry, Breaker-Limits und Zugangsdaten für die Fremdarchivierung."""

    model_config = SettingsConfigDict(env_prefix="WORTLAUT_ARCHIVE_")

    # SPN2 erlaubt authentifiziert 7 Captures/Minute — 10 s Abstand bleibt darunter
    wayback_min_interval_seconds: float = 10.0
    archive_today_min_interval_seconds: float = 15.0
    retry_attempts: int = 3
    retry_base_delay_seconds: float = 2.0
    optional_failure_limit: int = 3  # archive.today im Lauf stilllegen
    consecutive_failure_limit: int = 5  # Circuit-Breaker für den ganzen Lauf
    preflight_enabled: bool = True  # ENV WORTLAUT_ARCHIVE_PREFLIGHT_ENABLED
    # Internet-Archive-S3-Schlüssel (SPN2, #108). Pflicht am Composition-Root
    # (Exit 2 ohne beide) — als SecretStr, nie als Klartext-Feld.
    ia_access_key: SecretStr | None = None
    ia_secret: SecretStr | None = None
    # SPN2-Polling: Versuchslimit zählt Versuche, nicht Sekunden (Spec 0108 §4.4).
    spn2_poll_interval_seconds: float = 3.0
    spn2_poll_timeout_seconds: float = 180.0

    @model_validator(mode="after")
    def _key_pair_must_be_complete(self) -> "ArchiveSettings":
        """Genau EINER der beiden Schlüssel gesetzt → Fehler (AC2).

        Die Meldung nennt nur die Feldnamen, nie einen Wert (R-SEC-01).
        """
        if (self.ia_access_key is None) ^ (self.ia_secret is None):
            raise ValueError(
                "Zugangsdaten unvollständig: ia_access_key und ia_secret "
                "müssen zusammen gesetzt werden (beide oder keines)"
            )
        return self
