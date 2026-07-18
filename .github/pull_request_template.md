<!-- Definition of Done — alle Punkte müssen erfüllt sein, sonst kein Merge. -->

## Was & Warum
<!-- Kurzbeschreibung + verlinktes Issue (Closes #123) -->

Closes #

## Definition of Done
- [ ] Akzeptanzkriterien des Issues erfüllt (Tests belegen sie)
- [ ] Alle CI-Gates grün (Lint/Type/Test/Coverage/SAST/Komplexität/Architektur)
- [ ] Review durch Architekt + (wo relevant) Security
- [ ] Öffentliche Interfaces & Docs aktualisiert
- [ ] **Beweisketten-Invarianten gewahrt** (Immutability; Anti-Halluzination-Gate)
- [ ] Keine neuen Gott-Methoden/-Klassen (Komplexität/Größe im Limit)
- [ ] Kein Secret, kein Pickle-Load, **kein LLM-Freitext im Serving-Layer**

## Betroffene Interfaces / Layer
<!-- Welches Protocol / welcher Layer (ingest / evidence / store / retrieval / serving)? -->

## Recht / Security
<!-- rights_basis betroffen? Provenienz? Threat-Model-Bezug? Neue Angriffsfläche? -->
