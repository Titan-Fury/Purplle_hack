# PROMPT: "Write pytest for the camera role resolver: (1) folder/filename convention
#          maps paths to entry/billing/staff_area/floor; (2) registry role wins over
#          everything; (3) the VLM provider fallback chain tries Gemini, then Groq, etc.,
#          returning the first valid label — without real network calls."
# CHANGES MADE: Mocked requests.post to fail for Gemini and succeed for Groq, asserting the
#          chain falls through and records last_provider. Used a tiny fake layout object
#          for the registry-priority test instead of loading the real JSON.
from __future__ import annotations

import types

import pytest

from pipeline import camera_resolver as cr


@pytest.mark.parametrize("path,expected", [
    ("clips/ST1008/entry/cam.mp4", "entry"),
    ("clips/ST1008/billing/counter.mp4", "billing"),
    ("footage/backroom_01.mp4", "staff_area"),
    ("a/b/floor-2.mp4", "floor"),
    ("random_name.mp4", None),
])
def test_role_from_convention(path, expected):
    assert cr.role_from_convention(path) == expected


def test_registry_role_wins(monkeypatch):
    cam = types.SimpleNamespace(camera_id="CAM_X", role="billing", source="x/entry/cam.mp4")
    layout = types.SimpleNamespace(cameras={"CAM_X": cam})
    # source path screams "entry", but the registry says billing and must win
    role, method = cr.resolve_role("CAM_X", cam.source, layout, mode="auto")
    assert role == "billing" and method == "registry"


def test_convention_used_when_not_in_registry():
    layout = types.SimpleNamespace(cameras={})
    role, method = cr.resolve_role("CAM_NEW", "clips/ST1008/billing/x.mp4", layout)
    assert role == "billing" and method == "convention"


def test_vlm_provider_fallback_chain(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("GROQ_API_KEY", "q")

    class _Resp:
        def __init__(self, word): self._w = word
        def json(self): return {"choices": [{"message": {"content": self._w}}]}

    calls = []

    def fake_post(url, **kw):
        calls.append(url)
        if "generativelanguage" in url:      # Gemini fails
            raise RuntimeError("gemini down")
        return _Resp("billing")              # Groq answers

    import requests
    monkeypatch.setattr(requests, "post", fake_post)

    vlm = cr.VLMClassifier()
    assert [p.name for p in vlm.providers] == ["gemini", "groq"]
    role = vlm.classify_b64("ZmFrZQ==")
    assert role == "billing"
    assert vlm.last_provider == "groq"       # fell through Gemini to Groq
    assert any("generativelanguage" in u for u in calls) and any("groq" in u for u in calls)
