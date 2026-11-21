"""eval/timeline_diff.py flag rules: framing reference under a composed crop, timing by majority, colour note."""
import importlib.util
from pathlib import Path

import numpy as np

_spec = importlib.util.spec_from_file_location(
    "timeline_diff", Path(__file__).resolve().parents[2] / "eval" / "timeline_diff.py")
td = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(td)


def test_composed_crop_is_the_editors_zoom_over_the_angles():
    angle = {"zoom": [1.05, 1.05], "rotation": 0.7}
    assert td.composed_crop({"ZoomX": 1.575, "ZoomY": 1.575, "RotationAngle": 0.7}, angle)   # SD 273742: a 1.5 crop
    assert not td.composed_crop({"ZoomX": 1.05, "ZoomY": 1.05, "RotationAngle": 0.7}, angle)  # the angle's own sizing
    assert not td.composed_crop({"ZoomX": 1.0, "ZoomY": 1.0}, None)
    assert not td.composed_crop(None)


def test_timing_needs_a_majority_on_one_nonzero_offset():
    assert td.timing_verdict([0, 0, -2, 0, 0]) is None          # PP 273155: one sample
    assert td.timing_verdict([-2]) is None                      # a single sample never flags
    assert td.timing_verdict([0, 0, 0, 0, 0]) is None
    assert td.timing_verdict([-1, -1, -1, 0, 0]) == -1
    assert td.timing_verdict([-1, 1, -2, 2, 0]) is None         # no agreement
    assert td.timing_verdict([]) is None


def test_colour_note_names_the_largest_shift():
    dc = np.zeros((3, 4))
    dc[2, 1] = -3.0
    dc[0, 0] = 1.5
    assert td.colour_note(dc) == "B p5 -3.0"


def _textures(n, seed=0):
    """n frames of one blurred-noise scene, each with its own blurred-noise detail, so neighbours register but
    each frame matches itself best."""
    import cv2
    rng = np.random.default_rng(seed)
    blur = lambda x: cv2.GaussianBlur(x, (0, 0), 4)  # noqa: E731
    base = blur(rng.normal(0, 1, (td.H, td.W, 3)).astype(np.float32))
    return [np.clip(128 + 900 * (base + 0.5 * blur(rng.normal(0, 1, (td.H, td.W, 3)).astype(np.float32))), 0, 255)
            .astype(np.uint8) for _ in range(n)]


def test_measure_placed_finds_a_one_frame_lead_from_the_streamed_window():
    master = _textures(14)
    stab = master[1:] + master[-1:]          # stab frame f shows master frame f+1
    got, fit, offs, cs, cm = td.measure_placed(zip(master, stab), 14, 4, 1.0)
    assert got == 14
    probes = td.probe_frames(14)
    assert len(offs) == len(probes) and all(o == 1 for o in offs)
    assert len(fit) == len(range(0, 14, 4))


def _old_loop(n, step):
    """The frames registered and, for each probe, the master frames compared, as the whole-cut loop did them."""
    probe = td.probe_frames(n)
    order = sorted(set(range(0, n, step)) | set(probe))
    return [(f, [f + o for o in range(-2, 3) if 0 <= f + o < n] if f in probe else None) for f in order]


def test_streamed_window_sees_the_same_frames_as_the_whole_cut_loop(monkeypatch):
    """Each frame's value is its index; record which stab frame is registered and which master frames it is warped
    against, and compare with the old whole-cut loop for short and long cuts and several steps."""
    seen = []

    def register(s, m, ref):
        seen.append((int(s[0, 0, 0]), []))
        return np.eye(2, 3, dtype=np.float32), 1.0

    def warp(m, w, size, flags=None):
        seen[-1][1].append(int(m[0, 0, 0]))
        return m
    monkeypatch.setattr(td, "register", register)
    monkeypatch.setattr(td.cv2, "warpAffine", warp)
    monkeypatch.setattr(td, "decompose", lambda w: (1.0, 1.0, 0.0, 0.0, 0.0))
    monkeypatch.setattr(td, "stats", lambda a, m: np.zeros((3, 4)))
    monkeypatch.setattr(td, "grey", lambda f, size=None: np.array([f[0, 0, 0], 1.0, 2.0], np.float32))
    for n in range(3, 30):
        frames = [np.full((td.H, td.W, 3), i, np.uint8) for i in range(n)]
        probes = set(td.probe_frames(n))
        for step in (1, 2, 3, 7):
            seen.clear()
            td.measure_placed(zip(frames, frames), n, step, 1.0)
            # on the step grid the last warp is the colour one, against the frame itself
            got = [(f, (ms[:-1] if f % step == 0 else ms) if f in probes else None) for f, ms in seen]
            assert got == _old_loop(n, step), (n, step)


def test_paired_stops_at_the_shorter_render_and_raises_on_a_failed_one(tmp_path):
    import shutil
    import subprocess
    import pytest
    if not shutil.which("ffmpeg"):
        pytest.skip("no ffmpeg")
    def clip(name, frames):
        p = tmp_path / name
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=320x180:rate=25",
                        "-frames:v", str(frames), "-pix_fmt", "yuv420p", str(p)], check=True)
        return str(p)
    a, b = clip("a.mp4", 10), clip("b.mp4", 6)
    got = list(td.paired(a, b, 0, 10))
    assert len(got) == 6 and got[0][0].shape == (td.H, td.W, 3)
    with pytest.raises(subprocess.CalledProcessError):
        list(td.paired(a, str(tmp_path / "missing.mp4"), 0, 10))


def test_diff_unchanged_matches_whole_array_mean_and_max():
    a, b = _textures(3, 1), _textures(3, 2)
    count, mean_abs, max_abs = td.diff_unchanged(zip(a, b))
    d = np.abs(np.stack(a).astype(np.int16) - np.stack(b).astype(np.int16))
    assert (count, mean_abs, max_abs) == (3, d.mean(), d.max())
