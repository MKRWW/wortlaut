# wortlaut — Engineering-Methodik

> Wie wir bauen, damit es **nicht** wie ein weggeworfenes Wochenendprojekt endet.
> Kern: **Spec-driven + TDD + kleine, testbare Increments.** Kein Code ohne Spec,
> kein Code ohne vorher fehlschlagenden Test, kein Increment ohne messbare
> Akzeptanz. Der Stack ist entschieden ([docs/adr/](adr/)), die Regeln sind
> verbindlich ([rules.md](rules.md)).

## Warum so streng

„Erst mal drauflos" produziert Code, den niemand mehr anfassen will. Die Gegenmittel
sind nicht Meinung, sondern Verfahren: **Spec zuerst** (die Absicht ist explizit und
review-bar, bevor Code entsteht), **Test zuerst** (das Verhalten ist definiert und
beweisbar, bevor es implementiert wird), **klein** (jeder Schritt ist überschaubar
und reversibel).

---

## 1. Der Increment-Loop (verbindlich)

Jeder Increment durchläuft genau diese Stufen. Keine wird übersprungen.

```
1. STORY   (PO)         INVEST-Story mit testbaren Akzeptanzkriterien   → [DoR]
2. SPEC    (Architekt)  Increment-Spec: Design, Interfaces, Testplan     → Review
3. RED     (Coder/QA)   Tests schreiben, die die AC prüfen — sie SCHEITERN
4. GREEN   (Coder)      minimaler Code, bis die Tests grün sind
5. REFACTOR(Coder)      aufräumen bei grünen Tests (keine neue Funktion)
6. REVIEW  (Architekt + Security)  Diff gegen Spec + Regelwerk
7. GATE    (CI)         alle Hard-Gates grün                             → [DoD]
8. MERGE   → develop; Release develop→main
```

- **Spec vor Code:** Stufe 2 vor Stufe 3. Ändert sich die Absicht, ändert sich zuerst
  die Spec.
- **Test vor Code (TDD):** Stufe 3 vor Stufe 4. Es gibt **keinen** Produktivcode ohne
  einen zuvor fehlschlagenden Test (R-TEST-05).
