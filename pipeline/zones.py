"""Zone geometry and store-layout helpers.

Loads `store_layout.json` and answers two questions the tracker needs every frame:
  1. Which named zone (if any) contains a point?  -> point-in-polygon
  2. Did a track cross the entry line, and in which direction? -> signed-side test

All coordinates in the layout are normalised [0..1], so this works for any camera
resolution without changes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

try:
    from shapely.geometry import Point, Polygon

    _HAS_SHAPELY = True
except ImportError:  # graceful fallback — ray casting below
    _HAS_SHAPELY = False


@dataclass
class Zone:
    zone_id: str
    sku_zone: Optional[str]
    is_billing: bool
    camera_id: str
    polygon: list[list[float]]
    _shape: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if _HAS_SHAPELY:
            self._shape = Polygon(self.polygon)

    def contains(self, x: float, y: float) -> bool:
        if _HAS_SHAPELY:
            return self._shape.contains(Point(x, y))
        return _ray_cast(x, y, self.polygon)


@dataclass
class Camera:
    camera_id: str
    role: str
    source: str
    width: int
    height: int
    fps: float
    entry_line: Optional[dict]
    inbound_direction: Optional[str]
    covers_zones: list[str]
    overlaps_with: list[str]


def _ray_cast(x: float, y: float, poly: list[list[float]]) -> bool:
    """Even-odd point-in-polygon, used only if shapely is unavailable."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


class StoreLayout:
    """Indexed view over one store's cameras and zones."""

    def __init__(self, store_id: str, raw: dict):
        self.store_id = store_id
        self.open_hours = raw.get("open_hours", {})
        self.cameras: dict[str, Camera] = {}
        for c in raw.get("cameras", []):
            frame = c.get("frame", {})
            self.cameras[c["camera_id"]] = Camera(
                camera_id=c["camera_id"],
                role=c.get("role", "floor"),
                source=c.get("source", ""),
                width=frame.get("width", 1920),
                height=frame.get("height", 1080),
                fps=frame.get("fps", 15),
                entry_line=c.get("entry_line"),
                inbound_direction=c.get("inbound_direction"),
                covers_zones=c.get("covers_zones", []),
                overlaps_with=c.get("overlaps_with", []),
            )
        self.zones: dict[str, Zone] = {
            z["zone_id"]: Zone(
                zone_id=z["zone_id"],
                sku_zone=z.get("sku_zone"),
                is_billing=z.get("is_billing", False),
                camera_id=z["camera_id"],
                polygon=z["polygon"],
            )
            for z in raw.get("zones", [])
        }

    def zones_for_camera(self, camera_id: str) -> list[Zone]:
        return [z for z in self.zones.values() if z.camera_id == camera_id]

    def zone_at(self, camera_id: str, x: float, y: float) -> Optional[Zone]:
        """Return the named zone containing normalised point (x, y) on this camera."""
        for z in self.zones_for_camera(camera_id):
            if z.contains(x, y):
                return z
        return None


@lru_cache(maxsize=4)
def load_layout(path: str) -> dict[str, StoreLayout]:
    """Load every store from the layout file, indexed by store_id (cached)."""
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {s["store_id"]: StoreLayout(s["store_id"], s) for s in raw.get("stores", [])}


def line_side(line: dict, x: float, y: float) -> float:
    """Signed distance of point to the directed entry line (>0 one side, <0 other)."""
    x1, y1, x2, y2 = line["x1"], line["y1"], line["x2"], line["y2"]
    return (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
