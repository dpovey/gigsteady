"""De-blur ghost check: how much fusion changes a region where people move, versus the rest of the frame.

Usage: .venv/bin/python eval/ghost_check.py <src> <in> <out> <people x,y,w,h (4K output px)> <dev_dir> <k> [<k> ...]
Renders the shot once with de-blur off and once per --fuse-contrast value k (0 = the shipped gate), handles 0,
then per frame: ghost = mean |fused - plain| inside the people box, activity = the same outside it (10-bit luma
codes, half resolution, RGB-first decode). A good gate keeps ghost ~0 on frames where the people move while
keeping activity (the vibration repair) elsewhere. Prints a table and the fused-frame counts; stops nothing.
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from gigsteady.pipeline import Options, stabilise  # noqa: E402

W, H = 1920, 1080


def frames(path: str):
    p = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "error", "-i", path, "-vf",
                          f"format=gbrp16le,scale={W}:{H}:flags=area,format=gray16le", "-f", "rawvideo", "-"],
                         stdout=subprocess.PIPE)
    while len(b := p.stdout.read(W * H * 2)) == W * H * 2:
        yield np.frombuffer(b, np.uint16).reshape(H, W).astype(np.float32) / 64
    p.wait()


def ghost(plain: str, fused: str, box: tuple[int, int, int, int]):
    """Per frame: (mean |fused - plain| inside box, same outside), 10-bit luma codes at half resolution."""
    x, y, w, h = (v // 2 for v in box)
    g, act = [], []
    for P, Fz in zip(frames(plain), frames(fused)):
        d = np.abs(Fz - P)
        inside = d[y:y + h, x:x + w]
        g.append(float(inside.mean()))
        act.append(float((d.sum() - inside.sum()) / (d.size - inside.size)))
    return np.array(g), np.array(act)


def main() -> None:
    src, a, b, box, dev = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], Path(sys.argv[5])
    ks = [float(k) for k in sys.argv[6:]]
    dev.mkdir(parents=True, exist_ok=True)
    plain = dev / "plain.mov"
    if not plain.exists():
        stabilise(src, a, b, plain, Options(handles=0, fuse=0))
    res = {}
    for k in ks:
        out = dev / f"fuse_k{k:g}.mov"
        rep = stabilise(src, a, b, out, Options(handles=0, fuse=4, fuse_contrast=k)) if not out.exists() else \
            json.loads(out.with_suffix(".json").read_text())
        g, act = ghost(str(plain), str(out), tuple(int(v) for v in box.split(",")))
        res[k] = (rep["fusion"], g, act)
    print("frame " + " ".join(f"   k={k:<4g} ghost/activity" for k in ks))
    for i in range(len(res[ks[0]][1])):
        print(f"{i:5d} " + " ".join(f"   {res[k][1][i]:6.3f} / {res[k][2][i]:6.3f}    " for k in ks))
    for k in ks:
        fz, g, act = res[k]
        print(f"k={k:g}: fused {fz['fused']}/{fz['frames']}  ghost mean {g.mean():.3f} max {g.max():.3f}  "
              f"activity mean {act.mean():.3f}")


if __name__ == "__main__":
    main()
