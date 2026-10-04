"""Unit (Spec 0096, AC2/AC14, §4.1/§4.3): Adapter-Registry-Verhalten und
Vorrang der Rechtsgrundlage — ohne ENV, ohne Netz, ohne DB."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import pytest

from wortlaut.ingest.adapter import RawSource, SourceRef, SpanDraft
from wortlaut.ingest.dip import DipPlenarprotokollAdapter
from wortlaut.ingest.registry import (
    DEFAULT_ADAPTER,
    AdapterEntry,
    AdapterRegistry,
    default_registry,
)
from wortlaut.ingest.rights import RIGHTS_BASES, resolve_rights_basis
from wortlaut.store.models import _RIGHTS_BASIS

# Vorrang-Tests verwenden bewusst KEIN "amtliches_werk_p5": der alte Default
# wuerde eine Verwechslung mit dem neuen Vorrang nicht aufdecken (Spec 0096 §11).
_LIZENZ = "lizenz"


class _ProbeAdapter:
    """Minimaler Adapter ohne I/O — nur die Vertragsglieder der Registry."""

    name = "probe"
    version = "1.0.0"
    trust_level = "verified_primary"
    parliament = "bundestag"
    mandate_role = "MdB"
    rights_basis: str | None = _LIZENZ

    async def discover(self, since: datetime) -> Sequence[SourceRef]:
        return []

    async def fetch(self, ref: SourceRef) -> RawSource:
        raise AssertionError("not used")

    def normalize(self, raw: RawSource) -> str:
        return ""

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        return []

    async def aclose(self) -> None:
        return None


def _entry(name: str, rights_basis: str | None) -> AdapterEntry:
    """Eintrag mit dem Probe-Adapter als Fabrik (Klasse als ``create``)."""
    return AdapterEntry(
        name=name,
        version="1.0.0",
        trust_level="verified_primary",
        rights_basis=rights_basis,
        create=_ProbeAdapter,
    )


def test_register_and_get() -> None:
    """§4.1: ``register`` + ``get``; unbekannter Name liefert ``None``."""
    registry = AdapterRegistry()
    entry = _entry("quelle", _LIZENZ)
    registry.register(entry)
    assert registry.get("quelle") == entry
    assert registry.get("unbekannt") is None


def test_duplicate_name_rejected() -> None:
    """§4.1: doppelter Name wird abgelehnt (``ValueError``)."""
    registry = AdapterRegistry()
    entry = _entry("duplikat", _LIZENZ)
    registry.register(entry)
    with pytest.raises(ValueError, match="bereits registriert"):
        registry.register(entry)


def test_names_sorted() -> None:
    """§4.1: ``names()`` und ``entries()`` sind nach Name sortiert."""
    registry = AdapterRegistry()
    registry.register(_entry("zeta", None))
    registry.register(_entry("alpha", None))
    registry.register(_entry("mid", None))
    expected = ["alpha", "mid", "zeta"]
    assert registry.names() == expected
    assert [entry.name for entry in registry.entries()] == expected


def test_default_registry_has_dip() -> None:
    """AC2: ``default_registry()`` trägt ``dip-api`` mit den Klassenattributen
    des DIP-Adapters und der Fabrik ``from_env``; jeder Aufruf ist frisch
    (kein Modul-Zustand, §4.1)."""
    registry = default_registry()
    assert registry.names() == [DEFAULT_ADAPTER]
    entry = registry.get(DEFAULT_ADAPTER)
    assert entry is not None
    assert entry.name == DipPlenarprotokollAdapter.name
    assert entry.version == DipPlenarprotokollAdapter.version
    assert entry.trust_level == DipPlenarprotokollAdapter.trust_level
    assert entry.rights_basis == DipPlenarprotokollAdapter.rights_basis
    assert entry.create == DipPlenarprotokollAdapter.from_env
    assert default_registry() is not registry


def test_rights_bases_match_db_enum() -> None:
    """AC14: ``RIGHTS_BASES`` ist identisch mit dem DB-Enum (Werte + Reihenfolge)."""
    assert RIGHTS_BASES == tuple(_RIGHTS_BASIS.enums)


@pytest.mark.parametrize(
    ("override", "per_source", "adapter_default", "expected"),
    [
        (None, None, _LIZENZ, _LIZENZ),
        (None, "zitat_p51", _LIZENZ, "zitat_p51"),
        ("oeffentlich_gemacht_art9e", "zitat_p51", _LIZENZ, "oeffentlich_gemacht_art9e"),
        (None, None, None, None),
    ],
)
def test_resolve_order(
    override: str | None,
    per_source: str | None,
    adapter_default: str | None,
    expected: str | None,
) -> None:
    """§4.3: Vorrang ``override`` > ``per_source`` > ``adapter_default``;
    ohne jede Angabe bleibt ``None`` (kein stummer Default, #97)."""
    resolved = resolve_rights_basis(
        override=override, per_source=per_source, adapter_default=adapter_default
    )
    assert resolved == expected
