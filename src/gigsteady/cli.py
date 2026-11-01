"""gigsteady: concert-footage stabiliser (lock-off for fixed cameras, planned paths for handheld, gyro fusion,
vibration de-blur). Frame numbers are source frames counted from 0; --out is exclusive."""
import argparse
import sys
from pathlib import Path


def add_shot_options(p: argparse.ArgumentParser) -> None:
    p.add_argument("--handles", type=int, default=25, help="extra frames each side (clamped at the file ends)")
    p.add_argument("--mode", choices=["auto", "lock", "track"], default="auto")
    p.add_argument("--crop-budget", type=float, default=1.10, help="track mode: max crop (0 = no limit)")
    p.add_argument("--smoother", choices=["l1", "gauss"], default="l1")
    p.add_argument("--zoom", type=float, help="force the output zoom")
    p.add_argument("--fuse", type=int, help="vibration de-blur radius (default: 4 in lock mode, off in track; 0 = off)")
    p.add_argument("--fuse-denoise", type=float, default=0.0, help="0 = deblur only; up to 1 = temporal denoise")
    p.add_argument("--no-gyro", action="store_true", help="ignore recorded gyro")
    p.add_argument("--cpu", action="store_true", help="don't use the GPU")


def options(a):
    from .pipeline import Options
    return Options(mode=a.mode, handles=a.handles, crop_budget=a.crop_budget, smoother=a.smoother, zoom=a.zoom,
                   fuse=a.fuse, fuse_denoise=a.fuse_denoise, gyro="off" if a.no_gyro else "auto", gpu=not a.cpu)


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
    add_shot_options(p)
    a = ap.parse_args(argv)
    if a.cmd == "probe":
        from .probe import probe
        print(probe(a.clip))
    elif a.cmd == "stabilise":
        from .pipeline import stabilise
        stabilise(a.clip, a.in_frame, a.out_frame, a.output, options(a))
    return 0


if __name__ == "__main__":
    sys.exit(main())
