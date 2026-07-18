# Mitmachen bei wortlaut

Danke, dass du beitragen willst. `wortlaut` sucht Leute mit **RAG, Archivierung,
Diarization, Datenqualität und Recht**. Auch nicht-technische Beiträge (juristische
Prüfung, Quellenrecherche, Doku) sind willkommen.

## Wo entwickelt wird

- **GitHub ist die Collaboration-Plattform:** Issues, Pull Requests, Diskussion.
  Fork → Branch → PR wie üblich.
- Es gibt zusätzlich einen **selbstgehosteten Spiegel** (souveräne Kopie des Codes).
  Für Beiträge musst du dich damit nicht befassen — arbeite ganz normal auf GitHub.

## Die nicht verhandelbaren Prinzipien

Ein PR, der eines davon verletzt, wird nicht gemergt — sie sind der Kern des Projekts:

1. **Wortlaut oder nichts.** Kein Code-Pfad, der ein Zitat glättet, zusammenfasst
   oder von einem Modell umformulieren lässt, bevor es ausgegeben wird. Ausgabe =
   wörtlicher Span aus der Datenbank, byte-geprüft gegen die verhashte Quelle.
2. **Provenienz vor Verarbeitung.** Nichts wird verarbeitet, was nicht vorher
   gehasht und fremdarchiviert wurde.
3. **Parteineutralität.** Kein Feld, kein Ranking, kein Default bevorzugt oder
   benachteiligt eine Partei.
4. **Nur Mandatsträger, nur öffentlich.** Keine Privatpersonen, keine nicht-
   öffentlichen Äußerungen.

## Neue Quelle beitragen (der häufigste Beitrag)

Eine neue Quelle (weiterer Landtag, weitere Plattform) wird als **Ingest-Adapter**
implementiert — siehe [docs/datamodel.md](docs/datamodel.md) §7. Der Adapter kann
*nur* entdecken/holen/parsen; Hashing, Archivierung, Speicherung und Indexierung
macht der Kern. So kann kein Adapter die Beweiskette umgehen. Das ist meist **eine
Datei**.

## Sicherheit

Sicherheitslücken **nicht** als öffentliches Issue melden. Nutze die im
Repository hinterlegten privaten Meldewege (Security Advisory). Das Threat-Model
liegt in [docs/security.md](docs/security.md).

## Qualität

- Signierte Commits bevorzugt.
- Kein Merge ohne Review.
- Tests für neue Adapter und Kernlogik.

## Lizenz

Beiträge stehen unter [AGPL-3.0](LICENSE).
