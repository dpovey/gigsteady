"""Rule (c) end to end on a delivered render: song 1, measured from the pixels, fails at record 38900 only.
Set GIGSTEADY_BOUNDARY_RENDER to that render (tests/fixtures/boundaries/song1.json describes it)."""
import json
import os
from pathlib import Path

import pytest

from gigsteady.eval import boundaries as bd

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "boundaries" / "song1.json"
RENDER = Path(os.environ.get("GIGSTEADY_BOUNDARY_RENDER", "/nonexistent-set-GIGSTEADY_BOUNDARY_RENDER"))


@pytest.mark.footage
@pytest.mark.skipif(not RENDER.exists(), reason="set GIGSTEADY_BOUNDARY_RENDER to the song 1 delivery render")
def test_song1_delivery_fails_only_at_the_through_edit():
    d = json.loads(FIX.read_text())
    pairs = bd.measure_pairs(str(RENDER))
    assert len(pairs) == d["frames"] - 1
    rows = bd.check(pairs, d["record_in"], d["overlays"])
    assert [r["record"] for r in rows if r["fail"]] == [38900]
