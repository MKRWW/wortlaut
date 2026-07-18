
# wortlaut — Recht & Threat-Model

> [!warning] Kein Rechtsrat
> Dieses Dokument ist eine **strukturierte Kartierung der Rechtslage durch einen
> Nicht-Juristen**, um die Architektur rechtsbewusst zu bauen und einem echten
> Anwalt die Prüfung zu erleichtern. Es ersetzt **keine** anwaltliche Beratung.
> Vor Phase 3 (Audio) und **zwingend** vor Phase 4 (Social Media) muss ein
> Fachanwalt für Urheber-/Medien-/IT-Recht draufschauen. Die offenen Fragen sind
> in §11 gesammelt.

**Grundthese:** Öffentliche Äußerungen von Mandatsträgern in ihrer politischen
Funktion zu dokumentieren ist in Deutschland gut abgesichert — *wenn* drei Dinge
stimmen: (1) **Wortlauttreue**, (2) **Beschränkung auf den öffentlich-politischen
Bereich**, (3) **saubere Rechtsgrundlage pro Quellenkategorie**. Das Projekt ist so
zu bauen, dass diese drei Dinge im Datenmodell erzwungen sind, nicht nur gute
Absicht bleiben.

---

## 1. Zweck & Geltungsbereich (der rechtliche Rahmen des Projekts)

- **Zweck:** Transparenz über das öffentliche Reden und Handeln von Personen in
  politischen Mandaten/Ämtern. Dokumentation im **öffentlichen Interesse**, mit
  journalistisch-archivarischem Charakter.
- **Personenkreis (eng!):** ausschließlich **Mandats-/Funktionsträger** (MdB, MdL,
  Fraktions-/Parteifunktionäre) und nur **in ihrer öffentlichen/politischen Rolle**.
  Keine Privatpersonen, keine Familienangehörigen, kein privates Leben.
- **Äußerungsbegriff (eng!):** nur **öffentlich** getätigte Äußerungen (Parlament,
  öffentliche Reden, Interviews, öffentlich einsehbare Social-Media-Accounts).
  **Nie** heimlich/privat Gesprochenes.

Diese drei Einhegungen sind der wichtigste Rechtsschutz überhaupt — sie schneiden
die meisten Angriffsflächen (DSGVO, Persönlichkeitsrecht, StGB) an der Wurzel weg.

---

## 2. Quellenkategorien — je eigene Rechtslage

Zentrale Einsicht: **Es gibt nicht „die" Rechtslage, sondern eine pro Quellentyp.**
Das muss im Datenmodell als `rights_basis`-Feld pro `source` landen.

| Quellentyp | Urheberrecht | DSGVO-Lage | Risiko | MVP? |
|------------|--------------|-----------|--------|------|
| Plenarprotokolle (Bund/Länder) | **§ 5 UrhG amtliches Werk → gemeinfrei** | Redner amtlich, öffentlich | **niedrig** | ✅ |
| Drucksachen, Kleine/Große Anfragen | **§ 5 UrhG gemeinfrei** | dito | **niedrig** | ✅ |
| Öffentliche Reden (Parlament/Bühne) | amtlich bzw. öffentlich gehalten | öffentlich | niedrig–mittel | Ph. 3 |
| Interviews/Podcasts/Presse | **fremdes Urheberrecht** → nur § 51 Zitatrecht | öffentlich | **mittel–hoch** | Ph. 3 |
| Social-Media-Posts (Mandatsträger) | Urheberrecht des Verfassers + Plattform-TOS | Art. 9 heikel | **hoch** | Ph. 4 |
| Video/Bild von Auftritten | Filmrechte + KUG (Recht am Bild) | öffentlich | mittel–hoch | Ph. 4+ |

**Konsequenz fürs MVP:** Die zwei „niedrig"-Zeilen (amtliche Werke) sind genau der
MVP-Scope. Alles ab „mittel" wird bewusst später und mit Juristen-Review gebaut.

---

## 3. Urheberrecht (UrhG)

### 3.1 Amtliche Werke — § 5 UrhG (der Freibrief fürs MVP)
Plenarprotokolle, Drucksachen, parlamentarische Anfragen und Antworten sind
**amtliche Werke** und genießen **keinen Urheberrechtsschutz** (§ 5 Abs. 1/2 UrhG).
Sie dürfen frei gespeichert, vervielfältigt, durchsuchbar gemacht und im Wortlaut
wiedergegeben werden. **Das ist die rechtliche Basis des gesamten MVP.**

