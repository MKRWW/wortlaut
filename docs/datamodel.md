
# wortlaut — Datenmodell & Schnittstellen-Spec

> Konkretisiert das v0-Datenmodell aus [Architektur](architecture.md) §3 und baut die
> Anforderungen aus [Recht](legal.md) §10 und
> [Security](security.md) §6 direkt ins Schema ein.
> **Ziel:** ein Schema, das die Kern-Prinzipien nicht *erlaubt* zu verletzen,
> sondern *strukturell verhindert*.

**Stack:** PostgreSQL 16 + `pgvector` (dense) + Postgres-FTS (sparse/BM25-nah),
Python/FastAPI. Ein Store für relational + Vektor + Volltext.

---

## 0. Die Prinzipien, die das Schema erzwingt

| Prinzip (Quelle) | Wie das Schema es erzwingt |
|------------------|----------------------------|
| Provenienz vor allem (README §1) | `span.source_id` NOT NULL; `source` nur mit Hash+Archiv anlegbar (CHECK) |
| Wortlaut oder nichts (README §1) | `span.verbatim_text` immutable + Offsets + Anti-Halluzination-Gate (§5) |
| Maschine urteilt nicht (README §1) | Klassifikation liegt in `span_topic`, **nie** in `span.verbatim_text` |
| Parteineutral (README §1) | `party` = freies `text`, kein Enum; kein Ranking-Feld |
| Nur Amtsträger, nur öffentlich (Legal §1) | `span` braucht `mandate_id`; `source` braucht `public_evidence` |
| Rechtsgrundlage pro Quelle (Legal §10) | `source.rights_basis` NOT NULL; `ungeklaert` wird nie ausgespielt |
| Beweis-erhaltende Redaction (Legal §10) | Immutable `span` + mutabler `span_state` + `audit_log` |
| Immutability (Security §6) | Append-only Trigger auf `source`/`span`-Inhaltsspalten |
| Anti-Halluzination (Security §6) | Byte-Match `verbatim_text` gegen verhashte Quelle vor Output |
| Trust pro Ingest-Adapter (Security §6) | `source.adapter` → `ingest_adapter.trust_level` steuert Verify-Pflicht |

---

## 1. Entity-Überblick

```
ingest_adapter ──< source ──< span >── speaker ──< mandate
                     │           │
                     │           ├── span_state   (1:1, mutabel, auditiert)
                     │           ├── span_topic >── topic_tag   (n:m, Metadaten)
                     │           └── rebuttal      (Betroffenen-Replik)
                     │
                  (WORM raw bytes, Fremdarchive)

audit_log        (append-only, jede Statusänderung)
ledger_anchor    (periodischer Merkle-Root, externe Verankerung)
```

**Immutabel (Beweis):** `ingest_adapter`, `source`, `span` (Inhalt), `span_topic`.
**Mutabel (Status, auditiert):** `span_state`, `speaker`/`mandate` (Korrekturen),
`rebuttal`.

---

## 2. Enums

```sql
CREATE TYPE source_type   AS ENUM (
  'plenarprotokoll','drucksache','dip_vorgang','rede',
  'interview','podcast','social_post','video');

-- Rechtsgrundlage der Verarbeitung (Legal §3/§10)
CREATE TYPE rights_basis  AS ENUM (
  'amtliches_werk_p5',        -- § 5 UrhG, gemeinfrei (MVP)
  'oeffentlich_gemacht_art9e',-- Art. 9(2)(e) DSGVO, öffentlich gemacht
  'zitat_p51',                -- § 51 UrhG Zitatrecht
  'lizenz',                   -- ausdrücklich lizenziert
  'ungeklaert');              -- wird NIE ausgespielt

-- Verifikations-Zustand der Sprecher-/Inhaltszuordnung (Legal §10, Security §6)
CREATE TYPE verification  AS ENUM (
  'official',        -- amtliche Quelle, Zuordnung amtlich (Protokoll) — Top-Trust
  'machine',         -- automatisch zugeordnet, NICHT zitierfähig
  'human_verified',  -- von Mensch bestätigt → zitierfähig
  'disputed',        -- angezweifelt/bestritten
  'superseded');     -- durch korrigierten Span ersetzt

-- Ausspiel-/Zugriffsklasse (Legal §7/§10, Security §3.3)
CREATE TYPE visibility_class AS ENUM (
  'public',      -- frei ausspielbar
  'restricted',  -- nur intern/authentifiziert
  'sensitive');  -- rechtlich heikel (z.B. §130-Fälle), Sonderbehandlung

-- Vertrauensniveau des Ingest-Adapters (Security §6)
CREATE TYPE trust_level   AS ENUM (
  'verified_primary', -- amtliche Primärquelle (DIP, Landtag) → official möglich
  'secondary',        -- seriös, aber nicht amtlich → Human-Verify nötig
  'low');             -- unsicher → Human-Verify + sensitive-Default
```

