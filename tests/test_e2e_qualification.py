"""End-to-end lifecycle: record, verify, analyse, change, propagate, accept, revalidate."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from runevidence.cli import main
from runevidence.rungraph_cli import main as rungraph_main

REPO = Path(__file__).resolve().parents[1]


def test_single_experiment_lifecycle(tmp_path: Path, capsys):
    root = tmp_path / "experiment"
    shutil.copytree(REPO / "examples/generic-experiment", root)
    assert main(["validate", str(root), "--strict"]) == 0
    capsys.readouterr()

    metrics = root / "runs/tuned/metrics.json"
    document = json.loads(metrics.read_text(encoding="utf-8"))
    document["eval"]["accuracy"] = 0.5
    metrics.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    assert main(["validate", str(root), "--strict"]) == 1
    out = capsys.readouterr().out
    assert "result-value-mismatch" in out
    assert "claim-at-risk" in out

    assert main(["rehash", str(root), "tuned-metrics"]) == 0
    assert main(["validate", str(root), "--strict"]) == 0

    assert main(["claim", str(root), "--id", "claim-tuned-perfect"]) == 0
    assert main(["lineage", str(root), "--id", "claim-tuning-helps"]) == 0
    assert main(["impact", str(root), "--id", "tuned-metrics"]) == 0


def test_workspace_lifecycle(tmp_path: Path, capsys):
    root = tmp_path / "study"
    shutil.copytree(REPO / "examples/workspace-study", root)
    assert rungraph_main(["validate", str(root), "--strict"]) == 0
    capsys.readouterr()

    metrics = root / "tuned/metrics.json"
    metrics.write_text(json.dumps({"accuracy": 0.2}) + "\n", encoding="utf-8")
    assert rungraph_main(["validate", str(root), "--strict"]) == 1
    out = capsys.readouterr().out
    assert "tuned" in out
    assert "at-risk" in out or "supported" in out

    report = rungraph_main(["report", str(root), "--json"])
    assert report == 1
    document = json.loads(capsys.readouterr().out)
    assert document["format"] == "rungraph-report"

    assert main(["rehash", str(root / "tuned"), "tuned-metrics"]) == 0
    assert rungraph_main(["validate", str(root), "--strict"]) == 0
