"""A closed output reader must not produce an unhandled traceback."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_validate_survives_a_closed_output_pipe(tmp_path: Path):
    artifacts = [
        {
            "id": f"missing-{index}",
            "path": f"missing/{index}.csv",
            "sha256": "0" * 64,
            "size_bytes": 1,
        }
        for index in range(2000)
    ]
    (tmp_path / "runevidence.json").write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "project": {"id": "p", "name": "p"},
                "artifacts": artifacts,
            }
        ),
        encoding="utf-8",
    )

    process = subprocess.Popen(
        [sys.executable, "-m", "runevidence.cli", "validate", str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None and process.stderr is not None
    assert process.stdout.readline()
    process.stdout.close()
    stderr = process.stderr.read()
    code = process.wait(timeout=30)

    assert code != 0
    assert b"BrokenPipeError" not in stderr
    assert b"Traceback (most recent call last)" not in stderr
