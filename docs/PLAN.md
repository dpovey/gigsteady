# gigsteady: build plan (2026-11-01)

Status: approved with Dean's decisions (2026-11-01):
- Handheld default is the L1 planner; before it ships, a blind A/B page of ~16 Photo Pit shots (prototype vs gigsteady) goes to Dean.
- Place results makes all its changes in one run, reading each back before the next and stopping on the first mismatch; Undo place removes only what it created.
- Throwaway Resolve project "gigsteady test" may be created in the current database for development. Tell Dean before switching away from his project.
- Grade: the MVP places ungraded clips; grading stays with Dean.

Nothing has been built yet. This plan is based on reading the whole of the
`~/src/personal/pit-stabilise` README and scripts (`lockoff.py`, `paths.py`, `gyrofuse.py`, `fuse.py`, `gpu.py`,
`run_batch.py`, `make_review.py`, eval scripts, `vendor/gyrodump`, the vendored telemetry-parser at 19bc11f), the
Resolve 21.1 scripting stubs, and the Resolve sections of the bottb video-post runbook.

## What the prototype hard-codes (to generalise when porting)

- 25 fps and 3840x2160 everywhere (`-ss start/25`, `FW/FH`, `frame_ms=40`, the 40 px "moved" rule). gigsteady
  reads fps (as a rational), size and codec from ffprobe. Pixel thresholds scale with width (40 px at 3840).
- Seeking uses `-ss` with a float rounded to 3 decimals. That's exact at 25 fps but not at 29.97 or 23.976. Use exact
  rational seeks, and test that a ranged decode matches a sequential decode frame for frame (framemd5).
- Resume treats any existing output as done, but outputs are written under their final name. A killed job (not a
  failed one) leaves a partial file that a resume then skips. gigsteady writes to `*.partial.mov`, renames on
  success, and keeps a sidecar JSON with a hash of the parameters. Resume skips a shot only when the file and the
  hash both match.
- Paths are relative to the repo (`data/gyro`, `data/lockoff`). gigsteady uses a per-file cache directory and a job
  directory instead.

## Repo layout

```
gigsteady/
  pyproject.toml            uv project, Python 3.11; entry point `gigsteady`; extra [gpu] = torch
  README.md                 usage, install, results log pointer
  docs/PLAN.md              this file
  docs/notebook.md          results log (numbers, failures), like the prototype README
  src/gigsteady/
    cli.py                  argparse: stabilise, batch, gyro, review, eval, resolve-install
    probe.py                ffprobe: fps (rational), size, codec, timecode, rtmd present
    io.py                   ranged decode (VideoToolbox for HEVC only), 16-bit RGB reader, ProRes HQ writer
                            (prores_videotoolbox), timecode stamped on the output, threaded queues
    prep.py                 lighting-normalised gradients, static/texture mask, lighting states
    register.py             ECC wrappers, chunked parallel registration, fill_gaps
    lock.py                 lock mode (median ref, per-state refs tied by equal median position)
    track.py                track mode (frame-to-frame steps, low-confidence filled)
    paths.py                L1 crop-constrained planner (HiGHS), roll first then x/y; gauss kept for comparison
    gyro/  (python side)    cache reader, interval integration, IBIS steps, per-shot calibration, complementary filter
    fuse_cpu.py, fuse_gpu.py temporal fusion (selective, per-neighbour ECC, x16 cap, moving gate dilated)
    warp.py                 crop/zoom maths (needed_zoom vectorised), CPU Lanczos / GPU bicubic warp
    pipeline.py             auto mode (lock -> track fallback rules), one shot end to end, report dict
    batch.py                job dir, N parallel processes, resume, CSV, per-shot logs, final summary line
    review.py               review page generator (blinded, see below)
    resolve/
      bridge.py             connect (in-app globals or external DaVinciResolveScript), atexit/os._exit guard
      resolve_items.py      selected items / In-Out range -> source file + source frame range
      multicam.py           FCPXML parse + sync-timeline fallback + picture check
      place.py              import, new track, append, read back, restore In/Out and playhead
      menu/                 the thin stdlib-only scripts installed into Resolve's Scripts menu
  rust/gs-gyro/             Rust: ranged rtmd dump (telemetry-parser as a git dependency, pinned + small patch)
  eval/                     orb_jitter, measure_steady (ECC + phase-correlation wobble), fuse_check/burst_check,
                            gyro_truth (faster), all importing from gigsteady only
  tests/
    unit/                   synthetic: no footage, seconds
    regress/                reference shots with tolerances (needs the media drives; marked, opt-in)
    fixtures/               expected numbers + prototype paths copied in (small CSVs/JSON only)
```

