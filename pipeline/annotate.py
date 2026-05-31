"""Generate a LOCAL annotated demo clip for the dashboard video panel.

IMPORTANT — licence: the CCTV footage may not be published or redistributed, so the
output (which contains the footage) is written to app/static/demo.mp4 and is GIT-IGNORED.
It is never committed. The dashboard shows the video panel only if this file exists, so
the public deployment stays licence-compliant.

Usage:
    python -m pipeline.annotate --store ST1008 --camera CAM_ENTRY_01 --seconds 40

Draws YOLO+ByteTrack boxes, track ids, the entry line, and a live ENTRY/EXIT counter
using the same StoreTracker logic the pipeline uses, then downscales for a light file.
"""
from __future__ import annotations

import argparse
import os
import sys

import cv2

from .config import CONFIG
from .reid import ReIDGallery
from .tracker import StoreTracker
from .zones import load_layout


def main() -> int:
    ap = argparse.ArgumentParser(description="Render a local annotated demo clip (gitignored).")
    ap.add_argument("--store", default="ST1008")
    ap.add_argument("--camera", default="CAM_ENTRY_01")
    ap.add_argument("--layout", default="data/store_layout.json")
    ap.add_argument("--out", default="app/static/demo.mp4")
    ap.add_argument("--width", type=int, default=960)
    ap.add_argument("--seconds", type=float, default=40.0)
    args = ap.parse_args()

    layout = load_layout(args.layout)[args.store]
    cam = layout.cameras[args.camera]
    if not cam.source or not os.path.exists(cam.source):
        print(f"source clip not found for {args.camera}: {cam.source!r}")
        print("This tool only runs locally where the footage is present.")
        return 2

    from ultralytics import YOLO

    model = YOLO(CONFIG.model_weights)
    cap = cv2.VideoCapture(cam.source)
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    fps = cam.fps or 30.0
    stride = max(1, CONFIG.frame_stride)
    scale = args.width / W if W else 1.0
    out_w, out_h = int(W * scale), int(H * scale)

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    vw = cv2.VideoWriter(args.out, cv2.VideoWriter_fourcc(*"mp4v"), fps / stride, (out_w, out_h))

    events: list = []
    tracker = StoreTracker(layout, ReIDGallery(), events.append)
    ly = cam.entry_line
    vertical = ly and abs(ly["x1"] - ly["x2"]) < abs(ly["y1"] - ly["y2"])

    entry_n = exit_n = reentry_n = step = 0
    for result in model.track(source=cam.source, stream=True, persist=True,
                              tracker=CONFIG.tracker_cfg, classes=[0],
                              conf=CONFIG.conf_threshold, vid_stride=stride, verbose=False):
        ts = step * stride / fps
        if ts > args.seconds:
            break
        frame = result.orig_img
        before = len(events)
        b = result.boxes
        if b is not None and b.id is not None:
            for tid, (x1, y1, x2, y2), conf in zip(
                b.id.int().tolist(), b.xyxy.cpu().numpy(), b.conf.cpu().numpy()
            ):
                cx = ((x1 + x2) / 2) / W
                cy = ((y1 + y2) / 2) / H
                crop = frame[max(0, int(y1)):int(y2), max(0, int(x1)):int(x2)]
                tracker.update(args.camera, int(tid), cx, cy, float(conf),
                               crop if crop.size else None, ts)
                inside = (cx < ly["x1"]) if (ly and vertical) else (cy > (ly["y1"] if ly else 0.5))
                col = (0, 200, 0) if inside else (0, 165, 255)
                cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), col, 2)
                cv2.putText(frame, f"ID{int(tid)} {conf:.2f}", (int(x1), int(y1) - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
        tracker.flush_lost(ts)
        for e in events[before:]:
            if e["event_type"] == "ENTRY":
                entry_n += 1
            elif e["event_type"] == "EXIT":
                exit_n += 1
            elif e["event_type"] == "REENTRY":
                reentry_n += 1

        if ly:
            if vertical:
                xp = int(ly["x1"] * W)
                cv2.line(frame, (xp, 0), (xp, H), (255, 0, 255), 2)
            else:
                yp = int(ly["y1"] * H)
                cv2.line(frame, (0, yp), (W, yp), (255, 0, 255), 2)
        cv2.rectangle(frame, (0, 0), (470, 86), (0, 0, 0), -1)
        cv2.putText(frame, f"ENTRY:{entry_n}  EXIT:{exit_n}  REENTRY:{reentry_n}",
                    (10, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
        cv2.putText(frame, f"t={ts:5.1f}s  {args.camera}", (10, 70),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)

        vw.write(cv2.resize(frame, (out_w, out_h)))
        step += 1

    vw.release()
    print(f"wrote {args.out}  ({out_w}x{out_h}, ENTRY={entry_n} EXIT={exit_n} REENTRY={reentry_n})")
    print("Note: this file is gitignored (footage licence) — it serves the local dashboard only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
