# Increment-Spec: DB-Schema Phase 0 — `ingest_adapter` + `source` + Immutability (#2)

- **Story/Issue:** #2 · **Status:** Reviewed · **Phase/Layer:** phase/0 · `store`
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)
- Baut auf **#16** (Persistenz-Fundament: async ORM-`Base`, Alembic-async, Testcontainers).

## 1. Ziel
Die ersten Beweis-Tabellen strukturell unveränderlich machen: `ingest_adapter` und
`source` samt Enums (`source_type`, `rights_basis`, `trust_level`), Dedup über
`content_hash`, Fremdarchiv-Pflicht und **DB-Trigger-Immutability** — damit Rohquellen
append-only erfasst werden und selbst ein kompromittierter App-Layer sie nicht still
ändern kann. Schema laut [docs/datamodel.md](../docs/datamodel.md) §2, §3.1, §3.2, §4.

## 2. Nicht-Ziele (Scope-Grenze)
- **Keine** weiteren Tabellen: `speaker`/`mandate`/`span`/`span_state`/`span_embedding`/
  `topic_tag`/`span_topic`/`rebuttal`/`audit_log`/`ledger_anchor` → spätere Increments.
- **Keine** Enums außer den dreien oben (`verification`/`visibility_class` kommen mit `span_state`).
- **Keine** Ingest-Pipeline, kein Hashing/Archiv/WORM-Code (das sind #3–#7), **keine** echten Daten, **keine** AI.
- **Kein** Anti-Halluzination-Gate, kein Retrieval.
- Immutability wird **nicht** übers ORM erzwungen, sondern über DB-Trigger (ADR-0003 rev.).

## 3. Betroffene Interfaces / Öffentliche Signaturen

### 3a. Alembic-Migration (rohes SQL, Quelle der Wahrheit = datamodel §2/§3.1/§3.2/§4)
```
migrations/versions/0002_*.py   # down_revision = "0001" (pgvector-Extension aus #16)
```
`upgrade()` (per `op.execute`, DDL verbatim aus datamodel):
1. `CREATE TYPE source_type … / rights_basis … / trust_level …`   (§2, nur diese drei)
2. `CREATE TABLE ingest_adapter (…)`                              (§3.1, PK `(name, version)`)
3. `CREATE TABLE source (…)` inkl. FK `(adapter_name, adapter_version)→ingest_adapter`,
   `content_hash char(64) UNIQUE`, `CONSTRAINT chk_archive`, `CONSTRAINT chk_rights`,
   `rights_basis NOT NULL`  (§3.2 verbatim)
4. `CREATE FUNCTION forbid_mutation()` + Trigger `trg_source_immutable`
   `BEFORE UPDATE OR DELETE ON source` (§4); analog `trg_ingest_adapter_immutable`
   auf `ingest_adapter` (dort in §0/§3.1 als „immutabel je Version" deklariert).
`downgrade()`: Trigger → Tabellen → Function → Enum-Typen (umgekehrte Reihenfolge).
> `gen_random_uuid()` ist in PostgreSQL 16 Core (keine Extension nötig).

### 3b. ORM-Modelle (nur Lese-/Schreib-Ergonomie, keine Business-Logik)
```python
# src/wortlaut/store/models.py   (nutzt Base aus #16 store/db.py; R-ARCH-02: store importiert keinen Layer)
class IngestAdapter(Base):
    __tablename__ = "ingest_adapter"
    name:        Mapped[str]           = mapped_column(Text, primary_key=True)
    version:     Mapped[str]           = mapped_column(Text, primary_key=True)
    trust_level: Mapped[str]           = mapped_column(ENUM("verified_primary","secondary","low",
                                                            name="trust_level", create_type=False))
    description: Mapped[str | None]    = mapped_column(Text)
    created_at:  Mapped[datetime]      = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())

class Source(Base):
    __tablename__ = "source"
    id:            Mapped[UUID]        = mapped_column(PgUUID(as_uuid=True), primary_key=True,
                                                       server_default=func.gen_random_uuid())
    source_type:   Mapped[str]         = mapped_column(ENUM(name="source_type", create_type=False))
    rights_basis:  Mapped[str]         = mapped_column(ENUM(name="rights_basis", create_type=False))  # NOT NULL
    adapter_name:  Mapped[str]         = mapped_column(Text)
    adapter_version: Mapped[str]       = mapped_column(Text)
    origin_url:    Mapped[str]         = mapped_column(Text)
    content_hash:  Mapped[str]         = mapped_column(CHAR(64), unique=True)   # SHA-256, Dedup-Anker
    byte_size:     Mapped[int]         = mapped_column(BigInteger)
    mime_type:     Mapped[str]         = mapped_column(Text)
    retrieved_at:  Mapped[datetime]    = mapped_column(TIMESTAMP(timezone=True))
    raw_bytes_ref: Mapped[str]         = mapped_column(Text)
    archive_wayback: Mapped[str | None]= mapped_column(Text)
    archive_today:   Mapped[str | None]= mapped_column(Text)
    warc_ref:      Mapped[str | None]  = mapped_column(Text)
    normalized_text: Mapped[str | None]= mapped_column(Text)
    created_at:    Mapped[datetime]    = mapped_column(TIMESTAMP(timezone=True), server_default=func.now())
    __table_args__ = (ForeignKeyConstraint(["adapter_name","adapter_version"],
                                           ["ingest_adapter.name","ingest_adapter.version"]),)
```
- **Enum-Typen** werden von der Migration erzeugt → im ORM `create_type=False` (kein doppeltes CREATE).
- **Constraints/Trigger/CHECK** leben in der Migration (rohes SQL), nicht im ORM. Das ORM „kennt"
  die Constraints nicht — Tests prüfen die DB-Ebene, nicht das ORM.

## 4. Design (kurz, verweist auf ADRs/Docs)
- **Immutability über DB-Trigger, nicht ORM** (ADR-0003 rev., datamodel §4, R-DATA-01): `forbid_mutation()`
  wirft bei `UPDATE`/`DELETE`. Korrektur einer Quelle = **neue** `source`-Zeile (neuer Hash), nie Mutation.
- **Dedup = Constraint, nicht Code:** `content_hash char(64) UNIQUE`. Zweiter Insert desselben Hashes scheitert
  auf DB-Ebene (die Pipeline in #3/#7 fängt das ab, aber die Garantie liegt in der DB).
- **Provenienz-Pflicht als CHECK:** `chk_archive` erzwingt ≥1 Fremdarchiv (Legal §1/§10); `rights_basis NOT NULL`.
- **Adapter als Naht:** `source` referenziert `(adapter_name, adapter_version)` → `ingest_adapter`; `trust_level`
  dort steuert später den Start-`verification`-State (nicht Teil von #2).
- **Rohes SQL in Alembic** (statt ORM-`metadata.create_all`) für Enums, CHECKs und Trigger — die
  Immutabilitäts- und Provenienz-Invarianten sind DB-Wahrheit, nicht ORM-Konvention.

## 5. Testbare Akzeptanzkriterien (Given/When/Then)
- [ ] **AC1** *Given* frische DB, *When* `alembic upgrade head`, *Then* existieren Tabellen `ingest_adapter`,
      `source` **und** Enum-Typen `source_type`, `rights_basis`, `trust_level` (Katalog-Abfrage). `[integration]`
- [ ] **AC2** *Given* eine `source`-Zeile, *When* `UPDATE source …`, *Then* Exception (append-only Trigger). `[integration]`
- [ ] **AC3** *Given* eine `source`-Zeile, *When* `DELETE FROM source …`, *Then* Exception (append-only Trigger). `[integration]`
- [ ] **AC4** *Given* eine `source` mit `content_hash = H`, *When* zweiter Insert mit demselben `H`,
      *Then* `UNIQUE`-Verletzung (Dedup). `[integration]`
- [ ] **AC5** *Given* Insert einer `source` mit `archive_wayback IS NULL AND archive_today IS NULL`,
      *Then* `chk_archive`-Verletzung; *When* ≥1 Archiv gesetzt, *Then* Insert ok. `[integration]`
- [ ] **AC6** *Given* Insert einer `source` mit unbekanntem `(adapter_name, adapter_version)`,
      *Then* FK-Verletzung; *When* Adapter existiert, *Then* Insert ok. `[integration]`
- [ ] **AC7** *Given* Insert einer `source` **ohne** `rights_basis`, *Then* `NOT NULL`-Verletzung (Legal §10). `[integration]`
- [ ] **AC8** *Given* eine `ingest_adapter`-Zeile, *When* `UPDATE`/`DELETE`, *Then* Exception (immutabel je Version). `[integration]`
- [ ] **AC9** *Given* `downgrade` nach `upgrade`, *Then* Tabellen, Function, Trigger und Enum-Typen sind
      wieder weg (Migration reversibel). `[integration]`
> Jedes AC ist von einem automatisierten Integrationstest mit Ja/Nein beantwortbar.

## 6. Testplan (Test-zu-AC-Mapping)
- **Integration (Testcontainers Postgres, Harness aus #16; `alembic upgrade head` im Setup):**
  - `test_schema_objects_exist` (pg_type/information_schema) → **AC1**
  - `test_source_update_forbidden` → **AC2** · `test_source_delete_forbidden` → **AC3**
  - `test_source_content_hash_unique` → **AC4**
  - `test_source_requires_archive` (+ Positivfall) → **AC5**
  - `test_source_fk_adapter` (+ Positivfall) → **AC6**
  - `test_source_rights_basis_not_null` → **AC7**
  - `test_ingest_adapter_immutable` → **AC8**
  - `test_migration_0002_downgrade_clean` → **AC9**
  - Test-Helfer: `_insert_adapter()` / `_insert_source(**overrides)` mit gültigen Defaults.
- **Unit (schnell, rein, kein Container):**
  - `test_models_map_expected_tables_and_columns` — ORM-`__tablename__`/Spaltennamen == Schema (Mapping-Sanity, kein DB-Roundtrip).
- Fehler-Asserts über `sqlalchemy.exc.IntegrityError` bzw. `DBAPIError`/`ProgrammingError` (Trigger-`RAISE`).

## 7. Recht / Security
- **Immutability-Invariante (R-DATA-01, Security §6):** `source`/`ingest_adapter` append-only via Trigger — Kern-Beweis-Garantie.
- **rights_basis Pflicht (Legal §10):** `NOT NULL`; `ungeklaert` wird später nie ausgespielt (App-Gate, nicht #2).
- **Provenienz (README §1, Legal §1/§10):** `chk_archive` (≥1 Fremdarchiv) + `content_hash` (Anker/Dedup) strukturell erzwungen.
- **Keine Secrets/DSN im Code** (R-SEC-01) — unverändert aus #16 (ENV).

## 8. Risiken & offene Fragen
- **Native PG-ENUM + ORM:** `create_type=False` nötig, sonst versucht das ORM ein zweites `CREATE TYPE`. In Migration bewusst rohes SQL.
- **Downgrade-Reihenfolge:** Trigger vor Tabellen, Enums zuletzt droppen (sonst „type is used"). Von AC9 abgedeckt.
- **`gen_random_uuid()`**: Core in PG16 (Digest-gepinntes `pgvector/pgvector`-Image aus #16 ist PG16) — kein `pgcrypto` nötig; falls Image-Basis <13, wäre Extension nötig (nicht der Fall).
- **Trigger auf `ingest_adapter`** geht über datamodel §4 (nur `source`/`span` gelistet) hinaus, folgt aber §0/§3.1 („immutabel je Version"). Bewusste, konservative Härtung; per AC8 getestet.
- `chk_rights`-CHECK aus §3.2 ist ein No-op (`… OR true`). **Entscheidung (Stakeholder): bleibt drin** —
  konservativ, datamodel §3.2 **verbatim**, als Doku-Platzhalter fürs spätere App-seitige Rechte-Gate.
  Nicht sinnvoll testbar (passt immer), daher **kein eigenes AC** — nur Bestandteil von AC1 (Objekt existiert).

## 9. Definition of Done (Verweis)
[../docs/rules.md](../docs/rules.md) DoD: alle AC grün (inkl. Integration-Job), alle Gates grün
(Lint·Type·Test·Coverage ≥80, Security-Gate, Architektur-Fitness), Review, Immutabilitäts-/
Provenienz-Invarianten gewahrt, keine Gott-Klassen, kein Secret/Pickle/LLM-Freitext. PR referenziert **#2**.
