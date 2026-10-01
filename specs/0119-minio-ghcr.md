# Increment-Spec: MinIO-Testimage aus eigener Registry (#119, Option A)

> ## AUFTRAG AN DEN CODER — ZUERST LESEN
> Du bist der **Coder**, nicht der Reviewer. **Implementiere diese Spec.**
> - Ändere genau die Dateien aus **§10**, sonst keine.
> - **Keine Rückfragen.** Wenn etwas unklar ist, halte dich wörtlich an **§11**.
> - **Schreibe keine Review-Analyse** und **ändere diese Spec nicht.**
> - Halte die Do-NOT-Liste in **§12** ein.
> - Führe **keine** git-, docker-, npm-, uv- oder alembic-Befehle aus außer dem in **§13**.

- **Story/Issue:** #119 · **Status:** Reviewed · **Phase/Layer:** Test-Infrastruktur, Deploy-Vorlage
- Methodik: [../docs/engineering.md](../docs/engineering.md) · Regeln: [../docs/rules.md](../docs/rules.md)
- Entblockt jeden offenen PR, insbesondere #120 (#118).

## 0. Ausgangslage

Die Integrationstests starten MinIO aus
`minio/minio@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e`.
Dieses Image ist weder auf Docker Hub noch auf quay.io mehr abrufbar. Alle WORM-Integrationstests
scheitern im CI-Setup mit `ImageNotFound`; `develop` ist seit mindestens 26.09.2026 rot.

Das unveränderte Image (`RELEASE.2025-09-07T16-13-09Z`) liegt jetzt öffentlich unter

```
ghcr.io/mkrww/minio@sha256:a16cad481969d7ddb6fcd2f1c58284af3d1266190b9bedc3ef0801aeb05a93a9
```

Inhalt identisch mit der Kopie im Cache des Betriebs: dieselben Schicht-Digests
(`RootFS.Layers`), dieselbe Konfiguration (`Cmd`, `Entrypoint`). Der Manifest-Digest unterscheidet
sich vom alten, weil das Manifest beim Umpacken neu geschrieben wird.

## 1. Ziel

Tests und Deploy-Vorlage ziehen MinIO aus der eigenen Registry, per Digest gepinnt. CI wird wieder
grün, ohne dass sich am Testverhalten etwas ändert.

## 2. Nicht-Ziele

- **Kein** Wechsel auf einen anderen S3-Dienst (Option B, eigene ADR).
- **Keine** Änderung an Testlogik, Fixtures oder Assertions.
- **Keine** Änderung an Produktivcode unter `src/`.

## 5. Testbare Akzeptanzkriterien

- **AC1** `tests/integration/conftest.py` und `tests/integration/test_cli_ingest.py` setzen
  `MINIO_IMAGE` auf genau `ghcr.io/mkrww/minio@sha256:a16cad481969d7ddb6fcd2f1c58284af3d1266190b9bedc3ef0801aeb05a93a9`. Kein anderer Verweis auf
  `minio/minio` bleibt in `tests/`.
- **AC2** `deploy/compose.example.yml` verwendet für den Dienst `worm` genau dieselbe Referenz statt
  `minio/minio:latest`.
- **AC3** Die volle Testsuite (`-m "not live"`) ist ohne jede weitere Änderung grün — lokal und im CI.

## 10. Files (NUR diese ändern)

- `tests/integration/conftest.py`
- `tests/integration/test_cli_ingest.py`
- `deploy/compose.example.yml`

## 11. Umsetzungsdetails je Datei

### `tests/integration/conftest.py` und `tests/integration/test_cli_ingest.py`

Jeweils nur die eine Zeile `MINIO_IMAGE = "..."` ersetzen durch:

```python
MINIO_IMAGE = (
    "ghcr.io/mkrww/minio@sha256:a16cad481969d7ddb6fcd2f1c58284af3d1266190b9bedc3ef0801aeb05a93a9"
)
```

Die Klammer ist Pflicht: Ohne sie waere die Zeile laenger als die erlaubten 100 Zeichen (ruff E501).
Diese Form ist zugleich die, die `ruff format` erzeugt.

Direkt darüber einen dreizeiligen Kommentar setzen (der ADR-0006-Verweis bleibt erhalten):

```python
# ADR-0006: digest-gepinnt (repository@sha256, ohne Tag — sonst pullt docker-py nicht).
# Unveraenderte Kopie von minio/minio:RELEASE.2025-09-07T16-13-09Z.
# Upstream ist nicht mehr abrufbar (#119).
```

Steht über der Zeile bereits ein Kommentar zum Image, wird er durch diesen ersetzt.

### `deploy/compose.example.yml`

Nur die Zeile `image: minio/minio:latest` ersetzen durch:

```yaml
    image: ghcr.io/mkrww/minio@sha256:a16cad481969d7ddb6fcd2f1c58284af3d1266190b9bedc3ef0801aeb05a93a9
```

Darüber, eingerückt wie die übrigen Kommentare des Dienstes, ein Kommentar:

```yaml
    # Gepinnte, unveraenderte Kopie von minio/minio:RELEASE.2025-09-07T16-13-09Z.
    # Upstream ist nicht mehr abrufbar (#119); ein Wechsel des Speichers braucht eine ADR.
```

## 12. Do-NOT (hart)

- Keine anderen Zeilen in diesen Dateien ändern.
- Keine weiteren Dateien anfassen.
- Keinen Tag statt des Digests verwenden.

## 13. Abschluss (und NUR das an Kommandos ausführen)

- `git status --porcelain` ausgeben. **Sonst nichts.**

Das Gate fährt der Reviewer selbst.
