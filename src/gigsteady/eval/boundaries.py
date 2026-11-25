"""Rule (c): zoom jumps at overlay boundaries, measured on a finished render.

A stabilised overlay is a tighter crop than its source. At a boundary where the neighbour shows the same picture
(a through-edit, or the same camera without a change of framing), that crop reads as a zoom jump. This check
measures every consecutive frame pair of the render (streamed: one ffmpeg pipe at 960x540 grey, two frames held)
with ORB features and a RANSAC similarity, then looks at the pair across each overlay's start and end:

- same picture: at least MIN_INLIERS inliers (a cut to another camera has a handful);
- step: the pair's scale over the median of its +-4 neighbouring pairs (camera moves cancel out);
- a jump: STEP_MIN < |log step| < STEP_MAX. Under 2% is measurement noise and slow camera zoom; over 30% is a
  deliberate wide/tight cut between two framings of the same stage (one song has seven, x0.42-0.52 and x2.3-2.4),
  where the stabiliser's crop is not what the viewer sees. The check can't tell an editor's own 2-30% punch-in at an
  overlay boundary from a crop jump: such a failure needs a look.

On the six delivered songs it was built against, it flags song 1 at 3:02.0 (record 38900, x1.098)
and nothing else (tests/fixtures/boundaries).

usage: gigsteady eval boundaries <render> --record-in N --overlays <place_state.json | manifest.json | shots.json>
       [--pairs cache.csv] [--fps 25]
       [--json report.json]       exit 1 when a boundary fails, 2 when there are no overlay ranges to check
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np

W, H = 960, 540
MIN_INLIERS = 40
STEP_MIN, STEP_MAX = math.log(1.02), math.log(1.30)
NEIGHBOURS = 4


def measure_pairs(render: str, out_csv: Path | None = None) -> dict[int, tuple[float, int]]:
    """Frame k -> (scale, inliers) of the similarity from frame k-1 to frame k, for every frame of the render."""
    import cv2
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-nostdin", "-i", render, "-map", "0:v:0", "-vf",
                          f"scale={W}:{H}:flags=area", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"],
                         stdout=subprocess.PIPE, bufsize=W * H * 4)
    orb = cv2.ORB_create(1500)
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    out, prev, k = {}, None, 0
    failed = False
    try:
        while True:
            b = p.stdout.read(W * H)
            if len(b) < W * H:
                if b:
                    raise RuntimeError(f"{render}: partial frame {len(b)} of {W * H} bytes after frame {k}")
                break
            kp, de = orb.detectAndCompute(np.frombuffer(b, np.uint8).reshape(H, W), None)
            if prev is not None:
                out[k] = similarity(prev, (kp, de), bf)
            prev, k = (kp, de), k + 1
    except BaseException:
        failed = True
        raise
    finally:
        p.stdout.close()
        rc = p.wait()
        if rc != 0 and not failed:        # don't mask the error in flight with ffmpeg's SIGPIPE
            raise RuntimeError(f"ffmpeg failed on {render} (exit {rc})")
    if out_csv:
        with open(out_csv, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["frame", "scale", "inliers"])
            w.writerows([f, *v] for f, v in sorted(out.items()))
    return out


def similarity(a, b, bf) -> tuple[float, int]:
    """(scale, inliers) of the similarity taking keypoints a to keypoints b; (nan, 0) when they don't register."""
    import cv2
    (ka, da), (kb, db) = a, b
    if da is None or db is None or len(ka) < 12 or len(kb) < 12:
        return float("nan"), 0
    m = bf.match(da, db)
    if len(m) < 12:
        return float("nan"), 0
    M, inl = cv2.estimateAffinePartial2D(np.float32([ka[x.queryIdx].pt for x in m]),
                                         np.float32([kb[x.trainIdx].pt for x in m]),
                                         method=cv2.RANSAC, ransacReprojThreshold=3.0)
    if M is None:
        return float("nan"), 0
    return float(np.hypot(M[0, 0], M[1, 0])), int(inl.sum())


def read_pairs(path: Path) -> dict[int, tuple[float, int]]:
    with open(path, newline="") as fh:
        return {int(r["frame"]): (float(r["scale"]) if r["scale"] not in ("", "nan") else float("nan"), int(r["inliers"]))
                for r in csv.DictReader(fh)}


