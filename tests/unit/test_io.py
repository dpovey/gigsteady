from fractions import Fraction

import numpy as np
import pytest

from conftest import read_code
from gigsteady import io
from gigsteady.probe import frames_to_tc, probe, tc_to_frames


@pytest.mark.parametrize("name", ["h264_25", "h264_2997", "hevc_25"])
@pytest.mark.parametrize("start", [0, 1, 47, 48, 49, 137, 290])
def test_ranged_decode_is_frame_exact(coded_clips, name, start):
    g = io.decode_gray(coded_clips[name], start, 5, width=640)
    assert [read_code(f) for f in g] == list(range(start, min(start + 5, 300)))


@pytest.mark.parametrize("name", ["h264_2997", "hevc_25"])
def test_rgb_reader_matches_and_counts(coded_clips, name):
    frames = list(io.read_rgb48(coded_clips[name], 100, 4))
    assert len(frames) == 4 and frames[0].dtype == np.uint16
    assert [read_code((f[..., 1] >> 8).astype(np.uint8)) for f in frames] == [100, 101, 102, 103]


def test_rgb_reader_raises_past_end(coded_clips):
    with pytest.raises(RuntimeError, match="decoder ended"):
        list(io.read_rgb48(coded_clips["h264_25"], 298, 5))


def test_probe(coded_clips):
    c = probe(coded_clips["h264_2997"])
    assert c.fps == Fraction(30000, 1001) and (c.width, c.height) == (640, 360) and c.frames == 300


def test_prores_roundtrip(coded_clips, tmp_path):
    out = str(tmp_path / "o.mov")
    w = io.ProResWriter(out, 640, 360, Fraction(25), timecode="01:00:00:10")
    for f in io.read_rgb48(coded_clips["hevc_25"], 10, 6):
        w.write(f)
    w.close()
    c = probe(out)
    assert c.codec == "prores" and c.frames == 6 and c.timecode == "01:00:00:10"
    assert [read_code(f) for f in io.decode_gray(out, 0, 6, width=640)] == list(range(10, 16))


def test_timecode_maths():
    assert tc_to_frames("01:00:00:10", Fraction(25)) == 90010
    assert frames_to_tc(90010, Fraction(25)) == "01:00:00:10"
