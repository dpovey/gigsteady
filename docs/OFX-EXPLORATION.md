# gigsteady as a DaVinci Resolve OFX plugin: exploration (2026-11-03)

Desk research only. Nothing was run in Resolve and no plugin was built. Each claim is tagged
**[header]** (read in the Resolve 21.1 Developer/OpenFX package on this Mac), **[web]** (fetched page) or
**[unconfirmed]**. Paths in the "Sources" section.

## Summary

- OFX is feasible for the render half of gigsteady: warp plus de-blur. Resolve's own sample package includes a
  Metal plugin that fetches other frames at arbitrary times (`RandomFrameAccessPlugin`) and one that fetches
  previous and next (`TemporalBlurPlugin`). That is the exact capability the de-blur needs. [header]
- Dean's requirement is to keep the CLI and Resolve-script route and add OFX as a second front end. This works if
  the analysis stays in Python and writes one sidecar per shot that both renderers read. The risk is that two
  renderers (PyTorch/MPS and a Metal or C++ kernel) give different pixels. Section "Proposed architecture" sets out
  how to contain that.
- Resolve exposes the source file path and source frame number to a plugin through Blackmagic-specific properties
  (`OfxImageEffectPropSrcFilePath`, `OfxImageEffectPropSrcFrame`). If they are populated for timeline and multicam
  clips this solves the sidecar-keying problem. I could not confirm that they are, which is the first thing to test.
- There is no standard OFX analysis action. A multi-frame pass inside the plugin is possible only by hand (button
  plus fetching frames over a range) and would duplicate ECC registration in C++/Metal. Keep analysis offline.
- Free Resolve versus Studio: could not confirm from a primary source. Secondary sources say third-party OFX works
  in both. Gyroflow's OFX is documented for Resolve generally. GPU-accelerated OFX in free Resolve is
  [unconfirmed].
- Cheapest route: C++ with Blackmagic's sample as the template (Xcode project and Makefile supplied), Metal
  kernels. Rust (`ofx` crate, as Gyroflow does) is possible but has a thinner Metal story.

## Resolve OFX host facts

**Version and suites.** Resolve 21.1 ships OpenFX 1.4 headers plus a Blackmagic extension header
(`ofxImageEffectExt.h`) and the 1.5 native-colour header `ofx-native-v1.5_aces-v1.3_ocio-v2.3.h`. [header] The
README is "Updated as of 12 May 2026". The Support folder is the standard OFX C++ wrapper. [header] I did not
enumerate which suites Resolve actually returns from `fetchSuite` at run time [unconfirmed].

**GPU rendering.** The sample README says the samples use "CUDA, OpenCL and Metal render support". [header]
Opt-in is by descriptor flags, from `RandomFrameAccessPlugin.cpp`: `setSupportsMetalRender(true)` on Apple,
`setSupportsOpenCLRender(true)`, and `setSupportsCudaRender(true)` + `setSupportsCudaStream(true)` off Apple. [header]
Standard OFX semantics [web, ofxGPURender.h reference]: host sets `kOfxImageEffectPropMetalEnabled`=1 when both sides
support it; images then arrive as `id<MTLBuffer>` in `kOfxImagePropData`; the host passes
`kOfxImageEffectPropMetalCommandQueue`; the plugin should enqueue work on that queue and not wait. The samples ship
`MetalKernel.mm` (Objective-C++ that builds a compute pipeline from source). The CPU path is still required as a
fallback, and the sample's `render()` only accepts float RGBA (anything else throws `kOfxStatErrUnsupported`). [header]

Notes that matter for us:
- A third-party write-up says Resolve does not use the OFX OpenGL render path, and that Resolve Studio from 16 on is
  Metal-capable. [web, secondary; see Sources] Treat as plausible, not authoritative.
- Images are device buffers, not textures, so a Lanczos kernel is hand-written (no hardware sampler for Lanczos
  anyway).
- Whether a Metal plugin may use MPS/MPSGraph or share the host's `MTLDevice` beyond the command queue is
  [unconfirmed]. The queue gives the device (`queue.device`).
- Tiles: the temporal sample sets `kSupportsTiles false`, `setHostFrameThreading(false)`. [header]

