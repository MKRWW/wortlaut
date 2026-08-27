"""CLI Unit-Tests AC1-AC8 (+ main/__main__ Coverage, #73 Breaker, #77/#108 Pre-Flight) — keine Live-Netz-/DB-Calls."""

from __future__ import annotations

import subprocess
import sys
from argparse import Namespace
from collections.abc import Iterator
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from wortlaut.archive.errors import ArchiveError
from wortlaut.archive.settings import ArchiveSettings
from wortlaut.cli import _run, main
from wortlaut.ingest.adapter import SourceRef
from wortlaut.ingest.dip import DipFetchError
from wortlaut.pipeline.ingest import IngestOutcome

# ── Fakes ────────────────────────────────────────────────────────────────

USER_STATUS_SUMMARY = "available=3 processing=0 daily_captures=0/30000"


class FakeAdapter:
    name = "fake"
    version = "1.0"
    trust_level = "verified_primary"

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


class FakeArchiver:
    def __init__(self) -> None:
        self.aclose_called = False
        self.user_status_calls = 0
        self.archive_calls: list[str] = []
        self.archive_error: ArchiveError | None = None
        self.user_status_error: ArchiveError | None = None
        self.archive_result = "https://web.archive.org/snapshot/xyz"
        self.user_status_result = USER_STATUS_SUMMARY

    async def user_status(self) -> str:
        self.user_status_calls += 1
        if self.user_status_error is not None:
            raise self.user_status_error
        return self.user_status_result

    async def archive(self, origin_url: str) -> str:
        self.archive_calls.append(origin_url)
        if self.archive_error is not None:
            raise self.archive_error
        return self.archive_result

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
        "no_preflight": False,
    }
    base.update(kw)
    return Namespace(**base)


