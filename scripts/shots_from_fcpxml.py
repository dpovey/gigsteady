"""Shot list for gigsteady batch from a Resolve FCPXML export and the API item list (scripts/resolve_items.py).

Usage: /usr/bin/python3 scripts/shots_from_fcpxml.py <Info.fcpxml> <items.json> <out_shots.json> [angle ...]
       [--through-edits neither|render-all]
Each main-track item whose angle is listed (default Static Desk, Photo Pit) becomes {name, src, in, out, record...}.
Items whose angle or record range disagree between the FCPXML and the API stop the run.

Through-edits (rule a, gigsteady.edits): adjacent items of one camera take are one shot. If every part can be
stabilised they are merged (one camera path, placed part by part); if a part can't (Dynamic Zoom), the default
"neither" stabilises none of them, and "render-all" renders the whole take and leaves the Dynamic Zoom part to the
editor. Also writes <out stem>.items.json (every main-track item, for scripts/manifest.py --items) and
<out stem>.through_edits.json (what happened to each through-edit and why).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from gigsteady.resolve.fcpxml import Project  # noqa: E402
from gigsteady.edits import Item, apply_through_edits  # noqa: E402

args = sys.argv[1:]
policy = "neither"
if "--through-edits" in args:
    i = args.index("--through-edits")
    policy = args[i + 1]
    del args[i:i + 2]
fcp, items_p, out_p = args[:3]
angles = args[3:] or ["Static Desk", "Photo Pit"]
P = Project(fcp)
d = json.loads(Path(items_p).read_text())
shots, every = [], []
for it in d["items"]:
    if it["track"] != 1 or it["type"] != "Multicam":
        continue
    r = P.resolve(it["start"])
    ang = it["name"].split(" - ")[-1]
    if not r or r["angle"] != ang or (r["record_start"], r["record_end"]) != (it["start"], it["end"]):
        sys.exit(f"FCPXML and API disagree at record {it['start']}: {ang} vs {r}")
    if "dynamic_zoom" not in it:
        sys.exit(f"{items_p}: item at {it['start']} has no dynamic_zoom field; re-read the timeline with this "
                 "version of resolve_items.py (an older dump would merge a Dynamic Zoom take as stabilisable)")
    every.append(Item(record_start=it["start"], record_end=it["end"], angle=ang, src=r["path"], src_in=r["src_frame"],
                      dynamic_zoom=bool(it.get("dynamic_zoom")), name=it["name"]))
    if ang not in angles or not r["path"]:
        continue
    tag = "SD" if ang == "Static Desk" else "PP" if ang == "Photo Pit" else ang.replace(" ", "")
    n = it["end"] - it["start"]
    shots.append({"name": f"{tag}_rec{it['start']}", "src": r["path"], "in": r["src_frame"], "out": r["src_frame"] + n,
                  "record_start": it["start"], "record_end": it["end"], "track": it["track"], "item_id": it["id"],
                  "angle": ang, "angle_transform": r["angle_transform"], "item_transform": r["item_transform"]})
shots, decisions = apply_through_edits(shots, every, policy)
out = Path(out_p)
out.write_text(json.dumps(shots, indent=1))
stem = out.with_suffix("")
Path(f"{stem}.items.json").write_text(json.dumps([{"record_start": i.record_start, "record_end": i.record_end,
                                                   "angle": i.angle, "src": i.src, "in": i.src_in,
                                                   "dynamic_zoom": i.dynamic_zoom, "name": i.name} for i in every], indent=1))
Path(f"{stem}.through_edits.json").write_text(json.dumps(decisions, indent=1))
print(f"{len(shots)} shots -> {out_p}; through-edits: "
      + (", ".join(f"{d['chain'][0][0]} {d['action']}" for d in decisions) or "none"))
