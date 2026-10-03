"""Unit (Spec 0095, AC1–AC3): Adapter-Vertrag — ``aclose`` im Protocol,
gemeinsame Fehlerbasis ``AdapterError``, ``cli.py`` fängt nur die Basis."""

from __future__ import annotations

import ast
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from wortlaut.ingest.adapter import (
    AdapterError,
    IngestAdapter,
    RawSource,
    SourceRef,
    SpanDraft,
)
from wortlaut.ingest.dip import DipFetchError, DipHostNotAllowed, DipPlenarprotokollAdapter

_CLI_SOURCE = (Path(__file__).resolve().parents[2] / "src" / "wortlaut" / "cli.py").read_text(
    encoding="utf-8"
)


class _WithoutAclose:
    """Probe: erfüllt das Protocol bis auf ``aclose``."""

    name = "probe"
    version = "1.0.0"
    trust_level = "verified_primary"
    rights_basis = "lizenz"

    async def discover(self, since: datetime) -> Sequence[SourceRef]:
        return []

    async def fetch(self, ref: SourceRef) -> RawSource:
        raise AssertionError("not used")

    def normalize(self, raw: RawSource) -> str:
        return ""

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        return []


class _WithAclose(_WithoutAclose):
    async def aclose(self) -> None:
        return None


class _WithoutRightsBasis:
    """Probe: erfüllt das Protocol bis auf ``rights_basis``."""

    name = "probe"
    version = "1.0.0"
    trust_level = "verified_primary"

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


def _names_of(node: ast.expr | None) -> list[str]:
    """Namen eines ExceptHandler-Typs (Name, Attribute, Tupel aufgefaltet)."""
    if node is None:
        return []
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [node.attr]
    if isinstance(node, ast.Tuple):
        names: list[str] = []
        for elt in node.elts:
            names.extend(_names_of(elt))
        return names
    return []


# ── AC1: aclose im Vertrag ──────────────────────────────────────────────


def test_protocol_requires_aclose() -> None:
    """AC1: Objekt ohne ``aclose`` erfüllt das Protocol nicht (runtime-
    checkable); mit allen Mitgliedern schon."""
    without = _WithoutAclose()
    assert isinstance(without, IngestAdapter) is False
    complete = _WithAclose()
    assert isinstance(complete, IngestAdapter) is True


# ── AC8: Rechtsgrundlage im Vertrag (Spec 0096) ─────────────────────────


def test_protocol_declares_rights_basis() -> None:
    """AC8: ``IngestAdapter`` deklariert ``rights_basis``; ``SourceRef`` hat
    ``rights_basis`` mit Default ``None``; der DIP-Adapter deklariert
    ``amtliches_werk_p5``."""
    without = _WithoutRightsBasis()
    assert isinstance(without, IngestAdapter) is False
    assert SourceRef("u", "t", {}).rights_basis is None
    assert DipPlenarprotokollAdapter.rights_basis == "amtliches_werk_p5"


# ── AC2: Fehlerbasis ────────────────────────────────────────────────────


def test_error_hierarchy() -> None:
    """AC2: ``DipFetchError`` erbt von ``AdapterError``; ``DipHostNotAllowed``
    ist zugleich ``DipFetchError`` und ``ValueError``."""
    assert issubclass(DipFetchError, AdapterError)
    assert issubclass(DipHostNotAllowed, DipFetchError)
    assert issubclass(DipHostNotAllowed, ValueError)


# ── AC3: Kern fängt nur die Basis ───────────────────────────────────────


def test_cli_catches_only_adapter_error() -> None:
    """AC3: ``cli.py`` importiert nichts aus ``wortlaut.ingest.dip`` (#96);
    kein ``except`` nennt ``DipFetchError`` oder ``ValueError``."""
    tree = ast.parse(_CLI_SOURCE)

    dip_imports = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "wortlaut.ingest.dip"
        for alias in node.names
    ]
    assert dip_imports == []

    caught = [
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler)
        for name in _names_of(node.type)
    ]
    assert "DipFetchError" not in caught
    assert "ValueError" not in caught
