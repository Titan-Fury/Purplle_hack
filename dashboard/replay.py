"""Replay a JSONL event stream into the API in (simulated) real time.

Lets you demonstrate the live dashboard without re-running YOLO: it reads events
produced by the pipeline (or the provided samples), orders them by timestamp, and POSTs
them to /events/ingest spaced by their real inter-event gaps divided by --speed.

Usage:
    python -m dashboard.replay --events pipeline/output/events_STORE_BLR_002.jsonl \
        --api-url http://localhost:8000 --speed 60
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime

import requests


def _ts(e: dict) -> float:
    return datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--events", default="data/sample_events.jsonl")
    p.add_argument("--api-url", default="http://localhost:8000")
    p.add_argument("--speed", type=float, default=60.0, help="real-time speed multiplier")
    p.add_argument("--batch", type=int, default=1)
    args = p.parse_args()

    with open(args.events, encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]
    events.sort(key=_ts)
    if not events:
        print("no events to replay")
        return

    url = f"{args.api_url.rstrip('/')}/events/ingest"
    print(f"replaying {len(events)} events -> {url} at {args.speed}x")

    prev = _ts(events[0])
    buf: list[dict] = []
    sent = 0
    for e in events:
        gap = (_ts(e) - prev) / args.speed
        if gap > 0:
            time.sleep(min(gap, 5.0))  # cap waits so demos stay snappy
        prev = _ts(e)
        buf.append(e)
        if len(buf) >= args.batch:
            try:
                requests.post(url, json={"events": buf}, timeout=5)
                sent += len(buf)
                print(f"  sent {sent}/{len(events)}", end="\r")
            except requests.RequestException as ex:
                print("post failed:", ex)
            buf.clear()
    if buf:
        requests.post(url, json={"events": buf}, timeout=5)
        sent += len(buf)
    print(f"\ndone: {sent} events replayed")


if __name__ == "__main__":
    main()
