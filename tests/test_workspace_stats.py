"""Per-metric statistics in the workspace comparison surface."""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from runevidence.rungraph_cli import main


def manifest(results: list[dict]) -> dict:
    return {
        "schema_version": "0.11",
        "project": {"id": "p", "name": "p"},
        "runs": [{"id": "r"}],
        "results": [
            {"run_id": "r", **result, "id": f"res-{index}"}
            for index, result in enumerate(results)
        ],
    }


def make_root(root: Path) -> Path:
    left = root / "a"
    left.mkdir(parents=True)
    (left / "runevidence.json").write_text(
        json.dumps(
            manifest(
                [
                    {"name": "accuracy", "value": 0.5},
                    {"name": "loss", "value": None},
                    {"name": "note", "value": "high"},
                    {"name": "buckets", "value": {"kind": "tf2-histogram", "buckets": []}},
                ]
            )
        ),
        encoding="utf-8",
    )
    right = root / "b"
    right.mkdir()
    (right / "runevidence.json").write_text(
        json.dumps(
            manifest(
                [
                    {"name": "accuracy", "value": 0.9},
                    {"name": "accuracy", "value": 0.7},
                    {"name": "loss", "value": 0.2},
                    {"name": "flag", "value": True},
                    {"name": "quality", "value": None},
                ]
            )
        ),
        encoding="utf-8",
    )
    assert main(["init", str(root), "--id", "study", "--name", "Study"]) == 0
    return root


def test_stats_reports_counts_and_distribution(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    capsys.readouterr()

    assert main(["stats", str(root), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)

    assert data["format"] == "rungraph-stats"
    assert data["version"] == "0.1"
    assert data["metric"] is None
    entries = {entry["name"]: entry for entry in data["metrics"]}
    assert list(entries) == ["accuracy", "buckets", "flag", "loss", "note", "quality"]

    accuracy = entries["accuracy"]
    assert accuracy["rows"] == 3
    assert accuracy["numeric"] == 3
    assert accuracy["missing"] == 0
    assert accuracy["non_numeric"] == 0
    assert accuracy["ambiguous"] == 1
    assert accuracy["min"] == 0.5
    assert accuracy["max"] == 0.9
    assert accuracy["mean"] == statistics.fmean([0.5, 0.9, 0.7])
    assert accuracy["median"] == statistics.median([0.5, 0.9, 0.7])
    assert accuracy["stddev"] == statistics.pstdev([0.5, 0.9, 0.7])

    assert entries["loss"]["rows"] == 2
    assert entries["loss"]["numeric"] == 1
    assert entries["loss"]["missing"] == 1
    assert entries["loss"]["mean"] == 0.2

    assert entries["note"]["non_numeric"] == 1
    assert entries["flag"]["non_numeric"] == 1
    assert entries["buckets"]["non_numeric"] == 1

    quality = entries["quality"]
    assert quality["numeric"] == 0
    assert quality["missing"] == 1
    assert quality["min"] is None and quality["max"] is None
    assert quality["mean"] is None and quality["median"] is None
    assert quality["stddev"] is None


def test_stats_filters_one_metric(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    capsys.readouterr()

    assert main(["stats", str(root), "--metric", "loss", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["metric"] == "loss"
    assert [entry["name"] for entry in data["metrics"]] == ["loss"]


def test_stats_returns_no_entries_for_an_unknown_metric(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    capsys.readouterr()

    assert main(["stats", str(root), "--metric", "absent"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["metrics"] == []
    assert data["exit_code"] == 0


def test_stats_is_json_only_and_reports_workspace_errors(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    capsys.readouterr()
    assert main(["stats", str(root)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["format"] == "rungraph-stats"

    (root / "rungraph.json").unlink()
    assert main(["stats", str(root)]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["format"] == "rungraph-error"
    assert error["command"] == "stats"
