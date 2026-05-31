"""Simulated real-time replay — proves the pipeline -> API -> dashboard are genuinely
connected (Part E), not batch-processed.

Reads the committed event file, remaps it onto the ST1008_LIVE demo store with fresh
event_ids, and POSTs the events into the running API in clip-time order. The dashboard
(pointed at ST1008_LIVE) then shows visitors / funnel / queue climbing live.

Usage:
    # 1) start the API (any store data is fine; the live store is separate)
    # 2) run the replay against it
    python -m pipeline.replay --api http://localhost:8000 --speed 20

Flags:
    --api     base URL of the running API            (default http://localhost:8000)
    --src     event file to replay                   (default data/seed_events_ST1008.jsonl)
    --store   target store id (must end in _LIVE)     (default ST1008_LIVE)
    --speed   real-time multiplier (20 = 20x faster)  (default 20.0)
    --no-reset  do not clear the live store first
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime

import requests


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Replay events into the API in simulated real time.")
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--src", default="data/seed_events_ST1008.jsonl")
    ap.add_argument("--store", default="ST1008_LIVE")
    ap.add_argument("--speed", type=float, default=20.0)
    ap.add_argument("--no-reset", action="store_true")
    args = ap.parse_args()

    if not args.store.endswith("_LIVE"):
        print("refusing: --store must end in _LIVE (the /reset endpoint is gated to those).")
        return 2

    with open(args.src, "r", encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]
    events.sort(key=lambda e: e["timestamp"])
    if not events:
        print("no events to replay")
        return 1

    api = args.api.rstrip("/")
    if not args.no_reset:
        try:
            r = requests.post(f"{api}/stores/{args.store}/reset", timeout=10)
            print(f"reset {args.store}: {r.json()}")
        except Exception as exc:  # noqa: BLE001
            print(f"warning: reset failed ({exc}); continuing")

    speed = max(0.1, args.speed)
    print(f"replaying {len(events)} events -> {args.store} at {speed}x "
          f"(dashboard: {api}/dashboard)")

    prev = _parse_ts(events[0]["timestamp"])
    sent = 0
    for e in events:
        now = _parse_ts(e["timestamp"])
        gap = (now - prev).total_seconds() / speed
        if gap > 0:
            time.sleep(min(gap, 5.0))  # cap long empty-store gaps so the demo keeps moving
        prev = now

        e = dict(e)
        e["store_id"] = args.store
        e["event_id"] = str(uuid.uuid4())  # fresh id so re-runs never collide on dedup

        try:
            resp = requests.post(f"{api}/events/ingest", json={"events": [e]}, timeout=10)
            sent += resp.json().get("accepted", 0)
        except Exception as exc:  # noqa: BLE001
            print(f"\ningest error: {exc}")
            return 1
        print(f"\r  sent {sent}/{len(events)}  [{e['event_type']:<20}] "
              f"{e.get('zone_id') or '-':<14}", end="", flush=True)

    print(f"\ndone — {sent} events streamed into {args.store}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
