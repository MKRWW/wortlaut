"""API-Einstellungen aus der Umgebung (Prefix ``WORTLAUT_API_``)."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    """Bind-Adresse und Worker-Zahl des ASGI-Servers (Prefix WORTLAUT_API_). Keine Secrets."""

    model_config = SettingsConfigDict(env_prefix="WORTLAUT_API_")

    # 0.0.0.0 ist im Container der einzig von aussen erreichbare Bind — ein
    # 127.0.0.1-Default waere ein stiller Betriebsfehler (der Container antwortet
    # nie). Die Exposition regelt das Deployment (Cloudflare Tunnel, kein
    # oeffentlicher Port), nicht die Bind-Adresse (Spec 0081 §7).
    host: str = "0.0.0.0"
    # ge/le-Constraints: eine kaputte ENV muss zu Exit 2 fuehren, nicht zu einem
    # halb gestarteten Server (fail-fast in wortlaut.cli._run_serve).
    port: int = Field(default=8000, ge=1, le=65535)
    # Default 1: workers > 1 vervielfacht die DB-Verbindungen (eigener Pool pro
    # Worker) — wer hochdreht, muss max_connections kennen (Spec 0081 §8).
    workers: int = Field(default=1, ge=1)
