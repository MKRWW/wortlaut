"""Adapter aus fremden Paketen (#141).

Nur freigegebene Entry Points der Gruppe ``wortlaut.adapters`` werden geladen;
Vertrauen ist gedeckelt (Spec 0141).
"""

from __future__ import annotations

import importlib.metadata
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from typing import Protocol

from pydantic_settings import BaseSettings, SettingsConfigDict

from wortlaut.ingest.adapter import IngestAdapter, RawSource, SourceRef, SpanDraft
from wortlaut.ingest.conformance import TRUST_LEVELS
from wortlaut.ingest.registry import AdapterEntry, AdapterRegistry, default_registry
from wortlaut.ingest.rights import RIGHTS_BASES

PLUGIN_GROUP = "wortlaut.adapters"


class PluginError(Exception):
    """Fehlkonfiguration der Plugin-Freigabe; Meldung ohne Fremdtext."""


class PluginSettings(BaseSettings):
    """Betreiber-Freigaben: kommagetrennte Entry-Point-Namen."""

    model_config = SettingsConfigDict(env_prefix="WORTLAUT_ADAPTER_")

    plugins: str = ""
    verified: str = ""


class EntryPointLike(Protocol):
    name: str

    def load(self) -> object: ...


def _names(raw: str) -> list[str]:
    return [s.strip() for s in raw.split(",") if s.strip()]


class _PinnedAdapter:
    """Deckel-Wrapper: pinnt die geprüften Werte des Registry-Eintrags (§4.2).

    Die Instanz kann nichts anderes behaupten als den Eintrag; alles andere
    wird an ``inner`` delegiert.
    """

    def __init__(
        self,
        inner: IngestAdapter,
        *,
        name: str,
        version: str,
        trust_level: str,
        rights_basis: str | None,
    ) -> None:
        self._inner = inner
        self.name = name
        self.version = version
        self.trust_level = trust_level
        self._rights_basis = rights_basis

    @property
    def rights_basis(self) -> str | None:
        return self._rights_basis

    async def discover(self, since: datetime) -> Sequence[SourceRef]:
        return await self._inner.discover(since)

    async def fetch(self, ref: SourceRef) -> RawSource:
        return await self._inner.fetch(ref)

    def normalize(self, raw: RawSource) -> str:
        return self._inner.normalize(raw)

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        return self._inner.parse(raw, normalized)

    async def aclose(self) -> None:
        await self._inner.aclose()


def _pinned_factory(obj: AdapterEntry, trust_level: str) -> Callable[[], IngestAdapter]:
    def create() -> IngestAdapter:
        return _PinnedAdapter(
            obj.create(),
            name=obj.name,
            version=obj.version,
            trust_level=trust_level,
            rights_basis=obj.rights_basis,
        )

    return create


def _load_entry(name: str, entry_points: Sequence[EntryPointLike]) -> AdapterEntry:
    matches = [ep for ep in entry_points if ep.name == name]
    if not matches:
        raise PluginError(f"kein Entry Point '{name}' in Gruppe {PLUGIN_GROUP}")
    if len(matches) > 1:
        raise PluginError(f"mehrdeutig: {len(matches)} Entry Points '{name}'")
    try:
        obj = matches[0].load()
    except Exception as exc:
        raise PluginError(f"'{name}' laden fehlgeschlagen: {type(exc).__name__}") from None
    if not isinstance(obj, AdapterEntry):
        raise PluginError(f"'{name}' ist kein AdapterEntry")
    if obj.name != name:
        raise PluginError(f"'{name}' deklariert anderen Namen")
    if obj.trust_level not in TRUST_LEVELS:
        raise PluginError(f"'{name}' trust_level nicht in TRUST_LEVELS")
    if obj.rights_basis is not None and obj.rights_basis not in RIGHTS_BASES:
        raise PluginError(f"'{name}' rights_basis nicht in RIGHTS_BASES")
    return obj


def load_plugins(
    registry: AdapterRegistry,
    *,
    allowed: Sequence[str],
    verified: Sequence[str],
    entry_points: Iterable[EntryPointLike],
) -> None:
    """Freigegebene Entry Points laden und registrieren (§4.1); erster Fehler bricht ab."""
    unallowed = [n for n in verified if n not in allowed]
    if unallowed:
        raise PluginError(
            "freigegeben als verified, aber nicht in WORTLAUT_ADAPTER_PLUGINS: "
            + ", ".join(unallowed)
        )
    points = list(entry_points)
    seen: set[str] = set()
    for name in allowed:
        if name in seen:
            continue
        seen.add(name)
        if registry.get(name) is not None:
            raise PluginError(f"'{name}' ist ein eingebauter Adapter")
        obj = _load_entry(name, points)
        effective = obj.trust_level
        if effective == "verified_primary" and name not in verified:
            effective = "secondary"
        registry.register(
            AdapterEntry(
                name=name,
                version=obj.version,
                trust_level=effective,
                rights_basis=obj.rights_basis,
                create=_pinned_factory(obj, effective),
                declared_trust_level=obj.trust_level,
                plugin=True,
            )
        )


def registry_from_env() -> AdapterRegistry:
    """Eingebaute Adapter plus freigegebene Plugins (§4.3); kein Modul-Zustand."""
    settings = PluginSettings()
    allowed = _names(settings.plugins)
    verified = _names(settings.verified)
    registry = default_registry()
    entry_points: Iterable[EntryPointLike] = []
    if allowed:
        entry_points = importlib.metadata.entry_points(group=PLUGIN_GROUP)
    load_plugins(registry, allowed=allowed, verified=verified, entry_points=entry_points)
    return registry
