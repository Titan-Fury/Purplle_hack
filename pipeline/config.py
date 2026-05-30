"""Pipeline configuration — all tunables in one place, overridable via env vars.

Kept dependency-free (no pydantic) so the pipeline image stays lightweight and can
run on a GPU host without the API stack installed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, default))


@dataclass(frozen=True)
class PipelineConfig:
    # ---- paths ----
    store_layout_path: str = os.getenv("STORE_LAYOUT_PATH", "data/store_layout.json")
    output_dir: str = os.getenv("PIPELINE_OUTPUT_DIR", "pipeline/output")

    # ---- clip → wall-clock anchoring ----
    # Real timestamp = clip_start + frame_offset. Override per run if known.
    clip_start_iso: str = os.getenv("CLIP_START_ISO", "2026-03-03T14:30:00Z")

    # ---- detection ----
    model_weights: str = os.getenv("YOLO_WEIGHTS", "yolov8n.pt")
    device: str = os.getenv("YOLO_DEVICE", "")  # "" -> auto (cuda if available)
    person_class_id: int = 0  # COCO 'person'
    conf_threshold: float = _f("YOLO_CONF", 0.25)  # low: keep low-conf, do not suppress
    iou_threshold: float = _f("YOLO_IOU", 0.5)
    tracker_cfg: str = os.getenv("TRACKER_CFG", "bytetrack.yaml")
    frame_stride: int = _i("FRAME_STRIDE", 1)  # process every Nth frame for speed

    # ---- behavioural rules ----
    dwell_emit_interval_s: int = _i("DWELL_EMIT_INTERVAL_S", 30)
    zone_min_presence_s: float = _f("ZONE_MIN_PRESENCE_S", 1.0)  # debounce zone flicker
    entry_line_min_disp: float = _f("ENTRY_LINE_MIN_DISP", 0.05)  # hysteresis margin (normalised)
    reentry_window_s: float = _f("REENTRY_WINDOW_S", 600.0)
    track_lost_grace_s: float = _f("TRACK_LOST_GRACE_S", 2.0)

    # ---- staff heuristic (rule-based; see CHOICES.md) ----
    staff_min_zones: int = _i("STAFF_MIN_ZONES", 4)
    staff_min_presence_s: float = _f("STAFF_MIN_PRESENCE_S", 240.0)
    staff_min_entry_crossings: int = _i("STAFF_MIN_ENTRY_CROSSINGS", 3)

    # ---- re-id ----
    reid_match_threshold: float = _f("REID_MATCH_THRESHOLD", 0.55)  # cosine distance (OSNet)
    reid_cnn_threshold: float = _f("REID_CNN_THRESHOLD", 0.30)      # torchvision CNN embedding
    reid_hist_threshold: float = _f("REID_HIST_THRESHOLD", 0.12)    # stricter for histogram
    reid_coexist_window_s: float = _f("REID_COEXIST_WINDOW_S", 1.5)  # active -> can't be same person
    reid_gallery_ttl_s: float = _f("REID_GALLERY_TTL_S", 900.0)

    # ---- ingest ----
    api_url: str = os.getenv("API_URL", "")  # if set, stream events live
    ingest_batch_size: int = _i("INGEST_BATCH_SIZE", 200)


CONFIG = PipelineConfig()
