"""Ranged decode = sequential decode on the real camera files (Sony FX3 H.264 4:2:2, Canon R6 II / R5 HEVC)."""
from pathlib import Path

import numpy as np
import pytest

from gigsteady import io

FILES = ["/Volumes/BOTTB/Footage/CAM A (Roaming)/C5691.MP4",
         "/Volumes/BOTTB SYD26/PHOTOPIT_ROAMING/0V6A0922.MP4",
         "/Volumes/BOTTB SYD26/STATIC DESKCAM/3O1A7144.MP4"]


@pytest.mark.footage
@pytest.mark.parametrize("path", FILES)
def test_ranged_equals_sequential(path):
    if not Path(path).exists():
        pytest.skip("drive not mounted")
    seq = io.decode_gray(path, 0, 80).astype(np.int16)
    for start in (13, 37, 61):
        r = io.decode_gray(path, start, 5).astype(np.int16)
        diffs = [np.abs(r[0] - seq[start + d]).mean() for d in (-1, 0, 1)]
        assert diffs[1] < 0.5 and diffs[1] < min(diffs[0], diffs[2]) / 3, (start, diffs)
