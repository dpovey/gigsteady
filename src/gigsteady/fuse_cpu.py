"""Temporal fusion for vibration blur on fixed cameras: selective, cached, tiled (CPU).

Each stabilised frame is cached with half-res luminance, a crispness map and an ECC input. For output frame t:

  1. Candidates: neighbours within +-radius at least min_gain (5%) crisper overall. None -> frame t as is.
  2. Align each candidate to frame t: sub-pixel ECC (shift + rotation) on lighting-normalised gradients at 960 px.
     (Dense optical flow, DIS, was dropped: on dark, hazy Log footage it reported 18-64 px row shifts where the true
     wobble is ~5 px, and flagged ~90% of the frame as moving.)
  3. Weights per pixel: crispness ratio ** sharp, capped x16 after the exponent (uncapped, ratio**8 reached ~1e9 and
     overrode the similarity gate), minus 1, times a similarity gate; zero wherever any candidate differs at coarse
     scale (sigma 8), dilated 31 px: moving areas are off limits (without it a waving hand came out with repeated
     fingers). Crispness is squared edge steepness after light smoothing (a band-pass score rewarded doubled edges).
  4. Blend at full size only in tiles with non-zero weight (bicubic resample of the candidate).

denoise > 0 also lets equally crisp neighbours in (classic temporal denoise, slightly softer).
"""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import cv2
import numpy as np

from .prep import prep
from .register import ecc

AW = 960
TILE = 240  # full-res px (4K); scaled with width


