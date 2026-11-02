"""5-frame no-stabilisation identity clips through gigsteady's exact colour path, in chroma variants, for testing how
Resolve decodes them against the camera file (graded identity test).

Usage: .venv/bin/python scripts/identity_variants.py <out_dir>
Variants per camera: 444 (ProRes 4444 XQ-free prores_ks profile 4, yuv444p10le, no chroma subsampling) and
422dec (ProRes 422 HQ, chroma decimated at even columns without the [1 2 1] filter: exact for co-sited sources).
"""
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from gigsteady import colour as C  # noqa: E402
from gigsteady.io import read_rgb48  # noqa: E402
from gigsteady.probe import probe  # noqa: E402

SHOTS = (("PP_rec151212", "/Volumes/BOTTB SYD26/PHOTOPIT_ROAMING/0V6A0916.MP4", 195),
         ("SD_rec151717", "/Volumes/BOTTB SYD26/STATIC DESKCAM/3O1A7144.MP4", 61797))


def planes(rgb: np.ndarray, sub: bool) -> bytes:
    ycc = cv2.transform(rgb.astype(np.float32), np.hstack([C._M, C._OFF[:, None]]))
    ps = [ycc[..., 0], ycc[..., 1], ycc[..., 2]] if not sub else [ycc[..., 0], ycc[:, 0::2, 1], ycc[:, 0::2, 2]]
    return b"".join(np.clip(np.rint(p), 4, 1019).astype("<u2").tobytes() for p in ps)


out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
for name, src, start in SHOTS:
    c = probe(src)
    for var, pix, enc in (("444", "yuv444p10le", ["-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuv444p10le"]),
                          ("422dec", "yuv422p10le", ["-c:v", "prores_videotoolbox", "-profile:v", "hq",
                                                     "-pix_fmt", "p210le"])):
        dst = out / f"identity_v012_{var}_{name}.mov"
        p = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-f", "rawvideo", "-pix_fmt", pix, "-s",
                              f"{c.width}x{c.height}", "-r", "25", "-colorspace", "bt709", "-color_range", "tv",
                              "-i", "-", *enc, "-colorspace", "bt709", "-color_range", "tv", "-y", str(dst)],
                             stdin=subprocess.PIPE)
        for f in read_rgb48(src, start, 5):
            p.stdin.write(planes(f, var != "444"))
        p.stdin.close()
        assert p.wait() == 0
        print(dst)