### 3.2 Zitatrecht — § 51 UrhG (für nicht-amtliche Quellen ab Phase 3)
Für Interviews, Podcasts, Presseartikel greift **nicht** § 5, sondern das
**Zitatrecht**: Zitieren ist zulässig, soweit es durch einen **besonderen Zweck**
(Beleg-/Auseinandersetzungsfunktion) gerechtfertigt ist und im Umfang geboten
bleibt.
> [!caution] Achtung — die härteste offene Frage
> Eine **reine Zitat-Datenbank** ohne eigene inhaltliche Auseinandersetzung könnte
> die Belegfunktion des § 51 überdehnen (das Zitat muss Beleg *innerhalb* eines
> eigenen Werks sein, nicht Selbstzweck). Gegenargument: Kontextdaten
> (Sprecher/Datum/Fundstelle/Verlinkung) + Dokumentationszweck können eine eigene
> geistige Leistung begründen. **Muss der Anwalt klären** (siehe §11). Für's MVP
> irrelevant, weil amtliche Werke ohnehin frei sind.

### 3.3 Berichterstattung über Tagesereignisse — § 50 UrhG
Kann für tagesaktuelle Auftritte zusätzlich tragen, ist aber zeitlich begrenzt und
kein Fundament für ein Dauerarchiv.

### 3.4 Unser *eigenes* Datenbankherstellerrecht — §§ 87a–87e UrhG
Der Korpus stellt eine **wesentliche Investition** dar → wir erwerben ein eigenes
**Datenbankherstellerrecht** (sui generis). Zweischneidig:
- **Pro:** schützt *unseren* Korpus gegen systematisches Absaugen durch Dritte.
- **Contra/Vorsicht:** Beim Ingest dürfen wir **fremde** geschützte Datenbanken
  (z. B. kommerzielle Presse-/Medienarchive) **nicht** systematisch auslesen. Nur
  Primärquellen (amtliche Portale, DIP-API) oder klar lizenzierte Quellen.

---

## 4. Datenschutz (DSGVO / BDSG)

Namen, Zitate, Zuordnung zu Personen = **personenbezogene Daten**. DSGVO greift.

### 4.1 Rechtsgrundlage der Verarbeitung
Mehrere tragfähige Säulen, kumulativ zu prüfen:
- **Art. 6 Abs. 1 lit. f — berechtigtes Interesse:** Transparenz über politisches
  Handeln von Amtsträgern; deren Interesse an Nichtdokumentation ist bei
  öffentlichem Wirken gering. Interessenabwägung fällt zugunsten der Dokumentation
  aus — **je öffentlicher/amtlicher die Äußerung, desto klarer.**
- **Art. 85 DSGVO — Medienprivileg:** Werden Daten zu **journalistisch-
  redaktionellen** Zwecken verarbeitet, sind weite Teile der DSGVO (Auskunft,
  Löschung) eingeschränkt. Ob wortlaut als „journalistisch" gilt, hängt vom
  **redaktionellen Charakter** ab (Auswahl, Einordnung, Veröffentlichungszweck).
  **Anzustreben und im Auftreten zu untermauern.**
- **Art. 89 DSGVO — Archiv im öffentlichen Interesse / Forschung:** eigene
  Privilegierung für Langzeitarchivierung, mit Garantien (Datenminimierung).

### 4.2 Besondere Kategorien — Art. 9 DSGVO (die politische Meinung!)
Politische Meinungen sind **besondere Kategorie** (Art. 9 Abs. 1) — Verarbeitung
grundsätzlich verboten, außer Ausnahme greift. Einschlägig:
- **Art. 9 Abs. 2 lit. e — vom Betroffenen offensichtlich öffentlich gemacht:** Ein
  Politiker, der öffentlich redet/postet, macht seine politische Meinung
  **offensichtlich öffentlich**. **Das ist die tragende Ausnahme** — und zugleich
  der Grund, warum die „nur öffentlich"-Einhegung (§1) rechtlich so zentral ist.
- **Art. 9 Abs. 2 lit. g — erhebliches öffentliches Interesse** (mit
  gesetzl. Grundlage), **lit. j — Archiv/Forschung.**

### 4.3 Betroffenenrechte & ihre Grenzen
- **Auskunft (Art. 15), Löschung (Art. 17):** grundsätzlich gegeben, **aber**
  eingeschränkt durch Medienprivileg (Art. 85), Archivzweck (Art. 17 Abs. 3 lit. d)
  und Meinungs-/Informationsfreiheit (Art. 17 Abs. 3 lit. a).
