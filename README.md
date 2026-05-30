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

# point the layout at where your clips live (data/store_layout.json maps cameras -> files)
# batch mode: write events to JSONL
python -m pipeline.run --store STORE_BLR_002 --layout data/store_layout.json \
    --out pipeline/output/events_STORE_BLR_002.jsonl

# live mode: stream events straight into the running API in real time
python -m pipeline.run --store STORE_BLR_002 --api-url http://localhost:8000 --realtime
```

`data/store_layout.json` maps each `camera_id` to a clip path, an entry line, and zone
polygons (normalised 0–1 coordinates). Replace it with the organiser's official layout
when available — no code changes are needed.

The pipeline emits the event schema in `app/models.py`; events land in the JSONL file
and (with `--api-url`) are POSTed to `/events/ingest` in batches.

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
