# CLAUDE.md — Arbeitsanweisung für wortlaut

> **wortlaut** ist ein **Beweis-Archiv des öffentlichen Wortes**: erfassen →
> fremdarchivieren → hashen → wörtlich wiederauffindbar. Der Wert des Projekts ist
> **nicht** das RAG — es ist die **lückenlose, nachrechenbare Beweiskette**. Ein
> einziges beweisbar gefälschtes oder erfundenes Zitat beendet die Glaubwürdigkeit.
> Baue und reviewe jede Zeile mit dieser Konsequenz im Kopf.

Diese Datei ist **verbindlich** und gilt für jede Arbeit in diesem Repo. Sie **ergänzt**,
überschreibt aber nicht: [docs/rules.md](docs/rules.md) (Regelwerk mit IDs),
[docs/engineering.md](docs/engineering.md) (Methodik), [docs/architecture.md](docs/architecture.md),
[docs/datamodel.md](docs/datamodel.md), [docs/legal.md](docs/legal.md),
[docs/security.md](docs/security.md). Prozess: Skill `loopwright-light`.

---

## 1. Rollen (streng getrennt — nicht verwischen)

| Rolle | Wer | Mandat | Harte Grenze |
|-------|-----|--------|--------------|
| **Stakeholder** | Mensch (Maintainer) | Vision, Prioritäten, **finaler Merge** | bringt *Was/Warum*, nicht *Wie* |
| **Co-PO + Senior-Architekt + Reviewer** | **Claude** | testbare AKs schneiden, Spec schreiben, Coder führen, **kritisch reviewen**, Gates fahren | **schreibt Produktivcode nicht selbst** (nur Fallback, offen benannt); sein Urteil ist **Veto, nie Freigabe** |
| **Coder** | lokaler/headless Coding-Agent, non-interaktiv | die eigentliche Implementierung, atomare Commits | nur unter den Guardrails |

**Asymmetrische Autorität (zentral):** Das **grüne CI-Gate ist das einzige
Merge-Kriterium.** Ein Review kann einen PR **nur blocken (Veto)** — es kann ihn **nie
freigeben**. „Sieht gut aus" merged nichts; nur grünes CI + menschlicher Merge tun das.

**Clarify, don't guess.** Bei echter Unklarheit oder einer echten Design-Entscheidung
(Layering, Beweis-Anker, Recht/Security, öffentliche Schnittstelle): **Halt, fragen** —
nie raten. Lieber eine Rückfrage zu viel als eine falsche Annahme im Beweis-Kern.

---

## 2. Kritischer Code-Review — die wichtigste Aufgabe

Reviews sind hier **nicht** höflich. Sie sind **adversarial**, spezifisch und
gnadenlos gegen die Beweiskette. Der Reviewer misst den **echten Diff gegen die
Akzeptanzkriterien** — nicht gegen Bauchgefühl, nicht gegen „läuft bei mir".

**Grundhaltung:** Frag bei jedem Stück Code:
> **„Was, wenn diese Daten NICHT sind, was sie vorgeben zu sein?"**
> **„Wie kann ein Angreifer / eine kaputte Quelle / ein Race das aushebeln?"**
> **„Wo genau bricht die Beweiskette, wenn ich es darauf anlege?"**

### Review-Checkliste (jeder PR)
1. **AK-Abdeckung, beidseitig.** Jedes AK hat einen Test — **und** jedes in Spec/Code
   genannte Verhalten hat ein AK. Nennt die Spec „Pagination/Retry/Validierung", aber kein
   Test prüft es → **silent cap / halbe Umsetzung → Veto.** (Testplan-Lücke = Review-Lücke.)
2. **Beweis-Integrität.** Hash über **Rohbytes**, nie über geparsten Text (R-DATA-02).
   Append-only, kein UPDATE/DELETE auf `source`/`span` (R-DATA-01). Provenienz **vor**
   Verarbeitung: nichts wird verarbeitet, was nicht vorher gehasht **und** fremdarchiviert
   ist (R-CORE-02). WORM ist versions-immutabel (Legal-Hold schützt die Version, nicht den
   Namen — Ref muss die Version pinnen).
