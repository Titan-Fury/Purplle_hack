"""Per-track behavioural state machine.

Consumes detection/track updates (one camera at a time, in frame order) and emits
schema-correct behavioural events. Visitor identity is resolved at the *store* level
via the ReID gallery, so:

  * group entry        -> each ByteTrack id is an individual => N tracks => N ENTRYs
  * re-entry           -> ReID match on a previously-exited visitor => REENTRY (not ENTRY)
  * cross-camera dup   -> same person on overlapping cameras keeps one visitor_id
  * partial occlusion  -> low-confidence detections are emitted, never silently dropped
  * empty periods      -> no tracks => no events (no crash)
  * staff              -> rule-based classifier flags is_staff=true
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from .config import CONFIG
from .zones import Camera, StoreLayout, Zone, line_side

EmitFn = Callable[[dict], None]


@dataclass
class TrackState:
    visitor_id: str
    is_staff: bool = False
    session_seq: int = 0
    current_zone: Optional[str] = None
    zone_entered_at: Optional[float] = None
    last_dwell_emit_at: Optional[float] = None
    prev_side: Optional[float] = None
    has_entered: bool = False
    first_seen: float = 0.0
    last_seen: float = 0.0
    zones_visited: set = field(default_factory=set)
    entry_crossings: int = 0
    in_billing: bool = False


class StoreTracker:
    """Drives behavioural event emission for one store across all its cameras."""

    def __init__(self, layout: StoreLayout, gallery, emit: EmitFn):
        self.layout = layout
        self.gallery = gallery
        self.emit = emit
        self._tracks: dict[tuple[str, int], TrackState] = {}
        # visitors currently inside each billing zone -> queue depth
        self._billing_occupants: dict[str, set] = {}
        self._inside_sign: dict[str, float] = {}  # camera_id -> sign of "inside"

    # ---- helpers ----------------------------------------------------------
    def _inside_sign_for(self, cam: Camera) -> float:
        """Precompute which side of the entry line is 'inside the store'."""
        if cam.camera_id in self._inside_sign:
            return self._inside_sign[cam.camera_id]
        line = cam.entry_line
        mx, my = (line["x1"] + line["x2"]) / 2, (line["y1"] + line["y2"]) / 2
        dx, dy = {"down": (0, 0.1), "up": (0, -0.1), "right": (0.1, 0), "left": (-0.1, 0)}.get(
            cam.inbound_direction or "down", (0, 0.1)
        )
        sign = 1.0 if line_side(line, mx + dx, my + dy) > 0 else -1.0
        self._inside_sign[cam.camera_id] = sign
        return sign

    def _next_seq(self, st: TrackState) -> int:
        st.session_seq += 1
        return st.session_seq

    def _classify_staff(self, st: TrackState) -> bool:
        if st.is_staff:
            return True
        presence = st.last_seen - st.first_seen
        st.is_staff = (
            len(st.zones_visited) >= CONFIG.staff_min_zones
            or presence >= CONFIG.staff_min_presence_s
            or st.entry_crossings >= CONFIG.staff_min_entry_crossings
        )
        return st.is_staff

    # ---- main update ------------------------------------------------------
    def update(
        self,
        camera_id: str,
        track_id: int,
        cx: float,
        cy: float,
        confidence: float,
        crop: Optional[np.ndarray],
        ts: float,
    ) -> None:
        """One detection update. (cx, cy) are normalised [0..1] centroid coords."""
        cam = self.layout.cameras.get(camera_id)
        if cam is None:
            return
        key = (camera_id, track_id)
        st = self._tracks.get(key)

        if st is None:
            visitor_id, is_reentry, _cross = self._assign_identity(camera_id, crop, ts, cam)
            st = TrackState(visitor_id=visitor_id, first_seen=ts, last_seen=ts)
            self._tracks[key] = st
            if cam.role == "entry":
                # identity assigned; ENTRY/REENTRY emitted on the actual line crossing
                st.prev_side = line_side(cam.entry_line, cx, cy)
            st._pending_reentry = is_reentry  # type: ignore[attr-defined]

        st.last_seen = ts
        self._classify_staff(st)

        if cam.role == "entry" and cam.entry_line:
            self._handle_threshold(st, cam, cx, cy, confidence, ts)

        self._handle_zone(st, cam, camera_id, cx, cy, confidence, ts)

    def _assign_identity(self, camera_id, crop, ts, cam):
        emb = self.gallery.embed(crop) if crop is not None else None
        return self.gallery.assign(emb, camera_id, ts)

    # ---- entry / exit -----------------------------------------------------
    def _handle_threshold(self, st, cam, cx, cy, confidence, ts):
        side = line_side(cam.entry_line, cx, cy)
        if st.prev_side is None:
            st.prev_side = side
            return
        crossed = (st.prev_side <= 0 < side) or (st.prev_side >= 0 > side)
        if not crossed:
            st.prev_side = side
            return
        inside_sign = self._inside_sign_for(cam)
        going_inside = (side > 0) == (inside_sign > 0)
        st.prev_side = side
        if going_inside:
            st.entry_crossings += 1
            et = "REENTRY" if getattr(st, "_pending_reentry", False) or st.has_entered else "ENTRY"
            st._pending_reentry = False  # type: ignore[attr-defined]
            st.has_entered = True
            self._emit(st, cam.camera_id, et, None, 0, confidence, ts)
        else:
            self.gallery.mark_exit(st.visitor_id, ts)
            self._emit(st, cam.camera_id, "EXIT", None, 0, confidence, ts)

    # ---- zones ------------------------------------------------------------
    def _handle_zone(self, st, cam, camera_id, cx, cy, confidence, ts):
        zone: Optional[Zone] = self.layout.zone_at(camera_id, cx, cy)
        zid = zone.zone_id if zone else None

        # left previous zone?
        if st.current_zone and st.current_zone != zid:
            dwell = int((ts - (st.zone_entered_at or ts)) * 1000)
            prev_zone = self.layout.zones.get(st.current_zone)
            self._emit(st, camera_id, "ZONE_EXIT", st.current_zone, dwell, confidence, ts,
                       sku=prev_zone.sku_zone if prev_zone else None)
            if prev_zone and prev_zone.is_billing:
                self._leave_billing(st, prev_zone, camera_id, dwell, confidence, ts)
            st.current_zone = None
            st.zone_entered_at = None
            st.last_dwell_emit_at = None

        # entered a new zone?
        if zid and st.current_zone != zid:
            st.current_zone = zid
            st.zone_entered_at = ts
            st.last_dwell_emit_at = ts
            st.zones_visited.add(zid)
            self._emit(st, camera_id, "ZONE_ENTER", zid, 0, confidence, ts, sku=zone.sku_zone)
            if zone.is_billing:
                self._join_billing(st, zone, camera_id, confidence, ts)

        # continued dwell?
        if zid and st.current_zone == zid and st.zone_entered_at is not None:
            since_emit = ts - (st.last_dwell_emit_at or st.zone_entered_at)
            if since_emit >= CONFIG.dwell_emit_interval_s:
                dwell = int((ts - st.zone_entered_at) * 1000)
                self._emit(st, camera_id, "ZONE_DWELL", zid, dwell, confidence, ts,
                           sku=zone.sku_zone)
                st.last_dwell_emit_at = ts

    # ---- billing queue ----------------------------------------------------
    def _join_billing(self, st, zone, camera_id, confidence, ts):
        occupants = self._billing_occupants.setdefault(zone.zone_id, set())
        queue_depth = len(occupants)  # existing people ahead, before adding self
        occupants.add(st.visitor_id)
        st.in_billing = True
        if queue_depth > 0:
            self._emit(st, camera_id, "BILLING_QUEUE_JOIN", zone.zone_id, 0, confidence, ts,
                       queue_depth=queue_depth)

    def _leave_billing(self, st, zone, camera_id, dwell, confidence, ts):
        occupants = self._billing_occupants.get(zone.zone_id, set())
        occupants.discard(st.visitor_id)
        st.in_billing = False
        # Candidate abandonment — the API confirms against POS (no transaction followed).
        self._emit(st, camera_id, "BILLING_QUEUE_ABANDON", zone.zone_id, dwell, confidence, ts,
                   queue_depth=len(occupants))

    # ---- emission ---------------------------------------------------------
    def _emit(self, st, camera_id, event_type, zone_id, dwell_ms, confidence, ts,
              sku=None, queue_depth=None):
        self.emit({
            "store_id": self.layout.store_id,
            "camera_id": camera_id,
            "visitor_id": st.visitor_id,
            "event_type": event_type,
            "ts_epoch": ts,
            "zone_id": zone_id,
            "dwell_ms": max(0, int(dwell_ms)),
            "is_staff": st.is_staff,
            "confidence": round(float(confidence), 4),
            "queue_depth": queue_depth,
            "sku_zone": sku,
            "session_seq": self._next_seq(st),
        })

    def flush_lost(self, now: float) -> None:
        """Close zones for tracks not seen within the grace window (e.g. occlusion end)."""
        grace = CONFIG.track_lost_grace_s
        for key, st in list(self._tracks.items()):
            if now - st.last_seen <= grace:
                continue
            if st.current_zone:
                zone = self.layout.zones.get(st.current_zone)
                dwell = int((st.last_seen - (st.zone_entered_at or st.last_seen)) * 1000)
                self._emit(st, key[0], "ZONE_EXIT", st.current_zone, dwell, 0.5, st.last_seen,
                           sku=zone.sku_zone if zone else None)
                if zone and zone.is_billing:
                    self._leave_billing(st, zone, key[0], dwell, 0.5, st.last_seen)
            del self._tracks[key]