---

## 3. Tabellen

### 3.1 `ingest_adapter` — die Erweiterbarkeits-Naht (immutabel je Version)
```sql
CREATE TABLE ingest_adapter (
  name         text        NOT NULL,          -- z.B. 'dip-api'
  version      text        NOT NULL,          -- z.B. '1.0.0'
  trust_level  trust_level NOT NULL,
  description  text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (name, version)
);
```

### 3.2 `source` — die archivierte Rohquelle (IMMUTABEL, append-only)
```sql
CREATE TABLE source (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_type    source_type NOT NULL,
  rights_basis   rights_basis NOT NULL,
  adapter_name   text NOT NULL,
  adapter_version text NOT NULL,
  origin_url     text NOT NULL,               -- public_evidence: Permalink Pflicht
  content_hash   char(64) NOT NULL UNIQUE,    -- SHA-256 über Rohbytes = Anker + Dedup
  byte_size      bigint NOT NULL,
  mime_type      text NOT NULL,
  retrieved_at   timestamptz NOT NULL,
  raw_bytes_ref  text NOT NULL,               -- WORM/Object-Lock-Pfad
  archive_wayback text,
  archive_today   text,
  warc_ref        text,
  normalized_text text,                       -- kanonischer Klartext für Offsets/FTS
  created_at     timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (adapter_name, adapter_version)
      REFERENCES ingest_adapter(name, version),
  -- public_evidence: mindestens EIN Fremdarchiv muss existieren (Legal §1/§10)
  CONSTRAINT chk_archive CHECK (
      archive_wayback IS NOT NULL OR archive_today IS NOT NULL),
  -- ungeklärte Rechtslage darf nicht mit Ausspielung koexistieren (App-Gate zusätzlich)
  CONSTRAINT chk_rights CHECK (rights_basis <> 'ungeklaert' OR true)
);
```

### 3.3 `speaker` — Mandatsträger (partei-agnostisch)
```sql
CREATE TABLE speaker (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  full_name    text NOT NULL,
  external_ids jsonb NOT NULL DEFAULT '{}',   -- {dip_id, wikidata_qid, ...}
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);
```

### 3.4 `mandate` — zeitlich begrenztes Mandat (löst Parteiwechsel & Amtsträger-Constraint)
```sql
CREATE TABLE mandate (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  speaker_id  uuid NOT NULL REFERENCES speaker(id),
  role        text NOT NULL,                  -- 'MdB','MdL Thüringen','Fraktionsvors.'
  parliament  text NOT NULL,                  -- 'bundestag','th-landtag',...
  party       text,                           -- Partei ZUR ZEIT des Mandats (frei!)
  active_from date NOT NULL,
  active_to   date                            -- NULL = laufend
);
CREATE INDEX idx_mandate_speaker ON mandate(speaker_id);
```
> **Warum `mandate` statt Felder am `speaker`:** Politiker wechseln Partei, verlieren/
> gewinnen Mandate. Ein Span wird gegen das Mandat geprüft, das `spoken_at` abdeckt →
> so ist „nur Amtsträger, in Funktion" (Legal §1) *zeitrichtig* erzwingbar.