**Temporal access.** Supported and shipped as a sample. The recipe in `RandomFrameAccessPlugin.cpp`: descriptor
`setTemporalClipAccess(true)`; input clip `setTemporalClipAccess(true)`; in `render`, call
`m_SrcClip->fetchImage(frame - interval)`, `fetchImage(frame)`, `fetchImage(frame + interval)`. `getFramesNeeded` is
an override that can declare a range (the sample leaves it empty; `TemporalBlurPlugin` uses it). [header] Standard
OFX note [web]: if a host caches output, frames generated out of temporal order may be invalid and need re-render.
Unknown [unconfirmed]: the cost of each `fetchImage` (decode of a 4K HEVC/ProRes frame at another time), how many
the host keeps in cache, whether 9 fetches per output frame (4 each side) is fast or thrashes, and behaviour at clip
ends (handles, past end of source).

**Multi-frame analysis pass.** OFX has no analysis action. Known patterns (from OFX practice generally, none
verified in Resolve): (a) a push-button parameter whose `changedParam` handler loops `fetchImage(t)` over
`clip->getFrameRange()` and stores results; (b) a sidecar file computed outside the host; (c) results stored in a
hidden string parameter (serialised with the project). Whether Resolve lets `changedParam` run a long loop of
`fetchImage` on arbitrary times (rather than only inside `render`) is [unconfirmed] and is a key spike question.
I found no documented Resolve-specific example of (a).

**Persistent per-clip data.** Parameters are saved in the Resolve project/node graph. A string parameter holds a
path (`kOfxParamStringIsFilePath` exists, [header]) or a small blob. A big per-frame array (a few floats x thousands
of frames) in a string param is possible but heavy; a path to a sidecar is cleaner. Per-instance (not per-clip)
storage: when the same node is copied by CopyGrades or similar, the param copies too. Behaviour with multicam angles
and Render-in-Place [unconfirmed].

**Where a plugin sees frames.** `render` gets `args.time` on the timeline-effect clip time (the clip as it reaches
the node). What `fetchImage(t)` means for a plugin on a clip-level Edit/Color node is expected to be source-clip
frames after Resolve's own retime, but this is [unconfirmed]. Handles beyond the cut: fetch at times outside the
clip's range may or may not return source media past the cut [unconfirmed]. Blackmagic added two properties for
this: `OfxImageEffectPropSrcFilePath` (instance, read-only, "the source file path") and
`OfxImageEffectPropSrcFrame` (render inArgs, "the source frame that is being rendered"), plus
`OfxImageEffectPropResolvePage`. [header, ofxImageEffectExt.h] The comment text is thin and I could not find
Blackmagic documentation saying when they are filled (clip node vs timeline node vs multicam angle vs compound/
nested). If filled correctly they give "source file + source frame", exactly the sidecar key. Multicam is the
highest-risk case. The existing tool already re-composes multicam angle transforms by hand, which suggests the
angle is applied after the clip's own transform, so a plugin on the angle's clip node would see the angle source.
Not verified.

**Free versus Studio.** Secondary sources (TryUncle 2026, Neat Video) say Neat Video's OFX works in free and Studio.
[web, secondary] Blackmagic's feature comparison was not fetched. Free Resolve may restrict GPU processing to a
single GPU and some codecs, but whether it runs GPU OFX is [unconfirmed]. Dean has Resolve 21.1 (Studio or free not
stated in the task); check which.

## Prior art

**Gyroflow OFX.** Repo `gyroflow/gyroflow-ofx` is marked obsolete; the maintained code is
`gyroflow/gyroflow-plugins` (folders `openfx`, `adobe`, `frei0r`, `common`). [web]
- Language: Rust. `openfx/Cargo.toml`: `crate-type = ["cdylib"]`, depends on `ofx` 0.3 patched to
  `AdrianEddy/ofx-rs` rev `0b15219`, plus `gyroflow-plugin-base` (local `../common`). Source files in `openfx/src`:
  `lib.rs`, `gyroflow.rs`, `fuscript.rs`. [web]
- Per-frame data: the plugin does not analyse. It reads a `.gyroflow` project file (exported from the main app,
  contains gyro data and parameters) or loads gyro from the video file itself for GoPro 8+, DJI, Insta360, Sony and
  BRAW (Sony/BRAW need a lens profile). Params: "Load for current file", "Browse". [web] This is precisely the
  "offline analysis, sidecar, apply at render time" architecture proposed here.
