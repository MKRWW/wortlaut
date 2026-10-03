"""Unit (Spec 0096): Adapter-Auswahl ueber die Registry."""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wortlaut.cli import _run, main
from wortlaut.ingest.adapter import IngestAdapter, SourceRef
from wortlaut.ingest.registry import DEFAULT_ADAPTER, AdapterEntry, AdapterRegistry
from wortlaut.pipeline.ingest import IngestOutcome

_TEST_ADAPTER = "test-quelle"
_SINCE = "2024-01-01"
_LIZENZ = "lizenz"
_UNGEKLAERT = "ungeklaert"
_AMTLICHES = "amtliches_werk_p5"

# ── Fakes ────────────────────────────────────────────────────────────────


class _RegistryFake:
    version = "2.0.0"
    trust_level = "secondary"

    def __init__(self, name: str, rights_basis: str | None, refs: list[SourceRef]) -> None:
        self.name = name
        self.rights_basis = rights_basis
        self.refs = refs
        self.discover_calls = 0
        self.aclose_calls = 0

    async def discover(self, since: datetime) -> list[SourceRef]:
        self.discover_calls += 1
        return self.refs

    async def aclose(self) -> None:
        self.aclose_calls += 1


def _entry(adapter: _RegistryFake) -> AdapterEntry:
    def create() -> IngestAdapter:
        return cast(IngestAdapter, adapter)

    return AdapterEntry(
        name=adapter.name,
        version=adapter.version,
        trust_level=adapter.trust_level,
        rights_basis=adapter.rights_basis,
        create=create,
    )


class _FakeWorm:
    def __init__(self) -> None:
        self.ensure_bucket_called = False

    async def ensure_bucket(self) -> None:
        self.ensure_bucket_called = True


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_a: object) -> bool:
        return False

    async def commit(self) -> None:
        pass


class _FakeSessionmaker:
    def __call__(self) -> _FakeSession:
        return _FakeSession()


@pytest.fixture
def wired() -> Iterator[SimpleNamespace]:
    """Wie ``tests/unit/test_cli.py``, ohne Patch auf ``default_registry``
    (die echte Registry laeuft, der CLI waehlt per Name)."""
    dip = _RegistryFake(DEFAULT_ADAPTER, "amtliches_werk_p5", [])
    adapter = _RegistryFake(_TEST_ADAPTER, "amtliches_werk_p5", [])
    worm = _FakeWorm()
    engine = MagicMock()
    engine.dispose = AsyncMock()
    ingest = AsyncMock(return_value=IngestOutcome("inserted", None, "h"))
    with (
        patch("wortlaut.cli.DbSettings", return_value=MagicMock(dsn="f")),
        patch("wortlaut.cli.WormSettings", return_value=MagicMock()),
        patch("wortlaut.cli.create_async_engine_from", return_value=engine),
        patch("wortlaut.cli.make_sessionmaker", return_value=_FakeSessionmaker()),
        patch("wortlaut.cli.MinioWormStore", return_value=worm),
        patch("wortlaut.cli.upgrade_head", new=AsyncMock()),
        patch("wortlaut.cli.ensure_ingest_adapter", new=AsyncMock()),
        patch("wortlaut.cli.ingest_source", new=ingest),
    ):
        yield SimpleNamespace(
            dip=dip,
            adapter=adapter,
            worm=worm,
            engine=engine,
            ingest=ingest,
        )


# ── AC1, AC2, AC3, AC4, AC6 ──────────────────────────────────────────────


def test_adapter_option_selects_registered_adapter(wired: SimpleNamespace) -> None:
    """AC1: --adapter waehlt den registrierten Adapter; der andere bleibt ungerufen."""
    registry = AdapterRegistry()
    registry.register(_entry(wired.dip))
    registry.register(_entry(wired.adapter))
    with patch("wortlaut.cli.default_registry", return_value=registry):
        rc = main(["ingest", "--since", _SINCE, "--adapter", _TEST_ADAPTER, "--no-migrate"])
    assert rc == 0
    assert wired.adapter.discover_calls == 1
    assert wired.dip.discover_calls == 0