### 3.5 `span` — die zitierfähige Einheit (INHALT IMMUTABEL)
```sql
CREATE TABLE span (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id     uuid NOT NULL REFERENCES source(id),       -- Beweiskette
  speaker_id    uuid NOT NULL REFERENCES speaker(id),
  mandate_id    uuid REFERENCES mandate(id),               -- Amtsträger-Bezug
  verbatim_text text NOT NULL,                             -- WÖRTLICH, ungeglättet
  text_start    int NOT NULL,                              -- Offset in source.normalized_text
  text_end      int NOT NULL,                              --   → Anti-Halluzination-Gate
  spoken_at     date NOT NULL,
  locator       jsonb NOT NULL DEFAULT '{}',               -- {drucksache, seite, ts_start..}
  permalink     text NOT NULL,                             -- tiefer Link zur Fundstelle
  span_hash     char(64) NOT NULL,                         -- SHA-256 über verbatim_text
  fts           tsvector,                                  -- deterministisch aus verbatim_text
  created_at    timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT chk_offsets CHECK (text_end > text_start)
);
CREATE INDEX idx_span_source   ON span(source_id);
CREATE INDEX idx_span_speaker  ON span(speaker_id);
CREATE INDEX idx_span_spokenat ON span(spoken_at);
CREATE INDEX idx_span_fts      ON span USING gin (fts);
```
> **Embedding bewusst NICHT im `span`:** Embeddings sind **abgeleitet, ersetzbar und
> provider-spezifisch** (dev bge-m3 lokal vs. live gehostet → andere Dimension). Sie
> gehören nicht in die immutable Beweistabelle. Sie liegen in `span_embedding` (§3.5b)
> — model-versioniert, neu berechenbar, ohne den Beweis anzufassen.

### 3.5b `span_embedding` — abgeleitete Vektoren, model-versioniert (Provider-swappable)
```sql
CREATE TABLE span_embedding (
  span_id    uuid NOT NULL REFERENCES span(id),
  model      text NOT NULL,             -- 'bge-m3@local' | 'voyage-3@hosted' | ...
  dim        int  NOT NULL,             -- 1024, 3072, ... (je Modell)
  embedding  vector NOT NULL,           -- pgvector, Dimension pro Modell
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (span_id, model)
);
-- HNSW-Index je konkretem Modell/Dim (ein Index pro aktiv genutztem Embedding-Space):
-- CREATE INDEX ON span_embedding USING hnsw (embedding vector_cosine_ops)
--   WHERE model = 'bge-m3@local';
```
> Dev und Live können **gleichzeitig** Embeddings halten (verschiedene `model`-Zeilen).
> Modellwechsel = neue Zeilen berechnen, alte droppen — **Span/Beweis unberührt**.
> Das ist die technische Umsetzung von „Inferenz ist ersetzbar, Beweis nicht".

### 3.6 `span_state` — mutabler Status (1:1, jede Änderung auditiert)
```sql
CREATE TABLE span_state (
  span_id      uuid PRIMARY KEY REFERENCES span(id),
  verification verification     NOT NULL,
  visibility   visibility_class NOT NULL,
  redacted     boolean          NOT NULL DEFAULT false,  -- gesperrt ≠ gelöscht
  redaction_reason text,
  updated_at   timestamptz      NOT NULL DEFAULT now()
);
```
> **Der Redaction-Trick (Legal §10, Security §6):** Ein Span wird nie gelöscht — nur
> `redacted=true` gesetzt. `span.verbatim_text`, `source.raw_bytes_ref`, Hash und WARC
> **bleiben als Beweis**. Nur die *Ausspielung* wird unterbunden. Append-only bleibt
> gewahrt, die Beweiskette unversehrt.

### 3.7 `topic_tag` & `span_topic` — Klassifikation als Metadaten (nie im Span-Text)
```sql
CREATE TABLE topic_tag (
  id     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  label  text NOT NULL,
  scheme text NOT NULL DEFAULT 'wortlaut-v1',   -- welche Taxonomie
  UNIQUE (scheme, label)
);
CREATE TABLE span_topic (
  span_id       uuid NOT NULL REFERENCES span(id),
  topic_tag_id  uuid NOT NULL REFERENCES topic_tag(id),
  confidence    real NOT NULL,
  classified_by text NOT NULL,                  -- 'model@version' — versioniert
  classified_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (span_id, topic_tag_id, classified_by)
);
```

### 3.8 `rebuttal` — Betroffenen-Replik (Legal §6)
```sql
CREATE TABLE rebuttal (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  span_id    uuid NOT NULL REFERENCES span(id),
  url        text,
  note       text,
  created_at timestamptz NOT NULL DEFAULT now()
);
```

