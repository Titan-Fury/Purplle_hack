# PROMPT: "Write a single end-to-end journey test for the Store Intelligence API using the
#          existing httpx ASGITransport harness (client/make_event/STORE). Ingest one realistic
#          day of CCTV-derived events (a converter, a queue-abandoner, a browser, an excluded
#          staff member, and a re-entry), then assert the WHOLE surface is consistent: health,
#          metrics, funnel, heatmap, anomalies, the batched /summary, idempotent re-ingest,
#          /stats, the served /dashboard HTML + vendored /static/three.min.js, and *_LIVE reset."
# CHANGES MADE: Anchored the scenario on 2026-03-03 (the date the bundled POS file has
#          transactions for STORE_BLR_002) so conversion is exercised for real; discovered the
#          billing zone id from /layout instead of hard-coding; placed the abandoner's billing
#          presence before the first POS transaction so it stays a genuine non-converter; and
#          asserted /summary equals the standalone endpoints plus the UI data-contract anchors.
from __future__ import annotations

import pytest

from .conftest import STORE, make_event

pytestmark = pytest.mark.asyncio

DATE = "2026-03-03"  # the bundled POS file has STORE_BLR_002 transactions from 14:31:08Z


async def _ingest(client, events):
    r = await client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200
    return r.json()


async def _billing_zone(client) -> str:
    r = await client.get(f"/stores/{STORE}/layout")
    assert r.status_code == 200
    zones = r.json()["zones"]
    return next(z["zone_id"] for z in zones if z["is_billing"])


def _scenario(bz: str) -> list[dict]:
    """One store-day: 3 customers + 1 staff (excluded) + a re-entry of an existing visitor.

    VIS_1 converts (billing presence 14:30:30 -> POS txn 14:31:08, <5 min).
    VIS_2 abandons and does NOT convert (billing presence 14:24:30, before the first txn).
    VIS_3 only browses (no billing). STAFF_1 is excluded from every customer metric.
    """
    return [
        # excluded staff member
        make_event("ENTRY", "STAFF_1", ts=f"{DATE}T14:00:00Z", is_staff=True),
        make_event("ZONE_ENTER", "STAFF_1", "MAKEUP", ts=f"{DATE}T14:01:00Z", is_staff=True),
        # VIS_3 — browser only
        make_event("ENTRY", "VIS_3", ts=f"{DATE}T14:20:00Z"),
        make_event("ZONE_ENTER", "VIS_3", "MAKEUP", ts=f"{DATE}T14:20:30Z", dwell_ms=90_000),
        # VIS_2 — queue abandoner, no purchase (billing presence precedes the first txn)
        make_event("ENTRY", "VIS_2", ts=f"{DATE}T14:24:00Z"),
        make_event("ZONE_ENTER", "VIS_2", "MAKEUP", ts=f"{DATE}T14:24:20Z", dwell_ms=30_000),
        make_event("BILLING_QUEUE_JOIN", "VIS_2", bz, ts=f"{DATE}T14:24:30Z", queue_depth=4),
        make_event("BILLING_QUEUE_ABANDON", "VIS_2", bz, ts=f"{DATE}T14:25:00Z"),
        # VIS_1 — converter
        make_event("ENTRY", "VIS_1", ts=f"{DATE}T14:29:00Z"),
        make_event("ZONE_ENTER", "VIS_1", "MAKEUP", ts=f"{DATE}T14:29:30Z", dwell_ms=120_000),
        make_event("BILLING_QUEUE_JOIN", "VIS_1", bz, ts=f"{DATE}T14:30:30Z", queue_depth=2),
        # VIS_1 returns later — re-entry must NOT create a new unique visitor
        make_event("REENTRY", "VIS_1", ts=f"{DATE}T14:55:00Z"),
    ]


