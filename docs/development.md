
# wortlaut — Rollen, agentischer Loop & Guardrails

> Der Betrieb *um* den Code. Ziel: ein agentischer Entwicklungs-Loop mit **harten,
> maschinell erzwungenen Guardrails** — Reviews, Unit-/Security-Tests, Code-Qualität,
> klare Interfaces (keine Gott-Klassen). Menschliche Schwächen (UI, IT-Security)
> werden zu **fest verdrahteten Agent-Rollen**, nicht zu blinden Flecken.

**Grundsatz:** Qualität ist nicht Disziplin, sondern **Gate**. Was nicht grün ist,
merged nicht. Menschen und Agenten *können* Fehler machen — die CI-Gates lassen sie
nicht durch.

---

## 1. Rollen

### Mensch (Stakeholder)
- **Stakeholder** — besitzt das „Warum", nimmt ab/lehnt ab, priorisiert.
- **Visionär** — Produktvision, Richtung.
- **Coder** (optional) — implementiert selbst, wenn gewollt.

### Agenten
| Rolle | Runtime | Aufgabe | Gate-Verantwortung |
|-------|---------|---------|--------------------|
| **PO** | Claude | Vision → DoR-fertiges Backlog auf GitHub, Akzeptanzkriterien, Priorisierung | DoR |
| **Architekt + Reviewer** | Claude | Increment-Specs, Diff-Review, Gate fahren, mergen | DoD, Review |
| **Coder** | opencode (self-hosted GPU-Rig / OpenRouter) | implementiert Increments auf Branches, öffnet PRs | grüne CI vor PR-Ready |
| **Security** | Claude + SAST-Tooling | threat-model-bewusstes Review, treibt SAST/Dep-/Secret-Scan | Security-Gate |
| **QA/Test** | Claude | erzwingt Unit-/Integrationstests, Coverage, DoD | Test-Gate |
| **UI/UX** | Claude (frontend-design) | Frontend-Design, sobald UI drankommt | UI-Review |

> **Runtime-Prinzip:** Reasoning-lastige Rollen (PO, Review, Security, QA) laufen auf
> Claude. Die **Implementierung** (Tippen) delegiert an **opencode** (lokal/OpenRouter).
> Trennung: *Denken vs. Tippen* — deckt sich mit der `delegate`-Konvention (Claude =
> Architekt+Reviewer, lokaler Agent = Coder).

---

## 2. Der agentische Loop

```
   Vision (Stakeholder)
        │
        ▼
   PO-Agent groomt Backlog ──► [ DoR-Gate ] ──► Stakeholder gibt frei
        │
        ▼
   Architekt (Claude) schreibt Increment-Spec
        │
        ▼
   Coder (opencode) implementiert auf Feature-Branch
        │
        ▼
   Pull Request ──► [ CI HARD-GATES ] ──► Claude-Review + Security-Review
        │                                          │
        │                                    [ DoD-Gate ]
        ▼                                          │
   Merge ◄─────────────────────────────────────────┘
        │
        ▼
   PO aktualisiert Board (GitHub Projects)
```

Ein Increment ist **klein** (ein PR, ein Reviewgang). Kein Big-Bang.

---

## 3. Hard Guardrails (CI = nicht verhandelbar, blockt Merge)

Alles auf **GitHub Actions** (öffentliches Repo → kostenlos). Jeder PR gegen `main`
muss grün sein:

1. **Lint + Format** — `ruff` (+ `ruff format --check`).
2. **Typecheck** — `mypy` strict.
3. **Tests** — `pytest`, Unit + Integration, **Coverage-Schwelle** (Start 80 %).
4. **Security-Gate:**
   - **SAST:** `semgrep` (+ `bandit` für Python).
   - **Dependencies:** `pip-audit`.
   - **Secrets:** `gitleaks`.
   - **Modelle:** `safetensors`-Check (kein Pickle-Load).
5. **Code-Qualität (SonarQube, self-hosted):** Cognitive Complexity, Duplication,
   Maintainability-Rating als Quality-Gate.
6. **Keine Gott-Methoden/-Klassen** (harte Limits, via Sonar + ruff):
   - Methodenlänge, Klassengröße, Parameteranzahl, zyklomatische/kognitive Komplexität.
7. **Architektur-Fitness:** `import-linter` erzwingt Layering
   (Adapter → Kern verboten; Output-Layer darf keinen LLM-Freitext importieren).
8. **wortlaut-Spezialgate — „kein summarize":** ein AST/Statik-Check, dass der
   **Serving-/Output-Layer nie einen LLM-Completion-Call** enthält. Erzwingt das
   Kernprinzip *im CI*, nicht per Disziplin. (Siehe [Security](security.md) §3.3.)
