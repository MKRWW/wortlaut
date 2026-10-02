"""Unit (Spec 0130 §4.4): CLI-Subcommand ``capture`` — Summary/Exit-Codes (AC9).

Keine Live-Netz-/DB-Calls: alle Composition-Root-Deps von ``wortlaut.cli``
werden patcht; ``capture_source`` liefert gebrachte ``CaptureOutcome``s,
``list_sources_needing_capture`` gebrachte Pending-Quellen. AC9: dry-run ohne
Zugangsdaten → Exit 0, echter Lauf ohne Zugangsdaten → Exit 2, Summary-Zeile
in Feld-Reihenfolge, Exit-Codes (parametrisiert), Circuit-Breaker. Seit #132
hier auch die vier Pre-Flight-Tests (übertragen aus ``test_cli.py``, §4.5):
der Pre-Flight läuft jetzt am ``capture``, nicht am ``ingest``.
"""

from __future__ import annotations

import uuid
from argparse import Namespace
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from wortlaut.archive.errors import ArchiveError
from wortlaut.archive.settings import ArchiveSettings
from wortlaut.cli import _run_capture
from wortlaut.pipeline.capture import CaptureOutcome
from wortlaut.store.captures import CaptureCandidate

_SNAPSHOT_URL = "https://web.archive.org/web/20260805170741/x.pdf"

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
        raise AssertionError("not used (capture_source ist patcht)")

    async def get(self, ref: str) -> bytes:
        raise AssertionError("not used (capture_source ist patcht)")


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


class _FakeWayback:
    """WaybackArchiver-Doppel: nur ``user_status`` (Pre-Flight) und ``aclose``;
    ``archive`` wird nie gebraucht (capture_source ist patcht)."""

    def __init__(self) -> None:
        self.user_status_calls = 0
        self.user_status_error: ArchiveError | None = None
        self.closed = 0

    async def user_status(self) -> str:
        self.user_status_calls += 1
        if self.user_status_error is not None:
            raise self.user_status_error
        return "available=3 processing=0 daily_captures=0/30000"

    async def aclose(self) -> None:
        self.closed += 1


class _FakeArchiveToday:
    """archive.today-Doppel: im Capture-Pfad nie verwendet — jeder Aufruf wirft (AC10)."""

    def __init__(self) -> None:
        self.archive_calls = 0
        self.closed = 0

    async def archive(self, origin_url: str) -> str:
        self.archive_calls += 1
        raise AssertionError("archive.today wird im Capture-Pfad nie aufgerufen")

    async def aclose(self) -> None:
        self.closed += 1


def _archive_settings(
    *,
    consecutive_failure_limit: int = 5,
    preflight_enabled: bool = True,
    ia_access_key: str | None = "k-abc-1",
    ia_secret: str | None = "s-xyz-2",
) -> SimpleNamespace:
    """ArchiveSettings-Ersatz; die Zugangsdaten sind SecretStr-Objekte (wie in
    der echten Klasse), damit ``_ia_credentials`` unverändert getestet wird."""
    return SimpleNamespace(
        wayback_min_interval_seconds=1.0,
        archive_today_min_interval_seconds=1.0,
        retry_attempts=1,
        retry_base_delay_seconds=0.0,
        optional_failure_limit=3,
        consecutive_failure_limit=consecutive_failure_limit,
        preflight_enabled=preflight_enabled,
        spn2_poll_interval_seconds=1.0,
        spn2_poll_timeout_seconds=1.0,
        attest_max_snapshot_bytes=100 * 1024,
        capture_cooldown_captured_hours=72.0,
        capture_cooldown_failed_hours=6.0,
        ia_access_key=SecretStr(ia_access_key) if ia_access_key is not None else None,
        ia_secret=SecretStr(ia_secret) if ia_secret is not None else None,
    )


def _ns(**kw: object) -> Namespace:
    base: dict[str, object] = {
        "limit": None,
        "no_migrate": True,
        "dry_run": False,
        "no_preflight": False,
    }
    base.update(kw)
    return Namespace(**base)


