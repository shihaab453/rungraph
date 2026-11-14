"""End-to-end tests for the recorded-comparability example folder.

The example in ``examples/comparability-recorded`` is read-only test data. The
tests call ``runevidence.cli.main`` directly on the committed manifests and
never write into the example folder.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runevidence import cli
from runevidence import run_comparability as command

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "comparability-recorded"

COMPARABLE = "COMPARABLE_RECORDED_CONDITIONS"
NOT_COMPARABLE = "NOT_COMPARABLE_RECORDED_CONDITIONS"
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"

PAIRS = (
    (
        "baseline.json",
        "target-comparable.json",
        "baseline-result",
        "target-comparable-result",
        0,
        COMPARABLE,
        "ALL_MANDATORY_DIMENSIONS_MATCH",
    ),
    (
        "baseline.json",
        "target-drifted.json",
        "baseline-result",
        "target-drifted-result",
        1,
        NOT_COMPARABLE,
        "MANDATORY_DIMENSION_DIFFERENT:evaluation_split",
    ),
    (
        "baseline.json",
        "target-insufficient.json",
        "baseline-result",
        "target-insufficient-result",
        2,
        INSUFFICIENT,
        "MANDATORY_DIMENSION_INSUFFICIENT:evaluation_split",
    ),
)

MANIFESTS = (
    "baseline.json",
    "target-comparable.json",
    "target-drifted.json",
    "target-insufficient.json",
)


def _run(capsys, argv: list[str]) -> tuple[int, str, str]:
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _single_json(text: str) -> dict:
    """Parse exactly one complete JSON document with no trailing content."""
    document, end = json.JSONDecoder().raw_decode(text)
    assert text[end:].strip() == ""
    return document


@pytest.mark.parametrize(
    ("baseline_name", "target_name", "baseline_result", "target_result",
     "expected_code", "expected_decision", "expected_reason"),
    PAIRS,
)
def test_example_comparability_pairs(
    baseline_name,
    target_name,
    baseline_result,
    target_result,
    expected_code,
    expected_decision,
    expected_reason,
    capsys,
):
    code, out, err = _run(
        capsys,
        [
            "comparability",
            str(EXAMPLE / baseline_name),
            str(EXAMPLE / target_name),
            "--baseline-result",
            baseline_result,
            "--target-result",
            target_result,
            "--json",
        ],
    )

    assert code == expected_code
    assert err == ""
    document = _single_json(out)
    assert set(document) == {"format", "application_status", "core", "notice"}
    assert document["format"] == command.RESULT_FORMAT
    assert document["application_status"] == command.APPLICATION_STATUS
    assert document["notice"] == command.NOTICE
    assert out == json.dumps(
        document, indent=2, ensure_ascii=True, allow_nan=False
    ) + "\n"

    core = document["core"]
    assert set(core) == command.CORE_KEYS
    assert core["decision"] == expected_decision
    assert core["input_rejected"] is False
    assert expected_reason in core["reason_codes"]


@pytest.mark.parametrize("name", MANIFESTS)
def test_example_manifest_validates_clean(name, capsys):
    code, out, err = _run(
        capsys,
        ["validate", str(EXAMPLE / name), "--json", "--summary-only"],
    )

    assert code == 0
    assert err == ""
    summary = _single_json(out)
    assert summary["summary_version"] == 1
    assert summary["total_findings"] == 0
    assert summary["by_severity"] == {}
    assert summary["by_code"] == {}
    assert summary["exit_code"] == 0


def test_example_dataset_still_exists():
    data = EXAMPLE / "data" / "toy.csv"
    assert data.is_file()
    assert data.stat().st_size > 0
