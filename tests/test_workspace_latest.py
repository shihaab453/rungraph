"""Latest-step metric selection for workspace comparison surfaces."""

from __future__ import annotations

import json
from pathlib import Path

from runevidence.rungraph_cli import main


def manifest(results: list[tuple[str, float]]) -> dict:
    return {
        "schema_version": "0.11",
        "project": {"id": "p", "name": "p"},
        "runs": [{"id": "r"}],
        "results": [
            {"id": f"res-{index}", "run_id": "r", "name": name, "value": value}
            for index, (name, value) in enumerate(results)
        ],
    }


def make_root(root: Path) -> Path:
    left = root / "a"
    left.mkdir(parents=True)
    (left / "runevidence.json").write_text(
        json.dumps(
            manifest(
                [
                    ("loss @ 5", 0.9),
                    ("loss @ 10", 0.5),
                    ("accuracy @ 3", 0.7),
                ]
            )
        ),
        encoding="utf-8",
    )
    right = root / "b"
    right.mkdir()
    (right / "runevidence.json").write_text(
        json.dumps(manifest([("loss @ 20", 0.4), ("accuracy @ 3", 0.8)])),
        encoding="utf-8",
    )
    assert main(["init", str(root), "--id", "study", "--name", "Study"]) == 0
    return root


def test_latest_selects_the_highest_step_per_run(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    capsys.readouterr()

    assert main(["compare", str(root), "--metric", "loss"]) == 0
    exact = json.loads(capsys.readouterr().out)
    assert exact["rows"] == []

    assert main(["compare", str(root), "--metric", "loss", "--latest"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["latest"] is True
    assert {row["value"] for row in data["rows"]} == {0.5, 0.4}
    assert {row["step"] for row in data["rows"]} == {10, 20}
    assert {row["name"] for row in data["rows"]} == {"loss"}


def test_latest_matrix_uses_base_names_and_steps(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    capsys.readouterr()

    assert main(["matrix", str(root), "--latest"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["latest"] is True
    assert data["metrics"] == ["accuracy", "loss"]
    losses = [cell for cell in data["cells"] if cell["metric"] == "loss"]
    assert {cell["value"] for cell in losses} == {0.4, 0.5}
    assert {cell["step"] for cell in losses} == {10, 20}


def test_latest_leaderboard_ranks_the_selected_values(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    capsys.readouterr()

    assert main(
        ["leaderboard", str(root), "--metric", "loss", "--direction", "min", "--latest"]
    ) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["latest"] is True
    assert [row["value"] for row in data["rows"]] == [0.4, 0.5]
