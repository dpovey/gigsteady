"""Gyro on the Brisbane FX3 (C5691): ranged dump = prototype full dump; T1/T3 fused paths = prototype."""
import csv
from pathlib import Path

import numpy as np
import pytest

from gigsteady.gyro import cache
from gigsteady.pipeline import Options, analyse

SRC = "/Volumes/BOTTB/Footage/CAM A (Roaming)/C5691.MP4"
PROTO = Path("/Users/dean/src/personal/pit-stabilise/data")
FIX = Path(__file__).parent.parent / "fixtures/proto_paths"
pytestmark = [pytest.mark.footage, pytest.mark.skipif(not Path(SRC).exists(), reason="BOTTB not mounted")]


def test_cache_matches_full_dump():
    full = PROTO / "gyro/C5691.bin"
    if not full.exists():
        pytest.skip("prototype dump not present")
    d = cache.load(SRC, 530_000, 535_000)
    f = np.fromfile(full, "<f8").reshape(-1, 7)
    f = f[(f[:, 0] >= 530_000) & (f[:, 0] < 535_000)]
    assert len(d["imu"]) == len(f) and np.array_equal(d["imu"][:, 1:4], f[:, 1:4])
    assert np.abs(d["imu"][:, 0] - f[:, 0]).max() < 0.05


@pytest.mark.parametrize("name,start", [("T1_fused_l1", 13300), ("T3_fused_l1", 15400)])
def test_fused_track_parity(name, start):
    a = analyse(SRC, start, 350, Options(mode="track"))
    rows = list(csv.DictReader((FIX / f"{name}.csv").open()))
    ref = np.array([[float(r["dx_4k"]), float(r["dy_4k"])] for r in rows])
    assert a["report"]["gyro"] == "used" and a["report"]["r2"] >= 0.84 and a["report"]["ibis"]
    assert np.abs(a["p"][:, :2] * a["k"] - ref).max() < 0.5
