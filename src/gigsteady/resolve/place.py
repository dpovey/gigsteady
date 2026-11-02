"""Place a finished job's outputs on a DUPLICATE of a cut timeline; the original timeline is never changed.

Each step is one change, read back before the next; the first mismatch stops the run (raise Mismatch). Everything
created (timeline, track, bin, media pool items, timeline items) is recorded in <job>/place_state.json so undo()
removes exactly that. Resolve 21.1 facts relied on: AddTrack only adds at the top; AppendToTimeline's endFrame is
exclusive; AddTrack/ImportMedia/AppendToTimeline can clear In/Out and move the playhead (saved and restored);
GetIsTrackEnabled/GetIsTrackLocked read False on a timeline that isn't the current one (read them while it is).
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


class Mismatch(RuntimeError):
    pass


def check(cond: bool, what: str) -> None:
    if not cond:
        raise Mismatch(what)


def find_timeline(project, name: str):
    for i in range(1, project.GetTimelineCount() + 1):
        t = project.GetTimelineByIndex(i)
        if t.GetName() == name:
            return t
    return None


def snapshot(tl) -> dict:
    """Every video item's (track, start, end, name) for comparing a duplicate with its master."""
    return {t: [(it.GetStart(), it.GetEnd(), it.GetName()) for it in (tl.GetItemListInTrack("video", t) or [])]
            for t in range(1, tl.GetTrackCount("video") + 1)}


def compose(angle: dict, item: dict) -> dict:
    """Angle transform (inside the multicam) then the item's own: zoom multiplies, rotation adds, pan/tilt are the
    item's (applied last, in timeline pixels). The stabiliser's crop is already baked into the output."""
    az = (angle.get("zoom") or (1.0, 1.0))[0]
    return {"ZoomX": round(item.get("ZoomX", 1.0) * az, 6), "ZoomY": round(item.get("ZoomY", 1.0) * az, 6),
            "RotationAngle": round(item.get("RotationAngle", 0.0) + angle.get("rotation", 0.0), 6),
            "Pan": item.get("Pan", 0.0), "Tilt": item.get("Tilt", 0.0)}


