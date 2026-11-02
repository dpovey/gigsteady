"""Connect to DaVinci Resolve: the injected `resolve` global inside Resolve, else DaVinciResolveScript.

External scripts must call guard() first: Resolve's scripting library can hang the interpreter at exit.
"""
from __future__ import annotations

import atexit
import os
import sys

MODULES = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules/"
LIB = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"


def guard() -> None:
    atexit.register(lambda: (sys.stdout.flush(), sys.stderr.flush(), os._exit(0)))


def connect(injected=None):
    if injected is not None:
        return injected
    os.environ.setdefault("RESOLVE_SCRIPT_LIB", LIB)
    if MODULES not in sys.path:
        sys.path.append(MODULES)
    import DaVinciResolveScript as dvr
    r = dvr.scriptapp("Resolve")
    if r is None:
        raise RuntimeError("Resolve is not reachable (is it running, with external scripting set to Local?)")
    return r


def state(resolve) -> dict:
    """Read-only snapshot: project, timeline, render status. Never reads clip properties while rendering."""
    pm = resolve.GetProjectManager()
    p = pm.GetCurrentProject()
    tl = p.GetCurrentTimeline() if p else None
    out = {"page": resolve.GetCurrentPage(), "project": p.GetName() if p else None,
           "rendering": bool(p and p.IsRenderingInProgress()),
           "database": pm.GetCurrentDatabase(), "timeline": None}
    if tl:
        out["timeline"] = {"name": tl.GetName(), "start": tl.GetStartFrame(), "end": tl.GetEndFrame(),
                           "fps": tl.GetSetting("timelineFrameRate"), "video_tracks": tl.GetTrackCount("video"),
                           "playhead": tl.GetCurrentTimecode(), "mark_in_out": tl.GetMarkInOut()}
    return out
