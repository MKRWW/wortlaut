# ADR-0005: MinIO (S3 Object-Lock) als WORM-Speicher

- **Status:** Superseded by [ADR-0007](0007-worm-lock-mode.md) (2026-07-20)
- Die Grundwahl **MinIO/S3 Object-Lock** bleibt gültig; **abgelöst wird nur der
  Lock-Modus** (Compliance → Governance + unbegrenzter Legal-Hold), siehe ADR-0007.

## Kontext
Die Rohbytes jeder Quelle müssen **unveränderlich** abgelegt werden (Beweis-Integrität,
R-DATA-01, Security §3.6). „Unveränderlich" muss auf Speicher-Ebene erzwungen sein,
nicht nur per Konvention. Der Kern läuft souverän/lokal.

## Entscheidung
**MinIO** (S3-kompatibel), selbstgehostet, mit **Object-Lock (WORM, Compliance-Mode)**.
Zugriff über einen schmalen Storage-Adapter mit **nur** `put`/`get` — **kein**
Delete-/Overwrite-Pfad im Code.

## Konsequenzen
- (+) Echtes WORM: gesperrte Objekte lassen sich nicht überschreiben/löschen.
- (+) S3-kompatibel → später auf anderen S3-Store portierbar.
- (+) Souverän/lokal, kein US-Cloud-Abschaltpunkt für den Beweisspeicher.
- (−) Betrieb/Backup von MinIO liegt bei uns.
- Der Adapter wird gegen ein **echtes MinIO im Testcontainer** getestet (ADR-0006):
  put→get roundtrip **und** dass Overwrite/Delete scheitert.

## Alternativen
- **Filesystem append-only:** schwächere Garantien, kein Object-Lock → verworfen.
- **US-Cloud-S3:** widerspricht der Souveränitäts-Grenze für den Korpus → verworfen.