Nothing imports from `pit-stabilise`, `gigstills`, `bottb` or `mix-assist`. The prototype's per-shot path CSVs
and the shot lists get copied into `tests/fixtures` as data.

## CLI

```
gigsteady stabilise <clip> --in F --out F [--handles 25] [--mode auto|lock|track] [--crop-budget 1.10]
    [--fuse 4 | --no-fuse] [--fuse-denoise 0] [--gyro auto|off] [--gpu/--cpu] [-o out.mov] [--report out.json]
gigsteady batch <shots.json|job_dir> --jobs N [same options]    resumes; job_dir/{results.csv,logs/,outputs/}
                                                             last line: "GIGSTEADY BATCH DONE ok=.. failed=.. skipped=.."
gigsteady gyro <clip> [--range a:b]                          builds/updates the gyro cache (bounded memory)
gigsteady review <job_dir> [--out dir]                       review page
gigsteady eval <...>                                         the independent checks
gigsteady resolve install | uninstall                        copies the menu scripts, writes their config
```

- `--in/--out` are source frame numbers (inclusive in, exclusive out, written down in the help text). Handles are
  clamped at the file's ends, and how much was actually used is written to the report so Resolve placement knows.
- Default fusion: on in lock mode, off in track mode (the prototype showed it does nothing on handheld shots and
  costs ~1 s/frame). `--fuse` forces it on.
- Output: ProRes 422 HQ via VideoToolbox, at source size, fps and timecode (start = source TC of the first frame
  written), plus a JSON sidecar (mode, crop, gyro report, fused counts, params hash, version).
- Default output root comes from `~/.config/gigsteady/config.toml`. gigsteady refuses to write anywhere that would
  leave the boot disk under 25 GB free (checked before each shot, from the estimated size of ~3 GB per 4K minute).
- Each batch job's memory: ~2.2 GB RAM plus 1-2 GB GPU (prototype). `--jobs` defaults to 3, and the batch checks
  headroom in `vm_stat` at start and warns.

## Resolve integration (Workspace > Scripts > gigsteady)

The menu scripts are thin and stdlib-only (Resolve runs them with its own Python). They talk to Resolve through the
injected `resolve` global and shell out to the gigsteady CLI in the repo's `.venv`. Three entries:

1. **Stabilise selection.** Read-only on the project. Takes `GetSelectedClips()`; if nothing is selected, a small
   UIManager dialog offers "In/Out range on track V_n". It resolves each item to (file, source in/out). Multicam
   items are resolved as below. A dialog shows the shot list (mode, handles, fuse on/off). Unresolvable items are
   listed with the reason. It writes `job.json` to `<output root>/<project>/<timeline>/<job id>/` and starts
   `gigsteady batch` detached (new session, nohup, its own log). The menu returns at once.
2. **Status.** Read-only: done/total, failures, ETA, from the job directory.
3. **Place results.** For a finished job: bring up the media pool bin "gigsteady/<job>", import the outputs, add a
   new video track (Resolve 21.1 `AddTrack` only adds at the top) named `gigsteady <job>`, and append each result
   with `{mediaPoolItem, startFrame: handle_in, endFrame: handle_in + n (EXCLUSIVE on 21.1), recordFrame: item
   start, trackIndex: new, mediaType: 1}`. Then it reads back every placed item (start, end, left offset, file path)
   against the original's record range. Before it starts, it saves the playhead (`GetCurrentTimecode`) and In/Out.
   `AddTrack`/`ImportMedia`/`AppendToTimeline` clear In/Out and move the playhead (runbook, seen twice), so it
   restores both after and reads them back. It never deletes, moves or edits an existing item. The unique IDs of
   the created items and track are recorded so that **Undo place** can remove exactly those.
   It refuses to run if a render is in progress, if the open timeline is a multicam (it hijacks the API), or if the
   job's timeline isn't the current one.

Rules carried over: `atexit.register(lambda: (sys.stdout.flush(), os._exit(0)))` first in every external script;
verify every write by read-back, never by return value; no clip property reads while a render runs; no
`StartRendering()` anywhere; Mark In/Out and markers are relative to `GetStartFrame()`, items are absolute;
append in chunks of ≤ 300.

