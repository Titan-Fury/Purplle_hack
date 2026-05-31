# Follow-up video — prep sheet

A tight script + the questions most likely to be asked, with answers grounded in the
code/design. Goal: show the thing works and that the decisions were deliberate, not lucky.

---

## 60-second demo script (record this first)

1. `docker compose up --build -d` → `curl /health` returns `LIVE`. *(one command, no .env)*
2. Open `/dashboard` — hero loads, KPIs animate in from the seeded `ST1008` run.
3. Click **▶ Live Replay**, run `python -m pipeline.replay --api ... --speed 20`.
   Visitors / funnel / queue **climb in real time** → pipeline → API → dashboard are live.
4. Open `/docs`, fire `GET /stores/ST1008/funnel` and `/metrics` — fresh JSON, not cached.
5. One line on the north star: *conversion = unique purchasing visitors / unique visitors.*

---

## Likely questions & crisp answers

**Q1. How do you know which clip is the entry / billing / staff camera?**
A fixed CCTV camera's role is an installation fact, so resolution is layered
(`camera_resolver.py`): **registry** (`store_layout.json`, authoritative) → **filename
convention** (`clips/<store>/entry/…`, for fleet bulk-ingest) → **auto-classify** (VLM if a
key is set, else a motion heuristic) as a *logged proposal*, never trusted blindly for the
counting-critical entry role. A bare folder with none of that still works via `--clips-dir`,
which synthesizes roles, a default door-line and full-frame zones on the fly.

**Q2. Conversion shows 0% on the sample clip — is that a bug?**
No, and that's the point. The POS has a purchase lull (19:54→20:25 IST), so no transaction
falls in the 5-minute window of the billing visits in this 2.5-min clip. The system refuses
to attribute false conversions; on a window with in-window purchases it reports non-zero.
Integrity over a flattering number.

**Q3. Your raw ENTRY line-crossings under-count. Why trust the funnel?**
Because footfall is **not** the raw ENTRY count. Glare/occlusion at the glass door fragments
tracks, so the funnel denominator is **multi-camera unique visitors** (distinct Re-ID
identities), which is robust to a noisy door line. The corrected vertical door-line still
fires ENTRY/EXIT, but counting doesn't depend on it.

**Q4. How do you avoid double-counting the same person (re-entry / two cameras)?**
A store-level **Re-ID gallery** assigns one `visitor_id` per identity. It's 3-tier and
degrades gracefully: OSNet (torchreid) → torchvision ResNet18 embedding → HSV histogram. On
the real footage the CNN tier roughly halved duplicate ids (46→23) by merging the same
shopper across the entry and floor cameras. Staff are excluded by a back-of-house camera
oracle that the gallery propagates to every camera.

**Q5. Does this scale to 40 live stores? What breaks first?**
The API is **compute-on-read** (raw events in Postgres, metrics computed per request) — every
endpoint is always fresh and a new metric is a pure function, no migration. The honest limit:
it's an O(events-in-window) scan per request, so `/metrics`/`/funnel` latency under concurrent
load breaks first. Migration path: materialised per-minute rollups keyed by
`(store, minute, zone)` + a hot cache, keeping the same endpoint contracts.

**Q6. Why one event schema with a `metadata` blob instead of a table per type?**
The most common query is "reconstruct a visitor's session in time order across event types" —
table-per-type makes that a multi-way union and forks ingestion. One typed event (shared
top-level fields + small `metadata`) serialises to exactly the brief's JSON, validates
pipeline output and API input in one Pydantic model, and encodes the cross-field rules
(threshold events carry no zone; zone events must; queue joins set `queue_depth`).

**Q7. What did the AI get wrong / where did you override it?**
Three places (see CHOICES.md): it treated Re-ID as optional (I made it the centre of
`visitor_id`), it wanted a table-per-event schema (I chose one typed event), and it pushed VLM
auto-classification as the *primary* camera resolver (I demoted it to a last-resort proposal).

---

## Things to have on screen / ready to show
- `docs/DESIGN.md` (architecture + trade-offs) and `docs/CHOICES.md` (decisions).
- The CI run on GitHub (`.github/workflows/ci.yml`): tests **and** a `docker compose up` smoke.
- The requirements-coverage table in `README.md` (every brief requirement → where it lives).
- 44 passing tests, ~90% coverage on `app/`.

## Don't get caught out
- The annotated CCTV clip is **never committed** (footage licence) — show it locally only.
- `ST1008_LIVE` is a separate store so the always-on seeded `ST1008` demo never resets.
- Numbers vary with the input window — they are computed, not hardcoded (integrity cap note).
