"""/events/ingest — batch event ingestion endpoint.

The body is typed as a list of raw dicts (not validated Events) so a single malformed
event yields a per-item error in the response rather than a 422 for the whole batch.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..ingestion import ingest_events
from ..models import IngestResponse

router = APIRouter(prefix="/events", tags=["events"])


class RawIngest(BaseModel):
    # validate only batch-level constraints here; per-event validation happens inside
    # ingest_events to support partial success.
    events: list[dict] = Field(..., min_length=1, max_length=500)


@router.post("/ingest", response_model=IngestResponse)
async def ingest(
    payload: RawIngest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> IngestResponse:
    request.state.event_count = len(payload.events)
    return await ingest_events(session, payload.events)
