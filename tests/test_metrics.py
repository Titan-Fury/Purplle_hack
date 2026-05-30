# PROMPT: "Write pytest tests for /metrics, /funnel and /heatmap covering edge cases:
#          staff excluded from counts, zero-purchase store, completely empty store,
#          re-entry not double-counted in the funnel, and the heatmap LOW data_confidence
#          flag under 20 sessions."
# CHANGES MADE: Added a POS-correlated conversion test using a billing event timed just
#          before a real transaction in data/pos_transactions.csv, and asserted the
#          funnel ENTRY stage counts distinct visitors (so REENTRY collapses to one).
from __future__ import annotations

from tests.conftest import STORE, make_event

DATE = "?date=2026-03-03"


async def _ingest(client, events):
    r = await client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200
    return r.json()


async def test_staff_excluded_from_metrics(client):
    await _ingest(client, [
        make_event("ENTRY", "VIS_C"),
        make_event("ENTRY", "VIS_STAFF", is_staff=True),
    ])
    m = (await client.get(f"/stores/{STORE}/metrics{DATE}")).json()
    assert m["unique_visitors"] == 1
    assert m["staff_excluded"] is True


async def test_staff_excluded_at_visitor_level(client):
    # a visitor flagged staff only partway through must be excluded entirely
    await _ingest(client, [
        make_event("ENTRY", "VIS_S", is_staff=False),                 # early event, not yet flagged
        make_event("ZONE_DWELL", "VIS_S", "MAKEUP", dwell_ms=95000, is_staff=True),  # now staff
        make_event("ENTRY", "VIS_REAL"),
    ])
    m = (await client.get(f"/stores/{STORE}/metrics{DATE}")).json()
    assert m["unique_visitors"] == 1  # only VIS_REAL; VIS_S dropped despite an unflagged event


async def test_zero_purchase_store(client):
    # visitor browses but never near a POS transaction -> conversion 0, no crash
    await _ingest(client, [
        make_event("ENTRY", "VIS_C", ts="2026-03-03T20:00:00Z"),
        make_event("ZONE_ENTER", "VIS_C", "MAKEUP", ts="2026-03-03T20:01:00Z"),
    ])
    m = (await client.get(f"/stores/{STORE}/metrics{DATE}")).json()
    assert m["unique_visitors"] == 1
    assert m["purchases"] == 0
    assert m["conversion_rate"] == 0.0


async def test_empty_store_returns_zeros(client):
    m = (await client.get("/stores/STORE_EMPTY/metrics")).json()
    assert m["unique_visitors"] == 0
    assert m["conversion_rate"] == 0.0


async def test_conversion_via_pos_correlation(client):
    # POS has a txn at 2026-03-03T14:31:08Z; billing presence just before it converts.
    await _ingest(client, [
        make_event("ENTRY", "VIS_A", ts="2026-03-03T14:30:00Z"),
        make_event("ZONE_ENTER", "VIS_A", "BILLING", ts="2026-03-03T14:31:00Z"),
    ])
    m = (await client.get(f"/stores/{STORE}/metrics{DATE}")).json()
    assert m["purchases"] == 1
    assert m["conversion_rate"] == 1.0


async def test_reentry_not_double_counted_in_funnel(client):
    # Same visitor_id enters, exits, re-enters -> funnel ENTRY stage must count 1, not 2.
    await _ingest(client, [
        make_event("ENTRY", "VIS_R", ts="2026-03-03T14:30:00Z"),
        make_event("EXIT", "VIS_R", ts="2026-03-03T14:35:00Z"),
        make_event("REENTRY", "VIS_R", ts="2026-03-03T14:40:00Z"),
        make_event("ZONE_ENTER", "VIS_R", "SKINCARE", ts="2026-03-03T14:41:00Z"),
    ])
    f = (await client.get(f"/stores/{STORE}/funnel{DATE}")).json()
    entry_stage = next(s for s in f["stages"] if s["stage"] == "ENTRY")
    assert entry_stage["count"] == 1


async def test_heatmap_low_confidence_flag(client):
    await _ingest(client, [make_event("ZONE_ENTER", "VIS_C", "SKINCARE")])
    h = (await client.get(f"/stores/{STORE}/heatmap{DATE}")).json()
    assert h["data_confidence"] == "LOW"  # < 20 sessions
    assert any(z["zone_id"] == "SKINCARE" for z in h["zones"])
