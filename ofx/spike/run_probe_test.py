"""Throwaway Resolve test for the gigsteady OFX probe (system Python, Resolve running, probe installed).

Usage: /usr/bin/python3 ofx/spike/run_probe_test.py build    # timeline + clips + probe effect, then step the playhead
       /usr/bin/python3 ofx/spike/run_probe_test.py step     # step the playhead again (viewer renders)
       /usr/bin/python3 ofx/spike/run_probe_test.py cleanup  # delete the test timeline (by the id recorded at build)

Builds "gigsteady OFX probe test" in the current project with: a plain clip and a trimmed clip of the Static Desk file,
and a cut of the multicam clip. Applies "gigsteady probe" to each with TimelineItem.AddEffect("ofx", id). Stepping the
playhead over each clip makes the viewer render, which the probe logs. A Deliver render is left to the editor's
render tool (it saves and restores the render settings); this script never starts a render. Every change is read
back; the current timeline, playhead and In/Out are restored. State: work/ofx_probe/state.json.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from gigsteady.resolve.bridge import connect, guard  # noqa: E402
from gigsteady.resolve.place import check, find_timeline, find_timeline_by_id  # noqa: E402

NAME = "gigsteady OFX probe test"
STATE = ROOT / "work" / "ofx_probe" / "state.json"
SD_FILE = "/Volumes/BOTTB SYD26/STATIC DESKCAM/3O1A7144.MP4"
MULTICAM = "Sydney 2026 Multicam"
CLIPS = [("plain", SD_FILE, 65899, 66004), ("trimmed", SD_FILE, 65950, 65990), ("multicam", MULTICAM, 76359, 76464)]


def walk(folder):
    for c in folder.GetClipList() or []:
        yield c
    for f in folder.GetSubFolderList() or []:
        yield from walk(f)


def tc(frame: int, fps: int = 25) -> str:
    s, f = divmod(frame, fps)
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{f:02d}"


def main() -> None:
    guard()
    cmd = sys.argv[1]
    r = connect()
    p = r.GetProjectManager().GetCurrentProject()
    check(not p.IsRenderingInProgress(), "a render is in progress")
    cur = p.GetCurrentTimeline()
    view = (cur.GetUniqueId(), cur.GetCurrentTimecode(), cur.GetMarkInOut().get("video", {}))
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    try:
        if cmd == "build":
            check(find_timeline(p, NAME) is None, f"{NAME!r} already exists")
            fx = [e for e in r.GetEffectsList("video") if "gigsteady probe" in json.dumps(e)]
            check(len(fx) == 1, f"probe not found in GetEffectsList (installed and Resolve restarted?): {fx}")
            print("effect:", fx[0])
            fx_id = fx[0].get("id") or fx[0].get("ID") or fx[0].get("Id")
            mp = p.GetMediaPool()
            items = {}
            for c in walk(mp.GetRootFolder()):
                if c.GetClipProperty("File Path") == SD_FILE:
                    items[SD_FILE] = c
                if c.GetName() == MULTICAM and c.GetClipProperty("Type") == "Multicam":
                    items[MULTICAM] = c
            check(SD_FILE in items and MULTICAM in items, f"media not found: {list(items)}")
            tl = mp.CreateEmptyTimeline(NAME)
            check(tl is not None, "CreateEmptyTimeline failed")
            st = {"timeline": tl.GetUniqueId(), "name": NAME, "clips": []}
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_text(json.dumps(st, indent=1))
            p.SetCurrentTimeline(tl)
            check(p.GetCurrentTimeline().GetUniqueId() == tl.GetUniqueId(), "could not open the test timeline")
            for label, key, a, b in CLIPS:
                # AppendToTimeline goes to the current timeline: stop if it changed under us
                check(p.GetCurrentTimeline().GetUniqueId() == tl.GetUniqueId(), "current timeline changed; stopping")
                new = mp.AppendToTimeline([{"mediaPoolItem": items[key], "startFrame": a, "endFrame": b,
                                            "mediaType": 1, "trackIndex": 1}]) or []
                check(len(new) == 1, f"{label}: append returned {len(new)}")
                it = new[0]
                e = it.AddEffect("ofx", fx_id)
                check(e is not None and len(it.GetEffects("ofx") or []) == 1, f"{label}: AddEffect read-back")
                row = {"label": label, "source": key, "src_in": a, "src_out": b, "start": it.GetStart(),
                       "end": it.GetEnd(), "left_offset": it.GetLeftOffset(), "item": it.GetUniqueId()}
                st["clips"].append(row)
                STATE.write_text(json.dumps(st, indent=1))
                print("placed", row)
            cmd = "step"
        if cmd == "step":
            tl = find_timeline_by_id(p, st["timeline"])
            check(tl is not None, "test timeline not found")
            p.SetCurrentTimeline(tl)
            for c in st["clips"]:
                for f in range(c["start"], min(c["end"], c["start"] + 12)):
                    tl.SetCurrentTimecode(tc(f))
                    time.sleep(0.4)  # let the viewer render this frame
                print("stepped", c["label"], c["start"], "..", min(c["end"], c["start"] + 12))
        if cmd == "cleanup":
            tl = find_timeline_by_id(p, st.get("timeline", ""))
            check(tl is not None and tl.GetName() == NAME, "test timeline not found or renamed; leaving it")
            if p.GetCurrentTimeline().GetUniqueId() == tl.GetUniqueId():
                others = [p.GetTimelineByIndex(i) for i in range(1, p.GetTimelineCount() + 1)]
                others = [o for o in others if o.GetUniqueId() != tl.GetUniqueId()]
                check(others and p.SetCurrentTimeline(others[0]), "could not switch off the test timeline")
            p.GetMediaPool().DeleteTimelines([tl])
            check(find_timeline_by_id(p, st["timeline"]) is None, "test timeline still there")
            st["deleted"] = True
            STATE.write_text(json.dumps(st, indent=1))
            print("deleted", NAME)
    finally:
        tl = find_timeline_by_id(p, view[0])
        if tl is not None:
            p.SetCurrentTimeline(tl)
            if "in" in view[2]:
                tl.SetMarkInOut(view[2]["in"], view[2]["out"], "video")
            tl.SetCurrentTimecode(view[1])
            print("restored", view)


if __name__ == "__main__":
    main()