class Placer:
    def __init__(self, resolve, job: Path, master_name: str, dup_name: str, log=print):
        self.r, self.job, self.log = resolve, Path(job), log
        self.p = resolve.GetProjectManager().GetCurrentProject()
        self.mp = self.p.GetMediaPool()
        self.master_name, self.dup_name = master_name, dup_name
        self.state_p = self.job / "place_state.json"
        self.state = json.loads(self.state_p.read_text()) if self.state_p.exists() else {
            "project": self.p.GetName(), "master": master_name, "duplicate": dup_name, "created": {}, "placed": [],
            "skipped": [], "started": dt.datetime.now().isoformat()}

    def save(self) -> None:
        self.state_p.write_text(json.dumps(self.state, indent=1))

    def guard(self) -> None:
        check(not self.p.IsRenderingInProgress(), "a render is in progress")

    def run(self, manifest: list[dict], skip_dynamic_zoom: bool = True) -> dict:
        self.guard()
        master = find_timeline(self.p, self.master_name)
        check(master is not None, f"no timeline {self.master_name}")
        check(find_timeline(self.p, self.dup_name) is None, f"timeline {self.dup_name} already exists")
        cur = self.p.GetCurrentTimeline()
        saved = {"timeline": cur.GetName(), "playhead": cur.GetCurrentTimecode(), "inout": cur.GetMarkInOut()}
        self.state["saved_view"] = saved
        before = snapshot(master)

        # 1. duplicate (make master current first so the duplicate is of it, as shown)
        if cur.GetName() != self.master_name:
            self.p.SetCurrentTimeline(master)
            check(self.p.GetCurrentTimeline().GetName() == self.master_name, "could not open master")
        # Track enable/lock states read as False on a timeline that isn't current (21.1), so read them now
        tracks = {t: {"enabled": master.GetIsTrackEnabled("video", t), "locked": master.GetIsTrackLocked("video", t),
                      "name": master.GetTrackName("video", t)} for t in range(1, master.GetTrackCount("video") + 1)}
        self.state["master_tracks"] = tracks
        dup = master.DuplicateTimeline(self.dup_name)
        dup = find_timeline(self.p, self.dup_name)
        check(dup is not None, "duplicate not created")
        self.state["created"]["timeline"] = dup.GetUniqueId()
        self.save()
        check(snapshot(dup) == before, "duplicate's video items differ from master")
        self.log("duplicated ->", self.dup_name)

        # 2. make the duplicate current (AppendToTimeline works on the current timeline)
        self.p.SetCurrentTimeline(dup)
        check(self.p.GetCurrentTimeline().GetUniqueId() == dup.GetUniqueId(), "could not open the duplicate")

        # 3. new top track, then shift tracks 2..n up one so track 2 is free (titles first, from the top down)
        n = dup.GetTrackCount("video")
        dup.AddTrack("video")
        check(dup.GetTrackCount("video") == n + 1, "AddTrack did not add a video track")
        self.state["created"]["track_added"] = n + 1
        self.save()
        for t in range(n, 1, -1):
            items = dup.GetItemListInTrack("video", t) or []
            want = [(it.GetStart(), it.GetEnd(), it.GetName()) for it in items]
            if items:
                dup.MoveItems(items, {"trackIndex": t + 1}, False)
            got = [(it.GetStart(), it.GetEnd(), it.GetName()) for it in (dup.GetItemListInTrack("video", t + 1) or [])]
            check(got == want and not (dup.GetItemListInTrack("video", t) or []), f"moving track {t} -> {t + 1}")
            check(not tracks[t]["locked"], f"track {t} is locked")
            dup.SetTrackEnable("video", t + 1, tracks[t]["enabled"])
            check(dup.GetIsTrackEnabled("video", t + 1) == tracks[t]["enabled"], f"track {t + 1} enable")
            dup.SetTrackName("video", t + 1, tracks[t]["name"])
            check(dup.GetTrackName("video", t + 1) == tracks[t]["name"], f"track name {t + 1}")
            self.log(f"moved track {t} ({len(items)} items) -> {t + 1}")
        dup.SetTrackEnable("video", 2, True)
        check(dup.GetIsTrackEnabled("video", 2), "track 2 enable")
        dup.SetTrackName("video", 2, "gigsteady")
        check(dup.GetTrackName("video", 2) == "gigsteady", "track 2 name")
        exp = {1: before[1], 2: []}
        exp.update({t + 1: before[t] for t in range(2, n + 1)})
        check(snapshot(dup) == exp, "duplicate's tracks after the shift")
        want_en = {1: tracks[1]["enabled"], 2: True, **{t + 1: tracks[t]["enabled"] for t in range(2, n + 1)}}
        got_en = {t: dup.GetIsTrackEnabled("video", t) for t in want_en}
        check(got_en == want_en, f"duplicate's track enables {got_en} != {want_en}")

        # 4. bin + import
        root = self.mp.GetRootFolder()
        name = f"gigsteady {self.job.name}"
        check(not any(f.GetName() == name for f in root.GetSubFolderList()), f"bin {name} already exists")
        folder = self.mp.AddSubFolder(root, name)
        check(folder is not None and folder.GetName() == name, "bin not created")
        self.state["created"]["bin"] = folder.GetUniqueId()
        self.save()
        self.mp.SetCurrentFolder(folder)
        rows = [m for m in manifest if m["status"] == "ok"]
        mpis = self.mp.ImportMedia([m["output"] for m in rows]) or []
        self.state["created"]["media"] = [x.GetUniqueId() for x in mpis]
        self.save()
        by_path = {x.GetClipProperty("File Path"): x for x in mpis}
        check(len(mpis) == len(rows) and all(m["output"] in by_path for m in rows), "import count/paths")
        self.log(f"imported {len(mpis)} clips into bin {name}")

        # 5. place, grade, transform: one clip at a time
        v1 = {(it.GetStart(), it.GetEnd()): it for it in dup.GetItemListInTrack("video", 1)}
        self.state["created"]["items"] = []
        for m in rows:
            orig = v1.get((m["record_start"], m["record_end"]))
            check(orig is not None and orig.GetName().endswith(m["angle"]), f"{m['name']}: original not found on V1")
            props = orig.GetProperty() or {}
            if skip_dynamic_zoom and props.get("DynamicZoomEnabled"):
                self.state["skipped"].append({"name": m["name"], "reason": "dynamic zoom on the original"})
                self.save()
                self.log(m["name"], "skipped (dynamic zoom)")
                continue
            new = self.mp.AppendToTimeline([{"mediaPoolItem": by_path[m["output"]], "startFrame": m["place_start"],
                                             "endFrame": m["place_end"], "recordFrame": m["record_start"],
                                             "trackIndex": 2, "mediaType": 1}]) or []
            check(len(new) == 1, f"{m['name']}: append returned {len(new)} items")
            it = new[0]
            self.state["created"]["items"].append(it.GetUniqueId())
            self.save()
            check((it.GetStart(), it.GetEnd(), it.GetLeftOffset()) ==
                  (m["record_start"], m["record_end"], m["place_start"]),
                  f"{m['name']}: placed at {(it.GetStart(), it.GetEnd(), it.GetLeftOffset())}")
            g = orig.GetColorGroup()
            if g is not None:
                it.AssignToColorGroup(g)
                check(it.GetColorGroup() is not None and it.GetColorGroup().GetName() == g.GetName(),
                      f"{m['name']}: colour group")
            orig.CopyGrades([it])
            og, ng = orig.GetNodeGraph(), it.GetNodeGraph()
            check(og.GetNumNodes() == ng.GetNumNodes() and
                  all(og.GetLUT(i) == ng.GetLUT(i) for i in range(1, og.GetNumNodes() + 1)),
                  f"{m['name']}: grade read-back")
            tr = compose(m.get("angle_transform") or {}, {k: props.get(k) for k in
                                                         ("ZoomX", "ZoomY", "RotationAngle", "Pan", "Tilt")
                                                         if props.get(k) is not None})
            for k, v in tr.items():
                if abs((it.GetProperty(k) or 0) - v) > 1e-4:
                    it.SetProperty(k, v)
                    check(abs(it.GetProperty(k) - v) < 1e-3, f"{m['name']}: {k} read back {it.GetProperty(k)} != {v}")
            self.state["placed"].append({"name": m["name"], "item": it.GetUniqueId(), "original": orig.GetUniqueId(),
                                         "nodes": ng.GetNumNodes(), "lut": ng.GetLUT(1) if ng.GetNumNodes() else None,
                                         "group": g.GetName() if g else None, "transform": tr})
            self.save()
            self.log(m["name"], "placed", tr)

        # 6. master untouched; restore the view
        check(snapshot(master) == before, "master changed")
        self.restore()
        self.state["finished"] = dt.datetime.now().isoformat()
        self.save()
        return self.state

    def restore(self) -> None:
        s = self.state.get("saved_view")
        if not s:
            return
        tl = find_timeline(self.p, s["timeline"])
        self.p.SetCurrentTimeline(tl)
        io = s["inout"].get("video", {})
        if "in" in io and "out" in io:
            tl.SetMarkInOut(io["in"], io["out"])
        tl.SetCurrentTimecode(s["playhead"])
        check(self.p.GetCurrentTimeline().GetName() == s["timeline"], "restore: timeline")
        check(tl.GetCurrentTimecode() == s["playhead"], f"restore: playhead {tl.GetCurrentTimecode()}")
        check(tl.GetMarkInOut().get("video", {}) == io, f"restore: In/Out {tl.GetMarkInOut()}")
        self.log("restored", s["timeline"], s["playhead"], io)