Not solved by the MVP, needs Dean's call: the result is ungraded Log ProRes, so the new item doesn't carry the
source's grade, colour group or input colour space. (On Log projects the API can only half-set the input space;
see the runbook.) Options: (a) leave grading to Dean; (b) copy the source item's transform and, for plain clips,
`CopyGrades` / `AssignToColorGroup`; (c) for multicam items, map the angle to the camera's group by name. Any of
these gets proved by a 2-frame render comparison before it's trusted.

## Multicam: how a timeline item maps to a source file and frame

Facts (runbook, 21.1): `GetMediaPoolItem()` returns the multicam clip. `GetSourceStartFrame()` returns multicam time
and sometimes rounds one frame early (13 of 32 Chase cuts). The item's `GetName()` is "<multicam> - <active angle>".
There's no API to read the angles.

General approach, in order:

1. **FCPXML export of the timeline** (`Timeline.Export(path, EXPORT_FCPXML_1_10)`). It writes a file and doesn't
   touch the project. The `<media><multicam>` resource lists every `<mc-angle>` with its clips (asset `src`,
   `offset`, `start`, `duration`). Each cut is an `<mc-clip>` with `offset/start/duration` and an
   `<mc-source angleID srcEnable="video">` saying which angle is active. Then source frame = multicam time − angle
   clip offset + angle clip start, on the right segment when an angle is made of several clips. This needs
   checking against the known Sydney map (Static Desk: src = rec − 89960) before it's trusted.
