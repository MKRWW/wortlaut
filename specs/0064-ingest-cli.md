# Increment-Spec 0064: Ingest-CLI (`python -m wortlaut ingest`)

- **Issue:** #64 · **Status:** Reviewed · **Phase/Layer:** phase/1 · `cli` (neuer Composition-Root)
- Baut auf #7 (`pipeline.ingest_source`), #6 (DIP-Adapter), #4 (Archiver), #5 (WORM), #40–#42 (Spans).

## 1. Ziel
Laufbarer `python -m wortlaut ingest --since <datum>`: DIP-Plenarprotokolle entdecken und jedes end-to-end
durch die **vorhandene** Pipeline schleusen. Reine Verdrahtung — keine neue Fetch-/Archiv-/Parse-Logik, kein LLM.

## 2. Files (NUR diese anlegen)
- `src/wortlaut/cli.py`            — CLI (argparse) + Composition-Root
- `src/wortlaut/__main__.py`       — delegiert an `cli.main()`
- `src/wortlaut/store/adapters.py` — idempotenter Adapter-Registry-Seed
- `tests/unit/test_cli.py`         — Unit-Tests AC1–AC8

> NICHT anlegen/ändern: `pyproject.toml`, den import-linter-Contract, Integrationstests, irgendeine
> bestehende Datei. Das macht der Architekt separat.

## 3. Was jede Datei enthält

### `src/wortlaut/store/adapters.py`
```python
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

async def ensure_ingest_adapter(
    session: AsyncSession, *, name: str, version: str, trust_level: str
) -> None:
    """Idempotenter get-or-create der ingest_adapter-Registry-Zeile (FK-Voraussetzung für source).
    Muster wie store/spans.py:resolve_or_create_speaker. Bei existierender (name,version) NO-OP,
    kein IntegrityError. Nutzt rohes SQL gegen die Tabelle `ingest_adapter` (Spalten:
    name, version, trust_level) — INSERT ... ON CONFLICT (name, version) DO NOTHING."""
```
- Schau in `store/spans.py` (get-or-create-Muster) und in die Migration/das ORM-Modell für die echten
  Spaltennamen der Tabelle `ingest_adapter`. Wenn eine Spalte fehlt/anders heißt, RICHTE DICH NACH DEM
  ECHTEN SCHEMA (nicht raten).

### `src/wortlaut/cli.py`
```python
def main(argv: list[str] | None = None) -> int:
    # argparse: prog "wortlaut", subcommand "ingest" mit:
    #   --since (Pflicht, YYYY-MM-DD -> datetime), --rights-basis (default "amtliches_werk_p5"),
    #   --limit (int, optional), --no-migrate (store_true), --dry-run (store_true)
    # ruft asyncio.run(_run(args)) und gibt dessen int zurück; unbekanntes/fehlendes Subcommand -> 2.

async def _run(args) -> int:
    # Composition-Root. Reihenfolge:
    # 1) Settings aus ENV: DbSettings(), WormSettings(), DipSettings() — fehlt eine Pflicht-ENV,
    #    fange die pydantic ValidationError, schreib eine klare Meldung nach stderr, return != 0 (z.B. 2).
    # 2) engine = create_async_engine_from(DbSettings()); sessions = make_sessionmaker(engine)
    #    worm = MinioWormStore(WormSettings()); adapter = DipPlenarprotokollAdapter(DipSettings())
    #    wayback = WaybackArchiver(); atoday = ArchiveTodayArchiver()
    #    deps = PipelineDeps(adapter=adapter, wayback=wayback, archive_today=atoday, worm=worm)
    # 3) try: bootstrap
    #      if not args.no_migrate: await upgrade_head(DbSettings().dsn)
    #      await worm.ensure_bucket()
    #      async with sessions() as s: await ensure_ingest_adapter(s, name=adapter.name,
    #          version=adapter.version, trust_level=adapter.trust_level); await s.commit()
    #    refs = list(await adapter.discover(args.since))
    #    if args.limit is not None and len(refs) > args.limit:
    #        print(f"limit: {len(refs)} entdeckt, kappe auf {args.limit}", file=sys.stderr); refs = refs[:args.limit]
    #    Zähler: inserted/skipped_duplicate/archive_failed/fetch_error/spans_total = 0
    #    if args.dry_run: Summary mit dry_run=True ausgeben, return 0 (KEIN ingest_source!)
    #    for ref in refs:
    #        try:
    #            async with sessions() as s:
    #                outcome = await ingest_source(ref, deps=deps, session=s, rights_basis=args.rights_basis)
    #            Zähler nach outcome.status hochzählen; spans_total += outcome.span_count
    #        except (DipFetchError, ValueError) as e:
    #            fetch_error += 1; print(f"fetch_error: {ref.origin_url}: {e}", file=sys.stderr)
    #    Summary nach stdout: f"discovered={len(refs)} inserted={inserted} skipped_duplicate={skipped} "
    #                         f"archive_failed={archive_failed} fetch_error={fetch_error} spans_total={spans_total}"
    #    return 0
    # 4) finally: await adapter.aclose(); await wayback.aclose(); await atoday.aclose(); await engine.dispose()
    #    (jede aclose in ein eigenes try/except, damit ein Fehler die anderen nicht verschluckt)
```
- Imports: `from wortlaut.pipeline.ingest import ingest_source, PipelineDeps`,
  `from wortlaut.ingest.dip import DipPlenarprotokollAdapter, DipFetchError`,
  `from wortlaut.ingest.settings import DipSettings`,
  `from wortlaut.archive.archiver import WaybackArchiver, ArchiveTodayArchiver`,
  `from wortlaut.store.worm import MinioWormStore`, `from wortlaut.store.settings import DbSettings, WormSettings`,
  `from wortlaut.store.db import create_async_engine_from, make_sessionmaker`,
  `from wortlaut.store.migrations import upgrade_head`, `from wortlaut.store.adapters import ensure_ingest_adapter`.
