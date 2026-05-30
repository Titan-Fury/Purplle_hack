"""POS transaction loading and visitor→purchase correlation.

There is no customer_id in POS data, so correlation is by **time window + store**:
a visitor present in the billing zone within POS_CORRELATION_WINDOW_S *before* a
transaction timestamp is counted as having converted for that session.
"""
from __future__ import annotations

import csv
from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from functools import lru_cache

from .config import get_settings


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.strip().replace("Z", "+00:00")).astimezone(timezone.utc)


@lru_cache(maxsize=1)
def _load() -> dict[str, list[tuple[datetime, float]]]:
    """store_id -> sorted list of (timestamp, basket_value)."""
    path = get_settings().pos_csv_path
    by_store: dict[str, list[tuple[datetime, float]]] = {}
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    ts = _parse_ts(row["timestamp"])
                    val = float(row["basket_value_inr"])
                except (KeyError, ValueError):
                    continue
                by_store.setdefault(row["store_id"].strip(), []).append((ts, val))
    except FileNotFoundError:
        return {}
    for v in by_store.values():
        v.sort(key=lambda t: t[0])
    return by_store


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
