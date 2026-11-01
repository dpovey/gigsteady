"""Score track-mode planners against gyro + IBIS truth (cameras with gyro, e.g. the FX3).

True camera image motion per step = the gyro + IBIS prediction, calibrated per window against the picture
(R2 ~0.85-0.9 on FX3); moving performers don't enter it. Shake left in an output = true path + the correction a
method applies, scored as RMS of that minus its sigma-3 smoothing (the deliberate move), source 4K px.
Methods: none, gauss (picture), L1 (picture), L1 + gyro. L1 + gyro uses the same gyro as the truth, so its score
flatters it; the picture-only rows are the fair planner comparison.

Faster than the prototype's eval_gyro_truth.py: each window is decoded once and its picture steps computed once,
shared by all methods (the prototype ran three full analyses per window in software H.264 decode).
"""
import numpy as np

from .. import io
from ..gyro.fuse import calibrate, gyro_fuser, interval_angles, window
from ..probe import probe
from ..track import analyse_track, picture_steps
from .steady import smooth_rms


def score_window(src: str, start: int, n: int, budget: float = 1.10) -> dict:
    clip = probe(src)
    g = io.decode_gray(src, start, n)
    k = clip.width / g.shape[2]
    sp, good, corr = picture_steps(g)
    st = (sp, good, corr)
    res = {"gauss": analyse_track(g, k, clip.width, clip.height, budget, "gauss", steps=st),
           "L1": analyse_track(g, k, clip.width, clip.height, budget, "l1", steps=st),
           "L1+gyro": analyse_track(g, k, clip.width, clip.height, budget, "l1", steps=st,
                                    fuse_steps=gyro_fuser(src, start, n, clip.fps))}
    t, gy, dI, frame_t = window(src, start, n, clip.fps)
    Y, ok = sp[1:, :3], np.where(good, corr, 0.0)[1:] >= 0.6
    r2, off, M = calibrate(t, gy, frame_t, dI, Y, ok)
    X = interval_angles(t, gy, frame_t + off)
    Gs = np.column_stack([X, *([dI] if dI is not None else []), np.ones(len(X))]) @ M
    true = np.vstack([[0, 0], np.cumsum(Gs[:, :2], 0)])
    row = {"r2": r2, "none": smooth_rms(true * k)}
    for name, r in res.items():
        row[name] = smooth_rms((true + r.p[:, :2]) * k)
    return row


def main(argv: list[str]) -> None:
    src = argv[0]
    budget = float(argv[argv.index("--budget") + 1]) if "--budget" in argv else 1.10
    wins = [a for a in argv[1:] if ":" in a]
    print(f"{'window':>12} {'R2':>5} | {'none':>6} {'gauss':>6} {'L1':>6} {'L1+gyro':>8}   (shake left, 4K px; crop {budget})")
    rows = []
    for w in wins:
        s, n = map(int, w.split(":"))
        r = score_window(src, s, n, budget)
        rows.append(r)
        print(f"{w:>12} {r['r2']:5.2f} | {r['none']:6.2f} {r['gauss']:6.2f} {r['L1']:6.2f} {r['L1+gyro']:8.2f}", flush=True)
    med = {k: np.median([r[k] for r in rows]) for k in ("none", "gauss", "L1", "L1+gyro")}
    print(f"{'median':>12} {'':5} | {med['none']:6.2f} {med['gauss']:6.2f} {med['L1']:6.2f} {med['L1+gyro']:8.2f}")
