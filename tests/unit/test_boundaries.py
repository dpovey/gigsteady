"""Rule (c): the render check flags a zoom step at an overlay boundary where both sides show the same picture.

The fixtures are measured pair rows (scale, inliers) around every overlay boundary of six delivered songs. Only
song 1 at 3:02.0 (record 38900, x1.098) should fail: song 5's seven wide/tight cuts are
deliberate (x0.42-0.52, x2.3-2.4) and no other boundary steps."""
import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from gigsteady.eval import boundaries as bd

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "boundaries"


def load(name):
    d = json.loads((FIX / f"{name}.json").read_text())
    pairs = {int(k): (v[0] if v[0] is not None else float("nan"), v[1]) for k, v in d["pairs"].items()}
    return d, pairs


@pytest.mark.parametrize("song", ["song1", "song2", "song3", "song4", "song5", "song6"])
def test_six_deliveries(song):
    d, pairs = load(song)
    rows = bd.check(pairs, d["record_in"], d["overlays"])
    assert rows, "no boundary inside the render"
    fails = [(r["record"], r["step"]) for r in rows if r["fail"]]
    if song == "song1":
        assert [f[0] for f in fails] == [38900] and fails[0][1] == pytest.approx(1.10, abs=0.01)
    else:
        assert fails == []


def test_deliberate_wide_tight_cuts_are_same_picture_but_pass():
    d, pairs = load("song5")
    big = [r for r in bd.check(pairs, d["record_in"], d["overlays"]) if r["same_picture"] and r["step"]
           and abs(np.log(r["step"])) > bd.STEP_MAX]
    assert len(big) == 7


def test_step_and_window_rules():
    pairs = {k: (1.0, 200) for k in range(20)}
    pairs[10] = (1.10, 300)
    assert bd.step(pairs, 10) == pytest.approx(1.10)
    pairs[10] = (1.10, 10)                                               # another camera: not one picture
    assert bd.step(pairs, 10) is None
    rows = bd.check({k: (1.0, 200) for k in range(1, 20)} | {10: (1.10, 300)}, 1000,
                    [{"name": "x", "record_start": 1010, "record_end": 1050}])
    assert [(r["record"], r["fail"]) for r in rows] == [(1010, True)]    # the end lies past the render: skipped
    assert bd.check({k: (1.0, 200) for k in range(1, 20)} | {10: (1.01, 300)}, 1000,
                    [{"name": "x", "record_start": 1010, "record_end": 1011}])[0]["fail"] is False


def _scene(rng, w=1280, h=720):
    import cv2
    img = cv2.GaussianBlur(rng.integers(0, 255, (h, w), np.uint8), (0, 0), 3)
    return cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX)


def test_measure_pairs_reads_a_crop_step_on_a_real_encode(tmp_path):
    """30 frames of one slowly panning scene; from frame 15 the picture is a centre crop at 1.10 (a stabilised
    overlay next to its own source). The pair across frame 15 steps x1.10; the CLI fails on that boundary."""
    import cv2
    rng = np.random.default_rng(3)
    big = _scene(rng, 1400, 800)
    frames = []
    for k in range(30):
        f = big[40:760, 60 + k:1340 + k]
        if k >= 15:
            h, w = f.shape
            c = f[int(h * (1 - 1 / 1.1) / 2):int(h * (1 + 1 / 1.1) / 2), int(w * (1 - 1 / 1.1) / 2):int(w * (1 + 1 / 1.1) / 2)]
            f = cv2.resize(c, (w, h), interpolation=cv2.INTER_AREA)
        frames.append(f)
    mp4 = tmp_path / "step.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", "1280x720", "-r", "25", "-i", "-",
                    "-c:v", "libx264", "-crf", "16", "-pix_fmt", "yuv420p", "-y", str(mp4)],
                   input=b"".join(f.tobytes() for f in frames), check=True)
    cache = tmp_path / "pairs.csv"
    pairs = bd.measure_pairs(str(mp4), cache)
    assert sorted(pairs) == list(range(1, 30))
    assert pairs[15][0] == pytest.approx(1.10, abs=0.01) and pairs[15][1] >= bd.MIN_INLIERS
    assert bd.read_pairs(cache)[15][0] == pytest.approx(pairs[15][0], abs=1e-9)
    ov = tmp_path / "manifest.json"
    ov.write_text(json.dumps([{"name": "A_rec515", "status": "ok", "record_start": 515, "record_end": 600},
                              {"name": "skip", "status": "editor", "record_start": 505, "record_end": 515}]))
    assert bd.main([str(mp4), "--record-in", "500", "--overlays", str(ov), "--pairs", str(cache)]) == 1
    rows = bd.check(pairs, 500, bd.overlays_from(ov))
    assert [(r["record"], r["fail"]) for r in rows] == [(515, True)]


def test_overlays_from_a_place_state_reads_what_was_placed(tmp_path):
    st = tmp_path / "place_state.json"
    st.write_text(json.dumps({"placed": [{"name": "A", "record_start": 10, "record_end": 20},
                                         {"name": "old", "record_start": 30}]}))   # an older entry: skipped
    assert bd.overlays_from(st) == [{"name": "A", "record_start": 10, "record_end": 20}]
    st.write_text(json.dumps({"placed": [{"name": "old", "record_start": 30}]}))
    assert bd.main(["unused.mp4", "--record-in", "0", "--overlays", str(st)]) == 2   # nothing to check is not a pass
