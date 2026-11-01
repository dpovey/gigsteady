"""Matching on structure, not brightness, and choosing what to match on.

All of this runs at analysis size (960 px wide), so the sigmas are in analysis pixels whatever the source size.
"""
import cv2
import numpy as np


def prep(f: np.ndarray) -> np.ndarray:
    """Lighting-normalised structure: gradient magnitude over local brightness, renormalised, so haze, beams and
    level changes mostly cancel."""
    g = cv2.GaussianBlur(f.astype(np.float32), (0, 0), 1.0)
    mag = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))
    local = cv2.GaussianBlur(g, (0, 0), 15) + 4.0
    m = mag / local
    return (m / (cv2.GaussianBlur(m, (0, 0), 25) + 1e-3)).clip(0, 8)


def static_mask(stack: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Top quarter of pixels by edge strength in the median over temporal std: textured and still (drum hardware,
    screen edges, stands), not dark empty crowd or moving hands."""
    score = cv2.GaussianBlur(ref / (stack.std(axis=0) + 0.3), (0, 0), 3)
    return (score > np.percentile(score, 75)).astype(np.uint8)


def light_states(g: np.ndarray) -> np.ndarray:
    """Label frames by lighting state: 2 clusters of log mean luma when the shot flips between levels
    (10th-90th percentile spread at least 1.4x, at least 5 frames in each); otherwise all 0."""
    lum = np.log(g.reshape(len(g), -1).mean(1) + 2)
    lo, hi = np.percentile(lum, [10, 90])
    if hi - lo < np.log(1.4):
        return np.zeros(len(g), int)
    c = np.array([lo, hi])
    for _ in range(20):
        lab = np.abs(lum[:, None] - c[None]).argmin(1)
        c = np.array([lum[lab == k].mean() if (lab == k).any() else c[k] for k in range(2)])
    return lab if min((lab == 0).sum(), (lab == 1).sum()) >= 5 else np.zeros(len(g), int)
