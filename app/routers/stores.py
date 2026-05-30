"""/stores/{id}/* — metrics, funnel, heatmap analytics endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..analytics import compute_funnel, compute_heatmap, compute_metrics
from ..db import get_session
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
