"""Undo the Yellow flags that resolve_flags.py put on the shared multicam media-pool clip (a flag on a multicam
timeline item is a flag on its media-pool clip, so every multicam cut showed it), keep Yellow only on the bin clips of
cuts still needing dynamic zoom by hand, and mark those cuts with markers on the duplicate (per timeline).
Usage: /usr/bin/python3 scripts/resolve_fix_flags.py <job_dir> --survey | --apply <name> [<name> ...]"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from gigsteady.resolve.bridge import connect, guard  # noqa: E402
from gigsteady.resolve.place import check, find_timeline  # noqa: E402

guard()
job = Path(sys.argv[1])
st = json.loads((job / "place_state.json").read_text())
p = connect().GetProjectManager().GetCurrentProject()
check(p.GetName() == st["project"] and not p.IsRenderingInProgress(), "wrong project or rendering")
cur = p.GetCurrentTimeline()
view = (cur.GetName(), cur.GetCurrentTimecode(), cur.GetMarkInOut().get("video", {}))
master, dup = find_timeline(p, st["master"]), find_timeline(p, st["duplicate"])
folder = next(f for f in p.GetMediaPool().GetRootFolder().GetSubFolderList() if f.GetUniqueId() == st["created"]["bin"])
clips = {Path(c.GetClipProperty("File Path")).stem: c for c in folder.GetClipList()}
p.SetCurrentTimeline(dup)
try:
    v1 = dup.GetItemListInTrack("video", 1)
    mcs = {}
    for it in v1:
        m = it.GetMediaPoolItem()
        if m and m.GetClipProperty("Type") == "Multicam":
            mcs[m.GetUniqueId()] = m
    print("multicam pool clips:", {u: (m.GetName(), m.GetFlagList()) for u, m in mcs.items()})
    print("bin clips flagged:", {n: c.GetFlagList() for n, c in clips.items() if c.GetFlagList()})
    print("duplicate markers:", dup.GetMarkers())
    if sys.argv[2] == "--apply":
        keep = sys.argv[3:]
        for m in mcs.values():
            if "Yellow" in (m.GetFlagList() or []):
                m.ClearFlags("Yellow")
            check("Yellow" not in (m.GetFlagList() or []), f"multicam clip still Yellow: {m.GetFlagList()}")
        for n, c in clips.items():
            if n not in keep and "Yellow" in (c.GetFlagList() or []):
                c.ClearFlags("Yellow")
            check(("Yellow" in (c.GetFlagList() or [])) == (n in keep), f"{n}: bin flags {c.GetFlagList()}")
        check(all("Yellow" not in (it.GetFlagList() or []) for it in v1), "a V1 item still reads Yellow")
        st0 = dup.GetStartFrame()
        added = []
        for n in keep:
            rec = int(n.split("_rec")[1])
            it = next(i for i in v1 if i.GetStart() == rec)
            name = "Needs dynamic zoom (gigsteady)"
            mks = dup.GetMarkers()
            f = next((k for k, v in mks.items() if v["name"] == name and rec - st0 <= k < it.GetEnd() - st0), None)
            if f is None:  # first free frame of the cut (markers are one per frame; others may sit on the cut start)
                f = next(k for k in range(rec - st0, it.GetEnd() - st0) if k not in mks)
                dup.AddMarker(f, "Rose", name,
                              f"{n}: stabilised clip in bin 'gigsteady good4u_v1'; original's DZ rectangles are not "
                              "the default, so rebuild by hand", it.GetEnd() - rec - (f - (rec - st0)))
            mk = dup.GetMarkers().get(f)
            check(mk is not None and mk["color"] == "Rose" and mk["name"] == name, f"{n}: marker read-back {mk}")
            added.append({"name": n, "marker_frame": f})
            print(n, "marker at timeline frame", f)
        st["flags"] = st.get("flags", []) + [{"cleanup": "Yellow cleared from multicam pool clip and placed bin clips",
                                              "kept_bin_flags": keep, "markers_on_duplicate": added}]
        (job / "place_state.json").write_text(json.dumps(st, indent=1))
finally:
    tl = find_timeline(p, view[0])
    p.SetCurrentTimeline(tl)
    if "in" in view[2]:
        tl.SetMarkInOut(view[2]["in"], view[2]["out"])
    tl.SetCurrentTimecode(view[1])
    check(tl.GetCurrentTimecode() == view[1] and tl.GetMarkInOut().get("video", {}) == view[2], "restore view")
    print("restored", view)
