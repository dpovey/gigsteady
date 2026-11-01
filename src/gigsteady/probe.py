"""What a clip is: frame rate (exact), size, codec, timecode, whether it carries Sony rtmd gyro."""
import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache


@dataclass(frozen=True)
class Clip:
    path: str
    width: int
    height: int
    fps: Fraction
    codec: str
    pix_fmt: str
    frames: int | None
    timecode: str | None
    has_rtmd: bool

    @property
    def scale(self) -> float:
        """Pixel thresholds in the prototype were set at 3840 wide; multiply by this for other widths."""
        return self.width / 3840


@lru_cache(maxsize=64)
def probe(path: str) -> Clip:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
                         capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    v = next(s for s in info["streams"] if s.get("codec_type") == "video")
    fps = Fraction(v.get("r_frame_rate") or v["avg_frame_rate"])
    tc = v.get("tags", {}).get("timecode") or info.get("format", {}).get("tags", {}).get("timecode")
    for s in info["streams"]:
        tc = tc or s.get("tags", {}).get("timecode")
    frames = int(v["nb_frames"]) if v.get("nb_frames", "").isdigit() else None
    if frames is None and "duration" in info.get("format", {}):
        frames = int(round(float(info["format"]["duration"]) * fps))
    rtmd = any(s.get("codec_tag_string") == "rtmd" for s in info["streams"])
    return Clip(path, int(v["width"]), int(v["height"]), fps, v["codec_name"], v.get("pix_fmt", ""), frames, tc, rtmd)


def tc_to_frames(tc: str, fps: Fraction) -> int:
    """Non-drop timecode -> frame count at the clip's nominal rate."""
    h, m, s, f = (int(x) for x in tc.replace(";", ":").split(":"))
    base = round(float(fps))
    return ((h * 60 + m) * 60 + s) * base + f


def frames_to_tc(n: int, fps: Fraction) -> str:
    base = round(float(fps))
    s, f = divmod(n, base)
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{f:02d}"
