# PROMPT: "Write offline pytest for the zero-config clip-folder ingest: (1) discover_clips
#          finds videos recursively and ignores non-video files, sorted; (2) synthesize_layout
#          maps convention paths to entry/billing/floor, gives the entry camera the default
#          door-line, and full-frame zones to floor/billing; (3) when no clip resolves to
#          'entry', the first clip is promoted so entry counting still fires."
# CHANGES MADE: Used only convention-named paths so the resolver never touches YOLO/VLM
#          (keeps the test fast and network-free). Asserted the fallback door-line geometry
#          and is_billing flag explicitly.
from __future__ import annotations

from pipeline.ingest import (
    DEFAULT_ENTRY_LINE,
    discover_clips,
    synthesize_layout,
)


def test_discover_clips_recursive_and_filtered(tmp_path):
    (tmp_path / "entry").mkdir()
    (tmp_path / "entry" / "door.mp4").write_bytes(b"x")
    (tmp_path / "floor_cam.MOV").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("ignore me")
    (tmp_path / "thumb.jpg").write_bytes(b"x")
    found = discover_clips(str(tmp_path))
    assert len(found) == 2
    assert all(f.lower().endswith((".mp4", ".mov")) for f in found)
    assert found == sorted(found)


def test_synthesize_roles_zones_and_default_entry_line():
    clips = [
        "clips/X/entry/door.mp4",
        "clips/X/billing/counter.mp4",
        "clips/X/floor/aisle.mp4",
    ]
    lay = synthesize_layout("RANDOM_STORE", clips)
    roles = {c.role for c in lay.cameras.values()}
    assert roles == {"entry", "billing", "floor"}

    entry = next(c for c in lay.cameras.values() if c.role == "entry")
    assert entry.entry_line == DEFAULT_ENTRY_LINE
    assert entry.inbound_direction == "left"

    # floor + billing get a full-frame zone; entry does not
    assert len(lay.zones) == 2
    billing = next(z for z in lay.zones.values() if z.is_billing)
    assert len(billing.polygon) == 4
    assert not any(z.camera_id == entry.camera_id for z in lay.zones.values())


def test_synthesize_promotes_first_clip_when_no_entry():
    clips = ["clips/X/floor/a.mp4", "clips/X/floor/b.mp4"]
    lay = synthesize_layout("NO_ENTRY_STORE", clips)
    cams = list(lay.cameras.values())
    assert cams[0].role == "entry"
    assert cams[0].entry_line == DEFAULT_ENTRY_LINE
    # the promoted camera's floor zone is dropped
    assert not any(z.camera_id == cams[0].camera_id for z in lay.zones.values())
