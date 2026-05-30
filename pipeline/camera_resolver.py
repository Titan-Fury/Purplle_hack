"""Camera role resolution — how the pipeline decides if a clip is entry/floor/billing/staff.

A physical CCTV camera does not move, so its role is fundamentally a registration fact,
not something to infer per clip. We therefore resolve in priority order:

  1. REGISTRY    — store_layout.json already declares the camera's role (authoritative).
  2. CONVENTION  — the file path/name encodes the role, e.g. clips/ST1008/entry/cam.mp4.
  3. AUTO        — last resort: classify from the video content. A VLM is used if one is
                   configured (VLM_API_KEY); otherwise a motion/track heuristic. Auto
                   results are *proposals* — logged for human confirmation, never trusted
                   blindly for the counting-critical 'entry' role.

This layering means a single store works out-of-the-box (registry) while a 40-store fleet
can bulk-ingest by convention, and genuinely unlabelled footage still gets a best-effort
role instead of failing.
"""
from __future__ import annotations

import os
import re
from typing import Optional

ROLES = ("entry", "floor", "billing", "staff_area")

_CONVENTION_KEYWORDS = {
    "entry": ("entry", "entrance", "door", "threshold", "gate"),
    "billing": ("billing", "cash", "checkout", "counter", "till", "pos"),
    "staff_area": ("staff", "backroom", "back_room", "boh", "store_room", "storeroom", "employee"),
    "floor": ("floor", "aisle", "shelf", "browse", "foh", "sales"),
}


def role_from_convention(path: str) -> Optional[str]:
    """Infer a role from folder/filename tokens. Returns None if nothing matches."""
    p = (path or "").lower().replace("\\", "/")
    # prefer a directory segment match (clips/<store>/<role>/...), then filename tokens
    for role in ROLES:  # deterministic priority
        for kw in _CONVENTION_KEYWORDS[role]:
            if re.search(rf"(^|[/_\- ]){re.escape(kw)}([/_\- .]|$)", p):
                return role
    return None


