"""Single-manifest per-metric statistics."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runevidence.cli import main


def manifest() -> dict:
    return {
        "schema_version": "0.11",
        "project": {"id": "p", "name": "p"},
        "runs": [{"id": "r1"}, {"id": "r2"}],
        "results": [
            {"id": "a1", "run_id": "r1", "name": "accuracy", "value": 0.5},
            {"id": "a2", "run_id": "r2", "name": "accuracy", "value": 0.9},
            {"id": "a3", "run_id": "r2", "name": "accuracy", "value": 0.7},
            {"id": "m1", "run_id": "r1", "name": "loss", "value": None},
            {"id": "n1", "run_id": "r1", "name": "note", "value": "high"},
            {"id": "h1", "run_id": "r1", "name": "buckets", "value": {"kind": "tf2-histogram", "buckets": []}},
        ],
    }


def make_root(tmp_path: Path) -> Path:
    (tmp_path / "runevidence.json").write_text(json.dumps(manifest()), encoding="utf-8")
    return tmp_path


def test_stats_text_and_json(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    assert main(["stats", str(root), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["format"] == "runevidence-stats-v1"
    entries = {entry["name"]: entry for entry in data["metrics"]}
    assert entries["accuracy"]["rows"] == 3
    assert entries["accuracy"]["numeric"] == 3
    assert entries["accuracy"]["ambiguous"] == 1
    assert entries["accuracy"]["mean"] == pytest.approx(0.7)
    assert entries["loss"]["missing"] == 1
    assert entries["note"]["non_numeric"] == 1
    assert entries["buckets"]["non_numeric"] == 1

    assert main(["stats", str(root), "--metric", "accuracy"]) == 0
    out = capsys.readouterr().out
    assert "Metrics:" in out
    assert "3 rows, 3 numeric" in out
    assert "stddev" in out


def test_stats_unknown_metric_and_empty_numeric(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    assert main(["stats", str(root), "--metric", "absent", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["metrics"] == []

    assert main(["stats", str(root), "--metric", "loss", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["metrics"][0]["numeric"] == 0
    assert data["metrics"][0]["mean"] is None
    assert data["metrics"][0]["stddev"] is None


def test_stats_loads_a_manifest_file_and_reports_load_errors(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    assert main(["stats", str(root / "runevidence.json"), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["metrics"]

    (root / "runevidence.json").unlink()
    assert main(["stats", str(root), "--json"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["ok"] is False
