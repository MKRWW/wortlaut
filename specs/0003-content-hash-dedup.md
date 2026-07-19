# Increment-Spec: Content-Hash + Dedup (#3)

- **Story/Issue:** #3 · **Status:** Draft · **Phase/Layer:** phase/0 · `evidence` (+ `store`)
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)
- Baut auf **#2** (Schema mit `source.content_hash char(64) UNIQUE`).

## 1. Ziel
Der Beweisketten-**Anker**: eine deterministische, reine `content_hash(raw: bytes) -> str`
(SHA-256 über die **Rohbytes**, nicht über geparsten Text), plus ein DB-Dedup-Check
`source_exists(content_hash)` — damit jede Rohquelle eindeutig identifiziert und ein
zweites Verarbeiten derselben Bytes vermieden wird (README §2, architecture §2).

## 2. Nicht-Ziele (Scope-Grenze)
- **Keine** Ingest-Pipeline / kein `fetch`/`normalize`/`insert` (das ist #6/#7).
- **Kein** Fremdarchiv, **kein** WORM-Storage (#4/#5).
- **Kein** Span-/Text-Hash (`span_hash` kommt mit dem Span-Increment).
- **Kein** AI, kein Streaming großer Dateien (bewusst später, s. §8), keine API.
- `evidence` bleibt **rein** (importiert keinen wortlaut-Layer); der Dedup-Check ist DB-Sache → `store`.

## 3. Betroffene Interfaces / Öffentliche Signaturen
```python
# src/wortlaut/evidence/hashing.py   (REIN: keine wortlaut-Imports, keine I/O)
def content_hash(raw: bytes) -> str:
    """SHA-256 über die Rohbytes, als 64-stelliger lowercase-Hex-String (= source.content_hash)."""

# src/wortlaut/store/sources.py   (DB-Query gegen die source-Tabelle aus #2)
async def source_exists(session: AsyncSession, content_hash: str) -> bool:
    """True, wenn bereits eine source mit diesem content_hash existiert (Dedup-Vorabcheck)."""
```
- **Layering (R-ARCH-02):** `evidence` importiert nichts aus wortlaut (pure). `store.sources`
  nutzt das `Source`-Modell aus #2. Weder importiert `serving`.
- **Dedup-Garantie** ist und bleibt der **DB-UNIQUE-Constraint aus #2**; `source_exists` ist nur
  ein Vorab-Check (Optimierung/„continue" in der Pipeline), **kein** Ersatz für die harte Garantie.

## 4. Design (kurz)
- **SHA-256 über Rohbytes** via `hashlib.sha256(raw).hexdigest()` → 64 lowercase-Hex = exakt der
  `char(64)`-Anker in `source.content_hash`. Determinismus ist die Kern-Eigenschaft (gleiche Bytes
  ⇒ gleicher Hash, immer), damit der Hash gegen einen öffentlichen Wert nachrechenbar ist.
- **Rohbytes ≠ Text:** bewusst über die unveränderten Bytes, nicht über `normalized_text` — sonst
  wäre der Anker von Parsing/Encoding abhängig und nicht mehr gegen die Quelle prüfbar.
- **Race:** Zwei gleichzeitige Ingests derselben Bytes → `source_exists` kann bei beiden `False`
  liefern; der zweite Insert scheitert dann am **UNIQUE** (#2). Das ist korrekt so — die DB ist die
  Wahrheit, der Vorab-Check nur Effizienz. (Das saubere Abfangen im Insert-Pfad ist #7.)

## 5. Testbare Akzeptanzkriterien (Given/When/Then)
- [ ] **AC1** *Given* bekannte Rohbytes, *When* `content_hash`, *Then* == der bekannte SHA-256-Hex
      (fester Testvektor, z.B. `b""` → `e3b0c442...b855`). `[unit]`
- [ ] **AC2** *Given* dieselben Bytes zweimal, *Then* identischer Hash (Determinismus). `[unit]`
- [ ] **AC3** *Given* Rohbytes vs. deren geparster/normalisierter Text (andere Bytes), *Then*
      **verschiedene** Hashes (Anker über Rohbytes, nicht Text). `[unit]`
- [ ] **AC4** *Given* beliebige Bytes, *Then* Ausgabe ist **genau 64 Zeichen lowercase-Hex**
      (passt in `char(64)`). `[unit]`
- [ ] **AC5** *Given* frische DB, *When* `source_exists(unbekannter_hash)`, *Then* `False`. `[integration]`
- [ ] **AC6** *Given* eine `source` mit `content_hash = H` (eingefügt), *When* `source_exists(H)`,
      *Then* `True` (Duplikat erkannt). `[integration]`

## 6. Testplan (Test-zu-AC-Mapping)
- **Unit (rein, kein Container):** `tests/unit/test_hashing.py`
  - `test_known_vector` → AC1 · `test_deterministic` → AC2 · `test_rawbytes_not_text` → AC3
  - `test_hex64_lowercase` → AC4 (+ Rand: `b""`, große/binäre Bytes)
- **Integration (Testcontainers Postgres, Harness aus #16/#2):** `tests/integration/test_source_dedup.py`
  - `test_source_exists_false_for_unknown` → AC5
  - `test_source_exists_true_after_insert` → AC6 (Adapter+Source einfügen wie in #2, dann prüfen)

## 7. Recht / Security
- **Beweisketten-Anker (README §2, architecture §2):** deterministisch + testbar; jeder Hash
  gegen die verhashte Quelle nachrechenbar.
- **Dedup** strukturell durch DB-UNIQUE (#2); `source_exists` nur Vorabcheck.
- `evidence` bleibt rein/I/O-frei — keine Secrets, keine Netz-/DB-Kopplung im Hashing.

## 8. Risiken & offene Fragen
- **Streaming/Speicher:** `content_hash(raw: bytes)` lädt die ganze Quelle in den Speicher. Für den
  MVP (Parlaments-Drucksachen/Protokolle, klein) unkritisch; eine chunked/Streaming-Variante ist
  bewusst **verschoben** (→ Design-Entscheidung D2).
- **Scope `source_exists`:** gehört der Dedup-Vorabcheck schon in #3 (in `store`) oder ganz in die
  Pipeline #7? (→ Design-Entscheidung D1.)
- **`store/sources.py`-Ort:** einfache Modulfunktion vs. Repository-Klasse — Start: schlichte Funktion.

## 9. Definition of Done (Verweis)
[../docs/rules.md](../docs/rules.md) DoD: alle AC grün (Unit + Integration), alle Gates grün
(Lint·Type·Test·Coverage ≥80, Security, Architektur), Review gegen die AKs, keine Gott-Klassen,
kein Secret/Pickle/LLM-Freitext. PR referenziert **#3**.
