# Increment-Spec: <Titel> (#<Issue>)

> Kopiervorlage. Die Spec entsteht **vor** Tests/Code und wird **vor** dem Coden
> reviewt. Methodik: [../engineering.md](../engineering.md). Regeln: [../rules.md](../rules.md).

- **Story/Issue:** #<n>
- **Status:** Draft | Reviewed | Umgesetzt
- **Phase / Layer:** <phase/0…> · <ingest|evidence|store|retrieval|serving>

## 1. Ziel
<Ein Satz: welcher Wert, für wen.>

## 2. Nicht-Ziele (Scope-Grenze)
- <was dieser Increment ausdrücklich NICHT tut>

## 3. Betroffene Interfaces / Öffentliche Signaturen
```python
# Neue/geänderte öffentliche Schnittstellen (Protocol/Funktion/Klasse) mit Typen.
```
- Layering-Bezug (R-ARCH-02): <welche Abhängigkeitsrichtung>

## 4. Design (kurz)
<Die wichtigsten Entscheidungen für DIESEN Increment; Verweis auf relevante ADR.>

## 5. Testbare Akzeptanzkriterien (Given/When/Then + Metrik)
- [ ] **AC1** — Given …, When …, Then <messbare Aussage>.
- [ ] **AC2** — …
> Jedes AC muss von einem automatisierten Test mit Ja/Nein beantwortbar sein.

## 6. Testplan
- **Unit (schnell, rein):** <welche>
- **Integration (Testcontainers, echte Postgres/MinIO):** <welche>
- **Invarianten (Pflicht, R-DATA):** <welche Trigger/WORM-/Constraint-Tests>
- Test-zu-AC-Mapping: AC1→test_…, AC2→test_…

## 7. Recht / Security
- rights_basis / Provenienz / Immutability betroffen? <ja/nein + wie>
- Threat-Model-Bezug (R-SEC/R-DATA): <…>

## 8. Risiken & offene Fragen
- <Risiko / Unbekanntes, das den Increment gefährden könnte>

## 9. Definition of Done (Verweis)
Erfüllt [../rules.md](../rules.md) DoD: AC grün, alle Gates grün, Review, Invarianten
gewahrt, keine Gott-Klassen, kein Secret/Pickle/LLM-Freitext im Serving.