def _archive_settings_ns(
    *,
    preflight_enabled: bool = True,
    ia_access_key: str | None = None,
    ia_secret: str | None = None,
) -> SimpleNamespace:
    """ArchiveSettings-Ersatz; die Zugangsdaten sind SecretStr-Objekte (wie in
    der echten Klasse), damit ``_ia_credentials`` unverändert getestet wird.
    Zusammengesetzte Testwerte (S6698: keine ausschreibenden
    Zugangsdaten-artigen Literale)."""
    return SimpleNamespace(
        wayback_min_interval_seconds=10.0,
        archive_today_min_interval_seconds=15.0,
        retry_attempts=3,
        retry_base_delay_seconds=2.0,
        optional_failure_limit=3,
        consecutive_failure_limit=5,
        preflight_enabled=preflight_enabled,
        ia_access_key=SecretStr(ia_access_key) if ia_access_key is not None else None,
        ia_secret=SecretStr(ia_secret) if ia_secret is not None else None,
        spn2_poll_interval_seconds=3.0,
        spn2_poll_timeout_seconds=180.0,
    )


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
def wired(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[SimpleNamespace]:
    """Patcht alle Composition-Root-Deps von wortlaut.cli; gibt Handles zurueck.

    ENV-Defaults: beide IA-Zugangsdaten gesetzt (der normale, produktive Fall);
    Tests, die die Pflicht pruefen, patchen ArchiveSettings selbst (keine
    Zugangsdaten im Namespace). Der gepatchte ArchiveSettings-Ersatz traegt
    die Zugangsdaten als SecretStr im Objekt (nicht nur in der ENV).
    """
    _credentials_env(monkeypatch, access="k-abc-1", secret="s-xyz-2")
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
        patch(
            "wortlaut.cli.ArchiveSettings",
            return_value=_archive_settings_ns(ia_access_key="k-abc-1", ia_secret="s-xyz-2"),
        ),
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


# ── #73: Circuit-Breaker (AC7) + Gründe-Aggregation (AC8) ────────────────


def _archive_failed_outcome(url: str) -> IngestOutcome:
    """archive_failed mit Wayback-404 und archive.today-429 als Labels."""
    return IngestOutcome(
        "archive_failed",
        None,
        f"h-{url}",
        span_count=0,
        archive_failures=("wayback:http_status_404", "archive_today:http_status_429"),
    )


async def test_circuit_breaker_aborts_run(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC7: consecutive_failure_limit=5 erreicht -> Lauf bricht ab, Exit-Code 3,
    nicht mehr als 5 Sources verarbeitet, Ausgabe nennt Abbruchgrund und
    Gründe-Verteilung."""
    wired.adapter.refs = [_ref(f"http://a/p{i}.pdf") for i in range(1, 8)]
    wired.ingest.side_effect = [_archive_failed_outcome(f"http://a/p{i}.pdf") for i in range(1, 8)]
    rc = await _run(_ns())
    cap = capfd.readouterr()
    assert rc == 3
    assert wired.ingest.call_count == 5  # nicht mehr als `limit` Sources
    assert "archive_failed=5" in cap.out
    assert "Circuit-Breaker" in cap.err
    assert "häufigster Grund" in cap.err
    assert (
        "archive_today:http_status_429=5" in cap.err
    )  # häufigster Grund (Gleichstand → alphabetisch)
    # Summary-Zeile mit Gründe-Verteilung (Häufigkeit absteigend, Gleichstand alphabetisch)
    assert "reasons=archive_today:http_status_429=5,wayback:http_status_404=5" in cap.out
    # Abbruch tritt ein, BEVOR die restlichen Refs verarbeitet werden
    assert "p6" not in cap.err


async def test_circuit_breaker_resets_on_success(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC7 (Zähler-Reset): ein Erfolg zwischen den Fehlern setzt die
    aufeinanderfolgenden archive_failed zurück -> kein Abbruch, rc 0.

    Pattern (12 Refs, je 3.): F F I — die kurzen Fails-Serien (max. 2) werden
    durch die Inserts durchbrochen; der aufeinanderfolgende Zähler erreicht nie
    das Limit 5, der Lauf bleibt am Leben.
    """
    wired.adapter.refs = [_ref(f"http://a/p{i}.pdf") for i in range(1, 13)]
    outcomes: list[IngestOutcome] = []
    for i in range(1, 13):
        if i % 3 == 0:
            outcomes.append(IngestOutcome("inserted", None, f"h{i}", span_count=0))
        else:
            outcomes.append(_archive_failed_outcome(f"http://a/p{i}.pdf"))
    wired.ingest.side_effect = outcomes
    rc = await _run(_ns())
    cap = capfd.readouterr()
    assert rc == 0
    assert wired.ingest.call_count == 12
    assert "inserted=4" in cap.out
    assert "archive_failed=8" in cap.out


async def test_circuit_breaker_without_reset_would_abort(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """Gegenprobe zum Reset-Test: WÄRE der Zähler nicht reset, würde die Serie
    p1..p5 (5 consecutive) den Breaker auslösen. Ein Erfolg als p3 verhindert
    das — das zeigt: der Reset ist das einzige, was den Lauf am Leben hält."""
    wired.adapter.refs = [_ref(f"http://a/p{i}.pdf") for i in range(1, 8)]
    outcomes: list[IngestOutcome] = []
    for i in range(1, 8):
        if i == 3:
            outcomes.append(IngestOutcome("inserted", None, f"h{i}", span_count=0))
        else:
            outcomes.append(_archive_failed_outcome(f"http://a/p{i}.pdf"))
    wired.ingest.side_effect = outcomes
    rc = await _run(_ns())
    # p1,p2 = 2 Fails, p3 = Insert (reset), p4..p7 = 4 Fails → unter Limit → rc 0
    assert rc == 0
    assert wired.ingest.call_count == 7
    assert "archive_failed=6" in capfd.readouterr().out


async def test_summary_reports_failure_reasons(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC8: Lauf mit ≥1 Archiv-Fehler -> Summary-Zeile enthält ein
    `reasons=`-Feld mit jedem Grund (Dienst, Kürzel, Anzahl)."""
    wired.adapter.refs = [_ref(f"http://a/p{i}.pdf") for i in range(1, 5)]
    wired.ingest.side_effect = [_archive_failed_outcome(f"http://a/p{i}.pdf") for i in range(1, 5)]
    rc = await _run(_ns())
    out = capfd.readouterr().out
    assert rc == 0
    assert "reasons=archive_today:http_status_429=4,wayback:http_status_404=4" in out


async def test_summary_reports_dash_when_no_failures(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC8 (leerer Counter): keine Archiv-Fehler -> `reasons=-`."""
    wired.adapter.refs = [_ref("http://a/p1.pdf")]
    rc = await _run(_ns())
    out = capfd.readouterr().out
    assert rc == 0
    assert "reasons=-" in out


# ── #77/#108: Pre-Flight-Archiv-Health-Check (User-Status-Probe) ─────────


async def test_preflight_failure_aborts_before_discover(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC1: User-Status-Probe wirft ArchiveError (401) -> Exit 3, discover 0×,
    fetch 0×, Ausgabe nennt 'Pre-Flight' samt Grund — VOR dem ersten Fetch."""
    wired.adapter.refs = [_ref("http://a/p1.pdf"), _ref("http://b/p2.pdf")]
    wired.wayback.user_status_error = ArchiveError(
        "wayback", "unauthorized", status_code=401, transient=False
    )
    rc = await _run(_ns())
    cap = capfd.readouterr()
    assert rc == 3
    assert wired.adapter.discover_calls == 0  # kein DIP-Call
    assert wired.adapter.fetch_calls == 0  # kein Ziel-PDF
    assert wired.ingest.call_count == 0
    assert "Pre-Flight" in cap.err
    assert "401" in cap.err  # Statuscode bleibt in der Meldung erhalten
    assert wired.wayback.user_status_calls == 1  # genau ein Probe-Call


async def test_preflight_healthy_runs_normally(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC2: Probe liefert die User-Status-Zusammenfassung -> Ingest läuft
    normal weiter (Exit 0, Summary wie bisher). Der User-Status-Call geht
    genau einmal raus; es wird KEIN Capture abgesetzt."""
    wired.adapter.refs = [_ref("http://a/p1.pdf"), _ref("http://b/p2.pdf")]
    rc = await _run(_ns())
    cap = capfd.readouterr()
    assert rc == 0
    assert wired.wayback.user_status_calls == 1  # die Probe ging raus
    assert wired.wayback.archive_calls == []  # KEIN Capture durch die Probe
    assert wired.adapter.discover_calls == 1
    assert wired.ingest.call_count == 2  # beide Refs normal verarbeitet
    assert "discovered=2" in cap.out  # Summary wie bisher


async def test_no_preflight_flag_skips_probe(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC4: --no-preflight -> kein Probe-Call, Lauf verhält sich unverändert (Exit 0)."""
    wired.adapter.refs = [_ref("http://a/p1.pdf"), _ref("http://b/p2.pdf")]
    rc = await _run(_ns(no_preflight=True))
    assert rc == 0
    assert wired.wayback.user_status_calls == 0  # kein Probe-Call
    assert wired.ingest.call_count == 2


async def test_preflight_disabled_via_settings_skips_probe(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC4: preflight_enabled=False per ENV -> kein Probe-Call, normaler Lauf."""
    wired.adapter.refs = [_ref("http://a/p1.pdf")]
    with patch(
        "wortlaut.cli.ArchiveSettings",
        return_value=_archive_settings_ns(
            preflight_enabled=False, ia_access_key="k-abc-1", ia_secret="s-xyz-2"
        ),
    ):
        rc = await _run(_ns())
    assert rc == 0
    assert wired.wayback.user_status_calls == 0  # kein Probe-Call
    assert wired.ingest.call_count == 1


async def test_dry_run_skips_probe(
    wired: SimpleNamespace, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC5: --dry-run -> kein Probe-Call, Dry-Run-Zeile unverändert."""
    wired.adapter.refs = [_ref("http://a/p1.pdf"), _ref("http://b/p2.pdf")]
    rc = await _run(_ns(dry_run=True))
    cap = capfd.readouterr()
    assert rc == 0
    assert wired.wayback.user_status_calls == 0  # kein Probe-Call
    assert "dry_run=True" in cap.out  # Dry-Run-Zeile wörtlich unverändert


# ── #108: Zugangsdaten-Pflicht am Composition-Root (AC16/AC17) ───────────


async def test_ohne_zugangsdaten_exit_2(
    wired: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC16: keine IA-Zugangsdaten in der ENV -> rc 2, stderr nennt beide
    ENV-Namen, und es wurde KEIN DIP-Call und KEIN Archiv-Call abgesetzt
    (Abbruch VOR Engine, Bootstrap, Pre-Flight und discover)."""
    _credentials_env(monkeypatch, access=None, secret=None)
    # Der Fixture-Patch liefert Credential-Settings; hier bewusst die ECHTE
    # Klasse (liest die geleerte ENV), damit der Pflicht-Abbruch greift.
    with patch("wortlaut.cli.ArchiveSettings", return_value=ArchiveSettings()):
        wired.adapter.refs = [_ref("http://a/p1.pdf")]
        rc = await _run(_ns())
    cap = capfd.readouterr()
    assert rc == 2
    assert "WORTLAUT_ARCHIVE_IA_ACCESS_KEY" in cap.err
    assert "WORTLAUT_ARCHIVE_IA_SECRET" in cap.err
    assert wired.adapter.discover_calls == 0  # kein DIP-Call
    assert wired.adapter.fetch_calls == 0  # kein Ziel-Fetch
    assert wired.wayback.user_status_calls == 0  # kein Archiv-Call
    assert wired.wayback.archive_calls == []
    assert wired.ingest.call_count == 0
    # Der Abbruch liegt VOR der Engine-Erzeugung — das try/finally (und damit
    # dispose) wird in diesem Pfad nicht betreten.
    assert wired.engine.dispose.await_count == 0


async def test_dry_run_ohne_zugangsdaten_ok(
    wired: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    """AC17: keine IA-Zugangsdaten, ABER --dry-run -> rc 0 (Dry-Run
    archiviert nicht und braucht keine Zugangsdaten)."""
    _credentials_env(monkeypatch, access=None, secret=None)
    with patch("wortlaut.cli.ArchiveSettings", return_value=ArchiveSettings()):
        wired.adapter.refs = [_ref("http://a/p1.pdf"), _ref("http://b/p2.pdf")]
        rc = await _run(_ns(dry_run=True))
    cap = capfd.readouterr()
    assert rc == 0
    assert "dry_run=True" in cap.out
    assert wired.wayback.user_status_calls == 0  # Dry-Run überspringt die Probe
    assert wired.ingest.call_count == 0
