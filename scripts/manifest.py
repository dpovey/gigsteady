"""Per-output placement manifest for a finished job: handles actually used, crop, mode, frame counts read back.

Usage: .venv/bin/python scripts/manifest.py <job_dir> [--items <items.json>]   (job_dir has shots.json + outputs/)
Writes <job_dir>/manifest.json and manifest.csv. For placement: the shot is output frames
[place_start, place_end) (end exclusive, as AppendToTimeline's endFrame on 21.1), at record_start.
A shot merged across a through-edit (it has "parts") gives one row per part, each placed on its own timeline item;
a part marked "place": "editor" (Dynamic Zoom) gets status "editor" and is left for the editor.
With --items (the selection's every-item list, from shots_from_fcpxml.py), also writes crop_matches.json: unstabilised
items that show the same picture as an overlay across a through-edit, with the Zoom factor that matches its crop
(gigsteady.edits.crop_matches; resolve_place.py applies it).
"""
import csv
import json
import subprocess
import sys
from pathlib import Path

job = Path(sys.argv[1])
items_p = Path(sys.argv[sys.argv.index("--items") + 1]) if "--items" in sys.argv else None
shots = {s["name"]: s for s in json.loads((job / "shots.json").read_text())}
rows = []
for name, s in shots.items():
    rep_p = job / "outputs" / f"{name}.json"
    mov = job / "outputs" / f"{name}.mov"
    if not (rep_p.exists() and mov.exists()):
        rows.append({"name": name, "status": "missing"})
        continue
    r = json.loads(rep_p.read_text())
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-count_packets", "-select_streams", "v:0", "-show_entries",
         "stream=nb_read_packets,width,height,codec_name,profile,pix_fmt:stream_tags=timecode", "-of", "json", str(mov)],
        capture_output=True, text=True, check=True).stdout)["streams"][0]
    n = s["out"] - s["in"]
    frames = int(probe["nb_read_packets"])
    fz = r.get("fusion") or {}
    row = {"name": name, "status": "ok", "item_id": s.get("item_id"), "angle": s.get("angle"),
           "record_start": s.get("record_start"), "record_end": s.get("record_end"),
           "src": s["src"], "src_in": s["in"], "src_out": s["out"],
           "output": str(mov), "output_frames": frames, "output_tc": probe.get("tags", {}).get("timecode"),
           "handle_in": r["handle_in"], "handle_out": r["handle_out"],
           "place_start": r["handle_in"], "place_end": r["handle_in"] + n,
           "mode": r["mode"], "stab_zoom": r["zoom"], "lock_rejected": r.get("lock_rejected"),
           "fused_frames": fz.get("fused"), "gyro": bool(r.get("gyro")),
           "codec": f"{probe['codec_name']} {probe.get('profile')} {probe['pix_fmt']} {probe['width']}x{probe['height']}",
           "options_key": r["options_key"], "version": r["version"]}
    if frames != r["handle_in"] + n + r["handle_out"]:
        row["status"] = f"frame count {frames} != handles + shot {r['handle_in'] + n + r['handle_out']}"
    if not s.get("parts"):
        rows.append(row)
        continue
    for pt in s["parts"]:
        k = pt["record_end"] - pt["record_start"]
        rows.append(dict(row, item_id=pt.get("item_id"), record_start=pt["record_start"], record_end=pt["record_end"],
                         place_start=r["handle_in"] + pt["offset"], place_end=r["handle_in"] + pt["offset"] + k,
                         part_of=name, editor_why=pt.get("why"),
                         status=row["status"] if row["status"] != "ok" or pt.get("place") != "editor" else "editor"))
if items_p:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
    from gigsteady.edits import crop_matches, item_from_dict  # noqa: E402
    items = [item_from_dict(i) for i in json.loads(items_p.read_text())]
    cm = crop_matches(list(shots.values()), items)
    (job / "crop_matches.json").write_text(json.dumps(cm, indent=1))
    print(f"CROP MATCHES {len(cm)} -> {job / 'crop_matches.json'}")
elif (job / "crop_matches.json").exists():   # a stale list from an earlier run must not be applied to this one
    (job / "crop_matches.json").unlink()
    print("removed stale crop_matches.json (no --items)")
(job / "manifest.json").write_text(json.dumps(rows, indent=1))
keys = list(dict.fromkeys(k for r in rows for k in r))
with (job / "manifest.csv").open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
bad = [r for r in rows if r["status"] not in ("ok", "editor")]
print(f"MANIFEST {len(rows)} shots, {len(rows) - len(bad)} ok, {len(bad)} not ok -> {job / 'manifest.json'}")
for r in bad:
    print(" ", r["name"], r["status"])
