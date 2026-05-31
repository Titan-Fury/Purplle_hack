"""/stores/{id}/* — metrics, funnel, heatmap analytics endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..analytics import (
    _fetch,
    compute_funnel,
    compute_heatmap,
    compute_metrics,
    recent_events,
    resolve_window,
)
from ..anomalies import compute_anomalies
from ..db import delete_store_events, get_session
from ..layout import get_store_meta, known_store_ids

router = APIRouter(prefix="/stores", tags=["analytics"])


def _check_store(store_id: str) -> None:
    # Unknown store with no layout entry -> 404 (but still serve if events exist).
    if get_store_meta(store_id) is None and store_id not in known_store_ids():
        # allow stores not in layout (events may still exist); only block clearly-empty ids
        if not store_id:
            raise HTTPException(status_code=404, detail="unknown store")


@router.get("/{store_id}/metrics")
async def metrics(
    store_id: str,
    date: str | None = Query(default=None, description="YYYY-MM-DD; defaults to latest event day"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    _check_store(store_id)
    return await compute_metrics(session, store_id, date)


@router.get("/{store_id}/funnel")
async def funnel(
    store_id: str,
    date: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    _check_store(store_id)
    return await compute_funnel(session, store_id, date)


@router.get("/{store_id}/heatmap")
async def heatmap(
    store_id: str,
    date: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    _check_store(store_id)
    return await compute_heatmap(session, store_id, date)


@router.get("/{store_id}/anomalies")
async def anomalies(
    store_id: str,
    date: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    _check_store(store_id)
    return await compute_anomalies(session, store_id, date)


@router.get("/{store_id}/summary")
async def summary(
    store_id: str,
    date: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """One batched read powering the live dashboard — metrics + funnel + heatmap +
    anomalies + recent events in a single round-trip (vs. five separate polls).

    The day-window and event set are resolved/fetched once and shared across all four
    analytics functions, instead of each re-resolving and re-scanning independently."""
    _check_store(store_id)
    window = await resolve_window(session, store_id, date)
    events = await _fetch(session, store_id, *window)
    return {
        "metrics": await compute_metrics(session, store_id, date, window=window, events=events),
        "funnel": await compute_funnel(session, store_id, date, window=window, events=events),
        "heatmap": await compute_heatmap(session, store_id, date, window=window, events=events),
        "anomalies": await compute_anomalies(session, store_id, date, window=window, events=events),
        "events": (await recent_events(session, store_id, 25))["events"],
    }


@router.get("/{store_id}/events")
async def events(
    store_id: str,
    limit: int = Query(default=30, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Recent events (newest first) for the live dashboard ticker."""
    _check_store(store_id)
    return await recent_events(session, store_id, limit)


@router.get("/{store_id}/layout")
async def layout(store_id: str) -> dict:
    """Zone polygons + billing flags for rendering the store-map heatmap."""
    meta = get_store_meta(store_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="no layout for store")
    return {
        "store_id": store_id,
        "zones": [
            {
                "zone_id": z["zone_id"],
                "polygon": z.get("polygon", []),
                "is_billing": bool(z.get("is_billing")),
                "is_staff_area": bool(z.get("is_staff_area")),
                "sku_zone": z.get("sku_zone"),
            }
            for z in meta.zones
        ],
    }


@router.post("/{store_id}/reset")
async def reset(store_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    """Clear a live-demo store's events so the replay can start clean.
    Gated to *_LIVE store ids so production data can never be deleted."""
    if not store_id.endswith("_LIVE"):
        raise HTTPException(status_code=403, detail="reset is only allowed for *_LIVE demo stores")
    deleted = await delete_store_events(session, store_id)
    return {"store_id": store_id, "deleted": deleted}