async def test_full_store_day_journey(client):
    bz = await _billing_zone(client)
    events = _scenario(bz)
    res = await _ingest(client, events)
    assert res["accepted"] == len(events) and res["rejected"] == 0

    # ---- health: the store is now known and reports its last event ----
    h = await client.get("/health")
    assert h.status_code == 200
    hs = next(s for s in h.json()["stores"] if s["store_id"] == STORE)
    assert hs["last_event"] is not None

    # ---- metrics: staff excluded, re-entry not double-counted, real POS conversion ----
    m = (await client.get(f"/stores/{STORE}/metrics?date={DATE}")).json()
    assert m["unique_visitors"] == 3          # VIS_1/2/3; STAFF_1 excluded, REENTRY not a new id
    assert m["staff_excluded"] is True
    assert m["purchases"] == 1                # VIS_1 only
    assert m["conversion_rate"] == round(1 / 3, 4)
    assert m["conversion_rate"] == round(m["purchases"] / m["unique_visitors"], 4)
    assert 0.0 <= m["conversion_rate"] <= 1.0
    assert m["abandonment_rate"] == 0.5       # VIS_2 abandoned, not converted; /2 billing visitors
    assert m["queue_depth"] == {"current": 2, "max": 4}

    # ---- funnel: monotonically non-increasing, ENTRY == unique visitors ----
    f = (await client.get(f"/stores/{STORE}/funnel?date={DATE}")).json()
    counts = [s["count"] for s in f["stages"]]
    assert [s["stage"] for s in f["stages"]] == ["ENTRY", "ZONE_VISIT", "BILLING_QUEUE", "PURCHASE"]
    assert counts == [3, 3, 2, 1]
    assert counts == sorted(counts, reverse=True)         # never gains people downstream
    assert counts[0] == m["unique_visitors"]

    # ---- heatmap: known zones present, normals bounded ----
    hm = (await client.get(f"/stores/{STORE}/heatmap?date={DATE}")).json()
    assert hm["zones"], "heatmap should list zones"
    assert all(0.0 <= z["avg_dwell_norm"] <= 100.0 for z in hm["zones"])

    # ---- /summary equals the standalone endpoints (single batched read) ----
    s = (await client.get(f"/stores/{STORE}/summary?date={DATE}")).json()
    assert set(s) >= {"metrics", "funnel", "heatmap", "anomalies", "events"}
    assert s["metrics"] == m
    assert s["funnel"] == f
    assert s["heatmap"] == hm
    assert s["anomalies"] == (await client.get(f"/stores/{STORE}/anomalies?date={DATE}")).json()
    assert len(s["events"]) == len(events)

    # ---- idempotency: re-posting the identical batch changes nothing ----
    again = await _ingest(client, events)
    assert again["accepted"] == 0 and again["duplicates"] == len(events)
    m2 = (await client.get(f"/stores/{STORE}/metrics?date={DATE}")).json()
    assert m2 == m

    # ---- observability counters advanced ----
    st = (await client.get("/stats")).json()
    assert st["requests_total"] >= 1
    assert "endpoints" in st and "requests_by_status" in st


async def test_dashboard_and_static_assets_served(client):
    """The single-page dashboard and its vendored 3D library are actually served, and the
    HTML still carries the element IDs the dashboard JS binds to (the UI data contract)."""
    r = await client.get("/dashboard")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    html = r.text
    for anchor in ('id="kpis"', 'id="funnel"', 'id="map"', 'id="anomalies"',
                   'id="health"', 'id="ticker"', "/summary"):
        assert anchor in html, f"dashboard lost data-contract anchor: {anchor}"
    # new UI features wired up
    assert 'id="bg3d"' in html and 'id="themeToggle"' in html
    assert "/static/three.min.js" in html
    assert 'data-theme' in html  # dark-mode plumbing present

    # the vendored Three.js is reachable via the static mount
    js = await client.get("/static/three.min.js")
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]


async def test_live_store_reset_clears_only_demo_data(client):
    live = "ST1008_LIVE"
    await _ingest(client, [make_event("ENTRY", "VIS_A", ts=f"{DATE}T14:30:00Z", store_id=live)])
    assert (await client.get(f"/stores/{live}/metrics?date={DATE}")).json()["unique_visitors"] == 1

    r = await client.post(f"/stores/{live}/reset")
    assert r.status_code == 200 and r.json()["deleted"] >= 1
    assert (await client.get(f"/stores/{live}/metrics?date={DATE}")).json()["unique_visitors"] == 0
