"""Analytics computations: metrics, funnel, heatmap.

Design notes:
  * The unit of analysis is the **visitor session** (visitor_id). REENTRY reuses the
    same visitor_id, so counting distinct visitor_ids never double-counts a re-entry.
  * Staff (is_staff=true) are excluded from every customer metric.
  * The query window defaults to the calendar day of the store's most recent event
    ("today" in live operation), and accepts an explicit ?date=YYYY-MM-DD override.
    This keeps metrics real-time without being tied to an absolute clock for testing.
  * Events for a window are loaded once and aggregated in Python for clarity. At
    fleet scale this aggregation would move to SQL/materialised rollups (see DESIGN.md).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import EventRow
from .layout import get_store_meta
from .pos import converted_visitors


async def resolve_window(
    session: AsyncSession, store_id: str, date_str: Optional[str] = None
) -> tuple[datetime, datetime]:
    if date_str:
        day = datetime.fromisoformat(date_str).date()
    else:
        latest = (await session.execute(
            select(func.max(EventRow.timestamp)).where(EventRow.store_id == store_id)
        )).scalar_one_or_none()
        day = (latest or datetime.now(timezone.utc)).date()
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


async def _fetch(session, store_id, start, end, include_staff=False) -> list[EventRow]:
    """Fetch events in the window. By default excludes staff at the *visitor* level:
    if any of a visitor's events is is_staff=true, the whole visitor is dropped (more
    correct than per-event filtering, since the staff flag can be set mid-session)."""
    stmt = select(EventRow).where(
        EventRow.store_id == store_id,
        EventRow.timestamp >= start,
        EventRow.timestamp < end,
    )
    rows = list((await session.execute(stmt)).scalars().all())
    if include_staff:
        return rows
    staff_visitors = {e.visitor_id for e in rows if e.is_staff}
    return [e for e in rows if e.visitor_id not in staff_visitors]


async def recent_events(session, store_id, limit: int = 30) -> dict:
    """Most recent events for a store, newest first — powers the live dashboard ticker."""
    stmt = (
        select(EventRow)
        .where(EventRow.store_id == store_id)
        .order_by(EventRow.timestamp.desc(), EventRow.ingested_at.desc())
        .limit(max(1, min(limit, 200)))
    )
    rows = (await session.execute(stmt)).scalars().all()
    return {
        "store_id": store_id,
        "events": [
            {
                "event_type": e.event_type,
                "zone_id": e.zone_id,
                "visitor_id": e.visitor_id,
                "camera_id": e.camera_id,
                "timestamp": _aware(e.timestamp).isoformat(),
                "is_staff": e.is_staff,
                "queue_depth": e.queue_depth,
            }
            for e in rows
        ],
    }


def _billing_presence(events, billing_zones) -> list[tuple[str, datetime]]:
    return [
        (e.visitor_id, _aware(e.timestamp))
        for e in events
        if e.zone_id in billing_zones
    ]


def _aware(dt: datetime) -> datetime:
    """SQLite drops tzinfo on read; treat naive timestamps as UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


async def compute_metrics(session, store_id, date_str=None) -> dict:
    meta = get_store_meta(store_id)
    billing_zones = meta.billing_zone_ids if meta else set()
    start, end = await resolve_window(session, store_id, date_str)
    events = await _fetch(session, store_id, start, end)

    visitors = {e.visitor_id for e in events}
    unique_visitors = len(visitors)

    # dwell per zone: max dwell per (visitor, zone), averaged across visitors
    dwell: dict[str, dict[str, int]] = defaultdict(dict)
    for e in events:
        if e.zone_id and e.dwell_ms:
            cur = dwell[e.zone_id].get(e.visitor_id, 0)
            dwell[e.zone_id][e.visitor_id] = max(cur, e.dwell_ms)
    avg_dwell_per_zone = {
        z: round(sum(v.values()) / len(v)) for z, v in dwell.items() if v
    }

    # queue depth (current = latest known, max = peak)
    qd = [e.queue_depth for e in events if e.queue_depth is not None]
    join_events = sorted(
        [e for e in events if e.event_type == "BILLING_QUEUE_JOIN" and e.queue_depth is not None],
        key=lambda e: e.timestamp,
    )
    queue_depth = {
        "current": join_events[-1].queue_depth if join_events else 0,
        "max": max(qd) if qd else 0,
    }

    # conversion + abandonment
    presence = _billing_presence(events, billing_zones)
    billing_visitors = {v for v, _ in presence}
    converted = converted_visitors(store_id, presence, start, end)
    purchases = len(converted)
    conversion_rate = round(purchases / unique_visitors, 4) if unique_visitors else 0.0

    abandoners = {
        e.visitor_id for e in events if e.event_type == "BILLING_QUEUE_ABANDON"
    } - converted
    abandonment_rate = (
        round(len(abandoners) / len(billing_visitors), 4) if billing_visitors else 0.0
    )

    return {
        "store_id": store_id,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "unique_visitors": unique_visitors,
        "purchases": purchases,
        "conversion_rate": conversion_rate,
        "avg_dwell_per_zone_ms": avg_dwell_per_zone,
        "queue_depth": queue_depth,
        "abandonment_rate": abandonment_rate,
        "staff_excluded": True,
    }


