"""Unit (Spec 0132 §4.4, AC5): CLI-Subcommand ``status`` — nur lesend.

Keine Live-Netz-/DB-Calls: alle Composition-Root-Deps von ``wortlaut.cli``
werden patcht; ``backlog_counts`` liefert gebrachte ``BacklogCounts``.
Kern-Aussage: ``status`` fährt **keine** Migration (``upgrade_head`` 0×).
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wortlaut.cli import _run_status
from wortlaut.store.status import BacklogCounts

# ── Fakes ────────────────────────────────────────────────────────────────


class FakeSession:
    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_a: object) -> bool:
        return False


class FakeSessionmaker:
    def __call__(self) -> FakeSession:
        return FakeSession()


def _counts() -> BacklogCounts:
    return BacklogCounts(
        sources=5,
        unstamped=1,
        unattested=2,
        unattested_capture_failed=1,
        attested_without_spans=1,
    )


@pytest.fixture
def wired() -> Iterator[SimpleNamespace]:
    """Patcht die Composition-Root-Deps von ``wortlaut.cli`` für den Status-Pass."""
    engine = MagicMock()
    engine.dispose = AsyncMock()
    counts = AsyncMock(return_value=_counts())
    upgrade = AsyncMock()
    with (
        patch("wortlaut.cli.DbSettings", return_value=MagicMock(dsn="f")),
        patch("wortlaut.cli.create_async_engine_from", return_value=engine),
        patch("wortlaut.cli.make_sessionmaker", return_value=FakeSessionmaker()),
        patch("wortlaut.cli.upgrade_head", new=upgrade),
        patch("wortlaut.cli.backlog_counts", new=counts),
    ):
        yield SimpleNamespace(engine=engine, counts=counts, upgrade=upgrade)


# ── AC5: nur lesend — keine Migration ────────────────────────────────────


async def test_status_does_not_migrate(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC5: ``status`` liest den Rückstand und fährt **keine** Migration
    (``upgrade_head`` 0×), gibt die fünf Felder in fester Reihenfolge aus
    und beendet die Engine (``dispose`` 1×)."""
    rc = await _run_status()
    cap = capfd.readouterr()
    assert rc == 0
    assert wired.upgrade.call_count == 0  # keine Migration
    wired.counts.assert_awaited_once()
    wired.engine.dispose.assert_awaited_once()
    assert (
        cap.out == "sources=5 unstamped=1 unattested=2 unattested_capture_failed=1 "
        "attested_without_spans=1\n"
    )


async def test_status_config_error_exit_two(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """Konfiguration fehlt → Exit 2, Meldung auf stderr, keine Ausgabe,
    keine Engine (``dispose`` nie), keine Abfrage des Rückstands."""
    with patch("wortlaut.cli.DbSettings", side_effect=RuntimeError("no dsn")):
        rc = await _run_status()
    cap = capfd.readouterr()
    assert rc == 2
    assert "Konfiguration" in cap.err
    assert cap.out == ""
    wired.engine.dispose.assert_not_awaited()
    wired.counts.assert_not_awaited()
