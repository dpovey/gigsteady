"""Compare a gigsteady analyse-only job with the prototype's per-shot path CSVs (pit-stabilise/data/lockoff).

Usage: .venv/bin/python eval/parity.py <job_dir> [<proto_lockoff_dir>]
Prints per-shot max/median deviation of the correction (4K px), mode and zoom vs the prototype log, and a summary.
"""
import csv
import json
import re
import sys
from pathlib import Path

import numpy as np

PROTO = Path("/Users/dean/src/personal/pit-stabilise/data")


def load(p: Path) -> np.ndarray:
    return np.array([[float(r["dx_4k"]), float(r["dy_4k"])] for r in csv.DictReader(p.open())])


def main() -> None:
    job = Path(sys.argv[1])
    lock_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else PROTO / "lockoff"
    rows = []
    for rep_p in sorted(p for p in (job / "outputs").glob("*.json") if not p.name.startswith("._")):
        name = rep_p.stem
        rep = json.loads(rep_p.read_text())
        ours, ref_p = load(rep_p.with_suffix(".csv")), lock_dir / f"{name}.csv"
        if not ref_p.exists():
            continue
        ref = load(ref_p)
        d = np.abs(ours - ref).max(1) if len(ours) == len(ref) else np.array([np.inf])
        log = PROTO / "batch_logs" / f"{name}.log"
        pz = pm = ""
        if log.exists():
            t = log.read_text()
            pm = "track" if "track mode" in t else "lock"
            m = re.search(r"rendered zoom ([\d.]+)", t)
            pz = m.group(1) if m else ""
        rows.append((name, rep["mode"], pm, rep["zoom"], pz, float(d.max()), float(np.median(d))))
    bad = [r for r in rows if r[5] > 0.5 or (r[2] and r[1] != r[2]) or (r[4] and abs(float(r[4]) - r[3]) > 0.0051)]
    for r in rows:
        flag = "  <--" if r in bad else ""
        print(f"{r[0]:22} mode {r[1]:5} (proto {r[2]:5}) zoom {r[3]:.3f} (proto {r[4]:>5}) "
              f"max dev {r[5]:.3f} median {r[6]:.3f}{flag}")
    dev = np.array([r[5] for r in rows])
    print(f"PARITY {len(rows)} shots: max dev median {np.median(dev):.3f}, worst {dev.max():.3f}; "
          f"outside tolerance: {len(bad)}")


if __name__ == "__main__":
    main()
