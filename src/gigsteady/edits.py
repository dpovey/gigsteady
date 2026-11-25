"""Planning rules that keep the stabiliser's crop from showing as a zoom jump at an edit. Pure functions: no Resolve.

A stabilised overlay is a tighter crop of its picture (the output's own zoom, about 1.10 in track mode). Next to a cut
of another camera that difference is invisible. Next to the *same* picture it reads as a zoom jump: one delivered
song jumped x1.098 at a through-edit where one side was a Dynamic Zoom cut (skipped, so not
stabilised) and the other a stabilised overlay; the same edit measured x0.998 before stabilisation.

- `through_edits`: adjacent main-track items on the same angle and source file whose source frames continue across the
  edit (an edit inside one camera take).
- `apply_through_edits` (rule a, selection): a through-edit is one shot. If every part can be stabilised, the parts
  become one shot with one camera path across the edit, placed back part by part. If a part can't (Dynamic Zoom, or
  not selected), none of them is stabilised ("neither", the default), or all are rendered and the parts that can't
  be placed by script are left for the editor ("render-all").
- `overlay_zoom` (rule b, placement): the overlay's Zoom is the editor's zoom over the stabiliser's crop, never less
  than what covers the frame at the item's rotation, pan and tilt.
- `crop_matches` / `matched_zoom`: unstabilised neighbours that show the same picture as an overlay (through-edit
  partners left unstabilised), and the Zoom that gives them the overlay's crop at the edit.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# A cut list read through the API can put a multicam item's source frame one early (GetSourceStartFrame rounding,
# seen on a 2026 multicam), so continuity is allowed one frame of slack.
SOURCE_SLACK = 1


@dataclass
class Item:
    """One main-track timeline item, in record frames (end exclusive) with the source frame at its start."""
    record_start: int
    record_end: int
    angle: str
    src: str | None
    src_in: int | None
    dynamic_zoom: bool = False
    name: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def frames(self) -> int:
        return self.record_end - self.record_start


def item_from_dict(d: dict) -> Item:
    """Accepts gigsteady shot rows ({src, in, record_start, record_end}) and drp/cut-list rows
    ({angle_file_path, angle_source_frame, record_in, record_out})."""
    rs = d.get("record_start", d.get("record_in"))
    re_ = d.get("record_end", d.get("record_out"))
    return Item(record_start=int(rs), record_end=int(re_), angle=d.get("angle") or "",
                src=d.get("src", d.get("angle_file_path")), src_in=d.get("in", d.get("angle_source_frame")),
                dynamic_zoom=bool(d.get("dynamic_zoom")), name=d.get("name", ""))


def continues(a: Item, b: Item, slack: int = SOURCE_SLACK) -> bool:
    """b starts where a ends, on the same angle and file, with the source running on across the edit."""
    if a.record_end != b.record_start or a.angle != b.angle or not a.src or a.src != b.src:
        return False
    if a.src_in is None or b.src_in is None:
        return False
    return abs((a.src_in + a.frames) - b.src_in) <= slack


def through_edits(items: list[Item]) -> list[list[Item]]:
    """Chains of two or more items joined by through-edits, in record order."""
    items = sorted(items, key=lambda i: i.record_start)
    groups, cur = [], [items[0]] if items else []
    for a, b in zip(items, items[1:]):
        if continues(a, b):
            cur.append(b)
        else:
            if len(cur) > 1:
                groups.append(cur)
            cur = [b]
    if len(cur) > 1:
        groups.append(cur)
    return groups


def apply_through_edits(shots: list[dict], items: list[Item], policy: str = "neither") -> tuple[list[dict], list[dict]]:
    """Rule (a). `shots`: the selected gigsteady shots (each with record_start/record_end, src, in, out). `items`: every
    main-track item of the range, selected or not, so partners that weren't selected are seen.

    Returns (shots, decisions). A through-edit whose parts are all selected and none is Dynamic Zoom becomes one shot
    spanning the chain, with "parts" listing each part's record range and its offset into the merged shot, so it is
    placed part by part with one camera path. Otherwise, under "neither" every selected part of the chain is dropped;
    under "render-all" the chain is rendered as one shot and parts that can't be placed by script are marked
    "place": "editor". Decisions say what happened to each chain and why.
    """
    if policy not in ("neither", "render-all"):
        raise ValueError(f"policy {policy!r}: 'neither' or 'render-all'")
    by_start = {s["record_start"]: s for s in shots}
    out, decisions, used = [], [], set()
    for chain in through_edits(items):
        sel = [by_start.get(p.record_start) for p in chain]
        if not any(sel):
            continue
        blocked = [p for p, s in zip(chain, sel) if s is None or p.dynamic_zoom]
        ranges = [(p.record_start, p.record_end) for p in chain]
        if blocked and policy == "neither":
            for s in filter(None, sel):
                used.add(s["record_start"])
            why = ", ".join(f"{p.record_start} {'Dynamic Zoom' if p.dynamic_zoom else 'not selected'}" for p in blocked)
            decisions.append({"chain": ranges, "action": "dropped",
                              "why": f"through-edit with a part that can't be stabilised ({why}); stabilising only "
                                     "some parts shows the crop as a zoom jump at the edit"})
            continue
        base = next(s for s in sel if s)
        src, src_in = chain[0].src, chain[0].src_in
        merged = {**{k: v for k, v in base.items() if k not in ("item_id", "item_transform")},
                  "name": f"{(base.get('name') or 'TE').split('_rec')[0]}_rec{chain[0].record_start}_te",
                  "src": src, "in": src_in, "out": src_in + sum(p.frames for p in chain),
                  "record_start": chain[0].record_start, "record_end": chain[-1].record_end, "angle": chain[0].angle,
                  "parts": []}
        off = 0
        for p, s in zip(chain, sel):
            part = {"record_start": p.record_start, "record_end": p.record_end, "offset": off,
                    "item_id": (s or {}).get("item_id"), "item_transform": (s or {}).get("item_transform") or {}}
            if s is None or p.dynamic_zoom:
                part["place"] = "editor"
                part["why"] = "Dynamic Zoom" if p.dynamic_zoom else "not selected"
            off += p.frames
            merged["parts"].append(part)
            if s:
                used.add(s["record_start"])
        out.append(merged)
        decisions.append({"chain": ranges, "action": "merged", "name": merged["name"],
                          "editor_parts": [pt["record_start"] for pt in merged["parts"] if pt.get("place") == "editor"]})
    out += [s for s in shots if s["record_start"] not in used]
    out.sort(key=lambda s: s["record_start"])
    return out, decisions


def cover_zoom(pan: float = 0.0, tilt: float = 0.0, width: int = 3840, height: int = 2160, rotation: float = 0.0) -> float:
    """The least Zoom at which a full-frame image still covers the frame after a Rotation (degrees) and a Pan/Tilt in
    timeline pixels. Rotation needs cos t + (long/short) sin t; the pan/tilt margin is added on top (conservative)."""
    t = math.radians(abs(rotation) % 180.0)
    t = min(t, math.pi - t)
    rot = math.cos(t) + max(width, height) / min(width, height) * math.sin(t)
    return max(1.0, rot + 2.0 * abs(pan) / width, rot + 2.0 * abs(tilt) / height)


def overlay_zoom(editor_zoom: float, stab_zoom: float, pan: float = 0.0, tilt: float = 0.0,
                 width: int = 3840, height: int = 2160, rotation: float = 0.0) -> float:
    """Rule (b). The output already carries the stabiliser's crop, so the editor's framing is editor/stab; it may not
    fall under what covers the frame at the item's pan/tilt (on one song 0.909 would have shown black bands; three
    overlays with ~900 px pans needed 1.4675-1.485)."""
    if stab_zoom <= 0:
        raise ValueError(f"stab_zoom {stab_zoom}")
    return max(editor_zoom / stab_zoom, cover_zoom(pan, tilt, width, height, rotation))


def crop_matches(shots: list[dict], items: list[Item]) -> list[dict]:
    """Unstabilised items that touch an overlay part across a through-edit (the same picture on both sides), each once.
    The Zoom that matches the crop depends on both sides' placed transforms, so placement computes it
    (`matched_zoom`). Under rule (a) "neither" there are none; this is the safety net for lists built without it."""
    covered = {}
    for s in shots:
        for pt in s.get("parts") or [{"record_start": s["record_start"], "record_end": s["record_end"]}]:
            if pt.get("place") != "editor":
                covered[(pt["record_start"], pt["record_end"])] = s["name"]
    out = {}
    srt = sorted(items, key=lambda i: i.record_start)
    for a, b in zip(srt, srt[1:]):
        if not continues(a, b):
            continue
        for bare, over in ((a, b), (b, a)):
            kb, ko = (bare.record_start, bare.record_end), (over.record_start, over.record_end)
            if kb in covered or ko not in covered or kb in out:
                continue
            out[kb] = {"record_start": kb[0], "record_end": kb[1], "angle": bare.angle, "overlay": covered[ko],
                       "overlay_record_start": ko[0], "overlay_record_end": ko[1], "dynamic_zoom": bare.dynamic_zoom}
    return [out[k] for k in sorted(out)]


def matched_zoom(bare_zoom: float, overlay_editor_zoom: float, overlay_placed_zoom: float, stab_zoom: float,
                 angle_zoom: float = 1.0, tol: float = 1e-3) -> float | None:
    """The bare item's new Zoom so it shows the overlay's crop: on screen the overlay is placed x stab (relative to the
    source), the bare item is its zoom x the angle's. None when the two sides' editor zooms differ (the editor changed
    framing on purpose) or no change is needed."""
    if abs(bare_zoom - overlay_editor_zoom) > tol:
        return None
    want = overlay_placed_zoom * stab_zoom / angle_zoom
    return None if abs(want - bare_zoom) <= tol else want
