"""Pipeline entrypoint: process all cameras of a store -> a single event stream.

One ReID gallery + one StoreTracker are shared across every camera of the store, so
identity, billing-queue depth, and cross-camera dedup stay consistent. Output is a
JSONL event file; if --api-url is given, events are also streamed to the API live
(use --realtime for genuine real-time replay, e.g. for the live dashboard).

Usage:
    python -m pipeline.run --store STORE_BLR_002 \
        --layout data/store_layout.json \
        --out pipeline/output/events_STORE_BLR_002.jsonl \
        [--api-url http://localhost:8000] [--realtime] [--max-seconds 120]
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from .camera_resolver import resolve_role
from .config import CONFIG
from .detect import process_camera
from .emit import EventEmitter
from .reid import ReIDGallery
from .tracker import StoreTracker
from .zones import load_layout


def _parse_epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()


def main() -> None:
    p = argparse.ArgumentParser(description="Apex Store Intelligence detection pipeline")
    p.add_argument("--store", default="ST1008")
    p.add_argument("--layout", default=CONFIG.store_layout_path)
    p.add_argument("--out", default="")
    p.add_argument("--api-url", default=CONFIG.api_url)
    p.add_argument("--clip-start", default=CONFIG.clip_start_iso)
    p.add_argument("--realtime", action="store_true")
    p.add_argument("--max-seconds", type=float, default=None)
    p.add_argument("--cameras", nargs="*", default=None, help="subset of camera_ids")
    p.add_argument("--resolve-mode", choices=["registry", "auto"], default="registry",
                   help="registry: trust store_layout roles; auto: fall back to convention/VLM")
    args = p.parse_args()

    layouts = load_layout(args.layout)
    if args.store not in layouts:
        raise SystemExit(f"store {args.store} not in {args.layout}")
    layout = layouts[args.store]

    # Resolve/confirm each camera's role (registry -> convention -> auto-classify).
    for cam in layout.cameras.values():
        role, method = resolve_role(cam.camera_id, cam.source, layout, mode=args.resolve_mode)
        if method != "registry":
            print(f"[pipeline] {cam.camera_id}: role '{role}' resolved by {method}")
        cam.role = role

    out = args.out or f"{CONFIG.output_dir}/events_{args.store}.jsonl"
    clip_start = _parse_epoch(args.clip_start)

    gallery = ReIDGallery()
    emitter = EventEmitter(out, api_url=args.api_url)
    tracker = StoreTracker(layout, gallery, emitter)

    print(f"[pipeline] store={args.store} reid={gallery.mode} -> {out}"
          + (f" (live -> {args.api_url})" if args.api_url else ""))

    cam_ids = args.cameras or list(layout.cameras.keys())
    total = 0
    for cam_id in cam_ids:
        cam = layout.cameras.get(cam_id)
        if cam is None or not cam.source:
            print(f"[pipeline] skip {cam_id}: no source")
            continue
        print(f"[pipeline] processing {cam_id} ({cam.role}) <- {cam.source}")
        frames = process_camera(layout, cam, tracker, clip_start, args.realtime, args.max_seconds)
        total += frames
        print(f"[pipeline]   {cam_id}: {frames} frames")

    emitter.close()
    print(f"[pipeline] done: {emitter.written} events written"
          + (f", {emitter.invalid} invalid dropped" if emitter.invalid else ""))


if __name__ == "__main__":
    main()
