import csv
import json
import random
import subprocess

import numpy as np

from conftest import read_code
from gigsteady import review


def test_review_clip_panels_are_frame_exact(coded_clips, tmp_path):
    out = tmp_path / "c.mp4"
    review.make_clip([(coded_clips["h264_25"], 100), (coded_clips["hevc_25"], 37)], 20, out, None)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    f = np.frombuffer(raw, np.uint8).reshape(-1, 1080, 1920)
    assert len(f) == 20
    assert [read_code(x[:540, :960]) for x in f] == list(range(100, 120))
    assert [read_code(x[:540, 960:]) for x in f] == list(range(37, 57))


def items(k):
    return [{"name": f"qqshot{i}", "n": 10, "panels": [{"version": "qqproto", "path": "p", "start": 0},
                                                     {"version": "qqnew", "path": "q", "start": 0}]} for i in range(k)]


def test_blind_ids_repeats_and_sides():
    shown = review.blind(items(16), random.Random(1), 3, shuffle_sides=True)
    assert len(shown) == 19
    assert [s["id"] for s in shown] == [f"S{i:02d}" for i in range(1, 20)]
    assert sum(1 for s in shown if s.get("repeat_of")) == 3
    pos = {}
    for i, s in enumerate(shown):
        if s["name"] in pos:
            assert i - pos[s["name"]] >= 4
        pos[s["name"]] = i
    left = [s["panels"][0]["version"] for s in shown]
    assert 0 < left.count("qqproto") < 19          # sides randomised, not fixed
    assert sorted(s["name"] for s in shown if not s.get("repeat_of")) == sorted(f"qqshot{i}" for i in range(16))


def test_page_hides_names_and_unblind_maps_sides(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "make_clip", lambda *a: None)
    idx = review.build("ab", items(5), tmp_path / "rv", "t", random.Random(2), 2, repeats=1)
    text = idx.read_text()
    assert not any(w in text for w in ("qqshot", "qqproto", "qqnew", "key.json"))
    key = json.loads((tmp_path / "rv" / "key.json").read_text())
    c = key["clips"][0]
    marks = tmp_path / "m.csv"
    marks.write_text(f'clip,pref,use,note,marked_at\n"{c["id"]}","B1","yes","",""\n')
    out = review.unblind(tmp_path / "rv", marks)
    row = next(csv.DictReader(out.open()))
    assert row["picked"] == c["right"]["version"] and row["shot"] == c["name"]
    assert marks.read_text().startswith("clip,pref")  # export untouched


def test_controls_show_original_on_both_sides():
    shown = review.blind(items(10), random.Random(3), 2, shuffle_sides=False, controls=3)
    ctl = [s for s in shown if s.get("control")]
    assert len(shown) == 15 and len(ctl) == 3
    for s in ctl:
        assert s["panels"][0]["path"] == s["panels"][1]["path"] and s["panels"][0]["start"] == s["panels"][1]["start"]
