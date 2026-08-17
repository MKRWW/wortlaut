# syntax=docker/dockerfile:1
# --- Builder: Deps aus uv.lock (frozen), Projekt NICHT installieren ---
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS builder
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# --- Runtime: venv + Source-Layout (migrations/ muss unter parents[3] liegen) ---
FROM python:3.12-slim@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de AS runtime
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
# Dokumentiert den Default-Port des Serving-Entrypoints (#81, WORTLAUT_API_PORT).
# Kein Port wird dadurch geoeffnet — das macht das Deployment (-p / compose).
EXPOSE 8000
CMD ["python", "-m", "wortlaut"]
