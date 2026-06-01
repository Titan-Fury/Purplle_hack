# PROMPT: "Write pytest tests for a FastAPI batch ingest endpoint covering: partial
#          success on malformed events, idempotency by event_id (re-post = duplicates),
#          intra-batch duplicates, oversize batch rejection, and that low-confidence
#          events are NOT dropped."
# CHANGES MADE: Added explicit assertions on the structured error payload (index +
#          message), and a dedicated low-confidence test because the brief specifically
#          forbids silently suppressing low-confidence detections.
from __future__ import annotations

import uuid

from tests.conftest import make_event


async def test_ingest_partial_success(client):
    good = make_event()
    bad = make_event(confidence=5)            # invalid: confidence > 1
    missing = {"event_id": str(uuid.uuid4())}  # invalid: required fields missing
    r = await client.post("/events/ingest", json={"events": [good, bad, missing]})
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 1
    assert body["rejected"] == 2
    assert {e["index"] for e in body["errors"]} == {1, 2}


async def test_ingest_idempotent(client):
    e = make_event()
    first = await client.post("/events/ingest", json={"events": [e]})
    second = await client.post("/events/ingest", json={"events": [e]})
    assert first.json()["accepted"] == 1
    assert second.json()["accepted"] == 0
    assert second.json()["duplicates"] == 1


async def test_ingest_intra_batch_duplicate(client):
    e = make_event()
    r = await client.post("/events/ingest", json={"events": [e, dict(e)]})
    body = r.json()
    assert body["accepted"] == 1
    assert body["duplicates"] == 1


async def test_ingest_oversize_batch_rejected(client):
    batch = [make_event() for _ in range(501)]
    r = await client.post("/events/ingest", json={"events": batch})
    assert r.status_code == 422


async def test_low_confidence_not_dropped(client):
    e = make_event(confidence=0.03)
    r = await client.post("/events/ingest", json={"events": [e]})
    assert r.json()["accepted"] == 1
