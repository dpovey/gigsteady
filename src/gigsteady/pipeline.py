"""One shot end to end: decode, choose the mode, analyse, render, write the sidecar report.

Auto mode: lock when the shot registers to a fixed reference (>= 80% of frames, median correlation >= 0.5) and the
lock correction stays within 40 px at 4K (scaled with width); a bigger correction means a deliberate pan or tilt
that locking would fight (SD_rec297353: 124 px tilt; SD_rec298031: 160 px pan). Otherwise track mode.

Output is written to `<out>.partial.mov` and renamed when complete, with `<out>.json` beside it (report and a hash
of the parameters); a batch resume trusts an output only when both are there and the hash matches.
"""
import hashlib
import json
import os
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from . import __version__, io
from .lock import analyse_lock
from .probe import probe
from .track import analyse_track
from .warp import needed_zoom, output_matrices, warp_cpu

MOVED_PX_4K = 40.0
BOOT_DISK_MIN_GB = 25.0
PRORES_HQ_GB_PER_MIN_4K = 5.5   # 4K25 422 HQ is ~730 Mb/s


@dataclass
class Options:
    mode: str = "auto"            # auto | lock | track
    handles: int = 25
    crop_budget: float = 1.10     # track mode (0 = no limit)
    smoother: str = "l1"          # l1 | gauss
    zoom: float | None = None     # force the output zoom
    fuse: int | None = None       # None = auto (4 in lock mode, off in track), 0 = off
    fuse_sharp: float = 8.0
    fuse_denoise: float = 0.0
    fuse_tau: float = 0.06
    fuse_min_gain: float = 0.05
    gyro: str = "auto"            # auto | off
    gpu: bool = True
    encoder: str = "prores_videotoolbox"

    def key(self) -> str:
        return hashlib.sha1(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()[:12]


def check_disk(out: Path, n_frames: int, clip) -> None:
    """Refuse a write that would leave the boot disk under 25 GB free."""
    est = n_frames / float(clip.fps) / 60 * PRORES_HQ_GB_PER_MIN_4K * (clip.width * clip.height) / (3840 * 2160)
    out_dev = os.stat(out.parent).st_dev
    if out_dev == os.stat("/System/Volumes/Data").st_dev or out_dev == os.stat("/").st_dev:
        free = shutil.disk_usage("/System/Volumes/Data").free / 1e9
        if free - est < BOOT_DISK_MIN_GB:
            raise RuntimeError(f"writing ~{est:.1f} GB to the boot disk would leave {free - est:.0f} GB (< 25 GB)")


def analyse(src: str, start: int, n: int, opt: Options) -> dict:
    """Mode choice and correction path. Returns a dict with p (n,4), mode, and report fields."""
    clip = probe(src)
    g = io.decode_gray(src, start, n)
    k = clip.width / g.shape[2]
    rep: dict = {}
    mode = opt.mode
    if mode != "track":
        r = analyse_lock(g)
        moved = max(np.ptp(r.p[:, 0]), np.ptp(r.p[:, 1])) * k > MOVED_PX_4K * clip.scale
        rep.update(lock_matched=round(float(r.good.mean()), 3), lock_median_corr=round(float(np.median(r.corr)), 3),
                   lock_range_px=round(float(max(np.ptp(r.p[:, 0]), np.ptp(r.p[:, 1])) * k), 1),
                   lighting_states=np.bincount(r.states).tolist())
        if mode == "auto" and (r.good.mean() < 0.8 or np.median(r.corr) < 0.5 or moved):
            rep["lock_rejected"] = "moved" if moved else "registration"
            mode = "track"
        else:
            mode = "lock"
            p, good, corr = r.p, r.good, r.corr
    if mode == "track":
        fuse_steps = None
        if opt.gyro != "off" and clip.has_rtmd:
            from .gyro.fuse import gyro_fuser  # step 6
            fuse_steps = gyro_fuser(src, start, n, clip.fps)
        t = analyse_track(g, k, clip.width, clip.height, budget=opt.crop_budget, smoother=opt.smoother,
                          fuse_steps=fuse_steps)
        p, good, corr = t.p, t.good, t.corr
        rep.update(t.report)
    rep.update(mode=mode, matched=round(float(good.mean()), 3), median_corr=round(float(np.median(corr)), 3))
    return {"p": p, "good": good, "corr": corr, "k": k, "mode": mode, "report": rep}


def make_fuser(mode: str, opt: Options):
    radius = opt.fuse if opt.fuse is not None else (4 if mode == "lock" else 0)
    if not radius:
        return None
    args = (radius, opt.fuse_sharp, opt.fuse_denoise, opt.fuse_tau, opt.fuse_min_gain)
    if opt.gpu:
        from .gpu import FuserGPU
        return FuserGPU(*args)
    from .fuse_cpu import Fuser
    return Fuser(*args)


def render(src: str, start: int, n: int, A: np.ndarray, out: Path, fuser=None, gpu: bool = False,
           encoder: str = "prores_videotoolbox") -> None:
    clip = probe(src)
    w = io.ProResWriter(str(out), clip.width, clip.height, clip.fps, io.output_timecode(clip, start), encoder=encoder)
    radius = fuser.radius if fuser else 0
    if gpu:
        from . import gpu as G
    frames = io.read_rgb48(src, start, n)
    try:
        for j in range(n + radius):
            if j < n:
                f = next(frames)
                wf = G.warp_full(G.upload(f), A[j]) if gpu else warp_cpu(f, A[j])
                if fuser:
                    fuser.add(j, wf)
                else:
                    w.write(G.download(wf) if gpu else wf)
            t = j - radius
            if fuser and t >= 0:
                o = fuser.output(t)
                w.write(G.download(o) if gpu else o)
                fuser.drop_before(t - radius + 1)
    finally:
        frames.close()
        w.close()
    if w.count != n:
        raise RuntimeError(f"wrote {w.count} frames, expected {n}")


def stabilise(src: str, in_frame: int, out_frame: int, out: Path, opt: Options = Options(),
              log=print, render_video: bool = True) -> dict:
    """Stabilise source frames [in_frame, out_frame) plus handles (clamped at the file's ends) into `out`."""
    t0 = time.time()
    clip = probe(src)
    total = clip.frames or out_frame + opt.handles
    start = max(0, in_frame - opt.handles)
    end = min(total, out_frame + opt.handles)
    n = end - start
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    check_disk(out, n, clip)
    a = analyse(src, start, n, opt)
    zoom = opt.zoom or needed_zoom(a["p"], a["k"], clip.width, clip.height)
    A = output_matrices(a["p"], a["k"], zoom, clip.width, clip.height)
    t1 = time.time()
    use_gpu = opt.gpu and _gpu_ok()
    fuser = None
    if render_video:
        fuser = make_fuser(a["mode"], Options(**{**asdict(opt), "gpu": use_gpu}))
        partial = out.with_name(out.stem + ".partial" + out.suffix)
        render(src, start, n, A, partial, fuser, use_gpu, opt.encoder)
        os.replace(partial, out)
    rep = {"src": src, "in": in_frame, "out": out_frame, "start": start, "end": end,
           "handle_in": in_frame - start, "handle_out": end - out_frame, "frames": n,
           "fps": f"{clip.fps.numerator}/{clip.fps.denominator}", "zoom": zoom, **a["report"],
           "fusion": getattr(fuser, "stats", None), "gpu": use_gpu,
           "analysis_s": round(t1 - t0, 1), "render_s": round(time.time() - t1, 1),
           "options": asdict(opt), "options_key": opt.key(), "version": __version__,
           "output": str(out) if render_video else None}
    out.with_suffix(".json").write_text(json.dumps(rep, indent=1))
    path_csv(out.with_suffix(".csv"), a, start)
    log(f"{out.name}: {a['mode']} zoom {zoom} matched {rep['matched']:.0%} "
        f"fusion {rep['fusion']} in {rep['analysis_s'] + rep['render_s']:.0f}s")
    return rep


def path_csv(path: Path, a: dict, start: int) -> None:
    k = a["k"]
    with path.open("w") as fh:
        fh.write("frame,dx_4k,dy_4k,rot_deg,scale,ecc_corr,matched\n")
        for i, (q, gd, c) in enumerate(zip(a["p"], a["good"], a["corr"])):
            fh.write(f"{start + i},{q[0] * k:.2f},{q[1] * k:.2f},{np.degrees(q[2]):.4f},{q[3]:.5f},{c:.4f},{int(gd)}\n")


def _gpu_ok() -> bool:
    try:
        import torch
        return torch.backends.mps.is_available()
    except ImportError:
        return False
