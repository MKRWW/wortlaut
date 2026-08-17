"""Unit (Spec 0076): CLI-Subcommand ``timestamp`` — Summary/Exit-Codes (AC16, AC17, AC18).

Keine Live-Netz-/DB-Calls: alle Composition-Root-Deps von ``wortlaut.cli`` werden
patcht; ``timestamp_source`` liefert gebrachte ``TimestampOutcome``s,
``list_sources_without_timestamp`` gebrachte Pending-Quellen.
"""

from __future__ import annotations

import uuid
from argparse import Namespace
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wortlaut.cli import _run_timestamp
from wortlaut.pipeline.timestamp import TimestampOutcome
from wortlaut.store.timestamps import PendingSource

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
        raise AssertionError("not used (timestamp_source ist patcht)")

    async def get(self, ref: str) -> bytes:
        raise AssertionError("not used (timestamp_source ist patcht)")


def _tsa_settings(*, consecutive_failure_limit: int = 5) -> SimpleNamespace:
    return SimpleNamespace(
        profiles="freetsa,sigstore",
        timeout_seconds=1.0,
        consecutive_failure_limit=consecutive_failure_limit,
        profile_names=lambda: ["freetsa", "sigstore"],
    )


def _ns(**kw: object) -> Namespace:
    base: dict[str, object] = {
        "limit": None,
        "no_migrate": True,
        "dry_run": False,
    }
    base.update(kw)
    return Namespace(**base)


def _pending(i: int) -> PendingSource:
    return PendingSource(
        source_id=uuid.UUID(int=i),
        content_hash=f"{i:02x}" * 32,
        raw_bytes_ref=f"s3://bucket/{i}?versionId=1",
    )


def _stamped(i: int) -> object:
    from wortlaut.pipeline.timestamp import TimestampOutcome

    return TimestampOutcome("stamped", uuid.UUID(int=i), tsa_name="freetsa")


def _tsa_failed(i: int) -> object:
    from wortlaut.pipeline.timestamp import TimestampOutcome

    return TimestampOutcome("tsa_failed", uuid.UUID(int=i), failures=("freetsa:timeout",))


def _hash_mismatch(i: int) -> object:
    from wortlaut.pipeline.timestamp import TimestampOutcome

    return TimestampOutcome("hash_mismatch", uuid.UUID(int=i))


@pytest.fixture
def wired() -> Iterator[SimpleNamespace]:
    """Patcht die Composition-Root-Deps von ``wortlaut.cli`` für den Stempel-Pass."""
    engine = MagicMock()
    engine.dispose = AsyncMock()
    ts_source = AsyncMock()
    list_pending = AsyncMock(return_value=[])
    with (
        patch("wortlaut.cli.DbSettings", return_value=MagicMock(dsn="f")),
        patch("wortlaut.cli.WormSettings", return_value=MagicMock()),
        patch("wortlaut.cli.TimestampSettings", return_value=_tsa_settings()),
        patch("wortlaut.cli.create_async_engine_from", return_value=engine),
        patch("wortlaut.cli.make_sessionmaker", return_value=FakeSessionmaker()),
        patch("wortlaut.cli.MinioWormStore", return_value=FakeWorm()),
        patch("wortlaut.cli.upgrade_head", new=AsyncMock()),
        patch("wortlaut.cli.list_sources_without_timestamp", new=list_pending),
        patch("wortlaut.cli.timestamp_source", new=ts_source),
    ):
        yield SimpleNamespace(
            engine=engine,
            ts_source=ts_source,
            list_pending=list_pending,
        )


# ── AC16: Summary + Exit 0 ───────────────────────────────────────────────


async def test_summary_and_exit_zero(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC16: N pending Quellen, alle gestempelt → Exit 0, Summary nennt
    pending/stamped/hash_mismatch/worm_missing/tsa_failed/reasons."""
    wired.list_pending.return_value = [_pending(1), _pending(2)]
    wired.ts_source.side_effect = [_stamped(1), _stamped(2)]
    rc = await _run_timestamp(_ns())
    out = capfd.readouterr().out
    assert rc == 0
    assert "pending=2 stamped=2 hash_mismatch=0 worm_missing=0 tsa_failed=0 reasons=-" in out


# ── AC17: Circuit-Breaker (Exit 3) ───────────────────────────────────────


async def test_circuit_breaker_aborts_run(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC17: jede TSA-Anfrage scheitert; consecutive_failure_limit=5 erreicht → Lauf
    bricht ab, Exit 3, nicht mehr als limit Quellen verarbeitet, Ausgabe nennt den
    häufigsten Grund."""
    settings = _tsa_settings(consecutive_failure_limit=5)
    with patch("wortlaut.cli.TimestampSettings", return_value=settings):
        wired.list_pending.return_value = [_pending(i) for i in range(1, 8)]
        wired.ts_source.side_effect = [_tsa_failed(i) for i in range(1, 8)]
        rc = await _run_timestamp(_ns())
        cap = capfd.readouterr()
    assert rc == 3
    assert wired.ts_source.call_count == 5  # nicht mehr als `limit` Quellen
    assert "Circuit-Breaker" in cap.err
    assert "häufigster Grund" in cap.err
    assert "freetsa:timeout=5" in cap.err
    assert "tsa_failed=5" in cap.out
    # Abbruch BEVOR die restlichen Quellen verarbeitet werden
    assert "pending=7" in cap.out


# ── AC18: hash_mismatch ist laut (Exit 4) ────────────────────────────────


async def test_hash_mismatch_exit_four(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC18: Lauf mit ≥1 hash_mismatch → Lauf endet (alle verarbeitet), Exit 4,
    Ausgabe nennt die betroffene source_id."""
    source_id = uuid.UUID(int=42)
    wired.list_pending.return_value = [
        PendingSource(
            source_id=source_id, content_hash="a" * 64, raw_bytes_ref="s3://b/k?versionId=1"
        ),
    ]
    wired.ts_source.side_effect = [TimestampOutcome("hash_mismatch", source_id)]
    rc = await _run_timestamp(_ns())
    cap = capfd.readouterr()
    assert rc == 4
    assert str(source_id) in cap.err
    assert "hash_mismatch=1" in cap.out
    assert "pending=1" in cap.out


# ── dry-run + unbekanntes Profil (Konfiguration, Exit 2) ─────────────────


async def test_dry_run_lists_pending_only(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """--dry-run → nur pending=<n> ausgeben, keine Quelle gestempelt, Exit 0."""
    wired.list_pending.return_value = [_pending(1), _pending(2)]
    rc = await _run_timestamp(_ns(dry_run=True))
    out = capfd.readouterr().out
    assert rc == 0
    assert "pending=2" in out
    assert wired.ts_source.call_count == 0


async def test_unknown_profile_exits_two(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """Unbekannter Profilname → ValueError → Exit 2 (Konfiguration)."""
    settings = _tsa_settings(consecutive_failure_limit=5)
    exc = ValueError("unbekanntes TSA-Profil: 'gibtsnicht'")
    with (
        patch("wortlaut.cli.TimestampSettings", return_value=settings),
        patch("wortlaut.cli.load_profile", side_effect=exc),
    ):
        rc = await _run_timestamp(_ns())
        err = capfd.readouterr().err
    assert rc == 2
    assert "Konfiguration" in err
