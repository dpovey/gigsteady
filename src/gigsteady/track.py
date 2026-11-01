"""Track mode, for handheld cameras: follow the camera frame to frame, then plan a smooth path.

Steps across a light jump or strobe (correlation < 0.4) are not trusted: they are filled from the neighbouring
steps, or from the gyro when the camera recorded one (gyro.fuse), so a lighting change can't kick the picture.

Smoother "l1" (default, paths.plan): the least-jerk path that never needs more crop than the budget; held still
where it can be, constant-speed pans, smooth eases between. "gauss": one Gaussian sigma for the whole shot,
lowered until the crop fits the budget (the method the prototype's approved V2 Photo Pit renders used).
"""
from dataclasses import dataclass, field
from typing import Callable

import cv2
import numpy as np

from .paths import plan
from .prep import prep
from .register import ecc, params
from .warp import needed_zoom


@dataclass
class TrackResult:
    p: np.ndarray          # (n, 4) correction frame -> planned path, analysis px
    good: np.ndarray       # (n,) step matched by the picture
    corr: np.ndarray       # (n,) step ECC correlation
    path: np.ndarray       # (n, 4) raw camera path
    steps: np.ndarray      # (n, 4) steps used (after filling / gyro fusion)
    report: dict = field(default_factory=dict)


def picture_steps(g: np.ndarray, min_cc: float = 0.4):
    """Frame-to-frame (dx, dy, rot, scale) steps prev -> cur, unmatched steps filled by interpolation."""
    n = len(g)
    P = np.stack([prep(f) for f in g])
    steps, corr = [np.eye(2, 3)], [1.0]
    for a, b in zip(P[:-1], P[1:]):
        m = (cv2.GaussianBlur(a, (0, 0), 3) > np.percentile(a, 60)).astype(np.uint8)
        W, cc = ecc(a, b, m, np.eye(2, 3, dtype=np.float32))
        steps.append(W if W is not None and cc >= min_cc else None)
        corr.append(cc)
    sp = np.array([params(W) if W is not None else (np.nan,) * 4 for W in steps])
    good = ~np.isnan(sp[:, 0])
    idx = np.arange(n)
    for k in range(4):
        sp[:, k] = np.interp(idx, idx[good], sp[good, k]) if good.any() else (1.0 if k == 3 else 0.0)
    return sp, good, np.array(corr)


def camera_path(sp: np.ndarray) -> np.ndarray:
    """Absolute path (first frame -> each frame) in parameter space (small-angle approximation)."""
    return np.column_stack([np.cumsum(sp[:, 0]), np.cumsum(sp[:, 1]), np.cumsum(sp[:, 2]), np.cumprod(sp[:, 3])])


def _correction(smooth: np.ndarray, path: np.ndarray) -> np.ndarray:
    return np.column_stack([smooth[:, 0] - path[:, 0], smooth[:, 1] - path[:, 1], smooth[:, 2] - path[:, 2],
                            smooth[:, 3] / path[:, 3]])


def gauss_smooth(path: np.ndarray, sig: float) -> np.ndarray:
    pad = int(3 * sig)
    ext = np.pad(path, ((pad, pad), (0, 0)), mode="reflect", reflect_type="odd")
    ker = cv2.getGaussianKernel(2 * pad + 1, sig).ravel()
    return np.column_stack([np.convolve(ext[:, k], ker, "valid") for k in range(4)])


def analyse_track(g: np.ndarray, k: float, fw: int, fh: int, budget: float = 1.10, smoother: str = "l1",
                  sigma: float = 10.0, fuse_steps: Callable | None = None, steps=None) -> TrackResult:
    """g: (n, h, w) uint8 luma at analysis size; k = full width / analysis width.
    fuse_steps(steps, corr_where_good) -> (steps, report), e.g. gyro fusion; optional.
    steps: precomputed picture_steps(g) output, to share between several planners."""
    n, ah, aw = g.shape
    sp, good, corr = steps if steps is not None else picture_steps(g)
    sp = sp.copy()
    report: dict = {}
    if fuse_steps is not None:
        sp, report = fuse_steps(sp, np.where(good, corr, 0.0))
    path = camera_path(sp)
    zoom = lambda c: needed_zoom(c, k, fw, fh)  # noqa: E731
    if smoother == "l1" and budget:
        eff = budget
        for _ in range(4):  # the margin maths is small-angle; tighten if the real crop overshoots
            c = _correction(plan(path, eff, aw, ah), path)
            if zoom(c) <= budget + 0.006:
                break
            eff = 1 + (eff - 1) * 0.9
        report["smoother"] = "l1"
        return TrackResult(c, good, corr, path, sp, report)
    c = _correction(gauss_smooth(path, sigma), path)
    if budget and zoom(c) > budget:
        lo, hi = 1.0, sigma  # bisect the strongest smoothing that fits the budget
        for _ in range(8):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if zoom(_correction(gauss_smooth(path, mid), path)) <= budget else (lo, mid)
        sigma = lo
        c = _correction(gauss_smooth(path, sigma), path)
    report.update(smoother="gauss", sigma=round(float(sigma), 2))
    return TrackResult(c, good, corr, path, sp, report)
