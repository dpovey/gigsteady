"""Exact RGB -> Y'CbCr for the encoder: BT.709, 10-bit video range, 4:2:2 with co-sited chroma.

ffmpeg's own rgb48 -> 10-bit video-range conversion adds about +1 luma code (it scales as if full range were 0-1020),
which the Log LUTs in the grade amplify to ~1-2 8-bit codes. Doing the arithmetic here removes that.
"""
import cv2
import numpy as np

KR, KB = 0.2126, 0.0722
KG = 1 - KR - KB
# rows: Y', Cb, Cr from R, G, B in [0, 1]; video range 10-bit
_M = np.array([[KR, KG, KB],
               [-KR / (2 * (1 - KB)), -KG / (2 * (1 - KB)), 0.5],
               [0.5, -KG / (2 * (1 - KR)), -KB / (2 * (1 - KR))]])
_M = (_M * np.array([[876], [896], [896]]) / 65535).astype(np.float32)
_OFF = np.array([64, 512, 512], np.float32)


def rgb48_to_yuv422p10(frame: np.ndarray) -> bytes:
    """(H, W, 3) uint16 RGB -> planar yuv422p10le bytes (Y, then Cb, then Cr)."""
    h, w, _ = frame.shape
    ycc = cv2.transform(frame.astype(np.float32), np.hstack([_M, _OFF[:, None]]))  # (H, W, 3) float32
    y = ycc[..., 0]
    # 4:2:2 co-sited: chroma at even columns, [1 2 1]/4 horizontally
    c = cv2.filter2D(ycc[..., 1:], -1, np.array([[0.25, 0.5, 0.25]], np.float32), borderType=cv2.BORDER_REFLECT)
    cb, cr = c[:, 0::2, 0], c[:, 0::2, 1]
    out = [np.clip(np.rint(p), 4, 1019).astype("<u2") for p in (y, cb, cr)]
    return b"".join(p.tobytes() for p in out)

_MI = np.linalg.inv(np.array([[KR, KG, KB],
                              [-KR / (2 * (1 - KB)), -KG / (2 * (1 - KB)), 0.5],
                              [0.5, -KG / (2 * (1 - KR)), -KB / (2 * (1 - KR))]])).astype(np.float32)


def yuv422p10_to_rgb48(buf: bytes, w: int, h: int, full: bool) -> np.ndarray:
    """Planar yuv422p10le (BT.709, co-sited chroma) -> (H, W, 3) uint16 RGB. full: 0-1023 range, else 64-940/64-960."""
    a = np.frombuffer(buf, "<u2")
    y = a[:w * h].reshape(h, w).astype(np.float32)
    c = a[w * h:].reshape(2, h, w // 2).astype(np.float32)
    up = np.empty((2, h, w), np.float32)
    up[:, :, 0::2] = c
    up[:, :, 1:-1:2] = 0.5 * (c[:, :, :-1] + c[:, :, 1:])
    up[:, :, -1] = c[:, :, -1]
    if full:
        yn, cb, cr = y / 1023, (up[0] - 512) / 1023, (up[1] - 512) / 1023
    else:
        yn, cb, cr = (y - 64) / 876, (up[0] - 512) / 896, (up[1] - 512) / 896
    rgb = cv2.transform(np.dstack([yn, cb, cr]), _MI * 65535)
    return np.clip(np.rint(rgb), 0, 65535).astype(np.uint16)
