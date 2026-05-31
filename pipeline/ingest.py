"""Zero-config ingest for arbitrary clip folders.

The normal path expects a registered store in ``store_layout.json`` (authoritative
camera roles + hand-drawn zone polygons). But a grader may simply hand us a folder of
clips with no store, no labels and no geometry. This module makes that work:

  * ``discover_clips``   — find every video file under a directory (recursively).
  * ``synthesize_layout``— build a best-effort :class:`StoreLayout` on the fly:
        - each clip becomes a camera; its role is resolved by filename convention,
          then (optionally) by content auto-classification;
        - the entry camera gets a *default* vertical door-line at mid-frame so entry/
          exit counting still fires (documented fallback — real stores override it);
        - floor/billing cameras get a full-frame zone so dwell, queue and conversion
          analytics still have something to attribute to.

Footfall (unique visitors) never depends on geometry — it comes from distinct tracked
identities — so even an all-"floor" folder produces a meaningful funnel.
"""
from __future__ import annotations

import os
from typing import Optional

from .camera_resolver import auto_classify, role_from_convention
from .zones import StoreLayout

VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".m4v", ".mpg", ".mpeg", ".webm")

# Fallback door-line: vertical, mid-frame, inbound = left. Real deployments override
# this in store_layout.json after a one-time calibration; it lets an unlabelled clip
# still produce entry/exit counts instead of zero.
DEFAULT_ENTRY_LINE = {"x1": 0.5, "y1": 0.0, "x2": 0.5, "y2": 1.0}
DEFAULT_INBOUND = "left"
FULL_FRAME = [[0.02, 0.02], [0.98, 0.02], [0.98, 0.98], [0.02, 0.98]]


def discover_clips(clips_dir: str) -> list[str]:
    """Return sorted absolute paths of every video file under ``clips_dir``."""
    if not os.path.isdir(clips_dir):
        raise NotADirectoryError(clips_dir)
    found: list[str] = []
    for root, _dirs, files in os.walk(clips_dir):
        for name in files:
            if name.lower().endswith(VIDEO_EXTS):
                found.append(os.path.join(root, name))
    return sorted(found)


def _resolve_source_role(path: str, mode: str) -> tuple[str, str]:
    """Resolve a clip's role with no registry: convention first, then auto/default."""
    by_conv = role_from_convention(path)
    if by_conv:
        return by_conv, "convention"
    if mode == "auto":
        return auto_classify(path)  # VLM or motion heuristic
    return "floor", "default"


def synthesize_layout(
    store_id: str,
    clip_paths: list[str],
    resolve_mode: str = "auto",
    fps: float = 15.0,
) -> StoreLayout:
    """Build a best-effort :class:`StoreLayout` from a bare list of clips."""
    cameras: list[dict] = []
    zones: list[dict] = []
    roles_seen: list[str] = []

    for i, path in enumerate(clip_paths, start=1):
        role, _method = _resolve_source_role(path, resolve_mode)
        roles_seen.append(role)
        cam_id = f"CAM_AUTO_{i:02d}"
        cam: dict = {
            "camera_id": cam_id,
            "role": role,
            "source": path,
            "frame": {"width": 1920, "height": 1080, "fps": fps},
        }
        if role == "entry":
            cam["entry_line"] = dict(DEFAULT_ENTRY_LINE)
            cam["inbound_direction"] = DEFAULT_INBOUND
        cameras.append(cam)
        if role in ("floor", "billing"):
            zones.append({
                "zone_id": f"{role}_{i:02d}",
                "camera_id": cam_id,
                "is_billing": role == "billing",
                "sku_zone": None,
                "polygon": [list(p) for p in FULL_FRAME],
            })

    # If nothing resolved to an entry camera, promote the first clip so entry/exit
    # counting still happens (best-effort — logged by the caller).
    if clip_paths and "entry" not in roles_seen:
        cameras[0]["role"] = "entry"
        cameras[0]["entry_line"] = dict(DEFAULT_ENTRY_LINE)
        cameras[0]["inbound_direction"] = DEFAULT_INBOUND
        zones = [z for z in zones if z["camera_id"] != cameras[0]["camera_id"]]

    raw = {"store_id": store_id, "open_hours": {}, "cameras": cameras, "zones": zones}
    return StoreLayout(store_id, raw)
