"""aggiorna_youtube_stats.sh as launchd runs it: every step runs, but the exit code must tell the truth."""
import os
import shutil
import stat
import subprocess

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = "/usr/bin/python3"
pytestmark = pytest.mark.skipif(not os.path.exists(PY), reason="needs /usr/bin/python3")
STEPS = ["aggiorna_youtube_stats", "raccogli_metriche_video", "raccogli_finestre_fisse", "carica_bigquery"]


def build(tmp_path, failing=()):
    shutil.copy(os.path.join(HERE, "aggiorna_youtube_stats.sh"), tmp_path / "w.sh")
    os.chmod(tmp_path / "w.sh", os.stat(tmp_path / "w.sh").st_mode | stat.S_IEXEC)
    for s in STEPS:
        (tmp_path / f"{s}.py").write_text(
            f"import sys\nopen('calls.log', 'a').write('{s}\\n')\nsys.exit({1 if s in failing else 0})\n")


def run(tmp_path):
    r = subprocess.run(["/bin/bash", str(tmp_path / "w.sh")], capture_output=True, text=True)
    calls = (tmp_path / "calls.log").read_text().split() if (tmp_path / "calls.log").exists() else []
    return r.returncode, calls


def test_all_steps_ok_exits_zero(tmp_path):
    build(tmp_path)
    assert run(tmp_path) == (0, STEPS)


@pytest.mark.parametrize("failing", STEPS)
def test_one_failing_step_does_not_stop_the_others_but_the_exit_code_says_so(tmp_path, failing):
    build(tmp_path, failing=[failing])
    rc, calls = run(tmp_path)
    assert calls == STEPS and rc == 1
