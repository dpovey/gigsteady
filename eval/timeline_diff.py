"""Compare a render of the stabilised duplicate timeline with a render of the master, cut by cut, and report every
difference that ISN'T stabilisation: timing, colour/levels, rotation, framing, and changes to cuts that weren't
replaced.

Usage: .venv/bin/python eval/timeline_diff.py <master.mov> <stab.mov> <range_start> <items.json> <job_dir> [--out f.csv]
Both renders start at record frame <range_start>. items.json is scripts/resolve_items.py output (V1 cuts),
job_dir holds manifest.json and place_state.json.

Per placed cut, each frame of the stabilised render is registered to the master's frame (affine ECC on 480 px grey,
started from the stabiliser's crop). Expected: scale = the stabiliser's zoom, rotation within the stabiliser's own
roll, a shift that moves (that's the stabilisation). Anything else is reported: a constant rotation (wrong sign of the
angle transform), a scale off the crop (transform composed wrong), a better match one or two frames away (timing),
colour or level shifts after alignment. Cuts that weren't replaced must be identical.
"""
import argparse
import csv
import json
import subprocess
from pathlib import Path

import cv2
import numpy as np

W, H = 960, 540


def decode(path: str, start: int, n: int) -> np.ndarray:
    ss = ["-ss", f"{(start - 0.25) / 25:.6f}"] if start > 0 else []
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", *ss, "-i", path, "-frames:v", str(n),
                          # to RGB at full size first: resizing in YUV before the conversion biases RGB by ~2 codes
                          "-vf", f"format=gbrp16le,scale={W}:{H}:flags=area,format=rgb24", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)