- **KEINE Secrets nach stdout/stderr** (keine ENV-Werte, keine DSN mit Passwort ausgeben).
- ≤5 Params pro Funktion (R-ARCH-04); Deps via `PipelineDeps`.

### `src/wortlaut/__main__.py`
```python
import sys
from wortlaut.cli import main
if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

### `tests/unit/test_cli.py` (AC1–AC8, KEINE Live-Netz-/DB-Calls)
- Baue Fakes: FakeAdapter mit steuerbarer `discover()`-Rückgabe (Liste von `SourceRef`), `name/version/trust_level`,
  `aclose()`; FakeArchiver mit `aclose()`; FakeWorm mit `ensure_bucket()`; FakeSessionmaker/-Session
  (async context manager, `commit()`). Patche `ingest_source`, `upgrade_head` und die Settings-Konstruktoren
  (bzw. setze die Pflicht-ENV via `monkeypatch.setenv`) so, dass KEIN echtes Netz/DB nötig ist.
- `test_ingest_loops_per_ref` (AC1): 2 Refs -> ingest_source 2×, discovered=2 in der Ausgabe (capsys).
- `test_empty_discover_noop` (AC2): 0 Refs -> rc 0, ingest_source 0×.
- `test_partial_outcomes_dont_abort` (AC3): outcomes [archive_failed, inserted] -> rc 0, inserted=1 archive_failed=1.
- `test_fetch_error_caught` (AC4): eine Quelle wirft DipFetchError -> gefangen, fetch_error=1, Rest läuft, rc 0.
- `test_missing_env_exits_nonzero` (AC5): Pflicht-ENV nicht gesetzt -> rc != 0, ingest_source 0×.
- `test_resources_closed_in_finally` (AC6): adapter.aclose + beide archiver.aclose + engine.dispose je 1×
  (auch wenn discover/ingest wirft -> mit einer werfenden Variante prüfen).
- `test_dry_run_no_ingest` (AC7): --dry-run -> discover läuft, ingest_source 0×, Ausgabe markiert dry-run.
- `test_limit_caps_and_logs` (AC8): 3 Refs, --limit 1 -> ingest_source 1×, Kappungs-Log auf stderr.

## 4. Do-NOT (hart)
- KEINE git/docker/uv/npm/alembic-Befehle ausführen (nur die im Abschnitt Abschluss).
- KEINE bestehende Datei editieren, KEIN `pyproject.toml`, KEINE Integrationstests, KEIN Contract.
- KEIN LLM/kein Netz-Call in Unit-Tests. Keine Secrets in Ausgaben.
- Regex/Backslashes NICHT verdoppeln; keine erfundenen Spaltennamen — echtes Schema lesen.

## 5. Abschluss (und NUR das an Kommandos ausführen)
- `git status --porcelain` ausgeben.
