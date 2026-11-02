# Known issues

## De-blur ghosts moving people seen through haze (found 2026-11-03, shipped in good 4 u)

**What:** in lock mode with vibration de-blur, a moving person can be blended in from a neighbouring frame, leaving
a faint doubled outline. Seen by Dean on good 4 u, SD_rec155859 (record 155860 on the Sydney final cut; drummer and
singer behind stage haze) and "a few other frames". Shipped as is.

**Evidence** (`work/artefact_155859/`, not in git): `trio.png` = camera original | gigsteady v0.1.2 output | Render in
Place (graded), drummer region at 1:1, record frame 155860 (source 3O1A7144.MP4 frame 65900, output frame 26). `trio2.png`
= original | output | same shot re-rendered with `--fuse 0`: the doubling is gone with de-blur off, so de-blur causes it.
That shot fused 56 of 155 frames.

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
