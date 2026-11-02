"""Many shots, N at a time, each in its own process.

shots.json: [{"name", "src", "in", "out"}] (source frames, out exclusive), or the prototype's
[{"name", "src", "start", "n"}] (start, start+n; handles default to 0 for those, as the prototype had none).

Job directory: <job>/outputs/<name>.mov (+ .json report, .csv path), <job>/logs/<name>.log, <job>/results.csv.
Resume: a shot is skipped when its report exists with the same options key (and its .mov, when rendering);
anything else is redone, and stale .partial files are removed first. The last line printed is
"GIGSTEADY BATCH DONE ok=.. failed=.. skipped=.. in ..s".
Memory: each job peaks at ~3 GB RSS plus 1-2 GB GPU memory at 4K (SD_unstab_308139: 3.0 GB).
"""
import csv
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .pipeline import Options

FIELDS = ["name", "status", "mode", "zoom", "matched", "lighting_states", "smoother", "sigma", "gyro", "fused",
          "frames", "src", "in", "out", "handle_in", "handle_out", "seconds", "output", "error"]


def load_shots(path: Path) -> list[dict]:
    shots = json.loads(Path(path).read_text())
    out = []
    for s in shots:
        if "in" in s:
            out.append({"name": s["name"], "src": s["src"], "in": int(s["in"]), "out": int(s["out"]),
                        "handles": s.get("handles")})
        else:
            out.append({"name": s["name"], "src": s["src"], "in": int(s["start"]), "out": int(s["start"]) + int(s["n"]),
                        "handles": s.get("handles", 0)})
    return out


def run_batch(shots_file: Path, job: Path, opt: Options, jobs: int = 3, render: bool = True) -> list[dict]:
    from .cli import option_args
    shots = load_shots(shots_file)
    (job / "outputs").mkdir(parents=True, exist_ok=True)
    (job / "logs").mkdir(parents=True, exist_ok=True)
    if not (job / "shots.json").exists():
        (job / "shots.json").write_text(json.dumps(shots, indent=1))
    clean = option_args(opt)

    def run(s: dict) -> dict:
        out = job / "outputs" / f"{s['name']}.mov"
        rep_path = out.with_suffix(".json")
        handles = opt.handles if s["handles"] is None else s["handles"]
        key = Options(**{**opt.__dict__, "handles": handles}).key()
        row = {"name": s["name"], "src": s["src"], "in": s["in"], "out": s["out"]}
        if rep_path.exists() and (out.exists() or not render):
            rep = json.loads(rep_path.read_text())
            same_shot = (rep.get("src"), rep.get("in"), rep.get("out")) == (s["src"], s["in"], s["out"])
            if same_shot and rep.get("options_key") == key and (rep.get("output") is not None) == render:
                return {**row, **summary(rep), "status": "skipped"}
        for stale in (out.with_name(out.stem + ".partial.mov"), out if render else None, rep_path):
            if stale and stale.exists():
                stale.unlink()
        t0 = time.time()
        cmd = [sys.executable, "-m", "gigsteady.cli", "stabilise", s["src"], "--in", str(s["in"]), "--out",
               str(s["out"]), "-o", str(out), "--handles", str(handles), *clean, *([] if render else ["--no-render"])]
        with (job / "logs" / f"{s['name']}.log").open("w") as fh:
            rc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT).returncode
        if rc == 0 and rep_path.exists():
            return {**row, **summary(json.loads(rep_path.read_text())), "status": "ok",
                    "seconds": round(time.time() - t0, 1)}
        tail = (job / "logs" / f"{s['name']}.log").read_text().strip().splitlines()[-1:] or [""]
        return {**row, "status": f"failed rc={rc}", "error": tail[0][:200], "seconds": round(time.time() - t0, 1)}

    rows, t0 = [], time.time()
    with ThreadPoolExecutor(jobs) as pool:
        futs = [pool.submit(run, s) for s in shots]
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            rows.append(r)
            print(f"[{i}/{len(shots)}] {r['name']}: {r['status']} {r.get('mode', '')} zoom={r.get('zoom', '')} "
                  f"fused={r.get('fused', '')} {r.get('seconds', '')}s (elapsed {time.time() - t0:.0f}s)", flush=True)
    order = {s["name"]: i for i, s in enumerate(shots)}
    rows.sort(key=lambda r: order[r["name"]])
    with (job / "results.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    c = lambda p: sum(r["status"].startswith(p) for r in rows)  # noqa: E731
    print(f"GIGSTEADY BATCH DONE ok={c('ok')} failed={c('failed')} skipped={c('skipped')} in {time.time() - t0:.0f}s",
          flush=True)
    return rows


def summary(rep: dict) -> dict:
    fu = rep.get("fusion") or {}
    return {"mode": rep.get("mode"), "zoom": rep.get("zoom"), "matched": rep.get("matched"),
            "lighting_states": rep.get("lighting_states"), "smoother": rep.get("smoother"), "sigma": rep.get("sigma"),
            "gyro": rep.get("gyro"), "fused": fu.get("fused"), "frames": rep.get("frames"),
            "handle_in": rep.get("handle_in"), "handle_out": rep.get("handle_out"), "output": rep.get("output"),
            "seconds": round(rep.get("analysis_s", 0) + rep.get("render_s", 0), 1)}
