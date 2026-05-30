"""Detection + tracking driver (YOLOv8 + ByteTrack via Ultralytics).

Runs person detection with the built-in ByteTrack tracker on a single camera clip,
converts each tracked box to a normalised centroid, extracts the crop for Re-ID, and
feeds the StoreTracker which produces behavioural events.

ByteTrack's two-stage association recovers tracks through partial occlusion, so we keep
(rather than drop) low-confidence detections and pass their confidence straight through.
"""
from __future__ import annotations

import time

from .config import CONFIG
from .tracker import StoreTracker
from .zones import Camera, StoreLayout


def process_camera(
    layout: StoreLayout,
    cam: Camera,
    tracker: StoreTracker,
    clip_start_epoch: float,
    realtime: bool = False,
    max_seconds: float | None = None,
) -> int:
    """Process one camera clip end to end. Returns the number of frames processed."""
    from ultralytics import YOLO

    model = YOLO(CONFIG.model_weights)
    stride = max(1, CONFIG.frame_stride)
    stream = model.track(
        source=cam.source,
        stream=True,
        persist=True,
        tracker=CONFIG.tracker_cfg,
        classes=[CONFIG.person_class_id],
        conf=CONFIG.conf_threshold,
        iou=CONFIG.iou_threshold,
        device=CONFIG.device or None,
        vid_stride=stride,   # decode-level frame skipping -> real speedup
        verbose=False,
    )

    fps = cam.fps or 15.0
    step = 0          # number of processed (yielded) frames
    wall_start = time.time()

    for result in stream:
        # with vid_stride, each yielded frame advances real time by stride/fps
        elapsed = step * stride / fps
        ts_epoch = clip_start_epoch + elapsed
        if max_seconds is not None and elapsed > max_seconds:
            break

        h, w = result.orig_shape if hasattr(result, "orig_shape") else (cam.height, cam.width)
        boxes = result.boxes
        if boxes is not None and boxes.id is not None:
            frame = result.orig_img
            ids = boxes.id.int().tolist()
            xyxy = boxes.xyxy.cpu().numpy()
            confs = boxes.conf.cpu().numpy()
            for tid, (x1, y1, x2, y2), conf in zip(ids, xyxy, confs):
                cx = ((x1 + x2) / 2) / w
                cy = ((y1 + y2) / 2) / h
                crop = None
                if frame is not None:
                    yi1, yi2 = max(0, int(y1)), max(0, int(y2))
                    xi1, xi2 = max(0, int(x1)), max(0, int(x2))
                    crop = frame[yi1:yi2, xi1:xi2] if yi2 > yi1 and xi2 > xi1 else None
                tracker.update(cam.camera_id, int(tid), cx, cy, float(conf), crop, ts_epoch)

        tracker.flush_lost(ts_epoch)
        step += 1

        if realtime:
            target = wall_start + step * stride / fps
            sleep = target - time.time()
            if sleep > 0:
                time.sleep(sleep)

    return step
