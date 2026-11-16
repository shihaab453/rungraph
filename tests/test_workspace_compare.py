"""Per-pair comparability in the workspace comparison surface."""

from __future__ import annotations

import json
from pathlib import Path

from runevidence.rungraph_cli import main

CONDITIONS = {
    "evaluation_split": "test",
    "evaluation_protocol": {"protocol_id": "protocol-1", "version": 1},
    "evaluation_dataset_artifact_id": "data",
    "model_checkpoint_artifact_id": "ckpt",
    "source_revision": "rev-1",
    "environment": {"python": "3.12"},
}


def manifest(tag: str, split: str = "test") -> dict:
    conditions = {**CONDITIONS, "evaluation_split": split}
    run_id = f"r-{tag}"
    return {
        "schema_version": "0.2",
        "project": {"id": "p", "name": "p"},
        "runs": [{"id": run_id, "conditions": conditions}],
        "artifacts": [
            {"id": "data", "path": "data.csv", "sha256": "0" * 64, "size_bytes": 1, "kind": "dataset",
                "dataset_id": "ds-shared"},
            {"id": "ckpt", "path": "model.bin", "sha256": "1" * 64, "size_bytes": 1, "kind": "checkpoint"},
        ],
        "results": [
            {
                "id": f"acc-{tag}",
                "run_id": run_id,
                "name": "accuracy",
                "value": 0.9,
                "metric_definition": {"definition_id": "acc-v1"},
            }
        ],
        "relationships": [
            {"source": "data", "target": run_id, "type": "input_to"},
            {"source": "ckpt", "target": run_id, "type": "input_to"},
        ],
    }


def make_root(root: Path, target_split: str = "test") -> Path:
    for name, split in (("a", "test"), ("b", target_split)):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "runevidence.json").write_text(json.dumps(manifest(name, split)), encoding="utf-8")
    assert main(["init", str(root), "--id", "study", "--name", "Study"]) == 0
    return root


def test_compare_reports_comparable_pairs(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    capsys.readouterr()
    assert main(["compare", str(root), "--metric", "accuracy", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["comparability"][0]["conditions"] == "comparable"


def test_compare_reports_not_comparable_pairs(tmp_path: Path, capsys):
    root = make_root(tmp_path, target_split="validation")
    capsys.readouterr()
    assert main(["compare", str(root), "--metric", "accuracy", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["comparability"][0]["conditions"] == "not-comparable"


def test_matrix_and_leaderboard_state_conditions_available(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    capsys.readouterr()
    assert main(["matrix", str(root)]) == 0
    matrix = json.loads(capsys.readouterr().out)
    assert matrix["exit_code"] == 0
    assert main(["leaderboard", str(root), "--metric", "accuracy", "--direction", "max"]) == 0
    board = json.loads(capsys.readouterr().out)
    assert board["conditions"] == "available"