### 3.9 `audit_log` — append-only, tamper-evident (Legal §10, Security §6)
```sql
CREATE TABLE audit_log (
  id          bigserial PRIMARY KEY,
  entity_type text NOT NULL,                    -- 'span_state','speaker',...
  entity_id   uuid NOT NULL,
  action      text NOT NULL,                    -- 'redact','verify','dispute',...
  old_value   jsonb,
  new_value   jsonb,
  reason      text,
  actor       text NOT NULL,                    -- wer (Mensch/Prozess)
  created_at  timestamptz NOT NULL DEFAULT now(),
  prev_hash   char(64),                         -- Hash-Kette über Log-Einträge
  entry_hash  char(64) NOT NULL                 -- SHA-256(row + prev_hash)
);
```

### 3.10 `ledger_anchor` — externe Verankerung (Security §3.6, P2)
```sql
CREATE TABLE ledger_anchor (
  id           bigserial PRIMARY KEY,
  merkle_root  char(64) NOT NULL,               -- über alle source.content_hash
  span_count   bigint NOT NULL,
  anchored_at  timestamptz NOT NULL DEFAULT now(),
  external_ref text                             -- git-Tag / Timestamping / veröffentlicht
);
```

---

## 4. Immutability erzwingen (nicht nur versprechen)

Append-only auf DB-Ebene, damit selbst ein kompromittierter App-Layer die Beweis-
Inhalte nicht still ändern kann (Security §3.6):

```sql
CREATE OR REPLACE FUNCTION forbid_mutation() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'append-only: % auf % verboten', TG_OP, TG_TABLE_NAME;
END; $$ LANGUAGE plpgsql;

-- source & span-Inhalt: kein UPDATE/DELETE
CREATE TRIGGER trg_source_immutable BEFORE UPDATE OR DELETE ON source
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
CREATE TRIGGER trg_span_immutable   BEFORE UPDATE OR DELETE ON span
  FOR EACH ROW EXECUTE FUNCTION forbid_mutation();
```
- Korrekturen an einem Span = **neuer** Span + alter Span-`verification='superseded'`
  (über `span_state`, das mutabel ist). Der falsche Stand bleibt sichtbar-nachvollziehbar.
- DB-Rolle der App hat **kein** `DELETE`/`TRUNCATE` auf Beweistabellen (zusätzlich zu Trigger).

---

## 5. Anti-Halluzination-Gate (die wichtigste Ausgabe-Kontrolle)

**Kein Span verlässt das System, der nicht buchstäblich in seiner verhashten Quelle
steht.** Vor jeder Ausgabe (Security §3.3 / §6):

```
1. lade source.raw_bytes_ref, prüfe SHA-256 == source.content_hash        (Quelle unverändert?)
2. text := source.normalized_text
3. assert text[span.text_start : span.text_end] == span.verbatim_text     (Span echt?)
4. assert span_hash == SHA-256(span.verbatim_text)                        (Span unverändert?)
5. assert NOT span_state.redacted AND visibility='public'
         AND source.rights_basis <> 'ungeklaert'                          (ausspielbar?)
→ nur wenn ALLES passt, wird der Span zurückgegeben.
```
Damit kann weder Prompt Injection noch ein DB-Eingriff je ein Zitat *erfinden* — jede
ausgespielte Zeichenkette ist gegen einen öffentlichen Hash beweisbar.

---

## 6. `verification`-State-Machine

```
                     ┌─────────────┐
  amtliche Quelle →  │  official   │  (Protokolle: sofort zitierfähig)
                     └──────┬──────┘
                            │ Korrektur nötig
                            ▼
   Ingest (nicht-amtl.) → ┌─────────┐  Human-Verify   ┌────────────────┐
                          │ machine │────────────────▶│ human_verified │  (zitierfähig)
                          └────┬────┘                 └───────┬────────┘
                               │ Zweifel                      │ Einspruch/Beleg
                               ▼                               ▼
                          ┌──────────┐    Korrektur      ┌────────────┐
                          │ disputed │──────────────────▶│ superseded │
                          └──────────┘   (neuer Span)     └────────────┘
```
- **Zitierfähig** (im öffentlichen Output) = nur `official` **oder** `human_verified`.
- `machine` und `disputed` erscheinen nie im zitierfähigen Ausgabepfad.
- Trust-Level des Adapters steuert den Startzustand: `verified_primary` → darf
  `official`; sonst Start bei `machine`.
- Jeder Übergang schreibt `audit_log`.

---

## 7. Ingest-Adapter-Interface (die Erweiterbarkeits-Naht)

