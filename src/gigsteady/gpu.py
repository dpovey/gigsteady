"""GPU (Apple Metal via PyTorch MPS) versions of the 4K warp and the temporal fusion.

Same algorithm as fuse.py (see its docstring); what moves to the GPU is the per-pixel work: the
4K stabilising warp, half-res luminance / crispness / coarse maps and their blurs, the neighbour
resampling, the weights and the blend. Sub-pixel ECC alignment of each neighbour stays on the CPU,
on 960 px images (cheap, and OpenCV's ECC has no GPU counterpart here).

Differences from the CPU path: the 4K warp is bicubic (grid_sample) rather than Lanczos-4, and the
motion-mask dilation is a 31 px square rather than an ellipse.
"""
from collections import OrderedDict

import cv2
import numpy as np
import torch
import torch.nn.functional as F

from .prep import prep
from .register import ecc

AW = 960

DEV = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
LUMA = torch.tensor([0.2126, 0.7152, 0.0722], device=DEV).view(1, 3, 1, 1)
SOBEL = torch.tensor([[[[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]]], [[[-1, -2, -1], [0, 0, 0], [1, 2, 1]]]],
                     dtype=torch.float32, device=DEV)
_kernels: dict = {}
CONTRAST_FLOOR = 0.005  # ~5 10-bit codes: below this, local contrast is noise


def gauss(x: torch.Tensor, sigma: float) -> torch.Tensor:
    """Separable Gaussian blur of (1,C,H,W), reflect padding."""
    if sigma not in _kernels:
        r = int(np.ceil(3 * sigma))
        k = torch.exp(-(torch.arange(-r, r + 1, dtype=torch.float32) ** 2) / (2 * sigma ** 2))
        _kernels[sigma] = (k / k.sum()).to(DEV)
    k = _kernels[sigma]
    r = (len(k) - 1) // 2
    c = x.shape[1]
    x = F.conv2d(F.pad(x, (r, r, 0, 0), mode="reflect"), k.view(1, 1, 1, -1).repeat(c, 1, 1, 1), groups=c)
    return F.conv2d(F.pad(x, (0, 0, r, r), mode="reflect"), k.view(1, 1, -1, 1).repeat(c, 1, 1, 1), groups=c)


def smooth_half(x: torch.Tensor, sigma: float) -> torch.Tensor:
    """Wide blur of a half-res map, computed at quarter res and scaled back."""
    q = F.avg_pool2d(x, 2)
    return F.interpolate(gauss(q, sigma / 2), size=x.shape[-2:], mode="bilinear", align_corners=False)


def _grid(h: int, w: int) -> torch.Tensor:
    ys, xs = torch.meshgrid(torch.arange(h, dtype=torch.float32, device=DEV),
                            torch.arange(w, dtype=torch.float32, device=DEV), indexing="ij")
    return torch.stack([xs, ys, torch.ones_like(xs)], -1)  # (h,w,3) pixel coords


_grids: dict = {}


def sample(img: torch.Tensor, M: np.ndarray, mode: str) -> torch.Tensor:
    """out(x) = img(M @ [x, 1]) for a 2x3 pixel-space matrix M (the 'inverse map' convention)."""
    h, w = img.shape[-2:]
    if (h, w) not in _grids:
        _grids[(h, w)] = _grid(h, w)
    src = _grids[(h, w)] @ torch.tensor(M, dtype=torch.float32, device=DEV).T  # (h,w,2)
    g = torch.stack([(src[..., 0] + 0.5) / w * 2 - 1, (src[..., 1] + 0.5) / h * 2 - 1], -1)[None]
    return F.grid_sample(img, g, mode=mode, padding_mode="reflection", align_corners=False)


def upload(f: np.ndarray) -> torch.Tensor:
    """(H,W,3) uint16 -> (1,3,H,W) float32 0..1 on the GPU."""
    t = torch.from_numpy(f.view(np.int16)).to(DEV).to(torch.int32) & 0xFFFF
    return (t.permute(2, 0, 1)[None].to(torch.float32) / 65535.0).contiguous()


def download(x: torch.Tensor) -> np.ndarray:
    """(1,3,H,W) float32 0..1 -> (H,W,3) uint16."""
    q = (x.clamp(0, 1) * 65535.0).round().to(torch.int32)[0].permute(1, 2, 0).contiguous()
    return q.cpu().numpy().astype(np.uint16)


def warp_full(full: torch.Tensor, A: np.ndarray) -> torch.Tensor:
    """Stabilising warp: A maps source -> output (cv2.warpAffine convention)."""
    return sample(full, cv2.invertAffineTransform(A), "bicubic").clamp(0, 1)


