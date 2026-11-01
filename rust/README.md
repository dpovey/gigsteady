# rust/

- `gs-gyro/`: dumps Sony rtmd motion metadata (IMU, IBIS tables, frame timing) for a time range. Build with
  `cargo build --release -j 4` in `gs-gyro/`. gigsteady calls it through `src/gigsteady/gyro/cache.py`.
- `vendor/telemetry-parser/`: telemetry-parser (MIT OR Apache-2.0, by Adrian Eddy) at the revision in
  `UPSTREAM_REV`, with one patch, `telemetry-parser-time-range.patch`. It adds `InputOptions.time_range_ms`: the
  Sony MP4 parser skips rtmd samples outside the range before reading them. Without it the library keeps every
  frame's metadata, and the 100-min FX30 file peaked at 24 GB. With a range, the peak is ~1 GB, a fixed cost of the
  header/footer buffers it reads to find the boxes.

Checked (2026-11-01): a ranged dump of C5691 matches pit-stabilise's whole-file dump reading for reading (IMU,
IBIS, frames). On C1364 (FX30, 100 min) the readings are identical and the timestamps agree to 0.01 ms.