- **Design-Konsequenz:** Es braucht einen **dokumentierten Prozess** für
  Betroffenenanfragen (wer prüft, nach welchen Kriterien) und die technische
  Möglichkeit zur **Sperrung/Redaction** eines Spans — **ohne** die Beweiskette
  (Hash, WARC) zu zerstören (siehe §10).

---

## 5. Persönlichkeitsrecht, Bild & gesprochenes Wort

### 5.1 Zitattreue = Persönlichkeitsschutz UND unser stärkstes Argument
BVerfG-Rechtsprechung (Echternach/Böll-Linie): Ein **verfälschtes oder erfundenes
Zitat** verletzt das allgemeine Persönlichkeitsrecht. Der **korrekte Wortlaut** tut
das **nicht**. → Das **Wortlaut-Prinzip von wortlaut ist damit nicht nur
journalistische Redlichkeit, sondern aktiver Rechtsschutz.** Genau deshalb: **kein
Modell glättet je ein Zitat.** Ein geglättetes Zitat wäre ein potenziell
persönlichkeitsverletzendes Falschzitat.

### 5.2 Recht am eigenen Bild — §§ 22, 23 KUG (ab Bild/Video, Phase 4+)
Bildnisse dürfen nur mit Einwilligung verbreitet werden — **außer** § 23 Abs. 1
Nr. 1: **Bildnisse aus dem Bereich der Zeitgeschichte.** Politiker bei öffentlichen
Auftritten fallen regelmäßig darunter. Grenze: berechtigte Interessen (§ 23 Abs. 2),
v. a. Privatsphäre. → nur Amtsträger, nur öffentliche Auftritte.

### 5.3 Recht am gesprochenen Wort — § 201 StGB (die scharfe Grenze)
Das **nichtöffentlich** gesprochene Wort ist strafrechtlich geschützt; heimliche
Aufnahmen sind verboten. **Öffentliche** Reden (Parlament, Bühne, Podcast, Livestream)
sind **nicht** „nichtöffentlich". → **Nur öffentlich/für die Öffentlichkeit
Gesprochenes** wird verarbeitet. Diese Grenze ist im Ingest hart zu ziehen: keine
Quelle ohne belegten Öffentlichkeitscharakter.

---

## 6. Äußerungsrecht & Haftung des Betreibers

- **Tatsache vs. Meinung:** wortlaut **behauptet nichts** — es gibt Wortlaut wieder
  und ordnet ihn Sprecher/Datum/Fundstelle zu. Damit ist das klassische Risiko
  (unwahre Tatsachenbehauptung) minimiert. **Kein generierter Fließtext, keine
  Wertung im Output** (Architektur-Prinzip = Rechtsschutz).
- **Kontext-/Framing-Risiko:** Die **Auswahl** und **Anordnung** von Zitaten kann
  ein eigenes Aussage-/Framing-Element sein und Persönlichkeitsrechte berühren. →
  Neutralität by design (kein Ranking „belastend zuerst", keine tendenziösen
  Cluster-Labels), Kontext mitliefern (voller Span + Fundstelle statt Schnipsel).
- **Betreiberhaftung / Host-Privileg:** DDG (früher TMG) — Verantwortlichkeit,
  Impressumspflicht (§ 5 DDG), ladungsfähige Anschrift. **Rechtsform mit
  Haftungsbegrenzung** dringend empfohlen (§12).
- **Gegendarstellung/Replik:** Mechanismus vorsehen, über den Betroffene einen
  Hinweis/Widerspruch zu einem Span verlinken können (entschärft Angriffe, stärkt
  Neutralitätsanspruch).

---

## 7. Strafrechtliche Grenzen bei den *Inhalten selbst*

Sonderfall: Was, wenn eine dokumentierte Äußerung **selbst** strafbar ist
(z. B. § 130 StGB Volksverhetzung)?
- **Dokumentation/Berichterstattung** über strafbare Äußerungen ist grundsätzlich
  zulässig (Sozialadäquanz, Presse-/Wissenschaftsfreiheit) — das ist der *Kern* des
  Projektzwecks: Belege sichern.
- **Aber:** Die **Art der Wiedergabe** darf nicht selbst zur Verbreitung/Billigung
  werden. Neutrale, belegende Wiedergabe mit Quelle ≠ Verbreitung.
