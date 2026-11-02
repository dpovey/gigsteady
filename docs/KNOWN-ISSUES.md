# Known issues

## De-blur ghosts moving people seen through haze (found 2026-11-03, shipped in good 4 u)

**What:** in lock mode with vibration de-blur, a moving person can be blended in from a neighbouring frame, leaving
a faint doubled outline. Seen by Dean on good 4 u, SD_rec155859 (record 155860 on the Sydney final cut; drummer and
singer behind stage haze) and "a few other frames". Shipped as is.

**Evidence** (`work/artefact_155859/`, not in git): `trio.png` = camera original | gigsteady v0.1.2 output | Render in
Place (graded), drummer region at 1:1, record frame 155860 (source 3O1A7144.MP4 frame 65900, output frame 26). `trio2.png`
= original | output | same shot re-rendered with `--fuse 0`: the doubling is gone with de-blur off, so de-blur causes it.
That shot fused 56 of 155 frames.

**Dean's marks (2026-11-03):** ghost visible at 01:43:54:10 and :11 (strong) and :16, :17, :18 (faint). De-blur
blended exactly the run :10 to :18 (output frames 26-34; fused vs `--fuse 0` differs on those 9 and is identical
elsewhere, `work/artefact_155859/ghost_metric.csv`). The unmarked :12 to :15 change about as much (mean |d| 1.6-2.6 vs
1.4-2.7 10-bit codes in the drummer/singer region) and still-frame crops show doubling there too
(`fused_2468.png`: :11 | :13 | :15 | :17), so the whole fused run is affected and the marks record what reads at
playback speed. A ghost check therefore can't be "how much did fusion change the frame"; it has to detect doubled
edges (structure in the fused frame that lines up with the neighbour's position, not the current frame's).

**Cause:** the moving-area gate (`src/gigsteady/gpu.py:146-149`, same logic in `fuse_cpu.py`) flags a pixel as
moving when the coarse (sigma 8, half-res) difference to a neighbour exceeds `tau` (0.06) of the local *mean*
luminance `L`. Haze raises `L` and lowers the contrast of people behind it, so their motion stays under 6% and they
are fused. The similarity term (same `tau`, same `L`) is lenient for the same reason.

**Fix to build (next version):**
1. Normalise the motion and similarity tests by local *contrast* (local std of luma, or the noise level), not local
   mean, so a low-contrast subject that moves still trips the gate. Calibrate on this shot plus the SD_unstab_308139
   burst (which must keep its 70 → 92 sharpness gain).
2. Use a finer coarse map (sigma 8 at half res is wider than a head at this distance) or add a per-pixel test.
3. Add a ghost check to the regression tests and to timeline QC: compare fused against `--fuse 0` in non-static
   areas and fail on edge doubling.
4. Until then: on hazy shots with people moving, `--fuse 0` (lock-off without de-blur) avoids it.
