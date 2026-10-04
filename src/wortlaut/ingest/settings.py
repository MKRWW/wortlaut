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


class LandtagStSettings(BaseSettings):
    """Landtag Sachsen-Anhalt — Konfiguration (#145).

    Schalter ``enabled`` (ENV ``WORTLAUT_LANDTAG_ST_ENABLED``, Default ``False``):
    ohne ihn wird nichts abgerufen — ``discover`` wirft sofort ``LandtagStDisabled``
    und stellt keine Anfrage (robots.txt-Lage, Spec 0145 §0b.1).
    """

    model_config = SettingsConfigDict(env_prefix="WORTLAUT_LANDTAG_ST_")

    enabled: bool = False
    base_url: str = "https://padoka.landtag.sachsen-anhalt.de/files/plenum"
    wahlperiode: int = 8
    lookback: int = 3
    contact: str = "https://github.com/MKRWW/wortlaut"