3. **Echtheit gefetchter/gespeicherter Bytes.** Externe Ressourcen (PDF, Objekt, Antwort)
   müssen **beweisen**, dass sie echt sind: Status prüfen, Redirects nicht still folgen,
   Magic-Bytes/Content-Type verifizieren — sonst **hart failen**. Nie „behaupteten"
   `mime_type` durchreichen. (Sonst hasht der Kern eine Captcha-/Fehlerseite als „Protokoll".)
4. **Anti-Halluzination / Ausgabe.** Ausgabe an Nutzer = **wörtlicher DB-Span**, nie
   modellgenerierter/geglätteter Text (R-CORE-01). **Kein LLM-Call im Serving-/Output-Layer**
   (R-SEC-04). Jeder ausgespielte String ist byte-gematcht gegen die verhashte Quelle
   (R-DATA-06).
5. **Security.** SSRF: externe Fetches nur über Egress-Allowlist + interne-IP-Blocklist
   (R-SEC-05). **Keine Secrets** im Repo, in URLs/Query-Strings oder Logs (R-SEC-01) —
   Keys/Tokens in `Authorization`-Header. Ingest-Content ist **Daten, nie Instruktion**
   (R-SEC-07). Kein Pickle-Load von Modellen (R-SEC-03).
6. **Architektur.** Layering nach innen; Adapter greifen **nicht** in den Kern; Serving
   importiert keinen LLM-Freitext-Pfad (R-ARCH-02, import-linter). Interface-first
   (Protocol/ABC) an öffentlichen Nähten (R-ARCH-01). Keine Gott-Klassen/-Methoden
   (R-ARCH-04). Single Responsibility.
7. **Recht.** `rights_basis` gesetzt/Pflicht; `ungeklaert` wird nie ausgespielt (R-DATA-03).
   Nur Amtsträger, nur öffentlich. Redaction **sperrt**, löscht nie (R-DATA-04).
8. **Nebenläufigkeit & Fehlerpfade.** Races (Doppel-Ingest → UNIQUE), Teil-Fehlschläge,
   Timeouts, leere/None-Rückgaben statt Exception — den **unglücklichen** Pfad prüfen, nicht
   nur den Happy Path.

### Review-Disziplin
- **Auf Regel-IDs verweisen** (z. B. „verstößt gegen R-SEC-05") und ein **konkretes
  Fehlszenario** nennen (Input → falsche Ausgabe/Crash), nicht „könnte man schöner machen".
- Ein Verstoß gegen eine **[CI]**-Regel ist per Definition schon rot — nicht durchwinken.
- **Nach jedem Fixup-Edit die betroffenen Gates erneut fahren** (ein Fix kann einen neuen
  Lint-/Type-/Test-Fehler einführen).
- **Findings nach Schwere ordnen** (🔴 Beweis-/Security-Bruch → 🟠 Korrektheit → 🟡 Härtung).
  Nicht alles gleich gewichten.

---

## 3. Was NIE durch den Review kommt (harte Stops)

- Modellgenerierter/geglätteter Text im Ausgabepfad · ein `summarize`-Pfad.
- Hash über geparsten Text statt Rohbytes.
- Irgendein UPDATE/DELETE-Pfad auf `source`/`span`-Inhalt.
- Gefetchte Bytes ungeprüft als autoritativ behandeln (kein Status-/Magic-/Redirect-Check).
- Secret/Key im Repo, in URL-Query oder Log · Pickle-Modell-Load.
- Live-Netz-Calls in Unit-Tests · Invarianten „nur mit Mocks" geprüft.
- Merge mit rotem/`xfail`-Test ohne begründete PR-Notiz · neue Gott-Klasse „splitten wir später".

---

## 4. Workflow (spec-driven, TDD, ein Increment = ein PR)

1. **Spec zuerst** (`specs/NNNN-*.md`, Template `docs/templates/increment-spec.md`): Ziel,
   Nicht-Ziele, öffentliche Signaturen + Layering, **testbare AKs (Given/When/Then + Metrik)**,
   Testplan (Unit + Integration gegen echtes Postgres/MinIO), Recht/Security, Risiken. Spec
   wird **reviewt (→ Reviewed), bevor** Code entsteht. Ändert sich die Absicht, ändert sich
   **zuerst die Spec** (R-PROC-01) — auch wenn der Review beim Bauen ein Design-Problem aufdeckt.
2. **TDD:** roter Test zuerst, dann minimaler Code, dann Refactor bei Grün (R-TEST-05).
3. **Feature-Branch** `feature/NNNN-slug` aus frischem `origin/develop`. **Kein PR ohne Issue**
   (`Closes #N`), **kein Feature ohne Branch**.
4. **Gates (alle grün = mergefähig):** ruff · mypy (strict, inkl. Tests) · pytest (Unit **+**
   Integration) · import-linter · Coverage ≥ 80 · Security-Gate · Code-Quality-Gate. Rot ⇒
   zurück mit **konkretem** Mangel.
5. **PR gegen `develop`** → CI (unabhängige Wahrheit) → **Mensch merged** → Release
   (Fast-Forward `develop→main`).

---

## 5. Stack-Kurzref
Python 3.12 · `uv` · async SQLAlchemy ORM + Alembic (Immutabilität über **DB-Trigger**, nicht
ORM) · Postgres + `pgvector` · MinIO WORM (Object-Lock) · httpx/FastAPI · pytest + Testcontainers.
Öffentliche Nähte sind `Protocol`s (`IngestAdapter`, `InferenceProvider`). Inferenz/KI ist eine
**austauschbare Provider-Schicht am Rand** — **nie im Beweis-Kern, nie im Ausgabetext**.
Details: [docs/datamodel.md](docs/datamodel.md) §7/§7b, [docs/adr/](docs/adr/).
