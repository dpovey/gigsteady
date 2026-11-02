"""Blinded review pages (see the labelling rules in ~/src/personal/CLAUDE.md).

Two kinds:
- single: one job's results, original | stabilised. "Would you use this stabilised version in the cut?"
- ab: the same shots from two versions (say prototype vs gigsteady), sides randomised per clip.

Each clip is 1920x1080 H.264: top row the whole frame of both panels at 960x540, bottom row a 960x540 crop at
native pixels (the frame centre unless --crop). Clips get neutral ids (S01, S02 ...) in random order, a few are
repeated unannounced, and the page shows no mode, crop, score or file name. What is behind each id is written to
key.json, which the page never reads. Marks are kept in the browser and exported as a dated CSV; `unblind` joins
an export with the key into a new file and leaves the export as it is.
"""
import csv
import datetime as dt
import html
import json
import random
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .io import seek_args
from .probe import probe

PANEL = (960, 540)

Q_SINGLE = {
    "use": ("Would you use this stabilised version in the cut?",
            [("yes", "Yes"), ("trim", "Yes, with a trim"), ("no", "No"), ("unsure", "Can't tell")]),
}
Q_AB = {
    "pref": ("Which would you rather put in the cut?",
             [("A2", "A, clearly"), ("A1", "A, slightly"), ("same", "No real difference"), ("B1", "B, slightly"),
              ("B2", "B, clearly"), ("unsure", "Can't tell")]),
    "use": ("Would you use your pick (or either, if no difference) in the cut?",
            [("yes", "Yes"), ("trim", "Yes, with a trim"), ("no", "No"), ("unsure", "Can't tell")]),
}

INSTR_SINGLE = """<p><b>What this decides:</b> whether these stabilised shots go into the cut as they are, and where
the automatic settings need to change. Each clip shows the original on the left and the stabilised version on the
right; the top row is the whole frame, the bottom row a part of it at full resolution, so fine shake and blur show.
Both are ungraded Log: judge the motion and the crispness, not the colour.</p>
<p><b>Would you use this stabilised version in the cut?</b></p>
<ul>
<li><b>Yes</b>: you'd cut it in as it is, for the whole length shown.</li>
<li><b>Yes, with a trim</b>: usable, but only part of it (a jerk, wobble, smear or bad crop somewhere). Say where
in the note if you can ("last second", "after the light change").</li>
<li><b>No</b>: you wouldn't use this version, whether because the stabilising made it worse (wobble, warping,
ghosted people, too tight) or because it didn't fix the shake. Say which in the note if it's quick.</li>
<li><b>Can't tell</b>: the clip didn't play properly, or you can't judge it at this size. Don't guess.</li>
</ul>
<p>Judge the stabilised side only; the original is there for reference. If the original is unusable anyway (out
of focus, nothing happening), mark what you'd do with the stabilised side and add a note.</p>"""

INSTR_AB = """<p><b>What this decides:</b> which of two stabilising versions becomes the default for these shots.
Each clip shows the same shot twice: version <b>A</b> on the left, <b>B</b> on the right. The top row is the whole
frame, the bottom row a part of it at full resolution. Which version is A changes from clip to clip, and nothing
on the page says which is which. Both are ungraded Log: judge the motion, not the colour.</p>
<p><b>1. Which would you rather put in the cut?</b> Judge it as you would in the edit: steady without looking
locked or floaty, no wobble, jerks or warping, camera moves that still feel intended, and not cropped tighter
than it needs to be.</p>
<ul>
<li><b>Clearly</b>: you'd notice if the other one went in.</li>
<li><b>Slightly</b>: you'd pick it, but wouldn't mind the other.</li>
<li><b>No real difference</b>: you watched both and they'd do the same job.</li>
<li><b>Can't tell</b>: the clip didn't play properly, or you can't judge it at this size. Don't guess.</li>
</ul>
<p>If both are bad, still say which is less bad, then answer 2.</p>
<p><b>2. Would you use your pick in the cut?</b> Yes; yes with a trim (usable for only part of it: say where in
the note if you can); no; or can't tell. For "no real difference", answer for either.</p>"""


