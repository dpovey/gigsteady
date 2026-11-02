"""gigsteady: concert-footage stabiliser (lock-off for fixed cameras, planned paths for handheld, gyro fusion,
vibration de-blur). Frame numbers are source frames counted from 0; --out is exclusive."""
import argparse
import sys
from pathlib import Path


def add_shot_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--handles", type=int, default=25, help="extra frames each side (clamped at the file ends)")
    p.add_argument("--mode", choices=["auto", "lock", "track"], default="auto")
    p.add_argument("--crop-budget", type=float, default=1.10, help="track mode: max crop (0 = no limit; uses the gauss smoother)")
    p.add_argument("--smoother", choices=["l1", "gauss"], default="l1")
    p.add_argument("--zoom", type=float, help="force the output zoom")
    p.add_argument("--fuse", type=int, help="vibration de-blur radius (default: 4 in lock mode, off in track; 0 = off)")
    p.add_argument("--fuse-denoise", type=float, default=0.0, help="0 = deblur only; up to 1 = temporal denoise")
    p.add_argument("--fuse-contrast", type=float, default=0.4, help="de-blur moving gate vs local contrast (0 = off)")
    p.add_argument("--no-gyro", action="store_true", help="ignore recorded gyro")
    p.add_argument("--cpu", action="store_true", help="don't use the GPU")


def option_args(o) -> list[str]:
    """Options -> command-line flags (everything but handles), for batch sub-processes."""
    out = ["--mode", o.mode, "--crop-budget", str(o.crop_budget), "--smoother", o.smoother,
           "--fuse-denoise", str(o.fuse_denoise), "--fuse-contrast", str(o.fuse_contrast)]
    out += ["--zoom", str(o.zoom)] if o.zoom else []
    out += ["--fuse", str(o.fuse)] if o.fuse is not None else []
    out += ["--no-gyro"] if o.gyro == "off" else []
    out += ["--cpu"] if not o.gpu else []
    return out


def options(a):
    from .pipeline import Options
    return Options(mode=a.mode, handles=a.handles, crop_budget=a.crop_budget, smoother=a.smoother, zoom=a.zoom,
                   fuse=a.fuse, fuse_denoise=a.fuse_denoise, fuse_contrast=a.fuse_contrast, gyro="off" if a.no_gyro else "auto", gpu=not a.cpu)


def review_cmd(a) -> int:
    import json
    import random
    from . import review
    if a.kind == "unblind":
        print(review.unblind(a.source, a.marks))
        return 0
    seed = a.seed if a.seed is not None else random.randrange(1 << 30)
    crop = tuple(int(v) for v in a.crop.split(",")) if a.crop else None
    if a.kind == "single":
        items = review.single_items(a.source, a.only)
    else:
        if len(a.side) != 2:
            raise SystemExit("ab needs two --side version=directory")
        sides = {v: Path(d) for v, d in (s.split("=", 1) for s in a.side)}
        shots = [s for s in json.loads(a.source.read_text()) if not a.only or s["name"] in a.only]
        items = review.ab_items(shots, sides)
    print(review.build(a.kind, items, a.out, a.title, random.Random(seed), seed, a.repeats, crop, a.jobs,
                       a.controls, a.note))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gigsteady", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("probe", help="show what gigsteady sees in a clip")
    p.add_argument("clip")
    p = sub.add_parser("stabilise", help="stabilise one shot to ProRes 422 HQ")
    p.add_argument("clip")
    p.add_argument("--in", dest="in_frame", type=int, required=True)
    p.add_argument("--out", dest="out_frame", type=int, required=True, help="exclusive")
    p.add_argument("-o", "--output", type=Path, required=True)
    p.add_argument("--no-render", action="store_true", help="analyse only: write the report and path CSV")
    add_shot_options(p)
    p = sub.add_parser("batch", help="many shots, N at a time; resumes")
    p.add_argument("shots", type=Path, help="shots.json")
    p.add_argument("job", type=Path, help="job directory")
    p.add_argument("--jobs", type=int, default=3)
    p.add_argument("--no-render", action="store_true")
    add_shot_options(p)
    p = sub.add_parser("review", help="blinded review page: single <job> | ab <shots.json> --side v=dir x2 | unblind")
    p.add_argument("kind", choices=["single", "ab", "unblind"])
    p.add_argument("source", type=Path, help="job dir (single), shots.json (ab) or review dir (unblind)")
    p.add_argument("marks", type=Path, nargs="?", help="unblind: the exported marks CSV")
    p.add_argument("--out", type=Path, help="review directory")
    p.add_argument("--side", action="append", default=[], help="ab: version=directory (give two)")
    p.add_argument("--only", nargs="+", help="shot names to include")
    p.add_argument("--title", default="gigsteady review")
    p.add_argument("--repeats", type=int, default=3, help="clips shown twice, unannounced")
    p.add_argument("--seed", type=int)
    p.add_argument("--controls", type=int, default=0, help="single: clips whose right side is the original, unannounced")
    p.add_argument("--note", default="", help="extra HTML shown under the instructions")
    p.add_argument("--crop", help="x,y of the full-resolution crop (default: frame centre)")
    p.add_argument("--jobs", type=int, default=3)
    p = sub.add_parser("eval", help="independent checks: gyro-truth <clip> <start:n>...")
    p.add_argument("what", choices=["gyro-truth"])
    p.add_argument("rest", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    if a.cmd == "probe":
        from .probe import probe
        print(probe(a.clip))
    elif a.cmd == "stabilise":
        from .pipeline import stabilise
        stabilise(a.clip, a.in_frame, a.out_frame, a.output, options(a), render_video=not a.no_render)
    elif a.cmd == "eval":
        from .eval import gyro_truth
        gyro_truth.main(a.rest)
    elif a.cmd == "review":
        return review_cmd(a)
    elif a.cmd == "batch":
        from .batch import run_batch
        rows = run_batch(a.shots, a.job, options(a), a.jobs, not a.no_render)
        return 0 if all(not r["status"].startswith("failed") for r in rows) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
