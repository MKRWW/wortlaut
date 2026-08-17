"""CLI (argparse) + Composition-Root fuer ``python -m wortlaut ingest``.

Reine Verdrahtung — keine neue Fetch-/Archiv-/Parse-Logik, kein LLM.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

import uvicorn
from pydantic import ValidationError

from wortlaut.archive.archiver import ArchiveTodayArchiver, WaybackArchiver
from wortlaut.archive.errors import ArchiveError
from wortlaut.archive.preflight import probe_archive
from wortlaut.archive.settings import ArchiveSettings
from wortlaut.archive.throttle import DisableAfterFailures, RateLimiter
from wortlaut.ingest.dip import DipFetchError, DipPlenarprotokollAdapter
from wortlaut.ingest.settings import DipSettings
from wortlaut.pipeline.ingest import IngestOutcome, PipelineDeps, ingest_source
from wortlaut.pipeline.timestamp import TimestampOutcome, timestamp_source
from wortlaut.serving.settings import ApiSettings
from wortlaut.store.adapters import ensure_ingest_adapter
from wortlaut.store.db import create_async_engine_from, make_sessionmaker
from wortlaut.store.migrations import upgrade_head
from wortlaut.store.settings import DbSettings, WormSettings
from wortlaut.store.timestamps import list_sources_without_timestamp
from wortlaut.store.worm import MinioWormStore
from wortlaut.timestamp.profiles import load_profile
from wortlaut.timestamp.settings import TimestampSettings
from wortlaut.timestamp.tsa import FallbackTimeStamper, Rfc3161Tsa


def main(argv: list[str] | None = None) -> int:
    """Eintrittspunkt. Unbekanntes/fehlendes Subcommand -> 2."""
    parser = argparse.ArgumentParser(prog="wortlaut")
    subparsers = parser.add_subparsers(dest="subcommand")

    p_ingest = subparsers.add_parser("ingest")
    p_ingest.add_argument("--since", required=True, type=datetime.fromisoformat)
    p_ingest.add_argument("--rights-basis", default="amtliches_werk_p5")
    p_ingest.add_argument("--limit", type=int, default=None)
    p_ingest.add_argument("--no-migrate", action="store_true")
    p_ingest.add_argument("--dry-run", action="store_true")
    p_ingest.add_argument("--no-preflight", action="store_true")

    p_timestamp = subparsers.add_parser("timestamp")
    p_timestamp.add_argument("--limit", type=int, default=None)
    p_timestamp.add_argument("--no-migrate", action="store_true")
    p_timestamp.add_argument("--dry-run", action="store_true")

    subparsers.add_parser("serve")

    args = parser.parse_args(argv)

    subcommand = getattr(args, "subcommand", None)
    if subcommand not in ("ingest", "timestamp", "serve"):
        print("Fehler: Subcommand 'ingest', 'timestamp' oder 'serve' erforderlich", file=sys.stderr)
        return 2

    if subcommand == "ingest":
        return asyncio.run(_run(args))
    if subcommand == "timestamp":
        return asyncio.run(_run_timestamp(args))
    # uvicorn bringt seinen eigenen Event-Loop mit — kein asyncio.run drumherum.
    return _run_serve()


async def _run(args: argparse.Namespace) -> int:
    """Composition-Root. Reihenfolge: Settings -> Engine -> Adapter -> Loop."""
    # 1) Settings aus ENV
    try:
        db_settings = DbSettings()
        worm_settings = WormSettings()
        dip_settings = DipSettings()
        archive_settings = ArchiveSettings()
    except Exception as e:
        print(f"Konfiguration fehlgeschlagen: {_config_error(e)}", file=sys.stderr)
        return 2

    engine = create_async_engine_from(db_settings)
    sessions = make_sessionmaker(engine)
    adapter = DipPlenarprotokollAdapter(dip_settings)
    worm = MinioWormStore(worm_settings)
    wayback, atoday_inner, atoday = _build_archivers(archive_settings)
    deps = PipelineDeps(adapter=adapter, wayback=wayback, archive_today=atoday, worm=worm)

    try:
        # 2) Bootstrap
        if not args.no_migrate:
            await upgrade_head(db_settings.dsn)
        await worm.ensure_bucket()
        async with sessions() as s:
            await ensure_ingest_adapter(
                s,
                name=adapter.name,
                version=adapter.version,
                trust_level=adapter.trust_level,
            )
            await s.commit()

        # 3) Pre-Flight-Archiv-Health-Check (Spec 0077), NACH dem Bootstrap und
        #    VOR discover — bei totem Fremdarchiv wird damit kein DIP-Call und
        #    kein Ziel-PDF geladen.
        if not await _preflight_ok(args, archive_settings, wayback):
            return 3

        # 4) Discover + Loop
        try:
            refs = list(await adapter.discover(args.since))
        except (DipFetchError, ValueError) as e:
            print(f"discover fehlgeschlagen: {e}", file=sys.stderr)
            return 2

        if args.limit is not None and len(refs) > args.limit:
            print(
                f"limit: {len(refs)} entdeckt, kappe auf {args.limit}",
                file=sys.stderr,
            )
            refs = refs[: args.limit]

        stats = _RunStats()
        breaker_limit = archive_settings.consecutive_failure_limit

        if args.dry_run:
            print(
                f"discovered={len(refs)} inserted=0 "
                f"skipped_duplicate=0 archive_failed=0 "
                f"fetch_error=0 spans_total=0 dry_run=True"
            )
            return 0

        for ref in refs:
            try:
                async with sessions() as s:
                    outcome = await ingest_source(
                        ref, deps=deps, session=s, rights_basis=args.rights_basis
                    )
                stats.record(outcome)
                if outcome.status == "archive_failed":
                    labels = ",".join(outcome.archive_failures)
                    print(f"archive_failed: {ref.origin_url}: {labels}", file=sys.stderr)
            except (DipFetchError, ValueError) as e:
                stats.fetch_error += 1
                print(f"fetch_error: {ref.origin_url}: {e}", file=sys.stderr)

            # Circuit-Breaker (Q1): anhaltender Archiv-Ausfall bricht früh und
            # diagnostizierbar ab — Exit 3 (abgegrenzt von 2 = Konfiguration).
            # Limit <= 0 schaltet den Breaker ab.
            if 0 < breaker_limit <= stats.consecutive_archive_failed:
                print(
                    f"Circuit-Breaker: {breaker_limit} aufeinanderfolgende archive_failed "
                    f"— Abbruch, häufigster Grund: {stats.top_reason()}",
                    file=sys.stderr,
                )
                print(stats.summary_line(len(refs)))
                return 3

        print(stats.summary_line(len(refs)))
        return 0
    finally:
        await _aclose_all(adapter.aclose, wayback.aclose, atoday_inner.aclose, engine.dispose)


async def _run_timestamp(args: argparse.Namespace) -> int:
    """Composition-Root für den Stempel-Pass (Spec 0076 §11).

    Settings → Engine → Stempeler → Pass. Exit: 0 = ok, 2 = Konfiguration,
    3 = Circuit-Breaker, 4 = hash_mismatch.
    """
    # 1) Settings aus ENV UND Stempeler bauen — beides ist Konfiguration und endet
    #    im selben Exit 2. Ein gemeinsamer Block, damit es dafür genau EINEN
    #    Rückgabepunkt gibt (unbekanntes Profil und leere Profilliste werfen
    #    ValueError, fehlende ENV wirft ValidationError).
    try:
        db_settings = DbSettings()
        worm_settings = WormSettings()
        tsa_settings = TimestampSettings()
        stamper = FallbackTimeStamper(
            [
                Rfc3161Tsa(load_profile(name), timeout_seconds=tsa_settings.timeout_seconds)
                for name in tsa_settings.profile_names()
            ]
        )
    except Exception as e:
        print(f"Konfiguration fehlgeschlagen: {_config_error(e)}", file=sys.stderr)
        return 2

    engine = create_async_engine_from(db_settings)
    sessions = make_sessionmaker(engine)
    worm = MinioWormStore(worm_settings)

    try:
        # 3) Bootstrap
        if not args.no_migrate:
            await upgrade_head(db_settings.dsn)
        await worm.ensure_bucket()

        # 4) Pending-Quellen laden (abgeleitet: keine source_timestamp-Zeile).
        async with sessions() as s:
            pending = await list_sources_without_timestamp(s, limit=args.limit)

        if args.dry_run:
            print(f"pending={len(pending)} dry_run=True")
            return 0

        # 5) Pass: je Quelle timestamp_source in einer eigenen Session.
        stats = _TimestampStats()
        breaker_limit = tsa_settings.consecutive_failure_limit

        for source in pending:
            async with sessions() as s:
                outcome = await timestamp_source(source, session=s, worm=worm, stamper=stamper)
            stats.record(outcome)
            if outcome.status == "hash_mismatch":
                print(
                    f"hash_mismatch: {outcome.source_id} (WORM-Bytes passen nicht zum Ledger-Hash)",
                    file=sys.stderr,
                )

            # Circuit-Breaker: aufeinanderfolgende tsa_failed (Muster #73).
            if 0 < breaker_limit <= stats.consecutive_tsa_failed:
                print(
                    f"Circuit-Breaker: {breaker_limit} aufeinanderfolgende tsa_failed "
                    f"— Abbruch, häufigster Grund: {stats.top_reason()}",
                    file=sys.stderr,
                )
                print(stats.summary_line(len(pending)))
                return 3

        print(stats.summary_line(len(pending)))
        # hash_mismatch ist ein Alarm (nicht Statistik): Exit 4, abgegrenzt von 0/2/3.
        return 4 if stats.hash_mismatch > 0 else 0
    finally:
        await _aclose_all(stamper.aclose, engine.dispose)


def _run_serve() -> int:
    """Composition-Root fuer ``serve``: ENV pruefen, dann uebernimmt uvicorn.

    Die Settings werden hier NUR validiert (fail-fast, Exit 2 wie ``ingest``); gebaut
    wird je Worker-Prozess in ``wortlaut.serving.asgi.create_asgi_app`` — deshalb der
    Import-String statt einer App-Instanz (sonst ignoriert uvicorn ``workers``).
    """
    try:
        DbSettings()  # Validierung, kein toter Code: fehlende ENV -> Exit 2
        WormSettings()  # dito
        api = ApiSettings()
    except Exception as e:
        print(f"Konfiguration fehlgeschlagen: {_config_error(e)}", file=sys.stderr)
        return 2

    uvicorn.run(
        "wortlaut.serving.asgi:create_asgi_app",
        factory=True,
        host=api.host,
        port=api.port,
        workers=api.workers,
    )
    return 0


def _config_error(e: Exception) -> str:
    """Konfigurationsfehler OHNE ENV-Werte (R-SEC-01, Spec 0081 AC4).

    pydantic haengt an eine ``ValidationError`` das komplette Eingabe-Dict an —
    bei ``WormSettings`` also den Wert von ``WORTLAUT_WORM_SECRET_KEY``:
    ``input_value={'endpoint': ..., 'secret_key': 'TOPSECRET123'}``. Das landete
    damit woertlich auf stderr und in jedem Container-Log. Genannt werden
    deshalb ausschliesslich die betroffenen Feldnamen — das ist fuer den
    Betreiber ohnehin die nuetzlichere Information.

    Andere Fehler (z.B. unbekanntes TSA-Profil) tragen unseren eigenen Text ohne
    ENV-Werte und bleiben unveraendert diagnostizierbar.
    """
    if isinstance(e, ValidationError):
        fields = ", ".join(str(err["loc"][0]) for err in e.errors() if err["loc"])
        return f"fehlende oder ungueltige ENV-Felder: {fields or '-'}"
    return str(e)


async def _aclose_all(*closers: Callable[[], Awaitable[object]]) -> None:
    """Schliesst jede Ressource einzeln; ein Fehler darf die anderen nicht verschlucken."""
    for closer in closers:
        with contextlib.suppress(Exception):
            await closer()


async def _preflight_ok(
    args: argparse.Namespace, settings: ArchiveSettings, wayback: WaybackArchiver
) -> bool:
    """Pre-Flight-Probe (Spec 0077). ``False`` ⇒ der Lauf bricht mit Exit 3 ab.

    Übersprungen bei ``--no-preflight``, ``--dry-run`` oder
    ``preflight_enabled=false`` (§4.5/§4.6) — dann wird **kein** Call abgesetzt.
    Nur ``ArchiveError`` wird gefangen; ein ``SsrfBlocked`` ist ein
    Security-Stopp und fliegt unverändert durch (Festlegung aus #73).
    """
    if args.no_preflight or args.dry_run or not settings.preflight_enabled:
        return True
    try:
        await probe_archive(wayback, probe_url=settings.preflight_url)
    except ArchiveError as e:
        print(
            f"Pre-Flight: Fremdarchiv nicht funktionsfaehig ({e}) "
            f"— Abbruch vor dem ersten Ziel-Fetch",
            file=sys.stderr,
        )
        return False
    return True


def _build_archivers(
    settings: ArchiveSettings,
) -> tuple[WaybackArchiver, ArchiveTodayArchiver, DisableAfterFailures]:
    """Baut den Archiv-Stack: gedrosselt, retry-fähig, optionaler Dienst abschaltbar.

    Liefert auch den INNEREN archive.today-Archiver zurück — nur der hält den
    httpx-Client und muss im Cleanup geschlossen werden.
    """
    wayback = WaybackArchiver(
        limiter=RateLimiter(settings.wayback_min_interval_seconds),
        attempts=settings.retry_attempts,
        base_delay_seconds=settings.retry_base_delay_seconds,
    )
    atoday_inner = ArchiveTodayArchiver(
        limiter=RateLimiter(settings.archive_today_min_interval_seconds),
        attempts=settings.retry_attempts,
        base_delay_seconds=settings.retry_base_delay_seconds,
    )
    atoday = DisableAfterFailures(
        atoday_inner, service="archive_today", limit=settings.optional_failure_limit
    )
    return wayback, atoday_inner, atoday


@dataclass
class _TimestampStats:
    """Stempel-Pass-Laufzähler + Gründe-Verteilung als EIN Bündel (R-ARCH-04)."""

    stamped: int = 0
    hash_mismatch: int = 0
    worm_missing: int = 0
    tsa_failed: int = 0
    consecutive_tsa_failed: int = 0
    reasons: Counter[str] = field(default_factory=Counter)

    def record(self, outcome: TimestampOutcome) -> None:
        """Bucht ein TimestampOutcome ein; ein Erfolg bricht die Fehlerserie."""
        status = outcome.status
        self.reasons.update(outcome.failures)
        if status == "stamped":
            self.stamped += 1
            self.consecutive_tsa_failed = 0
        elif status == "hash_mismatch":
            self.hash_mismatch += 1
            self.consecutive_tsa_failed = 0
        elif status == "worm_missing":
            self.worm_missing += 1
            self.consecutive_tsa_failed = 0
        elif status == "tsa_failed":
            self.tsa_failed += 1
            self.consecutive_tsa_failed += 1

    def _ordered_reasons(self) -> list[tuple[str, int]]:
        """Häufigkeit absteigend, bei Gleichstand alphabetisch."""
        return sorted(self.reasons.items(), key=lambda item: (-item[1], item[0]))

    def top_reason(self) -> str:
        """Häufigster Grund als ``<label>=<n>``; ``-`` wenn keiner erfasst ist."""
        ordered = self._ordered_reasons()
        if not ordered:
            return "-"
        label, count = ordered[0]
        return f"{label}={count}"

    def summary_line(self, pending: int) -> str:
        """Summary-Zeile; Felder in fester Reihenfolge, reasons= angehängt (Muster _RunStats)."""
        ordered = self._ordered_reasons()
        reasons_field = ",".join(f"{label}={count}" for label, count in ordered) or "-"
        return (
            f"pending={pending} stamped={self.stamped} "
            f"hash_mismatch={self.hash_mismatch} worm_missing={self.worm_missing} "
            f"tsa_failed={self.tsa_failed} reasons={reasons_field}"
        )


@dataclass
class _RunStats:
    """Laufzähler + Gründe-Verteilung als EIN Bündel (R-ARCH-04: max. 5 Parameter)."""

    inserted: int = 0
    skipped: int = 0
    archive_failed: int = 0
    fetch_error: int = 0
    spans_total: int = 0
    consecutive_archive_failed: int = 0
    reasons: Counter[str] = field(default_factory=Counter)

    def record(self, outcome: IngestOutcome) -> None:
        """Bucht ein Ingest-Ergebnis ein; Erfolg/Dedup bricht die Fehlerserie."""
        self.reasons.update(outcome.archive_failures)
        if outcome.status == "inserted":
            self.inserted += 1
            self.consecutive_archive_failed = 0
        elif outcome.status == "skipped_duplicate":
            self.skipped += 1
            self.consecutive_archive_failed = 0
        elif outcome.status == "archive_failed":
            self.archive_failed += 1
            self.consecutive_archive_failed += 1
        self.spans_total += outcome.span_count

    def _ordered_reasons(self) -> list[tuple[str, int]]:
        """Häufigkeit absteigend, bei Gleichstand alphabetisch."""
        return sorted(self.reasons.items(), key=lambda item: (-item[1], item[0]))

    def top_reason(self) -> str:
        """Häufigster Grund als ``<label>=<n>``; ``-`` wenn keiner erfasst ist."""
        ordered = self._ordered_reasons()
        if not ordered:
            return "-"
        label, count = ordered[0]
        return f"{label}={count}"

    def summary_line(self, discovered: int) -> str:
        """Summary; bestehende Felder/Reihenfolge unverändert, reasons= angehängt."""
        ordered = self._ordered_reasons()
        reasons_field = ",".join(f"{label}={count}" for label, count in ordered) or "-"
        return (
            f"discovered={discovered} inserted={self.inserted} "
            f"skipped_duplicate={self.skipped} archive_failed={self.archive_failed} "
            f"fetch_error={self.fetch_error} spans_total={self.spans_total} "
            f"reasons={reasons_field}"
        )
