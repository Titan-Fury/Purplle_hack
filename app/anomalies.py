"""Operational anomaly detection.

Three anomaly classes from the brief, each with INFO/WARN/CRITICAL severity and a
suggested_action an operator can act on immediately:
  * BILLING_QUEUE_SPIKE   — queue_depth exceeds threshold
  * CONVERSION_DROP       — today's conversion rate below the trailing 7-day average
  * DEAD_ZONE             — a zone with no visits for > dead_zone_threshold_s

``compute_anomalies`` is a thin orchestrator; each detector is a small, independently
readable function returning zero or more anomaly dicts.
"""
from __future__ import annotations

from datetime import timedelta

from .analytics import _aware, _fetch, compute_metrics, resolve_window
from .config import get_settings
from .layout import get_store_meta


def _sev_for_queue(depth: int, threshold: int) -> str:
    if depth >= 2 * threshold:
        return "CRITICAL"
    if depth >= threshold:
        return "WARN"
    return "INFO"


def _queue_spike(events, settings) -> list[dict]:
    """Peak billing-queue depth in the window vs the spike threshold."""
    depths = [
        e.queue_depth for e in events
        if e.event_type == "BILLING_QUEUE_JOIN" and e.queue_depth is not None
    ]
    if not depths:
        return []
    peak, thr = max(depths), settings.queue_spike_threshold
    if peak < thr:
        return []
    return [{
        "type": "BILLING_QUEUE_SPIKE",
        "severity": _sev_for_queue(peak, thr),
        "metric": {"peak_queue_depth": peak, "threshold": thr},
        "suggested_action": "Open an additional billing counter and redeploy floor staff to the queue.",
    }]


async def _conversion_drop(session, store_id, start, today_cr, settings) -> list[dict]:
    """Today's conversion rate vs the trailing N-day average (days with traffic only)."""
    prior = []
    for i in range(1, settings.conversion_drop_lookback_days + 1):
        d = (start - timedelta(days=i)).date().isoformat()
        m = await compute_metrics(session, store_id, d)
        if m["unique_visitors"] > 0:
            prior.append(m["conversion_rate"])
    if not prior:
        return []
    avg = sum(prior) / len(prior)
    if avg <= 0 or today_cr >= avg:
        return []
    drop = (avg - today_cr) / avg
    if drop < 0.2:                       # below WARN — not worth surfacing
        return []
    return [{
        "type": "CONVERSION_DROP",
        "severity": "CRITICAL" if drop >= 0.4 else "WARN",
        "metric": {
            "today": round(today_cr, 4),
            "avg_7d": round(avg, 4),
            "drop_pct": round(drop * 100, 1),
        },
        "suggested_action": "Check till/staffing and recent layout changes; verify camera feeds are not stale.",
    }]


def _dead_zones(events, meta, now_ref, settings) -> list[dict]:
    """Layout zones with no ZONE_ENTER/DWELL for longer than the dead-zone threshold."""
    if not events:                       # zero-traffic window: no zone is "dead", it's just quiet
        return []
    last_activity: dict[str, object] = {}
    for e in events:
        if e.zone_id and e.event_type in ("ZONE_ENTER", "ZONE_DWELL"):
            ts = _aware(e.timestamp)
            if e.zone_id not in last_activity or ts > last_activity[e.zone_id]:
                last_activity[e.zone_id] = ts
    zone_ids = meta.zone_ids if meta else list(last_activity.keys())
    out = []
    for z in zone_ids:
        last = last_activity.get(z)
        lag = (now_ref - last).total_seconds() if last else None
        if last is None or lag > settings.dead_zone_threshold_s:
            out.append({
                "type": "DEAD_ZONE",
                "severity": "WARN",
                "metric": {"zone_id": z, "idle_seconds": round(lag) if lag else None},
                "suggested_action": f"Inspect zone '{z}': possible blocked aisle, missing stock, or camera outage.",
            })
    return out


async def compute_anomalies(session, store_id, date_str=None, *, window=None, events=None) -> dict:
    settings = get_settings()
    start, end = window or await resolve_window(session, store_id, date_str)
    if events is None:
        events = await _fetch(session, store_id, start, end)
    meta = get_store_meta(store_id)
    # reference "now" = latest event in window (works for live and replayed data)
    now_ref = max((_aware(e.timestamp) for e in events), default=end)
    today = await compute_metrics(session, store_id, date_str, window=(start, end), events=events)

    anomalies = (
        _queue_spike(events, settings)
        + await _conversion_drop(session, store_id, start, today["conversion_rate"], settings)
        + _dead_zones(events, meta, now_ref, settings)
    )
    return {
        "store_id": store_id,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
    }
