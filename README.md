# gigsteady

Stabiliser for live-concert footage, built for the cameras at a gig: a locked-off camera on a shaky desk or riser,
and a handheld camera in the photo pit. It works from the picture (and, on Sony bodies, the recorded gyro), copes
with stage lighting that flips between states, and repairs the motion blur that vibration leaves on fixed cameras.

Output is ProRes 422 HQ at source size, frame rate and timecode, so a stabilised shot drops back into an edit.
Developed against DaVinci Resolve 21.1 on Apple Silicon; there are scripts to find shots on a Resolve timeline and
place the results back on it.

Status: working and used on a real edit, but young. See [docs/KNOWN-ISSUES.md](docs/KNOWN-ISSUES.md).

## What it does

- **Lock mode** (fixed cameras): registers every frame to the shot's median picture on lighting-normalised gradients,
  inside a mask of static, textured areas, with lighting states tied together. The camera ends up locked off.
- **Track mode** (moving cameras): measures frame-to-frame motion and plans a smooth camera path with an L1 planner
  (Grundmann et al. 2011) inside a crop budget (default 1.10). Auto mode switches to track when the camera clearly
  moved or the shot won't register.
- **Gyro + IBIS fusion** for Sony files with `rtmd` metadata (FX3/FX30/A7 bodies), via a small Rust tool built on
  [telemetry-parser](https://github.com/AdrianEddy/telemetry-parser) that reads a time range without loading the
  whole file.
- **Vibration de-blur** (lock mode): each frame borrows detail from crisper neighbours, aligned, in static areas only.
- **Exact colour**: BT.709 conversion to and from 10-bit 4:2:2 is done in numpy, not ffmpeg (whose range arithmetic
  is about one code off).

## Install

macOS on Apple Silicon, Python 3.11 or 3.12, [uv](https://docs.astral.sh/uv/), ffmpeg 7+ on the PATH.

```
uv sync --extra gpu          # PyTorch for the Metal (MPS) render path; omit for CPU only
cd rust/gs-gyro && cargo build --release   # only needed for Sony gyro data
```

## Use

```
gigsteady probe clip.mp4
gigsteady stabilise clip.mp4 --in 1200 --out 1350 -o shot.mov      # source frames, --out exclusive
gigsteady batch shots.json job_dir --jobs 2                          # resumes; job_dir/{outputs,logs,results.csv}
gigsteady review single job_dir --out review/                         # blinded review page
```

`shots.json` is a list of `{"name", "src", "in", "out"}`. Useful options: `--mode auto|lock|track`,
`--handles 25`, `--crop-budget 1.10`, `--smoother l1|gauss`, `--fuse N` / `--fuse 0`, `--no-gyro`, `--cpu`.
Every output gets a `.json` report (mode, crop, de-blur stats, handles used) and a `.csv` of the per-frame correction.

## DaVinci Resolve

The `scripts/resolve_*.py` tools run with the system Python against a running Resolve (external scripting set to
Local). They read the timeline (`resolve_items.py`, `resolve_export_fcpxml.py`), turn multicam cuts into source
shots through an FCPXML export (`shots_from_fcpxml.py`), and place finished results on a *duplicate* of the
timeline (`resolve_place.py`): one change at a time, each read back, grades copied with `CopyGrades`, transforms
composed, and an undo that removes only what it created. `eval/timeline_diff.py` then compares renders of the two
timelines cut by cut. Resolve API behaviours found along the way are noted in the code (for example: track enable
reads `False` on a timeline that isn't current, and a flag on a multicam cut lands on the shared multicam clip).

An OpenFX plugin front end is being explored: [docs/OFX-EXPLORATION.md](docs/OFX-EXPLORATION.md).

## Tests

```
uv run pytest                      # unit tests, synthetic clips, ~20 s
uv run pytest -m footage           # regression tests against the author's footage (paths in tests/fixtures)
```

## Layout

`src/gigsteady/` the package (`pipeline.py` is the entry point; `lock.py`, `track.py`, `paths.py`, `warp.py`,
`gpu.py`/`fuse_cpu.py`, `colour.py`, `io.py`, `gyro/`, `resolve/`). `eval/` independent checks. `scripts/` Resolve
and job tools. `rust/` the gyro reader and the vendored, patched telemetry-parser. `docs/PLAN.md` the design.

## Licence

MIT. `rust/vendor/telemetry-parser` is © AdrianEddy, MIT or Apache-2.0 (its own licence files).
