# ADR-0002: `uv` als Build-/Dependency-Tool

- **Status:** Accepted (2026-07-18)

## Kontext
Reproduzierbare Builds sind für ein Beweis-orientiertes Projekt nicht optional
(Supply-Chain, R-SEC). Das erste Skelett nutzte `pip` ohne Lockfile.

## Entscheidung
**`uv`** für venv, Dependency-Resolution, Lockfile (`uv.lock`) und Task-Ausführung.
`pyproject.toml` bleibt Quelle der Wahrheit für Metadaten/Deps.

## Konsequenzen
- (+) **`uv.lock`** → deterministische, reproduzierbare Installs (Supply-Chain-Härtung).
- (+) Sehr schnell (Resolution/Install), ein Tool statt pip+venv+pip-tools.
- (+) CI wird schlanker und cache-freundlicher.
- (−) Jüngeres Tool; Team muss `uv`-Kommandos lernen.
- **Folge-Increment:** Toolchain-Migration pip→uv (CI-Workflows, Lockfile) — spec-first,
  nicht nebenbei.

## Alternativen
- **pip + pyproject (Status quo):** kein echtes Lockfile → verworfen.
- **Poetry:** Lockfile ja, aber langsamer und eigenwilliger als uv → verworfen.
