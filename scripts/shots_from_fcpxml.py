"""Shot list for gigsteady batch from a Resolve FCPXML export and the API item list (scripts/resolve_items.py).

Usage: /usr/bin/python3 scripts/shots_from_fcpxml.py <Info.fcpxml> <items.json> <out_shots.json> [angle ...]
Each main-track item whose angle is listed (default Static Desk, Photo Pit) becomes {name, src, in, out, record...}.
Items whose angle or record range disagree between the FCPXML and the API stop the run.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from gigsteady.resolve.fcpxml import Project  # noqa: E402

fcp, items_p, out_p = sys.argv[1:4]
angles = sys.argv[4:] or ["Static Desk", "Photo Pit"]
P = Project(fcp)
d = json.loads(Path(items_p).read_text())
shots = []
for it in d["items"]:
    if it["track"] != 1 or it["type"] != "Multicam":
        continue
    r = P.resolve(it["start"])
    ang = it["name"].split(" - ")[-1]
    if not r or r["angle"] != ang or (r["record_start"], r["record_end"]) != (it["start"], it["end"]):
        sys.exit(f"FCPXML and API disagree at record {it['start']}: {ang} vs {r}")
    if ang not in angles or not r["path"]:
        continue
    tag = "SD" if ang == "Static Desk" else "PP" if ang == "Photo Pit" else ang.replace(" ", "")
    n = it["end"] - it["start"]
    shots.append({"name": f"{tag}_rec{it['start']}", "src": r["path"], "in": r["src_frame"], "out": r["src_frame"] + n,
                  "record_start": it["start"], "record_end": it["end"], "track": it["track"], "item_id": it["id"],
                  "angle": ang, "angle_transform": r["angle_transform"], "item_transform": r["item_transform"]})
Path(out_p).write_text(json.dumps(shots, indent=1))
print(f"{len(shots)} shots -> {out_p}")
