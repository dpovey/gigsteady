"""Lock mode, for cameras meant to be fixed: register every frame to the shot's own temporal-median reference.

The camera doesn't move on purpose, so the right output is the scenery held still, not a smoothed path. That turns
stabilisation into registration against a fixed reference, which is what makes it robust to lighting.

  1. Reference = per-pixel temporal median of the prepped frames (performers and lights move; the set stays).
  2. Mask = textured and still pixels (prep.static_mask). Dense ECC inside the mask, warm-started.
  3. Two passes: the second rebuilds reference and mask from frames aligned by the first.
  4. Shots that flip between lighting levels get one reference per level. The levels are tied by the fixed-camera
     assumption (each level's median position = the main level's), never by matching their pictures: a bright
     reference matched to a dark one was biased by 3.6 px and the picture hopped on every flip.
     Limit: the true median positions of the states differ by sampling noise, about 1.25 x shake rms / sqrt(frames
     in the smaller state). For Static Desk (wobble 0.4-1.2 px at 4K) that is ~0.1-0.2 px.
  5. Frames that don't register take a transform interpolated from their neighbours.
"""
from dataclasses import dataclass

import cv2
import numpy as np

from .prep import light_states, prep, static_mask
from .register import fill_gaps, register, to_matrix


@dataclass
class LockResult:
    p: np.ndarray        # (n, 4) correction frame -> reference, analysis px
    good: np.ndarray     # (n,) registered
    corr: np.ndarray     # (n,) ECC correlation
    mask: np.ndarray     # main-state mask, uint8 0/255
    states: np.ndarray   # (n,) lighting state per frame


def analyse_lock(g: np.ndarray) -> LockResult:
    """g: (n, h, w) uint8 luma at analysis size."""
    n, h, w = g.shape
    P = np.stack([prep(f) for f in g])
    lab = light_states(g)
    main = np.bincount(lab).argmax()
    p = np.zeros((n, 4))
    p[:, 3] = 1.0
    fits = [(None, 0.0)] * n
    masks = {}
    for pass_ in range(2):
        refs = {}
        for k in np.unique(lab):
            sel = P[lab == k]
            if pass_ == 1:
                sel = np.stack([cv2.warpAffine(f, to_matrix(*q), (w, h), borderMode=cv2.BORDER_REPLICATE)
                                for f, q in zip(sel, p[lab == k])])
            refs[k] = np.median(sel, axis=0)
            masks[k] = static_mask(sel, refs[k])
        fits = [(None, 0.0)] * n
        for k in refs:
            idx = np.flatnonzero(lab == k)
            for i, fit in zip(idx, register(P[idx], refs[k], masks[k])):
                fits[i] = fit
        p, good = fill_gaps(fits)
        for k in refs:
            if k != main and (good & (lab == k)).any() and (good & (lab == main)).any():
                off = np.median(p[good & (lab == k)], 0) - np.median(p[good & (lab == main)], 0)
                off[3] = 0.0
                p[lab == k] -= off
    return LockResult(p, good, np.array([f[1] for f in fits]), masks[main] * 255, lab)
