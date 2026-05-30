"""Appearance Re-Identification for cross-camera dedup and re-entry detection.

Primary path: OSNet embeddings via torchreid.
Fallback path: an HSV colour-histogram descriptor — so the pipeline still runs and
degrades gracefully (per the brief: "detection confidence must degrade gracefully,
not fail silently") when torchreid/GPU weights are unavailable.

The gallery is keyed at the *store* level so the same physical person seen on the
entry camera and the overlapping floor camera resolves to one visitor_id.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .config import CONFIG


def _new_visitor_id() -> str:
    return f"VIS_{uuid.uuid4().hex[:8]}"


class _Embedder:
    """Lazily initialised OSNet extractor with histogram fallback."""

    def __init__(self) -> None:
        self._extractor = None
        self._mode = "histogram"
        try:
            from torchreid.utils import FeatureExtractor  # type: ignore

            self._extractor = FeatureExtractor(
                model_name="osnet_x0_25",
                device=CONFIG.device or "cuda",
            )
            self._mode = "osnet"
        except Exception:
            self._extractor = None  # fall back silently to histogram

    @property
    def mode(self) -> str:
        return self._mode

    def __call__(self, crop: np.ndarray) -> Optional[np.ndarray]:
        if crop is None or crop.size == 0:
            return None
        if self._extractor is not None:
            try:
                feat = self._extractor(crop).cpu().numpy().reshape(-1)
                return _l2(feat)
            except Exception:
                pass  # degrade to histogram for this crop
        return _hsv_histogram(crop)


def _l2(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def _hsv_histogram(crop: np.ndarray, bins: int = 16) -> np.ndarray:
    """Cheap appearance signature: normalised H/S histograms of the upper body."""
    try:
        import cv2

        h = crop.shape[0]
        upper = crop[: max(1, h // 2)]  # torso region is more discriminative
        hsv = cv2.cvtColor(upper, cv2.COLOR_BGR2HSV)
        hist_h = cv2.calcHist([hsv], [0], None, [bins], [0, 180]).reshape(-1)
        hist_s = cv2.calcHist([hsv], [1], None, [bins], [0, 256]).reshape(-1)
        return _l2(np.concatenate([hist_h, hist_s]).astype(np.float32))
    except Exception:
        return _l2(crop.mean(axis=(0, 1)).astype(np.float32))


def cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    if a is None or b is None or a.shape != b.shape:
        return 1.0
    return float(1.0 - np.dot(a, b))


@dataclass
class GalleryEntry:
    visitor_id: str
    embedding: np.ndarray
    last_seen: float
    exited: bool = False
    cameras: set = field(default_factory=set)


class ReIDGallery:
    """Store-scoped gallery resolving track appearances to stable visitor_ids."""

    def __init__(self) -> None:
        self._embedder = _Embedder()
        self._entries: dict[str, GalleryEntry] = {}

    @property
    def mode(self) -> str:
        return self._embedder.mode

    def embed(self, crop: np.ndarray) -> Optional[np.ndarray]:
        return self._embedder(crop)

    def _evict(self, now: float) -> None:
        ttl = CONFIG.reid_gallery_ttl_s
        stale = [vid for vid, e in self._entries.items() if now - e.last_seen > ttl]
        for vid in stale:
            del self._entries[vid]

    def assign(
        self, embedding: Optional[np.ndarray], camera_id: str, now: float
    ) -> tuple[str, bool, bool]:
        """Resolve an appearance to a visitor_id.

        Returns (visitor_id, is_reentry, is_cross_camera_dup).
        - is_reentry: matched a visitor that previously EXITed.
        - is_cross_camera_dup: matched an active visitor first seen on another camera.
        """
        self._evict(now)
        best_vid, best_dist = None, 1.0
        if embedding is not None:
            for vid, e in self._entries.items():
                d = cosine_distance(embedding, e.embedding)
                if d < best_dist:
                    best_vid, best_dist = vid, d

        if best_vid is not None and best_dist <= CONFIG.reid_match_threshold:
            e = self._entries[best_vid]
            is_reentry = e.exited and (now - e.last_seen) <= CONFIG.reentry_window_s
            is_cross = (not e.exited) and (camera_id not in e.cameras)
            # update entry (EMA on embedding to stay current)
            e.embedding = _l2(0.7 * e.embedding + 0.3 * embedding) if embedding is not None else e.embedding
            e.last_seen = now
            e.exited = False
            e.cameras.add(camera_id)
            return best_vid, is_reentry, is_cross

        vid = _new_visitor_id()
        self._entries[vid] = GalleryEntry(
            visitor_id=vid,
            embedding=embedding if embedding is not None else np.zeros(1, np.float32),
            last_seen=now,
            cameras={camera_id},
        )
        return vid, False, False

    def mark_exit(self, visitor_id: str, now: float) -> None:
        e = self._entries.get(visitor_id)
        if e is not None:
            e.exited = True
            e.last_seen = now