def test_default_adapter_without_option(wired: SimpleNamespace) -> None:
    """AC2: ohne --adapter gilt der Default (dip-api)."""
    registry = AdapterRegistry()
    registry.register(_entry(wired.dip))
    registry.register(_entry(wired.adapter))
    with patch("wortlaut.cli.default_registry", return_value=registry):
        rc = main(["ingest", "--since", _SINCE, "--no-migrate"])
    assert rc == 0
    assert wired.dip.discover_calls == 1
    assert wired.adapter.discover_calls == 0


def test_unknown_adapter_exit_2(wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]) -> None:
    """AC3: unbekannter Adapter -> rc 2 mit Veruegbarkeitsliste auf stderr,
    Konfiguration bleibt ungelesen (ingest und reparse)."""
    with patch("wortlaut.cli.DbSettings") as db:
        rc = main(["ingest", "--since", _SINCE, "--adapter", "gibt nicht", "--no-migrate"])
    err = capfd.readouterr().err
    assert rc == 2
    assert "Unbekannter Adapter" in err
    assert DEFAULT_ADAPTER in err
    db.assert_not_called()
    with patch("wortlaut.cli.DbSettings") as db:
        rc = main(["reparse", "--adapter", "gibt nicht", "--no-migrate"])
    err = capfd.readouterr().err
    assert rc == 2
    assert "Unbekannter Adapter" in err
    assert DEFAULT_ADAPTER in err
    db.assert_not_called()


