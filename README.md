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

# 4. replay a sample event stream in simulated real time (no GPU required)
python -m dashboard.replay --events data/sample_events.jsonl --api-url http://localhost:8000 --speed 30

# 5. open the live dashboard
#    http://localhost:8000/dashboard
```

The API is at `http://localhost:8000`, interactive docs at `/docs`, dashboard at `/dashboard`.

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
  deduplicated shoppers across cameras down to **14 visitors**, of which **2 were flagged
  staff** (60s+ stationary dwell / back-of-house) and excluded, leaving 12 customers, plus
  one real `REENTRY`. Detection runs at `vid_stride=2` for efficiency.

---

## API endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/events/ingest` | Batch ingest (≤500), idempotent by `event_id`, partial success |
| GET | `/stores/{id}/metrics` | Unique visitors, conversion, dwell/zone, queue depth, abandonment |
| GET | `/stores/{id}/funnel` | Entry → Zone → Billing → Purchase with drop-off % (session-based) |
| GET | `/stores/{id}/heatmap` | Zone visit/dwell normalised 0–100, `data_confidence` flag |
| GET | `/stores/{id}/anomalies` | Queue spike, conversion drop, dead zone (INFO/WARN/CRITICAL) |
| GET | `/health` | DB status + per-store last event + `STALE_FEED` warning |

All endpoints accept an optional `?date=YYYY-MM-DD`; the default window is the day of the
store's most recent event ("today" in live operation).

---

## Tests

```bash
pip install -r requirements.txt
pytest                      # 22 tests, ~93% statement coverage (gate: >70%)
```

Covered edge cases: empty store, all-staff clip, zero purchases, re-entry in funnel,
idempotency, graceful 503, low-confidence retention, group entry, billing-queue depth.

---

## Project layout

```
app/        FastAPI service: ingest, metrics, funnel, heatmap, anomalies, health
pipeline/   Detection: YOLOv8 + ByteTrack + OSNet Re-ID -> structured events
dashboard/  Real-time replay tool (web dashboard is served by the API at /dashboard)
data/       store_layout.json, pos_transactions.csv, sample_events.jsonl
tests/      pytest suite (prompt blocks at the top of each file)
docs/       DESIGN.md, CHOICES.md
```

## Deployment (demo)

The API + dashboard are a single service and deploy to Railway as-is: point Railway at
this repo (it builds the `Dockerfile`), add a Postgres plugin, and set `DATABASE_URL` to
the provided async DSN (`postgresql+asyncpg://...`). The container honours `$PORT`.
