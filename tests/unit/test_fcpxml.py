from gigsteady.resolve.fcpxml import Project

XML = """<?xml version="1.0"?><fcpxml version="1.10"><resources>
<format id="r0" frameDuration="1/25s" width="3840" height="2160"/>
<asset id="r1" name="a.MP4" start="100/1s" duration="1000/1s"><media-rep kind="original-media" src="file:///Volumes/X%20Y/a.MP4"/></asset>
<asset id="r2" name="b.MP4" start="0/1s" duration="1000/1s"><media-rep kind="original-media" src="file:///b.MP4"/></asset>
<media id="r9" name="MC"><multicam format="r0" tcStart="3180/1s">
 <mc-angle name="Desk" angleID="A"><gap offset="0/1s" duration="10/25s" start="3600/1s"/>
  <clip offset="10/25s" name="a.MP4" duration="100/25s" start="2600/25s"><adjust-transform scale="1.05 1.05" rotation="0.7"/>
   <video offset="0/1s" ref="r1" duration="100/25s" start="2600/25s"/></clip></mc-angle>
 <mc-angle name="Pit" angleID="B"><asset-clip ref="r2" offset="0/1s" name="b.MP4" duration="200/25s" start="5/25s"/></mc-angle>
</multicam></media></resources>
<library><event><project name="p"><sequence format="r0" tcStart="3180/1s"><spine>
 <mc-clip ref="r9" offset="3180/1s" start="79520/25s" duration="30/25s"><mc-source angleID="A" srcEnable="video"/></mc-clip>
 <mc-clip ref="r9" offset="79530/25s" start="79540/25s" duration="20/25s"><mc-source angleID="B" srcEnable="video"/></mc-clip>
</spine></sequence></project></event></library></fcpxml>"""


def test_resolve_multicam_items(tmp_path):
    p = tmp_path / "x.fcpxml"
    p.write_text(XML)
    P = Project(str(p))
    r = P.resolve(79500 + 5)  # first item, multicam frame 20 + 5 = 25, desk clip from 10, source start 104 s - 100 s
    assert r["angle"] == "Desk" and r["path"] == "/Volumes/X Y/a.MP4"
    assert r["src_frame"] == (104 - 100) * 25 + 25 - 10
    assert r["angle_transform"] == {"zoom": (1.05, 1.05), "rotation": 0.7}
    assert (r["record_start"], r["record_end"]) == (79500, 79530)
    r = P.resolve(79530)  # second item: multicam frame 40, pit asset-clip at 0 with start 5 frames
    assert r["angle"] == "Pit" and r["src_frame"] == 45
    assert P.resolve(79550) is None
