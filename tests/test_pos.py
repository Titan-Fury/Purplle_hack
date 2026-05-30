# PROMPT: "Write a pytest that verifies the POS loader auto-detects the real 39-column
#          Purplle export: aggregates line items to one transaction per invoice_number,
#          sums total_amount, and converts order_date+order_time (IST) to UTC. Also test
#          that a billing presence shortly before a transaction counts as converted."
# CHANGES MADE: Wrote a minimal 2-invoice fixture (3 line items) inline instead of using
#          the real PII file, and asserted the exact IST->UTC offset (12:15 IST -> 06:45 UTC).
from __future__ import annotations

import datetime as dt

import pytest


@pytest.fixture
def pos_with(monkeypatch):
    """Point the POS loader at a temp CSV and reset caches around the test."""
    from app import config, pos

    def _use(csv_path):
        monkeypatch.setenv("POS_CSV_PATH", str(csv_path))
        config.get_settings.cache_clear()
        pos._load.cache_clear()
        return pos

    yield _use
    config.get_settings.cache_clear()
    from app import pos as _p
    _p._load.cache_clear()


def test_brigade_schema_aggregates_invoices_and_converts_ist(tmp_path, pos_with):
    csv_path = tmp_path / "brigade.csv"
    csv_path.write_text(
        "order_id,invoice_number,order_date,order_time,store_id,total_amount\n"
        "1,INV1,10-04-2026,12:15:05,ST1008,400.00\n"   # INV1 line 1
        "1,INV1,10-04-2026,12:15:05,ST1008,99.00\n"    # INV1 line 2 -> summed = 499
        "2,INV2,10-04-2026,18:00:00,ST1008,250.00\n",  # INV2
        encoding="utf-8",
    )
    pos = pos_with(csv_path)

    start = dt.datetime(2026, 4, 10, 0, 0, tzinfo=dt.timezone.utc)
    end = start + dt.timedelta(days=1)
    txns = pos.transactions("ST1008", start, end)

    assert len(txns) == 2                       # two invoices, not three line items
    first_ts, first_amt = txns[0]
    assert first_amt == 499.0                   # 400 + 99 summed per invoice
    # 12:15:05 IST -> 06:45:05 UTC
    assert first_ts == dt.datetime(2026, 4, 10, 6, 45, 5, tzinfo=dt.timezone.utc)

    presence = [("VIS_A", first_ts - dt.timedelta(minutes=2))]
    assert pos.converted_visitors("ST1008", presence, start, end) == {"VIS_A"}


def test_brigade_no_conversion_outside_window(tmp_path, pos_with):
    csv_path = tmp_path / "brigade.csv"
    csv_path.write_text(
        "order_id,invoice_number,order_date,order_time,store_id,total_amount\n"
        "1,INV1,10-04-2026,12:15:05,ST1008,400.00\n",
        encoding="utf-8",
    )
    pos = pos_with(csv_path)
    start = dt.datetime(2026, 4, 10, 0, 0, tzinfo=dt.timezone.utc)
    end = start + dt.timedelta(days=1)
    ts0 = pos.transactions("ST1008", start, end)[0][0]
    presence = [("VIS_A", ts0 - dt.timedelta(minutes=30))]  # outside 5-min window
    assert pos.converted_visitors("ST1008", presence, start, end) == set()
