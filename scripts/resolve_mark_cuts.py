"""Mark cuts on gigsteady's duplicate timeline for hand work: a marker on the duplicate over each cut, and a flag on the
cut's stabilised clip in gigsteady's bin. Never flags timeline items: a flag on a multicam cut is a flag on the shared
multicam media-pool clip, so every cut of every timeline would show it.

Usage: /usr/bin/python3 scripts/resolve_mark_cuts.py <job_dir> <marker colour> "<marker name>" <name> [<name> ...]
       (names like SD_rec151966: the record start of the cut on V1). Read back while the duplicate is current;
       the view is restored. Recorded under "marks" in place_state.json.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from gigsteady.resolve.bridge import connect, guard  # noqa: E402
from gigsteady.resolve.place import check, find_timeline, find_timeline_by_id  # noqa: E402

guard()
job, colour, mname, names = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4:]
st_p = job / "place_state.json"
st = json.loads(st_p.read_text())
p = connect().GetProjectManager().GetCurrentProject()
check(p.GetName() == st["project"] and not p.IsRenderingInProgress(), "wrong project or rendering")
cur = p.GetCurrentTimeline()
view = (cur.GetName(), cur.GetCurrentTimecode(), cur.GetMarkInOut().get("video", {}))
dup = find_timeline_by_id(p, st["created"]["timeline"])
check(dup is not None, "duplicate timeline not found")
folder = next(f for f in p.GetMediaPool().GetRootFolder().GetSubFolderList() if f.GetUniqueId() == st["created"]["bin"])
mine = set(st["created"]["media"])
clips = {Path(c.GetClipProperty("File Path")).stem: c for c in folder.GetClipList() if c.GetUniqueId() in mine}
p.SetCurrentTimeline(dup)
done = []
try:
    v1 = {it.GetStart(): it for it in dup.GetItemListInTrack("video", 1)}
    st0 = dup.GetStartFrame()
    for n in names:
        it = v1.get(int(n.split("_rec")[1]))
        check(it is not None, f"{n}: no V1 cut starts there")
        mks = dup.GetMarkers()
        f = next((k for k, v in mks.items() if v["name"] == mname and it.GetStart() - st0 <= k < it.GetEnd() - st0), None)
        if f is None:  # first free frame of the cut: markers are one per frame and others may sit on the cut start
            f = next(k for k in range(it.GetStart() - st0, it.GetEnd() - st0) if k not in mks)
            dup.AddMarker(f, colour, mname, f"{n} (gigsteady)", it.GetEnd() - st0 - f)
        mk = dup.GetMarkers().get(f)
        check(mk is not None and mk["color"] == colour and mk["name"] == mname, f"{n}: marker read-back {mk}")
        c = clips.get(n)
        if c is not None and colour not in (c.GetFlagList() or []):
            c.AddFlag(colour)
        check(c is None or colour in (c.GetFlagList() or []), f"{n}: bin clip flag read-back")
        done.append({"name": n, "marker_frame": f, "bin_clip_flagged": c is not None})
        print(n, "marker at timeline frame", f, "| bin clip flagged" if c is not None else "")
finally:
    st.setdefault("marks", []).append({"colour": colour, "name": mname, "cuts": done})
    st_p.write_text(json.dumps(st, indent=1))
    tl = find_timeline(p, view[0])
    p.SetCurrentTimeline(tl)
    if "in" in view[2]:
        tl.SetMarkInOut(view[2]["in"], view[2]["out"])
    tl.SetCurrentTimecode(view[1])
    check(tl.GetCurrentTimecode() == view[1] and tl.GetMarkInOut().get("video", {}) == view[2], "restore view")
    print("restored", view)
