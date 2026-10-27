"""Result value checks: JSON Pointers, value comparison, unverifiable results."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from runevidence.checks import run_checks
from runevidence.checks.results import _MISSING, resolve_pointer, values_match
from runevidence.cli import main
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


def test_resolve_pointer_accepts_multi_digit_and_nested_list_indices():
    doc = {"items": list(range(12)), "runs": [{"scores": [0.1, 0.2]}]}

    assert resolve_pointer(doc, "/items/10") == 10
    assert resolve_pointer(doc, "/items/11") == 11
    assert resolve_pointer(doc, "/items/12") is _MISSING
    assert resolve_pointer(doc, "/runs/0/scores/1") == 0.2


def test_resolve_pointer_rejects_non_canonical_list_indices_without_raising():
    doc = {"items": ["a", "b"], "empty": []}

    # Superscript two passes str.isdigit() but int() rejects it; 5000 digits exceeds
    # Python's integer-string conversion limit. Both used to raise.
    for token in ["", "-", "-1", "+1", "01", "00", " 1", "1 ", "\u00b2", "\u0661", "9" * 5000]:
        assert resolve_pointer(doc, "/items/" + token) is _MISSING, repr(token[:10])
    assert resolve_pointer(doc, "/empty/0") is _MISSING


def test_resolve_pointer_keeps_object_keys_that_look_like_bad_indices():
    doc = {"\u00b2": "yes", "01": "value", "": "empty", "a/b": "slash", "a~b": "tilde", "9" * 5000: "long"}

    assert resolve_pointer(doc, "/\u00b2") == "yes"
    assert resolve_pointer(doc, "/01") == "value"
    assert resolve_pointer(doc, "/") == "empty"
    assert resolve_pointer(doc, "/a~1b") == "slash"
    assert resolve_pointer(doc, "/a~0b") == "tilde"
    assert resolve_pointer(doc, "/" + "9" * 5000) == "long"


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


BAD_INDEX_LOCATORS = ["/eval/scores/\u00b2", "/eval/scores/01", "/eval/scores/" + "9" * 5000]
BAD_INDEX_IDS = ["superscript-two", "zero-padded", "5000-digits"]


def use_metrics_with_a_list(root: Path, locator: str) -> None:
    """Record a tuned metrics file holding a list, and point the result at it."""
    data = json.dumps({"eval": {"scores": [1.0, 0.5]}}).encode()
    (root / "runs" / "tuned" / "metrics.json").write_bytes(data)
    doc = read_doc(root)
    art = next(a for a in doc["artifacts"] if a["id"] == "tuned-metrics")
    art["sha256"] = hashlib.sha256(data).hexdigest()
    art["size_bytes"] = len(data)
    doc["results"][1]["locator"] = locator
    write_doc(root, doc)


def test_list_locator_resolves_in_a_recorded_metrics_file(experiment):
    use_metrics_with_a_list(experiment, "/eval/scores/0")

    assert codes(check(experiment)) == []


@pytest.mark.parametrize("locator", BAD_INDEX_LOCATORS, ids=BAD_INDEX_IDS)
def test_malformed_list_index_locator_is_unverifiable(experiment, locator):
    use_metrics_with_a_list(experiment, locator)

    report = check(experiment)
    finding = only(report, Code.result_unverifiable)

    assert codes(report) == ["result-unverifiable"]
    assert finding.severity is Severity.warning
    assert finding.subject == "tuned-accuracy"
    assert "not found" in finding.message


@pytest.mark.parametrize("locator", BAD_INDEX_LOCATORS, ids=BAD_INDEX_IDS)
def test_malformed_list_index_locator_keeps_validate_json_parseable(experiment, locator, capsys):
    use_metrics_with_a_list(experiment, locator)

    assert main(["validate", str(experiment), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [f["code"] for f in data["findings"]] == ["result-unverifiable"]
    assert data["counts"]["warning"] == 1

    assert main(["validate", str(experiment), "--json", "--strict"]) == 1
    assert json.loads(capsys.readouterr().out)["exit_code"] == 1


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
