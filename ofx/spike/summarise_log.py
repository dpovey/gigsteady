"""Summarise ~/Library/Logs/gigsteady-ofx-spike.log: per source path, how render times map to source frames, whether
the source frame is set, and neighbour-fetch success and cost. Usage: python3 summarise_log.py [log] [--since ISO]"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

log = Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else \
    Path.home() / "Library/Logs/gigsteady-ofx-spike.log"
rows = [json.loads(line) for line in log.read_text().splitlines() if line.startswith("{")]
by = defaultdict(list)
for r in rows:
    if r["event"] == "render":
        by[r["src_path"] or "(no src_path)"].append(r)
print(f"{len(rows)} lines, {sum(len(v) for v in by.values())} render calls, instances: "
      f"{sum(r['event'] == 'instance' for r in rows)}")
for path, rs in by.items():
    rs.sort(key=lambda r: r["time"])
    off = {round(r["src_frame"] - r["time"], 3) for r in rs if r["src_frame"] >= 0}
    unset = sum(r["src_frame"] < 0 for r in rs)
    fetch = [f for r in rs for f in r["fetch"]]
    fails = [(r["time"], f["d"]) for r in rs for f in r["fetch"] if not f["ok"]]
    ms = [f["ms"] for f in fetch if f["ok"]]
    print(f"\n{path}\n  calls {len(rs)}  time {rs[0]['time']}..{rs[-1]['time']}  range {rs[0]['range']}  "
          f"unmapped {rs[0]['unmapped']}  fps {rs[0]['fps']}  scale {rs[0]['scale']}")
    print(f"  src_frame unset on {unset}; src_frame - time offsets: {sorted(off)[:6]}{' ...' if len(off) > 6 else ''}")
    print(f"  fetches {len(fetch)}, failed {len(fails)} {fails[:6]}; ms median {statistics.median(ms) if ms else 0:.1f} "
          f"max {max(ms) if ms else 0:.1f}; draft {sum(r['draft'] for r in rs)} interactive {sum(r['interactive'] for r in rs)}")
