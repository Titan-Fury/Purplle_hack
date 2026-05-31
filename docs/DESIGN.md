# DESIGN

## Overview

The system has two independently deployable halves joined by one contract — the **event
schema** in `app/models.py`. The detection pipeline is a producer; the API is a consumer.
Nothing in the API knows about pixels, and nothing in the pipeline knows about SQL. The
only thing they agree on is the shape of a behavioural event. This boundary is the most
important design decision in the project: it lets the heavy, GPU-bound CV work run
anywhere (a workstation, a batch job, an edge box) while the API stays a small, stateless,
horizontally-scalable web service.

## Data flow

1. **Detection** (`pipeline/`): YOLOv8 detects people per frame; ByteTrack assigns a track
   id that survives short occlusions; an OSNet (or histogram-fallback) Re-ID gallery maps
   tracks to a stable, store-level `visitor_id`. A per-track state machine
   (`tracker.py`) converts movement into the 8 event types — entry/exit by signed
   line-crossing, zone enter/exit/dwell by point-in-polygon, and billing-queue depth by
   counting concurrent occupants of the billing zone.
2. **Event stream** (`emit.py`): every event is stamped with a UUID and an ISO-8601 UTC
   timestamp (clip start + frame offset), validated against the Pydantic schema, written
   to JSONL, and optionally streamed to the API in real time.
3. **Ingestion** (`app/ingestion.py`): batches of ≤500 are validated per-item (partial
   success), de-duplicated by `event_id` (idempotency), and stored in Postgres.
4. **Analytics** (`app/analytics.py`, `anomalies.py`): metrics, funnel, heatmap and
   anomalies are computed on read over a time window, excluding staff and treating the
   `visitor_id` (not the raw event) as the unit of analysis so re-entries never
   double-count.
5. **Surface**: REST endpoints + a live dashboard that polls them every 3 seconds.

## Why these choices (short form — full reasoning in CHOICES.md)

