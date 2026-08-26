"""Timestamp-Einstellungen aus der Umgebung (Prefix ``WORTLAUT_TSA_``).

Nur im Composition-Root (CLI) gelesen und als einfache Werte in den Stamper
injiziert (DI) — der Timestamp-Layer selbst bleibt pydantic-frei (R-ARCH-02).

ENV wählt nur aus der im Code hinterlegten Registry aus (§0c): ``profiles``
nennt Profilnamen (Reihenfolge = Primär, Fallback). Keine URL und kein
Root-Zertifikat sind zur Laufzeit injizierbar — der Trust-Anker ist der einzige
Grund, warum ein Token überhaupt etwas beweist.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class TimestampSettings(BaseSettings):
    """Profil-Auswahl/-Reihenfolge, Timeout und Circuit-Breaker-Limit."""

    model_config = SettingsConfigDict(env_prefix="WORTLAUT_TSA_")

    # Die Reihenfolge ist die Fallback-Kette: erst freetsa, dann sigstore.
    profiles: str = "freetsa,sigstore"
    timeout_seconds: float = 10.0
    consecutive_failure_limit: int = 5  # Circuit-Breaker, Muster aus #73

    def profile_names(self) -> list[str]:
        """``profiles`` an „,"“ splitten, trimmen, leere Einträge verwirfen."""
        return [part.strip() for part in self.profiles.split(",") if part.strip()]
