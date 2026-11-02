"""Read-only: transform/crop/stabiliser properties of the given items on the current timeline (by unique id).
Usage: /usr/bin/python3 scripts/resolve_item_props.py <shots.json>"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from gigsteady.resolve.bridge import connect, guard  # noqa: E402

KEYS = ("ZoomX", "ZoomY", "Pan", "Tilt", "RotationAngle", "CropLeft", "CropRight", "CropTop", "CropBottom",
        "FlipX", "FlipY", "DynamicZoomEase", "CompositeMode", "Opacity", "RetimeProcess", "Scaling")
guard()
p = connect().GetProjectManager().GetCurrentProject()
if p.IsRenderingInProgress():
    sys.exit("render in progress")
tl = p.GetCurrentTimeline()
want = {s["item_id"]: s["name"] for s in json.loads(Path(sys.argv[1]).read_text())}
out = {}
for it in tl.GetItemListInTrack("video", 1) or []:
    if it.GetUniqueId() in want:
        pr = it.GetProperty() or {}
        out[want[it.GetUniqueId()]] = {k: pr.get(k) for k in KEYS if k in pr}
        out[want[it.GetUniqueId()]]["stab"] = {k: v for k, v in pr.items() if "stab" in k.lower()}
print(json.dumps(out, indent=1))
