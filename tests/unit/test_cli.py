"""CLI Unit-Tests AC1-AC8 (+ main/__main__ Coverage, #132: Ingest ohne Archiv)
— keine Live-Netz-/DB-Calls."""

from __future__ import annotations

import subprocess
import sys
from argparse import Namespace
from collections.abc import Iterator
from datetime import datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wortlaut.cli import _run, main
from wortlaut.ingest import registry as registry_module
from wortlaut.ingest.adapter import IngestAdapter, SourceRef
from wortlaut.ingest.dip import DipFetchError
from wortlaut.ingest.registry import DEFAULT_ADAPTER, AdapterEntry, AdapterRegistry
from wortlaut.pipeline.ingest import IngestOutcome

# ── Fakes ────────────────────────────────────────────────────────────────


class FakeAdapter:
    name = "fake"
    version = "1.0"
    trust_level = "verified_primary"
    rights_basis = "amtliches_werk_p5"

    def __init__(self) -> None:
        self.refs: list[SourceRef] = []
        self.discover_exc: Exception | None = None
        self.aclose_called = False
        self.discover_calls = 0
        self.fetch_calls = 0

    async def discover(self, since: datetime) -> list[SourceRef]:
        self.discover_calls += 1
        if self.discover_exc is not None:
            raise self.discover_exc
        return self.refs

    async def fetch(self, ref: SourceRef) -> object:
        self.fetch_calls += 1
        return object()

    async def aclose(self) -> None:
        self.aclose_called = True


class FakeWorm:
    def __init__(self) -> None:
        self.ensure_bucket_called = False

    async def ensure_bucket(self) -> None:
        self.ensure_bucket_called = True


class FakeSession:
    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_a: object) -> bool:
        return False

    async def commit(self) -> None:
        pass


class FakeSessionmaker:
    def __call__(self) -> FakeSession:
        return FakeSession()


def _ref(url: str) -> SourceRef:
    return SourceRef(url, "plenarprotokoll", {})


def _ns(**kw: object) -> Namespace:
    base: dict[str, object] = {
        "since": datetime(2024, 1, 1),
        "adapter": "dip-api",
        "rights_basis": "amtliches_werk_p5",
        "limit": None,
        "no_migrate": True,
        "dry_run": False,
    }
    base.update(kw)
    return Namespace(**base)


def _registry_with(adapter: object) -> AdapterRegistry:
    """Registry, deren Default-Eintrag genau ``adapter`` liefert."""
    typed = cast(IngestAdapter, adapter)
    registry = AdapterRegistry()
    registry.register(
        AdapterEntry(
            name=DEFAULT_ADAPTER,
            version=typed.version,
            trust_level=typed.trust_level,
            rights_basis=getattr(adapter, "rights_basis", None),
            create=lambda: typed,
        )
    )
    return registry


def _credentials_env(
    monkeypatch: pytest.MonkeyPatch, *, access: str | None, secret: str | None
) -> None:
    """Setzt/löscht die IA-Zugangsdaten-ENV (zusammengesetzt, S6698)."""
    monkeypatch.delenv("WORTLAUT_ARCHIVE_IA_ACCESS_KEY", raising=False)
    monkeypatch.delenv("WORTLAUT_ARCHIVE_IA_SECRET", raising=False)
    if access is not None:
        monkeypatch.setenv("WORTLAUT_ARCHIVE_IA_ACCESS_KEY", access)
    if secret is not None:
        monkeypatch.setenv("WORTLAUT_ARCHIVE_IA_SECRET", secret)


