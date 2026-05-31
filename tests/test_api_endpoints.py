# PROMPT: "Write pytest tests for three new FastAPI endpoints on an async SQLAlchemy app:
#          GET /stores/{id}/events (recent events, newest first), GET /stores/{id}/layout
#          (zone polygons), and POST /stores/{id}/reset (must be gated so only *_LIVE demo
#          stores can be cleared). Use the existing client fixture and make_event helper."
# CHANGES MADE: Asserted the ticker is ordered newest-first, that layout returns non-empty
#          polygons, and that /reset returns 403 for a real store but deletes for *_LIVE
#          (verified by a follow-up metrics call showing zero visitors).
from __future__ import annotations

import pytest

from .conftest import STORE, make_event

pytestmark = pytest.mark.asyncio


async def _ingest(client, events):
    r = await client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200
    return r.json()


async def test_recent_events_newest_first(client):
    await _ingest(client, [
        make_event("ENTRY", "VIS_A", ts="2026-03-03T14:30:00Z"),
        make_event("ZONE_ENTER", "VIS_A", "MAKEUP", ts="2026-03-03T14:31:00Z"),
        make_event("EXIT", "VIS_A", ts="2026-03-03T14:35:00Z"),
    ])
    r = await client.get(f"/stores/{STORE}/events?limit=10")
    assert r.status_code == 200
    evs = r.json()["events"]
    assert len(evs) == 3
    assert evs[0]["event_type"] == "EXIT"          # newest first
    assert evs[-1]["event_type"] == "ENTRY"
    assert evs[0]["timestamp"] >= evs[1]["timestamp"]


async def test_events_limit_clamped(client):
    await _ingest(client, [make_event("ENTRY", f"VIS_{i}") for i in range(5)])
    r = await client.get(f"/stores/{STORE}/events?limit=2")
    assert len(r.json()["events"]) == 2


async def test_layout_returns_polygons(client):
    r = await client.get(f"/stores/{STORE}/layout")
    assert r.status_code == 200
    zones = r.json()["zones"]
    assert zones and all("polygon" in z for z in zones)
    assert any(z["is_billing"] for z in zones)


async def test_layout_unknown_store_404(client):
    r = await client.get("/stores/NOPE/layout")
    assert r.status_code == 404


async def test_reset_rejected_for_real_store(client):
    await _ingest(client, [make_event("ENTRY", "VIS_A")])
    r = await client.post(f"/stores/{STORE}/reset")
    assert r.status_code == 403  # production data can never be wiped
    # data still present
    m = await client.get(f"/stores/{STORE}/metrics?date=2026-03-03")
    assert m.json()["unique_visitors"] == 1


async def test_reset_allowed_for_live_store(client):
    live = "ST1008_LIVE"
    await _ingest(client, [
        make_event("ENTRY", "VIS_A", ts="2026-03-03T14:30:00Z", store_id=live),
    ])
    r = await client.post(f"/stores/{live}/reset")
    assert r.status_code == 200
    assert r.json()["deleted"] >= 1
    m = await client.get(f"/stores/{live}/metrics?date=2026-03-03")
    assert m.json()["unique_visitors"] == 0


async def test_summary_batches_all_sections(client):
    await _ingest(client, [
        make_event("ENTRY", "VIS_A", ts="2026-03-03T14:30:00Z"),
        make_event("ZONE_ENTER", "VIS_A", "MAKEUP", ts="2026-03-03T14:31:00Z"),
    ])
    r = await client.get(f"/stores/{STORE}/summary?date=2026-03-03")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"metrics", "funnel", "heatmap", "anomalies", "events"}
    assert body["metrics"]["unique_visitors"] == 1
    assert body["funnel"]["stages"][0]["count"] == 1
    assert len(body["events"]) == 2


async def test_stats_counts_requests(client):
    await client.get(f"/stores/{STORE}/metrics?date=2026-03-03")
    r = await client.get("/stats")
    assert r.status_code == 200
    s = r.json()
    assert s["requests_total"] >= 1
    assert "endpoints" in s and "requests_by_status" in s
    assert s["uptime_seconds"] >= 0