def step(pairs: dict[int, tuple[float, int]], k: int) -> float | None:
    """Scale of pair k over the median of its same-picture neighbours; None if k isn't one picture or has too few."""
    s, n = pairs.get(k, (float("nan"), 0))
    if n < MIN_INLIERS or not s > 0:
        return None
    nb = [math.log(pairs[j][0]) for j in range(k - NEIGHBOURS, k + NEIGHBOURS + 1)
          if j != k and j in pairs and pairs[j][1] >= MIN_INLIERS and pairs[j][0] > 0]
    if len(nb) < 3:
        return None
    return math.exp(math.log(s) - float(np.median(nb)))


def overlays_from(path: Path) -> list[dict]:
    """Overlay ranges: a job's place_state.json ("placed": what is on the timeline; preferred), its manifest.json
    (status ok rows, including any the placer later skipped) or a shots.json."""
    rows = json.loads(Path(path).read_text())
    if isinstance(rows, dict):
        rows = rows.get("placed", [])
    return [{"name": r.get("name", ""), "record_start": int(r["record_start"]), "record_end": int(r["record_end"])}
            for r in rows if r.get("status", "ok") == "ok" and r.get("record_start") is not None
            and r.get("record_end") is not None]   # older place states record no record_end


def check(pairs: dict[int, tuple[float, int]], record_in: int, overlays: list[dict], fps: float = 25.0) -> list[dict]:
    """One row per overlay boundary inside the render: the edit frame, same picture or not, the step, and "fail"."""
    out, seen = [], set()
    frames = max(pairs) + 1 if pairs else 0
    for ov in sorted(overlays, key=lambda o: o["record_start"]):
        for rec, side in ((ov["record_start"], "start"), (ov["record_end"], "end")):
            k = rec - record_in        # the pair (k-1 -> k) crosses the edit at record `rec`
            if rec in seen or not 1 <= k < frames:
                continue
            seen.add(rec)
            s, n = pairs.get(k, (float("nan"), 0))
            st = step(pairs, k)
            fail = st is not None and STEP_MIN < abs(math.log(st)) < STEP_MAX
            out.append({"record": rec, "frame": k, "video_s": round(k / fps, 2), "overlay": ov["name"], "side": side,
                        "same_picture": n >= MIN_INLIERS, "inliers": n, "step": None if st is None else round(st, 4),
                        "fail": fail})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gigsteady eval boundaries", description=__doc__.split("\n\n")[0])
    ap.add_argument("render")
    ap.add_argument("--record-in", type=int, required=True, help="the record frame of the render's first frame")
    ap.add_argument("--overlays", type=Path, required=True,
                    help="the job's place_state.json (what was placed), manifest.json or a shots.json")
    ap.add_argument("--fps", type=float, default=25.0, help="for the times in the report")
    ap.add_argument("--pairs", type=Path, help="pair cache: read it if it exists, else write it")
    ap.add_argument("--json", type=Path, help="write the report here")
    a = ap.parse_args(argv)
    overlays = overlays_from(a.overlays)
    if not overlays:    # e.g. an older place_state.json, whose placed rows carry no record_end: not a pass
        print(f"BOUNDARIES no overlay ranges in {a.overlays}; nothing checked", file=sys.stderr)
        return 2
    if a.pairs and a.pairs.exists():
        pairs = read_pairs(a.pairs)
    else:
        pairs = measure_pairs(a.render, a.pairs)
    rows = check(pairs, a.record_in, overlays, a.fps)
    if a.json:
        a.json.write_text(json.dumps(rows, indent=1))
    bad = [r for r in rows if r["fail"]]
    for r in rows:
        if r["same_picture"]:
            print(f"{'FAIL' if r['fail'] else 'ok  '} {r['video_s']:8.2f}s record {r['record']} {r['overlay']} {r['side']}"
                  f"  step x{r['step']}  inliers {r['inliers']}")
    print(f"BOUNDARIES {len(rows)} checked, {sum(r['same_picture'] for r in rows)} same picture, {len(bad)} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
