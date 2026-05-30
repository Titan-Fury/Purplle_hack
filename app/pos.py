"""POS transaction loading and visitor→purchase correlation.

There is no reliable customer identity to join on (CCTV is anonymised), so correlation is
by **time window + store**: a visitor present in the billing zone within
POS_CORRELATION_WINDOW_S *before* a transaction timestamp is counted as converted.

The loader auto-detects two formats:
  * the documented schema  — store_id, transaction_id, timestamp(ISO), basket_value_inr
  * the real Purplle export — 39 columns, one row per line item; we aggregate to one
    transaction per invoice_number (sum total_amount) and build the timestamp from
    order_date (DD-MM-YYYY) + order_time (HH:MM:SS) interpreted as store-local IST.
"""
from __future__ import annotations

import csv
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from .config import get_settings

_IST = timezone(timedelta(hours=5, minutes=30))


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.strip().replace("Z", "+00:00")).astimezone(timezone.utc)


def _parse_brigade(date_s: str, time_s: str) -> datetime:
    # "10-04-2026" + "16:55:36", interpreted as IST, returned as UTC
    dt = datetime.strptime(f"{date_s.strip()} {time_s.strip()}", "%d-%m-%Y %H:%M:%S")
    return dt.replace(tzinfo=_IST).astimezone(timezone.utc)


@lru_cache(maxsize=1)
def _load() -> dict[str, list[tuple[datetime, float]]]:
    """store_id -> sorted list of (timestamp, basket_value) at transaction granularity."""
    path = get_settings().pos_csv_path
    by_store: dict[str, list[tuple[datetime, float]]] = {}
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            cols = set(reader.fieldnames or [])
            if {"order_date", "order_time", "total_amount", "invoice_number"} <= cols:
                _load_brigade(reader, by_store)
            else:
                _load_simple(reader, by_store)
    except FileNotFoundError:
        return {}
    for v in by_store.values():
        v.sort(key=lambda t: t[0])
    return by_store


def _load_simple(reader, by_store) -> None:
    for row in reader:
        try:
            ts = _parse_iso(row["timestamp"])
            val = float(row["basket_value_inr"])
        except (KeyError, ValueError):
            continue
        by_store.setdefault(row["store_id"].strip(), []).append((ts, val))


def _load_brigade(reader, by_store) -> None:
    # aggregate line items -> one transaction per invoice
    agg: dict[tuple[str, str], list] = defaultdict(lambda: [None, 0.0])
    for row in reader:
        try:
            store = (row.get("store_id") or "").strip()
            inv = (row.get("invoice_number") or "").strip()
            ts = _parse_brigade(row["order_date"], row["order_time"])
            amt = float(row.get("total_amount") or 0)
        except (KeyError, ValueError):
            continue
        rec = agg[(store, inv)]
        rec[0] = ts if rec[0] is None else min(rec[0], ts)
        rec[1] += amt
    for (store, _inv), (ts, amt) in agg.items():
        if ts is not None:
            by_store.setdefault(store, []).append((ts, amt))


def transactions(store_id: str, start: datetime, end: datetime) -> list[tuple[datetime, float]]:
    rows = _load().get(store_id, [])
    times = [t for t, _ in rows]
    lo = bisect_left(times, start)
    hi = bisect_right(times, end)
    return rows[lo:hi]


def converted_visitors(
    store_id: str,
    billing_presence: list[tuple[str, datetime]],
    start: datetime,
    end: datetime,
) -> set[str]:
    """Return the set of visitor_ids that converted.

    billing_presence: (visitor_id, timestamp) pairs for every billing-zone presence.
    A visitor converts if any of their billing timestamps falls in
    [txn - window, txn] for some transaction in the window.
    """
    window = get_settings().pos_correlation_window_s
    txns = [t for t, _ in transactions(store_id, start, end)]
    if not txns or not billing_presence:
        return set()
    txns.sort()
    converted: set[str] = set()
    for visitor_id, ts in billing_presence:
        # find transactions at or after ts, within `window` seconds
        i = bisect_left(txns, ts)
        if i < len(txns) and (txns[i] - ts).total_seconds() <= window:
            converted.add(visitor_id)
    return converted
