# Apex Store Intelligence

Turn raw retail CCTV into a live **offline-store conversion-rate** analytics system:
detect and track people → emit structured behavioural events → ingest into a
containerised REST API → watch metrics update on a live dashboard.

**North-star metric:** `conversion_rate = unique purchasing visitors / unique visitors`.

---

## Quick start (5 commands)

```bash
# 1. clone
git clone https://github.com/Titan-Fury/Purplle_hack.git && cd Purplle_hack

# 2. start the API + Postgres (no .env needed — compose has working defaults)
docker compose up --build -d

# 3. confirm it is live
curl http://localhost:8000/health

# 4. replay the real event stream in simulated real time (no GPU required)
python -m pipeline.replay --api http://localhost:8000 --speed 20

# 5. open the live dashboard and click "▶ Live Replay"
#    http://localhost:8000/dashboard
```

The API is at `http://localhost:8000`, interactive docs at `/docs`, dashboard at `/dashboard`.

> No Docker? `./run.sh` does the same locally (SQLite + auto-seed, no GPU/Postgres),
> `./run.sh test` runs the suite, `./run.sh docker` is the compose path above. Every push
> is checked by GitHub Actions (`.github/workflows/ci.yml`): unit tests **and** a
> `docker compose up` boot smoke that hits `/health` and `/metrics`.

### Live dashboard demo (Part E)

The replay streams the committed events into the API **in clip-time order** onto a separate
`ST1008_LIVE` store, so the dashboard's visitors / funnel / queue **climb live** — proof the
pipeline → API → dashboard are genuinely connected, not batch-loaded. Click **▶ Live Replay**
in the header (it switches to `ST1008_LIVE` and polls every second), then run step 4.

The dashboard is one self-contained page (vanilla JS, no CDN) with an animated 3D background,
a light/dark theme toggle (follows your OS, remembers your choice), and graceful
reduced-motion / offline / no-WebGL fallbacks.

Optional **camera-feed panel** (local only): the annotated clip contains the CCTV footage,
which the licence forbids publishing, so it is **never committed**. Generate it locally and the
dashboard will show it automatically:

```bash
python -m pipeline.annotate --store ST1008 --camera CAM_ENTRY_01 --seconds 40
#  -> writes app/static/demo.mp4 (gitignored); refresh the dashboard to see the panel
```

---

## Running the detection pipeline against the clips

The pipeline is intentionally separate from the API image (it needs torch/ultralytics and
ideally a GPU). Run it on a host with the clips and feed its output into the API.

```bash
# install pipeline deps (install a CUDA torch build first if you have a GPU)
pip install -r pipeline/requirements.txt

# batch mode: write events to JSONL (real store = ST1008, Brigade Road Bangalore)
python -m pipeline.run --store ST1008 --layout data/store_layout.json \
    --out pipeline/output/events_ST1008.jsonl

# live mode: stream events straight into the running API in real time
python -m pipeline.run --store ST1008 --api-url http://localhost:8000 --realtime
```

### Zero-config: an arbitrary folder of clips (no registered store)

If a reviewer hands over a bare folder of clips with no `store_layout` entry, point the
pipeline at it — roles, a default door-line and full-frame zones are synthesized on the fly
(`pipeline/ingest.py`):

```bash
python -m pipeline.run --store ANY_ID --clips-dir path/to/clips \
    --out pipeline/output/events_ANY_ID.jsonl
```

Each clip becomes a camera; its role is resolved by filename convention then content
auto-classification. The entry camera gets a fallback vertical door-line at mid-frame so
entry/exit counting still fires (real stores override it after a one-time calibration), and
floor/billing cameras get a full-frame zone so dwell/queue/conversion still compute. Footfall
(unique visitors) never depends on geometry, so even an all-`floor` folder yields a real funnel.

### Camera roles (entry / floor / billing / staff_area)

A camera's role is resolved in priority order (`pipeline/camera_resolver.py`):
1. **Registry** — `store_layout.json` declares each camera's role (authoritative; a
   physical camera doesn't move, so its role is a registration fact).
2. **Convention** — the file path/name (e.g. `clips/ST1008/entry/cam.mp4`) when not in the
   registry. Enables bulk fleet ingestion.
3. **Auto-classify** (`--resolve-mode auto`) — last resort: a VLM (if `VLM_API_KEY` is set)
   or a motion/track heuristic proposes a role, logged for human confirmation.

