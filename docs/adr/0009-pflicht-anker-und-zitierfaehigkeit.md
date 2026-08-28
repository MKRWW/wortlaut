# ADR-0009: Pflicht-Anker — Erfassen, Verarbeiten und Zitieren werden getrennt

- **Status:** Proposed (2026-08-28) — Annahme durch den Stakeholder steht aus
- **Kontext-Issue:** [#113](https://github.com/MKRWW/wortlaut/issues/113) · Auslöser: [#114](https://github.com/MKRWW/wortlaut/issues/114)
- **Berührt:** R-CORE-02 (Wortlaut **unverändert**, Umsetzung ändert sich), R-DATA-01/02, R-PROC-04
- **Baut auf:** [ADR-0008](0008-rfc3161-timestamping.md) (Eigenschaft B), #73, #74 (verworfen), #76, #78 (geparkt)

## Kontext

### Was jeder Anker bezeugt — und was nicht

ADR-0008 hat die beiden Eigenschaften benannt, aber nur eine davon entschieden:

| | Eigenschaft **A** — Fremdattestierung | Eigenschaft **B** — Existenz/Integrität |
|---|---|---|
| Aussage | „Zum Zeitpunkt T lieferte **diese URL** diese Bytes." | „**Diese Bytes** existierten zum Zeitpunkt T." |
| Träger | Unabhängiger Archivar (Wayback) | RFC-3161-Zeitstempel (#76) |
| Prüfbar ohne uns | ja | ja |
| Bindet an die Quelle | **ja** | **nein** |
| Status | offen, hing bisher an genau einem Anbieter | entschieden (ADR-0008) |

Die letzte Zeile ist der Kern: **B ersetzt A nicht.** Ein Zeitstempel beweist, dass wir bestimmte
Bytes zu einer Zeit hatten — nicht, dass der Bundestag sie ausgeliefert hat. Wer A durch B ersetzt,
tauscht eine Fremdbezeugung gegen eine Selbstauskunft mit Signatur.

### Warum die Frage jetzt akut ist

Zum zweiten Mal steht die Erfassung wegen **eines** Anbieters still:

- **#73 (August 2026):** Fremdarchiv-Ausfall, 157 Quellen verloren. Antwort war #76 (Eigenschaft B).
- **#114 (2026-08-28, gemessen):** Der Internet Archive erhält für `bundestag.de` **404**, während
  unser Server für dieselben URLs **200** bekommt — auf `dserver` *und* auf `www.bundestag.de`.
  Dieselbe URL wurde am Vortag noch erfolgreich archiviert (`http_status: 200`).
  `robots.txt` schließt Crawler **nicht** aus. Ursache offen (Sperre · ratenbasiert · IA-seitig).

Der Unterschied zu #73: Damals war der **Archivdienst** aus. Jetzt ist der Dienst gesund und
erreicht nur **unsere Quelle** nicht. Gegen diesen Fall hilft weder ein Retry noch ein zweiter
Zeitstempel — und es ist der Normalfall, mit dem ein Archiv rechnen muss, das fremde Server
weder kontrolliert noch beeinflusst.

### Zwei strukturelle Befunde aus dem Code

**1. Die Datenbank und die Pipeline sind sich uneinig.** `migrations/0002` erzwingt

```sql
CONSTRAINT chk_archive CHECK (archive_wayback IS NOT NULL OR archive_today IS NOT NULL)
```

— also *irgendeinen* der beiden. `pipeline/ingest.py` verlangt darüber hinaus **Wayback
namentlich**. „Wayback ist Pflicht-Anker" ist damit heute eine Pipeline-Konvention, keine
Schema-Invariante, und die beiden Ebenen sagen Verschiedenes.

**2. Der eigentliche Grund für das Insert-Gate ist die Append-only-Regel, nicht die Beweislogik.**
`archive_wayback` ist eine **Spalte auf `source`**, und `source` ist per Trigger append-only
(R-DATA-01): Ein UPDATE ist verboten. Ein Anker, der nicht nachgetragen werden **kann**, muss
zwangsläufig vor dem Insert vorliegen. Das Gate ist also eine Folge der Ablageform.

#76 hat für dasselbe Problem bereits ein Hausmuster: Der Zeitstempel liegt in einer **eigenen
append-only Tabelle** `source_timestamp`; „pending" ist **abgeleitet** (keine Zeile), es gibt
kein Status-Flag und kein UPDATE. Genau dieses Muster fehlt der Archivierung.

### Warum #74 verworfen wurde — und was sich geändert hat

#74 (Entkopplung) wurde als superseded geschlossen, weil ein `pending`-Zustand R-CORE-02 gebrochen
hätte. Diese Begründung nahm an, „pending" heiße *erfasst und nutzbar, nur ohne Archiv*. Der
Regeltext sagt aber etwas Engeres:

> **R-CORE-02 — Provenienz zuerst.** Nichts wird **verarbeitet**, was nicht vorher über Rohbytes
> gehasht **und** fremdarchiviert wurde.

Die Regel bindet das **Verarbeiten**, nicht das **Festhalten**. Rohbytes zu holen, zu hashen, in
WORM zu legen und zu stempeln *ist* die Provenienz — es ist nicht ihre Verarbeitung.

## Entscheidung

### 1. Drei Stufen statt einer Schwelle

| Stufe | Was passiert | Bedingung |
|---|---|---|
| **Erfassen** | fetch → `content_hash` über Rohbytes → WORM-put → RFC-3161-Stempel → `source`-Zeile inkl. eingefrorenem `normalized_text` | keine Fremdattestierung nötig |
| **Verarbeiten** | Spans, Embeddings | **Eigenschaft A liegt vor** |
| **Zitieren** | Retrieval, `/v1/search`, `/v1/sources`, jede Ausgabe | **Eigenschaft A liegt vor** |

R-CORE-02 bleibt **wörtlich unverändert**. Die Trennlinie liegt bei den **Spans** — sie sind das,
was zitiert wird.

**Warum `normalize` auf der Erfassen-Seite steht** (und nicht, wie zunächst naheliegend, bei
„Verarbeiten"): `normalized_text` ist eine Spalte auf derselben append-only `source`-Zeile und
ließe sich später **nicht** nachtragen — es müsste sonst ebenfalls ausgelagert werden, ohne dass
das etwas gewönne. Der Text wird nach #42 (Option A) bewusst **im Moment der Erfassung
eingefroren**, damit Span-Offsets versions-robust in genau den gespeicherten Text zeigen; ihn
später zu bilden, würde diesen Zweck aufheben. Und er wird **nirgends ausgespielt**:
`SourceEvidence` liefert Hash, Archiv-URLs und Metadaten, nie den Text. Ohne Spans wird aus
`normalized_text` nichts Sichtbares und nichts Zitierbares.

### 2. Die Attestierung wandert in eine eigene append-only Tabelle

Neue Tabelle `source_archive` nach dem Muster von `source_timestamp` (#76): eine Zeile je
erfolgreicher Attestierung, mit Archivar-Name, Snapshot-URL und Zeitpunkt. **„Pending" ist
abgeleitet** — eine Quelle ohne Zeile ist nicht attestiert. Kein Status-Flag, kein UPDATE, keine
Mutation einer bestehenden Zeile.

Damit wird die Nach-Attestierung überhaupt erst möglich, ohne die Append-only-Garantie anzutasten.
Ein eigener, wiederholbarer **Archiv-Pass** holt offene Quellen nach — genau wie der Stempel-Pass.

### 3. A bleibt Pflicht und wird von *einem* Archivar aus einer expliziten Registry erfüllt

Eigenschaft A ist erfüllt, sobald **mindestens ein** Archivar aus einer im Code geführten Registry
attestiert hat. Heute steht dort nur **Wayback**; #78 (perma.cc) ist der vorgesehene zweite
Eintrag. Die Registry ist Code, nicht Konfiguration — dieselbe Begründung wie beim Trust-Anker in
ADR-0008: Ein neuer Archivar kostet einen Review.

**`archive.today` zählt nicht als A.** Der Dienst ist bot-feindlich, hat keine dokumentierte API
und keine institutionelle Trägerschaft; wortlaut behandelt ihn bereits heute als abschaltbaren
Zusatzdienst (`DisableAfterFailures`). Er bleibt eine Zugabe, kein Anker.

### 4. Ein selbst betriebener Spiegel oder Proxy erfüllt A niemals

Ausdrücklich festgehalten, weil der Vorschlag naheliegt, sobald ein Archivar die Quelle nicht
erreicht: Holt der Archivar die Bytes bei **uns**, bezeugt der Snapshot „wortlauts Server lieferte
diese Bytes" — nicht „bundestag.de lieferte sie". Die Aussage wird zirkulär: Wir wären die Quelle
dessen, was wir beweisen wollen.

Der Spiegel-Zweck ist ohnehin bereits erfüllt — der WORM-Store hält die Rohbytes content-adressiert
und hashgesichert. Was fehlt, ist eine **fremde** Bezeugung, und die kann ein Server, den wir
betreiben, prinzipiell nicht liefern.

### 5. Was diese ADR *nicht* entscheidet

Sie ändert keinen Code. Die Umsetzung ist ein eigenes Increment mit Spec (#113 AC3), inklusive
Migration und Umgang mit den bereits erfassten Quellen.

## Begründung

- **Ein Ausfall darf ein Dokument nicht kosten.** Heute geht eine Quelle, die im Moment des Laufs
  nicht archivierbar ist, vollständig verloren — inklusive der Gelegenheit, ihre Bytes *zu diesem
  Zeitpunkt* zu stempeln. Je früher der Zeitstempel, desto mehr ist er wert; ihn an der
  Erreichbarkeit eines dritten Dienstes aufzuhängen, verschenkt Beweiskraft ohne Gegenwert.
- **Die Beweisschwelle sinkt nicht, sie verschiebt sich an die richtige Stelle.** Nichts wird
  zitierbar, was nicht fremdbezeugt ist. Was sich ändert, ist allein, dass eine unbezeugte Quelle
  nicht mehr **weggeworfen**, sondern **zurückgestellt** wird.
- **Das Muster existiert schon.** `source_timestamp` löst dasselbe Problem seit #76 und hat sich
  bewährt. Eine zweite Lösung für dieselbe Frage wäre die Doppelung, die dieses Projekt an anderer
  Stelle bereits teuer bezahlt hat.
- **Ein Anbieter ist ein Single Point of Failure** — die Lehre aus #73, in ADR-0008 für die TSAs
  bereits gezogen (zwei statt einer). Für die Archivare steht sie noch aus.

## Konsequenzen

- (+) Ein Archiv-Ausfall kostet einen Wiederholungslauf, kein Dokument. Rohbytes, Hash und
  Zeitstempel sind zum frühestmöglichen Zeitpunkt gesichert.
- (+) R-CORE-02 bleibt im Wortlaut unangetastet und wird in der Umsetzung **strenger**: Nicht erst
  die Ausgabe, schon die Span-Bildung setzt A voraus. Auch `/v1/sources/{id}` liefert für eine
  unattestierte Quelle nichts — sonst wäre der Beleg-Endpunkt das Schlupfloch.
- (+) Die Uneinigkeit zwischen `chk_archive` und der Pipeline verschwindet: Es gibt genau eine
  Stelle, die A definiert.
- (+) Ein zweiter Archivar (#78) wird zu einer additiven Registry-Zeile statt zu einem Umbau.
- (−) **Eine beweisrelevante Schema-Invariante ändert sich.** `chk_archive` entfällt in seiner
  heutigen Form; die Garantie muss an der Zitierfähigkeits-Grenze **gleichwertig** wieder
  entstehen — sonst tauschen wir eine DB-Zusicherung gegen Anwendungslogik ein, und genau davor
  warnt ADR-0003. Die Ersatz-Zusicherung gehört in dieselbe Migration, nicht in ein Folge-Ticket.
- (−) **Ein Rückstand entsteht, der sichtbar bleiben muss.** Unattestierte Quellen sind zu zählen
  und auszuweisen. Ein stiller, wachsender Berg unbezeugter Quellen wäre schlimmer als der heutige
  laute Abbruch.
- (−) **Driftgefahr.** Wächst der Rückstand, wird jemand vorschlagen, die Zitier-Schwelle „für den
  Anfang" zu senken. Das ist der Punkt, an dem das Projekt seinen Wert verliert. Eine Änderung der
  Schwelle ist **nur per neuer ADR mit Stakeholder-Approval** zulässig.
- (−) Die bereits erfassten Quellen brauchen eine Migration ihrer Anker aus der Spalte in die neue
  Tabelle — verlustfrei, aber nicht trivial.
- (−) Mehr bewegliche Teile: ein zusätzlicher Pass, eine zusätzliche Tabelle, ein zusätzlicher
  Zustand im Betrieb.

## Alternativen

- **Status quo (Insert-Gate auf Wayback).** Einfach und heute in Betrieb — hält aber die gesamte
  Erfassung an der Erreichbarkeit *einer* fremden Infrastruktur fest und wirft bei jedem Ausfall
  Dokumente weg, statt sie zurückzustellen. Zweimal in sechs Wochen eingetreten ⇒ verworfen.
- **A ersatzlos streichen, B genügt.** Löst das Problem sofort und zerstört die Aussage: Der
  Zeitstempel bindet Bytes an eine Zeit, nicht an eine Quelle. Damit wäre jedes Zitat nur noch
  durch unsere eigene Behauptung mit der Quelle verbunden ⇒ verworfen.
- **Eigener Spiegel/Proxy, den der Archivar abholt.** Sieht aus wie ein Anker, ist keiner (§4)
  ⇒ verworfen.
- **`archive.today` als vollwertigen zweiten Anker zulassen.** Kostenlos zu haben, aber
  bot-feindlich, ohne API-Zusage und ohne Trägerschaft; wir behandeln ihn schon heute als
  abschaltbar ⇒ verworfen, bleibt Zugabe.
- **R-CORE-02 umformulieren.** Nicht nötig — die Regel ist richtig, ihre Umsetzung als
  Insert-Gate war die Verkürzung ⇒ verworfen.
- **Mutable Statusspalte auf `source`** (`archive_state`) statt eigener Tabelle. Bricht
  Append-only (R-DATA-01) und die Trigger würden es verbieten ⇒ verworfen.
