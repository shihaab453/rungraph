"""Reference and relationship checks: ids, edges, provenance and claims."""

from __future__ import annotations

import json
from pathlib import Path

from runevidence.checks import run_checks
from runevidence.findings import Code, Severity
from runevidence.manifest import load_manifest

MANIFEST = "runevidence.json"


def read_doc(root: Path) -> dict:
    return json.loads((root / MANIFEST).read_text(encoding="utf-8"))


def write_doc(root: Path, doc: dict) -> None:
    (root / MANIFEST).write_text(
        json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def add_relationship(doc: dict, source: str, target: str, rel_type: str) -> None:
    doc["relationships"].append(
        {"source": source, "target": target, "type": rel_type, "note": None}
    )


def check(root: Path):
    return run_checks(load_manifest(root))


def codes(report) -> list[str]:
    return [f.code.value for f in report.findings]


def only(report, code: Code):
    matches = [f for f in report.findings if f.code is code]
    assert len(matches) == 1, f"expected exactly one {code.value}, got {codes(report)}"
    return matches[0]


def test_duplicate_id_is_an_error(experiment):
    doc = read_doc(experiment)
    doc["results"].append(
        {"id": "train-data", "run_id": "run-baseline", "name": "extra", "value": 0.1}
    )
    write_doc(experiment, doc)

    finding = only(check(experiment), Code.duplicate_id)

    assert finding.severity is Severity.error
    assert finding.subject == "train-data"
    assert "already used by an artifact" in finding.message


def test_dangling_reference_from_evidence_supports(experiment):
    doc = read_doc(experiment)
    doc["evidence"][0]["supports"] = ["ghost-result"]
    write_doc(experiment, doc)

    finding = only(check(experiment), Code.dangling_reference)

    assert finding.severity is Severity.error
    assert finding.subject == "claim-tuned-perfect"
    assert "'ghost-result'" in finding.message


def test_dangling_reference_from_artifact_run_id(experiment):
    doc = read_doc(experiment)
    doc["artifacts"][0]["run_id"] = "run-missing"
    write_doc(experiment, doc)

    finding = only(check(experiment), Code.dangling_reference)

    assert finding.severity is Severity.error
    assert finding.subject == "train-data"
    assert "run-missing" in finding.message


def test_produced_by_from_run_to_artifact_is_invalid(experiment):
    doc = read_doc(experiment)
    add_relationship(doc, "run-baseline", "baseline-config", "produced_by")
    write_doc(experiment, doc)

    finding = only(check(experiment), Code.invalid_relationship)

    assert finding.severity is Severity.error
    assert finding.subject == "run-baseline"
    assert "produced_by" in finding.message
    assert "cannot link a run" in finding.message


def test_input_to_from_result_to_run_is_invalid(experiment):
    doc = read_doc(experiment)
    add_relationship(doc, "baseline-accuracy", "run-baseline", "input_to")
    write_doc(experiment, doc)

    finding = only(check(experiment), Code.invalid_relationship)

    assert finding.severity is Severity.error
    assert finding.subject == "baseline-accuracy"
    assert "input_to" in finding.message


def test_artifact_attributed_to_two_runs_is_conflicting_provenance(experiment):
    doc = read_doc(experiment)
    add_relationship(doc, "baseline-config", "run-tuned", "produced_by")
    write_doc(experiment, doc)

    finding = only(check(experiment), Code.conflicting_provenance)

    assert finding.severity is Severity.error
    assert finding.subject == "baseline-config"
    assert "several runs" in finding.message


def test_result_reported_in_another_runs_file_is_a_warning(experiment):
    doc = read_doc(experiment)
    doc["results"][0]["artifact_id"] = "tuned-metrics"
    doc["results"][0]["locator"] = None
    write_doc(experiment, doc)

    finding = only(check(experiment), Code.conflicting_provenance)

    assert finding.severity is Severity.warning
    assert finding.subject == "baseline-accuracy"
    assert "run-baseline" in finding.message
    assert "run-tuned" in finding.message


def test_one_claim_cannot_support_and_contradict_the_same_id(experiment):
    doc = read_doc(experiment)
    doc["evidence"][0]["contradicts"] = ["tuned-accuracy"]
    write_doc(experiment, doc)

    finding = only(check(experiment), Code.conflicting_evidence)

    assert finding.severity is Severity.error
    assert finding.subject == "claim-tuned-perfect"
    assert "both supporting and contradicting" in finding.message


def test_same_run_and_metric_with_two_values_conflicts(experiment):
    doc = read_doc(experiment)
    doc["results"].append(
        {"id": "dup-tuned", "run_id": "run-tuned", "name": "test_accuracy", "value": 0.9}
    )
    write_doc(experiment, doc)

    finding = only(check(experiment), Code.conflicting_evidence)

    assert finding.severity is Severity.error
    assert finding.subject == "dup-tuned"
    assert "different values" in finding.message


def test_derived_from_cycle_is_an_error(experiment):
    doc = read_doc(experiment)
    add_relationship(doc, "run-baseline", "run-tuned", "derived_from")
    write_doc(experiment, doc)

    finding = only(check(experiment), Code.derivation_cycle)

    assert finding.severity is Severity.error
    assert "cycle" in finding.message


def test_claim_citing_nothing_is_a_warning(experiment):
    doc = read_doc(experiment)
    doc["evidence"][0]["supports"] = []
    doc["evidence"][0]["contradicts"] = []
    write_doc(experiment, doc)

    finding = only(check(experiment), Code.unsupported_claim)

    assert finding.severity is Severity.warning
    assert finding.subject == "claim-tuned-perfect"
    assert "cites nothing" in finding.message


def test_claim_whose_supports_are_all_unknown_is_an_error(experiment):
    doc = read_doc(experiment)
    doc["evidence"][0]["supports"] = ["ghost-result"]
    write_doc(experiment, doc)

    report = check(experiment)
    matching = [f for f in report.findings if f.code is Code.unsupported_claim]

    assert len(matching) == 1
    assert matching[0].severity is Severity.error
    assert matching[0].subject == "claim-tuned-perfect"
    assert "no valid support" in matching[0].message


def test_claim_at_risk_when_supporting_metrics_file_is_deleted(experiment):
    (experiment / "runs" / "tuned" / "metrics.json").unlink()

    report = check(experiment)
    at_risk = [f for f in report.findings if f.code is Code.claim_at_risk]

    assert at_risk, f"expected claim-at-risk, got {codes(report)}"
    assert {f.subject for f in at_risk} >= {"claim-tuned-perfect", "claim-tuning-helps"}
    assert all(f.severity is Severity.error for f in at_risk)
    imperfect = next(f for f in at_risk if f.subject == "claim-tuned-perfect")
    assert "tuned-metrics" in imperfect.message


def test_claim_at_risk_when_an_input_dataset_is_deleted(experiment):
    (experiment / "data" / "test.csv").unlink()

    report = check(experiment)
    at_risk = [f for f in report.findings if f.code is Code.claim_at_risk]

    assert at_risk, f"expected claim-at-risk, got {codes(report)}"
    perfect = next(f for f in at_risk if f.subject == "claim-tuned-perfect")
    assert "test-data" in perfect.message
    assert perfect.severity is Severity.error


def test_claim_is_not_at_risk_when_an_unrelated_file_breaks(experiment):
    (experiment / "REPORT.md").unlink()

    report = check(experiment)

    assert codes(report) == ["missing-file"]
    assert Code.claim_at_risk not in {f.code for f in report.findings}
