import subprocess
from pathlib import Path

import numpy as np
import pytest

W, H, BITS = 640, 360, 10


def code_frame(k: int) -> np.ndarray:
    """A frame whose index is written as 10 black/white bars (readable after lossy compression)."""
    f = np.full((H, W), 128, np.uint8)
    bw = W // BITS
    for b in range(BITS):
        f[: H // 2, b * bw:(b + 1) * bw] = 235 if (k >> b) & 1 else 16
    return f


def read_code(f: np.ndarray) -> int:
    h, w = f.shape
    bw = w / BITS
    return sum(1 << b for b in range(BITS) if f[: h // 4, int(b * bw + bw / 4):int((b + 1) * bw - bw / 4)].mean() > 128)


@pytest.fixture(scope="session")
def coded_clips(tmp_path_factory) -> dict:
    """Synthetic clips with frame indices burnt in: H.264 25p, H.264 29.97p, HEVC 25p (B-frames, GOP 48)."""
    d = tmp_path_factory.mktemp("coded")
    n = 300
    raw = b"".join(code_frame(k).tobytes() for k in range(n))
    out = {}
    for name, codec, rate in (("h264_25", "libx264", "25"), ("h264_2997", "libx264", "30000/1001"),
                              ("hevc_25", "libx265", "25")):
        p = d / f"{name}.mp4"
        extra = ["-x265-params", "log-level=error:keyint=48:bframes=4"] if codec == "libx265" else ["-g", "48", "-bf", "3"]
        subprocess.run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{W}x{H}", "-r", rate,
                        "-i", "-", "-c:v", codec, *extra, "-pix_fmt", "yuv420p", "-tag:v",
                        "hvc1" if codec == "libx265" else "avc1", "-y", str(p)], input=raw, check=True)
        out[name] = str(p)
    return out
