"""Place a finished job on a duplicate of a timeline (see gigsteady.resolve.place), or undo it.
Usage: /usr/bin/python3 scripts/resolve_place.py <job_dir> "<master timeline>" "<duplicate name>"
       /usr/bin/python3 scripts/resolve_place.py <job_dir> --undo
The job needs manifest.json (scripts/manifest.py) and shots.json (angle transforms)."""
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from gigsteady.resolve.bridge import connect, guard  # noqa: E402
from gigsteady.resolve import place  # noqa: E402

guard()
job = Path(sys.argv[1])
r = connect()
if sys.argv[2] == "--undo":
    place.undo(r, job)
    print("UNDO DONE")
    sys.exit(0)
shots = {s["name"]: s for s in json.loads((job / "shots.json").read_text())}
manifest = [dict(shots[m["name"]], **m) for m in json.loads((job / "manifest.json").read_text())]
pl = place.Placer(r, job, sys.argv[2], sys.argv[3], log=lambda *a: print(*a, flush=True))
try:
    st = pl.run(manifest)
    print(f"PLACE DONE placed={len(st['placed'])} skipped={len(st['skipped'])}")
except Exception as e:
    traceback.print_exc()
    print("PLACE STOPPED:", e, flush=True)
    try:
        pl.restore()
    except Exception as e2:
        print("restore failed:", e2)
    sys.exit(1)
