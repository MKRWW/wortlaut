"""Unit (Spec 0124): CLI-Subcommand ``attest`` — Summary/Exit-Codes (AC13, AC14).

Keine Live-Netz-/DB-Calls: alle Composition-Root-Deps von ``wortlaut.cli``
werden patcht; ``attest_source`` liefert gebrachte ``AttestOutcome``s,
``list_sources_without_attestation`` gebrachte Pending-Quellen, der Lookup ist
ein Doppel ohne Client. AC15 (läuft ohne IA-Zugangsdaten) zusätzlich mit
ECHTEN ``ArchiveSettings`` ohne gesetzte ``WORTLAUT_ARCHIVE_IA_*``-Variablen.
"""

from __future__ import annotations

import uuid
from argparse import Namespace
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from wortlaut.archive.settings import ArchiveSettings
from wortlaut.cli import _run_attest
from wortlaut.pipeline.attest import AttestOutcome
from wortlaut.store.attestations import PendingAttestation

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
        raise AssertionError("not used (attest_source ist patcht)")

    async def get(self, ref: str) -> bytes:
        raise AssertionError("not used (attest_source ist patcht)")


class _FakeLookup:
    """HttpWaybackLookup-Doppel: zählt Anfragen; der gepinnte Client wird nie gebaut."""

    def __init__(self) -> None:
        self.requests = 0
        self.closed = 0

    async def candidates(self, origin_url: str, *, sha1_b32: str) -> list[object]:
        self.requests += 1
        return []

    async def fetch(self, candidate: object) -> bytes | None:
        self.requests += 1
        return None

    async def aclose(self) -> None:
        self.closed += 1


def _archive_settings(*, consecutive_failure_limit: int = 5) -> SimpleNamespace:
    return SimpleNamespace(
        wayback_min_interval_seconds=1.0,
        attest_max_snapshot_bytes=100 * 1024,
        retry_attempts=1,
        retry_base_delay_seconds=0.0,
        attest_max_candidates=3,
        consecutive_failure_limit=consecutive_failure_limit,
    )


def _ns(**kw: object) -> Namespace:
    base: dict[str, object] = {
        "limit": None,
        "no_migrate": True,
        "dry_run": False,
    }
    base.update(kw)
    return Namespace(**base)


def _pending(i: int) -> PendingAttestation:
    return PendingAttestation(
        source_id=uuid.UUID(int=i),
        content_hash=f"{i:02x}" * 32,
        raw_bytes_ref=f"s3://bucket/{i}?versionId=1",
        origin_url=f"https://dserver.bundestag.de/{i}.pdf",
        retrieved_at=datetime(2026, 8, 5, tzinfo=UTC),
    )


def _attested(i: int) -> AttestOutcome:
    return AttestOutcome("attested", uuid.UUID(int=i))


def _no_match(i: int) -> AttestOutcome:
    return AttestOutcome("no_matching_snapshot", uuid.UUID(int=i))


def _unavailable(i: int) -> AttestOutcome:
    return AttestOutcome("snapshot_unavailable", uuid.UUID(int=i))


def _bytes_mismatch(i: int) -> AttestOutcome:
    return AttestOutcome("bytes_mismatch", uuid.UUID(int=i))


def _hash_mismatch(i: int) -> AttestOutcome:
    return AttestOutcome("hash_mismatch", uuid.UUID(int=i))


def _worm_missing(i: int) -> AttestOutcome:
    return AttestOutcome("worm_missing", uuid.UUID(int=i))


def _error(i: int) -> AttestOutcome:
    return AttestOutcome("error", uuid.UUID(int=i))


@pytest.fixture
def wired() -> Iterator[SimpleNamespace]:
    """Patcht die Composition-Root-Deps von ``wortlaut.cli`` für den Attestierungs-Pass."""
    engine = MagicMock()
    engine.dispose = AsyncMock()
    lookup = _FakeLookup()
    lookup_cls = MagicMock(return_value=lookup)
    attest = AsyncMock()
    list_pending = AsyncMock(return_value=[])
    with (
        patch("wortlaut.cli.DbSettings", return_value=MagicMock(dsn="f")),
        patch("wortlaut.cli.WormSettings", return_value=MagicMock()),
        patch("wortlaut.cli.ArchiveSettings", return_value=_archive_settings()),
        patch("wortlaut.cli.create_async_engine_from", return_value=engine),
        patch("wortlaut.cli.make_sessionmaker", return_value=FakeSessionmaker()),
        patch("wortlaut.cli.MinioWormStore", return_value=FakeWorm()),
        patch("wortlaut.cli.upgrade_head", new=AsyncMock()),
        patch("wortlaut.cli.list_sources_without_attestation", new=list_pending),
        patch("wortlaut.cli.attest_source", new=attest),
        patch("wortlaut.cli.HttpWaybackLookup", lookup_cls),
    ):
        yield SimpleNamespace(
            engine=engine,
            lookup=lookup,
            lookup_cls=lookup_cls,
            attest_source=attest,
            list_pending=list_pending,
        )


# ── AC13: dry-run-Zeile (vor jedem Lookup-Aufbau) ────────────────────────


async def test_dry_run_line(wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]) -> None:
    """AC13: --dry-run gibt pending=<n> dry_run=True aus, ohne Netzaufruf und
    ohne Lookup-Aufbau, Exit 0."""
    wired.list_pending.return_value = [_pending(1), _pending(2)]
    rc = await _run_attest(_ns(dry_run=True))
    out = capfd.readouterr().out
    assert rc == 0
    assert out == "pending=2 dry_run=True\n"
    assert wired.lookup_cls.call_count == 0
    assert wired.attest_source.call_count == 0
    assert wired.lookup.requests == 0


