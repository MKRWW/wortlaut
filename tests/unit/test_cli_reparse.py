"""Unit (Spec 0118): CLI-Subcommand ``reparse`` — Summary/Exit-Codes (AC8, AC9).

Keine Live-Netz-/DB-Calls: alle Composition-Root-Deps von ``wortlaut.cli`` werden
patcht; ``reparse_source`` liefert gebrachte ``ReparseOutcome``s,
``list_sources_without_spans`` gebrachte spanlose Quellen.
"""

from __future__ import annotations

import uuid
from argparse import Namespace
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from wortlaut.cli import _run_reparse
from wortlaut.pipeline.reparse import ReparseOutcome
from wortlaut.store.reparse import SpanlessSource

# ── Fakes ────────────────────────────────────────────────────────────────


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


class FakeWorm:
    async def ensure_bucket(self) -> None:
        pass

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        raise AssertionError("not used (reparse_source ist patcht)")

    async def get(self, ref: str) -> bytes:
        raise AssertionError("not used (reparse_source ist patcht)")


class FakeDipAdapter:
    name = "dip-api"
    version = "1.0.0"
    trust_level = "verified_primary"
    aclose_calls = 0

    async def aclose(self) -> None:
        self.aclose_calls += 1


def _ns(**kw: object) -> Namespace:
    base: dict[str, object] = {
        "limit": None,
        "no_migrate": True,
        "dry_run": False,
    }
    base.update(kw)
    return Namespace(**base)


def _spanless(i: int) -> SpanlessSource:
    return SpanlessSource(
        source_id=uuid.UUID(int=i),
        content_hash=f"{i:02x}" * 32,
        raw_bytes_ref=f"s3://bucket/{i}?versionId=1",
        origin_url=f"https://dserver.bundestag.de/{i}.pdf",
        source_type="plenarprotokoll",
        mime_type="application/pdf",
        retrieved_at=datetime(2026, 8, 5, tzinfo=UTC),
        normalized_text="text",
    )


def _reparsed(i: int) -> ReparseOutcome:
    return ReparseOutcome("reparsed", uuid.UUID(int=i))


def _still_empty(i: int) -> ReparseOutcome:
    return ReparseOutcome("still_empty", uuid.UUID(int=i))


def _no_text(i: int) -> ReparseOutcome:
    return ReparseOutcome("no_text", uuid.UUID(int=i))


def _skipped(i: int) -> ReparseOutcome:
    return ReparseOutcome("skipped_has_spans", uuid.UUID(int=i))


def _hash_mismatch(i: int) -> ReparseOutcome:
    return ReparseOutcome("hash_mismatch", uuid.UUID(int=i))


def _worm_missing(i: int) -> ReparseOutcome:
    return ReparseOutcome("worm_missing", uuid.UUID(int=i))


def _error(i: int) -> ReparseOutcome:
    return ReparseOutcome("error", uuid.UUID(int=i))


@pytest.fixture
def wired() -> Iterator[SimpleNamespace]:
    """Patcht die Composition-Root-Deps von ``wortlaut.cli`` für den Span-Nachzug."""
    engine = MagicMock()
    engine.dispose = AsyncMock()
    adapter = FakeDipAdapter()
    reparse = AsyncMock()
    list_spanless = AsyncMock(return_value=[])
    with (
        patch("wortlaut.cli.DbSettings", return_value=MagicMock(dsn="f")),
        patch("wortlaut.cli.WormSettings", return_value=MagicMock()),
        patch("wortlaut.cli.DipSettings", return_value=MagicMock(api_key="k")),
        patch("wortlaut.cli.create_async_engine_from", return_value=engine),
        patch("wortlaut.cli.make_sessionmaker", return_value=FakeSessionmaker()),
        patch("wortlaut.cli.MinioWormStore", return_value=FakeWorm()),
        patch("wortlaut.cli.upgrade_head", new=AsyncMock()),
        patch("wortlaut.cli.DipPlenarprotokollAdapter", return_value=adapter),
        patch("wortlaut.cli.list_sources_without_spans", new=list_spanless),
        patch("wortlaut.cli.reparse_source", new=reparse),
    ):
        yield SimpleNamespace(
            engine=engine,
            adapter=adapter,
            reparse_source=reparse,
            list_pending=list_spanless,
        )


