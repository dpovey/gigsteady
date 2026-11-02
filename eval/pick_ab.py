"""Pick the Photo Pit shots for the blind A/B (prototype vs gigsteady L1), stratified for the decision.

Usage: .venv/bin/python eval/pick_ab.py <l1_job_dir> <shots.json> <out_shots.json> [--n 16] [--seed 7]
Only track-mode shots can differ (lock mode doesn't use the path planner). Each is scored by how far gigsteady's
correction is from the prototype's (p90 of the per-frame difference, 4K px). Three quarters of the picks are the
largest differences, split evenly between shots where gigsteady crops tighter and where it crops the same or looser,
so the page is spent where the default could change. The rest are drawn at random from the smaller differences,
to check that "no real difference" is used when the paths are close. Prints the table (for the notes, not the page).
"""
import argparse
import csv
import json
import os
import random
from pathlib import Path

import numpy as np

PROTO = Path(os.environ.get("GIGSTEADY_PROTO", "/Users/dean/src/personal/pit-stabilise/data")) / "lockoff"


def load(p: Path) -> np.ndarray:
    return np.array([[float(r["dx_4k"]), float(r["dy_4k"])] for r in csv.DictReader(p.open())])


def proto_zoom(name: str) -> float | None:
    log = PROTO.parent / "batch_logs" / f"{name}.log"
    if log.exists():
        for w in log.read_text().split("rendered zoom ")[1:2]:
            return float(w.split()[0])
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("job", type=Path)
    ap.add_argument("shots", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    n, seed, job, shots_p, out_p = a.n, a.seed, a.job, a.shots, a.out
    shots = {s["name"]: s for s in json.loads(shots_p.read_text())}
    rows = []
    for name in shots:
        rep_p = job / "outputs" / f"{name}.json"
        if not rep_p.exists():
            continue
        rep = json.loads(rep_p.read_text())
        if rep["mode"] != "track":
            continue
        ours, ref = load(rep_p.with_suffix(".csv")), load(PROTO / f"{name}.csv")
        d = np.hypot(*(ours - ref).T) if len(ours) == len(ref) else np.array([np.inf])
        pz = proto_zoom(name)
        rows.append({"name": name, "p90": float(np.percentile(d, 90)), "zoom": rep["zoom"], "proto_zoom": pz})
    rows.sort(key=lambda r: -r["p90"])
    top_n = round(n * 0.75)
    tighter = [r for r in rows if r["proto_zoom"] and r["zoom"] > r["proto_zoom"] + 0.005]
    other = [r for r in rows if r not in tighter]
    pick = tighter[:top_n // 2]
    pick += other[:top_n - len(pick)]
    pick += [r for r in tighter + other if r not in pick][:top_n - len(pick)]
    rest = [r for r in rows if r not in pick]
    pick += random.Random(seed).sample(rest, min(n - len(pick), len(rest)))
    for r in rows:
        print(f"{r['name']:16} p90 {r['p90']:7.2f} px  zoom {r['zoom']:.3f} proto {r['proto_zoom']}"
              f"{'  PICK' if r in pick else ''}")
    names = {r["name"] for r in pick}
    out_p.write_text(json.dumps([s for k, s in shots.items() if k in names], indent=1))
    print(f"PICKED {len(pick)} of {len(rows)} track shots -> {out_p}")


if __name__ == "__main__":
    main()