def undo(resolve, job: Path, log=print) -> None:
    """Remove exactly what place created: the duplicate timeline (with its items) and the bin's imported clips."""
    st_p = Path(job) / "place_state.json"
    st = json.loads(st_p.read_text())
    p = resolve.GetProjectManager().GetCurrentProject()
    check(p.GetName() == st["project"], "different project open")
    check(not p.IsRenderingInProgress(), "a render is in progress")
    mp = p.GetMediaPool()
    tl = find_timeline(p, st["duplicate"])
    if tl is not None:
        check(tl.GetUniqueId() == st["created"].get("timeline"), "duplicate's id differs from the one created")
        if p.GetCurrentTimeline().GetUniqueId() == tl.GetUniqueId():
            p.SetCurrentTimeline(find_timeline(p, st["master"]))
        mp.DeleteTimelines([tl])
        check(find_timeline(p, st["duplicate"]) is None, "duplicate still there")
        log("deleted timeline", st["duplicate"])
    root = mp.GetRootFolder()
    for f in root.GetSubFolderList():
        if f.GetUniqueId() == st["created"].get("bin"):
            ids = set(st["created"].get("media", []))
            clips = [c for c in f.GetClipList() if c.GetUniqueId() in ids]
            check(len(clips) == len(f.GetClipList()), "bin holds clips gigsteady didn't import; leaving it")
            mp.DeleteClips(clips)
            mp.DeleteFolders([f])
            log("deleted bin", f.GetName(), "and", len(clips), "clips")
    st["undone"] = dt.datetime.now().isoformat()
    st_p.write_text(json.dumps(st, indent=1))


