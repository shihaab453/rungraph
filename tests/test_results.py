"""Result value checks: JSON Pointers, value comparison, unverifiable results."""

from __future__ import annotations

import json
from pathlib import Path

from runevidence.checks import run_checks
from runevidence.checks.results import _MISSING, resolve_pointer, values_match
from runevidence.findings import Code, Severity
from runevidence.manifest import load_manifest

MANIFEST = "runevidence.json"


def read_doc(root: Path) -> dict:
    return json.loads((root / MANIFEST).read_text(encoding="utf-8"))


def write_doc(root: Path, doc: dict) -> None:
    (root / MANIFEST).write_text(
        json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def check(root: Path):
    return run_checks(load_manifest(root))


def codes(report) -> list[str]:
    return [f.code.value for f in report.findings]


def only(report, code: Code):
    matches = [f for f in report.findings if f.code is code]
    assert len(matches) == 1, f"expected exactly one {code.value}, got {codes(report)}"
    return matches[0]


def test_edited_value_is_reported_as_a_mismatch(experiment):
    doc = read_doc(experiment)
    doc["results"][1]["value"] = 0.9
    write_doc(experiment, doc)

    report = check(experiment)
    finding = only(report, Code.result_value_mismatch)

    assert finding.severity is Severity.error
    assert finding.subject == "tuned-accuracy"
    assert "0.9" in finding.message
    assert "1.0" in finding.message
    assert Code.hash_mismatch not in {f.code for f in report.findings}


def test_float_tolerance_absorbs_representation_error():
    assert values_match(0.1 + 0.2, 0.3) is True
    assert values_match(0.3, 0.1 + 0.2) is True


def test_bools_are_never_equal_to_numbers():
    assert values_match(True, 1) is False
    assert values_match(1, True) is False
    assert values_match(False, 0) is False
    assert values_match(True, True) is True
    assert values_match(1, 1) is True


def test_resolve_pointer_handles_escaped_tokens():
    doc = {"a/b": 1, "a~b": 2, "nested": {"x/y": {"z": 3}}}

    assert resolve_pointer(doc, "/a~1b") == 1
    assert resolve_pointer(doc, "/a~0b") == 2
    assert resolve_pointer(doc, "/nested/x~1y/z") == 3


def test_resolve_pointer_handles_list_indices_and_missing_entries():
    doc = {"items": ["a", "b"], "nested": {"value": 7}}

    assert resolve_pointer(doc, "/items/0") == "a"
    assert resolve_pointer(doc, "/items/1") == "b"
    assert resolve_pointer(doc, "/items/2") is _MISSING
    assert resolve_pointer(doc, "/items/x") is _MISSING
    assert resolve_pointer(doc, "/nested/missing") is _MISSING


def test_resolve_pointer_accepts_empty_and_rejects_bad_syntax():
    doc = {"value": 7}

    assert resolve_pointer(doc, "") is doc
    assert resolve_pointer(doc, "value") is _MISSING
    assert resolve_pointer(doc, "/value/deeper") is _MISSING


def test_unresolvable_locator_is_unverifiable(experiment):
    doc = read_doc(experiment)
    doc["results"][1]["locator"] = "/eval/nope"
    write_doc(experiment, doc)

    report = check(experiment)
    finding = only(report, Code.result_unverifiable)

    assert finding.severity is Severity.warning
    assert finding.subject == "tuned-accuracy"
    assert "not found" in finding.message


def test_locator_without_artifact_id_is_unverifiable(experiment):
    doc = read_doc(experiment)
    doc["results"][1]["artifact_id"] = None
    write_doc(experiment, doc)

    report = check(experiment)
    finding = only(report, Code.result_unverifiable)

    assert finding.severity is Severity.warning
    assert finding.subject == "tuned-accuracy"
    assert "no artifact_id" in finding.message


def test_locator_on_non_json_artifact_is_unverifiable(experiment):
    doc = read_doc(experiment)
    doc["results"][1]["artifact_id"] = "report"
    doc["results"][1]["locator"] = "/any/where"
    write_doc(experiment, doc)

    report = check(experiment)
    finding = only(report, Code.result_unverifiable)

    assert finding.severity is Severity.warning
    assert finding.subject == "tuned-accuracy"
    assert ".json" in finding.message


def test_missing_metrics_file_is_not_reported_twice(experiment):
    (experiment / "runs" / "tuned" / "metrics.json").unlink()

    report = check(experiment)

    assert set(codes(report)) == {"missing-file", "claim-at-risk"}
    assert not any(code.startswith("result-") for code in codes(report))


def test_changed_metrics_file_also_reports_the_changed_value(experiment):
    metrics = experiment / "runs" / "tuned" / "metrics.json"
    metrics.write_bytes(metrics.read_bytes().replace(b'"accuracy": 1.0', b'"accuracy": 0.75'))
    report = check(experiment)
    found = codes(report)
    assert "hash-mismatch" in found
    finding = only(report, Code.result_value_mismatch)
    assert "records 1.0" in finding.message
    assert "runs/tuned/metrics.json at /eval/accuracy contains 0.75" in finding.message
    # Root causes print before the claims they put at risk.
    assert found.index("result-value-mismatch") < found.index("claim-at-risk")


def test_changed_metrics_file_with_same_value_only_reports_the_hash(experiment):
    metrics = experiment / "runs" / "tuned" / "metrics.json"
    metrics.write_bytes(metrics.read_bytes().replace(b'"f1": 1.0', b'"f1": 0.99'))
    report = check(experiment)
    assert "hash-mismatch" in codes(report)
    assert Code.result_value_mismatch not in {f.code for f in report.findings}