# ── AC8: dry-run-Zeile ───────────────────────────────────────────────────


async def test_dry_run_line(wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]) -> None:
    """AC8: --dry-run gibt pending=<n> dry_run=True aus und schreibt nichts, Exit 0."""
    wired.list_pending.return_value = [_spanless(1), _spanless(2)]
    rc = await _run_reparse(_ns(dry_run=True))
    out = capfd.readouterr().out
    assert rc == 0
    assert out == "pending=2 dry_run=True\n"
    assert wired.reparse_source.call_count == 0


# ── AC8: genau eine Ergebniszeile, fester Feld-Reihenfolge ───────────────


async def test_summary_line_field_order(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC8: Der echte Lauf gibt genau eine Zeile aus, Felder in dieser Reihenfolge:
    pending= reparsed= spans_total= still_empty= no_text= skipped_has_spans=
    hash_mismatch= worm_missing= error=."""
    wired.list_pending.return_value = [_spanless(1), _spanless(2), _spanless(3)]
    wired.reparse_source.side_effect = [
        ReparseOutcome("reparsed", uuid.UUID(int=1), span_count=2),
        ReparseOutcome("still_empty", uuid.UUID(int=2)),
        ReparseOutcome("no_text", uuid.UUID(int=3)),
    ]
    rc = await _run_reparse(_ns())
    cap = capfd.readouterr()
    assert rc == 0
    assert cap.out == (
        "pending=3 reparsed=1 spans_total=2 still_empty=1 no_text=1 "
        "skipped_has_spans=0 hash_mismatch=0 worm_missing=0 error=0\n"
    )
    assert cap.err == ""


# ── AC8: --limit wird an die Auswahl durchgereicht ───────────────────────


async def test_limit_passed_through(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC8: --limit N begrenzt die Auswahl auf N Quellen (limit wird durchgereicht)."""
    rc = await _run_reparse(_ns(limit=3))
    assert rc == 0
    wired.list_pending.assert_awaited_once_with(ANY, adapter_name="dip-api", limit=3)
    assert capfd.readouterr().out == (
        "pending=0 reparsed=0 spans_total=0 still_empty=0 no_text=0 "
        "skipped_has_spans=0 hash_mismatch=0 worm_missing=0 error=0\n"
    )


# ── AC9: Exit-Codes ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("factories", "expected"),
    [
        ([_reparsed], 0),
        ([_still_empty], 0),
        ([_no_text], 0),
        ([_skipped], 0),
        ([_worm_missing], 0),
        ([_hash_mismatch], 4),
        ([_error], 1),
        ([_hash_mismatch, _error], 4),
        ([_error, _hash_mismatch], 4),
    ],
)
async def test_exit_codes(
    wired: SimpleNamespace,
    capfd: pytest.CaptureFixture[str],
    factories: list[Callable[[int], ReparseOutcome]],
    expected: int,
) -> None:
    """AC9: 0 im Normalfall (auch bei still_empty/no_text/worm_missing/
    skipped_has_spans) · 4, sobald hash_mismatch > 0 (Vorrang) · sonst 1,
    sobald error > 0."""
    wired.list_pending.return_value = [_spanless(i) for i in range(1, len(factories) + 1)]
    wired.reparse_source.side_effect = [factory(i) for i, factory in enumerate(factories, 1)]
    rc = await _run_reparse(_ns())
    assert rc == expected
    assert wired.reparse_source.call_count == len(factories)


async def test_config_error_exits_two(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC9: Fehlende Konfiguration → Exit 2, Meldung ohne Werte (R-SEC-01)."""
    exc = ValueError("fehlende ENV: WORTLAUT_DIP_API_KEY")
    with patch("wortlaut.cli.DipSettings", side_effect=exc):
        rc = await _run_reparse(_ns())
    assert rc == 2
    assert "Konfiguration" in capfd.readouterr().err
    assert wired.reparse_source.call_count == 0