> The VLM is **optional and off by default**. It speaks the OpenAI-compatible API, so it
> works free & open-source with **Ollama** (no key): `VLM_BASE_URL=http://localhost:11434/v1`,
> `VLM_MODEL=llama3.2-vision` (or `llava`, `moondream`, `qwen2.5vl`). Hosted free tiers
> (Groq, OpenRouter, Gemini) also work by pointing `VLM_BASE_URL`/`VLM_API_KEY` at them.

For the provided footage the registry maps: `CAM 3`→entry, `CAM 1`/`CAM 2`→floor,
`CAM 5`→billing, `CAM 4`→staff_area. The **staff_area** camera (back-of-house) flags any
person seen there as staff and, via the shared Re-ID gallery, excludes them from customer
metrics on every camera.

### Data & privacy

`store_layout.json` maps each `camera_id` to a clip path, entry line, and zone polygons
(normalised 0–1). The committed `data/pos_transactions.csv` is **anonymised**
(store_id, transaction_id, timestamp, basket_value) — the raw Purplle export contains
customer names/phone numbers and is git-ignored. The POS loader auto-detects both the
documented schema and the raw 39-column export (aggregating line items to invoices and
converting IST→UTC).

### Notes on the demo data (read before judging the numbers)

The live demo is seeded from a real pipeline run on the Brigade Road clips
(`data/seed_events_ST1008.jsonl`, store `ST1008`, ~20:10 IST on 10 Apr 2026):

- **Conversion shows 0% for this clip — and that is correct, not a bug.** The POS has a
  purchase lull (19:54 → 20:25 IST), so no transaction falls within the 5-minute window of
  the billing visits captured in this 2.5-minute clip. The system deliberately attributes
  **no false conversions**; on a window containing in-window purchases it reports non-zero.
