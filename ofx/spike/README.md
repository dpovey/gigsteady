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
make check            # loads the bundle outside Resolve and prints the plugin identity
sudo make install     # copies only, run make first; /Library/OFX/Plugins/GigsteadyProbe.ofx.bundle, then restart Resolve
sudo make uninstall
```

In Resolve it appears under Effects > OpenFX > Gig-steady > "gigsteady probe".

The bundle also holds "Gig-steady" (`GigsteadyControls.cpp`): the planned Inspector controls (Mode Auto/Lock/Track,
crop budget, manual zoom, de-blur and its settings, overlay, reload), so the layout can be tried in Resolve. It does
nothing to the picture yet. Mode, framing and de-blur will choose between results the CLI analysis stores in the
shot's sidecar (both the lock and the track path), so changing them never needs re-analysis.

## Running the test

`run_probe_test.py` (system Python, Resolve running) builds a throwaway timeline "gigsteady OFX probe test" with
three hardcoded clips (a plain clip, a trimmed clip, a multicam cut), applies the probe to each with
`TimelineItem.AddEffect("ofx", ...)` and steps the playhead so the viewer renders them. It never starts a render; a
Deliver render is done by hand or with the editor's render tool. `cleanup` deletes the test timeline.
`summarise_log.py` turns the log into a table per source file. Each fetch is logged with a pixel signature (`sig`),
so a fetch that returned a clamped edge frame instead of the real neighbour shows up as a repeated signature.
