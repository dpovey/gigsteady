"""Gyro + picture fusion for the track path, when the camera recorded Sony rtmd gyro (FX3, FX30).

1. Gyro from the cache (cache.py), only the shot's window (+-1 s); integrated over each frame interval -> rotation
   (3 angles) per step.
2. Calibrate per shot against the picture: search the gyro/video time offset (+-80 ms, 2 ms steps) and fit
   picture step (dx, dy, rot) ~ [gyro angles, IBIS shift/roll change] @ M + b by least squares on confidently
   matched steps (correlation >= 0.6). M absorbs axis orientation, focal length and pixel pitch. IBIS is essential
   on IBIS-on footage: gyro alone explained R2 0.50/0.34 of dx/dy, gyro + IBIS 0.90/0.85. R2 < 0.5: not used.
3. Complementary filter on the cumulative path: the picture keeps the slow part (real image motion, parallax, gyro
   drift), the gyro the fast part (exact at high frequency, blind to light), crossover 4 frames. Steps the picture
   couldn't match (strobe, blackout, light jump) come from the gyro.
"""
from fractions import Fraction

import numpy as np

from . import cache


def interval_angles(t: np.ndarray, g: np.ndarray, edges: np.ndarray) -> np.ndarray:
    """Integrate angular velocity (deg/s) between consecutive edge times (s) -> (len(edges)-1, 3) deg."""
    cum = np.concatenate([[[0, 0, 0]], np.cumsum((g[1:] + g[:-1]) / 2 * np.diff(t)[:, None], axis=0)])
    at = np.column_stack([np.interp(edges, t, cum[:, k]) for k in range(3)])
    return np.diff(at, axis=0)


def ibis_steps(ib: np.ndarray, start: int, n: int, frame_ms: float) -> np.ndarray | None:
    """Per-step change of the mean IBIS imager shift (x, y in um) and roll over each frame. None if no IBIS."""
    if len(ib) == 0:
        return None
    f = np.round(ib[:, 0] / frame_ms).astype(int) - start
    out = np.full((n, 3), np.nan)
    for k in range(n):
        s = f == k
        t1, t2 = s & (ib[:, 1] == 1), s & (ib[:, 1] == 2)
        if t1.any():
            out[k, :2] = ib[t1, 3:5].mean(0) / 1000.0
        if t2.any():
            out[k, 2] = ib[t2, 5].mean()
    if (~np.isnan(out[:, 0])).sum() < 2:
        return None
    for c in range(3):
        g = ~np.isnan(out[:, c])
        out[:, c] = np.interp(np.arange(n), np.flatnonzero(g), out[g, c]) if g.any() else 0.0
    return np.diff(out, axis=0)


def calibrate(t, g, frame_t, dI, Y, ok):
    """Best (r2, offset s, M) over the offset search."""
    extra = [dI] if dI is not None else []
    best = None
    for off in np.arange(-0.08, 0.0801, 0.002):
        X = interval_angles(t, g, frame_t + off)
        A = np.column_stack([X, *extra, np.ones(len(X))])
        M, *_ = np.linalg.lstsq(A[ok], Y[ok], rcond=None)
        res = Y[ok] - A[ok] @ M
        r2 = float(np.mean(1 - (res ** 2).sum(0)[:2] / ((Y[ok] - Y[ok].mean(0)) ** 2).sum(0)[:2]))
        if best is None or r2 > best[0]:
            best = (r2, off, M)
    return best


def window(src: str, start: int, n: int, fps: Fraction):
    """Gyro samples (t s, deg/s) for the shot +-1 s, IBIS steps, frame start times."""
    fpsf = float(fps)
    t0, t1 = start / fpsf - 1.0, (start + n) / fpsf + 1.0
    d = cache.load(src, t0 * 1000, t1 * 1000)
    imu = d["imu"]
    t, g = imu[:, 0] / 1000.0, imu[:, 1:4]
    dI = ibis_steps(d["ibis"], start, n, 1000.0 / fpsf)
    return t, g, dI, (start + np.arange(n)) / fpsf


def gyro_fuser(src: str, start: int, n: int, fps: Fraction, crossover: float = 4.0):
    """A fuse_steps callable for track.analyse_track."""

    def fuse(steps: np.ndarray, corr: np.ndarray):
        rep = {"gyro": "none"}
        try:
            t, g, dI, frame_t = window(src, start, n, fps)
        except (FileNotFoundError, OSError) as e:
            return steps, {"gyro": f"unavailable: {e}"}
        if len(t) < 100:
            return steps, {"gyro": "no samples in window"}
        rep["ibis"] = dI is not None
        extra = [dI] if dI is not None else []
        Y, ok = steps[1:, :3], corr[1:] >= 0.6
        if ok.sum() < 20:
            return steps, {**rep, "gyro": "too few picture matches to calibrate"}
        r2, off, M = calibrate(t, g, frame_t, dI, Y, ok)
        rep.update({"gyro": "used" if r2 >= 0.5 else "rejected", "r2": round(float(r2), 3),
                    "offset_ms": round(float(off) * 1000, 1)})
        if r2 < 0.5:
            return steps, rep
        X = interval_angles(t, g, frame_t + off)
        G = np.column_stack([X, *extra, np.ones(len(X))]) @ M
        V = Y.copy()
        V[~ok] = G[~ok]
        pv, pg = np.cumsum(V, 0), np.cumsum(G, 0)
        k = np.exp(-0.5 * (np.arange(-int(3 * crossover), int(3 * crossover) + 1) / crossover) ** 2)
        k /= k.sum()
        lp = lambda p: np.column_stack([np.convolve(np.pad(p[:, i], len(k) // 2, mode="edge"), k, "valid")  # noqa: E731
                                        for i in range(p.shape[1])])
        fused = lp(pv) + (pg - lp(pg))
        out = steps.copy()
        out[1:, :3] = np.diff(np.vstack([[0, 0, 0], fused]), axis=0)
        rep["gyro_filled_steps"] = int((~ok).sum())
        return out, rep

    return fuse
