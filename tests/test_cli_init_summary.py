"""The init summary names the results the adapter already recorded."""

from __future__ import annotations

from pathlib import Path

from runevidence.cli import main


def test_init_summary_includes_the_result_count(tmp_path: Path, capsys):
    (tmp_path / "data.txt").write_text("x\n", encoding="utf-8")
    assert main(["init", str(tmp_path), "--id", "demo", "--name", "Demo"]) == 0
    out = capsys.readouterr().out
    assert "0 runs, 1 artifacts, 0 results" in out
