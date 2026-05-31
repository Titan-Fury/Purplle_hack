"""Lightweight in-process observability counters.

Aggregates request volume, per-endpoint latency, error rate and ingested-event totals so
an on-call engineer can see service health at a glance via GET /stats — complementing the
structured access logs (trace_id/latency) and the business /health endpoint.

Single-process and asyncio single-threaded, so plain dict mutation is safe (no locks).
At fleet scale these counters would be exported to Prometheus/StatsD instead.
"""
from __future__ import annotations

import time
from collections import defaultdict

_START = time.time()
_requests_total = 0
_events_ingested_total = 0
_by_status: dict[str, int] = defaultdict(int)
_by_endpoint: dict[str, dict] = defaultdict(lambda: {"count": 0, "latency_ms_sum": 0.0, "errors": 0})


def record(endpoint: str, status_code: int, latency_ms: float, event_count: int | None = None) -> None:
    global _requests_total, _events_ingested_total
    _requests_total += 1
    _by_status[f"{status_code // 100}xx"] += 1
    e = _by_endpoint[endpoint]
    e["count"] += 1
    e["latency_ms_sum"] += latency_ms
    if status_code >= 500:
        e["errors"] += 1
    if event_count:
        _events_ingested_total += event_count


def snapshot() -> dict:
    endpoints = {
        k: {
            "count": v["count"],
            "avg_latency_ms": round(v["latency_ms_sum"] / v["count"], 2) if v["count"] else 0.0,
            "errors": v["errors"],
        }
        for k, v in _by_endpoint.items()
    }
    return {
        "uptime_seconds": round(time.time() - _START, 1),
        "requests_total": _requests_total,
        "events_ingested_total": _events_ingested_total,
        "requests_by_status": dict(_by_status),
        "endpoints": endpoints,
    }
