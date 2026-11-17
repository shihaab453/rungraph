"""Latest-step selection for single-manifest comparison routes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runevidence.cli import main


def manifest() -> dict:
    return {
        "schema_version": "0.1",
        "project": {"id": "p", "name": "p"},
        "runs": [{"id": "r1"}, {"id": "r2"}],
        "results": [
            {"id": "a10", "run_id": "r1", "name": "loss @ 10", "value": 0.5},
            {"id": "a20", "run_id": "r2", "name": "loss @ 20", "value": 0.4},
            {"id": "acc1", "run_id": "r1", "name": "accuracy @ 3", "value": 0.7},
            {"id": "acc2", "run_id": "r2", "name": "accuracy @ 3", "value": 0.8},
        ],
    }


def make_root(tmp_path: Path) -> Path:
    (tmp_path / "runevidence.json").write_text(json.dumps(manifest()), encoding="utf-8")
    return tmp_path


def test_compare_latest_matches_step_families(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    assert main(
        ["compare", str(root), "--base", "r1", "--target", "r2", "--latest", "--json"]
    ) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["latest"] is True
    row = next(item for item in data["results"] if item["name"] == "loss")
    assert row["baseline"] == 0.5
    assert row["target"] == 0.4
    assert row["delta"] == pytest.approx(-0.1)

    assert main(["compare", str(root), "--base", "r1", "--target", "r2", "--json"]) == 0
    exact = json.loads(capsys.readouterr().out)
    names = {item["name"] for item in exact["results"]}
    assert "loss" not in names


def test_matrix_latest_uses_base_names(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    assert main(["matrix", str(root), "--latest", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["latest"] is True
    assert [row["metric"] for row in data["runs"][0]["values"]] == ["accuracy", "loss"]


def test_leaderboard_latest_ranks_base_metric(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    assert main(
        ["leaderboard", str(root), "--metric", "loss", "--direction", "min", "--latest", "--json"]
    ) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["latest"] is True
    assert data["metric"] == "loss"
    assert [row["value"] for row in data["ranked"]] == [0.4, 0.5]
