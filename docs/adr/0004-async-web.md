# ADR-0004: Async-I/O (asyncpg/httpx) + FastAPI

- **Status:** Accepted (2026-07-18)

## Kontext
Der Dienst ist I/O-lastig: Quellen holen, bei zwei Fremdarchiven ablegen, in WORM
schreiben, DB-Transaktionen, Embedding-/Rerank-Calls, API-Requests. Blockierendes I/O
würde Durchsatz und Parallelität unnötig begrenzen.

## Entscheidung
Durchgängig **async**: **asyncpg** (via SQLAlchemy async engine), **httpx** (async)
für Fetch/Archiv/Inferenz-Provider, **FastAPI** (async) für die API.

## Konsequenzen
- (+) Hohe I/O-Parallelität (viele Archiv-/Fetch-Calls gleichzeitig).
- (+) FastAPI: Pydantic-Validierung, OpenAPI, async-nativ.
- (−) Async ist fehleranfälliger (Event-Loop, Cancellation); verlangt Disziplin.
- (−) Tests müssen async-fähig sein (`pytest-asyncio`).
- **Regel:** CPU-lastige Arbeit (Hashing großer Blobs, Parsing) läuft in
  Worker/Threadpool, blockiert nie den Event-Loop.

## Alternativen
- **Synchron:** einfacher, aber blockiert bei I/O → für einen Netz-/IO-Dienst
  suboptimal → verworfen.
- **Hybrid (Kern sync, API async):** inkonsistent, mehr Grenzfälle → verworfen.