- **Ein Increment = ein PR.** Wird ein Increment groß, wird es geteilt (INVEST „Small").

---

## 2. INVEST — Kriterien für eine gute Story

Eine Story ist erst „ready" (DoR), wenn sie **INVEST** erfüllt:

| Buchstabe | Bedeutung | Prüffrage |
|-----------|-----------|-----------|
| **I** | Independent | Kann sie ohne Warten auf andere Stories gebaut werden? |
| **N** | Negotiable | Beschreibt sie das *Was/Warum*, nicht starr das *Wie*? |
| **V** | Valuable | Liefert sie erkennbaren Wert (für Nutzer/Beweiskette/Betrieb)? |
| **E** | Estimable | Ist der Aufwand abschätzbar (genug verstanden)? |
| **S** | Small | Passt sie in **einen** Increment/PR? |
| **T** | Testable | Sind die Akzeptanzkriterien **automatisiert prüfbar**? |

Scheitert ein Buchstabe → Story schneiden/schärfen, nicht bauen.

---

## 3. Testbare Akzeptanzkriterien (die zentrale Disziplin)

Ein Akzeptanzkriterium ist nur dann eins, wenn ein **automatisierter Test** es mit
**Ja/Nein** beantworten kann. Format: **Given / When / Then** + eine **messbare
Aussage**.

**Gut (testbar, deterministisch):**
> *Given* eine `source` mit `content_hash=X`, *When* eine zweite Quelle mit demselben
> `content_hash=X` eingefügt wird, *Then* schlägt der Insert mit Unique-Verletzung
> fehl und die Zeilenanzahl in `source` bleibt bei 1.

**Schlecht (nicht testbar):**
> *Der Import funktioniert zuverlässig.* · *Die Suche ist schnell.* · *Der Code ist sauber.*

### Was eine Metrik „testbar" macht
- **Messbar:** eine Zahl/ein Zustand, kein Gefühl. („p95-Latenz < 200 ms", nicht „schnell").
- **Deterministisch:** gleicher Input → gleiches Ergebnis (keine Zeit/Zufall-Abhängigkeit ohne Kontrolle).
- **Automatisierbar:** ein Test kann es ohne Mensch prüfen.
- **Eindeutig gebunden:** an ein Verhalten, nicht an eine Implementierung.

### Beispiele für Metrik-Typen
- **Korrektheit:** exakte Werte/Zustände (Hash stimmt, Zeilenanzahl, Fehlertyp).
- **Invariante:** „Overwrite/Delete scheitert" (WORM), „UPDATE auf `span` wirft" (Trigger).
- **Performance (wo relevant):** Latenz-/Durchsatz-Budget als Assertion (später, mit Baseline).
- **Robustheit:** definiertes Verhalten bei Fehlerfällen (Archiv-Ausfall → kein `source`-Insert).

---

## 4. TDD-Regeln (red → green → refactor)

1. **Red:** Schreibe den kleinsten Test, der ein Akzeptanzkriterium prüft und
   **fehlschlägt** (aus dem richtigen Grund).
2. **Green:** Schreibe den **minimalen** Code, der ihn grün macht — nicht mehr.
3. **Refactor:** Räume bei grünen Tests auf (Benennung, Duplikation, Struktur).
   Keine neue Funktionalität in diesem Schritt.
4. **Ein Verhalten pro Test.** Testname beschreibt das Verhalten
   (`test_dedup_rejects_duplicate_content_hash`), nicht die Methode.
5. **Unit vs. Integration getrennt** (Marker): Unit = schnell/rein; Integration =
   Testcontainers (echte Postgres/MinIO). Invarianten (R-DATA) sind **immer**
   Integrationstests.
6. **Kein Merge mit übersprungenen/`xfail`-Tests** ohne Begründung im PR.

---

## 5. Spec-driven: die Spec ist der Vertrag

Vor Stufe 3 existiert eine **Increment-Spec** (Template:
[templates/increment-spec.md](templates/increment-spec.md)), die enthält:
Ziel & Nicht-Ziele, betroffene Interfaces/Layer, öffentliche Signaturen, **testbare
Akzeptanzkriterien**, Testplan (welche Unit-/Integrationstests, welche Invarianten),
Recht-/Security-Bezug, Risiken. Die Spec wird **reviewt, bevor** Tests/Code entstehen.
Der Diff wird später **gegen die Spec** geprüft.

---

## 6. Rollen im Loop

| Stufe | Wer |
|-------|-----|
| Story/INVEST/AC | PO (Claude) → Stakeholder gibt frei |
| Spec | Architekt (Claude) |
| Red/Green/Refactor | Coder (opencode) |
| Test-Design/Review | QA + Architekt |
| Security-Review | Security (bei R-SEC/R-DATA-Bezug) |
| Gate/Merge | Architekt + CI |

---

## 7. Metriken, die wir dauerhaft messen

- **Coverage ≥ 80 %** (steigend) — R-TEST-01.
- **Kognitive/zyklomatische Komplexität ≤ 15, Methode/Klasse/Args im Limit** — R-ARCH-04.
- **Duplication / Maintainability** — SonarCloud-Gate (R-QUAL-04).
- **Pro Increment:** die in der Spec definierten Akzeptanz-Metriken (grün = erfüllt).
- *(Später, mit Baseline):* Performance-Budgets, ggf. Mutation-Score.

---

## 8. Anti-Patterns — was wir bewusst NICHT tun (die Loganalyzer-Lektion)

- ❌ Code vor Spec. ❌ Code vor (fehlschlagendem) Test.
- ❌ Große „Big-Bang"-PRs. ❌ Stories ohne testbare AC.
- ❌ Invarianten nur mit Mocks „geprüft".
- ❌ Gott-Klassen/-Methoden „wir splitten später".
- ❌ „funktioniert bei mir" als Akzeptanz.
- ❌ Direkt auf `main`/`develop` (außer dokumentiertes Admin-Bootstrapping).
