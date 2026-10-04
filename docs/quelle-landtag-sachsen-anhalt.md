# Quelle: Landtag von Sachsen-Anhalt

## Quelle und URL-Muster

Stenografische Berichte des Landtags von Sachsen-Anhalt als PDF mit Textlayer, fortlaufend
nummeriert. URL-Muster:
`https://padoka.landtag.sachsen-anhalt.de/files/plenum/wp{wahlperiode}/{nr:03d}stzg.pdf`
(Beispiel: `https://padoka.landtag.sachsen-anhalt.de/files/plenum/wp8/118stzg.pdf`). Eine nicht
vorhandene Nummer liefert 404. Links auf `www.landtag.sachsen-anhalt.de/fileadmin/…` leiten per
301 dorthin; die Sitzungsperioden-Seiten verlinken nicht alle Protokolle und taugen nicht zur
Entdeckung.

## Rechtsgrundlage

Amtliche Werke nach § 5 Abs. 2 UrhG laut Impressum des Landtags (`rights_basis =
amtliches_werk_p5`). Änderungsverbot (§ 62) und Quellenangabe (§ 63): der Wortlaut wird
unverändert übernommen, jeder Span führt sein Permalink.

## robots.txt-Lage und Schalter

robots.txt von PADOKA sperrt `Disallow: /files/`. Der Abruf ist deshalb standardmäßig aus:
Schalter `WORTLAUT_LANDTAG_ST_ENABLED` (Default `False`), freigeschaltet erst nach Zustimmung des
Landtags. Ohne Schalter wirft `discover` sofort `LandtagStDisabled` und stellt keine Anfrage;
`fetch` einer bekannten Ref bleibt möglich (Reparse, Tests). Zugriff mit erkennbarem User-Agent
`wortlaut-ingest/1.0 (+https://github.com/MKRWW/wortlaut)`, wenige Anfragen je Lauf.

## Entdeckung über die Nummer

Die höchste vorhandene Nummer je Wahlperiode wird per HEAD gesucht (exponentiell, dann binär;
≤ 21 Anfragen je Wahlperiode), danach werden die letzten `lookback` Protokolle als Refs
aufsteigend vergeben. Zusätzlich wird Wahlperiode `wahlperiode + 1` geprüft (neue Wahlperiode nach
der Wahl im September 2026). `since` wird nicht ausgewertet — die Quelle nennt das Datum erst im
PDF; Doppelte fängt der Kern über den Inhalts-Hash ab (`skipped_duplicate`).

## Vertrauen

Vertrauen `secondary`: Spans gelten als `machine`. Anhebung später, nach der Stichprobe.
`WORTLAUT_ADAPTER_VERIFIED` gilt nur für Plugins — für eingebaute Adapter erfolgt die Anhebung per
Code-Änderung.

## Rollen-Regel

Regierungsmitglieder werden aufgenommen: Fraktion leer, Rolle = Amtsbezeichnung
(`speaker_hint["role"]`; fehlt der Schlüssel, gilt `adapter.mandate_role` = `MdL`).
Berichterstatter sind Abgeordnete: Fraktion leer, keine eigene Rolle. Die Mandatssuche
unterscheidet Rollen, damit „Ministerin, ohne Fraktion“ und „Berichterstatterin, ohne Fraktion“
derselben Person nicht in ein Mandat fallen.

## Bekannte Grenzen

- Lücke in der Nummernfolge → binäre Suche kann neuere Protokolle übersehen (nicht beobachtet).
- Weitere Rollenformen (z. B. „Staatssekretärin im …“) laufen über `staatssekret`; unbekannte
  Formen landen als Fraktionstext — sichtbar in der Stichprobe vor der Anhebung.