# --------------------------------------------------------------------------- VLM
class VLMClassifier:
    """Optional vision-LLM classifier (OpenAI-compatible Chat Completions API).

    Works with hosted models (OpenAI/Groq/OpenRouter/Gemini) OR fully free & open-source
    local models via Ollama — which needs no API key:
        VLM_BASE_URL=http://localhost:11434/v1  VLM_MODEL=llama3.2-vision   (or llava,
        moondream, qwen2.5vl). Active if VLM_API_KEY is set OR VLM_BASE_URL is overridden.

    Prompt (documented in DESIGN.md):
        "You are labelling a retail CCTV still. Reply with exactly one word from
         [entry, floor, billing, staff_area]: 'entry' if it shows a store
         entrance/doorway/threshold, 'billing' if a checkout/cash counter, 'staff_area'
         if a back-office/storeroom/employee area, otherwise 'floor'."
    """

    PROMPT = (
        "You are labelling a retail CCTV still. Reply with exactly one word from "
        "[entry, floor, billing, staff_area]: 'entry' if it shows a store "
        "entrance/doorway/threshold, 'billing' if a checkout/cash counter, 'staff_area' "
        "if a back-office/storeroom/employee area, otherwise 'floor'."
    )

    def __init__(self) -> None:
        self.api_key = os.getenv("VLM_API_KEY", "")
        self._base_override = os.getenv("VLM_BASE_URL", "")
        self.base_url = self._base_override or "https://api.openai.com/v1"
        self.model = os.getenv("VLM_MODEL", "gpt-4o-mini")

    @property
    def available(self) -> bool:
        # a key (hosted) OR a custom base url (e.g. local Ollama, which needs no key)
        return bool(self.api_key or self._base_override)

    def classify_frame(self, frame_bgr) -> Optional[str]:
        if not self.available:
            return None
        try:
            import base64

            import cv2
            import requests

            ok, buf = cv2.imencode(".jpg", frame_bgr)
            if not ok:
                return None
            b64 = base64.b64encode(buf.tobytes()).decode()
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            resp = requests.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "max_tokens": 4,
                    "messages": [{"role": "user", "content": [
                        {"type": "text", "text": self.PROMPT},
                        {"type": "image_url",
                         "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ]}],
                },
                timeout=30,
            )
            word = resp.json()["choices"][0]["message"]["content"].strip().lower()
            return next((r for r in ROLES if r in word), None)
        except Exception:
            return None


# ---------------------------------------------------------------- motion heuristic
def _heuristic_role(source: str, sample_seconds: float = 45.0) -> str:
    """Classify by movement signature over a short sample window.

    entry      -> many tracks cross the mid-line (transit)
    billing    -> people present but mostly stationary and clustered (a queue)
    staff_area -> people very rarely present
    floor      -> people present and dispersed/moving (default)
    """
    from ultralytics import YOLO

    from .config import CONFIG

    model = YOLO(CONFIG.model_weights)
    tracks: dict[int, list[tuple[float, float]]] = {}
    frames = 0
    persons_per_frame: list[int] = []
    fps = 15.0

    for result in model.track(source=source, stream=True, persist=True,
                              tracker=CONFIG.tracker_cfg, classes=[0],
                              conf=CONFIG.conf_threshold, verbose=False):
        if frames / fps > sample_seconds:
            break
        h, w = result.orig_shape if hasattr(result, "orig_shape") else (1080, 1920)
        b = result.boxes
        n = 0
        if b is not None and b.id is not None:
            for tid, (x1, y1, x2, y2) in zip(b.id.int().tolist(), b.xyxy.cpu().numpy()):
                tracks.setdefault(int(tid), []).append((((x1 + x2) / 2) / w, ((y1 + y2) / 2) / h))
                n += 1
        persons_per_frame.append(n)
        frames += 1

    if not tracks:
        return "staff_area"  # essentially nobody present

    avg_persons = sum(persons_per_frame) / max(1, len(persons_per_frame))
    crossed = stationary = 0
    xs: list[float] = []
    for pts in tracks.values():
        ys = [p[1] for p in pts]
        xs.extend(p[0] for p in pts)
        if min(ys) < 0.5 < max(ys):
            crossed += 1
        disp = (max(p[0] for p in pts) - min(p[0] for p in pts)) + (max(ys) - min(ys))
        if disp < 0.1:
            stationary += 1
    n = len(tracks)
    cross_frac, stat_frac = crossed / n, stationary / n
    x_spread = (max(xs) - min(xs)) if xs else 0.0

    if avg_persons < 0.25:
        return "staff_area"
    if cross_frac >= 0.4:
        return "entry"
    if stat_frac >= 0.5 and x_spread < 0.5:
        return "billing"
    return "floor"


def auto_classify(source: str, vlm: Optional[VLMClassifier] = None) -> tuple[str, str]:
    """Return (role, method). Tries VLM on a sample frame, else the motion heuristic."""
    vlm = vlm or VLMClassifier()
    if vlm.available:
        try:
            import cv2

            cap = cv2.VideoCapture(source)
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(cap.get(cv2.CAP_PROP_FRAME_COUNT) * 0.3))
            ok, frame = cap.read()
            cap.release()
            if ok:
                role = vlm.classify_frame(frame)
                if role:
                    return role, "vlm"
        except Exception:
            pass
    return _heuristic_role(source), "heuristic"


def resolve_role(camera_id: str, source: str, layout, mode: str = "registry") -> tuple[str, str]:
    """Resolve a camera's role. Returns (role, method).

    mode: 'registry' (default; falls back to convention then 'floor'),
          'auto' (registry -> convention -> auto-classify).
    """
    cam = layout.cameras.get(camera_id) if layout else None
    if cam and getattr(cam, "role", None):
        return cam.role, "registry"
    by_conv = role_from_convention(source)
    if by_conv:
        return by_conv, "convention"
    if mode == "auto":
        return auto_classify(source)
    return "floor", "default"
