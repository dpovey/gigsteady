"""Flag cuts on the duplicate timeline (V1 items by record start) and their clips in gigsteady's bin; read back.
Usage: /usr/bin/python3 scripts/resolve_flags.py <job_dir> <colour> <name> [<name> ...]   (names like SD_rec151966)
       /usr/bin/python3 scripts/resolve_flags.py <job_dir> --survey   (read-only: flag colours in use)
Opens the duplicate to read back (flag reads on a non-current timeline aren't trusted), restores the view."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from gigsteady.resolve.bridge import connect, guard  # noqa: E402
from gigsteady.resolve.place import check, find_timeline, find_timeline_by_id  # noqa: E402

guard()
job = Path(sys.argv[1])
st = json.loads((job / "place_state.json").read_text())
p = connect().GetProjectManager().GetCurrentProject()
check(p.GetName() == st["project"] and not p.IsRenderingInProgress(), "wrong project or rendering")
cur = p.GetCurrentTimeline()
view = (cur.GetName(), cur.GetCurrentTimecode(), cur.GetMarkInOut().get("video", {}))
dup = find_timeline_by_id(p, st["created"]["timeline"])
folder = next(f for f in p.GetMediaPool().GetRootFolder().GetSubFolderList() if f.GetUniqueId() == st["created"]["bin"])
clips = {Path(c.GetClipProperty("File Path")).stem: c for c in folder.GetClipList()}
p.SetCurrentTimeline(dup)
try:
    v1 = {it.GetStart(): it for it in dup.GetItemListInTrack("video", 1)}
    if sys.argv[2] == "--survey":
        used = {}
        for t in range(1, dup.GetTrackCount("video") + 1):
            for it in dup.GetItemListInTrack("video", t) or []:
                for f in it.GetFlagList() or []:
                    used[f] = used.get(f, 0) + 1
        print("flags on duplicate items:", used, "| bin clips:", {n: c.GetFlagList() for n, c in clips.items() if c.GetFlagList()})
    else:
        colour, names = sys.argv[2], sys.argv[3:]
        done = []
        for n in names:
            rec = int(n.split("_rec")[1])
            it = v1[rec]
            check(it.GetName().endswith("Static Desk" if n.startswith("SD") else "Photo Pit"), f"{n}: V1 item angle")
            if colour not in (it.GetFlagList() or []):
                it.AddFlag(colour)
            check(colour in (it.GetFlagList() or []), f"{n}: item flag read-back {it.GetFlagList()}")
            c = clips[n]
            if colour not in (c.GetFlagList() or []):
                c.AddFlag(colour)
            check(colour in (c.GetFlagList() or []), f"{n}: clip flag read-back {c.GetFlagList()}")
            done.append({"name": n, "item": it.GetUniqueId(), "clip": c.GetUniqueId()})
            print(n, "flagged", colour, "item", it.GetStart(), "-", it.GetEnd())
        st.setdefault("flags", []).append({"colour": colour, "why": "needs dynamic zoom", "cuts": done})
        (job / "place_state.json").write_text(json.dumps(st, indent=1))
finally:
    tl = find_timeline(p, view[0])
    p.SetCurrentTimeline(tl)
    if "in" in view[2]:
        tl.SetMarkInOut(view[2]["in"], view[2]["out"])
    tl.SetCurrentTimecode(view[1])
    check(tl.GetCurrentTimecode() == view[1] and tl.GetMarkInOut().get("video", {}) == view[2], "restore view")
    print("restored", view)