def _candidate(i: int) -> CaptureCandidate:
    return CaptureCandidate(
        source_id=uuid.UUID(int=i),
        content_hash=f"{i:02x}" * 32,
        raw_bytes_ref=f"s3://bucket/{i}?versionId=1",
        origin_url=f"https://dserver.bundestag.de/{i}.pdf",
    )


def _captured(i: int) -> CaptureOutcome:
    return CaptureOutcome("captured", uuid.UUID(int=i), snapshot_url=_SNAPSHOT_URL)


def _already_archived(i: int) -> CaptureOutcome:
    return CaptureOutcome("already_archived", uuid.UUID(int=i))


def _failed(i: int) -> CaptureOutcome:
    return CaptureOutcome("failed", uuid.UUID(int=i), reason="wayback:http_status_429")


def _hash_mismatch(i: int) -> CaptureOutcome:
    return CaptureOutcome("hash_mismatch", uuid.UUID(int=i))


def _worm_missing(i: int) -> CaptureOutcome:
    return CaptureOutcome("worm_missing", uuid.UUID(int=i))


def _error(i: int) -> CaptureOutcome:
    return CaptureOutcome("error", uuid.UUID(int=i))


@pytest.fixture
def wired() -> Iterator[SimpleNamespace]:
    """Patcht die Composition-Root-Deps von ``wortlaut.cli`` für den Capture-Pass."""
    engine = MagicMock()
    engine.dispose = AsyncMock()
    lookup = _FakeLookup()
    lookup_cls = MagicMock(return_value=lookup)
    wayback = _FakeWayback()
    wayback_cls = MagicMock(return_value=wayback)
    atoday = _FakeArchiveToday()
    atoday_cls = MagicMock(return_value=atoday)
    capture = AsyncMock()
    list_pending = AsyncMock(return_value=[])
    with (
        patch("wortlaut.cli.DbSettings", return_value=MagicMock(dsn="f")),
        patch("wortlaut.cli.WormSettings", return_value=MagicMock()),
        patch("wortlaut.cli.ArchiveSettings", return_value=_archive_settings()),
        patch("wortlaut.cli.create_async_engine_from", return_value=engine),
        patch("wortlaut.cli.make_sessionmaker", return_value=FakeSessionmaker()),
        patch("wortlaut.cli.MinioWormStore", return_value=FakeWorm()),
        patch("wortlaut.cli.upgrade_head", new=AsyncMock()),
        patch("wortlaut.cli.list_sources_needing_capture", new=list_pending),
        patch("wortlaut.cli.capture_source", new=capture),
        patch("wortlaut.cli.WaybackArchiver", wayback_cls),
        patch("wortlaut.cli.ArchiveTodayArchiver", atoday_cls),
        patch("wortlaut.cli.HttpWaybackLookup", lookup_cls),
    ):
        yield SimpleNamespace(
            engine=engine,
            lookup=lookup,
            lookup_cls=lookup_cls,
            wayback_cls=wayback_cls,
            wayback=wayback,
            atoday_cls=atoday_cls,
            atoday=atoday,
            capture_source=capture,
            list_pending=list_pending,
        )


# ── AC9: dry-run ohne Zugangsdaten ───────────────────────────────────────


