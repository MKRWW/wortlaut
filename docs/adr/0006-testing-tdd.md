# ADR-0006: pytest + Testcontainers, TDD-Pflicht

- **Status:** Accepted (2026-07-18)

## Kontext
Der Kern-Wert steckt in **Invarianten**, die nur gegen echte Infrastruktur beweisbar
sind: append-only DB-Trigger, `pgvector`, WORM-Object-Lock, CHECK-Constraints.
Mocks würden genau diese Invarianten *nicht* prüfen — das wäre die gefährlichste Lücke.

## Entscheidung
- **pytest** (+ `pytest-asyncio`, `pytest-cov`) als Test-Runner.
- **Testcontainers** für Integrationstests: echte **PostgreSQL** und **MinIO** im
  Container pro Testlauf → Trigger/WORM/pgvector werden real geprüft.
- **TDD-Pflicht**: kein Produktivcode ohne vorher **fehlschlagenden** Test
  (red → green → refactor). Verankert im Engineering-Doc und Regelwerk (R-TEST).

## Konsequenzen
- (+) Invarianten (R-DATA-01/06) werden gegen echte DB/WORM bewiesen, nicht simuliert.
- (+) TDD erzwingt kleine, testbare Increments — die Loganalyzer-Lektion.
- (−) Integrationstests sind langsamer; CI braucht Docker (in GitHub Actions vorhanden).
- Trennung: schnelle **Unit-Tests** (Logik, rein) vs. **Integrationstests**
  (Testcontainers) mit Marker, damit Unit-Feedback schnell bleibt.

## Alternativen
- **Fakes/Mocks für DB/WORM:** testet die entscheidenden Invarianten nicht → verworfen.
- **docker-compose Test-Env:** real, aber weniger in den Testlauf integriert → verworfen.