def grey(f: np.ndarray, size=(480, 270)) -> np.ndarray:
    g = cv2.cvtColor(cv2.resize(f, size, interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY).astype(np.float32)
    return cv2.GaussianBlur(g, (0, 0), 1.0)


def register(stab: np.ndarray, master: np.ndarray, zoom: float):
    """Affine W with master(W x) ~ stab(x), at 480 px. Returns W (scaled to 960 px coordinates) and the ECC score."""
    s = 1 / zoom
    cx, cy = 240, 135
    w0 = np.array([[s, 0, (1 - s) * cx], [0, s, (1 - s) * cy]], np.float32)
    try:
        cc, w = cv2.findTransformECC(grey(stab), grey(master), w0, cv2.MOTION_AFFINE,
                                     (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 80, 1e-5), None, 5)
    except cv2.error:
        return None, 0.0
    w = w.copy()
    w[:, 2] *= 2
    return w, float(cc)


def decompose(w: np.ndarray) -> tuple[float, float, float, float]:
    a, b, c, d = w[0, 0], w[0, 1], w[1, 0], w[1, 1]
    sx, sy = np.hypot(a, c), np.hypot(b, d)
    rot = np.degrees(np.arctan2(c, a))  # rotation of the master->stab mapping's inverse; sign checked by stats
    return 1 / sx, 1 / sy, -rot, float(w[0, 2]), float(w[1, 2])


def stats(a: np.ndarray, m: np.ndarray) -> np.ndarray:
    """Per channel: mean and 5/50/95 percentiles of a and m inside the valid mask m (bool)."""
    out = []
    for ch in range(3):
        v = a[..., ch][m]
        out.append([v.mean(), *np.percentile(v, (5, 50, 95))])
    return np.array(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("master")
    ap.add_argument("stab")
    ap.add_argument("range_start", type=int)
    ap.add_argument("items", type=Path)
    ap.add_argument("job", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--step", type=int, default=2, help="register every Nth frame")
    a = ap.parse_args()
    all_items = json.loads(a.items.read_text())["items"]
    items = [i for i in all_items if i["track"] == 1]
    overlays = [(i["start"], i["end"]) for i in all_items if i["track"] > 1 and i["type"] not in (None,)]
    man = {m["name"]: m for m in json.loads((a.job / "manifest.json").read_text())}
    st = json.loads((a.job / "place_state.json").read_text())
    placed = {p["name"]: p for p in st["placed"]}
    n_total = int(subprocess.run(["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
                                  "stream=nb_read_packets", "-of", "csv=p=0", a.stab],
                                 capture_output=True, text=True, check=True).stdout.split()[0])
    rows = []
    for it in items:
        lo, hi = max(it["start"], a.range_start), min(it["end"], a.range_start + n_total)
        if hi <= lo:
            continue
        ang = it["name"].split(" - ")[-1]
        tag = {"Static Desk": "SD", "Photo Pit": "PP"}.get(ang, ang)
        name = f"{tag}_rec{it['start']}"
        k, n = lo - a.range_start, hi - lo
        M, S = decode(a.master, k, n), decode(a.stab, k, n)
        n = min(len(M), len(S))
        row = {"cut": name, "angle": ang, "rec_start": lo, "frames": n, "placed": name in placed,
               # titles composited over a moved background differ at their edges; that's expected
               "under_title_frames": sum(max(0, min(hi, e) - max(lo, b)) for b, e in overlays)}
        if name not in placed:
            d = np.abs(M[:n].astype(np.int16) - S[:n].astype(np.int16))
            row.update(mean_abs=round(float(d.mean()), 3), max_abs=int(d.max()),
                       verdict="identical" if d.max() == 0 else ("near" if d.mean() < 0.5 else "DIFFERS"))
            rows.append(row)
            print(row, flush=True)
            continue
        z = man[name]["stab_zoom"]
        fit, offs = [], []
        cs, cm = [], []
        for f in range(0, n, a.step):
            w, cc = register(S[f], M[f], z)
            if w is None or cc < 0.5:
                continue
            fit.append((*decompose(w), cc))
            mw = cv2.warpAffine(M[f], w, (W, H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
            mask = np.zeros((H, W), bool)
            mask[int(H * .1):int(H * .9), int(W * .1):int(W * .9)] = True
            cs.append(stats(S[f], mask))
            cm.append(stats(mw, mask))
            if f in (n // 4, n // 2, 3 * n // 4):  # timing: does a neighbouring master frame match better?
                g = grey(S[f])
                sc = {}
                for o in (-2, -1, 0, 1, 2):
                    if 0 <= f + o < n:
                        mo = cv2.warpAffine(M[f + o], w, (W, H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP)
                        sc[o] = float(np.corrcoef(g.ravel(), grey(mo).ravel())[0, 1])
                offs.append(max(sc, key=sc.get))
        if not fit:
            row.update(verdict="NOT REGISTERED")
            rows.append(row)
            print(row, flush=True)
            continue
        F = np.array(fit)
        dc = np.mean(np.array(cs) - np.array(cm), axis=0)  # stab minus master, 8-bit code values, per channel/stat
        row.update(registered=len(F), median_cc=round(float(np.median(F[:, 5])), 3),
                   stab_zoom=z, zoom_meas=round(float(np.median(F[:, 0])), 4),
                   zoom_err=round(float(np.median(F[:, 0]) - z), 4),
                   aspect_err=round(float(np.median(F[:, 0] - F[:, 1])), 4),
                   rot_median=round(float(np.median(F[:, 2])), 3), rot_spread=round(float(np.std(F[:, 2])), 3),
                   shift_spread_px=round(float(np.hypot(F[:, 3].std(), F[:, 4].std()) * 4), 1),
                   timing_offsets=" ".join(map(str, offs)),
                   d_mean_rgb=" ".join(f"{v:+.2f}" for v in dc[:, 0]),
                   d_p5_rgb=" ".join(f"{v:+.2f}" for v in dc[:, 1]),
                   d_p95_rgb=" ".join(f"{v:+.2f}" for v in dc[:, 3]))
        flags = []
        if abs(row["zoom_err"]) > 0.01:
            flags.append("framing")
        if abs(row["rot_median"]) > 0.3:
            flags.append("rotation")
        if any(o != 0 for o in offs):
            flags.append("timing")
        if np.abs(dc[:, 0]).max() > 2 or np.abs(dc[:, 1]).max() > 3 or np.abs(dc[:, 3]).max() > 3:
            flags.append("colour/levels")
        row["verdict"] = "ok" if not flags else "CHECK: " + ", ".join(flags)
        rows.append(row)
        print({k: row[k] for k in ("cut", "verdict", "zoom_meas", "stab_zoom", "rot_median", "timing_offsets",
                                   "d_mean_rgb", "d_p5_rgb", "d_p95_rgb")}, flush=True)
    out = a.out or a.job / "check" / "timeline_diff.csv"
    keys = sorted({k for r in rows for k in r}, key=lambda k: list(rows[0]).index(k) if k in rows[0] else 99)
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    bad = [r for r in rows if r["verdict"] not in ("identical", "ok")]
    print(f"TIMELINE DIFF {len(rows)} cuts, {sum(r['placed'] for r in rows)} placed, {len(bad)} to check -> {out}")
    for r in bad:
        print("  ", r["cut"], r["verdict"])


if __name__ == "__main__":
    main()
