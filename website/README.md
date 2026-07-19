# wortlaut — Homepage (wortlaut.io)

Statische Landingpage. **Kein Build, keine Abhängigkeiten.** Reines HTML/CSS.

## Dateien
- `index.html` — die Seite (eine Seite, Anker-Sektionen: Was, Prinzipien, FAQ, Mitmachen)
- `styles.css` — Marken-Look (Terminal-Dark, Monospace-Akzente, Grün/Amber)

## Lokal ansehen
Einfach `index.html` im Browser öffnen, oder ein Mini-Server:
```bash
python -m http.server -d website 8080   # dann http://localhost:8080
```

## Deploy auf den Webserver (wortlaut.io)
Den Inhalt von `website/` in das Web-Root des Servers legen (statisch ausliefern,
z.B. via nginx/caddy/Apache oder jedem Static-Hosting). Es braucht keinerlei
Runtime, nur einen Webserver, der Dateien ausliefert.

## Redaktionelles
- **Keine Betreiber-/Infra-Specifics** in den Text (öffentliche Seite, gleiche
  Disziplin wie das Repo).
- **Keine Klarnamen** der Truppe hier, die Seite spricht in Kompetenzen, nicht Personen.
- Links zeigen auf `develop` im Repo; bei Bedarf auf `main` umstellen.
