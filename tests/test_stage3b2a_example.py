"""End-to-end tests for the schema 0.2 recorded-comparability example.

The example in ``examples/comparability-recorded-0.2`` is read-only test data.
The tests call ``runevidence.cli.main`` directly on the committed manifests and
never write into the example folder. The separate 0.1 example is unchanged and
keeps its own test module.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runevidence import cli
from runevidence import run_comparability as command
from runevidence.manifest import load_manifest

EXAMPLE = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "comparability-recorded-0.2"
)
OLD_EXAMPLE = (
    Path(__file__).resolve().parents[1] / "examples" / "comparability-recorded"
)

COMPARABLE = "COMPARABLE_RECORDED_CONDITIONS"
NOT_COMPARABLE = "NOT_COMPARABLE_RECORDED_CONDITIONS"
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"

PAIRS = (
    (
        "baseline.json",
        "target-comparable.json",
        "baseline-run-result",
        "target-comparable-run-result",
        0,
        COMPARABLE,
        "ALL_MANDATORY_DIMENSIONS_MATCH",
    ),
    (
        "baseline.json",
        "target-drifted.json",
        "baseline-run-result",
        "target-drifted-run-result",
        1,
        NOT_COMPARABLE,
        "MANDATORY_DIMENSION_DIFFERENT:evaluation_split",
    ),
    (
        "baseline.json",
        "target-insufficient.json",
        "baseline-run-result",
        "target-insufficient-run-result",
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
    document, end = json.JSONDecoder().raw_decode(text)
    assert text[end:].strip() == ""
    return document


@pytest.mark.parametrize("name", MANIFESTS)
def test_example_0_2_manifests_validate_clean(capsys, name):
    code, out, err = _run(capsys, ["validate", str(EXAMPLE / name)])
    assert code == 0, out + err
    assert "0 errors" in out


@pytest.mark.parametrize(
    ("baseline", "target", "baseline_result", "target_result",
     "expected_code", "expected_decision", "expected_reason"),
    PAIRS,
)
def test_example_0_2_comparability_pairs(
    capsys,
    baseline,
    target,
    baseline_result,
    target_result,
    expected_code,
    expected_decision,
    expected_reason,
):
    code, out, err = _run(
        capsys,
        [
            "comparability",
            str(EXAMPLE / baseline),
            str(EXAMPLE / target),
            "--baseline-result",
            baseline_result,
            "--target-result",
            target_result,
            "--json",
        ],
    )
    assert code == expected_code, out + err
    assert err == ""
    envelope = _single_json(out)
    assert envelope["format"] == command.RESULT_FORMAT
    assert envelope["application_status"] == command.APPLICATION_STATUS
    core = envelope["core"]
    assert core["decision"] == expected_decision
    assert expected_reason in core["reason_codes"]
    assert core["input_rejected"] is False


@pytest.mark.parametrize(
    ("baseline", "target", "baseline_result", "target_result",
     "expected_code", "expected_decision"),
    [(a, b, c, d, e, f) for a, b, c, d, e, f, _ in PAIRS],
)
def test_example_0_2_compare_paths(
    baseline,
    target,
    baseline_result,
    target_result,
    expected_code,
    expected_decision,
):
    outcome = command.compare_paths(
        EXAMPLE / baseline,
        EXAMPLE / target,
        baseline_result_id=baseline_result,
        target_result_id=target_result,
    )
    assert outcome.exit_code == expected_code
    assert outcome.document["core"]["decision"] == expected_decision


def test_example_0_2_selects_one_of_three_attached_datasets():
    loaded = load_manifest(EXAMPLE / "baseline.json")
    manifest = loaded.manifest
    assert manifest.schema_version == "0.2"

    run = manifest.runs[0]
    assert run.conditions.evaluation_dataset_artifact_id == "test-data"
    assert run.conditions.model_checkpoint_artifact_id is None
    assert "model_checkpoint_artifact_id" not in run.conditions.model_fields_set

    datasets = [artifact for artifact in manifest.artifacts if artifact.kind == "dataset"]
    assert {artifact.id for artifact in datasets} == {
        "train-data",
        "validation-data",
        "test-data",
    }
    assert all(artifact.dataset_id for artifact in datasets)

    attached = {
        relationship.source
        for relationship in manifest.relationships
        if relationship.type.value == "input_to" and relationship.target == run.id
    }
    assert attached == {"train-data", "validation-data", "test-data"}

    result = manifest.results[0]
    assert result.metric_definition is not None
    assert "metric_definition" in result.model_fields_set


def test_old_0_1_example_is_unchanged():
    for name in (
        "baseline.json",
        "target-comparable.json",
        "target-drifted.json",
        "target-insufficient.json",
    ):
        raw = json.loads((OLD_EXAMPLE / name).read_text(encoding="utf-8"))
        assert raw["schema_version"] == "0.1"
        for run in raw["runs"]:
            assert "conditions" not in run
        for artifact in raw["artifacts"]:
            assert "dataset_id" not in artifact
        for result in raw["results"]:
            assert "metric_definition" not in result


def test_example_0_2_reads_only_the_two_manifests(monkeypatch):
    baseline = EXAMPLE / "baseline.json"
    target = EXAMPLE / "target-comparable.json"
    original = command.manifest.load_manifest
    loads = []

    def counted(path):
        loads.append(Path(path).resolve())
        return original(path)

    monkeypatch.setattr(command.manifest, "load_manifest", counted)
    outcome = command.compare_paths(
        baseline,
        target,
        baseline_result_id="baseline-run-result",
        target_result_id="target-comparable-run-result",
    )
    assert outcome.exit_code == 0
    assert loads == [baseline.resolve(), target.resolve()]
