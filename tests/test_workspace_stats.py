"""Per-metric statistics in the workspace comparison surface."""

from __future__ import annotations

import json
import json
import math
from pathlib import Path

import pytest

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
    assert accuracy["mean"] == pytest.approx(0.7)
    assert accuracy["median"] == pytest.approx(0.7)
    assert accuracy["stddev"] == pytest.approx(0.1632993161855452)

    assert entries["loss"]["rows"] == 2
    assert entries["loss"]["numeric"] == 1
    assert entries["loss"]["missing"] == 1
    assert entries["loss"]["mean"] == pytest.approx(0.2)

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


def stats_for(capsys, root: Path, results: list[dict]) -> dict:
    left = root / "a"
    left.mkdir(parents=True)
    (left / "runevidence.json").write_text(json.dumps(manifest(results)), encoding="utf-8")
    assert main(["init", str(root), "--id", "study", "--name", "Study"]) == 0
    capsys.readouterr()
    assert main(["stats", str(root), "--metric", "m", "--json"]) == 0
    raw = capsys.readouterr().out
    assert "Infinity" not in raw and "NaN" not in raw
    return json.loads(raw)["metrics"][0]


@pytest.mark.parametrize(
    ("values", "mean", "median", "stddev"),
    [
        ([1e308, 1e308], 1e308, 1e308, 0.0),
        ([9007199254740993, -9007199254740992], 0.5, 0.5, 9007199254740992.5),
        ([-1.7e308, 1e308, 1e308, 1e308], 3.25e307, 1e308, None),
        ([1e200, -1e200], 0.0, 0.0, 1e200),
        ([1e-200, -1e-200], 0.0, 0.0, 1e-200),
    ],
)
def test_extreme_values_stay_finite_and_version_independent(
    tmp_path: Path, capsys, values, mean, median, stddev
):
    entry = stats_for(capsys, tmp_path, [{"name": "m", "value": value} for value in values])

    assert entry["mean"] == pytest.approx(mean, rel=1e-12, abs=1e-300)
    assert entry["median"] == pytest.approx(median, rel=1e-12)
    if stddev is None:
        assert entry["stddev"] is not None and math.isfinite(entry["stddev"])
    else:
        assert entry["stddev"] == pytest.approx(stddev, rel=1e-12, abs=1e-300)


def test_integer_stddev_does_not_underflow(tmp_path: Path, capsys):
    entry = stats_for(
        capsys, tmp_path, [{"name": "m", "value": 10**200}, {"name": "m", "value": 10**200 + 1}]
    )
    assert entry["stddev"] == pytest.approx(0.5, rel=1e-12)


def test_float_stddev_keeps_small_differences(tmp_path: Path, capsys):
    entry = stats_for(
        capsys, tmp_path, [{"name": "m", "value": 1e16}, {"name": "m", "value": 1e16 + 2}]
    )
    assert entry["stddev"] == pytest.approx(1.0, rel=1e-12)
    assert entry["mean"] == pytest.approx(1e16, rel=1e-12)


def test_subnormal_median_does_not_underflow(tmp_path: Path, capsys):
    entry = stats_for(
        capsys, tmp_path, [{"name": "m", "value": 5e-324}, {"name": "m", "value": 5e-324}]
    )
    assert entry["median"] == 5e-324
    assert entry["mean"] == 5e-324
    assert entry["stddev"] == 0.0
