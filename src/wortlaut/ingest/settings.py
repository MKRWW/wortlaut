"""DIP-Adapter-Einstellungen (ENV-Präfix ``WORTLAUT_DIP_``).

API-Key kommt aus der Umgebung / Secrets, niemals aus dem Code (R-SEC-01).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class DipSettings(BaseSettings):
    """DIP-Plenarprotokoll-Adapter-Konfiguration."""

    model_config = SettingsConfigDict(env_prefix="WORTLAUT_DIP_")

    api_key: str
    api_base_url: str = "https://search.dip.bundestag.de/api/v1"
    pdf_host: str = "dserver.bundestag.de"
