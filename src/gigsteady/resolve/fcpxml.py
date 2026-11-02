"""Read a Resolve FCPXML 1.10 export: multicam angles and the cut's multicam items -> source file + frames.

Resolve's API can't say which angle a multicam item shows or where in the angle's file it is (GetSourceStartFrame
returns multicam time and sometimes rounds a frame early). The FCPXML export has both: each <mc-angle> is a list of
clips placed in multicam time, and each <mc-clip> on the spine says which angle's video it shows. All times are
rational seconds; frames = seconds * fps.
"""
from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET
from fractions import Fraction


def t(s: str | None) -> Fraction:
    if not s:
        return Fraction(0)
    return Fraction(s[:-1]) if s.endswith("s") else Fraction(s)


def _transform(el) -> dict:
    tr = el.find("adjust-transform")
    if tr is None:
        return {}
    out = {}
    if tr.get("scale"):
        sx, sy = (float(v) for v in tr.get("scale").split())
        out["zoom"] = (sx, sy)
    if tr.get("rotation"):
        out["rotation"] = float(tr.get("rotation"))
    if tr.get("position"):
        px, py = (float(v) for v in tr.get("position").split())
        out["position"] = (px, py)
    return {k: v for k, v in out.items() if v not in ((1.0, 1.0), 0.0, (0.0, 0.0))}


class Project:
    def __init__(self, path: str):
        root = ET.parse(path).getroot()
        res = root.find("resources")
        fmt = res.find("format")
        self.fps = 1 / t(fmt.get("frameDuration"))
        self.assets = {}
        for a in res.iter("asset"):
            rep = a.find("media-rep")
            src = urllib.parse.unquote(urllib.parse.urlparse(rep.get("src")).path) if rep is not None else None
            self.assets[a.get("id")] = {"path": src, "start": t(a.get("start")), "name": a.get("name")}
        self.multicams = {}
        for m in res.iter("media"):
            mc = m.find("multicam")
            if mc is None:
                continue
            angles = {}
            for ang in mc.iter("mc-angle"):
                segs = []
                for c in ang:
                    if c.tag not in ("clip", "asset-clip"):
                        continue
                    ref = c.get("ref") or c.find("video").get("ref")
                    segs.append({"offset": t(c.get("offset")), "duration": t(c.get("duration")),
                                 "start": t(c.get("start")), "asset": self.assets[ref],
                                 "enabled": c.get("enabled", "1") == "1", "transform": _transform(c)})
                angles[ang.get("angleID")] = {"name": ang.get("name"), "segments": segs}
            self.multicams[m.get("id")] = {"name": m.get("name"), "tc_start": t(mc.get("tcStart")), "angles": angles}
        seq = root.find(".//sequence")
        self.tc_start = t(seq.get("tcStart"))
        self.items = []
        spine = seq.find("spine")
        for el in spine.iter("mc-clip"):
            src = el.find("mc-source")
            if src is None or "video" not in src.get("srcEnable", "all") and src.get("srcEnable") != "all":
                continue
            if el.get("lane") not in (None, "0") and int(el.get("lane")) < 0:
                continue
            self.items.append({"ref": el.get("ref"), "offset": t(el.get("offset")), "start": t(el.get("start")),
                               "duration": t(el.get("duration")), "angle": src.get("angleID"),
                               "lane": int(el.get("lane") or 0), "enabled": el.get("enabled", "1") == "1",
                               "transform": _transform(el)})

    def frames(self, s: Fraction) -> int:
        v = s * self.fps
        if v.denominator != 1:
            raise ValueError(f"{s} s is not a whole frame at {self.fps} fps")
        return int(v)

    def resolve(self, record: int) -> dict | None:
        """The video item on the main lane at record frame `record` -> angle, file and source frame there."""
        for it in self.items:
            if it["lane"] != 0:
                continue
            a = self.frames(it["offset"])
            if not a <= record < a + self.frames(it["duration"]):
                continue
            mc = self.multicams[it["ref"]]
            ang = mc["angles"][it["angle"]]
            mt = self.frames(it["start"] - mc["tc_start"]) + record - a  # multicam time, frames from its start
            for s in ang["segments"]:
                o = self.frames(s["offset"])
                if o <= mt < o + self.frames(s["duration"]):
                    src = self.frames(s["start"] - s["asset"]["start"]) + mt - o
                    return {"angle": ang["name"], "path": s["asset"]["path"], "src_frame": src,
                            "record_start": a, "record_end": a + self.frames(it["duration"]),
                            "angle_transform": s["transform"], "item_transform": it["transform"]}
            return {"angle": ang["name"], "path": None, "src_frame": None, "record_start": a,
                    "record_end": a + self.frames(it["duration"]), "reason": "gap in the angle"}
        return None
