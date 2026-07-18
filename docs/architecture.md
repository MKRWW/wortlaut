# wortlaut — Architektur & Projektplan

> **Ein Archiv des öffentlichen Wortes.** Was Mandatsträger öffentlich sagen, wird
> erfasst, fremdarchiviert, gehasht und wörtlich wiederauffindbar gemacht. Die
> Maschine findet, strukturiert, sortiert — **sie urteilt nicht.** Ausgabe ist immer:
> wörtlicher Span, Sprecher, Datum, Permalink, Archivlink, Hash.

Kein „fasse zusammen". Zitierfähig heißt: **Wortlaut oder gar nichts.**

**MVP-Fokus:** öffentliche amtliche Quellen (Bundestag-DIP-API + Landtags-
Plenarprotokolle). **Ziel danach:** parteineutrale Infrastruktur für *alle*
Mandatsträger. Die Architektur ist von Tag 1 partei- und quellen-agnostisch.

**Repository & Hosting:** GitHub ist die öffentliche Collaboration-Plattform
(Issues, PRs). Zusätzlich läuft ein **selbstgehosteter, souveräner Pull-Mirror** des
Codes. Der **Korpus** (Rohbytes, WARC, Hashes, WORM-Ledger) bleibt immer souverän/
lokal — nie auf fremder Cloud.

---

## 1. Leitprinzipien (die Verfassung des Projekts)

1. **Provenienz vor allem.** Nichts wird verarbeitet, was nicht vorher archiviert
   und gehasht ist. Jedes Derivat (Transkript, Embedding, Klassifikation) trägt die
   Quell-Hash als Fremdschlüssel. Die Kette Rohbyte → Hash → Fremdarchiv → Span ist
   lückenlos oder der Span existiert nicht.
2. **Die Maschine urteilt nicht — als Architektur, nicht als Absicht.** Retrieval
   gibt *nur* wörtliche Spans zurück, nie generierten Fließtext. Kein `summarize`-Pfad
   im Ausgabecode.
3. **Parteineutralität by design.** Kein Datenmodellfeld, kein Ranking, kein Filter
   bevorzugt oder benachteiligt eine Partei.
4. **Öffentlich einsehbar & reproduzierbar.** Code ist OSS (AGPL-3.0). Die
   Beweiskette jedes Zitats ist von außen nachprüfbar (Hash + Archivlink).
5. **Souveränität, präzise.** Souverän bleiben MUSS der Korpus und die Beweis-
   Integrität. Die Inferenz (Embedding/Rerank/Query-LLM) ist zustandslos und
   austauschbar (dev lokal, live gehostet zulässig) — dorthin fließt nur öffentlicher
   Span-Text.
6. **Nur öffentliche Äußerungen von Mandatsträgern in politischer Funktion.** Keine
   Privatsphäre, keine Nicht-Mandatsträger, kein Doxxing. Im Datenmodell erzwungen.

---

## 2. Das Herzstück: Evidence Ledger (immutable, append-only)

Das Projekt steht und fällt nicht mit dem RAG — das sind Standardbausteine. Es steht
und fällt mit der **Beweiskette**:

```
Rohquelle
  → SHA-256 über die Rohbytes (nicht über geparsten Text!)
  → WARC-Capture + Fremdarchiv (Wayback + archive.today)
  → WORM-Speicher (append-only, kein Update, kein Delete)
  → ERST DANN: Parse → Spans → Embed → Index
```

**Regel:** Verarbeitung setzt einen bestätigten Ledger-Eintrag voraus. Das Derivat
referenziert immer die Quell-Hash. Wird die Quelle später gelöscht, bleibt Hash +
WARC + Fremdarchiv als Beweis bestehen.

---

## 3. Datenmodell (Überblick)

Kern-Entitäten. **Vollständiges Schema, Enums, Immutability-Trigger, Anti-
Halluzination-Gate, `verification`-State-Machine und Ingest-Adapter-Interface:
[datamodel.md](datamodel.md).**

- **`source`** — die archivierte Rohquelle (immutabel): Typ, `rights_basis`,
  `content_hash` (SHA-256 über Rohbytes, Anker + Dedup), Archiv-Links, WORM-Ref.
