# wortlaut

> **Ein Archiv des öffentlichen Wortes.** Was Mandatsträger öffentlich sagen, wird
> erfasst, fremdarchiviert, gehasht und wörtlich wiederauffindbar gemacht.
> Die Maschine findet, strukturiert, sortiert — **sie urteilt nicht.**

Ausgabe ist immer: **wörtlicher Span, Sprecher, Datum, Permalink, Archivlink, Hash.**
Kein „fasse zusammen". Zitierfähig heißt: **Wortlaut oder gar nichts.** Ein Zitat,
das ein Modell geglättet hat, ist eine Behauptung. Der echte Wortlaut nicht — der
steht da.

> *„Ich will Zeugnis ablegen bis zum letzten."* — Victor Klemperer

**Historischer Anker:** Victor Klemperer dokumentierte in *LTI – Lingua Tertii
Imperii* die Sprache des Dritten Reiches Wort für Wort, Beleg für Beleg — nicht als
Ankläger, sondern als Philologe, der Zeugnis ablegt. `wortlaut` ist dieselbe Haltung
in Code: **belegen statt urteilen.**

---

## Was es tut

Frag das System: *„Was hat Fraktion X im Landtag Y zu Thema Z eingebracht?"* →
Antwort ist eine Liste **wörtlicher Zitate** mit Drucksachen-Nummer, Datum, Permalink
zur Primärquelle, Archivlink und Hash. In Sekunden, nicht in Stunden Handrecherche.
Jedes Zitat ist unabhängig gegen einen Hash nachprüfbar.

## Prinzipien (die Verfassung des Projekts)

1. **Provenienz vor allem.** Nichts wird verarbeitet, was nicht vorher archiviert
   und gehasht ist. Die Kette Rohbyte → Hash → Fremdarchiv → Span ist lückenlos.
2. **Die Maschine urteilt nicht — als Architektur.** Retrieval gibt nur wörtliche
   Spans zurück, nie generierten Fließtext. Es gibt keinen `summarize`-Pfad.
3. **Parteineutral by design.** Kein Datenmodellfeld bevorzugt eine Partei.
4. **Öffentlich einsehbar & reproduzierbar.** Code offen (AGPL-3.0), jede
   Beweiskette von außen nachprüfbar.
5. **Nur öffentliche Äußerungen von Mandatsträgern in politischer Funktion.** Keine
   Privatsphäre, keine Nicht-Mandatsträger, kein Doxxing.

## Status

**Planungsphase / Pre-Alpha.** Architektur, Recht, Security und Datenmodell sind
spezifiziert (siehe [`docs/`](docs/)). Der erste Code (Beweis-Ledger + Hash-/Archiv-
Pipeline) ist der nächste Schritt.

**MVP-Fokus:** öffentliche, amtliche Quellen (Bundestag-DIP-API + Landtags-
Plenarprotokolle) — urheberrechtlich gemeinfrei (§ 5 UrhG), amtliche
Sprecherzuordnung. Weitere Quellen und Parteien folgen. Die Architektur ist von
Tag 1 partei- und quellen-agnostisch.

## Dokumentation

| Dokument | Inhalt |
|----------|--------|
| [docs/architecture.md](docs/architecture.md) | Vision, Evidence-Ledger, Datenmodell-Überblick, API, Deployment, Roadmap |
| [docs/datamodel.md](docs/datamodel.md) | Vollständiges Schema, Immutability, Anti-Halluzination-Gate, Ingest-Adapter-Interface |
| [docs/legal.md](docs/legal.md) | Rechtslage (UrhG, DSGVO, KUG), Beweis-Threat-Model, Anwalts-Checkliste |
| [docs/security.md](docs/security.md) | Security- & OpSec-Threat-Model (Adversary-Modell, Prompt Injection, Verteidigungs-Architektur) |

## Mitmachen

Gesucht: **RAG, Archivierung, Diarization, Datenqualität, Recht.** Neue Quellen
werden als **Ingest-Adapter** beigetragen (~eine Datei, siehe
[docs/datamodel.md](docs/datamodel.md) §7) — der Beweis-Kern bleibt unberührt.
Details in [CONTRIBUTING.md](CONTRIBUTING.md).

## Lizenz

[AGPL-3.0](LICENSE) — Netzwerk-Copyleft: Wer eine Instanz betreibt, muss den Code
offenlegen. Verhindert proprietäre Forks eines Transparenz-Werkzeugs.
