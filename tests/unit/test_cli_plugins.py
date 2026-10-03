"""Unit (Spec 0141, AC4-AC6): CLI mit einem echten Plugin-Paket in tmp_path."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wortlaut.cli import main

_PLUGIN = "landtag-probe"
_SINCE = "2024-01-01"

_MODULE_SOURCE = '''"""Probe-Modul fuer den echten importlib-Weg."""

from datetime import datetime

from wortlaut.ingest.adapter import AdapterError, SourceRef
from wortlaut.ingest.registry import AdapterEntry


class ProbeAdapter:
    name = "landtag-probe"
    version = "0.1.0"
    trust_level = "verified_primary"
    rights_basis = "amtliches_werk_p5"

    async def discover(self, since: datetime) -> list[SourceRef]:
        return []

    async def fetch(self, ref: SourceRef) -> object:
        raise AdapterError("x")

    def normalize(self, raw: object) -> str:
        return ""

    def parse(self, raw: object, normalized: str) -> list[object]:
        return []

    async def aclose(self) -> None:
        return None


ENTRY = AdapterEntry(
    name="landtag-probe",
    version="0.1.0",
    trust_level="verified_primary",
    rights_basis="amtliches_werk_p5",
    create=ProbeAdapter,
)
'''


class _FakeSession:
    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_a: object) -> bool:
        return False

    async def commit(self) -> None:
        return None


class _FakeSessionmaker:
    def __call__(self) -> _FakeSession:
        return _FakeSession()


@pytest.fixture
def plugin_pkg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    dist_info = tmp_path / "wl_probe_plugin-0.1.dist-info"
    dist_info.mkdir()
    (tmp_path / "wl_probe_plugin.py").write_text(_MODULE_SOURCE, encoding="utf-8")
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: wl-probe-plugin\nVersion: 0.1\n", encoding="utf-8"
    )
    (dist_info / "entry_points.txt").write_text(
        "[wortlaut.adapters]\nlandtag-probe = wl_probe_plugin:ENTRY\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "wl_probe_plugin", raising=False)
    monkeypatch.setenv("WORTLAUT_ADAPTER_PLUGINS", _PLUGIN)
    return _PLUGIN


def test_adapters_shows_capped_plugin(plugin_pkg: str, capfd: pytest.CaptureFixture[str]) -> None:
    """AC5/AC6: gedeckeltes Plugin zeigt sein deklariertes Vertrauen und (plugin)."""
    rc = main(["adapters"])
    out = capfd.readouterr().out
    assert rc == 0
    assert _PLUGIN in out
    assert "trust_level=secondary (gedeckelt, deklariert verified_primary)" in out
    assert "(plugin)" in out


def test_ingest_records_capped_trust(plugin_pkg: str) -> None:
    """AC6: ingest reicht den gedeckelten trust_level an ensure_ingest_adapter weiter."""
    ensure = AsyncMock()
    with (
        patch("wortlaut.cli.DbSettings", return_value=MagicMock(dsn="f")),
        patch("wortlaut.cli.WormSettings", return_value=MagicMock()),
        patch("wortlaut.cli.create_async_engine_from", return_value=AsyncMock()),
        patch("wortlaut.cli.make_sessionmaker", return_value=_FakeSessionmaker()),
        patch("wortlaut.cli.MinioWormStore", return_value=AsyncMock()),
        patch("wortlaut.cli.upgrade_head", new=AsyncMock()),
        patch("wortlaut.cli.ensure_ingest_adapter", new=ensure),
        patch("wortlaut.cli.ingest_source", new=AsyncMock()),
    ):
        rc = main(["ingest", "--since", _SINCE, "--adapter", _PLUGIN, "--no-migrate"])
    assert rc == 0
    assert ensure.await_args is not None
    assert ensure.await_args.kwargs["trust_level"] == "secondary"


def test_plugin_misconfiguration_exit_2(
    monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC4: nicht existierendes Plugin -> rc 2, fuer adapters und ingest."""
    monkeypatch.setenv("WORTLAUT_ADAPTER_PLUGINS", "gibt-es-nicht")
    rc = main(["adapters"])
    err = capfd.readouterr().err
    assert rc == 2
    assert "Plugin-Konfiguration fehlgeschlagen" in err
    rc = main(["ingest", "--since", _SINCE, "--no-migrate"])
    err = capfd.readouterr().err
    assert rc == 2
    assert "Plugin-Konfiguration fehlgeschlagen" in err