9. **Invarianten-Tests:** Immutability (kein UPDATE/DELETE auf `source`/`span`),
   Anti-Halluzination-Gate (Byte-Match) als Pflicht-Testfälle.

---

## 4. Clean-Interface-Regeln (keine Gott-Klassen)

- **Interfaces zuerst.** Öffentliche Nähte sind `Protocol`/ABC (schon angelegt:
  `IngestAdapter`, `InferenceProvider` — siehe [Datenmodell](datamodel.md)).
- **Ein Grund zu existieren pro Klasse/Modul** (SRP). Kern-Layer:
  `ingest` · `evidence` (hash/archiv/worm) · `store` (db) · `retrieval` · `serving`.
- **Kernregeln (CI-erzwungen wo möglich):**
  - Klasse ≤ ~200 LOC, Methode ≤ ~40 LOC, ≤ 5 Parameter, kognitive Komplexität ≤ 15.
  - Keine zirkulären Imports; Abhängigkeiten zeigen nach innen (Adapter → Kern nie).
  - Öffentliche Funktionen typisiert + dokumentiert; keine `Any`-Lecks.

---

## 5. Definition of Ready (DoR) — Issue ist bereit, wenn …

- [ ] **User Story** vorhanden (Als … will ich … damit …).
- [ ] **Akzeptanzkriterien** testbar formuliert (Given/When/Then).
- [ ] **Betroffene Interfaces/Module** benannt (welches `Protocol`, welcher Layer).
- [ ] **Rechts-/Security-Implikation** notiert (Domäne verlangt es —
      `rights_basis`? Provenienz? Threat-Model-Bezug?).
- [ ] **Testansatz** skizziert (welche Unit-/Integrationstests).
- [ ] **Auf ein Increment geschnitten** (ein PR realistisch).
- [ ] **Keine offenen Blocker/Abhängigkeiten.**

## 6. Definition of Done (DoD) — PR ist fertig, wenn …

- [ ] Akzeptanzkriterien erfüllt (Tests belegen sie).
- [ ] **Alle CI-Gates grün** (Lint/Type/Test/Coverage/SAST/Sonar/Komplexität/Architektur).
- [ ] **Review** durch Architekt (Claude) + Security-Agent (wo relevant).
- [ ] Öffentliche **Interfaces & Docs aktualisiert**.
- [ ] **Beweisketten-Invarianten gewahrt** (Immutability, Anti-Halluzination-Gate).
- [ ] Keine neuen Gott-Methoden/-Klassen (Sonar grün).
- [ ] Kein Secret/kein Pickle/kein LLM-Freitext im Serving-Layer.

---

## 7. Planung auf GitHub

- **Milestones = Phasen 0–5** (siehe [Architektur](architecture.md) §7).
- **Labels:** `type/*`, `phase/*`, `role/*`, `prio/*`, `status/*`, plus
  `needs-legal-review`, `good-first-issue`.
- **Issue-Templates:** User-Story (mit DoR-Checkliste), Bug, + Security-Hinweis
  (Lücken **nicht** öffentlich → Security Advisory).
- **PR-Template:** DoD-Checkliste.
- **Board (GitHub Projects v2):** Backlog → Ready (DoR erfüllt) → In Progress →
  In Review → Done. *(Board-Anlage braucht `project`-Scope am gh-Token.)*

---

## 8. Weaknesses → verdrahtete Rollen

- **UI-Design (Schwäche):** eigener **UI/UX-Agent** (frontend-design-Skill) besitzt
  jede UI-Arbeit; kein UI-Merge ohne dessen Review. Menschliche Schwäche ist
  neutralisiert.
- **IT-Security (Schwäche):** eigener **Security-Agent** + automatisierte Gates
  (SAST/Dep/Secret/Threat-Model). Security ist kein Nachgedanke, sondern Merge-Blocker.

---

## TL;DR

- **Rollen:** Mensch = Stakeholder/Visionär/(Coder). Agenten = PO, Architekt+Reviewer
  (Claude), Coder (opencode), Security, QA, UI.
- **Loop:** Vision → PO/DoR → Spec (Claude) → Code (opencode) → PR → CI-Gates →
  Review → DoD → Merge.
- **Guardrails sind CI, nicht Disziplin:** Lint/Type/Test/Coverage/SAST/Sonar/
  Komplexität/Architektur + das „kein-summarize"-Spezialgate.
- **Klare Interfaces:** `Protocol`-Nähte, harte Größen-/Komplexitätslimits → keine
  Gott-Klassen.
- **UI & Security** (deine Schwächen) sind eigene Agent-Rollen mit Merge-Blocker-Macht.
- **Geplant wird öffentlich auf GitHub** (Milestones/Labels/Issues/Board).