async def compute_funnel(session, store_id, date_str=None) -> dict:
    meta = get_store_meta(store_id)
    billing_zones = meta.billing_zone_ids if meta else set()
    start, end = await resolve_window(session, store_id, date_str)
    events = await _fetch(session, store_id, start, end)

    all_visitors = {e.visitor_id for e in events}
    zone_visitors = {
        e.visitor_id for e in events
        if e.event_type in ("ZONE_ENTER", "ZONE_DWELL") and e.zone_id not in billing_zones
    }
    presence = _billing_presence(events, billing_zones)   # compute once, reuse
    billing_visitors = {v for v, _ in presence}
    converted = converted_visitors(store_id, presence, start, end)

    stages = [
        ("ENTRY", len(all_visitors)),
        ("ZONE_VISIT", len(zone_visitors)),
        ("BILLING_QUEUE", len(billing_visitors)),
        ("PURCHASE", len(converted)),
    ]
    out = []
    for i, (name, count) in enumerate(stages):
        prev = stages[i - 1][1] if i else count
        drop = round((1 - count / prev) * 100, 2) if prev else 0.0
        out.append({"stage": name, "count": count, "drop_off_pct": max(0.0, drop)})

    return {
        "store_id": store_id,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "stages": out,
    }


async def compute_heatmap(session, store_id, date_str=None) -> dict:
    meta = get_store_meta(store_id)
    start, end = await resolve_window(session, store_id, date_str)
    events = await _fetch(session, store_id, start, end)

    visits: dict[str, set] = defaultdict(set)
    dwell: dict[str, dict[str, int]] = defaultdict(dict)
    for e in events:
        if not e.zone_id:
            continue
        if e.event_type == "ZONE_ENTER":
            visits[e.zone_id].add(e.visitor_id)
        if e.dwell_ms:
            dwell[e.zone_id][e.visitor_id] = max(dwell[e.zone_id].get(e.visitor_id, 0), e.dwell_ms)

    freq = {z: len(v) for z, v in visits.items()}
    avg_dwell = {z: round(sum(d.values()) / len(d)) for z, d in dwell.items() if d}
    max_freq = max(freq.values()) if freq else 0
    max_dwell = max(avg_dwell.values()) if avg_dwell else 0

    zone_ids = meta.zone_ids if meta else sorted(set(freq) | set(avg_dwell))
    zones = []
    for z in zone_ids:
        f = freq.get(z, 0)
        d = avg_dwell.get(z, 0)
        zones.append({
            "zone_id": z,
            "sku_zone": meta.sku_by_zone.get(z) if meta else None,
            "raw_visits": f,
            "avg_dwell_ms": d,
            "visit_frequency_norm": round(100 * f / max_freq, 1) if max_freq else 0.0,
            "avg_dwell_norm": round(100 * d / max_dwell, 1) if max_dwell else 0.0,
        })

    session_count = len({e.visitor_id for e in events})
    min_sessions = get_settings().min_sessions_for_confidence
    return {
        "store_id": store_id,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "session_count": session_count,
        "data_confidence": "LOW" if session_count < min_sessions else "OK",
        "zones": zones,
    }
