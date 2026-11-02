"""Frame-exact ranged decode and ProRes 422 HQ encode, all through ffmpeg.

Seeking: `-ss` before `-i` is accurate in ffmpeg (it decodes from the previous keyframe and drops frames before the
target). The target is set a quarter frame *before* frame `start`, so rounding of the time can never skip it; the
frames before it are dropped by ffmpeg because their timestamps are earlier still. Tested against sequential decode
(tests/unit/test_io.py) at 25 and 29.97 fps, H.264 and HEVC.

Hardware decode (VideoToolbox) is used for HEVC only: it fails on Sony 4K H.264 ("hardware accelerator failed to
decode picture" on every frame), so H.264 decodes in software.
"""
import queue
import subprocess
import threading
from collections.abc import Iterator

import numpy as np

from .probe import Clip, frames_to_tc, probe, tc_to_frames


def hw_args(clip: Clip) -> list[str]:
    return ["-hwaccel", "videotoolbox"] if clip.codec == "hevc" else []


def seek_args(clip: Clip, start: int) -> list[str]:
    if start <= 0:
        return []
    t = (start - 0.25) / clip.fps
    return ["-ss", f"{float(t):.6f}"]


def _input(clip: Clip, start: int, n: int) -> list[str]:
    return ["ffmpeg", "-nostdin", "-v", "error", *hw_args(clip), *seek_args(clip, start), "-i", clip.path,
            "-map", "0:v:0", "-frames:v", str(n)]


def analysis_size(clip: Clip, width: int = 960) -> tuple[int, int]:
    h = int(round(clip.height * width / clip.width / 2)) * 2
    return width, h


def decode_gray(path: str, start: int, n: int, width: int = 960) -> np.ndarray:
    """Frames [start, start+n) as (n, h, w) uint8 luma at analysis size."""
    clip = probe(path)
    w, h = analysis_size(clip, width)
    raw = subprocess.run([*_input(clip, start, n), "-vf", f"scale={w}:{h},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w)


MATRICES = {"bt709": "bt709", "smpte170m": "smpte170m", "bt470bg": "bt470bg", "bt2020nc": "bt2020", "bt2020c": "bt2020"}


def rgb_in_filter(clip: Clip) -> str:
    """YUV -> 16-bit RGB with the clip's own matrix and range, stated explicitly: left to itself the converter can
    use BT.601 on one side and the tag on the other (a measured 3.6-code luma / 2.6-code V shift on Canon HEVC)."""
    m = MATRICES.get(clip.color_space or "", "bt709" if clip.height >= 720 else "smpte170m")
    r = "pc" if clip.color_range == "pc" else "tv"
    return f"scale=in_color_matrix={m}:in_range={r},format=rgb48le"


def read_rgb48(path: str, start: int, n: int, prefetch: int = 4) -> Iterator[np.ndarray]:
    """Frames [start, start+n) as (H, W, 3) uint16 RGB at full size, decoded on a background thread.
    Raises if the decoder delivers fewer than n frames."""
    clip = probe(path)
    size = clip.width * clip.height * 6
    dec = subprocess.Popen([*_input(clip, start, n), "-vf", rgb_in_filter(clip), "-f", "rawvideo", "-"],
                           stdout=subprocess.PIPE)
    q: queue.Queue = queue.Queue(maxsize=prefetch)

    def reader():
        try:
            for j in range(n):
                b = dec.stdout.read(size)
                if len(b) < size:
                    q.put(RuntimeError(f"decoder ended at frame {j} of {n} ({path} @ {start})"))
                    return
                q.put(np.frombuffer(b, np.uint16).reshape(clip.height, clip.width, 3))
            q.put(None)
        finally:
            dec.stdout.close()

    threading.Thread(target=reader, daemon=True).start()
    try:
        while (f := q.get()) is not None:
            if isinstance(f, Exception):
                raise f
            yield f
    finally:
        dec.kill() if dec.poll() is None else None
        dec.wait()


class ProResWriter:
    """ProRes 422 HQ (VideoToolbox), 10-bit 4:2:2 BT.709 video range (tagged), fed 16-bit RGB on a background thread.
    Writes to `out` directly; callers write to a .partial name and rename on success."""

    def __init__(self, out: str, width: int, height: int, fps, timecode: str | None = None, prefetch: int = 4,
                 encoder: str = "prores_videotoolbox"):
        fps_s = f"{fps.numerator}/{fps.denominator}" if hasattr(fps, "numerator") else str(fps)
        tc = ["-timecode", timecode] if timecode else []
        prof = ["-profile:v", "hq"] if encoder == "prores_videotoolbox" else ["-profile:v", "3"]
        self.proc = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb48le", "-s", f"{width}x{height}",
             "-r", fps_s, "-i", "-", "-vf", "scale=out_color_matrix=bt709:out_range=tv,format="
             + ("p210le" if encoder == "prores_videotoolbox" else "yuv422p10le"), "-c:v", encoder, *prof,
             "-colorspace", "bt709", "-color_range", "tv", *tc, "-y", out],
            stdin=subprocess.PIPE)
        self.q: queue.Queue = queue.Queue(maxsize=prefetch)
        self.err: Exception | None = None
        self.t = threading.Thread(target=self._run)
        self.t.start()
        self.count = 0

    def _run(self):
        while (f := self.q.get()) is not None:
            try:
                self.proc.stdin.write(np.ascontiguousarray(f, np.uint16).tobytes())
            except Exception as e:  # keep draining so put() never blocks forever
                self.err = e

    def write(self, frame: np.ndarray) -> None:
        if self.err:
            raise self.err
        self.q.put(frame)
        self.count += 1

    def close(self) -> None:
        self.q.put(None)
        self.t.join()
        self.proc.stdin.close()
        rc = self.proc.wait()
        if self.err or rc != 0:
            raise RuntimeError(f"encoder failed (rc={rc}): {self.err}")


def output_timecode(clip: Clip, first_frame: int) -> str | None:
    """Timecode of source frame `first_frame` (the first frame written), from the clip's start timecode."""
    if not clip.timecode:
        return None
    return frames_to_tc(tc_to_frames(clip.timecode, clip.fps) + first_frame, clip.fps)
