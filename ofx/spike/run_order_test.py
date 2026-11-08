"""Throwaway Resolve test: are the grade and the clip zoom applied after an Edit-page OFX effect?

Usage: /usr/bin/python3 ofx/spike/run_order_test.py build     # timeline, clips, effect, grade/zoom, frame exports
       /usr/bin/python3 ofx/spike/run_order_test.py cleanup   # delete the test timeline (by the id recorded at build)

Needs the spike bundle installed (it holds "Gig-steady", com.dpovey.gigsteady.preview). Turns on the plugin's test
pattern with the flag file ~/Library/Logs/gigsteady-test-pattern (scripts can't set effect parameters) and removes it
afterwards. Builds "gigsteady OFX order test" with five 25-frame copies of one Static Desk shot:
  ref        no effect, no grade            fx         effect only
  grade      no effect, CDL slope 0.5       fx_grade   effect + CDL slope 0.5
  fx_zoom    effect + clip Zoom 1.5
exports frame 5 of each with TimelineItem.ExportFrame to work/ofx_probe/order/, and measures the white square: its
brightness says whether the grade came after the effect, its width whether the zoom did. Never renders. Restores the
current timeline, playhead and In/Out. State: work/ofx_probe/order_state.json.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from gigsteady.resolve.bridge import connect, guard  # noqa: E402
from gigsteady.resolve.place import check, find_timeline, find_timeline_by_id  # noqa: E402

NAME = "gigsteady OFX order test"
OUT = ROOT / "work" / "ofx_probe" / "order"
STATE = ROOT / "work" / "ofx_probe" / "order_state.json"
FLAG = Path.home() / "Library" / "Logs" / "gigsteady-test-pattern"
SD_FILE = os.environ.get("GIGSTEADY_SD_FILE", "")   # full path of the Static Deskcam clip 3O1A7144.MP4 as Resolve sees it
if not SD_FILE:
    sys.exit("set GIGSTEADY_SD_FILE to the full path of STATIC DESKCAM/3O1A7144.MP4")
SRC_IN = 65899
CASES = [("ref", False, None, None), ("fx", True, None, None), ("grade", False, 0.5, None),
         ("fx_grade", True, 0.5, None), ("fx_zoom", True, None, 1.5)]


def measure(png: Path) -> dict:
    """White square: mean brightness of its middle and its width along the centre row (pixels above 0.9 of max)."""
    import cv2  # the repo venv has it; Resolve's python may not, so measure is also run from the venv
    im = cv2.imread(str(png), cv2.IMREAD_UNCHANGED).astype(float)
    im = im / (65535 if im.max() > 255 else 255)
    h, w = im.shape[:2]
    mid = im[h // 2 - 8:h // 2 + 8, w // 2 - 8:w // 2 + 8, :3].mean(axis=(0, 1))
    row = im[h // 2, :, :3].min(axis=1)  # white = all channels high
    hi = row > 0.9 * row.max()
    xs = [x for x in range(w) if hi[x]]
    run = 0
    if xs:  # the run containing the centre
        x0 = x1 = w // 2
        while x0 > 0 and hi[x0 - 1]:
            x0 -= 1
        while x1 < w - 1 and hi[x1 + 1]:
            x1 += 1
        run = x1 - x0 + 1 if hi[w // 2] else 0
    return {"size": [w, h], "centre_rgb": [round(v, 3) for v in mid], "white_run_px": run}


def main() -> None:
    guard()
    cmd = sys.argv[1]
    if cmd == "measure":  # run from the repo venv: .venv/bin/python ofx/spike/run_order_test.py measure
        for c in CASES:
            print(c[0], measure(OUT / f"{c[0]}.png"))
        return
    r = connect()
    p = r.GetProjectManager().GetCurrentProject()
    check(not p.IsRenderingInProgress(), "a render is in progress")
    cur = p.GetCurrentTimeline()
    view = (cur.GetUniqueId(), cur.GetCurrentTimecode(), cur.GetMarkInOut().get("video", {}))
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    try:
        if cmd == "build":
            check(find_timeline(p, NAME) is None, f"{NAME!r} already exists")
            fx = [e for e in r.GetEffectsList("video") if e.get("name") == "Gig-steady"]
            check(len(fx) == 1, f"Gig-steady not in GetEffectsList (installed and Resolve restarted?): {fx}")
            mp = p.GetMediaPool()

            def walk(folder):
                yield from folder.GetClipList() or []
                for f in folder.GetSubFolderList() or []:
                    yield from walk(f)
            items = [c for c in walk(mp.GetRootFolder()) if c.GetClipProperty("File Path") == SD_FILE]
            check(len(items) >= 1, "Static Desk clip not in the media pool")
            tl = mp.CreateEmptyTimeline(NAME)
            check(tl is not None, "CreateEmptyTimeline failed")
            st = {"timeline": tl.GetUniqueId(), "name": NAME, "cases": []}
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_text(json.dumps(st, indent=1))
            p.SetCurrentTimeline(tl)
            OUT.mkdir(parents=True, exist_ok=True)
            FLAG.write_text("on\n")
            for label, use_fx, slope, zoom in CASES:
                check(p.GetCurrentTimeline().GetUniqueId() == tl.GetUniqueId(), "current timeline changed; stopping")
                new = mp.AppendToTimeline([{"mediaPoolItem": items[0], "startFrame": SRC_IN, "endFrame": SRC_IN + 25,
                                            "mediaType": 1, "trackIndex": 1}]) or []
                check(len(new) == 1, f"{label}: append returned {len(new)}")
                it = new[0]
                if use_fx:
                    check(it.AddEffect("ofx", fx[0]["id"]) is not None and len(it.GetEffects("ofx") or []) == 1,
                          f"{label}: AddEffect read-back")
                if slope is not None:
                    check(it.SetCDL({"NodeIndex": "1", "Slope": f"{slope} {slope} {slope}", "Offset": "0 0 0",
                                     "Power": "1 1 1", "Saturation": "1"}), f"{label}: SetCDL")
                if zoom is not None:
                    check(it.SetProperty("ZoomX", zoom) and it.SetProperty("ZoomY", zoom), f"{label}: zoom")
                    check(abs(it.GetProperty("ZoomX") - zoom) < 1e-6, f"{label}: zoom read-back")
                png = OUT / f"{label}.png"
                res = it.ExportFrame(5, {"outputType": "file", "filePath": str(png)})
                check(res.get("success") and png.exists(), f"{label}: ExportFrame {res.get('error')}")
                st["cases"].append({"label": label, "item": it.GetUniqueId(), "png": str(png)})
                STATE.write_text(json.dumps(st, indent=1))
                print("exported", label, png)
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
        FLAG.unlink(missing_ok=True)
        tl = find_timeline_by_id(p, view[0])
        if tl is not None:
            p.SetCurrentTimeline(tl)
            if "in" in view[2]:
                tl.SetMarkInOut(view[2]["in"], view[2]["out"], "video")
            tl.SetCurrentTimecode(view[1])
            print("restored", view)


if __name__ == "__main__":
    main()
