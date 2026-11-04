# OFX spike: gigsteady probe

A pass-through OpenFX effect that logs what DaVinci Resolve gives a plugin, to settle the unknowns in
[../../docs/OFX-EXPLORATION.md](../../docs/OFX-EXPLORATION.md) before the real renderer is built:

1. Is the source file path (`OfxImageEffectPropSrcFilePath`) set, and the source frame
   (`OfxImageEffectPropSrcFrame`), on plain, trimmed, speed-changed, multicam and compound clips, and in a Deliver
   render? These would key a per-shot gigsteady sidecar.
2. What time does `render()` receive, and the clip's frame range (mapped and unmapped)?
3. Can neighbouring frames (t-r .. t+r) be fetched, including at the clip's edges, and what does each fetch cost?

It is CPU only and passes the picture through unchanged. Each render call appends one JSON line to
`~/Library/Logs/gigsteady-ofx-spike.log`.

## Build and install

Needs the Xcode command line tools and DaVinci Resolve (its OFX SDK is under
`/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/OpenFX`; the BSD support library is copied
from there and patched at build time, so nothing third-party is vendored here).

```
make                  # build/GigsteadyProbe.ofx.bundle (arm64, ad-hoc signed)
make check            # loads the bundle outside Resolve and prints both plugin identities
sudo make install     # copies only, run make first; /Library/OFX/Plugins/GigsteadyProbe.ofx.bundle, then restart Resolve
sudo make uninstall
make engine           # development engine: reloads into a running Resolve, no sudo, no restart
make engine-off       # back to the engine built into the plugin
```

Everything that touches pixels or runs the analysis is in the engine (`Engine.cpp`, `ProgressPanel.mm`, C interface
in `Engine.h`). `make engine` puts a copy in `~/Library/Application Support/Gig-steady/dev/`; the installed plugin
loads it again whenever it changes. Changing the controls (`GigsteadyControls.cpp`) or `Engine.h` still needs
`make`, `sudo make install` and a Resolve restart.

In Resolve it appears under Effects > OpenFX > Gig-steady > "gigsteady probe".

The bundle also holds "Gig-steady" (`GigsteadyControls.cpp`, identifier `com.dpovey.gigsteady.preview`), the
stabiliser itself. **Stabilise** reads the 501 frames around the playhead (the plugin isn't told where the cut
starts and ends), shrinks each to 960-wide luma and streams them to this repo's `gigsteady analyse-frames`
(`.venv/bin/gigsteady`, built in at compile time), the same analysis as the CLI, with a progress window and Cancel.
The result (mode, zoom, per-frame correction) is stored in the project; render warps each analysed frame (bilinear)
and passes other frames through. Mode and crop budget need a re-analysis; manual zoom applies at render. De-blur
and the overlay are controls only so far. Analysis errors go to `~/Library/Logs/gigsteady-analyse.log`.

`make enginetest` builds a harness that runs the engine on ffmpeg-decoded frames without Resolve;
`enginetest.py` compares its renders and path with the CLI's.

## Running the test

`run_probe_test.py` (system Python, Resolve running) builds a throwaway timeline "gigsteady OFX probe test" with
three hardcoded clips (a plain clip, a trimmed clip, a multicam cut), applies the probe to each with
`TimelineItem.AddEffect("ofx", ...)` and steps the playhead so the viewer renders them. It never starts a render; a
Deliver render is done by hand or with the editor's render tool. `cleanup` deletes the test timeline.
`run_order_test.py build / measure / cleanup` checks whether the grade and clip zoom come after the effect (frame exports, no render). `summarise_log.py` turns the log into a table per source file. Each fetch is logged with a pixel signature (`sig`),
so a fetch that returned a clamped edge frame instead of the real neighbour shows up as a repeated signature.
