"""CLI Unit-Tests AC1-AC8 (+ main/__main__ Coverage) — keine Live-Netz-/DB-Calls."""

from __future__ import annotations

import subprocess
import sys
from argparse import Namespace
from collections.abc import Iterator
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wortlaut.cli import _run, main
from wortlaut.ingest.adapter import SourceRef
from wortlaut.ingest.dip import DipFetchError
from wortlaut.pipeline.ingest import IngestOutcome

# ── Fakes ────────────────────────────────────────────────────────────────


class FakeAdapter:
    name = "fake"
    version = "1.0"
    trust_level = "verified_primary"

    def __init__(self) -> None:
        self.refs: list[SourceRef] = []
        self.discover_exc: Exception | None = None
        self.aclose_called = False

    async def discover(self, since: datetime) -> list[SourceRef]:
        if self.discover_exc is not None:
            raise self.discover_exc
        return self.refs

    async def aclose(self) -> None:
        self.aclose_called = True


class FakeArchiver:
    def __init__(self) -> None:
        self.aclose_called = False

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
        "rights_basis": "amtliches_werk_p5",
        "limit": None,
        "no_migrate": True,
        "dry_run": False,
    }
    base.update(kw)
    return Namespace(**base)


@pytest.fixture
def wired() -> Iterator[SimpleNamespace]:
    """Patcht alle Composition-Root-Deps von wortlaut.cli; gibt Handles zurueck."""
    adapter = FakeAdapter()
    wayback = FakeArchiver()
    atoday = FakeArchiver()
    worm = FakeWorm()
    engine = MagicMock()
    engine.dispose = AsyncMock()
    ingest = AsyncMock(return_value=IngestOutcome("inserted", None, "h", span_count=0))
    with (
        patch("wortlaut.cli.DbSettings", return_value=MagicMock(dsn="f")),
        patch("wortlaut.cli.WormSettings", return_value=MagicMock()),
        patch("wortlaut.cli.DipSettings", return_value=MagicMock()),
        patch("wortlaut.cli.create_async_engine_from", return_value=engine),
        patch("wortlaut.cli.make_sessionmaker", return_value=FakeSessionmaker()),
        patch("wortlaut.cli.DipPlenarprotokollAdapter", return_value=adapter),
        patch("wortlaut.cli.WaybackArchiver", return_value=wayback),
        patch("wortlaut.cli.ArchiveTodayArchiver", return_value=atoday),
        patch("wortlaut.cli.MinioWormStore", return_value=worm),
        patch("wortlaut.cli.upgrade_head", new=AsyncMock()),
        patch("wortlaut.cli.ensure_ingest_adapter", new=AsyncMock()),
        patch("wortlaut.cli.ingest_source", new=ingest),
    ):
        yield SimpleNamespace(
            adapter=adapter,
            wayback=wayback,
            atoday=atoday,
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
    """AC3: [archive_failed, inserted] -> kein Abbruch, rc 0."""
    wired.adapter.refs = [_ref("http://a/p1.pdf"), _ref("http://b/p2.pdf")]
    wired.ingest.side_effect = [
        IngestOutcome("archive_failed", None, "h1", span_count=0),
        IngestOutcome("inserted", None, "h2", span_count=0),
    ]
    rc = await _run(_ns())
    out = capfd.readouterr().out
    assert rc == 0
    assert wired.ingest.call_count == 2
    assert "inserted=1" in out
    assert "archive_failed=1" in out


async def test_fetch_error_caught(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC4: DipFetchError -> gefangen (fetch_error=1), Rest laeuft, rc 0."""
    wired.adapter.refs = [_ref("http://a/p1.pdf"), _ref("http://b/p2.pdf")]
    wired.ingest.side_effect = [
        DipFetchError("net"),
        IngestOutcome("inserted", None, "h2", span_count=0),
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
    with patch("wortlaut.cli.DipSettings", side_effect=RuntimeError("no api key")):
        rc = await _run(_ns())
    assert rc != 0
    assert wired.ingest.call_count == 0
    assert "Konfiguration" in capfd.readouterr().err


async def test_resources_closed_in_finally(wired: SimpleNamespace) -> None:
    """AC6: aclose/dispose je 1x, auch wenn discover wirft."""
    wired.adapter.discover_exc = DipFetchError("boom")
    rc = await _run(_ns())
    assert rc == 2  # discover fehlgeschlagen
    assert wired.adapter.aclose_called
    assert wired.wayback.aclose_called
    assert wired.atoday.aclose_called
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
