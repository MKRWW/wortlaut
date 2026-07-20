# ADR-0007: WORM-Lock-Modus — Governance + unbegrenzter Legal-Hold

- **Status:** Accepted (2026-07-20)
- **Supersedes:** [ADR-0005](0005-object-storage-worm.md) (nur den Lock-*Modus*; die
  Wahl von MinIO/S3-Object-Lock als WORM-Speicher bleibt bestehen).

## Kontext
ADR-0005 hat den **Compliance-Mode** gewählt (auch `root` kann bis zum
Retention-Ablauf nicht löschen). Beim Ausplanen von #5 ist eine Spannung sichtbar
geworden: eine **valide DSGVO-Löschanordnung** (Art. 17, Härtefall) muss technisch
*adressierbar* bleiben — Compliance macht das bis zum Retention-Ablauf **unmöglich**
(auch für den Betreiber). Gleichzeitig soll der **Normalbetrieb praktisch unbegrenzt
unveränderlich** sein (R-DATA-01, Security §3.6): kein App-Layer, kein kompromittierter
Ingest-Pfad darf Beweis-Rohbytes ändern oder löschen.

## Entscheidung (Stakeholder-Approval 2026-07-20)
**MinIO S3 Object-Lock im `GOVERNANCE`-Mode**, und **jedes Objekt zusätzlich mit
`Legal-Hold = ON`** (ohne Ablaufdatum) abgelegt.
- Der **Storage-Adapter hat nur `put`/`get`** (+ idempotentes `ensure_bucket`) — **kein**
  Delete-/Overwrite-/Legal-Hold-Release-Pfad im Anwendungscode.
- Löschung im **DSGVO-Härtefall** = **privilegierte Out-of-band-Operation** (S3-Rechte
  `s3:BypassGovernanceRetention` + `s3:PutObjectLegalHold`), dokumentiert und auditiert,
  **nie** über den App-Adapter, **nie** über die reguläre Ingest-Rolle.

## Konsequenzen
- (+) **Normalbetrieb effektiv unlöschbar:** Legal-Hold hat kein Ablaufdatum → keine
  „Retention läuft aus"-Lücke wie bei reiner Compliance-Retention.
- (+) **DSGVO-Notfall bleibt adressierbar** — kein technischer Selbst-Widerspruch zu
  Art. 17 (deckt sich mit Legal §4.3 „Sperrung/Redaction, ohne die Beweiskette zu
  zerstören": der Härtefall ist die eng bewachte Ausnahme, nicht der Regelweg).
- (+) Deckt sich mit „**Redaction sperrt, löscht nie**" (R-DATA-04): reguläre
  Löschbegehren führen zu `span_state.redacted`, **nicht** zu WORM-Löschung.
- (−) Governance ist gegen einen **kompromittierten Privileg-Account** schwächer als
  Compliance. **Mitigation:** Least-Privilege, getrennte Credentials für den
  Bypass-Pfad, MFA, Audit-Log jeder Legal-Hold-Änderung.
- (−) Der **Bucket muss mit aktiviertem Object-Lock erstellt** werden (nur bei
  Bucket-Creation setzbar) — `ensure_bucket` erzwingt das.
- Getestet gegen **echtes MinIO im Testcontainer** (ADR-0006): put→get roundtrip **und**
  Overwrite/Delete scheitert **und** Legal-Hold ist aktiv.

## Alternativen
- **Compliance-Mode (ADR-0005):** härteste Garantie, aber DSGVO-Härtefall technisch
  unmöglich bis Retention-Ablauf → **abgelöst**.
- **Filesystem append-only:** schwächere Garantien, kein Object-Lock → verworfen (ADR-0005).
