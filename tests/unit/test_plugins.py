"""Unit (Spec 0141): Plugin-Laden mit Freigabe und Deckel — Fake-Entry-Points, kein Netz."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from unittest.mock import patch

import pytest

from wortlaut.ingest.adapter import AdapterError, RawSource, SourceRef, SpanDraft
from wortlaut.ingest.plugins import PluginError, load_plugins, registry_from_env
from wortlaut.ingest.registry import AdapterEntry, AdapterRegistry, default_registry

_NAME = "landtag-probe"


class _FakeAdapter:
    name = _NAME
    version = "0.1.0"
    trust_level = "verified_primary"
    rights_basis: str | None = "amtliches_werk_p5"

    async def discover(self, since: datetime) -> Sequence[SourceRef]:
        return []

    async def fetch(self, ref: SourceRef) -> RawSource:
        raise AdapterError("x")

    def normalize(self, raw: RawSource) -> str:
        return ""

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        return []

    async def aclose(self) -> None:
        return None


def _entry(**kw: Any) -> AdapterEntry:
    entry = AdapterEntry(
        name=_NAME,
        version="0.1.0",
        trust_level="verified_primary",
        rights_basis="amtliches_werk_p5",
        create=_FakeAdapter,
    )
    return dataclasses.replace(entry, **kw)


class _FakeEP:
    def __init__(self, name: str, obj: object = None, exc: Exception | None = None) -> None:
        self.name = name
        self.obj = obj
        self.exc = exc
        self.load_calls = 0

    def load(self) -> object:
        self.load_calls += 1
        if self.exc is not None:
            raise self.exc
        return self.obj


def test_allowed_plugin_is_registered() -> None:
    registry = default_registry()
    load_plugins(registry, allowed=[_NAME], verified=[], entry_points=[_FakeEP(_NAME, _entry())])
    entry = registry.get(_NAME)
    assert entry is not None
    assert entry.plugin is True


def test_unallowed_entry_point_not_loaded() -> None:
    ep = _FakeEP("anderes", _entry(name="anderes"))
    registry = default_registry()
    load_plugins(registry, allowed=[], verified=[], entry_points=[ep])
    assert ep.load_calls == 0
    assert registry.names() == default_registry().names()


def test_registry_from_env_without_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORTLAUT_ADAPTER_PLUGINS", raising=False)
    monkeypatch.delenv("WORTLAUT_ADAPTER_VERIFIED", raising=False)
    with patch("wortlaut.ingest.plugins.importlib.metadata.entry_points") as eps:
        assert registry_from_env().names() == default_registry().names()
        eps.assert_not_called()


def test_registry_from_env_with_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORTLAUT_ADAPTER_PLUGINS", _NAME)
    with patch(
        "wortlaut.ingest.plugins.importlib.metadata.entry_points",
        return_value=[_FakeEP(_NAME, _entry())],
    ):
        assert _NAME in registry_from_env().names()


def _load(entry: AdapterEntry, verified: list[str]) -> AdapterRegistry:
    registry = default_registry()
    load_plugins(registry, allowed=[_NAME], verified=verified, entry_points=[_FakeEP(_NAME, entry)])
    return registry


def test_verified_primary_capped_without_release() -> None:
    entry = _load(_entry(), []).get(_NAME)
    assert entry is not None
    assert entry.trust_level == "secondary"
    assert entry.declared_trust_level == "verified_primary"
    assert entry.create().trust_level == "secondary"


def test_verified_primary_kept_with_release() -> None:
    entry = _load(_entry(), [_NAME]).get(_NAME)
    assert entry is not None
    assert entry.trust_level == "verified_primary"
    assert entry.create().trust_level == "verified_primary"


class _LyingAdapter(_FakeAdapter):
    name = "anders"
    version = "9.9"
    rights_basis = "lizenz"


def test_lying_instance_is_pinned() -> None:
    entry = _load(_entry(trust_level="secondary", create=_LyingAdapter), []).get(_NAME)
    assert entry is not None
    instance = entry.create()
    assert instance.trust_level == "secondary"
    assert instance.name == _NAME
    assert instance.version == "0.1.0"
    assert instance.rights_basis == "amtliches_werk_p5"


async def test_pinned_adapter_delegates() -> None:
    entry = _load(_entry(), []).get(_NAME)
    assert entry is not None
    instance = entry.create()
    assert await instance.discover(datetime(2024, 1, 1)) == []
    await instance.aclose()


_ERROR_CASES: list[tuple[list[str], list[str], list[_FakeEP], str]] = [
    ([_NAME], [], [], "kein Entry Point"),
    ([_NAME], [], [_FakeEP(_NAME, _entry()), _FakeEP(_NAME, _entry())], "mehrdeutig"),
    ([_NAME], [], [_FakeEP(_NAME, "x")], "kein AdapterEntry"),
    ([_NAME], [], [_FakeEP(_NAME, _entry(name="anders"))], "anderen Namen"),
    ([_NAME], [], [_FakeEP(_NAME, _entry(trust_level="hoch"))], "trust_level"),
    ([_NAME], [], [_FakeEP(_NAME, _entry(rights_basis="gemeinfrei"))], "rights_basis"),
    (["dip-api"], [], [_FakeEP("dip-api", _entry(name="dip-api"))], "eingebauter Adapter"),
    ([], [_NAME], [], "nicht in WORTLAUT_ADAPTER_PLUGINS"),
    (
        [_NAME],
        [],
        [_FakeEP(_NAME, exc=RuntimeError("GEHEIM-Text"))],
        "laden fehlgeschlagen: RuntimeError",
    ),
]


@pytest.mark.parametrize(("allowed", "verified", "eps", "message"), _ERROR_CASES)
def test_misconfiguration_raises(
    allowed: list[str], verified: list[str], eps: list[_FakeEP], message: str
) -> None:
    registry = default_registry()
    with pytest.raises(PluginError, match=message):
        load_plugins(registry, allowed=allowed, verified=verified, entry_points=eps)


def test_collision_does_not_load() -> None:
    ep = _FakeEP("dip-api", _entry(name="dip-api"))
    registry = default_registry()
    with pytest.raises(PluginError):
        load_plugins(registry, allowed=["dip-api"], verified=[], entry_points=[ep])
    assert ep.load_calls == 0


def test_load_error_text_not_in_message() -> None:
    ep = _FakeEP(_NAME, exc=RuntimeError("GEHEIM-Text"))
    registry = default_registry()
    with pytest.raises(PluginError) as exc:
        load_plugins(registry, allowed=[_NAME], verified=[], entry_points=[ep])
    assert "GEHEIM" not in str(exc.value)
