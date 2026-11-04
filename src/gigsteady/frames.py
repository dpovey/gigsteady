"""`gigsteady analyse-frames`: the analysis for the OFX plugin, on frames the plugin already has.

The plugin fetches each frame from Resolve (which works for multicam and any other source, where it has no file
path), shrinks it to analysis-size luma and writes the frames to stdin as raw uint8, n x h x w. The result is the
same mode choice and correction path as `gigsteady stabilise`, written as JSON: the path in full-frame pixels (the
size the plugin was given), and the zoom that hides the edges. No gyro: there is no file to read it from.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

from . import __version__


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="gigsteady analyse-frames")
    ap.add_argument("--frames", type=int, required=True)
    ap.add_argument("--size", required=True, help="analysis size WxH of the raw frames on stdin")
    ap.add_argument("--full", required=True, help="full frame size WxH the path is for")
    ap.add_argument("--mode", choices=["auto", "lock", "track"], default="auto")
    ap.add_argument("--crop-budget", type=float, default=1.10)
    ap.add_argument("--smoother", choices=["l1", "gauss"], default="l1")
    ap.add_argument("--json", type=Path, required=True, help="where to write the result")
    a = ap.parse_args(argv)
    from .pipeline import Options, analyse_gray
    from .warp import needed_zoom

    w, h = (int(v) for v in a.size.split("x"))
    fw, fh = (int(v) for v in a.full.split("x"))
    t0 = time.time()
    raw = sys.stdin.buffer.read(a.frames * w * h)
    if len(raw) != a.frames * w * h:
        raise SystemExit(f"expected {a.frames} frames of {w}x{h}, got {len(raw)} bytes")
    g = np.frombuffer(raw, np.uint8).reshape(a.frames, h, w)
    opt = Options(mode=a.mode, crop_budget=a.crop_budget, smoother=a.smoother, gyro="off")
    r = analyse_gray(g, fw, fh, opt)
    k = r["k"]
    zoom = needed_zoom(r["p"], k, fw, fh)
    out = {"version": __version__, "mode": r["mode"], "zoom": zoom, "width": fw, "height": fh,
           "analysis_s": round(time.time() - t0, 1), "report": r["report"], "options_key": opt.key(),
           "p": [[round(q[0] * k, 3), round(q[1] * k, 3), round(float(q[2]), 7), round(float(q[3]), 6)]
                 for q in r["p"]]}
    tmp = a.json.with_name(a.json.name + ".partial")
    tmp.write_text(json.dumps(out, separators=(",", ":")))
    tmp.replace(a.json)
    return 0
