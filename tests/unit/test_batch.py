import json

from gigsteady.batch import run_batch
from gigsteady.pipeline import Options


def test_batch_resume_and_option_change(coded_clips, tmp_path):
    shots = tmp_path / "shots.json"
    shots.write_text(json.dumps([{"name": "a", "src": coded_clips["h264_25"], "in": 40, "out": 60}]))
    job = tmp_path / "job"
    opt = Options(handles=5, fuse=0, gpu=False)
    r1 = run_batch(shots, job, opt, jobs=1)
    assert r1[0]["status"] == "ok" and r1[0]["frames"] == 30 and r1[0]["handle_in"] == 5
    assert (job / "outputs/a.mov").exists() and not (job / "outputs/a.partial.mov").exists()
    assert run_batch(shots, job, opt, jobs=1)[0]["status"] == "skipped"
    (job / "outputs/a.partial.mov").write_bytes(b"junk")              # a killed earlier run
    r3 = run_batch(shots, job, Options(handles=5, fuse=0, gpu=False, crop_budget=1.05), jobs=1)
    assert r3[0]["status"] == "ok" and not (job / "outputs/a.partial.mov").exists()


def test_handles_clamp_at_file_start(coded_clips, tmp_path):
    shots = tmp_path / "shots.json"
    shots.write_text(json.dumps([{"name": "b", "src": coded_clips["h264_25"], "in": 3, "out": 20}]))
    r = run_batch(shots, tmp_path / "job", Options(handles=10, fuse=0, gpu=False), jobs=1, render=False)
    assert r[0]["handle_in"] == 3 and r[0]["handle_out"] == 10
