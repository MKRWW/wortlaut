# Increment-Spec 0066: Generisches prod-Dockerfile

- **Issue:** #66 · **Status:** Reviewed · **Phase/Layer:** phase/1 · Packaging (root) · Public/AGPL
- **Baut auf:** #64 (`python -m wortlaut`).

## 1. Ziel
Schlankes, non-root Multi-Stage-Image, das `python -m wortlaut ingest …` im Container lauffähig macht
(inkl. Migration). Command kommt von compose (das Image legt keinen Modus fest). Kein Secret im Image.

## 2. Files (NUR diese anlegen)
- `Dockerfile`      — exakt wie unten
- `.dockerignore`   — wie unten

> NICHT anlegen/ändern: `.github/workflows/*` (CI-Job macht der Architekt), `pyproject.toml`, App-Code,
> `store/migrations.py`, Tests. Keine git/docker/uv-Befehle (nur `git status --porcelain` im Abschluss).
> Base-Image als **Tag** lassen (`python:3.12-slim`) — den Digest-Pin setzt der Architekt (nicht raten!).

## 3. Dockerfile (genau so — die Reihenfolge/Layout ist kritisch)
```dockerfile
# syntax=docker/dockerfile:1
# --- Builder: Deps aus uv.lock (frozen), Projekt NICHT installieren ---
FROM python:3.12-slim AS builder
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# --- Runtime: venv + Source-Layout (migrations/ muss unter parents[3] liegen) ---
FROM python:3.12-slim AS runtime
RUN useradd --uid 10001 --create-home appuser
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY src/ /app/src
COPY migrations/ /app/migrations
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
USER appuser
CMD ["python", "-m", "wortlaut"]
```
WICHTIG (Begruendung, nicht ins Dockerfile): Das Projekt wird **nicht** nach site-packages installiert
(`--no-install-project`); der Code liegt als Source unter `/app/src` und `migrations/` unter `/app/migrations`,
damit `store/migrations.py` (`Path(__file__).parents[3]` == `/app`) die Migrationen findet. Deshalb NICHT
`uv sync` ohne `--no-install-project`, und NICHT das Layout ändern.

## 4. .dockerignore (genau so)
```
.git
.github
.venv
**/__pycache__
**/*.pyc
tests
data
*.md
scratch*
.claude*
```

## 5. Do-NOT (hart)
- KEINE CI/YAML-Dateien, KEIN `pyproject.toml`, KEIN App-Code, KEINE Tests anlegen/ändern.
- KEIN Digest raten (Base bleibt Tag `python:3.12-slim`).
- KEINE Secrets/ENV mit Zugangsdaten im Dockerfile.
- KEINE git/docker/uv-Befehle ausser `git status --porcelain`.

## 6. Abschluss (und NUR das)
- `git status --porcelain` ausgeben.
