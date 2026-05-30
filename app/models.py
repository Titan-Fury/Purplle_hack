"""Shared event schema — the single contract between the detection pipeline and the API.

This mirrors the schema in the problem statement (Part A, "Required Output Schema").
Both the pipeline (`pipeline/emit.py`) and the API ingest path validate against these
models, so a malformed event is rejected in exactly one place.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EventType(str, Enum):
    """The 8 behavioural event types from the catalogue."""

    ENTRY = "ENTRY"
    EXIT = "EXIT"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    REENTRY = "REENTRY"


# Event types that occur at the entry threshold and therefore carry no zone.
_THRESHOLD_TYPES = {EventType.ENTRY, EventType.EXIT, EventType.REENTRY}
# Event types that require a named zone.
_ZONE_TYPES = {
    EventType.ZONE_ENTER,
    EventType.ZONE_EXIT,
    EventType.ZONE_DWELL,
    EventType.BILLING_QUEUE_JOIN,
    EventType.BILLING_QUEUE_ABANDON,
}


class EventMetadata(BaseModel):
    """Nested metadata block. Unknown keys are ignored, not rejected."""

    model_config = ConfigDict(extra="ignore")

    queue_depth: Optional[int] = Field(
        default=None, ge=0, description="Integer; populated for BILLING_QUEUE_JOIN"
    )
    sku_zone: Optional[str] = Field(
        default=None, description="Zone label from store_layout.json"
    )
    session_seq: int = Field(
        default=0, ge=0, description="Ordinal position of this event in the visitor session"
    )


class Event(BaseModel):
    """A single behavioural event emitted by the detection pipeline."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    event_id: str = Field(..., description="UUID v4 — must be globally unique")
    store_id: str = Field(..., min_length=1)
    camera_id: str = Field(..., min_length=1)
    visitor_id: str = Field(..., min_length=1, description="Re-ID token, unique per visit session")
    event_type: EventType
    timestamp: datetime = Field(..., description="ISO-8601 UTC")
    zone_id: Optional[str] = Field(default=None, description="null for ENTRY/EXIT/REENTRY")
    dwell_ms: int = Field(default=0, ge=0, description="duration; 0 for instantaneous events")
    is_staff: bool = False
    confidence: float = Field(..., ge=0.0, le=1.0)
    metadata: EventMetadata = Field(default_factory=EventMetadata)

    # ---- field-level normalisation ----
    @field_validator("event_id")
    @classmethod
    def _event_id_is_uuid(cls, v: str) -> str:
        # We accept any UUID version string but require it be a valid UUID so
        # idempotency keys are well-formed.
        UUID(v)
        return v

    @field_validator("timestamp")
    @classmethod
    def _to_utc(cls, v: datetime) -> datetime:
        # Naive timestamps are assumed UTC; aware timestamps are converted to UTC.
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    # ---- cross-field consistency ----
    @model_validator(mode="after")
    def _zone_consistency(self) -> "Event":
        et = EventType(self.event_type)
        if et in _ZONE_TYPES and not self.zone_id:
            raise ValueError(f"{et.value} requires a non-null zone_id")
        if et in _THRESHOLD_TYPES and self.zone_id is not None:
            # Threshold events must not carry a zone; normalise rather than reject.
            self.zone_id = None
        if et is EventType.BILLING_QUEUE_JOIN and self.metadata.queue_depth is None:
            raise ValueError("BILLING_QUEUE_JOIN requires metadata.queue_depth")
        return self


class IngestRequest(BaseModel):
    """Batch ingest payload — up to 500 events per call."""

    events: list[Event] = Field(..., min_length=1, max_length=500)


class IngestItemError(BaseModel):
    """Per-item failure detail for partial-success responses."""

    index: int
    event_id: Optional[str] = None
    error: str


class IngestResponse(BaseModel):
    """Structured ingest result supporting partial success + idempotency."""

    accepted: int
    duplicates: int
    rejected: int
    errors: list[IngestItemError] = Field(default_factory=list)