- GPU path and Resolve workarounds: not stated on the pages I read. The core library is Gyroflow's (wgpu/OpenCL),
  but I did not read the source [unconfirmed]. The README says applying it on the Edit or Color page runs faster than
  in Fusion. [web]
- Licence: GPL-3.0 (older repo); GPLv3 with an App Store exception (plugins repo). Fine for reading and ideas; do not
  copy code into a non-GPL project. gigsteady is MIT-licensed and public, so read Gyroflow's plugin for ideas but don't copy its code.
- Open issues: 43 on the plugins repo; I did not read them. Reading them for Resolve multicam, caching and
  retime reports is a cheap next step.

**Resolve's own samples.** `TemporalBlurPlugin` and `RandomFrameAccessPlugin` (Blackmagic, 12 May 2026). Both have
Xcode projects and Makefiles that produce `[Name].ofx.bundle`. [header]

**Neat Video** (OFX build for Resolve, 6.1.3 added Resolve 21 support per a UniFab review): temporal denoise in OFX
is a normal product category, which shows the host supports multi-frame temporal reads at production quality.
[web, secondary] Its analysis is a "profile" built interactively and stored as presets, a pattern (b)/(c) above. I
found no primary source on how it fetches frames or on its multicam or caching problems.

**Not found:** any primary report of OFX problems with caching, frame-access order or multicam in Resolve. The
only caveat I found is a Drastic note that a plugin placed on the Timeline node can misbehave when render cache is
on. [web, secondary] Blackmagic forum searches returned nothing on `TemporalClipAccess`.

## Tooling

- **C++ with Blackmagic's template.** Copy `RandomFrameAccessPlugin` (the nearest to de-blur). It has the Support
  C++ wrapper, `MetalKernel.mm`, Xcode project and Makefile. Lowest friction, supported by the host vendor. [header]
- **Rust.** `ofx` crate (0.3; Gyroflow patches a fork), or `openfx-sys` bindings. [web] Good for sharing the sidecar
  reader with `rust/gs-gyro` (already in this repo), but Metal compute from Rust (`metal` or `objc2-metal`) means
  writing the kernels in MSL regardless. Gyroflow's use proves Resolve will load a Rust cdylib.
- **Python inside OFX:** not realistic (embedding an interpreter plus torch in the host, GIL, no MPS buffer
  interop). Rule out.
- **Install/signing (macOS).** Bundle `Name.ofx.bundle` goes in `/Library/OFX/Plugins` (needs admin; this Mac has no
  `/Library/OFX/Plugins` yet). [header] Gyroflow README adds: enable under Preferences > Video plugins, restart
  Resolve. [web] Apple Silicon: build `arm64`. Code signing and notarisation are needed only to distribute; a locally
  built bundle with ad-hoc signature (`codesign -s -`) should load, but I did not test whether Resolve's hardened
  runtime enforces library validation on OFX [unconfirmed].
- **Runtime dependencies in the bundle:** keep the bundle self-contained (static Metal source or `.metallib`
  in `Resources`, no Python, no torch).

## Proposed architecture

Principle: one analysis, two front ends. The CLI/Resolve-script route stays as is; OFX is added, not substituted.

```
gigsteady analyse   (Python, unchanged: ECC, lighting states, L1 plan, gyro fusion)
        |
   per-shot sidecar  <src>.gsteady.json (+ .npy / binary):
        |             key = source file path + hash(size, mtime, first MB) + fps, size
        |             per source frame: 2x3 (or dx,dy,rot,scale) correction, zoom, valid flag,
        |             per-frame static/moving mask params, plan version, options hash
        +----------------------> CLI render (today): torch/MPS warp + de-blur -> ProRes -> Resolve script placement
        +----------------------> OFX render (new): Metal warp + de-blur at render time, in Resolve
```