@pytest.fixture
def wired() -> Iterator[SimpleNamespace]:
    """Patcht alle Composition-Root-Deps von wortlaut.cli; gibt Handles zurück.

    Seit #132 (ADR-0009) liest ``_run`` nur noch Db-/Worm-/Dip-Settings —
    keine Internet-Archive-Zugangsdaten, keine Archivare, kein Pre-Flight.
    """
    adapter = FakeAdapter()
    worm = FakeWorm()
    engine = MagicMock()
    engine.dispose = AsyncMock()
    ingest = AsyncMock(return_value=IngestOutcome("inserted", None, "h"))
    with (
        patch("wortlaut.cli.DbSettings", return_value=MagicMock(dsn="f")),
        patch("wortlaut.cli.WormSettings", return_value=MagicMock()),
        patch("wortlaut.cli.create_async_engine_from", return_value=engine),
        patch("wortlaut.cli.make_sessionmaker", return_value=FakeSessionmaker()),
        patch("wortlaut.cli.default_registry", return_value=_registry_with(adapter)),
        patch("wortlaut.cli.MinioWormStore", return_value=worm),
        patch("wortlaut.cli.upgrade_head", new=AsyncMock()),
        patch("wortlaut.cli.ensure_ingest_adapter", new=AsyncMock()),
        patch("wortlaut.cli.ingest_source", new=ingest),
    ):
        yield SimpleNamespace(
            adapter=adapter,
            worm=worm,
            engine=engine,
            ingest=ingest,
        )


# ── AC1-AC8 ──────────────────────────────────────────────────────────────


