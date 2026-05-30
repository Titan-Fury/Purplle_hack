"""/health — the first thing an on-call engineer checks.

Reports DB connectivity and, per store, the last event timestamp and feed lag, raising
a STALE_FEED warning when lag exceeds the threshold (default 10 min).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import EventRow, get_session
from .layout import known_store_ids

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    out: dict = {"status": "ok", "database": "ok", "time": now.isoformat(), "stores": []}

    try:
        rows = (await session.execute(
            select(EventRow.store_id, func.max(EventRow.timestamp)).group_by(EventRow.store_id)
        )).all()
    except SQLAlchemyError:
        return {"status": "degraded", "database": "unavailable", "time": now.isoformat(), "stores": []}

    last_by_store = {sid: ts for sid, ts in rows}
    store_ids = sorted(set(last_by_store) | set(known_store_ids()))
    any_stale = False

    for sid in store_ids:
        last = last_by_store.get(sid)
        if last is None:
            out["stores"].append({"store_id": sid, "last_event": None, "feed": "NO_DATA"})
            continue
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        lag = (now - last).total_seconds()
        stale = lag > settings.stale_feed_threshold_s
        any_stale = any_stale or stale
        out["stores"].append({
            "store_id": sid,
            "last_event": last.isoformat(),
            "lag_seconds": round(lag),
            "feed": "STALE_FEED" if stale else "LIVE",
        })

    if any_stale:
        out["status"] = "warn"
        out["warning"] = "STALE_FEED"
    return out
