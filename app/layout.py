"""Lightweight, app-side view of store_layout.json.

The API only needs zone metadata (which zones exist, which is billing, the sku label),
not polygon geometry — so this loader has no shapely dependency and keeps the API image
slim. Geometry lives in the pipeline.
"""
from __future__ import annotations

import json
from functools import lru_cache

from .config import get_settings


class StoreMeta:
    def __init__(self, raw: dict):
        self.store_id: str = raw["store_id"]
        self.open_hours: dict = raw.get("open_hours", {})
        self.zones: list[dict] = raw.get("zones", [])
        self.zone_ids: list[str] = [z["zone_id"] for z in self.zones]
        self.billing_zone_ids: set[str] = {z["zone_id"] for z in self.zones if z.get("is_billing")}
        self.sku_by_zone: dict[str, str | None] = {
            z["zone_id"]: z.get("sku_zone") for z in self.zones
        }
        self.camera_ids: list[str] = [c["camera_id"] for c in raw.get("cameras", [])]


@lru_cache(maxsize=1)
def _all() -> dict[str, StoreMeta]:
    path = get_settings().store_layout_path
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    return {s["store_id"]: StoreMeta(s) for s in raw.get("stores", [])}


def get_store_meta(store_id: str) -> StoreMeta | None:
    return _all().get(store_id)


def known_store_ids() -> list[str]:
    return list(_all().keys())