- **Design-Konsequenz:** Möglichkeit, einzelne Spans als „rechtlich sensibel" zu
  **markieren** (nicht löschen — der Beleg ist ja der Zweck), ggf. mit Zugangs-/
  Darstellungsstufe. **Anwaltlich klären**, bevor solche Inhalte öffentlich
  ausgespielt werden.

---

## 8. Threat-Model — Angriffsvektoren & Gegenmaßnahmen (rechtlich/Beweis)

> Dieser Abschnitt deckt die **rechts-/beweisnahen** Vektoren (T1–T11). Der
> **vollständige technische & operative Angriff** — Server, DDoS, Prompt Injection,
> Supply Chain, Physisch, OpSec — steht im eigenen
> [Security & OpSec Threat-Model](security.md).

| # | Angriff | Gegenmaßnahme (Architektur) |
|---|---------|------------------------------|
| T1 | **Quelle wird gelöscht** (Post weg) | Fremdarchiv (Wayback/archive.today) + WARC + SHA-256 **vor** Verarbeitung; der Beleg überlebt die Löschung |
| T2 | **„Das Zitat ist manipuliert"** | Hash über Rohbytes + `/verify`-Endpoint: jeder kann die Kette nachrechnen |
| T3 | **„Aus dem Kontext gerissen"** | Voller Span + `locator` (Drucksache/Seite/Zeitstempel) + Permalink zur Primärquelle; kein Schnipsel-Cropping |
| T4 | **Abmahnung / Unterlassung** | Nur amtliche/öffentliche Quellen, Wortlauttreue, Neutralität, dokumentierte Rechtsgrundlage pro Quelle |
| T5 | **DSGVO-Löschbegehren** | Definierter Prüfprozess; Medien-/Archivprivileg; Redaction-Mechanismus, der Beweis erhält (§10) |
| T6 | **SLAPP / Einschüchterungsklage** | Rechtsform mit Haftungsbegrenzung, Rechtsschutz(versicherung), Prozessdoku, öffentliche Unterstützer |
| T7 | **Politischer Druck bei Regierungsbeteiligung** | Souveränität (lokal, kein US-Cloud-Abschaltpunkt), Fremdarchiv-Redundanz, OSS → forkbar/dezentral spiegelbar |
| T8 | **Vorwurf „Feindesliste/Doxxing"** | Harte Beschränkung auf Amtsträger *in Funktion*, nur öffentliche Äußerungen, keine Privatadressen/-daten, Neutralität by design |
| T9 | **Framing-Vorwurf („linke Kampagne")** | Kein generierter Text, keine Wertung, parteiagnostisches Datenmodell, Auswahl-Neutralität, Quelloffenheit |
| T10 | **Technischer Angriff / Übernahme** | Souveräne Infra, Backups off-host, Rohbyte-WORM read-only, minimale Angriffsfläche (kein öffentlicher Schreibzugang zum Korpus) |
| T11 | **Poisoning** (untergeschobene Fake-Quelle) | Nur verifizierte Ingest-Adapter (Primärquellen), Provenienz-Kette, Human-Verify für nicht-amtliche Zuordnung |

---

## 9. Neutralität als Rechtsstrategie (nicht nur Ethik)

Die im [Architektur](architecture.md) verankerte Neutralität ist der rote Faden durch
fast alle Verteidigungslinien:
- **parteiagnostisches Datenmodell** (`party` = freies Feld, kein Enum) → entkräftet
  „gegen eine bestimmte Partei gerichtet".
- **kein `summarize`, nur Spans** → entkräftet Falschzitat- und Framing-Vorwürfe.
- **AfD ist erste Datenquelle, nicht Sonderlogik** → das Tool taugt für jede Partei,
  das ist die beste Antwort auf „einseitig".

---

## 10. Design-Konsequenzen fürs Datenmodell (Brücke zur `datamodel.md`)

Direkt umzusetzen in [Datenmodell](datamodel.md):

1. **`rights_basis` pro `source`** (enum): `amtliches_werk_§5` | `zitat_§51` |
   `oeffentlich_gemacht_art9e` | `lizenz` | `ungeklaert`. Kein Ausspielen von
   `ungeklaert`.
2. **`visibility_class` pro `span`**: `public` | `restricted` | `sensitive`
   (für §7-Fälle) — Darstellung/Zugriff steuerbar, ohne zu löschen.
3. **`redaction`-Mechanismus:** Ein Span kann **gesperrt** werden (nicht aus dem
   Ledger gelöscht). Hash/WARC/Rohbyte bleiben als Beweis; nur die *Ausspielung*
   wird unterbunden. Append-only bleibt gewahrt.
4. **`verification`** (schon geplant): `official` | `machine` | `human_verified` —
   nicht-amtliche Sprecherzuordnung nie als zitierfähig ohne Human-Verify.
5. **Personenkreis-Constraint:** `speaker` muss ein Mandat/Funktion (`mandate`)
   tragen; kein Span ohne Amtsträger-Bezug.
6. **`public_evidence`-Pflicht:** jede `source` braucht belegten
   Öffentlichkeitscharakter (Permalink + Archivlink) — kein Ingest ohne.
7. **Betroffenen-Replik:** optionales `rebuttal_link` am Span.
8. **Audit-Log:** jede Redaction/Sperrung mit Grund + Zeit + Verantwortlichem
   (für spätere rechtliche Nachweisbarkeit).

---

## 11. Offene Fragen für den Fachanwalt (Prüf-Checkliste)

- [ ] **§ 51 vs. Zitat-Datenbank:** Trägt das Zitatrecht eine strukturierte
  Wortlaut-Datenbank *ohne* klassischen redaktionellen Fließtext? Welche
  Mindest-„Eigenleistung" ist nötig?
- [ ] **Medienprivileg (Art. 85):** Erfüllt wortlaut die Kriterien
  „journalistisch-redaktionell"? Was muss das Auftreten/Impressum dafür ausweisen?
- [ ] **Art. 9 lit. e Reichweite:** Deckt „offensichtlich öffentlich gemacht"
  auch die **dauerhafte, durchsuchbare Aggregation**, oder nur die Einzeläußerung?
- [ ] **Social Media (Phase 4):** Plattform-TOS vs. Urheberrecht des Verfassers vs.
  DSGVO — unter welcher Konstruktion überhaupt zulässig? Screenshot-Beweiswert?
- [ ] **Strafbare Inhalte (§7):** Wie dürfen möglicherweise volksverhetzende
  Original-Äußerungen dokumentiert/ausgespielt werden, ohne selbst zu verbreiten?
- [ ] **Rechtsform & Haftung (§12):** e. V. vs. gGmbH vs. Trägerschaft; persönliche
  Haftung des Betreibers minimieren.
- [ ] **Ingest fremder Datenbanken:** Wo endet zulässige Primärquellen-Nutzung, wo
  beginnt Eingriff in fremdes Datenbankherstellerrecht (§§ 87a ff)?
- [ ] **Löschbegehren-Prozess:** rechtssichere SOP für Betroffenenanfragen.

---

## 12. Betriebsform & organisatorische Absicherung (Empfehlung)

- **Rechtsform mit Haftungsbegrenzung** (e. V. oder gGmbH) statt Betrieb als
  Privatperson — schützt die Betreiber persönlich, ermöglicht Spenden/Förderung.
- **Impressum + Datenschutzerklärung + Verantwortlicher** (DDG/DSGVO-Pflicht).
- **Rechtsschutz** für Medien-/Äußerungsrecht einplanen (T6).
- **Transparenzbericht** (welche Löschbegehren, wie entschieden) — stärkt
  Neutralitäts- und Medienprivileg-Anspruch.
- **Dokumentierte Redaktions-/Prüf-Richtlinie** (wer nimmt was auf, nach welchen
  Kriterien) — macht „journalistisch-redaktionell" nachweisbar.

---

## TL;DR

- **MVP (amtliche Werke) ist rechtlich der sichere Hafen:** § 5 UrhG (gemeinfrei) +
  Art. 9 Abs. 2 lit. e DSGVO (öffentlich gemacht) + amtliche Sprecherzuordnung.
  Deshalb ist genau *das* der MVP.
- **Wortlauttreue ist Rechtsschutz**, nicht nur Ethik: Falschzitat verletzt
  Persönlichkeitsrecht, korrekter Wortlaut nicht → **kein Modell glättet je.**
- **Drei harte Einhegungen** entschärfen fast alles: nur **Amtsträger**, nur
  **öffentlich**, nur **Wortlaut**.
- **Riskant und darum später + mit Anwalt:** Interviews/Podcasts (§ 51), Social
  Media (Art. 9/TOS), strafbare Inhalte (§ 130).
- **Datenmodell muss tragen:** `rights_basis`, `visibility_class`, `redaction`
  (Beweis-erhaltend), `verification`, Amtsträger-Constraint, Audit-Log.
- **Vor Phase 4 zwingend Fachanwalt.** Checkliste steht in §11.
