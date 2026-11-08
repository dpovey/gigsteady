"""Where the sample media lives on this machine. Nothing is assumed: set the variables or the
footage tests skip.

  GIGSTEADY_MEDIA_SYD26  the folder holding PHOTOPIT_ROAMING/, STATIC DESKCAM/ (Sydney 2026 cards)
  GIGSTEADY_MEDIA_BOTTB  the folder holding Footage/CAM A (Roaming)/ (Brisbane FX3)
  GIGSTEADY_DEV_DIR      scratch folder for rendered test output; default <GIGSTEADY_MEDIA_SYD26>/gigsteady/dev
                         (full-resolution renders stay off the boot disk), else the temp dir
  GIGSTEADY_PROTO        the pit-stabilise prototype data folder (gyro/, lockoff/)
"""
import os
import tempfile
from pathlib import Path

SYD26_PREFIX = "/Volumes/BOTTB SYD26"     # as written in the recorded shot lists
BOTTB_PREFIX = "/Volumes/BOTTB"


def _root(var):
    v = os.environ.get(var)
    return Path(v).expanduser() if v else None


def syd26(rel: str) -> Path:
    r = _root("GIGSTEADY_MEDIA_SYD26")
    return (r / rel) if r else Path("/nonexistent-set-GIGSTEADY_MEDIA_SYD26") / rel


def bottb(rel: str) -> Path:
    r = _root("GIGSTEADY_MEDIA_BOTTB")
    return (r / rel) if r else Path("/nonexistent-set-GIGSTEADY_MEDIA_BOTTB") / rel


def local(recorded: str) -> str:
    """Map a path as recorded in a fixture shot list to this machine's media root."""
    if recorded.startswith(SYD26_PREFIX + "/"):
        return str(syd26(recorded[len(SYD26_PREFIX) + 1:]))
    if recorded.startswith(BOTTB_PREFIX + "/"):
        return str(bottb(recorded[len(BOTTB_PREFIX) + 1:]))
    return recorded


def dev_dir(create: bool = True) -> Path:
    """Where footage tests write renders; created on first use, not at import."""
    d = _root("GIGSTEADY_DEV_DIR")
    if d is None:
        media = _root("GIGSTEADY_MEDIA_SYD26")
        d = media / "gigsteady" / "dev" if media else Path(tempfile.gettempdir()) / "gigsteady-dev"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def proto() -> Path:
    return _root("GIGSTEADY_PROTO") or Path("/nonexistent-set-GIGSTEADY_PROTO")