async def test_dry_run_without_credentials(
    wired: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """AC9: --dry-run ohne IA-Zugangsdaten → Exit 0 mit `pending=<n> dry_run=True`,
    ohne Netz und ohne Pre-Flight (echte ArchiveSettings, geleerte ENV)."""
    monkeypatch.delenv("WORTLAUT_ARCHIVE_IA_ACCESS_KEY", raising=False)
    monkeypatch.delenv("WORTLAUT_ARCHIVE_IA_SECRET", raising=False)
    real = ArchiveSettings()
    assert real.ia_access_key is None
    assert real.ia_secret is None

    wired.list_pending.return_value = [_candidate(1), _candidate(2)]
    with patch("wortlaut.cli.ArchiveSettings", return_value=real):
        rc = await _run_capture(_ns(dry_run=True))
    out = capfd.readouterr().out
    assert rc == 0
    assert out == "pending=2 dry_run=True\n"
    assert wired.wayback.user_status_calls == 0
    assert wired.lookup_cls.call_count == 0
    assert wired.capture_source.call_count == 0
    assert wired.wayback.closed == 1
    assert wired.atoday.closed == 1
    assert wired.engine.dispose.await_count == 1


# ── AC9: echter Lauf ohne Zugangsdaten → Exit 2 ──────────────────────────


async def test_missing_credentials_exit_2(
    wired: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """AC9: echter Lauf ohne IA-Zugangsdaten → Exit 2, stderr nennt beide
    ENV-Namen; kein Capture, kein Pre-Flight, kein Archiver-Aufbau (Abbruch
    VOR der Engine-Erzeugung)."""
    monkeypatch.delenv("WORTLAUT_ARCHIVE_IA_ACCESS_KEY", raising=False)
    monkeypatch.delenv("WORTLAUT_ARCHIVE_IA_SECRET", raising=False)
    real = ArchiveSettings()
    wired.list_pending.return_value = [_candidate(1)]
    with patch("wortlaut.cli.ArchiveSettings", return_value=real):
        rc = await _run_capture(_ns())
    cap = capfd.readouterr()
    assert rc == 2
    assert "WORTLAUT_ARCHIVE_IA_ACCESS_KEY" in cap.err
    assert "WORTLAUT_ARCHIVE_IA_SECRET" in cap.err
    assert wired.capture_source.call_count == 0
    assert wired.wayback_cls.call_count == 0
    assert wired.atoday_cls.call_count == 0
    assert wired.engine.dispose.await_count == 0


# ── AC9: genau eine Ergebniszeile, fester Feld-Reihenfolge ───────────────


async def test_summary_line_field_order(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC9: Der echte Lauf gibt genau eine Zeile aus, Felder in dieser
    Reihenfolge: pending= captured= already_archived= failed= hash_mismatch=
    worm_missing= error=; der Pre-Flight-Call geht genau einmal raus,
    archive.today bleibt ungenutzt, alle Clients werden geschlossen."""
    wired.list_pending.return_value = [_candidate(1), _candidate(2), _candidate(3)]
    wired.capture_source.side_effect = [_captured(1), _already_archived(2), _failed(3)]
    rc = await _run_capture(_ns())
    cap = capfd.readouterr()
    assert rc == 0
    assert cap.out == (
        "pending=3 captured=1 already_archived=1 failed=1 hash_mismatch=0 worm_missing=0 error=0\n"
    )
    assert wired.wayback.user_status_calls == 1
    assert wired.atoday.archive_calls == 0
    assert wired.lookup.closed == 1
    assert wired.wayback.closed == 1
    assert wired.atoday.closed == 1
    assert wired.engine.dispose.await_count == 1


# ── AC9: Exit-Codes ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("factories", "expected"),
    [
        ([_captured], 0),
        ([_already_archived], 0),
        ([_failed], 0),
        ([_worm_missing], 0),
        ([_hash_mismatch], 4),
        ([_error], 1),
        ([_failed, _error], 1),
        ([_hash_mismatch, _error], 4),
    ],
)
async def test_exit_codes(
    wired: SimpleNamespace,
    capfd: pytest.CaptureFixture[str],
    factories: list[Callable[[int], CaptureOutcome]],
    expected: int,
) -> None:
    """AC9: 0 im Normalfall (auch bei failed, worm_missing) · 4, sobald
    hash_mismatch > 0 (Vorrang) · sonst 1, sobald error > 0. Mismatches
    nennen die source_id auf stderr; failed allein ist kein Fehler-Exit."""
    wired.list_pending.return_value = [_candidate(i) for i in range(1, len(factories) + 1)]
    wired.capture_source.side_effect = [factory(i) for i, factory in enumerate(factories, 1)]
    rc = await _run_capture(_ns())
    cap = capfd.readouterr()
    assert rc == expected
    assert wired.capture_source.call_count == len(factories)
    if expected == 4:
        assert "passen nicht zum Ledger-Hash" in cap.err
        for i, factory in enumerate(factories, 1):
            if factory(i).status == "hash_mismatch":
                assert str(uuid.UUID(int=i)) in cap.err


# ── AC9: Circuit-Breaker ─────────────────────────────────────────────────


async def test_circuit_breaker(wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]) -> None:
    """AC9: consecutive_failure_limit aufeinanderfolgende failed/error →
    Abbruch mit Exit 3, nicht mehr als limit Quellen verarbeitet."""
    wired.list_pending.return_value = [_candidate(i) for i in range(1, 8)]
    wired.capture_source.side_effect = [_failed(i) for i in range(1, 8)]
    rc = await _run_capture(_ns())
    cap = capfd.readouterr()
    assert rc == 3
    assert wired.capture_source.call_count == 5
    assert "Circuit-Breaker" in cap.err
    assert "pending=7" in cap.out


# ── Pre-Flight (übertragen aus tests/unit/test_cli.py, Spec 0132 §4.5) ───
#
# Der Pre-Flight läuft seit #132 hier, nicht im Ingest — die vier Tests
# behalten ihre Aussagen, „discover 0×“ heißt dort „keine Quelle verarbeitet“.


async def test_preflight_failure_aborts_before_discover(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """Ausfall: User-Status-Probe wirft ArchiveError (401) → Exit 3, keine
    Quelle verarbeitet, Ausgabe nennt 'Pre-Flight' samt Grund — VOR der
    ersten Quelle."""
    wired.list_pending.return_value = [_candidate(1), _candidate(2)]
    wired.wayback.user_status_error = ArchiveError(
        "wayback", "unauthorized", status_code=401, transient=False
    )
    rc = await _run_capture(_ns())
    cap = capfd.readouterr()
    assert rc == 3
    assert wired.capture_source.call_count == 0  # keine Quelle verarbeitet
    assert "Pre-Flight" in cap.err
    assert "401" in cap.err  # Statuscode bleibt in der Meldung erhalten
    assert wired.wayback.user_status_calls == 1  # genau ein Probe-Call


async def test_preflight_healthy_runs_normally(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """Gesund: Probe liefert die User-Status-Zusammenfassung → der Lauf geht
    normal weiter (Exit 0, Summary wie bisher). Der User-Status-Call geht
    genau einmal raus; die Probe setzt KEINEN Capture ab."""
    wired.list_pending.return_value = [_candidate(1), _candidate(2)]
    wired.capture_source.side_effect = [_captured(1), _already_archived(2)]
    rc = await _run_capture(_ns())
    cap = capfd.readouterr()
    assert rc == 0
    assert wired.wayback.user_status_calls == 1  # die Probe ging raus
    assert wired.capture_source.call_count == 2  # beide Quellen normal verarbeitet
    assert "pending=2" in cap.out


async def test_no_preflight_flag_skips_probe(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """--no-preflight → kein Probe-Call, Lauf verhält sich unverändert
    (Exit 0)."""
    wired.list_pending.return_value = [_candidate(1), _candidate(2)]
    wired.capture_source.side_effect = [_captured(1), _captured(2)]
    rc = await _run_capture(_ns(no_preflight=True))
    cap = capfd.readouterr()
    assert rc == 0
    assert wired.wayback.user_status_calls == 0  # kein Probe-Call
    assert wired.capture_source.call_count == 2
    assert "pending=2" in cap.out


async def test_preflight_disabled_via_settings_skips_probe(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """preflight_enabled=False per ENV → kein Probe-Call, normaler Lauf."""
    wired.list_pending.return_value = [_candidate(1), _candidate(2)]
    wired.capture_source.side_effect = [_captured(1), _captured(2)]
    with patch(
        "wortlaut.cli.ArchiveSettings",
        return_value=_archive_settings(preflight_enabled=False),
    ):
        rc = await _run_capture(_ns())
    cap = capfd.readouterr()
    assert rc == 0
    assert wired.wayback.user_status_calls == 0  # kein Probe-Call
    assert wired.capture_source.call_count == 2
    assert "pending=2" in cap.out
