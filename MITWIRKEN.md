# Du willst helfen — hier entlang

Sehr viele Menschen haben Hilfe angeboten. Danke. Diese Seite ist der Versuch, daraus
Arbeit zu machen, ohne dass alles über ein einzelnes Postfach läuft.

**Such dir eine Spur aus. Du musst dich bei niemandem melden, um anzufangen.**

Wenn du Code beitragen willst, steht das Technische in [CONTRIBUTING.md](CONTRIBUTING.md).
Diese Seite hier beantwortet die Frage davor: *Was passt zu mir, und was kostet es mich?*

---

## Die wichtigste Unterscheidung: Wer liest das Material?

wortlaut archiviert, was tatsächlich gesagt wurde. Ein Teil dieses Materials ist
menschenverachtend — das ist ja der Grund, warum es dokumentiert gehört. Wer es über
längere Zeit durcharbeitet, trägt eine reale Belastung davon. Das ist keine Frage von
Härte, und niemand muss sich das beweisen.

Deshalb ist die Mitarbeit **nach Exposition** sortiert, nicht nach Programmiersprache.
**Die meisten Aufgaben kommen mit dem Material nie in Berührung.**

---

## Spur A — kein Kontakt mit Inhalten

Hier liegt der größte Teil der Arbeit, und hier ist gerade der Engpass.

- **Betrieb**: Server, Deploy, Container, Tunnel, Monitoring, Backups
- **CI/CD**: Pipelines, Gates, Release
- **Frontend**: die Demo-Seite, Barrierefreiheit, Responsiveness
- **Doku**: Anleitungen, Architektur, Onboarding
- **Paketierung**: Images, Reproduzierbarkeit

Du siehst dabei Metadaten, Logs und Code — keine Redebeiträge.

## Spur B — Struktur, nicht Inhalt

Du siehst Text, arbeitest aber an der **Form**: Stimmt die Sprecher-Zuordnung? Ist das
Datum richtig? Hat der Parser den Tagesordnungspunkt erwischt? Sind die Offsets korrekt?

Die Beiträge laufen dabei am Auge vorbei, aber der Blick liegt auf der Mechanik. Für die
meisten ist das gut aushaltbar. Wenn nicht: wechsle zu A, ohne Erklärung.

## Spur C — Inhalt

Wortlaut-Stichproben, juristische Bewertung, Grenzfälle, Umgang mit möglicherweise
strafbaren Äußerungen.

**Freiwillig, klein, rotierend, zeitlich begrenzt.** Diese Spur ist nicht die „richtige"
Mitarbeit und nicht die anspruchsvollere — sie ist die anstrengendste. Wer hier
mitarbeitet, sollte das bewusst und begrenzt tun und jederzeit aufhören können.

---

## Ehrliche Ansagen vorab

**Antworten dauern.** Kommunikation ist für den Maintainer teuer. Antworten kommen
asynchron, meist in Issues, manchmal erst nach Tagen. Das ist keine Ablehnung und kein
Desinteresse. Das Projekt ist so gebaut, dass du **nicht** auf eine Antwort warten musst,
um anzufangen: Aufgaben stehen als Issues mit prüfbaren Kriterien da, und ob etwas fertig
ist, entscheidet die CI — nicht die Aufmerksamkeit einer Person.

**Pseudonyme sind ausdrücklich willkommen.** Wer Rechtsextremismus dokumentiert, kann zur
Zielscheibe werden. Wenn du unter Klarnamen nicht mitarbeiten kannst — wegen Arbeitgeber,
Familie oder Sicherheit — arbeite unter einem Pseudonym mit. Das ist kein Makel und
niemand fragt nach.

**Neutralität ist Bedingung, nicht Stilfrage.** Die Maschine gibt wieder, was gesagt
wurde, und wertet nicht. Kein Ranking, keine Kommentierung, keine Empörungsschleife. Das
ist nicht Zurückhaltung, sondern die tragende Verteidigungslinie: Ein Werkzeug, das für
jede Fraktion gleich funktioniert, ist gegen den Vorwurf der Parteilichkeit immun. Wer
hier mitarbeitet, trägt das mit — auch dann, wenn es in den Fingern juckt.

**Der Beweispfad bleibt KI-frei.** Kein Modell fasst zusammen, glättet oder formuliert um,
bevor etwas ausgegeben wird. Ausgabe ist der wörtliche, gegen den Hash geprüfte Span. Wer
Ideen für generative Features hat: nicht in diesem Pfad. Werkzeuge daneben — Triage,
Sortierhilfen, Tests — sind eine andere Sache und diskutierbar.

---

## Was gerade gebraucht wird

Die aktuell offenen Aufgaben stehen in den
[Issues](https://github.com/MKRWW/wortlaut/issues). Der Engpass liegt derzeit im
**Betrieb** (Spur A): Die Pipeline läuft, die Read-API ist container-fähig — was fehlt,
ist der Weg von „läuft auf dem Rechner" zu „läuft öffentlich erreichbar".

Wenn du **Betrieb** kannst, ist das gerade der wertvollste Beitrag überhaupt.

---

## Was gerade *nicht* gebraucht wird

Damit niemand Arbeit in etwas steckt, das nicht angenommen wird:

- **Zusammenfassungen, Bewertungen, „KI-Analysen" von Aussagen.** Verletzt das
  Kernprinzip (siehe oben).
- **Screenshot-Sammlungen.** Der Wert liegt in der nachrechenbaren Kette aus Primärquelle,
  Hash und Fremdarchiv. Ein Screenshot beweist nichts.
- **Ausweitung auf Privatpersonen.** Nur Mandats- und Funktionsträger, nur öffentlich
  Gesagtes. Das ist eine harte Grenze, auch rechtlich.
- **Neue Features, solange der MVP nicht steht.** Erst sichtbar, dann breiter.

---

## Wie du anfängst

1. Lies die vier nicht verhandelbaren Prinzipien in [CONTRIBUTING.md](CONTRIBUTING.md).
2. Such dir ein Issue, das zu deiner Spur passt.
3. Schreib einen Kommentar darunter, dass du es nimmst — damit niemand doppelt arbeitet.
4. Leg los. Für Code gilt: Branch, PR gegen `develop`, CI grün.

Wenn nichts Passendes dabei ist: Mach ein Issue auf und beschreibe, was du kannst und
wie viel Zeit du hast. Das ist hilfreicher als eine Nachricht, weil es sichtbar bleibt
und andere daran anknüpfen können.

---

Das Projekt steht unter AGPL-3.0. Es gehört niemandem allein, und es soll auch nicht von
einer einzelnen Person abhängen.