def smooth_half(im: np.ndarray, sigma: float) -> np.ndarray:
    """Wide Gaussian blur of a half-res map, computed at quarter res."""
    h, w = im.shape
    q = cv2.resize(im, (w // 2, h // 2), interpolation=cv2.INTER_AREA)
    return cv2.resize(cv2.GaussianBlur(q, (0, 0), sigma / 2), (w, h), interpolation=cv2.INTER_LINEAR)


class Frame:
    __slots__ = ("full", "Y", "E", "coarse", "crisp", "Q")

    def __init__(self, full: np.ndarray):
        fh, fw = full.shape[:2]
        self.full = full
        h = cv2.resize(full, (fw // 2, fh // 2), interpolation=cv2.INTER_AREA).astype(np.float32) / 65535.0
        self.Y = h @ np.array([0.2126, 0.7152, 0.0722], np.float32)
        g = cv2.GaussianBlur(self.Y, (0, 0), 1.5)
        self.E = smooth_half(cv2.Sobel(g, cv2.CV_32F, 1, 0) ** 2 + cv2.Sobel(g, cv2.CV_32F, 0, 1) ** 2, 6) + 1e-8
        self.coarse = smooth_half(self.Y, 8)
        k = self.E.size // 20
        self.crisp = float(np.partition(self.E, -k, axis=None)[-k:].mean())
        ah = int(round(fh * AW / fw / 2)) * 2
        self.Q = prep(cv2.resize(self.Y, (AW, ah), interpolation=cv2.INTER_AREA) * 255.0)


class Fuser:
    def __init__(self, radius=4, sharp=8.0, denoise=0.0, tau=0.06, min_gain=0.05, threads=8):
        self.radius, self.sharp, self.denoise, self.tau, self.min_gain = radius, sharp, denoise, tau, min_gain
        self.pool = ThreadPoolExecutor(threads)
        self.cache: "OrderedDict[int, Frame]" = OrderedDict()
        self.stats = {"frames": 0, "fused": 0, "tiles": 0, "pairs": 0}
        self.gx = self.gy = None

    def add(self, idx: int, warped_full: np.ndarray) -> None:
        if self.gx is None:
            fh, fw = warped_full.shape[:2]
            self.gx, self.gy = np.meshgrid(np.arange(fw, dtype=np.float32), np.arange(fh, dtype=np.float32))
        self.cache[idx] = Frame(warped_full)

    def drop_before(self, idx: int) -> None:
        for k in [k for k in self.cache if k < idx]:
            del self.cache[k]

    def output(self, t: int) -> np.ndarray:
        self.stats["frames"] += 1
        c = self.cache[t]
        js = [j for j in range(t - self.radius, t + self.radius + 1) if j != t and j in self.cache]
        if self.denoise == 0:
            js = [j for j in js if self.cache[j].crisp > c.crisp * (1 + self.min_gain)]
        if not js:
            return c.full
        FH, FW = c.full.shape[:2]
        HH, HW = c.Y.shape
        L = smooth_half(c.Y, 6) + 0.01
        warps, aligned = [], []
        for j in js:
            n = self.cache[j]
            self.stats["pairs"] += 1
            W, cc = ecc(c.Q, n.Q, None, np.eye(2, 3, dtype=np.float32))  # n(W x) ~ c(x), 960 px
            if W is None or cc < 0.5 or np.hypot(W[0, 2], W[1, 2]) > 4:
                W = np.eye(2, 3, dtype=np.float32)
            Wh = W.astype(np.float32).copy()
            Wh[:, 2] *= HW / AW
            warp_h = lambda im: cv2.warpAffine(im, Wh, (HW, HH), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP,  # noqa: E731
                                               borderMode=cv2.BORDER_REFLECT)
            aligned.append((warp_h(n.Y), warp_h(n.E), warp_h(n.coarse)))
            Wf = W.astype(np.float32).copy()
            Wf[:, 2] *= FW / AW
            warps.append(Wf)
        moving = np.zeros(c.Y.shape, bool)
        for _, _, Cw in aligned:
            moving |= np.abs(Cw - c.coarse) / L > self.tau
        moving = cv2.dilate(moving.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))).astype(bool)
        weights = []
        for Yw, Ew, _ in aligned:
            d = smooth_half(np.abs(Yw - c.Y), 3) / L
            sim = np.exp(-(d / self.tau) ** 2)
            sim[(sim < 0.3) | moving] = 0.0
            r = np.clip((Ew / c.E) ** self.sharp, 1 / 16, 16.0)
            weights.append((sim * np.where(r > 1, self.denoise + (r - 1), self.denoise * r)).astype(np.float32))
        total = np.sum(weights, axis=0)
        if total.max() < 0.02:
            return c.full
        self.stats["fused"] += 1
        out = c.full.copy()
        tile = max(16, int(round(TILE * FW / 3840 / 2)) * 2)
        th = tile // 2
        tiles = [(ty, tx) for ty in range(0, HH, th) for tx in range(0, HW, th) if total[ty:ty + th, tx:tx + th].max() >= 0.02]
        self.stats["tiles"] += len(tiles)

        def blend(tl):
            ty, tx = tl
            y0, x0 = ty * 2, tx * 2
            sl = (slice(y0, y0 + tile), slice(x0, x0 + tile))
            acc = c.full[sl].astype(np.float32)
            th_, tw_ = acc.shape[:2]
            tot = np.ones((th_, tw_), np.float32)
            for j, w, Wf in zip(js, weights, warps):
                wt = cv2.resize(w[ty:ty + th, tx:tx + th], (tw_, th_), interpolation=cv2.INTER_LINEAR)
                if wt.max() < 0.02:
                    continue
                gx, gy = self.gx[sl], self.gy[sl]
                mx = Wf[0, 0] * gx + Wf[0, 1] * gy + Wf[0, 2]
                my = Wf[1, 0] * gx + Wf[1, 1] * gy + Wf[1, 2]
                nt = cv2.remap(self.cache[j].full, mx, my, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)
                acc += nt.astype(np.float32) * wt[..., None]
                tot += wt
            out[sl] = np.clip(acc / tot[..., None], 0, 65535).astype(np.uint16)

        list(self.pool.map(blend, tiles))
        return out
