
# wortlaut — Regelwerk

> **Verbindlich. Dies ist die Grundlage, um mitzumachen.** Jeder Beitrag (Mensch oder
> Agent) hält dieses Regelwerk ein. Reviews und PRs verweisen auf **Regel-IDs**
> (z. B. „verstößt gegen R-SEC-04"). Was hier als **[CI]** markiert ist, wird
> maschinell erzwungen und blockt den Merge — kein Ermessen.

**Enforcement-Legende:**
- **[CI]** — hartes CI-Gate, blockt Merge automatisch.
- **[REVIEW]** — durch Reviewer (Architekt/Security/UI) durchgesetzt.
- **[AUTO]** — durch Tooling geprüft (z. B. Sonar), Ergebnis im Review gewichtet.

Regeln ohne Marker sind ebenso verbindlich, aber (noch) nicht automatisiert prüfbar.

---

## R-CORE — Grundprinzipien (unverhandelbar)

- **R-CORE-01** — **Wortlaut oder nichts.** Ausgabe an Nutzer = wörtlicher Span aus
  der DB, **nie** modellgenerierter, gekürzter oder geglätteter Text. Kein
  `summarize`-Pfad. **[CI]** (`check_no_llm_output`) **[REVIEW]**
- **R-CORE-02** — **Provenienz zuerst.** Nichts wird verarbeitet, was nicht vorher
  über Rohbytes gehasht **und** fremdarchiviert wurde. **[REVIEW]** + Invarianten-Test
- **R-CORE-03** — **Parteineutralität.** Kein Feld, kein Ranking, kein Default
  bevorzugt oder benachteiligt eine Partei. `party` bleibt freies Feld. **[REVIEW]**
- **R-CORE-04** — **Nur Mandatsträger, nur öffentlich.** Keine Privatpersonen, keine
  nicht-öffentlichen Äußerungen. **[REVIEW]**

## R-ARCH — Architektur & Interfaces (keine Gott-Klassen)

- **R-ARCH-01** — **Interface-first.** Öffentliche Nähte sind `Protocol`/ABC
  (`IngestAdapter`, `InferenceProvider`, …). **[REVIEW]**
- **R-ARCH-02** — **Layering.** Abhängigkeiten zeigen nach innen; **Adapter dürfen
  nicht in den Kern greifen**, der Serving-Layer importiert keinen LLM-Freitext-Pfad.
  **[CI]** (import-linter)
- **R-ARCH-03** — **Single Responsibility.** Ein Grund zu existieren pro Modul/Klasse.
  **[REVIEW]**
- **R-ARCH-04** — **Keine Gott-Klassen/-Methoden.** Klasse ≤ ~200 LOC, Methode
  ≤ ~40 LOC, ≤ 5 Parameter, kognitive Komplexität ≤ 15. **[CI]** **[AUTO]**
- **R-ARCH-05** — **Keine zirkulären Imports.** **[CI]**

## R-QUAL — Code-Qualität

- **R-QUAL-01** — **Lint + Format** grün (`ruff`, `ruff format --check`). **[CI]**
- **R-QUAL-02** — **Typecheck** (`mypy` strict); kein `Any`-Leck an öffentlichen
  Grenzen. **[CI]**
- **R-QUAL-03** — Öffentliche Funktionen sind **typisiert + docstring**. **[REVIEW]**
- **R-QUAL-04** — **Sonar-Quality-Gate** (Duplication, Maintainability) grün. **[AUTO]**

## R-TEST — Tests

- **R-TEST-01** — **Coverage ≥ 80 %** (steigt mit Reife). **[CI]**
- **R-TEST-02** — **Invarianten-Tests Pflicht:** Immutability (kein UPDATE/DELETE auf
  `source`/`span`) und Anti-Halluzination-Gate (Byte-Match). **[CI]**
- **R-TEST-03** — **Keine Live-Netz-Calls in Unit-Tests** (Fixtures/Mocks). **[REVIEW]**
- **R-TEST-04** — **Jedes Akzeptanzkriterium hat einen Test.** **[REVIEW]**
- **R-TEST-05** — **TDD:** kein Produktivcode ohne vorher **fehlschlagenden** Test
  (red → green → refactor). **[REVIEW]**

## R-PROC — Vorgehen (Spec-driven)

- **R-PROC-01** — **Spec-first:** kein Increment ohne reviewte Increment-Spec
  (Template in increment-spec).
  Ändert sich die Absicht, ändert sich zuerst die Spec. **[REVIEW]**
- **R-PROC-02** — **INVEST:** Stories erfüllen INVEST, bevor sie „ready" sind (Teil DoR). **[REVIEW]**
- **R-PROC-03** — **Ein Increment = ein PR** (klein). **[REVIEW]**
- **R-PROC-04** — **Stack-Entscheidungen als ADR** festhalten (siehe `docs/adr/`);
  eine ADR wird abgelöst, nicht still editiert. **[REVIEW]**

> Methodik-Details (Loop, testbare AC, TDD-Regeln): siehe `docs/engineering.md`.

## R-SEC — Security

- **R-SEC-01** — **Keine Secrets im Repo** (gitleaks). **[CI]**
- **R-SEC-02** — **SAST + Dependency-Audit** grün (`semgrep`, `bandit`, `pip-audit`).
  Start advisory, wird zum Blocker. **[CI]**
- **R-SEC-03** — **Modelle nur als `safetensors`**, nie Pickle-Load. **[CI]** **[REVIEW]**
- **R-SEC-04** — **Kein LLM-Call im Serving-/Output-Layer.** **[CI]**
  (`check_no_llm_output`)
- **R-SEC-05** — **Externe Fetches nur über Egress-Allowlist + interne IP-Blocklist**
  (SSRF-Schutz). **[REVIEW]**
- **R-SEC-06** — **Untrusted-Parsing** (PDF/Audio/Docs) nur in **isolierter, netzloser
  Sandbox** mit CPU/RAM/Zeit-Limits. **[REVIEW]**
- **R-SEC-07** — **Ingest-Content ist Daten, nie Instruktion.** Quelltext strikt von
  System-Prompts trennen (Prompt-Injection-Abwehr). **[REVIEW]**

## R-DATA — Beweis & Datenintegrität

- **R-DATA-01** — **Immutability:** kein UPDATE/DELETE auf `source`/`span`-Inhalt
  (append-only). **[CI]** (Invarianten-Test + DB-Trigger)
- **R-DATA-02** — **Hash über Rohbytes**, nicht über geparsten Text. **[REVIEW]**
- **R-DATA-03** — **`rights_basis` ist Pflicht;** `ungeklaert` wird **nie**
  ausgespielt. **[REVIEW]**
- **R-DATA-04** — **Redaction sperrt, löscht nie** — Hash/WARC/Rohbyte bleiben.
  **[REVIEW]**
- **R-DATA-05** — **Nicht-amtliche Zuordnung nie zitierfähig ohne `human_verified`.**
  **[REVIEW]**
- **R-DATA-06** — **Anti-Halluzination-Gate:** kein Span-Output ohne Byte-Match gegen
  die verhashte Quelle. **[CI]**

---

## Definition of Ready (DoR) — Gate für „ready"

Ein Issue darf erst in Bearbeitung, wenn **alle** erfüllt:
- [ ] User Story vorhanden (Als … will ich … damit …).
- [ ] Akzeptanzkriterien testbar (Given/When/Then).
- [ ] Betroffene Interfaces/Module benannt.
- [ ] Rechts-/Security-Implikation notiert (R-DATA/R-SEC-Bezug).
- [ ] Testansatz skizziert.
- [ ] Auf ein Increment geschnitten.
- [ ] Keine offenen Blocker.

## Definition of Done (DoD) — Gate für Merge

Ein PR ist fertig, wenn **alle** erfüllt:
- [ ] Akzeptanzkriterien erfüllt (Tests belegen sie) — R-TEST-04.
- [ ] Alle CI-Gates grün (R-QUAL, R-TEST, R-SEC, R-ARCH).
- [ ] Review durch Architekt + Security (wo R-SEC/R-DATA berührt).
- [ ] Öffentliche Interfaces & Docs aktualisiert.
- [ ] Beweisketten-Invarianten gewahrt (R-DATA-01, R-DATA-06).
- [ ] Keine neuen Gott-Klassen/-Methoden (R-ARCH-04).
- [ ] Kein Secret / kein Pickle / kein LLM-Freitext im Serving (R-SEC-01/03/04).

---

## Review-Regeln

- **Kein Merge ohne Review** durch den Architekt-Reviewer.
- **Security-Review Pflicht**, sobald ein PR `type/security` trägt oder R-SEC-/
  R-DATA-Pfade berührt (Ingest, Evidence, Serving, Fetcher, Storage).
- **UI-Review Pflicht** bei UI-Änderungen (UI/UX-Rolle).
- Reviewer verweisen auf **Regel-IDs**; ein Verstoß gegen eine **[CI]**-Regel ist per
  Definition schon rot und wird nicht durchgewunken.

## Enforcement-Übersicht (Regel → Gate)

| Bereich | CI-Job | Regeln |
|---------|--------|--------|
| Lint/Type/Test/Coverage | `quality` | R-QUAL-01/02, R-TEST-01/02 |
| Security | `security` | R-SEC-01/02/03/04, R-CORE-01 |
| Architektur | `architecture` | R-ARCH-02/04/05 |
| Review/Manuell | — | R-CORE-02/03/04, R-ARCH-01/03, R-QUAL-03, R-SEC-05/06/07, R-DATA-* |

---

## Änderung des Regelwerks

Das Regelwerk selbst wird nur per **PR mit Stakeholder-Approval** geändert. Neue
Regeln bekommen eine neue ID (IDs werden nie wiederverwendet). Wird eine Regel von
**[REVIEW]** zu **[CI]** hochgestuft, wird das im Enforcement-Marker vermerkt.

> Wie wir arbeiten (Rollen, Loop) steht in
> [Entwicklungs-Loop](development.md). Die *Regeln* stehen hier.