**Ziel:** Mitstreiter fügen neue Quellen (Landtag X, Plattform Y) hinzu, **ohne** den
Kern anzufassen. Der Adapter kann *nur* entdecken/holen/parsen. Hashing, Archivierung,
WORM-Storage, Embedding, Indexierung macht **immer der Kern** — so kann kein Adapter
die Beweiskette umgehen.

```python
from typing import Protocol, Iterable
from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True)
class SourceRef:                 # Zeiger auf eine noch nicht geholte Quelle
    origin_url: str
    source_type: str
    hint: dict                   # adapter-spezifische Metadaten

@dataclass(frozen=True)
class RawSource:                 # das, was der Adapter geholt hat
    origin_url: str
    source_type: str
    raw_bytes: bytes             # → Kern hasht/archiviert/WORM-t DIESE Bytes
    mime_type: str
    retrieved_at: datetime

@dataclass(frozen=True)
class SpanDraft:                 # Parse-Ergebnis, noch OHNE Beweis-Anker
    verbatim_text: str
    text_start: int              # Offset in normalisiertem Quelltext
    text_end: int
    speaker_hint: dict           # {name, dip_id, ...} → Kern resolved speaker/mandate
    spoken_at: str
    locator: dict
    permalink: str

class IngestAdapter(Protocol):
    name: str
    version: str
    trust_level: str             # 'verified_primary' | 'secondary' | 'low'

    def discover(self, since: datetime) -> Iterable[SourceRef]: ...
    def fetch(self, ref: SourceRef) -> RawSource: ...
    def normalize(self, raw: RawSource) -> str: ...      # kanonischer Klartext
    def parse(self, raw: RawSource, normalized: str) -> Iterable[SpanDraft]: ...
```

**Pipeline (Kern, NICHT im Adapter):**
```
for ref in adapter.discover(since):
    raw   = adapter.fetch(ref)
    hash_ = sha256(raw.raw_bytes)
    if source_exists(hash_): continue          # Dedup über content_hash
    archive_wayback, archive_today = fremd_archivieren(raw.origin_url)   # VOR Verarbeitung
    worm_ref = worm_store(raw.raw_bytes)
    norm     = adapter.normalize(raw)
    src      = insert_source(hash_, worm_ref, archives, norm, adapter, ...)
    for d in adapter.parse(raw, norm):
        assert norm[d.text_start:d.text_end] == d.verbatim_text   # Adapter-Selbstcheck
        speaker, mandate = resolve_speaker(d.speaker_hint, d.spoken_at)  # Amtsträger-Check
        span = insert_span(src, speaker, mandate, d, span_hash=sha256(d.verbatim_text))
        init_span_state(span, adapter.trust_level)     # official vs machine
        enqueue_embedding(span)                        # async, GPU
```

> **Das ist die Naht:** Ein neuer Landtag = eine neue `IngestAdapter`-Implementierung
> (~1 Datei). Kern, Beweiskette und Sicherheits-Gates bleiben unberührt.

---

## 7b. Inference-Provider-Interface (dev lokal ↔ live gehostet, swappable)

Analog zum Ingest-Adapter: die **Inferenz** (Embedding, Rerank, Query-LLM) ist eine
**austauschbare Provider-Schicht**, kein fest verdrahteter Dienst. So läuft dev auf
**lokal** (bge-m3/Infinity, vLLM — billig iterieren, Korpus beliebig oft neu embedden)
und live gegen eine **gehostete API** — dieselbe Codebasis, nur andere Config.

```python
class InferenceProvider(Protocol):
    embed_model: str                 # z.B. 'bge-m3@local' | 'voyage-3@hosted'
    embed_dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def rerank(self, query: str, spans: list[str]) -> list[float]: ...
    # LLM NUR zur Query-Verständnis/Umformulierung — NIE für Ausgabetext (Security §3.3)
    def understand_query(self, q: str) -> QueryPlan: ...
```

**Souveränitäts-Grenze (hart, im Provider erzwingen):**
- Zur gehosteten AI dürfen **nur** ausspielbare, öffentliche Span-/Query-Texte
  (`rights_basis` ∈ {`amtliches_werk_p5`,`oeffentlich_gemacht_art9e`}, `visibility='public'`,
  `redacted=false`). Das ist ohnehin öffentliches Material.
- **Nie** zu gehosteter AI: Rohbytes/WORM, `machine`/`disputed`/`sensitive`-Spans,
  unveröffentlichtes Pipeline-Material, Betreiber-/Contributor-Daten.