async def test_ingest_loops_per_ref(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC1: 2 Refs -> ingest_source 2x, discovered=2."""
    wired.adapter.refs = [_ref("http://a/p1.pdf"), _ref("http://b/p2.pdf")]
    rc = await _run(_ns())
    assert rc == 0
    assert wired.ingest.call_count == 2
    assert "discovered=2" in capfd.readouterr().out


async def test_empty_discover_noop(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC2: 0 Refs -> rc 0, ingest_source 0x."""
    rc = await _run(_ns())
    assert rc == 0
    assert wired.ingest.call_count == 0
    assert "discovered=0" in capfd.readouterr().out


async def test_partial_outcomes_dont_abort(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC3: [skipped_duplicate, inserted] -> kein Abbruch, rc 0."""
    wired.adapter.refs = [_ref("http://a/p1.pdf"), _ref("http://b/p2.pdf")]
    wired.ingest.side_effect = [
        IngestOutcome("skipped_duplicate", None, "h1"),
        IngestOutcome("inserted", None, "h2"),
    ]
    rc = await _run(_ns())
    out = capfd.readouterr().out
    assert rc == 0
    assert wired.ingest.call_count == 2
    assert "inserted=1" in out
    assert "skipped_duplicate=1" in out


async def test_fetch_error_caught(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC4: DipFetchError -> gefangen (fetch_error=1), Rest laeuft, rc 0."""
    wired.adapter.refs = [_ref("http://a/p1.pdf"), _ref("http://b/p2.pdf")]
    wired.ingest.side_effect = [
        DipFetchError("net"),
        IngestOutcome("inserted", None, "h2"),
    ]
    rc = await _run(_ns())
    out = capfd.readouterr().out
    assert rc == 0
    assert wired.ingest.call_count == 2
    assert "fetch_error=1" in out
    assert "inserted=1" in out


async def test_missing_env_exits_nonzero(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC5: Pflicht-Config fehlt -> rc != 0, kein ingest_source."""
    with (
        patch("wortlaut.cli.default_registry", new=registry_module.default_registry),
        patch("wortlaut.ingest.dip.DipSettings", side_effect=RuntimeError("no api key")),
    ):
        rc = await _run(_ns())
    assert rc != 0
    assert wired.ingest.call_count == 0
    assert "Konfiguration" in capfd.readouterr().err


async def test_resources_closed_in_finally(wired: SimpleNamespace) -> None:
    """AC6: Adapter-aclose und Engine-dispose je 1x, auch wenn discover wirft."""
    wired.adapter.discover_exc = DipFetchError("boom")
    rc = await _run(_ns())
    assert rc == 2  # discover fehlgeschlagen
    assert wired.adapter.aclose_called
    wired.engine.dispose.assert_awaited_once()


async def test_dry_run_no_ingest(wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]) -> None:
    """AC7: --dry-run -> discover laeuft, ingest_source 0x, dry_run=True."""
    wired.adapter.refs = [_ref("http://a/p1.pdf"), _ref("http://b/p2.pdf")]
    rc = await _run(_ns(dry_run=True))
    assert rc == 0
    assert wired.ingest.call_count == 0
    assert "dry_run=True" in capfd.readouterr().out


async def test_limit_caps_and_logs(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC8: 3 Refs, --limit 1 -> ingest_source 1x, Kappungs-Log auf stderr."""
    wired.adapter.refs = [
        _ref("http://a/p1.pdf"),
        _ref("http://b/p2.pdf"),
        _ref("http://c/p3.pdf"),
    ]
    rc = await _run(_ns(limit=1))
    cap = capfd.readouterr()
    assert rc == 0
    assert wired.ingest.call_count == 1
    assert "kappe" in cap.err
    assert "discovered=1" in cap.out


# ── main() / __main__ (Argparse + Entrypoint-Coverage) ───────────────────


def test_main_no_subcommand_returns_2(capfd: pytest.CaptureFixture[str]) -> None:
    """main() ohne Subcommand -> rc 2."""
    assert main([]) == 2
    assert "ingest" in capfd.readouterr().err


def test_main_missing_since_exits() -> None:
    """argparse: fehlendes Pflicht-Arg --since -> SystemExit(2)."""
    with pytest.raises(SystemExit):
        main(["ingest"])


def test_main_dispatches_to_run(wired: SimpleNamespace) -> None:
    """main() parst und dispatcht via asyncio.run an _run (dry-run -> rc 0)."""
    assert main(["ingest", "--since", "2024-01-01", "--dry-run"]) == 0


def test_module_entrypoint_no_subcommand() -> None:
    """`python -m wortlaut` ohne Subcommand -> Exit 2 (deckt __main__.py)."""
    result = subprocess.run(
        [sys.executable, "-m", "wortlaut"],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2


# ── #132 AC4: Ingest ohne Internet Archive ───────────────────────────────


async def test_ingest_needs_no_ia_credentials(
    wired: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """AC4: keine WORTLAUT_ARCHIVE_IA_* in der ENV -> der Lauf geht trotzdem
    durch (kein Exit 2 mehr, ADR-0009) und meldet die neue Summary-Zeile."""
    _credentials_env(monkeypatch, access=None, secret=None)
    wired.adapter.refs = [_ref("http://a/p1.pdf")]
    rc = await _run(_ns())
    cap = capfd.readouterr()
    assert rc == 0
    assert "Konfiguration" not in cap.err
    assert wired.ingest.call_count == 1
    assert "discovered=1 inserted=1 skipped_duplicate=0 fetch_error=0" in cap.out


async def test_ingest_never_builds_archivers(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC4: ``_run`` ruft keinen Pre-Flight und baut keine Archivare (Patches
    auf ``_build_archivers``/``_preflight_ok`` mit Zählern = 0)."""
    build_archivers = MagicMock()
    preflight = AsyncMock(return_value=True)
    with (
        patch("wortlaut.cli._build_archivers", build_archivers),
        patch("wortlaut.cli._preflight_ok", preflight),
    ):
        wired.adapter.refs = [_ref("http://a/p1.pdf"), _ref("http://b/p2.pdf")]
        rc = await _run(_ns())
    out = capfd.readouterr().out
    assert rc == 0
    assert build_archivers.call_count == 0
    assert preflight.call_count == 0
    assert "discovered=2 inserted=2 skipped_duplicate=0 fetch_error=0" in out


async def test_dry_run_skips_probe(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC5 (angepasst, #132 §4.5): --dry-run → kein Probe-Call — seit #132 baut
    ``ingest`` gar keine Archivare (Zähler = 0), Dry-Run-Zeile wie in §4.2."""
    build_archivers = MagicMock()
    preflight = AsyncMock(return_value=True)
    with (
        patch("wortlaut.cli._build_archivers", build_archivers),
        patch("wortlaut.cli._preflight_ok", preflight),
    ):
        wired.adapter.refs = [_ref("http://a/p1.pdf"), _ref("http://b/p2.pdf")]
        rc = await _run(_ns(dry_run=True))
    cap = capfd.readouterr()
    assert rc == 0
    assert build_archivers.call_count == 0
    assert preflight.call_count == 0
    assert "dry_run=True" in cap.out  # Dry-Run-Zeile wörtlich unverändert


# ── #132 §4.2: Summary-Zeile in Feld-Reihenfolge ─────────────────────────


async def test_summary_line_field_order(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC4: genau eine Summary-Zeile, Felder in der Reihenfolge aus
    Spec 0132 §4.2: discovered= inserted= skipped_duplicate= fetch_error=
    (``archive_failed``, ``spans_total`` und ``reasons`` entfallen)."""
    wired.adapter.refs = [
        _ref("http://a/p1.pdf"),
        _ref("http://b/p2.pdf"),
        _ref("http://c/p3.pdf"),
    ]
    wired.ingest.side_effect = [
        IngestOutcome("inserted", None, "h1"),
        IngestOutcome("skipped_duplicate", None, "h2"),
        DipFetchError("net"),
    ]
    rc = await _run(_ns())
    cap = capfd.readouterr()
    assert rc == 0
    assert cap.out == "discovered=3 inserted=1 skipped_duplicate=1 fetch_error=1\n"


async def test_summary_reports_dash_when_no_failures(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC8 (angepasst, #132 §4.2): ``reasons=`` entfällt mit dem Archiv — bei
    einem sauberen Lauf ist die Zeile exakt ``discovered=1 inserted=1
    skipped_duplicate=0 fetch_error=0`` (statt ``reasons=-``)."""
    wired.adapter.refs = [_ref("http://a/p1.pdf")]
    rc = await _run(_ns())
    out = capfd.readouterr().out
    assert rc == 0
    assert out == "discovered=1 inserted=1 skipped_duplicate=0 fetch_error=0\n"


async def test_summary_reports_failure_reasons(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC8 (angepasst, #132 §4.2): die Gründe-Verteilung entfällt mit dem
    Archiv im Ingest — die Zeile zählt stattdessen die Ausfall-Klasse
    ``fetch_error`` pro Quelle."""
    wired.adapter.refs = [_ref(f"http://a/p{i}.pdf") for i in range(1, 5)]
    wired.ingest.side_effect = [
        IngestOutcome("inserted", None, "h1"),
        IngestOutcome("inserted", None, "h2"),
        IngestOutcome("skipped_duplicate", None, "h3"),
        DipFetchError("net"),
    ]
    rc = await _run(_ns())
    out = capfd.readouterr().out
    assert rc == 0
    assert out == "discovered=4 inserted=2 skipped_duplicate=1 fetch_error=1\n"


# ── #108 (weiter gültig): Dry-Run ohne Zugangsdaten ──────────────────────


async def test_dry_run_ohne_zugangsdaten_ok(
    wired: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC17 (weiter gültig): keine IA-Zugangsdaten, ABER --dry-run -> rc 0
    (Dry-Run archiviert nicht und braucht keine Zugangsdaten)."""
    _credentials_env(monkeypatch, access=None, secret=None)
    wired.adapter.refs = [_ref("http://a/p1.pdf"), _ref("http://b/p2.pdf")]
    rc = await _run(_ns(dry_run=True))
    out = capfd.readouterr().out
    assert rc == 0
    assert "dry_run=True" in out
    assert wired.ingest.call_count == 0
