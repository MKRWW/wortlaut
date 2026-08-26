"""API-Einstellungen aus der Umgebung (Prefix ``WORTLAUT_API_``)."""

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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

    # Vollstaendige Allowlist, KEINE Ergaenzung des Defaults: wer die Variable setzt,
    # muss https://wortlaut.io mit aufzaehlen, sonst sperrt er die Produktion aus.
    # NoDecode schaltet die JSON-Dekodierung der Settings-Quelle ab — ohne sie waere
    # die kommagetrennte Schreibweise ein SettingsError und nur JSON zulaessig
    # (Spec 0086 Abschnitt 0a).
    cors_origins: Annotated[list[str], NoDecode] = ["https://wortlaut.io"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [p.strip() for p in v.split(",") if p.strip()]
        return v

    @field_validator("cors_origins", mode="after")
    @classmethod
    def _check_origins(cls, v: list[str]) -> list[str]:
        # Starlette vergleicht den Origin-Header exakt: ein Trailing Slash matcht nie,
        # ein Eintrag ohne Schema auch nicht — beides waere ein stiller Totalausfall
        # (Spec 0086 Abschnitt 0b). Lieber Exit 2 als eine Allowlist, die nichts trifft.
        if not v:
            raise ValueError("mindestens ein Origin erforderlich")
        for o in v:
            if not o.startswith(("http://", "https://")):
                raise ValueError(f"Origin ohne Schema: {o!r}")
            if any(c.isspace() for c in o) or o.endswith("/"):
                raise ValueError(f"kein gueltiger Origin: {o!r}")
        return v
