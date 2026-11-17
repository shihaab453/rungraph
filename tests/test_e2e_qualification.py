"""End-to-end lifecycle: record, verify, compose, compare, corrupt, propagate, accept."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from runevidence.cli import main
from runevidence.rungraph_cli import main as rungraph_main


def write_metrics(path: Path, accuracy: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"eval": {"accuracy": accuracy}}, indent=2) + "\n", encoding="utf-8")


def author(result_file: Path, payload: dict) -> None:
    result_file.write_text(json.dumps(payload), encoding="utf-8")


def init_member(folder: Path, name: str, accuracy: float) -> tuple[str, str, str, str]:
    """Record a fresh experiment, one result and one claim; return its ids."""
    write_metrics(folder / "runs" / name / "metrics.json", accuracy)
    assert main(["init", str(folder), "--adapter", "generic", "--name", name, "--id", name]) == 0
    manifest = json.loads((folder / "runevidence.json").read_text(encoding="utf-8"))
    run_id = manifest["runs"][0]["id"]
    artifact_id = next(
        item["id"]
        for item in manifest["artifacts"]
        if item["path"] == f"runs/{name}/metrics.json"
    )
    result_id = f"{name}-accuracy"
    result_file = folder / "result.json"
    author(
        result_file,
        {
            "id": result_id,
            "run_id": run_id,
            "name": "test_accuracy",
            "value": accuracy,
            "artifact_id": artifact_id,
            "locator": "/eval/accuracy",
        },
    )
    assert main(["result", "add", str(folder), "--file", str(result_file)]) == 0
    claim_id = f"claim-{name}"
    author(
        result_file,
        {"id": claim_id, "claim": f"{name} reports accuracy {accuracy}", "supports": [result_id], "contradicts": []},
    )
    assert main(["evidence", "add", str(folder), "--file", str(result_file)]) == 0
    result_file.unlink()
    return run_id, result_id, claim_id, artifact_id


def test_full_lifecycle_records_composes_compares_and_propagates(tmp_path: Path, capsys):
    root = tmp_path / "study"
    baseline = root / "baseline"
    tuned = root / "tuned"
    init_member(baseline, "baseline", 0.5)
    _, _, _, tuned_artifact = init_member(tuned, "tuned", 0.9)
    capsys.readouterr()

    assert main(["validate", str(baseline), "--strict"]) == 0
    assert main(["validate", str(tuned), "--strict"]) == 0

    assert rungraph_main(["init", str(root), "--id", "study", "--name", "Study"]) == 0
    document = json.loads((root / "rungraph.json").read_text(encoding="utf-8"))
    members = {member["path"]: member["id"] for member in document["members"]}
    tuned_member = members["tuned"]
    workspace_claim = {
        "id": "tuned-higher",
        "claim": "The tuned run reports higher test accuracy.",
        "supports": [{"member": tuned_member, "id": "tuned-accuracy"}],
        "contradicts": [],
        "source": "README.md",
    }
    claim_file = tmp_path / "claim.json"
    claim_file.write_text(json.dumps(workspace_claim), encoding="utf-8")
    current = hashlib.sha256((root / "rungraph.json").read_bytes()).hexdigest()
    assert rungraph_main(
        ["claim", "add", str(root), "--file", str(claim_file), "--expected-sha256", current]
    ) == 0
    assert rungraph_main(["validate", str(root), "--strict"]) == 0
    capsys.readouterr()

    assert rungraph_main(["compare", str(root), "--metric", "test_accuracy", "--json"]) == 0
    comparison = json.loads(capsys.readouterr().out)
    assert comparison["format"] == "rungraph-compare"
    assert {row["value"] for row in comparison["rows"]} == {0.5, 0.9}

    write_metrics(tuned / "runs" / "tuned" / "metrics.json", 0.2)
    assert main(["validate", str(tuned), "--strict"]) == 1
    findings = capsys.readouterr().out
    assert "result-value-mismatch" in findings
    assert "claim-at-risk" in findings

    assert rungraph_main(["validate", str(root), "--strict"]) == 1
    capsys.readouterr()
    assert rungraph_main(["report", str(root), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    labels = {claim["id"]: claim["labels"] for claim in report["claims"]}
    assert "at-risk" in labels["claim-tuned"]
    assert "supported" not in labels["claim-tuned"]
    assert "at-risk" in labels["tuned-higher"]

    assert rungraph_main(
        ["impact", str(root), "--from", f"result:{tuned_member}#tuned-accuracy"]
    ) == 0
    affected = json.loads(capsys.readouterr().out)
    assert "tuned-higher" in [claim["id"] for claim in affected["claims"]]

    assert main(["rehash", str(tuned), tuned_artifact]) == 0
    assert main(["validate", str(tuned), "--strict"]) == 0
    assert rungraph_main(["validate", str(root), "--strict"]) == 0
    capsys.readouterr()

    assert rungraph_main(["compare", str(root), "--metric", "test_accuracy", "--json"]) == 0
    comparison = json.loads(capsys.readouterr().out)
    assert {row["value"] for row in comparison["rows"]} == {0.5, 0.2}


def test_negative_paths_refuse_without_writing(tmp_path: Path, capsys):
    root = tmp_path / "study"
    init_member(root / "baseline", "baseline", 0.5)
    init_member(root / "tuned", "tuned", 0.9)
    assert rungraph_main(["init", str(root), "--id", "study", "--name", "Study"]) == 0
    capsys.readouterr()
    before = (root / "rungraph.json").read_bytes()

    bad_patch = tmp_path / "patch.json"
    bad_patch.write_text(
        json.dumps({"format": "rungraph-patch", "version": "0.1", "operations": [{"op": "add-member"}]}),
        encoding="utf-8",
    )
    current = hashlib.sha256(before).hexdigest()
    assert rungraph_main(
        ["patch", str(root), "--file", str(bad_patch), "--expected-sha256", current]
    ) == 2
    captured = capsys.readouterr()
    assert "needs 'member'" in captured.out
    assert "Traceback" not in captured.err
    assert (root / "rungraph.json").read_bytes() == before

    assert main(["result", "remove", str(root / "tuned"), "--id", "tuned-accuracy"]) == 2
    assert "still cited" in capsys.readouterr().out


def test_a_multi_adapter_workspace_verifies_and_propagates(tmp_path: Path, capsys):
    pytest.importorskip("runevidence_sacred")
    pytest.importorskip("runevidence_lightning")
    from runevidence.checks.files import _dir_fd_supported

    if not _dir_fd_supported():
        pytest.skip("Lightning needs POSIX directory-descriptor reads")
    root = tmp_path / "study"
    sacred = root / "sacred-run"
    lightning = root / "lightning-run"
    sacred_run = sacred / "1"
    sacred_run.mkdir(parents=True)
    (sacred_run / "run.json").write_text(
        json.dumps(
            {
                "experiment": {"name": "sacred", "id": 4},
                "status": "COMPLETED",
                "start_time": "2026-11-17T10:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (sacred_run / "config.json").write_text(
        json.dumps({"learning_rate": 0.1}), encoding="utf-8"
    )
    (sacred_run / "metrics.json").write_text(
        json.dumps({"loss": {"values": [0.5], "steps": [1], "timestamps": []}}),
        encoding="utf-8",
    )
    version = lightning / "version_0"
    version.mkdir(parents=True)
    (version / "metrics.csv").write_text(
        "step,loss,accuracy\n1,0.4,0.8\n", encoding="utf-8", newline=""
    )

    assert main(["init", str(sacred), "--adapter", "sacred", "--name", "sacred", "--id", "sacred"]) == 0
    assert main(["init", str(lightning), "--adapter", "lightning", "--name", "lightning", "--id", "lightning"]) == 0
    assert main(["validate", str(sacred), "--strict"]) == 0
    assert main(["validate", str(lightning), "--strict"]) == 0

    assert rungraph_main(["init", str(root), "--id", "study", "--name", "Study"]) == 0
    assert rungraph_main(["validate", str(root), "--strict"]) == 0
    capsys.readouterr()

    assert rungraph_main(["compare", str(root), "--metric", "loss @ 1", "--json"]) == 0
    comparison = json.loads(capsys.readouterr().out)
    assert len(comparison["rows"]) == 2
    assert {row["value"] for row in comparison["rows"]} == {0.4, 0.5}

    document = json.loads((root / "rungraph.json").read_text(encoding="utf-8"))
    sacred_member = next(
        member["id"] for member in document["members"] if member["path"] == "sacred-run"
    )
    sacred_manifest = json.loads((sacred / "runevidence.json").read_text(encoding="utf-8"))
    sacred_result = next(
        item["id"] for item in sacred_manifest["results"] if item["name"] == "loss @ 1"
    )
    claim = {
        "id": "study-loss",
        "claim": "Both tools report loss at step 1.",
        "supports": [{"member": sacred_member, "id": sacred_result}],
        "contradicts": [],
    }
    claim_file = tmp_path / "claim.json"
    claim_file.write_text(json.dumps(claim), encoding="utf-8")
    current = hashlib.sha256((root / "rungraph.json").read_bytes()).hexdigest()
    assert rungraph_main(
        ["claim", "add", str(root), "--file", str(claim_file), "--expected-sha256", current]
    ) == 0
    assert rungraph_main(["validate", str(root), "--strict"]) == 0
    capsys.readouterr()

    metrics_path = sacred_run / "metrics.json"
    document = json.loads(metrics_path.read_text(encoding="utf-8"))
    document["loss"]["values"][0] = 0.9
    metrics_path.write_text(json.dumps(document), encoding="utf-8")

    assert main(["validate", str(sacred)]) == 1
    capsys.readouterr()
    assert rungraph_main(["validate", str(root)]) == 1
    capsys.readouterr()
    assert rungraph_main(["report", str(root), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    labels = {item["id"]: item["labels"] for item in report["claims"]}
    assert "at-risk" in labels["study-loss"]
    assert "supported" not in labels["study-loss"]