**Shared (single source of truth).** All analysis; the sidecar schema and writer; `warp.py` maths (`full_matrices`,
`needed_zoom`, `output_matrices`: the fixed per-shot zoom and the crop budget); options hash; the gyro cache; the
review page and evals (they can score either front end's output).

**Duplicated, by necessity.** The pixel kernels. OFX needs a Metal (and, if wanted, CPU) implementation of the
Lanczos warp, and of the de-blur: per-neighbour alignment, crispness weighting, static-area mask, x16 cap, moving
gate dilation. The sidecar reader (Python writer, C++ or Rust reader; keep the format trivial, e.g. a small JSON
header plus a flat little-endian float32 array, so there is no parser logic to diverge).

**Open decision on de-blur.** Today fusion does its own per-neighbour ECC at render time (`fuse_cpu.py`,
`gpu.py:FuserGPU`). That is analysis inside the render and the heaviest thing to port. Two options:
1. Move the neighbour alignment into the analysis pass and store per-frame, per-neighbour 2x3 matrices (and the
   moving-area mask parameters or a low-res mask) in the sidecar. Then both renderers only warp and blend, and the
   OFX kernel stays simple. This also makes the CLI and OFX share the alignment, which removes the largest
   parity risk. Cost: sidecar size (up to 8 matrices per frame, small) and a change to the existing fuser.
2. Port ECC to Metal. Not recommended.
Recommend option 1; it needs measuring that stored alignments match render-time ones (they should, being
deterministic on the same frames).

**Parity risk and controls.**
- Resampling differences: torch `grid_sample` bicubic on MPS (the GPU path, per PLAN.md) versus CPU `cv2` Lanczos
  versus a new Metal Lanczos. These three already differ. Pick one reference (cv2 Lanczos4 on CPU is the spec),
  write the Metal kernel to match it, and test.
- Edge handling (reflect borders), pixel-centre convention (cv2 uses integer pixel centres), half-pixel
  offsets, and 8/10-bit versus float input: Resolve hands float RGBA, the CLI path is 16-bit RGB via ffmpeg. Colour
  and range handling is a different pipeline (that is a benefit of OFX: no ffmpeg range/matrix round trip).
- Parity test: render ~20 frames from three shots via both paths, difference them in float, report max/mean
  abs error and PSNR; set a tolerance (for example PSNR above 50 dB on the warp-only path). Put this under
  `tests/regress`. Run it whenever either kernel changes.
- De-blur blending is non-linear (selection by crispness, caps): small differences can flip a frame's weights.
  Store the weights (or the chosen neighbours) in the sidecar, so both paths take the same decisions.
- Version both: sidecar `plan_version` and a `kernel_version` string stamped into the CLI report and shown in the
  OFX UI, so a mismatch is visible.

**What OFX does not need to do.** Placement, CopyGrades, multicam recomposition, track shuffling and
Render-in-Place all go away for OFX users, because the effect sits on the clip node and the timeline stays
untouched. The Resolve-script route keeps these for ProRes-based delivery.

**Plugin UI.** Parameters: sidecar path (auto-resolved from `SrcFilePath` + a lookup folder), "Reload", enable
de-blur, de-blur strength, neighbours (0 to 4), a debug overlay (crop/zoom, plan version). Missing sidecar: pass the
frame through unchanged and show a message (`sendMessage`), do not error mid-render.

## Risks and unknowns

Ordered by what kills the idea:
1. **Source identity.** Are `SrcFilePath` and `SrcFrame` populated on a clip node, a timeline node, a multicam angle,
   a nested/compound clip, and after a speed change? If not, keying the sidecar needs a user-selected file plus a
   frame offset parameter, which is fragile on trimmed clips. Biggest single unknown.
2. **Temporal fetch semantics and cost.** What `fetchImage(t±n)` returns on timeline vs source clips; handles past the
   cut; cost with 8 neighbours at 4K; cache behaviour in playback and during delivery render; whether Resolve calls
   `render` out of order or in parallel across frames (the sample sets `HostFrameThreading false`, but does the
   host render several frames concurrently regardless?).
3. **Multicam.** Does a plugin on a multicam angle see the angle source, with the angle's own transform applied before
   or after the plugin? The current tool recomposes these by hand, so this matters.
4. **Colour/bit depth.** What space the host hands in (timeline colour space, float), and that warp+blend are fine in
   that space; the sample accepts float RGBA only.
5. **Metal in a free Resolve** and under Rosetta-free arm64: [unconfirmed].
6. **Performance.** 4K Lanczos plus 9 fetched frames per output frame in real time is unlikely on a laptop; it needs
   only to be fast enough to scrub and to render at a reasonable rate. Render-in-Place of the OFX result is still an
   option for a heavy shot.
7. **Parity** as above. 8. **Licence**: do not lift GPL code from Gyroflow.
9. **Signing/hardened runtime**, and `/Library/OFX/Plugins` needs admin; a per-user path is not documented
   ([unconfirmed]).
10. **Sidecar staleness**: footage edited or re-analysed while Resolve is open; plan: key by hash, reload button.

## Spike plan

Goal: answer risks 1 to 3 and 6 with the smallest build. Estimated one to two days. No gigsteady maths yet.

1. Copy `RandomFrameAccessPlugin`, rename, build with the supplied Xcode project or Makefile, install in
   `/Library/OFX/Plugins` (admin), enable, restart Resolve. Confirm it loads and runs on Metal. (Needs Dean to
   approve the install; use the throwaway project "gigsteady test".)
2. Change the render so it ignores pixels and writes diagnostics: log (to a file) `SrcFilePath`, `SrcFrame`,
   `args.time`, `kOfxImageEffectPropResolvePage`, `renderScale`, the clip's `getFrameRange()`, `fetchImage` success for
   `t-4..t+4` and for times outside the clip range, and the elapsed time of each fetch.
3. Run the same plugin on: (a) a plain timeline clip on the Edit page, (b) a clip trimmed with handles available,
   (c) a clip with a speed change, (d) a multicam angle (Edit page and via the Color page node), (e) a compound clip,
   (f) during Deliver render and with render cache on. Read the log to learn what is filled and in what order frames
   come.
4. Make the plugin draw `SrcFrame` as a number/barcode into the image, plus the sum of the fetched neighbour at +1
   shown as a difference image. This gives a visual check without logs and proves frames are source-aligned.
5. Add a hardcoded 2x3 affine warp (Metal) from a tiny JSON sidecar keyed on `SrcFilePath` for one shot already
   analysed by the CLI. Compare to the CLI ProRes output for the same frames: the first parity number and timing.
6. Decision gate: if `SrcFilePath` + `SrcFrame` are reliable (3a to 3e) and fetch cost is acceptable, port de-blur
   (option 1). If multicam fails, ship OFX for single-camera clips only and keep the script route for multicam.

## Sources

Primary, read on this Mac (Resolve 21.1):
- `/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/OpenFX/README.txt`
- `.../OpenFX/RandomFrameAccessPlugin/RandomFrameAccessPlugin.cpp` and `MetalKernel.mm`
- `.../OpenFX/TemporalBlurPlugin/TemporalBlurPlugin.cpp`
- `.../OpenFX/OpenFX-1.4/include/ofxImageEffectExt.h` (Blackmagic extensions: `OfxImageEffectPropSrcFilePath`,
  `OfxImageEffectPropSrcFrame`, `OfxImageEffectPropResolvePage`, `OfxImageEffectPropNoSpatialAwareness`)
- `.../OpenFX/OpenFX-1.4/include/ofxGPURender.h`, `ofxParam.h`, `ofxImageEffect.h`

Web:
- OpenFX GPU render reference: https://openfx.readthedocs.io/en/main/Reference/api/file/ofxGPURender_8h.html
- OpenFX properties reference (TemporalClipAccess): https://openfx.readthedocs.io/en/latest/Reference/ofxPropertiesReference.html
- OpenFX 1.5 release notes: https://openfx.readthedocs.io/en/main/_sources/ReleaseNotes/relnotes-1.5.rst.txt
- Gyroflow OFX (obsolete): https://github.com/gyroflow/gyroflow-ofx
- Gyroflow plugins (maintained): https://github.com/gyroflow/gyroflow-plugins ;
  `openfx/Cargo.toml` at https://raw.githubusercontent.com/gyroflow/gyroflow-plugins/main/openfx/Cargo.toml
- Secondary (Metal in Resolve, no official status): https://dev.to/denissvinarchuk/the-easy-way-to-develop-your-own-apple-metal-plugin-and-integrate-it-into-davinci-resolve-k60
- Secondary (Neat Video free/Studio): https://tryuncle.com/learn/davinci-resolve/best-davinci-resolve-plugins-for-beginners-to-buy
- Secondary (timeline-node caching caveat): https://drastic.tv/support-59/supporttipstechnical/226-using-the-drastic-openfx-plugins-in-davinci-resolve

Could not confirm (no source found): Resolve's full OFX suite list at run time; which of `SrcFilePath`/`SrcFrame`
are filled where; behaviour of `fetchImage` outside a clip's range and on multicam; free-Resolve GPU OFX; hardened
runtime effect on unsigned bundles; any Blackmagic forum thread on temporal access; Gyroflow's GPU path and its
Resolve-specific workarounds (source not read).
