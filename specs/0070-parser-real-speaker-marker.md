# Increment-Spec 0070: Sprecher-Marker auf reales BT-Format

- **Issue:** #70 · **Status:** Reviewed · **Phase/Layer:** phase/1 · `wortlaut.ingest` · Public/AGPL
- **Baut auf:** #41 (Parsing), #51 (Kontext/Zwischenrufe). **Coder:** Architekt (Regex + PDF-Fixtures +
  Bestandsdatei-Edits = hermes-Schwächen; #41-Präzedenz „Fixtures baut der Architekt").
- **Reproduziert an echten Daten:** BT WP21/90 (`btp/21/21090.pdf`) → `spans_total=0`.

## 1. Ziel
Der Sprecher-Marker erkennt Redebeiträge im **echten** Stenografischen Bericht. Belegt (21/90,
716.983 Zeichen): reales Format ist `^<Name> (<Fraktion>):` (**181** Treffer), das bisher verlangte
`^Abg. <Name> (<Fraktion>):` = **0** Treffer. `Abg.` steht nur in Zwischenrufen. Präsidiums-Zweig
`(?:Vize)?Präsident(in)?…:` ist bereits korrekt (163 reale Marker) und bleibt.

## 2. Files (ändern)
- `src/wortlaut/ingest/protokoll_parse.py` — `SPEAKER_MARKER`: `Abg\.\s+`-Präfix aus dem Sprecher-Zweig
  entfernen. Präsidiums-Zweig unverändert. Docstring/Kommentar anpassen (Zwischenrufe erklären).
- `tests/fixtures/dip/_make_zweispaltiges_protokoll.py` + `_make_kontext_protokoll.py` — `Abg. `-Präfix
  aus den Sprecher-Markern entfernen (reales Format); Präsidiums-/Zwischenruf-Zeilen bleiben.
- `tests/fixtures/dip/plenarprotokoll_zweispaltig.pdf` + `plenarprotokoll_kontext.pdf` — aus den
  Generatoren **neu bauen** (deterministisch).
- `tests/unit/test_dip_parsing.py` + `test_dip_context.py` — erwartete Namen ohne `Abg.`-Präfix.

## 3. Neuer Regex (genau so)
```python
# '<Name> (<Fraktion>):' ODER Praesidiums-Marker '(Vize)Praesident(in) <Name>:'.
# ^ + MULTILINE: Marker beginnen eine Zeile. Zwischenrufe '(Zuruf des Abg. X [AfD]: ...)' starten
# mit '(' und werden vom Namens-Zeichensatz [^(\n] am Zeilenanfang NIE als Sprecher gelesen (AC2).
SPEAKER_MARKER = re.compile(
    r"^(?:(?P<name>[^(\n]+?)\s+\((?P<party>[^)\n]+)\):"
    r"|(?P<pres>Vizepräsident(?:in)?|Präsident(?:in)?)\b[^:\n]*:)",
    re.MULTILINE,
)
```

## 4. Testbare Akzeptanzkriterien
- **AC1 — reales Format matcht.** Given ein Text mit `Steffen Bilger (CDU/CSU):\n…`, When `segment_speeches`,
  Then ein Segment mit `name=="Steffen Bilger"`, `party=="CDU/CSU"`.
- **AC2 — Zwischenruf ist KEIN Sprecher.** Given eine Zeile `(Zuruf des Abg. Erika Musterfrau [SPD]: Das ist falsch!)`
  innerhalb eines Beitrags, Then entsteht **kein** neuer Span; der Zuruf bleibt Teil des `verbatim_text`.
- **AC3 — `Abg.` wird NICHT ins Präfix gezogen.** Kein Segment-`name` beginnt mit `Abg.`. (Regression gegen den Bug.)
- **AC4 — Präsidium bleibt Grenze, kein Span.** Given `Präsidentin Julia Klöckner:` zwischen zwei Beiträgen,
  Then kein Präsidiums-Span; der vorherige Beitrag endet **vor** dem Präsidiums-Marker (kein Bluten).
- **AC5 — Offset-Invariante.** Für jedes Segment gilt `normalized[text_start:text_end] == verbatim_text`.
- **AC6 — Mehrere Fraktionen.** `AfD`, `SPD`, `CDU/CSU`, `Die Linke`, `BÜNDNIS 90/DIE GRÜNEN` werden als
  `party` korrekt erkannt (fraktions-/parteiagnostisch, keine hartkodierte Liste).
- **AC7 — Fixtures realistisch.** Beide PDF-Fixtures enthalten Sprecher-Marker **ohne** `Abg.`-Präfix; die
  Parsing-/Kontext-Tests (#41/#51) laufen gegen dieses reale Format grün.
- **AC8 — Gates.** ruff + mypy(strict) + pytest (unit) grün; CI komplett grün + 0 neue Sonar-Issues.

## 5. Bekannte, bewusste Grenze (kein Blocker, Follow-up)
Regierungsbank/Minister (`<Name>, Bundesminister…:`) und fraktionslose ohne `(Fraktion)` werden im MVP
**nicht** erfasst (kein `(Fraktion):`-Muster). Als eigenes Increment nachziehen.

## 6. Do-NOT
- Keine hartkodierte Partei-Liste (parteiagnostisch, R-CORE). Keine anderen Module. Präsidiums-Zweig nicht ändern.
- Offset-Invariante nie brechen (`verbatim` IST der Slice). Regex nach Änderung auf doppelte Backslashes prüfen.

## 7. Nachlauf
Nach Merge: echten Lauf gegen ein **anderes** reales Protokoll (Dedup blockt 21/90) → `spans_total>0` erwarten.
