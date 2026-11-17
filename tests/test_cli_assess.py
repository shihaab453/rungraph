"""Evidence-aware comparison (conditions plus artifact/value integrity)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from runevidence.cli import main


def _artifact(rel: str, data: bytes, kind: str, **extra) -> dict:
    return {
        "id": extra.pop("id", rel.split(".")[0]),
        "path": rel,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "kind": kind,
        **extra,
    }


def make_member(
    root: Path, tag: str, split: str = "test", *, locator: bool = True
) -> Path:
    folder = root / tag
    folder.mkdir(parents=True)
    data = b"dataset\n"
    checkpoint = b"model\n"
    metrics = b'{"accuracy": 0.9}\n'
    (folder / "data.csv").write_bytes(data)
    (folder / "model.bin").write_bytes(checkpoint)
    (folder / "metrics.json").write_bytes(metrics)
    run_id = f"run-{tag}"
    document = {
        "schema_version": "0.2",
        "project": {"id": "p", "name": "p"},
        "runs": [
            {
                "id": run_id,
                "conditions": {
                    "evaluation_split": split,
                    "evaluation_protocol": {"protocol_id": "protocol-1", "version": 1},
                    "evaluation_dataset_artifact_id": "data",
                    "model_checkpoint_artifact_id": "checkpoint",
                    "source_revision": "rev-1",
                    "environment": {"python": "3.12"},
                },
            }
        ],
        "artifacts": [
            _artifact("data.csv", data, "dataset", id="data", dataset_id="ds-shared"),
            _artifact("model.bin", checkpoint, "checkpoint", id="checkpoint"),
            _artifact("metrics.json", metrics, "metrics", id="metrics"),
        ],
        "results": [
            {
                "id": f"acc-{tag}",
                "run_id": run_id,
                "name": "accuracy",
                "value": 0.9,
                "metric_definition": {"definition_id": "acc-v1"},
                **(
                    {"locator": "/accuracy", "artifact_id": "metrics"}
                    if locator
                    else {}
                ),
            }
        ],
        "relationships": [
            {"source": "data", "target": run_id, "type": "input_to"},
            {"source": "checkpoint", "target": run_id, "type": "input_to"},
        ],
    }
    (folder / "runevidence.json").write_text(json.dumps(document), encoding="utf-8")
    return folder


def run_assess(baseline: Path, target: Path, capsys, *extra: str) -> tuple[int, str]:
    code = main(
        [
            "assess",
            str(baseline),
            str(target),
            "--baseline-result",
            "acc-base",
            "--target-result",
            "acc-target",
            *extra,
        ]
    )
    return code, capsys.readouterr().out


def test_clean_and_comparable_is_permitted(tmp_path: Path, capsys):
    baseline = make_member(tmp_path, "base")
    target = make_member(tmp_path, "target")
    code, out = run_assess(baseline, target, capsys)
    assert code == 0
    assert "Recorded conditions: comparable" in out
    assert "Assessment: PERMITTED" in out


def test_changed_evidence_is_not_permitted(tmp_path: Path, capsys):
    baseline = make_member(tmp_path, "base")
    target = make_member(tmp_path, "target")
    (target / "data.csv").write_bytes(b"changed\n")
    code, out = run_assess(baseline, target, capsys)
    assert code == 1
    assert "Target evidence: 1 errors" in out
    assert "Assessment: NOT PERMITTED" in out


def test_differing_conditions_are_not_permitted(tmp_path: Path, capsys):
    baseline = make_member(tmp_path, "base", split="test")
    target = make_member(tmp_path, "target", split="validation")
    code, out = run_assess(baseline, target, capsys)
    assert code == 1
    assert "Recorded conditions: not-comparable" in out
    assert "Assessment: NOT PERMITTED" in out


def test_json_envelope(tmp_path: Path, capsys):
    baseline = make_member(tmp_path, "base")
    target = make_member(tmp_path, "target")
    code, out = run_assess(baseline, target, capsys, "--json")
    data = json.loads(out)
    assert code == 0
    assert data["format"] == "runevidence-assess-v1"
    assert data["conditions"] == "comparable"
    assert data["conditions_error"] is None
    assert data["results"]["baseline"]["state"] == "verified"
    assert data["results"]["target"]["state"] == "verified"
    assert data["values_verified"] is True
    assert data["permitted"] is True
    assert data["evidence"]["baseline"]["intact"] is True


def test_locatorless_selected_values_are_not_permitted(tmp_path: Path, capsys):
    baseline = make_member(tmp_path, "base", locator=False)
    target = make_member(tmp_path, "target", locator=False)
    code, out = run_assess(baseline, target, capsys, "--json")
    data = json.loads(out)
    assert code == 1
    assert data["values_verified"] is False
    assert data["results"]["baseline"] == {
        "id": "acc-base",
        "state": "unverified",
        "reason": "no-source-locator",
    }
    assert data["results"]["target"]["state"] == "unverified"
    assert data["permitted"] is False
    assert data["evidence"]["baseline"]["intact"] is True


def test_one_unverified_side_blocks_permission(tmp_path: Path, capsys):
    baseline = make_member(tmp_path, "base")
    target = make_member(tmp_path, "target", locator=False)
    code, out = run_assess(baseline, target, capsys)
    assert code == 1
    assert "Selected result values: baseline verified (locator), target unverified (no-source-locator)" in out
    assert "Assessment: NOT PERMITTED" in out


def test_a_missing_selected_result_is_not_permitted(tmp_path: Path, capsys):
    baseline = make_member(tmp_path, "base")
    target = make_member(tmp_path, "target")
    code = main(
        [
            "assess",
            str(baseline),
            str(target),
            "--baseline-result",
            "acc-base",
            "--target-result",
            "absent",
            "--json",
        ]
    )
    data = json.loads(capsys.readouterr().out)
    assert code == 1
    assert data["results"]["target"]["state"] == "missing"
    assert data["conditions"] == "unavailable"
    assert data["conditions_error"] == "MAPPING_FAILED"
    assert data["permitted"] is False


def test_a_changed_selected_value_is_failed(tmp_path: Path, capsys):
    baseline = make_member(tmp_path, "base")
    target = make_member(tmp_path, "target")
    (target / "metrics.json").write_bytes(b'{"accuracy": 0.4}\n')
    code, out = run_assess(baseline, target, capsys, "--json")
    data = json.loads(out)
    assert code == 1
    assert data["results"]["target"]["state"] == "failed"
    assert data["results"]["target"]["reason"] in {
        "result-value-mismatch",
        "hash-mismatch",
    }
    assert data["permitted"] is False


def test_an_unreadable_source_is_unverified(tmp_path: Path, capsys):
    baseline = make_member(tmp_path, "base")
    target = make_member(tmp_path, "target")
    (target / "metrics.json").unlink()
    code, out = run_assess(baseline, target, capsys, "--json")
    data = json.loads(out)
    assert code == 1
    assert data["results"]["target"]["state"] == "unverified"
    assert data["results"]["target"]["reason"] == "missing-file"
    assert data["evidence"]["target"]["intact"] is False


def test_an_unrelated_warning_does_not_block_verified_values(tmp_path: Path, capsys):
    baseline = make_member(tmp_path, "base")
    target = make_member(tmp_path, "target")
    (target / "notes.txt").write_text("note", encoding="utf-8")
    document = json.loads((target / "runevidence.json").read_text(encoding="utf-8"))
    document["artifacts"].append({"id": "notes", "path": "notes.txt"})
    (target / "runevidence.json").write_text(json.dumps(document), encoding="utf-8")

    code, out = run_assess(baseline, target, capsys, "--json")
    data = json.loads(out)
    assert code == 0
    assert data["evidence"]["target"]["warnings"] >= 1
    assert data["results"]["target"]["state"] == "verified"
    assert data["permitted"] is True


def test_unsupported_verification_is_unverified(tmp_path: Path, capsys):
    baseline = make_member(tmp_path, "base")
    target = make_member(tmp_path, "target")
    document = json.loads((target / "runevidence.json").read_text(encoding="utf-8"))
    document["results"][0]["artifact_id"] = "data"
    (target / "runevidence.json").write_text(json.dumps(document), encoding="utf-8")

    code, out = run_assess(baseline, target, capsys, "--json")
    data = json.loads(out)
    assert code == 1
    assert data["results"]["target"]["state"] == "unverified"
    assert data["results"]["target"]["reason"] == "result-unverifiable"
    assert data["permitted"] is False


def test_an_unexpected_comparability_failure_is_named(tmp_path: Path, monkeypatch):
    from runevidence import run_assess

    baseline = make_member(tmp_path, "base")
    target = make_member(tmp_path, "target")

    def explode(*args, **kwargs):
        raise RuntimeError("simulated internal failure")

    monkeypatch.setattr(run_assess.run_comparability, "compare_paths", explode)
    outcome = run_assess.assess(
        baseline,
        target,
        baseline_result_id="acc-base",
        target_result_id="acc-target",
    )
    assert outcome.exit_code == 1
    assert outcome.document["conditions"] == "unavailable"
    assert outcome.document["conditions_error"] == "INTERNAL_ERROR"
    assert outcome.document["permitted"] is False
    assert "Recorded conditions: unavailable (INTERNAL_ERROR)" in outcome.text


def test_a_known_comparability_error_code_is_preserved(tmp_path: Path, monkeypatch):
    from runevidence import run_assess

    baseline = make_member(tmp_path, "base")
    target = make_member(tmp_path, "target")

    class Outcome:
        exit_code = 2
        document = {"error": {"code": "UNSUPPORTED_VERSION"}}
        text = ""

    monkeypatch.setattr(
        run_assess.run_comparability, "compare_paths", lambda *a, **k: Outcome()
    )
    outcome = run_assess.assess(
        baseline,
        target,
        baseline_result_id="acc-base",
        target_result_id="acc-target",
    )
    assert outcome.document["conditions"] == "unavailable"
    assert outcome.document["conditions_error"] == "UNSUPPORTED_VERSION"
    assert outcome.exit_code == 1


def test_an_unreadable_source_is_never_verified(tmp_path: Path, capsys):
    import os

    if os.name != "posix":
        pytest.skip("permission bits are POSIX-only")
    baseline = make_member(tmp_path, "base")
    target = make_member(tmp_path, "target")
    metrics = target / "metrics.json"
    metrics.chmod(0)
    try:
        code, out = run_assess(baseline, target, capsys, "--json")
    finally:
        metrics.chmod(0o600)
    data = json.loads(out)
    assert code == 1
    assert data["results"]["target"]["state"] == "unverified"
    assert data["results"]["target"]["reason"] in {
        "locator-unreadable",
        "missing-file",
    }
    assert data["values_verified"] is False


def test_a_changed_source_that_still_matches_is_not_verified(tmp_path: Path, capsys):
    baseline = make_member(tmp_path, "base")
    target = make_member(tmp_path, "target")
    (target / "metrics.json").write_bytes(b'{"accuracy": 0.9, "note": "x"}\n')

    code, out = run_assess(baseline, target, capsys, "--json")
    data = json.loads(out)
    assert code == 1
    assert data["results"]["target"]["state"] == "unverified"
    assert data["results"]["target"]["reason"] == "hash-mismatch"
    assert data["values_verified"] is False


def test_insufficient_conditions_are_not_an_internal_error(tmp_path: Path, capsys):
    baseline = make_member(tmp_path, "base")
    target = make_member(tmp_path, "target")
    path = target / "runevidence.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    del document["runs"][0]["conditions"]["evaluation_split"]
    path.write_text(json.dumps(document), encoding="utf-8")

    code, out = run_assess(baseline, target, capsys, "--json")
    data = json.loads(out)
    assert code == 1
    assert data["conditions"] == "unavailable"
    assert data["conditions_error"] == "INSUFFICIENT_EVIDENCE"
    assert data["conditions_error"] != "INTERNAL_ERROR"


def test_a_schema_0_3_unsupported_locator_is_unverified(tmp_path: Path, capsys):
    baseline = make_member(tmp_path, "base")
    target = make_member(tmp_path, "target")
    path = target / "runevidence.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["schema_version"] = "0.3"
    document["results"][0]["artifact_id"] = "data"
    path.write_text(json.dumps(document), encoding="utf-8")

    code, out = run_assess(baseline, target, capsys, "--json")
    data = json.loads(out)
    assert code == 1
    assert data["results"]["target"]["state"] == "unverified"
    assert data["results"]["target"]["reason"] == "locator-kind-mismatch"
    assert data["values_verified"] is False