- **Beweis-Integrität ist provider-unabhängig:** Hash-Kette, WORM, `/verify` und das
  Anti-Halluzination-Gate laufen immer lokal/souverän — egal wo embedded wird.

> **Ein Gotcha, bewusst notiert:** dev (bge-m3, 1024) und live (gehostet, andere Dim)
> nutzen verschiedene Embedding-Spaces. Beim Cutover dev→live wird der Korpus **einmal
> komplett neu embedded** (`span_embedding`-Zeilen für das Live-Modell). Kosten/Zeit
> dafür einplanen — der Beweis (`span`) bleibt dabei unangetastet.

## 8. Retrieval-Datenfluss (Hybrid, read-only)

```
Query
 ├─ dense:  provider.embed(query) → HNSW k-NN auf span_embedding WHERE model=<aktiv>
 └─ sparse: to_tsquery → GIN auf span.fts (BM25-nah)
        │
        └─ Fusion (RRF) → Reranker (bge-reranker) → Top-N
                │
   FILTER (hart, nicht optional):
     span_state.verification IN ('official','human_verified')
     AND span_state.redacted = false
     AND span_state.visibility = 'public'
     AND source.rights_basis <> 'ungeklaert'
                │
   Anti-Halluzination-Gate (§5) je Treffer
                │
   Ausgabe: [{verbatim_text, speaker, mandate.party, spoken_at,
              permalink, archive_url, content_hash, span_hash, verification}]
```
Kein LLM formuliert Ausgabetext. Das LLM darf höchstens die *Query* verstehen/
umformulieren — sein Output erreicht nie ungefiltert den Nutzer (Security §3.3).

---

## 9. Öffentliche Read-Replica (Datenklassen-Trennung, Security §3/§6)

Die öffentliche Replica bekommt **nur**:
- `span` mit `verification IN (official, human_verified)`, `redacted=false`,
  `visibility='public'`, `rights_basis <> 'ungeklaert'`
- zugehörige `source`-**Metadaten** (Hash, Archivlinks, Permalink) — **nicht** die
  `raw_bytes_ref`/WORM-Pfade, **nicht** Pipeline-Interna, **nicht** `machine`/
  `disputed`-Spans.
Roh-/Identitäts-/Verarbeitungsdaten bleiben im souveränen Kern.

---

## 10. Offene Design-Entscheidungen

1. **BM25:** Postgres-FTS zum Start (ein Store) vs. OpenSearch (echtes BM25) — Start PG-FTS.
2. **Embedding-Dim/Modell:** bge-m3 (1024) fix, oder Multi-Vektor (ColBERT-Stil) später?
3. **`normalized_text`:** in `source` speichern (einfach) vs. rekonstruierbar aus WARC
   (spart Platz, kostet Rechenzeit beim Verify) — Start: speichern.
4. **Speaker-Resolution:** Fuzzy-Matching-Schwelle; ab wann `machine` vs. Auto-`official`?
5. **Merkle-Anchor-Intervall & externer Anker** (git-Tag? RFC-3161-TSA? öffentliche Kette?).
6. **Locator-Schema je `source_type`** — als JSON-Schema pro Typ formalisieren.

---

## TL;DR

- **Ein Schema, das Prinzipien erzwingt statt erlaubt:** Provenienz (FK + CHECK),
  Wortlaut (immutable + Anti-Halluzination-Gate), Neutralität (`party` frei),
  Amtsträger/öffentlich (`mandate` + `public_evidence`), Rechtsgrundlage
  (`rights_basis`).
- **Beweis immutabel, Status mutabel:** `source`/`span` append-only (Trigger),
  Redaction/Verify über `span_state` + `audit_log` — Löschung sperrt, zerstört nie.
- **Anti-Halluzination-Gate (§5):** jede ausgespielte Zeichenkette ist gegen einen
  öffentlichen Hash beweisbar → Prompt Injection kann kein Zitat erfinden.
- **Ingest-Adapter-Interface (§7) = die Erweiterbarkeits-Naht:** Adapter holt/parst,
  Kern hasht/archiviert/indexiert. Neue Quelle = eine Datei, Beweiskette unberührt.
- **Nächster Schritt:** Phase-0-Increment-Spec — `ingest_adapter`+`source`-Schema +
  Hash+Archiv+WORM-Pipeline (ohne AI) — für den Coder.