def test_adapters_lists_entries(
    wired: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """AC4: `adapters` listet die Eintraege; rights_basis=None -> "je Quelle",
    der Default ist markiert."""
    monkeypatch.delenv("WORTLAUT_DIP_API_KEY", raising=False)
    rc = main(["adapters"])
    out = capfd.readouterr().out
    assert rc == 0
    assert DEFAULT_ADAPTER in out
    assert "version=1.0.0" in out
    assert "trust_level=verified_primary" in out
    assert "rights_basis=amtliches_werk_p5" in out
    assert "(default)" in out
    registry = AdapterRegistry()
    registry.register(_entry(_RegistryFake(_TEST_ADAPTER, None, [])))
    with patch("wortlaut.cli.default_registry", return_value=registry):
        rc = main(["adapters"])
    out = capfd.readouterr().out
    assert rc == 0
    assert "rights_basis=je Quelle" in out


def test_adapter_reads_own_settings(
    wired: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """AC6: der Adapter liest seine eigene Konfiguration; der CLI kennt keine
    Adapter-ENV-Namen (Konfigurationsfehler -> rc 2)."""
    monkeypatch.delenv("WORTLAUT_DIP_API_KEY", raising=False)
    rc = main(["ingest", "--since", _SINCE, "--no-migrate"])
    err = capfd.readouterr().err
    assert rc == 2
    assert "Konfiguration fehlgeschlagen" in err
    assert "api_key" in err
    cli_source = Path(__file__).resolve().parents[2] / "src" / "wortlaut" / "cli.py"
    assert "WORTLAUT_DIP" not in cli_source.read_text(encoding="utf-8")


# ── AC9, AC10, AC11, AC12 ────────────────────────────────────────────────


class _NoRightsAdapter:
    name = DEFAULT_ADAPTER
    version = "2.0.0"
    trust_level = "secondary"

    def __init__(self, refs: list[SourceRef]) -> None:
        self.refs = refs
        self.discover_calls = 0
        self.aclose_calls = 0

    async def discover(self, since: datetime) -> list[SourceRef]:
        self.discover_calls += 1
        return self.refs

    async def aclose(self) -> None:
        self.aclose_calls += 1


def _ref(url: str, rights_basis: str | None = None) -> SourceRef:
    return SourceRef(url, "plenarprotokoll", {}, rights_basis=rights_basis)


def _ns(**kw: object) -> Namespace:
    base: dict[str, object] = {
        "since": datetime(2024, 1, 1),
        "adapter": DEFAULT_ADAPTER,
        "rights_basis": None,
        "limit": None,
        "no_migrate": True,
        "dry_run": False,
    }
    base.update(kw)
    return Namespace(**base)


async def test_adapter_default_used_without_option(wired: SimpleNamespace) -> None:
    """AC9: ohne --rights-basis gilt der Adapter-Default, nicht der alte CLI-Default."""
    fake = _RegistryFake(DEFAULT_ADAPTER, _LIZENZ, [_ref("https://a.example/p1.pdf")])
    registry = AdapterRegistry()
    registry.register(_entry(fake))
    with patch("wortlaut.cli.default_registry", return_value=registry):
        rc = await _run(_ns())
    assert rc == 0
    assert wired.ingest.call_args_list[0].kwargs["rights_basis"] == _LIZENZ


async def test_explicit_option_overrides(wired: SimpleNamespace) -> None:
    """AC9: --rights-basis uebersteuert den Adapter-Default."""
    fake = _RegistryFake(DEFAULT_ADAPTER, _LIZENZ, [_ref("https://a.example/p2.pdf")])
    registry = AdapterRegistry()
    registry.register(_entry(fake))
    with patch("wortlaut.cli.default_registry", return_value=registry):
        rc = await _run(_ns(rights_basis="zitat_p51"))
    assert rc == 0
    assert wired.ingest.call_args_list[0].kwargs["rights_basis"] == "zitat_p51"


async def test_missing_rights_basis_exit_2(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC10: keine Rechtsgrundlage (Adapter None, Ref ohne Angabe) -> Exit 2, nichts erfasst."""
    fake = _RegistryFake(DEFAULT_ADAPTER, None, [_ref("https://a.example/p3.pdf")])
    registry = AdapterRegistry()
    registry.register(_entry(fake))
    with patch("wortlaut.cli.default_registry", return_value=registry):
        rc = await _run(_ns())
    err = capfd.readouterr().err
    assert rc == 2
    assert wired.ingest.call_count == 0
    assert "rights_basis fehlt" in err
    assert fake.aclose_calls == 1


async def test_missing_rights_basis_dry_run_exit_2(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC10: die Rechtsgrundlagen-Pruefung gilt auch im Dry-Run (Exit 2, nichts erfasst)."""
    fake = _RegistryFake(DEFAULT_ADAPTER, None, [_ref("https://a.example/p4.pdf")])
    registry = AdapterRegistry()
    registry.register(_entry(fake))
    with patch("wortlaut.cli.default_registry", return_value=registry):
        rc = await _run(_ns(dry_run=True))
    err = capfd.readouterr().err
    assert rc == 2
    assert wired.ingest.call_count == 0
    assert "rights_basis fehlt" in err
    assert fake.aclose_calls == 1


async def test_adapter_without_attribute_exit_2(wired: SimpleNamespace) -> None:
    """AC10: Adapter ohne rights_basis-Attribut -> 'keine Angabe', Exit 2, nichts erfasst."""
    fake = _NoRightsAdapter([_ref("https://a.example/p5.pdf")])

    def create() -> IngestAdapter:
        return cast(IngestAdapter, fake)

    registry = AdapterRegistry()
    registry.register(
        AdapterEntry(
            name=DEFAULT_ADAPTER,
            version=fake.version,
            trust_level=fake.trust_level,
            rights_basis=None,
            create=create,
        )
    )
    with patch("wortlaut.cli.default_registry", return_value=registry):
        rc = await _run(_ns())
    assert rc == 2
    assert wired.ingest.call_count == 0
    assert fake.aclose_calls == 1


async def test_one_of_two_missing_exit_2(wired: SimpleNamespace) -> None:
    """AC10: fehlt die Angabe bei nur einer von zwei Refs -> Exit 2, nichts erfasst."""
    refs = [
        _ref("https://a.example/p6.pdf", _LIZENZ),
        _ref("https://a.example/p7.pdf"),
    ]
    fake = _RegistryFake(DEFAULT_ADAPTER, None, refs)
    registry = AdapterRegistry()
    registry.register(_entry(fake))
    with patch("wortlaut.cli.default_registry", return_value=registry):
        rc = await _run(_ns())
    assert rc == 2
    assert wired.ingest.call_count == 0
    assert fake.aclose_calls == 1


async def test_invalid_rights_basis_exit_2(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC11: Wert ausserhalb der RIGHTS_BASES -> Exit 2, nichts erfasst."""
    fake = _RegistryFake(DEFAULT_ADAPTER, None, [_ref("https://a.example/p8.pdf", "gemeinfrei")])
    registry = AdapterRegistry()
    registry.register(_entry(fake))
    with patch("wortlaut.cli.default_registry", return_value=registry):
        rc = await _run(_ns())
    err = capfd.readouterr().err
    assert rc == 2
    assert wired.ingest.call_count == 0
    assert "rights_basis ungueltig" in err
    assert fake.aclose_calls == 1


def test_invalid_option_rejected_by_argparse() -> None:
    """AC11: --rights-basis mit ungueltigem Wert -> argparse beendet mit Exit 2."""
    argv = ["ingest", "--since", _SINCE, "--rights-basis", "gemeinfrei"]
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2


async def test_per_source_values(wired: SimpleNamespace) -> None:
    """AC12: Adapter-Default None -> jede Ref uebergibt ihren eigenen Wert, in Reihenfolge."""
    refs = [
        _ref("https://a.example/p9.pdf", _AMTLICHES),
        _ref("https://a.example/p10.pdf", _LIZENZ),
    ]
    fake = _RegistryFake(DEFAULT_ADAPTER, None, refs)
    registry = AdapterRegistry()
    registry.register(_entry(fake))
    with patch("wortlaut.cli.default_registry", return_value=registry):
        rc = await _run(_ns())
    assert rc == 0
    assert wired.ingest.call_args_list[0].kwargs["rights_basis"] == _AMTLICHES
    assert wired.ingest.call_args_list[1].kwargs["rights_basis"] == _LIZENZ


async def test_per_source_beats_adapter_default(wired: SimpleNamespace) -> None:
    """AC12: die Quellen-Angabe schlaegt den Adapter-Default (auch ungeklaert)."""
    fake = _RegistryFake(
        DEFAULT_ADAPTER, _AMTLICHES, [_ref("https://a.example/p11.pdf", _UNGEKLAERT)]
    )
    registry = AdapterRegistry()
    registry.register(_entry(fake))
    with patch("wortlaut.cli.default_registry", return_value=registry):
        rc = await _run(_ns())
    assert rc == 0
    assert wired.ingest.call_args_list[0].kwargs["rights_basis"] == _UNGEKLAERT


def test_cli_has_no_rights_basis_default(wired: SimpleNamespace) -> None:
    """AC9 ueber argparse: ``ingest`` ohne ``--rights-basis`` uebergibt die
    Angabe des Adapters — ein wieder eingebauter CLI-Default fiele hier auf."""
    fake = _RegistryFake(DEFAULT_ADAPTER, _LIZENZ, [_ref("https://a.example/p12.pdf")])
    registry = AdapterRegistry()
    registry.register(_entry(fake))
    with patch("wortlaut.cli.default_registry", return_value=registry):
        rc = main(["ingest", "--since", _SINCE, "--no-migrate"])
    assert rc == 0
    assert wired.ingest.call_args_list[0].kwargs["rights_basis"] == _LIZENZ
