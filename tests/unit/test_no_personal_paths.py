"""Shipped source (public repo) names no user home or personal volume."""
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SHIPPED = ("src", "scripts", "eval", "ofx", "rust")
SUFFIXES = (".py", ".rs", ".cpp", ".h", ".hpp", ".c", ".sh", ".toml", ".swift", ".m", ".mm", ".json")
PERSONAL = re.compile(r"/Users/|/Volumes/BOTTB|/private/tmp/claude")


def _in_git_checkout() -> bool:
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=REPO, capture_output=True, text=True)
    return r.returncode == 0 and Path(r.stdout.strip()).resolve() == REPO.resolve()


def test_no_personal_paths_in_shipped_source():
    # Production runs use a `git archive` copy of the code, which has no repository to list files from; the
    # check belongs to the checkout the code is committed from.
    if not _in_git_checkout():
        pytest.skip("not a git checkout (a git archive copy): shipped files cannot be listed")
    files = subprocess.run(["git", "ls-files", *SHIPPED], cwd=REPO, capture_output=True, text=True,
                           check=True).stdout.split("\n")
    bad = []
    for f in filter(lambda f: f.endswith(SUFFIXES), files):
        for n, line in enumerate((REPO / f).read_text(errors="replace").splitlines(), 1):
            if PERSONAL.search(line):
                bad.append(f"{f}:{n}")
    assert not bad, bad


def test_footage_tests_read_media_from_env(monkeypatch):
    import sys
    sys.path.insert(0, str(REPO / "tests/regress"))
    import media
    monkeypatch.setenv("GIGSTEADY_MEDIA_SYD26", "/x/syd")
    assert media.local("/Volumes/BOTTB SYD26/STATIC DESKCAM/a.MP4") == "/x/syd/STATIC DESKCAM/a.MP4"
    monkeypatch.delenv("GIGSTEADY_MEDIA_SYD26")
    assert not Path(media.local("/Volumes/BOTTB SYD26/a.MP4")).exists()


def test_dev_dir_defaults_to_the_media_drive_and_is_not_created_early(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "regress"))
    import media
    monkeypatch.delenv("GIGSTEADY_DEV_DIR", raising=False)
    monkeypatch.setenv("GIGSTEADY_MEDIA_SYD26", str(tmp_path))
    d = media.dev_dir(create=False)
    assert d == tmp_path / "gigsteady" / "dev" and not d.exists()
    monkeypatch.setenv("GIGSTEADY_DEV_DIR", str(tmp_path / "elsewhere"))
    assert media.dev_dir(create=False) == tmp_path / "elsewhere"