def find_render(d: Path, name: str) -> tuple[Path, int] | None:
    """A version's render of a shot and the frame in it where the shot starts (after the handles)."""
    for p in (d / "outputs" / f"{name}.mov", d / f"{name}.mov", d / f"{name}_fuse4.mov"):
        if p.exists():
            rep = p.with_name(name + ".json")
            off = json.loads(rep.read_text()).get("handle_in", 0) if rep.exists() else 0
            return p, off
    return None


def make_clip(panels: list[tuple[str, int]], n: int, out: Path, crop: tuple[int, int] | None) -> None:
    """panels: (file, first frame) each; writes the 2x2 review clip."""
    if out.exists():
        return
    cmd = ["nice", "-n", "10", "ffmpeg", "-nostdin", "-v", "error"]
    fc = []
    pw, ph = PANEL
    for i, (path, start) in enumerate(panels):
        c = probe(path)
        cmd += seek_args(c, start) + ["-i", path]
        cw, ch = min(pw, c.width), min(ph, c.height)
        x, y = crop if crop else ((c.width - cw) // 2, (c.height - ch) // 2)
        fc.append(f"[{i}:v]trim=end_frame={n},setpts=PTS-STARTPTS,split[f{i}][c{i}]")
        fc.append(f"[f{i}]scale={pw}:{ph}[s{i}]")
        fc.append(f"[c{i}]crop={cw}:{ch}:{x}:{y},pad={pw}:{ph}:(ow-iw)/2:(oh-ih)/2[x{i}]")
    k = len(panels)
    fc.append("".join(f"[s{i}]" for i in range(k)) + f"hstack=inputs={k}[top]")
    fc.append("".join(f"[x{i}]" for i in range(k)) + f"hstack=inputs={k}[bot]")
    fc.append("[top][bot]vstack=inputs=2,format=yuv420p[v]")
    tmp = out.with_suffix(".partial.mp4")
    cmd += ["-filter_complex", ";".join(fc), "-map", "[v]", "-frames:v", str(n), "-c:v", "libx264", "-crf", "18",
            "-preset", "fast", "-threads", "4", "-movflags", "+faststart", "-y", str(tmp)]
    subprocess.run(cmd, check=True)
    tmp.rename(out)


def single_items(job: Path, only: list[str] | None = None) -> list[dict]:
    items = []
    for rep in sorted((job / "outputs").glob("*.json")):
        if rep.name.startswith("._"):
            continue
        r = json.loads(rep.read_text())
        name = rep.stem
        if only and name not in only:
            continue
        out = rep.with_suffix(".mov")
        if not out.exists():
            continue
        n = r["out"] - r["in"]
        items.append({"name": name, "n": n, "panels": [
            {"version": "original", "path": r["src"], "start": r["in"]},
            {"version": "stabilised", "path": str(out), "start": r["handle_in"]}]})
    return items


def ab_items(shots: list[dict], sides: dict[str, Path]) -> list[dict]:
    (va, da), (vb, db) = sides.items()
    items = []
    for s in shots:
        ra, rb = find_render(da, s["name"]), find_render(db, s["name"])
        if not (ra and rb):
            print("skipped (missing render):", s["name"], flush=True)
            continue
        p = [{"version": va, "path": str(ra[0]), "start": ra[1]}, {"version": vb, "path": str(rb[0]), "start": rb[1]}]
        items.append({"name": s["name"], "n": s["n"], "panels": p})
    return items


def blind(items: list[dict], rng: random.Random, repeats: int, shuffle_sides: bool) -> list[dict]:
    """Random order, neutral ids, a few unannounced repeats (spread out, sides drawn again)."""
    shown = [dict(it) for it in items]
    for it in rng.sample(items, min(repeats, len(items))):
        shown.append(dict(it, repeat_of=it["name"]))
    rng.shuffle(shown)
    # keep a repeat away from its first showing where possible
    for _ in range(50):
        pos = {}
        bad = False
        for i, it in enumerate(shown):
            if it["name"] in pos and i - pos[it["name"]] < max(3, len(shown) // 4):
                bad = True
            pos[it["name"]] = i
        if not bad:
            break
        rng.shuffle(shown)
    for i, it in enumerate(shown):
        it["id"] = f"S{i + 1:02d}"
        if shuffle_sides:
            it["panels"] = rng.sample(it["panels"], 2)
    return shown


def page(title: str, kind: str, shown: list[dict], page_id: str) -> str:
    qs = Q_AB if kind == "ab" else Q_SINGLE
    instr = INSTR_AB if kind == "ab" else INSTR_SINGLE
    labels = ("A", "B") if kind == "ab" else ("original", "stabilised")
    cards = []
    for it in shown:
        qhtml = "".join(
            f'<div class="q" data-q="{q}"><span class="qt">{html.escape(text)}</span>'
            + "".join(f'<button data-v="{v}">{html.escape(lab)}</button>' for v, lab in opts) + "</div>"
            for q, (text, opts) in qs.items())
        cards.append(f"""
<section class="shot" data-id="{it['id']}">
  <header><b>{it['id']}</b></header>
  <video src="clips/{it['id']}.mp4" muted loop playsinline preload="none" controls></video>
  <div class="labels"><span>{labels[0]}</span><span>{labels[1]}</span></div>
  {qhtml}
  <input class="note" placeholder="note (optional)" />
</section>""")
    qkeys = json.dumps(list(qs))
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
body{{font:14px -apple-system,system-ui,sans-serif;background:#111;color:#ddd;margin:0;padding:16px 24px}}
h1{{font-size:18px;margin:0 0 4px}} .instr{{color:#bbb;max-width:1100px}} .instr li{{margin:2px 0}}
.top{{position:sticky;top:0;background:#111;padding:8px 0;z-index:2;border-bottom:1px solid #333;display:flex;gap:12px;align-items:center}}
.shot{{margin:18px 0;padding:12px;border:1px solid #2a2a2a;border-radius:8px}} .shot.done{{border-color:#555}}
video{{width:100%;max-width:1440px;display:block;margin-top:8px;background:#000}}
.labels{{display:flex;max-width:1440px}} .labels span{{flex:1;text-align:center;color:#888;font-size:13px}}
.q{{margin-top:8px}} .qt{{display:inline-block;min-width:380px;color:#aaa}}
button{{background:#222;color:#ddd;border:1px solid #444;border-radius:4px;padding:4px 10px;margin-right:6px;cursor:pointer}}
button.on{{background:#3d5afe;border-color:#3d5afe;color:#fff}}
.note{{margin-top:8px;background:#1b1b1b;color:#ddd;border:1px solid #444;border-radius:4px;padding:4px;width:50%}}
</style></head><body>
<h1>{html.escape(title)} ({len(shown)} clips)</h1>
<div class="instr">{instr}
<p>Keys: <code>space</code> play/pause the clip in view, <code>s</code> half speed. Your marks stay in this browser;
press Export when you're done (you can export more than once).</p></div>
<div class="top"><span id="count"></span><button id="export">Export marks (CSV)</button></div>
{''.join(cards)}
<script>
const KEY='gigsteady-review-{page_id}', QS={qkeys};
const st=JSON.parse(localStorage.getItem(KEY)||'{{}}');
const save=()=>{{localStorage.setItem(KEY,JSON.stringify(st));count();}};
const done=id=>QS.every(q=>(st[id]||{{}})[q]);
function count(){{const n=[...document.querySelectorAll('.shot')].filter(s=>done(s.dataset.id)).length;
 document.getElementById('count').textContent=`marked ${{n}}/{len(shown)}`;}}
document.querySelectorAll('.shot').forEach(sec=>{{
  const id=sec.dataset.id; st[id]=st[id]||{{}};
  sec.querySelectorAll('.q').forEach(qd=>{{const q=qd.dataset.q;
    qd.querySelectorAll('button').forEach(b=>{{if(st[id][q]==b.dataset.v)b.classList.add('on');
      b.addEventListener('click',()=>{{st[id][q]=b.dataset.v;st[id].at=new Date().toISOString();
        qd.querySelectorAll('button').forEach(x=>x.classList.toggle('on',x==b));sec.classList.toggle('done',done(id));save();}});}});}});
  const inp=sec.querySelector('.note'); inp.value=st[id].note||'';
  inp.addEventListener('input',()=>{{st[id].note=inp.value;save();}});
  sec.classList.toggle('done',done(id));
}});
const io=new IntersectionObserver(es=>es.forEach(e=>{{const v=e.target;if(e.isIntersecting){{v.preload='auto';v.play().catch(()=>{{}});}}else v.pause();}}),{{threshold:0.6}});
document.querySelectorAll('video').forEach(v=>io.observe(v));
function inView(){{return [...document.querySelectorAll('.shot')].find(s=>{{const r=s.getBoundingClientRect();return r.top<innerHeight*0.5&&r.bottom>innerHeight*0.3;}});}}
document.addEventListener('keydown',e=>{{if(e.target.tagName=='INPUT')return;const s=inView();if(!s)return;const v=s.querySelector('video');
 if(e.key==' '){{e.preventDefault();v.paused?v.play():v.pause();}}
 if(e.key=='s'){{v.playbackRate=v.playbackRate==1?0.5:1;}}}});
document.getElementById('export').addEventListener('click',()=>{{
  const esc=c=>`"${{String(c??'').replaceAll('"','""')}}"`;
  const rows=[['clip',...QS,'note','marked_at']].concat([...document.querySelectorAll('.shot')].map(s=>{{const x=st[s.dataset.id]||{{}};
    return [s.dataset.id,...QS.map(q=>x[q]),x.note,x.at];}}));
  const blob=new Blob([rows.map(r=>r.map(esc).join(',')).join('\\n')+'\\n'],{{type:'text/csv'}});
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download=`{page_id}_marks_${{new Date().toISOString().slice(0,10)}}.csv`;a.click();}});
count();
</script></body></html>"""


def build(kind: str, items: list[dict], out: Path, title: str, rng: random.Random, seed: int, repeats: int = 3,
          crop: tuple[int, int] | None = None, jobs: int = 3) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    (out / "clips").mkdir(exist_ok=True)
    shown = blind(items, rng, repeats, shuffle_sides=(kind == "ab"))
    with ThreadPoolExecutor(jobs) as pool:
        list(pool.map(lambda it: make_clip([(p["path"], p["start"]) for p in it["panels"]], it["n"],
                                           out / "clips" / f"{it['id']}.mp4", crop), shown))
    page_id = f"{out.name}-{seed}"
    instr = INSTR_AB if kind == "ab" else INSTR_SINGLE
    key = {"page_id": page_id, "kind": kind, "title": title, "seed": seed, "built": dt.datetime.now().isoformat(),
           "crop": crop, "questions": Q_AB if kind == "ab" else Q_SINGLE, "instructions": instr,
           "clips": [{"id": it["id"], "name": it["name"], "n": it["n"], "repeat_of": it.get("repeat_of"),
                      "left": it["panels"][0], "right": it["panels"][1]} for it in shown]}
    (out / "key.json").write_text(json.dumps(key, indent=1))
    (out / "index.html").write_text(page(title, kind, shown, page_id))
    return out / "index.html"


def unblind(review: Path, marks: Path) -> Path:
    """Join an exported marks CSV with the key; writes <marks>_unblinded.csv, leaves the export untouched."""
    key = json.loads((review / "key.json").read_text())
    clips = {c["id"]: c for c in key["clips"]}
    rows = list(csv.DictReader(marks.open()))
    out = marks.with_name(marks.stem + "_unblinded.csv")
    qs = list(key["questions"])
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["clip", "shot", "repeat_of", "left", "right", *qs, "picked", "note", "marked_at"])
        for r in rows:
            c = clips[r["clip"]]
            left, right = c["left"]["version"], c["right"]["version"]
            pref = r.get("pref", "")
            picked = {"A": left, "B": right}.get(pref[:1], pref) if key["kind"] == "ab" else ""
            w.writerow([r["clip"], c["name"], c["repeat_of"] or "", left, right, *(r.get(q, "") for q in qs),
                        picked, r.get("note", ""), r.get("marked_at", "")])
    return out
