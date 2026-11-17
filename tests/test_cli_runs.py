"""Single-manifest run inventory."""

from __future__ import annotations

import json
from pathlib import Path

from runevidence.cli import main


def make_root(tmp_path: Path) -> Path:
    document = {
        "schema_version": "0.2",
        "project": {"id": "p", "name": "p"},
        "runs": [
            {
                "id": "r1",
                "name": "Baseline",
                "started_at": "2026-11-17T10:00:00+00:00",
                "params": {"lr": 0.1},
                "conditions": {"evaluation_split": "test"},
                "metadata": {"note": "x"},
            },
            {"id": "r2", "name": "Tuned", "params": {"lr": 0.01}},
        ],
        "artifacts": [
            {"id": "data", "path": "data.csv", "sha256": "0" * 64, "size_bytes": 1, "run_id": "r1"}
        ],
        "results": [
            {"id": "acc", "run_id": "r1", "name": "accuracy", "value": 0.9}
        ],
    }
    (tmp_path / "data.csv").write_bytes(b"x")
    (tmp_path / "runevidence.json").write_text(json.dumps(document), encoding="utf-8")
    return tmp_path


def test_runs_lists_params_conditions_and_counts(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    assert main(["runs", str(root), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["format"] == "runevidence-runs-v1"
    rows = {row["run"]: row for row in data["runs"]}
    assert rows["r1"]["counts"] == {"artifacts": 1, "results": 1}
    assert rows["r1"]["conditions"] == {"evaluation_split": "test"}
    assert "metadata" not in rows["r1"]
    assert rows["r2"]["started_at"] is None


def test_runs_metadata_and_text(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    assert main(["runs", str(root), "--metadata", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["runs"][0]["metadata"] == {"note": "x"}

    assert main(["runs", str(root)]) == 0
    out = capsys.readouterr().out
    assert "Runs (2):" in out
    assert "r1 (Baseline): 1 artifacts, 1 results" in out
    assert '"lr": 0.1' in out
    assert "evaluation_split" in out


def test_runs_summaries_are_whitelisted(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    document = json.loads((root / "runevidence.json").read_text(encoding="utf-8"))
    document["runs"][0]["metadata"] = {
        "sacred": {"status": "COMPLETED", "command": "train", "private": "no"},
        "other": {"secret": "no"},
    }
    (root / "runevidence.json").write_text(json.dumps(document), encoding="utf-8")

    assert main(["runs", str(root), "--summaries", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["runs"][0]["summaries"] == {
        "sacred": {"status": "COMPLETED", "command": "train"}
    }
