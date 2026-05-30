# PROMPT: "Write pytest tests for the anomalies endpoint (queue spike severity, dead
#          zone), the /health stale-feed warning, an all-staff clip, and graceful HTTP
#          503 when the database raises SQLAlchemyError (no stack trace in the body)."
# CHANGES MADE: Forced the 503 path by overriding the get_session dependency to raise
#          OperationalError, and asserted the response body contains no traceback/driver
#          strings. Added an all-staff test since the brief lists it as a required edge case.
from __future__ import annotations

from sqlalchemy.exc import OperationalError

from app.db import get_session
from app.main import app
from tests.conftest import STORE, make_event

DATE = "?date=2026-03-03"


async def test_queue_spike_critical(client):
    await client.post("/events/ingest", json={"events": [
        make_event("BILLING_QUEUE_JOIN", "VIS_A", "BILLING", queue_depth=12),
    ]})
    a = (await client.get(f"/stores/{STORE}/anomalies{DATE}")).json()
    spike = [x for x in a["anomalies"] if x["type"] == "BILLING_QUEUE_SPIKE"]
    assert spike and spike[0]["severity"] == "CRITICAL"
    assert "suggested_action" in spike[0]


async def test_dead_zone_detected(client):
    await client.post("/events/ingest", json={"events": [
        make_event("ZONE_ENTER", "VIS_A", "SKINCARE"),
    ]})
    a = (await client.get(f"/stores/{STORE}/anomalies{DATE}")).json()
    dead = [x for x in a["anomalies"] if x["type"] == "DEAD_ZONE"]
    assert len(dead) >= 1  # unvisited zones flagged while store has activity


async def test_all_staff_clip(client):
    await client.post("/events/ingest", json={"events": [
        make_event("ENTRY", "VIS_S1", is_staff=True),
        make_event("ENTRY", "VIS_S2", is_staff=True),
    ]})
    m = (await client.get(f"/stores/{STORE}/metrics{DATE}")).json()
    assert m["unique_visitors"] == 0
    assert m["conversion_rate"] == 0.0


async def test_health_reports_stale_feed(client):
    await client.post("/events/ingest", json={"events": [make_event()]})
    h = (await client.get("/health")).json()
    store = next(s for s in h["stores"] if s["store_id"] == STORE)
    # data is dated in the past, so the feed must be flagged stale
    assert store["feed"] == "STALE_FEED"
    assert h["status"] == "warn"


async def test_graceful_503_on_db_error(client):
    async def boom():
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))
        yield

    app.dependency_overrides[get_session] = boom
    try:
        r = await client.get(f"/stores/{STORE}/metrics")
        assert r.status_code == 503
        assert r.json()["code"] == "DB_UNAVAILABLE"
        assert "Traceback" not in r.text and "OperationalError" not in r.text
    finally:
        app.dependency_overrides.clear()