- **`speaker`** + **`mandate`** — Mandatsträger, partei-agnostisch; Mandat zeitlich
  begrenzt (löst Parteiwechsel und den „nur Amtsträger, in Funktion"-Constraint).
- **`span`** — die zitierfähige Einheit (Inhalt immutabel): `verbatim_text`
  (wörtlich), Offsets in die Quelle, Locator, Permalink, `verification`.
- **`span_state`** — mutabler Status (Verifikation, Sichtbarkeit, Redaction),
  auditiert.
- **`span_embedding`** — abgeleitete Vektoren, model-versioniert (Provider-swappable).
- **`topic_tag` / `span_topic`** — Klassifikation als Metadaten, **nie** im Span-Text.

> **Design-Grundsatz:** Beweis immutabel, Status mutabel. Löschbegehren *sperren*
> einen Span (`redacted`), zerstören ihn nie — der Beweis (Hash/WARC) bleibt.

---

## 4. Schnittstellen (API-Grundriss)

Read-first. Alle Such-Endpunkte geben **Span-Listen** zurück, nie Fließtext.

```
GET  /v1/search            hybrid (dense + BM25 + rerank) → span[]
GET  /v1/spans/{id}        ein Span inkl. voller Beweiskette
GET  /v1/sources/{id}      Rohquelle + alle Archiv-/Hash-Belege
GET  /v1/speakers/{id}     Mandatsträger + external_ids
GET  /v1/spans/{id}/verify Beweiskette prüfen: Hash nachrechnen, Archivlinks live
POST /v1/ingest            (intern/authed) neue Quelle in den Ledger
```

Jede Such-Antwort ist eine Liste von: `{verbatim_text, speaker, spoken_at, permalink,
archive_url, content_hash, verification}`.

---

## 5. Tech-Stack & Deployment

| Schicht | Wahl | Warum |
|---------|------|-------|
| DB | PostgreSQL + `pgvector` | Ein Store für relational + Vektor |
| Volltext/BM25 | Postgres-FTS (Start) / OpenSearch (später) | Hybrid-Retrieval |
| Embeddings | bge-m3 (dev) / gehostet (live) | mehrsprachig, dense+sparse |
| Reranker | bge-reranker-v2-m3 | |
| LLM (nur Query-Verständnis, NIE Ausgabe-Text) | vLLM (dev) / gehostet (live) | reine Suchhilfe |
| ASR (später) | WhisperX + Diarization | mit Pflicht-Human-Verify |
| Archivierung | WARC + Wayback + archive.today | Fremdarchiv-Redundanz |
| WORM-Storage | S3 mit Object-Lock (MinIO) | append-only Beweisspeicher |
| Backend | Python (FastAPI) | ML-Ökosystem |
| Deploy | Docker-Compose, ein Dienst pro File | |

**Deployment-Modell (dev ↔ live):** Die Inferenz ist eine austauschbare Provider-
Schicht (Details: [datamodel.md](datamodel.md) §7b). **Dev/MVP lokal** (billig
iterieren, Korpus beliebig oft neu embedden). **Live gehostet** (Verfügbarkeit,
Skalierung). Kein Souveränitäts-Bruch: zur gehosteten AI geht nur öffentlicher Span-
Text; Korpus und Beweis-Integrität bleiben provider-unabhängig lokal.

---

## 6. MVP-Schnitt (bewusst klein — die Kette beweisen, nicht die Vision)

> **MVP = eine vollständige, saubere Pipeline über DIP-API (Bundestag) +
> Plenarprotokolle eines Landtags.**

Warum genau die:
- Amtliche Werke, **§ 5 UrhG gemeinfrei** → urheberrechtlich unbedenklich.
- Redner **amtlich zugeordnet** → keine Diarization-Unsicherheit.
- Maschinenlesbar → **kein Whisper nötig**.
- Beweist die *ganze Kette* an einfachem Material, bevor Audio/Social die Komplexität
  sprengen.

**Bewusst geparkt (spätere Phasen):** Social Media, Whisper-Diarization, Podcasts,
Bild-OCR — der rechtlich + technisch riskanteste Teil.

---

## 7. Phasen-Roadmap

- **Phase 0 — Fundament:** Ledger-Schema, WORM-Storage, Hash+Archiv-Pipeline. *Ohne
  AI.* Nur: rein, gehasht, fremdarchiviert, unveränderlich.
- **Phase 1 — MVP:** DIP + Landtags-Protokolle → Spans → Hybrid-Retrieval → API.
- **Phase 2 — Fläche:** weitere Landtage + Bundestag-Protokolle vollständig.
- **Phase 3 — Audio:** WhisperX + Diarization + Pflicht-Human-Verify-Workflow.
- **Phase 4 — Social:** eigenes Arbeitspaket **mit Jurist-Review** vorab.
- **Phase 5 — parteineutral:** Öffnung für alle Parteien, öffentliche Instanz.

---

## 8. Recht & Sicherheit

Zwei eigene Dokumente:
- **Recht/Beweis:** [legal.md](legal.md) — UrhG, DSGVO, KUG/StGB, Threat-Table,
  Anwalts-Checkliste.
- **Security/OpSec:** [security.md](security.md) — Adversary-Modell, Prompt Injection,
  Supply Chain, Verteidigungs-Architektur.

Kern: Das **MVP (amtliche Werke) ist der rechtlich sichere Hafen** (§ 5 UrhG +
Art. 9(2)(e) DSGVO). **Wortlauttreue ist Rechtsschutz** (ein Falschzitat verletzt das
Persönlichkeitsrecht, der korrekte Wortlaut nicht). Vor Phase 4 zwingend Fachanwalt.

---

## 9. OSS-Governance

- **Lizenz:** AGPL-3.0 (Netzwerk-Copyleft — verhindert proprietäre Forks).
- **Öffentlich:** Repo offen, Beweisketten von außen prüfbar.
- **Erweiterbar:** Ingest-Adapter als Plugins (ein Adapter pro Quelle) — Mitstreiter
  tragen Quellen bei, ohne den Kern anzufassen.
- **Beitragende gesucht:** RAG, Archivierung, Diarization, Datenqualität, Recht.

---

## 10. Offene Design-Entscheidungen

1. BM25: Postgres-FTS zum Start vs. OpenSearch.
2. WORM-Storage: MinIO Object-Lock vs. anderes append-only-Schema.
3. Embedding: bge-m3 (1024) fix vs. Multi-Vektor (ColBERT-Stil) später.
4. Speaker-Resolution: Fuzzy-Matching-Schwelle; ab wann automatisch vs. Human-Verify.
5. Merkle-Anchor-Intervall & externer Vertrauensanker.
6. UI: Braucht das MVP schon eine UI oder reicht API + CLI?
