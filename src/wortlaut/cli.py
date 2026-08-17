"""CLI (argparse) + Composition-Root fuer ``python -m wortlaut ingest``.

Reine Verdrahtung — keine neue Fetch-/Archiv-/Parse-Logik, kein LLM.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from wortlaut.archive.archiver import ArchiveTodayArchiver, WaybackArchiver
from wortlaut.archive.settings import ArchiveSettings
from wortlaut.archive.throttle import DisableAfterFailures, RateLimiter
from wortlaut.ingest.dip import DipFetchError, DipPlenarprotokollAdapter
from wortlaut.ingest.settings import DipSettings
from wortlaut.pipeline.ingest import IngestOutcome, PipelineDeps, ingest_source
from wortlaut.store.adapters import ensure_ingest_adapter
from wortlaut.store.db import create_async_engine_from, make_sessionmaker
from wortlaut.store.migrations import upgrade_head
from wortlaut.store.settings import DbSettings, WormSettings
from wortlaut.store.worm import MinioWormStore


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

    args = parser.parse_args(argv)

    if getattr(args, "subcommand", None) != "ingest":
        print("Fehler: Subcommand 'ingest' erforderlich", file=sys.stderr)
        return 2

    return asyncio.run(_run(args))


async def _run(args: argparse.Namespace) -> int:
    """Composition-Root. Reihenfolge: Settings -> Engine -> Adapter -> Loop."""
    # 1) Settings aus ENV
    try:
        db_settings = DbSettings()
        worm_settings = WormSettings()
        dip_settings = DipSettings()
        archive_settings = ArchiveSettings()
    except Exception as e:
        print(f"Konfiguration fehlgeschlagen: {e}", file=sys.stderr)
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

        # 3) Discover + Loop
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
        # Jede Cleanup-Aktion einzeln; ein Fehler darf die anderen nicht verschlucken.
        for closer in (adapter.aclose, wayback.aclose, atoday_inner.aclose, engine.dispose):
            with contextlib.suppress(Exception):
                await closer()


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
