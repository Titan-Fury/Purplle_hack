# CHOICES

Three decisions, each with the options I weighed, what AI suggested, and what I chose.

---

## Decision 1 — Detection model: YOLOv8 + ByteTrack + OSNet Re-ID

**Options considered**

| Option | Pros | Cons |
|---|---|---|
| YOLOv8 + ByteTrack + OSNet | Mature, fast, CPU-capable; ByteTrack recovers occluded tracks; OSNet gives appearance Re-ID for re-entry/cross-camera | Re-ID adds weight; OSNet weights need a download |
| RT-DETR + ByteTrack | Strong accuracy, no NMS | Heavier, slower on modest GPUs, less battle-tested tooling |
| MediaPipe | Light, no GPU | Built for few foreground people, weak in crowded retail scenes |
| Detection only, no Re-ID | Simplest | Cannot solve re-entry or cross-camera dedup — fails two scored edge cases |

**What AI suggested.** The LLM's default recommendation was "YOLOv8 + ByteTrack" and it
initially treated Re-ID as optional. I pushed back: without appearance Re-ID, a customer
who steps out and returns produces a second `ENTRY`, which is exactly the re-entry
inflation the brief calls out as a known vendor problem. So I added OSNet Re-ID as a
store-level gallery and made it the design centre of `visitor_id` assignment.

**What I chose and why.** YOLOv8n for detection (fast, accurate enough; the brief grades
how I handle imperfection, not a perfect rate), ByteTrack for tracking (its two-stage
association keeps low-confidence boxes alive through partial occlusion instead of dropping
them), and OSNet for Re-ID. Crucially, the Re-ID layer **degrades gracefully**: if
torchreid/weights are unavailable it falls back to an HSV colour-histogram descriptor, so
the pipeline still runs end-to-end. Staff are excluded with a rule-based classifier rather
than a VLM (see DESIGN.md) — I considered a VLM for uniform recognition and would revisit
it if the heuristic's false-positive rate proved too high on real footage.

---

## Decision 2 — Event schema design

**Options considered.** (a) One flat table with nullable columns for everything; (b) a
typed event with a small open `metadata` object; (c) a separate table per event type.

**What AI suggested.** The LLM leaned toward a strongly-typed-per-event design (option c)
for "cleanliness". I overrode it. A table-per-type schema makes the most common query —
"reconstruct a visitor's session in time order across event types" — into a multi-way
union, and it makes ingestion branch on type. That is the wrong shape for this workload.

**What I chose and why.** A single event type (option b): fixed top-level fields that every
event shares (`event_id`, `store_id`, `visitor_id`, `event_type`, `timestamp`,
`confidence`, `is_staff`) plus a small `metadata` block for the few type-specific fields
(`queue_depth`, `sku_zone`, `session_seq`). Reasons: (1) it serialises to exactly the JSON
the brief specifies; (2) cross-field validators encode the rules directly — threshold
events (`ENTRY`/`EXIT`/`REENTRY`) must not carry a zone, zone events must, and
`BILLING_QUEUE_JOIN` must set `queue_depth`; (3) one Pydantic model validates both the
pipeline output and the API input, so a malformed event is rejected in one place. The cost
is a sparse `metadata` for most events, which is a non-issue at this scale and in JSONB.

---

## Decision 3 — API architecture: compute-on-read over pre-aggregation

**Options considered.** (a) Pre-aggregate metrics into rollup tables at ingest time;
(b) compute metrics on read from raw events; (c) an in-memory streaming aggregator.

**What AI suggested.** The LLM suggested pre-aggregating at ingest for speed. I disagreed
for the current scope. Pre-aggregation means every new metric needs a schema change and a
backfill, and it risks serving stale numbers — directly against the "real-time, not cached
from yesterday" requirement.

**What I chose and why.** Compute-on-read (option b): store raw events, compute metrics per
request over a time window. It is the simplest correct design, every endpoint is always
fresh, and adding a new metric is a pure-Python function with no migration. I am explicit
about the limit: this is an O(events-in-window) scan per request, so at 40 live stores
streaming continuously the first thing to break is `/funnel`/`/metrics` latency under
concurrent load. The migration path is materialised per-minute rollups keyed by
`(store, minute, zone)` plus a small hot cache, which preserves the same endpoint
contracts. Storage is Postgres (async via asyncpg) for production with SQLite as a
zero-setup fallback for tests and local runs — chosen so `docker compose up` needs no
external services and the test suite runs without a database server.
