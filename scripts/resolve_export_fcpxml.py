"""Read-only on the project: export a timeline as FCPXML 1.10 (default: the current one) to a file.
Usage: /usr/bin/python3 scripts/resolve_export_fcpxml.py <out.fcpxml> [timeline name]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from gigsteady.resolve.bridge import connect, guard  # noqa: E402

guard()
r = connect()
p = r.GetProjectManager().GetCurrentProject()
if p.IsRenderingInProgress():
    sys.exit("render in progress")
tl = p.GetCurrentTimeline()
if len(sys.argv) > 2:
    tl = next(t for t in (p.GetTimelineByIndex(i) for i in range(1, p.GetTimelineCount() + 1))
              if t.GetName() == sys.argv[2])
out = Path(sys.argv[1]).resolve()
ok = tl.Export(str(out), r.EXPORT_FCPXML_1_10, r.EXPORT_NONE)
print("export", tl.GetName(), "->", out, "returned", ok, "exists", out.exists(), out.stat().st_size if out.exists() else 0)
