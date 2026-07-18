# ADR-0001: Python 3.12 als Sprache/Runtime

- **Status:** Accepted (2026-07-18)

## Kontext
Der Korpus-/RAG-Stack lebt im Python-Ökosystem: Whisper/WhisperX, HuggingFace,
vLLM/Infinity-Clients, pgvector-Bindings, wissenschaftliche Libs. Ein Sprachbruch
würde uns von diesem Ökosystem abschneiden.

## Entscheidung
**Python 3.12** als einzige Sprache für Backend, Pipeline und Tooling.

## Konsequenzen
- (+) Direkter Zugriff auf das gesamte ML-/RAG-Ökosystem.
- (+) 3.12: moderne Typisierung, Performance-Verbesserungen.
- (−) Nicht die schnellste Runtime; performancekritische Pfade ggf. später auslagern.
- Mindestversion `>=3.12` in `pyproject.toml` erzwungen; mypy `python_version = 3.12`.

## Alternativen
- **Go/Rust:** schneller, aber ML-Ökosystem-Anbindung teuer/unreif → verworfen.
- **Python 3.11:** ok, aber 3.12 bringt Typing-/Perf-Vorteile ohne relevante Nachteile.