2. **Fallback: the sync timeline** (the prototype's method). Dean names the timeline the multicam was built from,
   and angles are matched to its tracks by name: src = multicam time − rec_start + src_start.
3. **Always: a picture check per item.** gigsteady matches one frame Resolve shows (`TimelineItem.ExportFrame` or
   `Timeline.ExportFrame`; to be tested on 21.1, since stills exports have been stale before) against source frames
   f−3 … f+3 on structure (gradients, so the grade doesn't matter), corrects a ±1 offset, and flags anything else.
   This catches the rounding error the runbook found.

What it can't handle (refused with a reason): retimed items or angle clips (speed ≠ 100%); angles whose clip is a
compound, Fusion clip or nested timeline; items that span two angle clips (split them in Resolve); title,
generator or adjustment items; and a multicam opened in the timeline. Plain clips use the media pool item's file
path and `GetLeftOffset()` (exact frames), with the same picture check.

## Gyro: bounded-memory plan

Cause of the 24 GB peak: `Input::from_stream` parses every rtmd sample into a `GroupedTagMap` and keeps them all
(`input.samples`). The pieces needed for a fix are already public in the vendored crate:
`util::read_track_samples` hands over each sample *before* its bytes are read, and `Sony::parse_metadata(data, …)`
parses one sample.

Plan (`rust/gs-gyro`):
1. **Ranged parse.** Add `InputOptions.time_range: Option<(f64, f64)>` (a ~20-line patch to the Sony MP4 path that
   skips samples outside the range before reading them) plus a tag whitelist (gyro, accelerometer, IBIS, imager,
   lens). `normalized_imu` then works unchanged on the reduced set. The patch is kept as a pinned git dependency
   with a patch file; it could go upstream later.
2. **Chunked cache.** `gigsteady gyro <clip>` dumps in 2-minute chunks with 2 s of overlap into a per-file cache
   (`~/Library/Caches/gigsteady/gyro/<size>-<mtime>-<stem>/`: imu.f64, ibis.f64, frames.f64, a manifest of the
   chunks done). Memory stays bounded by the chunk, and a stopped dump resumes. A shot only needs its own range, so
   `stabilise` dumps just that range (+2 s) on demand.
3. **Checks.** Ranged output for C5691 equals the prototype's full `data/gyro/C5691.bin` (+ `.ibis`, `.frames`)
   sample for sample. Peak RSS (`/usr/bin/time -l`) of a full chunked dump of the 100-min FX30 drum file comes in
   under 1 GB, against 24 GB. Watch for timestamps being rebased to the first sample when parsing starts
   mid-file; the comparison catches it.

The calibration (±80 ms offset search, least-squares axes/scale with IBIS, R² ≥ 0.5 gate, crossover 4 frames, gyro
fills unmatched steps) ports as is. It uses the camera's own frame timestamps from `.frames` instead of
`frame/fps`.

## Review page (follows the labelling rules)

Instructions at the top in the terms you decide in ("would you use this stabilised version in the cut?": yes /
yes with a trim / no / can't tell, plus a note). The clips are named neutrally (S01, S02 …) in random order, with a
few repeated unannounced. The page shows no mode, crop, fused count or flags until the marks are exported. The
layout keeps the prototype's: whole frame on top, 1:1 4K crop below, original | stabilised. For comparisons
between versions (prototype vs gigsteady), the sides are A/B, randomised per clip. Exports are dated CSVs and
stay unedited.

## Test plan (don't grade a method with itself)

- **Unit (synthetic, seconds, no footage):** ECC recovers known shift/rotation on textured noise under a 3x
  brightness flip and haze; lighting states tie by median (no 3.6 px bias); fill_gaps with no matches; L1 path
  stays inside bounds and is piecewise; needed_zoom vs brute force; gyro calibration recovers known axes, scale
  and a 24 ms offset from synthetic steps; fusion weight cap (no 1e9), moving gate keeps a "waving" patch
  untouched; resume (kill mid-write → re-run redoes the shot); ranged decode equals sequential decode (H.264 and
  HEVC); handles clamp at the file's ends.
- **Regression (reference shots, independent metrics, tolerances):**

  | Shot | Check | Pass |
  |---|---|---|
  | SD_unstab_308139 | lock; measure_steady wobble; pc wobble; burst_check frames 122-123 | wobble ≤ 0.3, pc ≤ 0.7; 122-123 ≥ 88 (prototype 92, from 70) |
  | SD_render8 (light flips) | step at each flip frame; fuse_check | no step over 0.5 px at flips; flip frames unchanged by fusion |
  | PP_render0/1 | orb_jitter (+ ECC jitter) | ≤ prototype +5% (ours 9.3 / 5.4), crop ≤ 1.105 |
  | Brisbane T1/T3 (C5691 8:52, 10:16) | gyro R² with IBIS; gyro-truth shake left | R² ≥ 0.85; ≤ prototype +5% |
  | 16 hard pit windows | no failure, crop ≤ 1.105, orb_jitter | all ok; median ≤ prototype +5% |

  Same encoder on both sides (hardware ProRes reads ~5% sharper than prores_ks).
- **Approved-batch parity (73 Static Desk + 75 Photo Pit, analysis only, ~1-2 h with 3 jobs):** compare
  gigsteady's per-frame correction with the prototype's saved per-shot CSVs. Static Desk should match: max
  deviation ≤ 0.5 px at 4K, same mode, crop within 0.005. Any shot outside that is rendered and goes on a blind
  A/B page. Photo Pit **will differ by design**: the approved renders used the Gaussian sigma search (logs show
  `sigma`), and some predate the crop budget. If L1 is the default there, gate on no failures, crop ≤ 1.10 and
  orb_jitter not worse, plus a blind A/B review of ~16 stratified shots (most where the paths differ most) with 3
  repeats.
- **gyro-truth speed-up:** decode each window once at analysis size (cached as npy), run every method on the same
  frames, and use the ranged gyro cache. The target is the 8 windows in under 10 minutes (it timed out at 30 after 6).

## Step order (each step ends verified and committed)

1. Repo skeleton, uv env, CLI stub, probe + ranged decode/encode with frame-exact tests. (Find out the personal
   git identity from the account-separation note first.)
2. Port prep/register/lock and the lock tests; `stabilise --mode lock` on SD_unstab_308139 (no fusion) and
   measure.
3. Track mode + L1/gauss planners, auto-mode rules (registration share, median correlation, 40 px scaled);
   PP_render0/1 and the hard windows.
4. Fusion, CPU then MPS; SD_unstab_308139 burst and SD_render8 flips.
5. Batch + resume + CSV/logs/summary; Static Desk parity run (analysis only) against the 73 approved shots.
6. gs-gyro ranged dump + cache, with the bit-for-bit check on C5691 and the RSS check on the FX30 file; port
   gyrofuse; T1/T3; faster gyro-truth eval.
7. Review page (blinded) + Photo Pit A/B page for Dean.
8. Resolve: read-only resolver (plain + multicam via FCPXML, the sync-timeline fallback, the picture check),
   tested read-only against the Sydney project and checked against the prototype's sync map.
9. Resolve: menu scripts and Place, developed in a throwaway test project. The first write into a real project
   waits for Dean.
10. Notebook, README, cleanup-register rows, memory note.

After the MVP: gyro-driven rolling-shutter correction (FX30 47-48 Hz), and a better crop/path trade-off for fast
handheld shots at 1.10.
