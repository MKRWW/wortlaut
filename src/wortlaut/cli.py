"""CLI (argparse) + Composition-Root fuer ``python -m wortlaut ingest``.

Reine Verdrahtung — keine neue Fetch-/Archiv-/Parse-Logik, kein LLM.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from datetime import datetime

from wortlaut.archive.archiver import ArchiveTodayArchiver, WaybackArchiver
from wortlaut.ingest.dip import DipFetchError, DipPlenarprotokollAdapter
from wortlaut.ingest.settings import DipSettings
from wortlaut.pipeline.ingest import PipelineDeps, ingest_source
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
    except Exception as e:
        print(f"Konfiguration fehlgeschlagen: {e}", file=sys.stderr)
        return 2

    engine = create_async_engine_from(db_settings)
    sessions = make_sessionmaker(engine)
    adapter = DipPlenarprotokollAdapter(dip_settings)
    worm = MinioWormStore(worm_settings)
    wayback = WaybackArchiver()
    atoday = ArchiveTodayArchiver()
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

        inserted = skipped = archive_failed = fetch_error = spans_total = 0

        if args.dry_run:
            print(
                f"discovered={len(refs)} inserted={inserted} "
                f"skipped_duplicate={skipped} archive_failed={archive_failed} "
                f"fetch_error={fetch_error} spans_total={spans_total} dry_run=True"
            )
            return 0

        for ref in refs:
            try:
                async with sessions() as s:
                    outcome = await ingest_source(
                        ref, deps=deps, session=s, rights_basis=args.rights_basis
                    )
                if outcome.status == "inserted":
                    inserted += 1
                elif outcome.status == "skipped_duplicate":
                    skipped += 1
                elif outcome.status == "archive_failed":
                    archive_failed += 1
                spans_total += outcome.span_count
            except (DipFetchError, ValueError) as e:
                fetch_error += 1
                print(f"fetch_error: {ref.origin_url}: {e}", file=sys.stderr)

        print(
            f"discovered={len(refs)} inserted={inserted} "
            f"skipped_duplicate={skipped} archive_failed={archive_failed} "
            f"fetch_error={fetch_error} spans_total={spans_total}"
        )
        return 0
    finally:
        # Jede Cleanup-Aktion einzeln; ein Fehler darf die anderen nicht verschlucken.
        for closer in (adapter.aclose, wayback.aclose, atoday.aclose, engine.dispose):
            with contextlib.suppress(Exception):
                await closer()
