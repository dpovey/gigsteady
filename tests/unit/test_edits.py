"""Rules (a) and (b): through-edits are one shot; overlay zoom = editor/stab, never under the pan/tilt cover."""
import math

import pytest

from gigsteady.edits import (Item, apply_through_edits, continues, cover_zoom, crop_matches, item_from_dict,
                             matched_zoom, overlay_zoom, through_edits)
from gigsteady.resolve.place import compose

SRC = "camA/C5691.MP4"


def item(rs, re_, src_in, angle="CAM A", src=SRC, dz=False):
    return Item(record_start=rs, record_end=re_, angle=angle, src=src, src_in=src_in, dynamic_zoom=dz)


def shot(it, name=None):
    return {"name": name or f"A_rec{it.record_start}", "src": it.src, "in": it.src_in, "out": it.src_in + it.frames,
            "record_start": it.record_start, "record_end": it.record_end, "angle": it.angle, "item_id": f"id{it.record_start}"}


# a delivered cut around 38084-39277 (record - source = 6179 on C5691), with a CAM D cut between two through-edits
TE1 = [item(38084, 38127, 31905), item(38127, 38271, 31948)]
CAMD = item(38271, 38376, 31473, angle="CAM D", src="camD/C3777.MP4")
TE2 = [item(38828, 38900, 32649, dz=True), item(38900, 39277, 32721)]
ITEMS = TE1 + [CAMD] + TE2


def test_continuity_needs_same_angle_file_and_running_source():
    a, b = TE1
    assert continues(a, b)
    assert continues(a, item(38127, 38271, 31949))                      # one frame of API rounding slack
    assert not continues(a, item(38127, 38271, 31950))                   # a jump in the source: a real cut
    assert not continues(a, item(38127, 38271, 31948, src="camA/C5692.MP4"))
    assert not continues(a, item(38127, 38271, 31948, angle="CAM B"))
    assert not continues(a, item(38128, 38271, 31948))                   # not adjacent
    assert not continues(a, item(38127, 38271, None))


def test_through_edit_chains():
    groups = through_edits(ITEMS)
    assert [[i.record_start for i in g] for g in groups] == [[38084, 38127], [38828, 38900]]
    three = [item(0, 10, 100), item(10, 20, 110), item(20, 30, 120)]
    assert [len(g) for g in through_edits(three)] == [3]
    assert through_edits([]) == []


def test_all_parts_stabilisable_become_one_shot_placed_part_by_part():
    shots = [shot(i) for i in TE1]
    out, dec = apply_through_edits(shots, TE1)
    assert len(out) == 1
    m = out[0]
    assert (m["in"], m["out"], m["record_start"], m["record_end"]) == (31905, 31905 + 187, 38084, 38271)
    assert [(p["record_start"], p["record_end"], p["offset"], p["item_id"]) for p in m["parts"]] == \
        [(38084, 38127, 0, "id38084"), (38127, 38271, 43, "id38127")]
    assert m["name"] == "A_rec38084_te"
    assert dec == [{"chain": [(38084, 38127), (38127, 38271)], "action": "merged", "name": "A_rec38084_te",
                    "editor_parts": []}]


def test_a_dynamic_zoom_part_means_neither_by_default():
    # song 1 at 3:02: the DZ side was skipped and the other stabilised -> x1.10 zoom jump. Now neither is stabilised.
    shots = [shot(TE2[1])]
    out, dec = apply_through_edits(shots, ITEMS)
    assert out == []
    assert dec[0]["action"] == "dropped" and "38828 Dynamic Zoom" in dec[0]["why"]


def test_render_all_keeps_one_path_and_leaves_the_dz_part_to_the_editor():
    shots = [shot(i) for i in TE2]
    out, dec = apply_through_edits(shots, ITEMS, policy="render-all")
    assert len(out) == 1 and [p.get("place") for p in out[0]["parts"]] == ["editor", None]
    assert dec[0]["editor_parts"] == [38828]


def test_an_unselected_partner_blocks_the_take_and_others_pass_through():
    lone = item(40534, 40604, 34355)
    shots = [shot(TE1[0]), shot(lone)]                                   # 38127 was not selected (held)
    out, dec = apply_through_edits(shots, TE1 + [lone])
    assert [s["record_start"] for s in out] == [40534]
    assert "38127 not selected" in dec[0]["why"]
    with pytest.raises(ValueError):
        apply_through_edits(shots, TE1, policy="both")


