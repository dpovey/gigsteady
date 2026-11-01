"""Crop maths and the full-resolution stabilising warp.

Correction params p[t] = (dx, dy, rot, scale) map frame t to the stabilised frame, in analysis pixels; `k` =
full width / analysis width scales the shifts up. The output is zoomed about the centre by one fixed zoom for the
whole shot, just big enough that no edge shows.
"""
import cv2
import numpy as np

from .register import to_matrix

ZOOM_STEP, ZOOM_MAX = 0.005, 1.6


def full_matrices(p: np.ndarray, k: float) -> np.ndarray:
    return np.stack([to_matrix(q[0] * k, q[1] * k, q[2], q[3]) for q in p])


def needed_zoom(p: np.ndarray, k: float, fw: int, fh: int) -> float:
    """Smallest centred zoom (on a 0.005 grid, plus one step of margin) whose crop stays inside every frame warped
    by p. Same answer as the prototype's per-frame search, vectorised."""
    zs = np.arange(1.0, ZOOM_MAX, ZOOM_STEP)
    corners = np.array([[0, 0], [fw, 0], [0, fh], [fw, fh]], float)
    c = (corners[None] - [fw / 2, fh / 2]) / zs[:, None, None] + [fw / 2, fh / 2]   # (z, 4, 2) output corners
    need = 1.0
    for M in full_matrices(p, k):
        inv = cv2.invertAffineTransform(M)
        src = c @ inv[:, :2].T + inv[:, 2]                                          # (z, 4, 2) in source
        ok = ((src[..., 0] >= 0) & (src[..., 0] <= fw) & (src[..., 1] >= 0) & (src[..., 1] <= fh)).all(1)
        z = zs[np.argmax(ok)] if ok.any() else ZOOM_MAX
        need = max(need, z)
    return round(need + ZOOM_STEP, 3)


def zoom_matrix(zoom: float, fw: int, fh: int) -> np.ndarray:
    return np.array([[zoom, 0, fw / 2 * (1 - zoom)], [0, zoom, fh / 2 * (1 - zoom)], [0, 0, 1]])


def output_matrices(p: np.ndarray, k: float, zoom: float, fw: int, fh: int) -> np.ndarray:
    """2x3 source -> output (cv2.warpAffine convention) per frame: correction, then the fixed zoom."""
    Z = zoom_matrix(zoom, fw, fh)
    return np.stack([(Z @ np.vstack([M, [0, 0, 1]]))[:2] for M in full_matrices(p, k)])


def warp_cpu(frame: np.ndarray, A: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    return cv2.warpAffine(frame, A, (w, h), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
