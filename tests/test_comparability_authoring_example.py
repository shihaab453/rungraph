"""The documented schema 0.2 authoring example is executable.

Extracts the single bash block from ``docs/comparability-authoring-example.md``
and runs it in a scratch directory with this interpreter and ``src`` on the
path. No repository file is written.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOC = REPO / "docs" / "comparability-authoring-example.md"


@pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX-only evidence: the documented authoring example is a bash script",
)
def test_authoring_example_runs(tmp_path):
    text = DOC.read_text(encoding="utf-8")
    blocks = re.findall(r"```bash\n(.*?)```", text, re.S)
    assert len(blocks) == 1, "the authoring example must have exactly one bash block"

    script = tmp_path / "authoring-example.sh"
    script.write_text(blocks[0], encoding="utf-8", newline="\n")
    work = tmp_path / "work"
    env = {
        **os.environ,
        "PYTHON": sys.executable,
        "PYTHONPATH": str(REPO / "src"),
        "WORK": str(work),
    }
    completed = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert completed.stderr == ""
    assert "comparability preflight: ready" in completed.stdout
    assert "COMPARABLE_RECORDED_CONDITIONS" in completed.stdout
    assert (work / "baseline.json").is_file()
    assert (work / "target.json").is_file()
