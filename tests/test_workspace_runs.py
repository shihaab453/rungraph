"""The cross-member run inventory."""

from __future__ import annotations

import json
from pathlib import Path

from runevidence.rungraph_cli import main


def manifest(run: dict, *, artifacts: int = 0, results: int = 0) -> dict:
    return {
        "schema_version": "0.2",
        "project": {"id": "p", "name": "p"},
        "runs": [run],
        "artifacts": [
            {"id": f"art-{index}", "path": f"data-{index}.txt", "sha256": "0" * 64, "size_bytes": 0, "run_id": run["id"]}
            for index in range(artifacts)
        ],
        "results": [
            {"id": f"res-{index}", "run_id": run["id"], "name": f"metric-{index}", "value": index}
            for index in range(results)
        ],
    }


def make_root(root: Path) -> Path:
    conditions = {
        "evaluation_split": "test",
        "evaluation_protocol": {"protocol_id": "protocol-1", "version": 1},
        "evaluation_dataset_artifact_id": "art-0",
        "model_checkpoint_artifact_id": "art-1",
        "source_revision": "rev-1",
        "environment": {"python": "3.12"},
    }
    left = root / "a"
    left.mkdir(parents=True)
    (left / "runevidence.json").write_text(
        json.dumps(
            manifest(
                {
                    "id": "r1",
                    "name": "Baseline",
                    "started_at": "2026-11-17T10:00:00+00:00",
                    "params": {"lr": 0.1},
                    "conditions": conditions,
                },
                artifacts=2,
                results=2,
            )
        ),
        encoding="utf-8",
    )
    right = root / "b"
    right.mkdir()
    (right / "runevidence.json").write_text(
        json.dumps(
            manifest(
                {"id": "r2", "name": "Tuned", "params": {"lr": 0.01}, "metadata": {"note": "x"}},
                results=1,
            )
        ),
        encoding="utf-8",
    )
    assert main(["init", str(root), "--id", "study", "--name", "Study"]) == 0
    return root


def test_runs_lists_members_params_conditions_and_counts(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    capsys.readouterr()
    assert main(["runs", str(root), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["format"] == "rungraph-runs"
    assert data["metadata"] is False
    rows = {(row["member"], row["run"]): row for row in data["runs"]}
    assert set(rows) == {("prov-a", "r1"), ("prov-b", "r2")}
    assert rows[("prov-a", "r1")]["name"] == "Baseline"
    assert rows[("prov-a", "r1")]["params"] == {"lr": 0.1}
    assert rows[("prov-a", "r1")]["conditions"]["evaluation_split"] == "test"
    assert rows[("prov-a", "r1")]["started_at"] == "2026-11-17T10:00:00+00:00"
    assert rows[("prov-a", "r1")]["counts"] == {"artifacts": 2, "results": 2}
    assert "metadata" not in rows[("prov-b", "r2")]
    assert rows[("prov-b", "r2")]["conditions"] is None


def test_runs_metadata_and_member_filter(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    capsys.readouterr()
    assert main(["runs", str(root), "--metadata", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    row = next(item for item in data["runs"] if item["run"] == "r2")
    assert row["metadata"] == {"note": "x"}

    assert main(["runs", str(root), "--member", "prov-a", "--json"]) == 0
    filtered = json.loads(capsys.readouterr().out)
    assert [item["run"] for item in filtered["runs"]] == ["r1"]
    assert filtered["member"] == "prov-a"


def test_runs_text_and_unknown_member(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    capsys.readouterr()
    assert main(["runs", str(root)]) == 0
    out = capsys.readouterr().out
    assert "Runs (2):" in out
    assert "prov-a / r1 (Baseline): 2 artifacts, 2 results" in out
    assert '"lr": 0.1' in out
    assert "evaluation_split" in out
    assert "started:" in out

    assert main(["runs", str(root), "--member", "absent"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["format"] == "rungraph-error"
    assert error["command"] == "runs"