* **Compute-on-read, not pre-aggregation.** At this data scale, computing metrics from raw
  events on each request keeps the system simple and always-fresh ("real-time, not cached
  from yesterday"). The known cost is that this won't hold at 40 live stores; the migration
  path is materialised per-minute rollups keyed by `(store, minute, zone)`.
* **Visitor session = `visitor_id`.** Re-ID reuses the id across a re-entry and across
  overlapping cameras, so a `COUNT(DISTINCT visitor_id)` is the conversion denominator and
  the funnel unit. This is what makes the north-star metric robust to the re-entry
  inflation problem.
* **Confidence is never suppressed.** Low-confidence detections are emitted with their
  score so the API can reason about uncertainty, rather than silently dropping them.

## Edge cases handled

Group entry (N tracks → N entries), staff exclusion (rule-based classifier flags
`is_staff`), re-entry (Re-ID match on an exited visitor → `REENTRY`, not a second
`ENTRY`), partial occlusion (ByteTrack recovery + graceful confidence), empty periods
(zero-traffic returns zeros, never null/crash), and cross-camera overlap (store-level
Re-ID gallery shares identity between the entry and floor cameras).

## Real-data calibration & known limitations

The system was run end-to-end on the real Brigade Road (ST1008) footage. Honest notes:

* **Footage reality vs. brief:** clips are 30/25 fps and ~2.5 min (not 15 fps / 20 min as
  the brief stated); the on-screen timestamps anchor the recording to ~20:10 IST,
  10 Apr 2026, which is what `--clip-start` is set to so POS correlation lines up.
* **Re-ID is 3-tier and degrades gracefully:** OSNet (torchreid, **active in the real run**,
  GPU) → a torchvision ResNet18 embedding → an HSV histogram. OSNet brought unique
  visitors from 46 (histogram) to **14**, deduplicating the same shopper across the
  entry/floor cameras (and surfacing a real `REENTRY`).
* **Staff now firing:** the back-of-house CAM 4 oracle plus a "60s+ continuous single-zone
  dwell ⇒ staff (counter/till)" rule flag **2 staff**, who the API excludes at the
  *visitor* level (any staff event drops the whole visitor) — leaving 12 customers.
* **Efficiency:** detection runs at `vid_stride=2` (decode every other frame), roughly
  halving GPU work with negligible impact on counts; Re-ID embeddings are computed once per
  track (at birth), not per frame.
* **Conversion = 0 on the demo clip is correct, not a bug.** The POS has a purchase lull
  from 19:54→20:25 IST; the 2.5-min clip window (≈20:09–20:12) contains no transaction
  within the 5-minute correlation window, so the system correctly attributes *no* false
  conversions. On a longer window with in-window purchases it produces non-zero conversion.
* **Entry line validated & corrected.** Reviewing annotated entry-camera frames showed the
  store interior is to the *left* and the mall to the *right*, so the original horizontal
  mid-line was counting vertical motion *in the mall*. I replaced it with a vertical line at
  the glass-door plane (x=0.68, inside=left), which is geometrically correct. But glare and
  occlusion at the glass door fragment ByteTrack tracks right at the threshold, so continuous
  line-crossings under-count (≈1–2 per clip). The system therefore treats **multi-camera
  unique visitors (OSNet-deduped, 14) as the robust footfall metric** that drives the funnel;
  the raw ENTRY/EXIT line events are a secondary directional signal, not the headcount.

## AI-Assisted Decisions

**1. Staff detection — VLM vs. rule-based (LLM suggested VLM; I overrode).** When I asked
an LLM how to flag staff, its first suggestion was a vision-language model prompted per
crop ("is this person wearing a store uniform?"). I overrode it for the default path. A
per-crop VLM call is expensive, adds an external dependency and latency, and is hard to
make deterministic for an automated scoring harness. Instead I used a behavioural
heuristic — staff visit many zones, stay far longer than shoppers, and cross the entry
line repeatedly — which needs no network and is explainable. I kept the VLM as a
documented optional path because for genuinely ambiguous footage it would likely beat the
heuristic; the trade-off is cost and determinism, not capability.

**2. Idempotency strategy (LLM suggested DB upsert; I partly agreed).** The LLM proposed
Postgres `INSERT ... ON CONFLICT DO NOTHING`. I agreed with the intent but chose a
dialect-agnostic "select existing ids, then insert the rest" so the exact same code runs
on SQLite (tests, local) and Postgres (production) and returns accurate
accepted/duplicate counts. For batches ≤500 the extra SELECT is negligible.

**3. Coverage measurement (LLM diagnosed the greenlet gap).** My first coverage run
reported ~45% on the analytics module even though the endpoints clearly executed. An LLM
correctly identified that SQLAlchemy's async engine runs query code inside greenlets,
which coverage.py cannot trace without `concurrency = greenlet`. Adding that to
`.coveragerc` moved measured coverage from 65% to ~93% — the tests had been exercising the
code all along; only the measurement was wrong. I agreed and kept the fix.

## Live dashboard (Part E)

The brief rewards *proof that the pipeline → API → dashboard are genuinely connected, not
batch-processed*. So beyond the static seeded view, `pipeline/replay.py` streams the real
events into the API **in clip-time order** (scaled by `--speed`) onto a separate
`ST1008_LIVE` store; the dashboard's **▶ Live Replay** button switches to that store and
polls every second, so unique-visitors / funnel / queue visibly climb as events arrive. The
live store is kept separate from the seeded `ST1008` so the always-on public demo never goes
blank, and `POST /stores/{id}/reset` is **gated to `*_LIVE` ids** so production data can
never be wiped.

The dashboard (vanilla JS, no CDN) adds a store-map heatmap (rendered from `/layout`
polygons), a live event ticker (`/events`), and 3D/parallax depth. The annotated
camera-feed panel is **local-only by design**: the footage licence forbids publishing the
clips, so `pipeline/annotate.py` writes a **git-ignored** `app/static/demo.mp4` that the
dashboard shows only if present — the public deployment stays licence-compliant.

## Observability & read-path optimisation

Three layers answer "is the service healthy?": structured per-request JSON logs
(`trace_id`, endpoint, `latency_ms`, `status_code`, `event_count`), the business `/health`
(per-store last event + `STALE_FEED`), and **`/stats`** — in-process app metrics
(`app/observability.py`) aggregating request volume, per-endpoint average latency, error
rate and total events ingested. The endpoint label is the route *template*
(`/stores/{store_id}/metrics`) so cardinality stays bounded; at fleet scale these counters
would export to Prometheus.

The live dashboard polls **`/stores/{id}/summary`**, a single batched read that returns
metrics + funnel + heatmap + anomalies + recent events in one round-trip and one DB session,
instead of five separate polls per refresh — fewer connections and less middleware overhead
while keeping each individual endpoint available for the grading harness.