class Frame:
    __slots__ = ("full", "Y", "E", "coarse", "crisp", "Q")

    def __init__(self, full: torch.Tensor):
        self.full = full
        half = F.avg_pool2d(full, 2)
        self.Y = (half * LUMA).sum(1, keepdim=True)
        g = gauss(self.Y, 1.5)
        s = F.conv2d(F.pad(g, (1, 1, 1, 1), mode="reflect"), SOBEL)
        self.E = smooth_half((s ** 2).sum(1, keepdim=True), 6) + 1e-8
        self.coarse = smooth_half(self.Y, 8)
        ye = torch.cat([self.Y, self.E], 1)
        hh, hw = ye.shape[-2:]
        if hw == 2 * AW:  # 4K: quarter res is exactly 960 wide
            q = F.avg_pool2d(ye, 2)[0].cpu().numpy()
        else:
            ah = int(round(hh * AW / hw / 2)) * 2
            q = F.interpolate(ye, size=(ah, AW), mode="area")[0].cpu().numpy()
        k = q[1].size // 20
        self.crisp = float(np.partition(q[1], -k, axis=None)[-k:].mean())
        self.Q = prep(q[0] * 255.0)


class FuserGPU:
    def __init__(self, radius=4, sharp=8.0, denoise=0.0, tau=0.06, min_gain=0.05, contrast=0.4):
        self.radius, self.sharp, self.denoise, self.tau, self.min_gain = radius, sharp, denoise, tau, min_gain
        self.contrast = contrast
        self.cache: "OrderedDict[int, Frame]" = OrderedDict()
        self.stats = {"frames": 0, "fused": 0, "pairs": 0}

    def add(self, idx: int, warped_full: torch.Tensor) -> None:
        self.cache[idx] = Frame(warped_full)

    def drop_before(self, idx: int) -> None:
        for k in [k for k in self.cache if k < idx]:
            del self.cache[k]

    @torch.no_grad()
    def output(self, t: int) -> torch.Tensor:
        self.stats["frames"] += 1
        c = self.cache[t]
        js = [j for j in range(t - self.radius, t + self.radius + 1) if j != t and j in self.cache]
        if self.denoise == 0:
            js = [j for j in js if self.cache[j].crisp > c.crisp * (1 + self.min_gain)]
        if not js:
            return c.full
        FH, FW = c.full.shape[-2:]
        HH, HW = c.Y.shape[-2:]
        L = smooth_half(c.Y, 6) + 0.01
        warps, aligned = [], []
        for j in js:
            n = self.cache[j]
            self.stats["pairs"] += 1
            W, cc = ecc(c.Q, n.Q, None, np.eye(2, 3, dtype=np.float32))  # n(W x) ~ c(x), 960 px
            if W is None or cc < 0.5 or np.hypot(W[0, 2], W[1, 2]) > 4:
                W = np.eye(2, 3, dtype=np.float32)
            Wh = W.astype(np.float64).copy()
            Wh[:, 2] *= HW / AW
            aligned.append(sample(torch.cat([n.Y, n.E, n.coarse], 1), Wh, "bilinear"))
            Wf = W.astype(np.float64).copy()
            Wf[:, 2] *= FW / AW
            warps.append(Wf)
        moving = torch.zeros_like(c.Y, dtype=torch.bool)
        if self.contrast > 0:  # motion relative to local contrast: catches people moving behind haze (bright, flat)
            C = smooth_half((c.Y - c.coarse) ** 2, 8).sqrt() + CONTRAST_FLOOR
        for a in aligned:
            dc = (a[:, 2:3] - c.coarse).abs()
            moving |= dc / L > self.tau
            if self.contrast > 0:
                moving |= dc / C > self.contrast
        moving = F.max_pool2d(moving.float(), 31, stride=1, padding=15) > 0
        weights = []
        for a in aligned:
            d = smooth_half((a[:, 0:1] - c.Y).abs(), 3) / L
            sim = torch.exp(-(d / self.tau) ** 2)
            sim = torch.where((sim < 0.3) | moving, torch.zeros_like(sim), sim)
            r = ((a[:, 1:2] / c.E) ** self.sharp).clamp(1 / 16, 16.0)
            weights.append(sim * torch.where(r > 1, self.denoise + (r - 1), self.denoise * r))
        if float(torch.stack(weights).sum(0).max()) < 0.02:
            return c.full
        self.stats["fused"] += 1
        acc, tot = c.full.clone(), torch.ones_like(c.Y[:, :1])
        tot = F.interpolate(tot, size=(FH, FW), mode="nearest")
        for j, w, Wf in zip(js, weights, warps):
            if float(w.max()) < 0.02:
                continue
            wf = F.interpolate(w, size=(FH, FW), mode="bilinear", align_corners=False)
            acc += sample(self.cache[j].full, Wf, "bicubic").clamp(0, 1) * wf
            tot += wf
        return acc / tot
