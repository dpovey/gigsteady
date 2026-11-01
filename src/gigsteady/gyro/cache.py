"""Per-file cache of a camera file's motion metadata, filled in 2-minute chunks by rust/gs-gyro.

gs-gyro parses only the rtmd samples in the range it is given (patched telemetry-parser, rust/README), so memory
is bounded: ~1 GB peak on the 100-min FX30 file (fixed header/footer buffers), where a whole-file parse peaked at
24 GB. Each chunk is dumped with 2 s of overlap and trimmed to its own span, so chunks join without duplicates.
A shot only fills the chunks it needs. Chunk files: <cache>/<stem>-<size>-<mtime>/<k>.{imu,ibis,frames}
(f64 rows; layouts in rust/gs-gyro/src/main.rs).
"""
import os
import subprocess
from pathlib import Path

import numpy as np

CHUNK_MS = 120_000
PAD_MS = 2_000
COLS = {"imu": 7, "ibis": 6, "frames": 4}
REPO = Path(__file__).resolve().parents[3]


def gs_gyro() -> Path:
    p = Path(os.environ.get("GIGSTEADY_GS_GYRO", REPO / "rust/gs-gyro/target/release/gs-gyro"))
    if not p.exists():
        raise FileNotFoundError(f"{p} not built: cargo build --release in rust/gs-gyro")
    return p


def cache_dir(src: str) -> Path:
    st = os.stat(src)
    root = Path(os.environ.get("GIGSTEADY_CACHE", Path.home() / "Library/Caches/gigsteady"))
    return root / "gyro" / f"{Path(src).stem}-{st.st_size}-{int(st.st_mtime)}"


def _ensure_chunk(src: str, d: Path, k: int) -> None:
    if all((d / f"{k}.{e}").exists() for e in COLS):
        return
    d.mkdir(parents=True, exist_ok=True)
    t0, t1 = k * CHUNK_MS, (k + 1) * CHUNK_MS
    tmp = d / f"{k}.tmp"
    subprocess.run([str(gs_gyro()), src, str(tmp), "--range", str(max(0, t0 - PAD_MS)), str(t1 + PAD_MS)],
                   check=True, capture_output=True)
    for e, c in COLS.items():
        a = np.fromfile(f"{tmp}.{e}", "<f8").reshape(-1, c)
        a = a[(a[:, 0] >= t0) & (a[:, 0] < t1)]
        a.astype("<f8").tofile(d / f"{k}.{e}.part")
        os.replace(d / f"{k}.{e}.part", d / f"{k}.{e}")
        os.unlink(f"{tmp}.{e}")


def load(src: str, t0_ms: float, t1_ms: float) -> dict[str, np.ndarray]:
    """IMU, IBIS and frame rows with timestamps in [t0_ms, t1_ms), dumping missing chunks first."""
    d = cache_dir(src)
    ks = range(int(max(0, t0_ms) // CHUNK_MS), int(t1_ms // CHUNK_MS) + 1)
    for k in ks:
        _ensure_chunk(src, d, k)
    out = {}
    for e, c in COLS.items():
        a = np.concatenate([np.fromfile(d / f"{k}.{e}", "<f8").reshape(-1, c) for k in ks])
        out[e] = a[(a[:, 0] >= t0_ms) & (a[:, 0] < t1_ms)]
    return out