def item_state(it) -> dict:
    g = it.GetNodeGraph()
    n = g.GetNumNodes() if g else 0
    return {"start": it.GetStart(), "end": it.GetEnd(), "left": it.GetLeftOffset(), "enabled": it.GetClipEnabled(),
            "nodes": n, "luts": [g.GetLUT(i) for i in range(1, n + 1)] if g else [],
            **{k: it.GetProperty(k) for k in ("ZoomX", "ZoomY", "RotationAngle", "Pan", "Tilt")}}


def replace_media(resolve, job: Path, new_outputs: Path, log=print) -> dict:
    """Point the media pool clips place() imported at re-rendered files (same names in new_outputs), keeping the
    timeline items. Reads every placed item's position, grade and transform before and after, on the duplicate
    while it is current, then restores the saved view. Stops on the first mismatch."""
    st_p = Path(job) / "place_state.json"
    st = json.loads(st_p.read_text())
    p = resolve.GetProjectManager().GetCurrentProject()
    check(p.GetName() == st["project"], "different project open")
    check(not p.IsRenderingInProgress(), "a render is in progress")
    cur = p.GetCurrentTimeline()
    view = {"timeline": cur.GetName(), "playhead": cur.GetCurrentTimecode(), "inout": cur.GetMarkInOut()}
    dup = find_timeline(p, st["duplicate"])
    check(dup is not None and dup.GetUniqueId() == st["created"]["timeline"], "duplicate not found")
    p.SetCurrentTimeline(dup)
    check(p.GetCurrentTimeline().GetUniqueId() == dup.GetUniqueId(), "could not open the duplicate")
    try:
        want = {x["item"]: x["name"] for x in st["placed"] + st.get("placed_by_editor", [])}
        items = {it.GetUniqueId(): it for it in (dup.GetItemListInTrack("video", 2) or []) if it.GetUniqueId() in want}
        check(len(items) == len(want), f"found {len(items)} of {len(want)} placed items")
        before = {i: item_state(it) for i, it in items.items()}
        folder = next(f for f in p.GetMediaPool().GetRootFolder().GetSubFolderList()
                      if f.GetUniqueId() == st["created"]["bin"])
        ids = set(st["created"]["media"])
        swapped = []
        for c in folder.GetClipList():
            if c.GetUniqueId() not in ids:
                continue
            old = c.GetClipProperty("File Path")
            new = str(Path(new_outputs) / Path(old).name)
            check(Path(new).exists(), f"missing {new}")
            if old == new:
                continue
            ok = c.ReplaceClip(new)
            check(c.GetClipProperty("File Path") == new, f"{Path(old).name}: File Path {c.GetClipProperty('File Path')}"
                  f" after ReplaceClip (returned {ok})")
            swapped.append({"clip": c.GetUniqueId(), "from": old, "to": new})
            log("replaced", Path(old).name, "->", new)
        after = {i: item_state(it) for i, it in items.items()}
        for i in items:
            check(before[i] == after[i], f"{want[i]}: item changed {before[i]} -> {after[i]}")
        st.setdefault("replaced", []).append({"at": dt.datetime.now().isoformat(), "to": str(new_outputs),
                                              "clips": swapped})
        st_p.write_text(json.dumps(st, indent=1))
        log(f"{len(swapped)} clips replaced; {len(items)} placed items unchanged (position, grade, transform)")
        return st
    finally:
        tl = find_timeline(p, view["timeline"])
        p.SetCurrentTimeline(tl)
        io = view["inout"].get("video", {})
        if "in" in io and "out" in io:
            tl.SetMarkInOut(io["in"], io["out"])
        tl.SetCurrentTimecode(view["playhead"])
        check(p.GetCurrentTimeline().GetName() == view["timeline"], "restore: timeline")
        check(tl.GetCurrentTimecode() == view["playhead"], "restore: playhead")
        check(tl.GetMarkInOut().get("video", {}) == io, "restore: In/Out")
        log("restored", view["timeline"], view["playhead"], io)
