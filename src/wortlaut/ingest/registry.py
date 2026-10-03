"""Adapter-Registry (#96) — die eine Stelle, die konkrete Adapter kennt.

Der Composition-Root wählt per Name; Fremdpakete (Entry Points) folgen später.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from wortlaut.ingest.adapter import IngestAdapter
from wortlaut.ingest.dip import DipPlenarprotokollAdapter

DEFAULT_ADAPTER = "dip-api"


@dataclass(frozen=True)
class AdapterEntry:
    """Ein registrierter Adapter: Metadaten für die Liste plus Fabrik.

    ``create`` liest die Einstellungen des Adapters selbst aus der Umgebung —
    der Kern kennt deren Namen nicht.
    """

    name: str
    version: str
    trust_level: str
    rights_basis: str | None
    create: Callable[[], IngestAdapter]


class AdapterRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, AdapterEntry] = {}

    def register(self, entry: AdapterEntry) -> None:
        if entry.name in self._entries:
            raise ValueError(f"Adapter '{entry.name}' ist bereits registriert")
        self._entries[entry.name] = entry

    def get(self, name: str) -> AdapterEntry | None:
        return self._entries.get(name)

    def names(self) -> list[str]:
        return sorted(self._entries)

    def entries(self) -> list[AdapterEntry]:
        return [self._entries[n] for n in self.names()]


def default_registry() -> AdapterRegistry:
    """Neue Registry mit allen eingebauten Adaptern (kein Modul-Zustand)."""
    registry = AdapterRegistry()
    registry.register(
        AdapterEntry(
            name=DipPlenarprotokollAdapter.name,
            version=DipPlenarprotokollAdapter.version,
            trust_level=DipPlenarprotokollAdapter.trust_level,
            rights_basis=DipPlenarprotokollAdapter.rights_basis,
            create=DipPlenarprotokollAdapter.from_env,
        )
    )
    return registry
