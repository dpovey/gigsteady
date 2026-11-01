"""How still is the scenery? Independent of the method being graded where possible.

wobble     : lock-mode registration (masked ECC) of the clip to its own median: RMS distance from the median
             position, in SOURCE 4K px (divide by the output zoom). Same family as lock mode, so not independent.
pc_wobble  : the independent check: phase correlation of plain luminance against the median frame, central
             60% x 70%. Floor ~0.5 px on a locked shot.
jitter     : handheld yardstick: picture-only camera path minus its sigma-3-frame smoothing.
"""
import cv2
import numpy as np

from .. import io
from ..lock import analyse_lock
from ..probe import probe
from ..track import picture_steps, camera_path

KER = cv2.getGaussianKernel(19, 3).ravel()


def _k(path):
    return 3840 / 960 * probe(path).width / 3840


def smooth_rms(xy: np.ndarray) -> float:
    sm = np.column_stack([np.convolve(np.pad(xy[:, k], 9, mode="edge"), KER, "valid") for k in range(2)])
    return float(np.sqrt(((xy - sm) ** 2).sum(1).mean()))


def measure(path: str, start: int, n: int, zoom: float = 1.0) -> dict:
    g = io.decode_gray(path, start, n)
    k = probe(path).width / g.shape[2]
    r = analyse_lock(g)
    xy = r.p[:, :2] * k / zoom
    wob = np.linalg.norm(xy - np.median(xy, 0), axis=1)
    step = np.linalg.norm(np.diff(xy, axis=0), axis=1)
    sp, _, _ = picture_steps(g)
    cam = camera_path(sp)[:, :2] * k / zoom
    return {"wobble": float(np.sqrt((wob ** 2).mean())), "step_p90": float(np.percentile(step, 90)),
            "pc_wobble": pc_wobble(g, k / zoom), "jitter": smooth_rms(cam), "corr": float(np.median(r.corr))}


def pc_wobble(g: np.ndarray, k: float) -> float:
    n, ah, aw = g.shape
    crop = g[:, int(ah * 0.1):int(ah * 0.8), int(aw * 0.2):int(aw * 0.8)].astype(np.float32)
    ref = np.median(crop, axis=0)
    win = cv2.createHanningWindow(ref.shape[::-1], cv2.CV_32F)
    d = np.array([cv2.phaseCorrelate(ref, f, win)[0] for f in crop]) * k
    return float(np.sqrt(((d - np.median(d, axis=0)) ** 2).sum(1).mean()))
