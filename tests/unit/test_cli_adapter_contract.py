"""Unit (Spec 0095, AC4–AC7): Minimal-Adapter erfüllt genau das
``IngestAdapter``-Protocol; der Kern fängt nur ``AdapterError`` und bleibt
auf Kernfehlern stehen (kein ``fetch_error``)."""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Iterator, Sequence
from datetime import datetime
from types import SimpleNamespace
from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wortlaut.cli import _run
from wortlaut.ingest.adapter import AdapterError, RawSource, SourceRef, SpanDraft
from wortlaut.pipeline.ingest import IngestOutcome

INSERTED: Literal["inserted", "skipped_duplicate"] = "inserted"


class MinimalAdapter:
    """Erfüllt genau die Protocol-Mitglieder (keine weiteren Methoden,
    keine Vererbung vom DIP-Adapter); zählt die Aufrufe."""

    name = "minimal"
    version = "1.0.0"
    trust_level = "verified_primary"

    def __init__(self) -> None:
        self.refs: list[SourceRef] = []
        self.discover_exc: Exception | None = None
        self.discover_calls = 0
        self.aclose_calls = 0

    async def discover(self, since: datetime) -> Sequence[SourceRef]:
        self.discover_calls += 1
        if self.discover_exc is not None:
            raise self.discover_exc
        return self.refs

    async def fetch(self, ref: SourceRef) -> RawSource:
        raise AssertionError("fetch wird im Ingest-Loop nicht gerufen")

    def normalize(self, raw: RawSource) -> str:
        raise AssertionError("normalize wird im Ingest-Loop nicht gerufen")

    def parse(self, raw: RawSource, normalized: str) -> Sequence[SpanDraft]:
        raise AssertionError("parse wird im Ingest-Loop nicht gerufen")

    async def aclose(self) -> None:
        self.aclose_calls += 1


class FakeWorm:
    async def ensure_bucket(self) -> None:
        pass


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


def _ref(number: int) -> SourceRef:
    return SourceRef(f"http://a/p{number}.pdf", "plenarprotokoll", {})


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
    """Patcht die Composition-Root-Deps von ``wortlaut.cli``;
    ``DipPlenarprotokollAdapter`` wird durch den Minimal-Adapter ersetzt."""
    adapter = MinimalAdapter()
    worm = FakeWorm()
    engine = MagicMock()
    engine.dispose = AsyncMock()
    ingest = AsyncMock(return_value=IngestOutcome(INSERTED, None, "h1"))
    with (
        patch("wortlaut.cli.DbSettings", return_value=MagicMock(dsn="f")),
        patch("wortlaut.cli.WormSettings", return_value=MagicMock()),
        patch("wortlaut.cli.DipSettings", return_value=MagicMock()),
        patch("wortlaut.cli.create_async_engine_from", return_value=engine),
        patch("wortlaut.cli.make_sessionmaker", return_value=FakeSessionmaker()),
        patch("wortlaut.cli.DipPlenarprotokollAdapter", return_value=adapter),
        patch("wortlaut.cli.MinioWormStore", return_value=worm),
        patch("wortlaut.cli.upgrade_head", new=AsyncMock()),
        patch("wortlaut.cli.ensure_ingest_adapter", new=AsyncMock()),
        patch("wortlaut.cli.ingest_source", new=ingest),
    ):
        yield SimpleNamespace(adapter=adapter, worm=worm, engine=engine, ingest=ingest)


# ── AC4: Minimal-Adapter läuft durch ────────────────────────────────────


async def test_minimal_adapter_runs_through(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC4: Ein Adapter mit genau den Protocol-Mitgliedern läuft ``_run``
    vollständig durch: ``aclose`` genau einmal, kein ``AttributeError``."""
    wired.adapter.refs = [_ref(1)]
    rc = await _run(_ns())
    assert rc == 0
    assert wired.ingest.call_count == 1
    assert wired.adapter.aclose_calls == 1
    wired.engine.dispose.assert_awaited_once()


# ── AC5: Quelle überspringen ────────────────────────────────────────────


async def test_adapter_error_skips_source(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC5: ``AdapterError`` bei der zweiten von drei Quellen → Exit 0,
    ``fetch_error=1``, die anderen beiden Quellen werden verarbeitet."""
    wired.adapter.refs = [_ref(1), _ref(2), _ref(3)]
    wired.ingest.side_effect = [
        IngestOutcome(INSERTED, None, "h1"),
        AdapterError("quelle gerade nicht verfügbar"),
        IngestOutcome(INSERTED, None, "h3"),
    ]
    rc = await _run(_ns())
    out = capfd.readouterr().out
    assert rc == 0
    assert wired.ingest.call_count == 3
    assert "fetch_error=1" in out
    assert "inserted=2" in out


# ── AC6: Kernfehler bleiben sichtbar ────────────────────────────────────


async def test_core_value_error_not_swallowed(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC6: Wirft ``ingest_source`` einen ``ValueError`` (Kernfehler), bricht
    ``_run`` mit dieser Ausnahme ab — statt ihn als ``fetch_error`` zu zählen;
    das Herunterfahren läuft trotzdem."""
    wired.adapter.refs = [_ref(1), _ref(2)]
    wired.ingest.side_effect = [
        ValueError("Kernfehler"),
        IngestOutcome(INSERTED, None, "h2"),
    ]
    with pytest.raises(ValueError):
        await _run(_ns())
    assert wired.adapter.aclose_calls == 1
    wired.engine.dispose.assert_awaited_once()
    assert capfd.readouterr().out == ""


# ── AC7: Entdeckung scheitert sauber ────────────────────────────────────


async def test_discover_adapter_error_exit_2(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC7: ``discover`` wirft ``AdapterError`` → Exit 2 mit der Meldung
    ``discover fehlgeschlagen: …``; ``aclose`` wird trotzdem genau einmal
    aufgerufen."""
    wired.adapter.discover_exc = AdapterError("boom")
    rc = await _run(_ns())
    cap = capfd.readouterr()
    assert rc == 2
    assert "discover fehlgeschlagen" in cap.err
    assert wired.adapter.discover_calls == 1
    assert wired.adapter.aclose_calls == 1
