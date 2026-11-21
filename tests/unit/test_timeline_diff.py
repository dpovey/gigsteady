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
    assert len(offs) == len(probes) and all(o == 1 for o in offs[:-1])  # the last probe may sit at the end
    assert len(fit) == len(range(0, 14, 4))


def test_diff_unchanged_matches_whole_array_mean_and_max():
    a, b = _textures(3, 1), _textures(3, 2)
    count, mean_abs, max_abs = td.diff_unchanged(zip(a, b))
    d = np.abs(np.stack(a).astype(np.int16) - np.stack(b).astype(np.int16))
    assert (count, mean_abs, max_abs) == (3, d.mean(), d.max())
