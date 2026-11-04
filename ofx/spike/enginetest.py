"""Compare enginetest's renders with the CLI's own warp of the same frames (warp.output_matrices + cv2 Lanczos), and
its analysis with a CLI path CSV if one is given. Run from the repo's venv:
  python ofx/spike/enginetest.py <clip> <outdir> [cli_path.csv]
"""
import json
import sys
from pathlib import Path

import numpy as np

from gigsteady import io
from gigsteady.warp import output_matrices, warp_cpu


def main() -> None:
    clip, out = sys.argv[1], Path(sys.argv[2])
    a = json.loads((out / "analysis.json").read_text())
    r, t0 = a["result"], a["t0"]
    p = np.array(r["p"])
    print(f"engine analysis: {r['mode']} zoom {r['zoom']} frames {t0}-{t0 + len(p) - 1}")
    if len(sys.argv) > 3:
        c = np.genfromtxt(sys.argv[3], delimiter=",", names=True)
        i = np.searchsorted(c["frame"], t0)
        n = min(len(p), len(c) - i)
        print(f"vs CLI path: max |dx| {np.abs(p[:n, 0] - c['dx_4k'][i:i + n]).max():.3f} px, "
              f"max |dy| {np.abs(p[:n, 1] - c['dy_4k'][i:i + n]).max():.3f} px, "
              f"shake range {np.ptp(c['dx_4k']):.2f} x {np.ptp(c['dy_4k']):.2f} px")
    w, h = r["width"], r["height"]
    A = output_matrices(p, 1.0, r["zoom"], w, h)
    m = 64  # ignore the border, where Lanczos and bilinear treat edges differently
    for f in sorted(out.glob("render_*.f32")):
        frame = int(f.stem.split("_")[1])
        mine = np.fromfile(f, np.float32).reshape(h, w, 4)[..., :3]
        src = next(io.read_rgb48(clip, frame, 1)).astype(np.float32) / 65535
        ref = warp_cpu(src, A[frame - t0])
        unwarped = src
        d = np.abs(mine - ref)[m:-m, m:-m]
        d0 = np.abs(mine - unwarped)[m:-m, m:-m]
        print(f"frame {frame}: vs CLI warp mean {d.mean():.5f} p99 {np.percentile(d, 99):.4f}; "
              f"vs unwarped source mean {d0.mean():.5f} (zoom {r['zoom']}, dx {p[frame - t0, 0]:.2f}, dy {p[frame - t0, 1]:.2f})")


if __name__ == "__main__":
    main()
