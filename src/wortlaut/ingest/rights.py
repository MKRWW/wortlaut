"""Rechtsgrundlage je Quelle (docs/legal.md §2, §10; R-DATA-03); nur stdlib."""

from __future__ import annotations

RIGHTS_BASES: tuple[str, ...] = (
    "amtliches_werk_p5",
    "oeffentlich_gemacht_art9e",
    "zitat_p51",
    "lizenz",
    "ungeklaert",
)


def resolve_rights_basis(
    *, override: str | None, per_source: str | None, adapter_default: str | None
) -> str | None:
    """Erste gesetzte Angabe: ausdrückliche Übersteuerung, dann die Quelle, dann der Adapter."""
    for candidate in (override, per_source, adapter_default):
        if candidate is not None:
            return candidate
    return None
