"""Reference shots with known results (pit-stabilise README). Needs the media drives: pytest -m footage."""
import csv
import json
from pathlib import Path

import numpy as np
import pytest

from gigsteady import io
from gigsteady.lock import analyse_lock
from gigsteady.pipeline import Options, stabilise
from gigsteady.probe import probe
from gigsteady.track import analyse_track

FIX = Path(__file__).parent.parent / "fixtures"
SD = "/Volumes/BOTTB SYD26/STATIC DESKCAM/3O1A7144.MP4"
PIT = "/Volumes/BOTTB SYD26/PHOTOPIT_ROAMING/0V6A0922.MP4"
DEV = Path("/Volumes/BOTTB SYD26/gigsteady/dev")
pytestmark = [pytest.mark.footage, pytest.mark.skipif(not Path(SD).exists(), reason="BOTTB SYD26 not mounted")]


def proto(name):
    rows = list(csv.DictReader((FIX / "proto_paths" / f"{name}.csv").open()))
    return int(rows[0]["frame"]), len(rows), np.array([[float(r["dx_4k"]), float(r["dy_4k"])] for r in rows])


@pytest.mark.parametrize("name", ["SD_unstab_308139", "SD_render8", "SD_render7", "SD_unstab_320904"])
def test_lock_path_parity(name):
    start, n, ref = proto(name)
    r = analyse_lock(io.decode_gray(SD, start, n))
    assert np.abs(r.p[:, :2] * 4 - ref).max() < 0.5


@pytest.mark.parametrize("name,shots,smoother", [("PP_rec297197", "v2_photopit_shots.json", "gauss"),
                                                 ("HARD00_src26401", "pit_hard_shots.json", "l1")])
def test_track_path_parity(name, shots, smoother):
    s = next(x for x in json.loads((FIX / shots).read_text()) if x["name"] == name)
    _, _, ref = proto(name)
    c = probe(s["src"])
    g = io.decode_gray(s["src"], s["start"], s["n"])
    r = analyse_track(g, c.width / g.shape[2], c.width, c.height, budget=1.10, smoother=smoother)
    assert np.abs(r.p[:, :2] * 4 - ref).max() < 0.5


def test_sd_unstab_308139_burst_and_wobble():
    from gigsteady.eval.sharp import compare
    from gigsteady.eval.steady import measure
    plain, fused = DEV / "t_308139_plain.mov", DEV / "t_308139_fuse.mov"
    stabilise(SD, 218179, 218338, plain, Options(handles=0, fuse=0))
    rep = stabilise(SD, 218179, 218338, fused, Options(handles=0))
    assert rep["mode"] == "lock" and rep["zoom"] <= 1.03 and rep["fusion"]["fused"] >= 35
    r = compare([str(plain), str(fused)])
    s0, s1 = r[str(plain)]["sharp"], r[str(fused)]["sharp"]
    assert s0[122:124].max() < 75 and s1[122:124].min() >= 88       # prototype: 70 -> 92
    assert np.median(s1) >= 0.98 * np.median(s0)
    m = measure(str(fused), 0, 159, rep["zoom"])
    assert m["wobble"] <= 0.45 and m["pc_wobble"] <= 0.7               # original 5.4 / 5.2; prototype 0.36 / 0.56
