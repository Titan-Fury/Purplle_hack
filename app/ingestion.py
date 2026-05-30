"""Event ingestion: validate, deduplicate, store.

Designed for *partial success*: each event is validated individually, so one malformed
event never rejects the whole batch. Idempotency is by event_id — re-posting the same
payload inserts nothing new and reports the rest as duplicates (HTTP stays 2xx).
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import EventRow
from .models import Event, IngestItemError, IngestResponse


def _to_row(e: Event) -> EventRow:
    return EventRow(
        event_id=e.event_id,
        store_id=e.store_id,
        camera_id=e.camera_id,
        visitor_id=e.visitor_id,
        event_type=e.event_type if isinstance(e.event_type, str) else e.event_type.value,
        timestamp=e.timestamp,
        zone_id=e.zone_id,
        dwell_ms=e.dwell_ms,
        is_staff=e.is_staff,
        confidence=e.confidence,
        queue_depth=e.metadata.queue_depth,
        sku_zone=e.metadata.sku_zone,
        session_seq=e.metadata.session_seq,
        ingested_at=datetime.now(timezone.utc),
    )


async def ingest_events(session: AsyncSession, raw_events: list[dict]) -> IngestResponse:
    errors: list[IngestItemError] = []
    valid: dict[str, Event] = {}  # event_id -> event (last wins within batch)
    intra_batch_dups = 0

    # 1) validate each item independently
    for idx, raw in enumerate(raw_events):
        try:
            event = Event.model_validate(raw)
        except ValidationError as exc:
            errors.append(IngestItemError(
                index=idx,
                event_id=(raw or {}).get("event_id") if isinstance(raw, dict) else None,
                error="; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()),
            ))
            continue
        if event.event_id in valid:
            intra_batch_dups += 1
        valid[event.event_id] = event

    duplicates = intra_batch_dups
    accepted = 0

    if valid:
        # 2) find which event_ids already exist (idempotency)
        existing = set(
            (await session.execute(
                select(EventRow.event_id).where(EventRow.event_id.in_(list(valid.keys())))
            )).scalars().all()
        )
        new_events = [e for eid, e in valid.items() if eid not in existing]
        duplicates += len(existing)

        # 3) insert the genuinely new events
        if new_events:
            session.add_all([_to_row(e) for e in new_events])
            await session.commit()
            accepted = len(new_events)

    return IngestResponse(
        accepted=accepted,
        duplicates=duplicates,
        rejected=len(errors),
        errors=errors,
    )
