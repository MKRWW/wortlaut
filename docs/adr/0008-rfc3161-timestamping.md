# ADR-0008: RFC-3161-Zeitstempel — TSA-Wahl, Trust-Anker und Client-Bibliothek

- **Status:** Accepted (2026-08-17)
- **Kontext-Increment:** [#76](../../specs/0076-rfc3161-timestamp.md)
- **Berührt:** R-CORE-02 (nicht geändert), R-DATA-01/02, R-SEC-01/05, R-QUAL-02

## Kontext

Der Fremdarchiv-Ausfall (#73) hat gezeigt, dass die **Integritätshälfte** der Beweiskette am
selben Anbieter hing wie die Archivierung: fiel das Internet Archive aus, gab es für frisch
erfasste Quellen keinen anbieterunabhängigen Nachweis, dass genau diese Bytes zu einem Zeitpunkt
existierten. WORM + `content_hash` beweisen das nur **gegen uns selbst**.

Der Stakeholder hat sich für **Timestamp-first** entschieden: Anbieter-Unabhängigkeit über
Eigenschaft **B** (Existenz/Integrität zu T), nicht über Entkopplung von Archiv und Ingest (die
hätte R-CORE-02 gebrochen und wurde verworfen, #74). Eigenschaft **A** (Fremdattestierung durch
einen unabhängigen Archivar) bleibt offen und ist als Spike geparkt (#78).

Damit sind drei Stack-Entscheidungen zu treffen, die den **Beweis-Kern** berühren und deshalb
ADR-pflichtig sind (R-PROC-04): welche TSA, woran wird Vertrauen verankert, mit welcher Bibliothek.

## Entscheidung

### 1. TSA: freeTSA primär, sigstore als Fallback

| | Primär | Fallback |
|---|---|---|
| Name (`tsa_name`) | `freetsa` | `sigstore` |
| Endpunkt | `https://freetsa.org/tsr` | `https://timestamp.sigstore.dev/api/v1/timestamp` |
| Kosten / Registrierung | keine | keine |
| Transport | `https` | `https` |
| Root-Zertifikat | öffentlich, gültig bis 2041-03-07 | öffentlich, gültig bis 2035-04-06 |

Beide wurden **vor** der Entscheidung real angefragt: HTTP 200, `application/timestamp-reply`,
`messageImprint` byte-identisch mit unserem SHA-256, Nonce korrekt zurückgespiegelt, Verifikation
gegen den jeweiligen Root **OK**, Kreuzprobe (Token gegen den fremden Root) **FAILED**.

### 2. Trust-Anker: gepinnter **Root UND Signatur-Leaf** im Repo, ENV wählt nur aus

Root- **und** Leaf-Zertifikat je TSA liegen als PEM **im Paket**
(`src/wortlaut/timestamp/trust/<name>-root.pem`, `<name>-leaf.pem`). `WORTLAUT_TSA_PROFILES` wählt
Namen und Reihenfolge aus einer Registry im Code aus; **eine URL oder ein Zertifikat lässt sich
über ENV nicht injizieren.**

**Reines Root-Pinning wäre hier wirkungslos gewesen** — beim Nachbauen des Rezepts am echten Paket
kam heraus: `Verifier._verify_tsr_with_chains` schüttet Token-Zertifikate, konfigurierte Roots und
Intermediates in **einen** Trust-Bag und übergibt ihn an `PKCS7_verify`. Ein Token, das seine
eigene Kette inklusive Root mitbringt (freeTSA tut genau das), ist damit selbsttragend und
verifiziert auch gegen einen **fremden** konfigurierten Root. Wer sich eine eigene CA + TSA-Leaf
signiert und beide ins Token legt, käme durch.

Deshalb wird zusätzlich `tsa_certificate(<leaf>)` gesetzt: die Bibliothek identifiziert den Leaf
über `SignerInfo(issuer, serial)` — signatur-gedeckt, seit dem CVE-Fix — und vergleicht ihn
byte-gleich mit dem gepinnten. Damit ist der **öffentliche Schlüssel** gebunden, nicht nur ein Name.
`common_name(...)` ist **nicht** die Lösung: der Parameter vergleicht trotz seines Namens gegen den
vollständigen RFC4514-Subject-DN — ein DN ist kein Geheimnis und damit fälschbar.

**Folgeregel:** Profile sind **append-only**. Rotiert eine TSA ihr Signatur-Zertifikat, entsteht ein
**neues Profil** (`freetsa-2027`); ein bestehendes wird nie umgebogen, sonst werden alle mit ihm
gestempelten Tokens unverifizierbar. `source_timestamp.tsa_name` pinnt, welches Profil prüft.

### 3. Bibliothek: `rfc3161-client >= 1.0.8` (Trail of Bits, Apache-2.0)

Protokoll-Primitive für Request-Bau und Token-Verifikation, ohne eigenes Netz-I/O (das machen wir
mit `httpx`, wie bei der Fremdarchivierung). Die Version ist **nach unten gepinnt**.

## Begründung

- **Kein Single Point of Failure.** Genau die Lehre aus #73: ein einziger Anbieter für den
  Beweis-Anker ist der Fehler, den wir gerade erst bezahlt haben. Zwei TSAs kosten nichts.
- **Lizenz sauber.** Die Timestamp-Dienste von DigiCert/Sectigo sind laut ihren Terms an
  **Code-Signing-Kunden** adressiert. Ein Beweis-Archiv darf sein Fundament nicht auf eine
  Nutzung stellen, die der Anbieter so nicht vorgesehen hat.
- **Der Trust-Anker ist der ganze Beweis.** Ein Token beweist genau so viel, wie der Anker wert
  ist, gegen den es geprüft wird. Wäre der Anker zur Laufzeit injizierbar, könnte, wer den
  Container startet, beliebige Tokens „gültig" machen — ein Downgrade-Pfad mitten im Beweis-Kern.
  Ein neuer TSA kostet dadurch einen Code-Change **mit Review**, und das ist hier die gewünschte
  Eigenschaft (dieselbe Logik wie bei den konstanten Archiv-Hosts in `archive/archiver.py`).
- **Bibliothek statt Eigenbau.** ASN.1/CMS von Hand zu parsen wäre in einem Beweissystem die
  schlechteste aller Optionen. `rfc3161-client` ist die Verifikations-Bibliothek des
  Sigstore-Ökosystems, hat einen Rust-ASN.1-Kern und keine Netz-Oberfläche.
- **Die Version ist nicht kosmetisch:** **CVE-2026-33753** (≤ 1.0.5, CVSS 6.2) erlaubte es, per
  untergeschobenem Zertifikat im PKCS#7-Bag die TSA-Identitätsprüfung auszuhebeln, während die
  Signatur weiter gegen eine echte TSA validierte. Das ist **exakt** die Angriffsklasse, gegen die
  wir hier pinnen. `>= 1.0.8` ist Pflicht, kein „nice to have".

## Konsequenzen

- (+) Anbieterunabhängiger, **öffentlich nachrechenbarer** Nachweis pro Quelle: Jeder kann
  `sha256(rohbytes)` bilden, mit dem `messageImprint` im Token vergleichen und die Signatur gegen
  den veröffentlichten Root prüfen — ohne uns zu vertrauen.
- (+) **Additiv, kein Gate.** R-CORE-02 bleibt wörtlich unangetastet; ein fehlender Zeitstempel
  degradiert nichts (kein `ok`-Verlust, keine Sichtbarkeitsänderung).
- (+) Der Trust-Anker ist **im Diff sichtbar** und damit reviewbar.
- (−) **Nicht eIDAS-qualifiziert.** Es gibt keine gesetzliche Beweiskraftvermutung nach eIDAS
  Art. 41. Für Eigenschaft B genügt das; ein qualifizierter Zeitstempel bräuchte einen
  QTSP-Vertrag (kostenpflichtig) und ist ein späterer, rein vertraglicher Upgrade-Pfad — kein Code.
- (−) **freeTSA ist ein Ein-Personen-Dienst ohne SLA.** Deshalb der Fallback; fallen beide aus,
  bleiben Quellen „pending" und werden per Re-Run nachgeholt.
- (−) **Der Leaf-Pin macht Rotation zum Ereignis.** Rotiert die TSA, schlägt das **Stempeln** fehl
  — laut und sofort (`untrusted`, Quelle bleibt pending, Fallback übernimmt), nie stumm. Behoben
  wird das durch ein neues Profil, nicht durch Umbiegen des alten. Zertifikate werden
  **hinzugefügt, nie ersetzt**.
- (−) Neue **Rust-/PyO3-Dependency** im prod-Image (Wheels für manylinux vorhanden); der
  Docker-CI-Job deckt den Bau ab.
- Getestet gegen **echte, eingecheckte TSA-Tokens** als Fixtures (`tests/fixtures/tsa/`) — kein
  Netz-Call in der CI (R-TEST-03), und trotzdem gegen reale Antworten statt gegen selbstgebaute
  Attrappen.

## Alternativen

- **DFN (`zeitstempel.dfn.de`)** — funktioniert (real geprüft), aber nur `http`, TSA-Feld im Token
  `unspecified`, und die DFN-PKI adressiert Mitgliedseinrichtungen. Nutzungsrecht für ein privates
  Projekt ungeklärt ⇒ verworfen.
- **DigiCert / Sectigo** — höchste Verfügbarkeit, aber Code-Signing-Terms (s.o.) ⇒ verworfen.
- **Qualifizierter QTSP** — stärkste Rechtswirkung, kostet Geld und Vertrag ⇒ vertagt, nicht
  ausgeschlossen (Upgrade-Pfad ohne Code-Änderung außer einem neuen Registry-Eintrag).
- **Nur eine TSA** — kleinster Increment, wiederholt aber genau den Fehler aus #73 ⇒ verworfen.
- **Eigene ASN.1-Verifikation** (nur `cryptography`) — keine fremde Dependency, aber
  handgeschriebene CMS-/TSTInfo-Parsing-Logik im Beweis-Kern ⇒ verworfen.
- **`rfc3161ng`** — reine Python-Implementierung, deutlich weniger gepflegt, keine strikte
  Chain-/EKU-Prüfung ⇒ verworfen.