# ── AC13: genau eine Ergebniszeile, fester Feld-Reihenfolge ──────────────


async def test_summary_line_field_order(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC13: Der echte Lauf gibt genau eine Zeile aus, Felder in dieser
    Reihenfolge: pending= attested= no_matching_snapshot= snapshot_unavailable=
    bytes_mismatch= hash_mismatch= worm_missing= error=."""
    wired.list_pending.return_value = [_pending(1), _pending(2), _pending(3)]
    wired.attest_source.side_effect = [_attested(1), _no_match(2), _unavailable(3)]
    rc = await _run_attest(_ns())
    cap = capfd.readouterr()
    assert rc == 0
    assert cap.out == (
        "pending=3 attested=1 no_matching_snapshot=1 snapshot_unavailable=1 "
        "bytes_mismatch=0 hash_mismatch=0 worm_missing=0 error=0\n"
    )
    assert cap.err == ""
    assert wired.lookup.closed == 1


# ── AC14: Exit-Codes ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("factories", "expected"),
    [
        ([_attested], 0),
        ([_no_match], 0),
        ([_unavailable], 0),
        ([_worm_missing], 0),
        ([_bytes_mismatch], 4),
        ([_hash_mismatch], 4),
        ([_bytes_mismatch, _error], 4),
        ([_error, _bytes_mismatch], 4),
        ([_error], 1),
        ([_error, _attested], 1),
    ],
)
async def test_exit_codes(
    wired: SimpleNamespace,
    capfd: pytest.CaptureFixture[str],
    factories: list[Callable[[int], AttestOutcome]],
    expected: int,
) -> None:
    """AC14: 0 im Normalfall (auch bei no_matching_snapshot, snapshot_unavailable,
    worm_missing) · 4, sobald bytes_mismatch > 0 oder hash_mismatch > 0 (Vorrang) ·
    sonst 1, sobald error > 0. Mismatches nennen die source_id auf stderr."""
    wired.list_pending.return_value = [_pending(i) for i in range(1, len(factories) + 1)]
    wired.attest_source.side_effect = [factory(i) for i, factory in enumerate(factories, 1)]
    rc = await _run_attest(_ns())
    cap = capfd.readouterr()
    assert rc == expected
    assert wired.attest_source.call_count == len(factories)
    if expected == 4:
        assert "passen nicht zum Ledger-Hash" in cap.err
        # Genannt wird die source_id der Quelle mit dem Mismatch (hier: alle
        # außer den ``error``-Quellen, die nur in der Summary zählen).
        for i, factory in enumerate(factories, 1):
            if factory(i).status in ("bytes_mismatch", "hash_mismatch"):
                assert str(uuid.UUID(int=i)) in cap.err


async def test_circuit_breaker_aborts_run(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC14: consecutive_failure_limit aufeinanderfolgende error → Abbruch mit
    Exit 3, nicht mehr als limit Quellen verarbeitet."""
    wired.list_pending.return_value = [_pending(i) for i in range(1, 8)]
    wired.attest_source.side_effect = [_error(i) for i in range(1, 8)]
    rc = await _run_attest(_ns())
    cap = capfd.readouterr()
    assert rc == 3
    assert wired.attest_source.call_count == 5
    assert "Circuit-Breaker" in cap.err
    assert "pending=7" in cap.out


async def test_config_error_exits_two(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC14: Fehlende Konfiguration → Exit 2, Meldung ohne Werte (R-SEC-01)."""
    exc = ValueError("fehlende ENV: WORTLAUT_DB_DSN")
    with patch("wortlaut.cli.ArchiveSettings", side_effect=exc):
        rc = await _run_attest(_ns())
    assert rc == 2
    assert "Konfiguration" in capfd.readouterr().err
    assert wired.attest_source.call_count == 0


# ── AC15: läuft ohne Internet-Archive-Zugangsdaten ───────────────────────


async def test_runs_without_ia_credentials(
    wired: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """AC15: attest liest nur — im Gegensatz zu ``ingest`` ist dafür kein
    Exit 2 wegen fehlender ``WORTLAUT_ARCHIVE_IA_*`` fällig (echte Settings)."""
    monkeypatch.delenv("WORTLAUT_ARCHIVE_IA_ACCESS_KEY", raising=False)
    monkeypatch.delenv("WORTLAUT_ARCHIVE_IA_SECRET", raising=False)
    real = ArchiveSettings()
    assert real.ia_access_key is None
    assert real.ia_secret is None

    wired.list_pending.return_value = [_pending(1)]
    wired.attest_source.side_effect = [_attested(1)]
    with patch("wortlaut.cli.ArchiveSettings", return_value=real):
        rc = await _run_attest(_ns())
    cap = capfd.readouterr()
    assert rc == 0
    assert "attested=1" in cap.out


# ── --limit wird an die Auswahl durchgereicht ────────────────────────────


async def test_limit_passed_through(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """--limit N begrenzt die Auswahl auf N Quellen (limit wird durchgereicht)."""
    rc = await _run_attest(_ns(limit=3))
    assert rc == 0
    wired.list_pending.assert_awaited_once_with(ANY, limit=3)
    assert capfd.readouterr().out == (
        "pending=0 attested=0 no_matching_snapshot=0 snapshot_unavailable=0 "
        "bytes_mismatch=0 hash_mismatch=0 worm_missing=0 error=0\n"
    )
