# Increment-Spec 0068: prod-Image nach ghcr.io publizieren

- **Issue:** #68 · **Status:** Reviewed · **Phase/Layer:** phase/1 · Packaging/CI (root) · Public/AGPL
- **Baut auf:** #66 (generisches Dockerfile + CI-`docker`-Job build+smoke).
- **Coder:** Architekt (CI/YAML ist per 0066-Konvention Architekten-Territorium; hermes fasst
  `.github/workflows/*` nicht an — Existing-File-Edit + YAML-Landmine = hermes-Schwäche).

## 1. Ziel
Der bestehende CI-`docker`-Job baut+smoke-testet das Image, publiziert es aber nicht. Dieses Increment
erweitert **nur** diesen Job, sodass **bei `push` auf `develop`/`main`** das smoke-grüne Image nach
`ghcr.io/mkrww/wortlaut` gepusht wird — reproduzierbar per `:<git-sha>` gepinnt plus rollierendem
`:develop`/`:latest`. Pull-Ziel für die (private) prod-compose auf dem Dedicated.

## 2. Files (NUR diese ändern)
- `.github/workflows/ci.yml` — Job `docker` erweitern (siehe §4). Sonst nichts.

> NICHT ändern: App-Code, `Dockerfile`, `pyproject.toml`, Tests, andere CI-Jobs.

## 3. Testbare Akzeptanzkriterien (DoR-Gate)
- **AC1 — Publish bei push.** Given ein `push` auf `develop` (bzw. `main`), When der `docker`-Job grün
  durchläuft, Then existiert in ghcr.io das Image `ghcr.io/mkrww/wortlaut` mit Tag `:<github.sha>` **und**
  `:develop` (bzw. `:latest` bei `main`). *Verifikation:* nach dem Merge-Push `docker manifest inspect
  ghcr.io/mkrww/wortlaut:<sha>` bzw. Package-Listing.
- **AC2 — Kein Publish bei PR.** Given ein `pull_request`-Event, When der `docker`-Job läuft, Then wird
  **nicht** eingeloggt/gepusht (nur hadolint+build+smoke wie bisher). *Verifikation:* im PR-CI-Log fehlen
  die Login/Publish-Steps (`if: github.event_name == 'push'` überspringt sie).
- **AC3 — Kein Extra-Secret.** Login ausschließlich über das eingebaute `GITHUB_TOKEN`; der Job trägt
  `permissions: packages: write` (+ `contents: read`). *Verifikation:* keine `secrets.*` außer
  `GITHUB_TOKEN` im Job; kein neues Repo-Secret nötig.
- **AC4 — Publish nur nach grünem Smoke.** Die Login/Publish-Steps stehen **hinter** dem bestehenden
  Smoke-Step; schlägt Build/Smoke fehl, bricht der Job vor dem Push ab (Step-Reihenfolge + `set -euo
  pipefail`). *Verifikation:* Publish-Steps folgen Smoke im Job; kein Push bei rotem Smoke.
- **AC5 — Package public.** Nach dem ersten Publish ist das Package `wortlaut` auf **public** gestellt
  (kein Pull-Token auf dem Dedicated nötig). *Verifikation:* `docker pull ghcr.io/mkrww/wortlaut:<sha>`
  ohne Login erfolgreich. **Betreiber-Handgriff (einmalig):** GHCR-Packages sind beim ersten Push privat;
  Sichtbarkeit auf public setzen ist ein **einmaliger** GHCR-Package-Settings-Schritt (nicht via
  `GITHUB_TOKEN` automatisierbar) — siehe §6.
- **AC6 — Gates grün.** Alle 6 CI-Checks grün + 0 neue Sonar-Issues. YAML vor Push validiert.

## 4. YAML — Job `docker` erweitern (genau so; Rest des Jobs unverändert)
Ergänzungen: `permissions`-Block auf Job-Ebene + zwei neue Steps NACH dem Smoke-Step.
```yaml
  docker:
    name: Docker Image (build + smoke + publish)
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      # ... hadolint / Build (AC1) / Smoke (AC2-AC5) unveraendert ...
      - name: Login ghcr.io (nur push, nicht PR) [AC2/AC3]
        if: github.event_name == 'push'
        run: echo "${{ secrets.GITHUB_TOKEN }}" | docker login ghcr.io -u "${{ github.actor }}" --password-stdin
      - name: Publish nach ghcr.io (nur push, nur smoke-gruen) [AC1/AC4]
        if: github.event_name == 'push'
        run: |
          set -euo pipefail
          image="ghcr.io/mkrww/wortlaut"
          sha="${{ github.sha }}"
          docker tag wortlaut:ci "$image:$sha"
          docker push "$image:$sha"
          if [ "${{ github.ref_name }}" = "main" ]; then moving="latest"; else moving="develop"; fi
          docker tag wortlaut:ci "$image:$moving"
          docker push "$image:$moving"
          echo "published $image:$sha and $image:$moving"
```

## 5. Do-NOT (hart)
- KEINE anderen CI-Jobs, KEIN App-Code, KEIN Dockerfile, KEINE Tests ändern.
- KEIN Publish bei `pull_request` (Guard `if: github.event_name == 'push'` an beiden Steps — nie entfernen).
- KEIN Token in URL/Log; `GITHUB_TOKEN` nur via `--password-stdin`.
- Image-Name strikt lowercase (`ghcr.io/mkrww/wortlaut`).

## 6. Betreiber-Nachgang (einmalig, nach erstem Merge)
- GHCR-Package `wortlaut` auf **public** setzen (Package-Settings → Change visibility → Public).
- Dann in der (privaten) prod-compose auf `image: ghcr.io/mkrww/wortlaut:<sha>` pinnen.
