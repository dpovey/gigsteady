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
make                  # build/GigsteadyProbe.ofx.bundle (arm64, ad-hoc signed); build/hostcheck loads it
sudo make install     # /Library/OFX/Plugins/GigsteadyProbe.ofx.bundle, then restart Resolve
sudo make uninstall
```

In Resolve it appears under Effects > OpenFX > gigsteady > "gigsteady probe".

## Running the test

`run_probe_test.py` (system Python, Resolve running) builds a throwaway timeline "gigsteady OFX probe test" from a
shots list, applies the probe with `TimelineItem.AddEffect("ofx", ...)`, and queues one render job for it (started
by job id, never a bare `StartRendering()`). `summarise_log.py` turns the log into a table per clip.
