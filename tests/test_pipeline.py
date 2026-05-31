# PROMPT: "Write pytest tests for the detection layer without running YOLO: drive the
#          StoreTracker with synthetic normalised centroids to assert group entry emits
#          N ENTRYs, billing queue_depth is set, and zone dwell fires; plus Event schema
#          validation (uuid, confidence bounds, zone/threshold consistency)."
# CHANGES MADE: Used crop=None (no Re-ID embedding) so identities stay deterministic per
#          track, and asserted on event_type counts rather than exact visitor_ids. Added
#          schema tests that pin the zone-consistency rules from the problem statement.
from __future__ import annotations

import uuid
from collections import Counter

import pytest

from app.models import Event
from pipeline.reid import ReIDGallery
from pipeline.tracker import StoreTracker
from pipeline.zones import load_layout

LAYOUT = load_layout("data/store_layout.json")["STORE_BLR_002"]


def _run(steps):
    events = []
    tr = StoreTracker(LAYOUT, ReIDGallery(), events.append)
    for cam, tid, cx, cy, ts in steps:
        tr.update(cam, tid, cx, cy, 0.9, None, ts)
    tr.flush_lost(1e9)
    return events, Counter(e["event_type"] for e in events)


def test_group_entry_counts_individuals():
    steps = []
    for tid in (1, 2, 3):  # three people cross the entry line together (clear of hysteresis)
        steps.append(("CAM_ENTRY_01", tid, 0.5, 0.40, 0.0))
        steps.append(("CAM_ENTRY_01", tid, 0.5, 0.70, 0.5))
    _, counts = _run(steps)
    assert counts["ENTRY"] == 3  # individuals, not one group


def test_billing_queue_depth_set():
    steps = [
        ("CAM_BILLING_01", 10, 0.5, 0.5, 0.0),   # first occupant, depth 0 -> no JOIN
        ("CAM_BILLING_01", 11, 0.5, 0.5, 1.0),   # second, depth 1 -> JOIN
    ]
    events, counts = _run(steps)
    joins = [e for e in events if e["event_type"] == "BILLING_QUEUE_JOIN"]
    assert counts["BILLING_QUEUE_JOIN"] == 1
    assert joins[0]["queue_depth"] == 1


def test_billing_abandon_only_after_real_queue_join():
    # A lone shopper enters billing then leaves: never queued behind anyone, so the
    # pipeline must emit neither JOIN nor ABANDON (ABANDON is symmetric with JOIN).
    steps = [
        ("CAM_BILLING_01", 20, 0.5, 0.5, 0.0),    # alone in billing (depth 0)
        ("CAM_FLOOR_01", 20, 0.25, 0.5, 2.0),     # walked off to the floor -> left billing
    ]
    _, counts = _run(steps)
    assert counts["BILLING_QUEUE_JOIN"] == 0
    assert counts["BILLING_QUEUE_ABANDON"] == 0

    # Now a real queue: #31 waits behind #30, then #31 leaves without buying -> one abandon.
    steps = [
        ("CAM_BILLING_01", 30, 0.5, 0.5, 0.0),    # first (depth 0, no join)
        ("CAM_BILLING_01", 31, 0.5, 0.5, 1.0),    # second (depth 1, JOIN, queued)
        ("CAM_FLOOR_01", 31, 0.25, 0.5, 2.0),     # second leaves billing
    ]
    _, counts = _run(steps)
    assert counts["BILLING_QUEUE_JOIN"] == 1
    assert counts["BILLING_QUEUE_ABANDON"] == 1


def test_zone_dwell_emitted_after_threshold():
    steps = [
        ("CAM_FLOOR_01", 5, 0.25, 0.5, 0.0),    # enter SKINCARE
        ("CAM_FLOOR_01", 5, 0.25, 0.5, 35.0),   # still there 35s later -> DWELL
    ]
    _, counts = _run(steps)
    assert counts["ZONE_ENTER"] >= 1
    assert counts["ZONE_DWELL"] >= 1


def _base(**o):
    d = dict(event_id=str(uuid.uuid4()), store_id="S", camera_id="C", visitor_id="V",
             event_type="ENTRY", timestamp="2026-03-03T14:00:00Z", confidence=0.5)
    d.update(o)
    return d


def test_schema_accepts_valid_and_normalises_threshold_zone():
    e = Event.model_validate(_base(zone_id="X"))  # ENTRY must not carry a zone
    assert e.zone_id is None


def test_schema_rejects_bad_confidence_and_uuid():
    with pytest.raises(Exception):
        Event.model_validate(_base(confidence=1.5))
    with pytest.raises(Exception):
        Event.model_validate(_base(event_id="not-a-uuid"))


def test_schema_requires_zone_for_zone_events():
    with pytest.raises(Exception):
        Event.model_validate(_base(event_type="ZONE_ENTER"))
    with pytest.raises(Exception):
        Event.model_validate(_base(event_type="BILLING_QUEUE_JOIN", zone_id="BILLING"))
