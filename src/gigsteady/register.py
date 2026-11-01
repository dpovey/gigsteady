"""Dense ECC registration (Euclidean: shift + rotation) and the parameter conventions.

Parameters per frame are (dx, dy, rot rad, scale) in analysis pixels. `to_matrix(*p)` is the 2x3 affine.
"""
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

CRIT = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 60, 1e-5)


def ecc(ref: np.ndarray, f: np.ndarray, mask: np.ndarray | None, init: np.ndarray):
    """2x3 warp W with f(W x) ~ ref(x), and the correlation; (None, 0.0) if ECC fails."""
    try:
        cc, W = cv2.findTransformECC(ref, f, init.astype(np.float32).copy(), cv2.MOTION_EUCLIDEAN, CRIT, mask, 5)
        return W, cc
    except cv2.error:
        return None, 0.0


def register(prepped: np.ndarray, ref: np.ndarray, mask: np.ndarray, workers: int = 8, min_cc: float = 0.3):
    """Per frame: (warp ref -> frame or None, correlation). Contiguous chunks run in parallel threads (OpenCV
    releases the GIL); within a chunk each frame warm-starts from the previous one, and retries from identity when
    the warm start fails."""
    def run(chunk):
        out, init = [], np.eye(2, 3, dtype=np.float32)
        for f in chunk:
            W, cc = ecc(ref, f, mask, init)
            if W is None or cc < min_cc:
                W2, cc2 = ecc(ref, f, mask, np.eye(2, 3, dtype=np.float32))
                if W2 is not None and cc2 > cc:
                    W, cc = W2, cc2
            ok = W is not None and cc >= min_cc
            out.append((W if ok else None, cc))
            if ok:
                init = W
        return out

    if len(prepped) == 0:
        return []
    chunks = np.array_split(np.arange(len(prepped)), min(workers, max(1, len(prepped) // 8)))
    with ThreadPoolExecutor(len(chunks)) as pool:
        parts = list(pool.map(lambda ix: run(prepped[ix]), chunks))
    return [r for part in parts for r in part]


def params(M: np.ndarray) -> tuple[float, float, float, float]:
    s = np.hypot(M[0, 0], M[1, 0])
    return M[0, 2], M[1, 2], np.arctan2(M[1, 0], M[0, 0]), s


def to_matrix(dx, dy, rot, s) -> np.ndarray:
    c, si = s * np.cos(rot), s * np.sin(rot)
    return np.array([[c, -si, dx], [si, c, dy]])


def fill_gaps(fits) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame (dx, dy, rot, scale) frame -> reference; frames that didn't register are interpolated from their
    neighbours (never trusted). All failed: identity, and good is all False."""
    p = np.full((len(fits), 4), np.nan)
    for i, (M, _) in enumerate(fits):
        if M is not None:
            p[i] = params(cv2.invertAffineTransform(M))
    good = ~np.isnan(p[:, 0])
    if not good.any():
        p[:] = (0.0, 0.0, 0.0, 1.0)
        return p, good
    idx = np.arange(len(p))
    for k in range(4):
        p[:, k] = np.interp(idx, idx[good], p[good, k])
    return p, good
