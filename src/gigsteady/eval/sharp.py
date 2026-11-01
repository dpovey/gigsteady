"""Vibration blur: noise-robust edge steepness on static edges, per frame (prototype fuse_check / burst_check).

Masks come from the first clip (the plain render): static edges = strong Laplacian energy in the median and low
temporal std; flat = weak edges, low std (for noise). Sharpness = 95th percentile of gradient magnitude on static
edges after sigma-1.5 smoothing at half res, so removing noise doesn't read as lost detail. "Blurred" = under 90%
of the first clip's median. Compare clips through the same encoder: hardware ProRes reads ~5% sharper.
"""
import subprocess

import cv2
import numpy as np

W, H = 1920, 1080


def frames(path: str, n: int = 10 ** 6, start: int = 0) -> np.ndarray:
    ss = ["-ss", f"{start / 25:.3f}"] if start else []
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", *ss, "-i", path, "-frames:v", str(n),
                          "-vf", f"scale={W}:{H},format=gray16le", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint16).reshape(-1, H, W).astype(np.float32) / 65535.0


def masks(A: np.ndarray):
    med, std = np.median(A, axis=0), A.std(axis=0)
    grad = cv2.GaussianBlur(cv2.Laplacian(med, cv2.CV_32F) ** 2, (0, 0), 4)
    st = cv2.GaussianBlur(std, (0, 0), 4)
    static_edges = (grad > np.percentile(grad, 85)) & (st < np.percentile(st, 50))
    flat = (grad < np.percentile(grad, 30)) & (st < np.percentile(st, 40))
    moving = st > np.percentile(st, 90)
    return static_edges, flat, moving


def sharpness(V: np.ndarray, m: np.ndarray) -> np.ndarray:
    out = []
    for f in V:
        g = cv2.GaussianBlur(f, (0, 0), 1.5)
        out.append(np.percentile(cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0), cv2.Sobel(g, cv2.CV_32F, 0, 1))[m], 95))
    return np.array(out) * 1e3


def noise(V: np.ndarray, flat: np.ndarray) -> float:
    return float(np.median([(f - cv2.GaussianBlur(f, (0, 0), 1.5))[flat].std() for f in V]) * 1e3)


def compare(paths: list[str], n: int = 10 ** 6) -> dict:
    V = [frames(p, n) for p in paths]
    n = min(len(v) for v in V)
    V = [v[:n] for v in V]
    se, flat, _ = masks(V[0])
    S = [sharpness(v, se) for v in V]
    base = 0.9 * np.median(S[0])
    return {p: {"sharp": s, "blurred": np.flatnonzero(s < base).tolist(), "noise": noise(v, flat)}
            for p, s, v in zip(paths, S, V)}