- **Entry line was validated against the footage and corrected** to a vertical line at the
  glass-door plane (the store is to the *left*, mall to the *right* — a horizontal line
  mis-counted mall motion). Because glare/occlusion at the glass door fragment tracks, raw
  line-crossings under-count; **footfall is measured by multi-camera unique visitors** (the
  funnel's denominator), not by raw ENTRY events.
- Re-ID uses **OSNet** (torchreid, GPU) with torchvision ResNet18 / histogram fallbacks; it
  resolved the per-camera tracks to **14 store-level identities**, of which **2 were flagged
  staff** (60s+ stationary dwell / back-of-house oracle) and excluded, leaving **12 customers**.
  Detection runs at `vid_stride=2` for efficiency.

### Measured results — full-clip run (evidence, not a single sampled window)

The pipeline was run end-to-end over **all 5 cameras for their full duration** (~11.6 min of
footage, ~9.7k processed frames at `vid_stride=2`), OSNet Re-ID active on GPU. Output is the
committed `data/seed_events_ST1008.jsonl` (351 events) — reproducible with:

```bash
python -m pipeline.run --store ST1008 --layout data/store_layout.json --out data/seed_events_ST1008.jsonl
```

| Measured signal | Value | Note |
|---|---|---|
| Events emitted | **351** | across the full clips |
| Store-level identities | **14** | OSNet-resolved |
| → flagged staff / customers | **2 / 12** | back-of-house oracle + 60s-dwell rule |
| ENTRY / EXIT / REENTRY (line) | **1 / 1 / 0** | door-line crossings — see accuracy note |
| Distinct identities on entry cam | **3** | tracker formed 3 stable tracks at the door |
| Cross-camera Re-ID merges | **1** | conservative → unique-visitor is an *upper bound* |
| Billing JOIN / ABANDON | **4 / 4** | symmetric (abandon only after a real queue-join) |
| Peak queue depth | **1** | no real queue formed in this window |
| Avg dwell (s) | MAKEUP_FOH 11.6 · BILLING 10.8 · MAKEUP_WALL 7.3 · FRAGRANCE 3.4 · ENTRANCE 1.0 | per-zone, computed |

**Entry-count accuracy, honestly (with error bars).** The glass-door line fired only **1**
crossing, yet the entry camera clearly shows steady foot traffic (eyeball check of CAM 3:
people at ~0/32/42/63/95/127/137s, several on the mall side through reflective glass). So the
true footfall is **bracketed**:

```
raw line-crossings (1)   ≤   true footfall   ≤   total identities (12 customers)
        lower bound (occlusion/geometry undercounts)      upper bound (some cross-camera dups unmerged)
```

The system therefore reports footfall as **multi-camera unique visitors (12)**, not raw line
crossings — robust to a noisy door line. The remaining gap (entry-cam tracker formed only 3
stable tracks against heavy motion-blur + lightbox glare) is a YOLOv8n/feed-quality limit on
this specific low-bitrate camera, not a logic error; a higher-capacity detector (YOLOv8m /
RT-DETR) would close it. This is the one place I'd invest next to lift the detection score.

---

## API endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/events/ingest` | Batch ingest (≤500), idempotent by `event_id`, partial success |
| GET | `/stores/{id}/metrics` | Unique visitors, conversion, dwell/zone, queue depth, abandonment |
| GET | `/stores/{id}/funnel` | Entry → Zone → Billing → Purchase with drop-off % (session-based) |
| GET | `/stores/{id}/heatmap` | Zone visit/dwell normalised 0–100, `data_confidence` flag |
| GET | `/stores/{id}/anomalies` | Queue spike, conversion drop, dead zone (INFO/WARN/CRITICAL) |
| GET | `/stores/{id}/summary` | **Batched** metrics+funnel+heatmap+anomalies+events in one round-trip (dashboard) |
| GET | `/stores/{id}/events` | Recent events, newest first (powers the live dashboard ticker) |
| GET | `/stores/{id}/layout` | Zone polygons + billing flags (powers the store-map heatmap) |
| POST | `/stores/{id}/reset` | Clears a `*_LIVE` demo store before replay (gated; refuses real stores) |
| GET | `/health` | DB status + per-store last event + `STALE_FEED` warning |
| GET | `/stats` | App observability: request volume, per-endpoint latency, error rate, events ingested |

All endpoints accept an optional `?date=YYYY-MM-DD`; the default window is the day of the
store's most recent event ("today" in live operation).

---

## Requirements coverage

Where each scored dimension of the brief is implemented (for quick review):

| Requirement | Where | Evidence |
|---|---|---|
| Entry/exit + per-session visitor token | `pipeline/tracker.py` (`_handle_threshold`, Re-ID gallery) | door-line crossing; `visitor_id` per session |
| Staff exclusion / re-entry / group | `pipeline/reid.py`, `tracker.py`; `app/analytics.py` | staff flagged + excluded at visitor level; `REENTRY` on re-id; each track = 1 ENTRY |
| Schema compliance + unique ids | `app/models.py` (Pydantic v2 validators) | per-field validation, UUID + ISO-8601, zone/`queue_depth` cross-checks |
| `/events/ingest` idempotent, partial success | `app/ingestion.py`, `routers/events.py` | dedup by `event_id`; per-item errors; `tests/test_ingest.py` |
| `/metrics` (staff excl., zero-purchase, real-time) | `app/analytics.py` | visitor-level staff exclusion; compute-on-read |
| `/funnel` session-based, no double-count | `app/analytics.py` (`compute_funnel`) | unit = `visitor_id`; `tests/test_metrics.py` |
| Anomalies (severity + suggested_action) | `app/anomalies.py` | queue spike / conversion drop / dead zone |
| Containerised, `docker compose up` | `Dockerfile`, `docker-compose.yml` | api + postgres, inline defaults |
| Structured logs + `/health` | `app/middleware.py`, `app/health.py`, `app/observability.py` | trace_id/latency/status JSON logs; `STALE_FEED`; `/stats` app metrics |
| Tests >70% + edge cases | `tests/` (49 tests, ~90% coverage) | empty store, all-staff, zero-purchase, re-entry, 503, idempotency, end-to-end |
| AI usage depth | `docs/DESIGN.md`, `docs/CHOICES.md`, test prompt blocks | AI-assisted decisions + 3 choices with overrides |
| Live dashboard (Part E bonus) | `app/static/dashboard.html`, `pipeline/replay.py` | simulated real-time replay; metrics climb live; animated 3D background + dark mode |

---

## Tests

```bash
pip install -r requirements.txt
pytest                      # 49 tests, ~90% statement coverage (gate: >70%)
```

Covered edge cases: empty store, all-staff clip, zero purchases, re-entry in funnel,
idempotency, graceful 503, low-confidence retention, group entry, billing-queue depth.

---

## Project layout

```
app/        FastAPI service: ingest, metrics, funnel, heatmap, anomalies, health + /dashboard
pipeline/   Detection (YOLOv8 + ByteTrack + OSNet) -> events; replay.py (live demo); annotate.py (local video)
data/       store_layout.json, pos_transactions.csv, seed_events_ST1008.jsonl
tests/      pytest suite (prompt blocks at the top of each file)
docs/       DESIGN.md, CHOICES.md
```

## Deployment (demo)

The API + dashboard are a single service and deploy to Railway as-is: point Railway at
this repo (it builds the `Dockerfile`), add a Postgres plugin, and set `DATABASE_URL` to
the provided async DSN (`postgresql+asyncpg://...`). The container honours `$PORT`.