def test_overlay_zoom_is_editor_over_stab_never_under_the_cover():
    assert overlay_zoom(1.0, 1.10) == 1.0                                # never zooms out past the frame
    assert overlay_zoom(1.5, 1.10) == pytest.approx(1.3636, abs=1e-4)
    assert overlay_zoom(1.2, 1.0959) == pytest.approx(1.09499, abs=1e-4)
    # A_rec37286: editor 1.5, pan -928 px on a 3840 timeline -> the cover wins (1.485 was set by hand)
    assert overlay_zoom(1.5, 1.10, pan=-928.2) == pytest.approx(1.4834, abs=1e-4)
    assert cover_zoom(tilt=-186.7) == pytest.approx(1.1729, abs=1e-4)
    assert cover_zoom(pan=100, width=1920) == pytest.approx(1.1042, abs=1e-4)
    with pytest.raises(ValueError):
        overlay_zoom(1.0, 0)


def test_compose_applies_the_rule_with_the_angle_transform():
    tr = compose({"zoom": [1.05, 1.05], "rotation": 0.5}, {"ZoomX": 1.5, "ZoomY": 1.5, "Pan": 10.0}, stab_zoom=1.10)
    assert tr["ZoomX"] == pytest.approx(1.575 / 1.10, abs=1e-6) and tr["RotationAngle"] == 0.5 and tr["Pan"] == 10.0
    assert compose({}, {"ZoomX": 1.0, "ZoomY": 1.0}, stab_zoom=1.10)["ZoomX"] == 1.0
    assert compose({}, {"ZoomX": 1.2, "ZoomY": 1.2})["ZoomX"] == 1.2     # no stab zoom given: as before


def test_crop_matches_list_each_bare_neighbour_once():
    shots = [shot(TE2[1])]                                               # built without rule (a)
    assert crop_matches(shots, ITEMS) == [{"record_start": 38828, "record_end": 38900, "angle": "CAM A",
                                           "overlay": "A_rec38900", "overlay_record_start": 38900,
                                           "overlay_record_end": 39277, "dynamic_zoom": True}]
    merged, _ = apply_through_edits([shot(i) for i in TE1], ITEMS)
    assert crop_matches(merged, ITEMS) == []                             # both parts covered: nothing to match
    # A, B (Dynamic Zoom), C under render-all: B touches two overlay parts but is matched once (not x1.21)
    chain = [item(0, 10, 100), item(10, 20, 110, dz=True), item(20, 30, 120)]
    merged, _ = apply_through_edits([shot(i) for i in chain], chain, policy="render-all")
    assert [c["record_start"] for c in crop_matches(merged, chain)] == [10]


def test_matched_zoom_shows_the_overlays_crop_only_when_needed():
    # editor 1.0 both sides, overlay clamped at 1.0 over a 1.10 crop: the bare side goes to 1.10
    assert matched_zoom(1.0, 1.0, 1.0, 1.10) == pytest.approx(1.10)
    # editor 1.5 both sides: the overlay is placed at 1.5/1.1 and already shows 1.5 -> no change (not 1.65)
    assert matched_zoom(1.5, 1.5, overlay_zoom(1.5, 1.10), 1.10) is None
    # pan cover pushed the overlay past the editor's framing: the bare side follows it
    assert matched_zoom(1.5, 1.5, 1.485, 1.10) == pytest.approx(1.6335)
    assert matched_zoom(1.0, 1.2, 1.0, 1.10) is None                     # the editor changed framing on purpose
    assert matched_zoom(1.0, 1.0, 1.0, 1.10, angle_zoom=1.05) == pytest.approx(1.10 / 1.05)


def test_cover_zoom_includes_rotation():
    assert cover_zoom(rotation=0.5) == pytest.approx(math.cos(math.radians(0.5)) + 16 / 9 * math.sin(math.radians(0.5)))
    assert cover_zoom(rotation=-0.5) == pytest.approx(cover_zoom(rotation=0.5)) == pytest.approx(cover_zoom(rotation=179.5))
    assert cover_zoom(rotation=0.5, pan=192) == pytest.approx(cover_zoom(rotation=0.5) + 0.1)
    # the angle's 0.5 deg rotation used to be covered by its 1.05 zoom; editor/stab alone (0.955) would show corners
    tr = compose({"zoom": [1.05, 1.05], "rotation": 0.5}, {"ZoomX": 1.0, "ZoomY": 1.0}, stab_zoom=1.10)
    assert tr["ZoomX"] == pytest.approx(cover_zoom(rotation=0.5), abs=1e-6) and tr["ZoomX"] > 1.0


def test_item_from_dict_reads_both_list_formats():
    a = item_from_dict({"record_in": 38828, "record_out": 38900, "angle": "CAM A", "angle_file_path": SRC,
                        "angle_source_frame": 32649, "dynamic_zoom": True})
    b = item_from_dict(shot(TE2[1]))
    assert continues(a, b) and a.dynamic_zoom
