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
