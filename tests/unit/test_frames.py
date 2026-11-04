import json
import subprocess
import sys

import numpy as np

from test_lock import shot


def test_analyse_frames_returns_full_frame_path(tmp_path):
    """The OFX plugin's entry point: raw luma on stdin -> JSON path in full-frame pixels (here 4x the analysis size)."""
    g, shifts = shot(n=40)
    out = tmp_path / "r.json"
    subprocess.run([sys.executable, "-m", "gigsteady.cli", "analyse-frames", "--frames", "40", "--size", "960x540",
                    "--full", "3840x2160", "--mode", "lock", "--json", str(out)], input=g.tobytes(), check=True)
    r = json.loads(out.read_text())
    p = np.array(r["p"])
    assert r["mode"] == "lock" and r["width"] == 3840 and p.shape == (40, 4) and r["zoom"] >= 1
    # the correction undoes the shake: frame -> reference, so it is minus the shift (up to a constant), scaled by 4
    err = p[:, :2] + 4 * shifts[:, :2]
    assert np.abs(err - np.median(err, 0)).max() < 4 * 0.3
