"""Read-only: transform properties and track states of the items on a named timeline (default the multicam source).
Usage: /usr/bin/python3 scripts/resolve_angle_props.py ["timeline name"]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from gigsteady.resolve.bridge import connect, guard  # noqa: E402
from gigsteady.resolve.place import find_timeline  # noqa: E402

guard()
p = connect().GetProjectManager().GetCurrentProject()
if p.IsRenderingInProgress():
    sys.exit("render in progress")
name = sys.argv[1] if len(sys.argv) > 1 else "Sydney 2026 - Multicam source"
tl = find_timeline(p, name)
for t in range(1, tl.GetTrackCount("video") + 1):
    print("track", t, tl.GetTrackName("video", t), "enabled", tl.GetIsTrackEnabled("video", t),
          "locked", tl.GetIsTrackLocked("video", t))
    for it in (tl.GetItemListInTrack("video", t) or [])[:2]:
        pr = it.GetProperty() or {}
        print("  ", it.GetName(), {k: pr.get(k) for k in ("ZoomX", "ZoomY", "RotationAngle", "Pan", "Tilt",
                                                          "DynamicZoomEnabled")})
