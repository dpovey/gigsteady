"""Read-only: list video items overlapping the current timeline's In/Out (or the whole timeline).
Usage: /usr/bin/python3 scripts/resolve_items.py > items.json"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from gigsteady.resolve.bridge import connect, guard  # noqa: E402

guard()
r = connect()
p = r.GetProjectManager().GetCurrentProject()
if p.IsRenderingInProgress():
    sys.exit("render in progress: not reading clip properties")
tl = p.GetCurrentTimeline()
st = tl.GetStartFrame()
io = tl.GetMarkInOut().get("video", {})
a, b = (st + io["in"], st + io["out"] + 1) if "in" in io else (st, tl.GetEndFrame())
out = {"timeline": tl.GetName(), "range": [a, b], "items": []}
for t in range(1, tl.GetTrackCount("video") + 1):
    for it in tl.GetItemListInTrack("video", t) or []:
        if it.GetEnd() <= a or it.GetStart() >= b:
            continue
        m = it.GetMediaPoolItem()
        props = m.GetClipProperty() if m else {}
        out["items"].append({"track": t, "track_name": tl.GetTrackName("video", t), "name": it.GetName(),
                             "start": it.GetStart(), "end": it.GetEnd(), "left_offset": it.GetLeftOffset(),
                             "src_start": it.GetSourceStartFrame(), "src_end": it.GetSourceEndFrame(),
                             "enabled": it.GetClipEnabled(), "id": it.GetUniqueId(),
                             "type": props.get("Type"), "path": props.get("File Path"),
                             "clip_name": props.get("Clip Name")})
print(json.dumps(out, indent=1))
