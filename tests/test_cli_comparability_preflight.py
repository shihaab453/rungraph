"""S6 slice 3: ``comparability --preflight`` readiness evidence.

Text-only, read-only discovery/readiness over the accepted bridge and core.
These tests use the real 0.2 example plus synthetic manifest copies in tmp
folders; no example file is written.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
from pathlib import Path

import pytest

from runevidence import _experimental_comparability_bridge as bridge
from runevidence import cli
from runevidence import run_comparability as command
from runevidence.manifest import dump_raw, load_manifest

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "examples" / "comparability-recorded-0.2"
TEMPLATE = json.loads((EXAMPLE / "target-comparable.json").read_text(encoding="utf-8"))

FOOTER = (
    "Readiness does not establish matching values, artifact integrity, or a "
    "successful comparison.\n"
    "Omitted candidates and pairwise compatibility were not assessed."
)
PAIR_FOOTER = FOOTER + (
    "\nA ready pair can still be INSUFFICIENT_EVIDENCE when a text dimension "
    "differs only by case or surrounding whitespace."
)


def run_cli(argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def make_doc(
    *,
    run_id: str = "run",
    result_id: str = "result",
    split="test",
    split_state: str = "value",
    dataset: bool = True,
    environment=None,
) -> dict:
    doc = copy.deepcopy(TEMPLATE)
    doc["runs"][0]["id"] = run_id
    doc["runs"][0]["name"] = run_id
    for relationship in doc["relationships"]:
        if relationship.get("type") == "input_to":
            relationship["target"] = run_id
    conditions = doc["runs"][0]["conditions"]
    if split_state == "missing":
        conditions.pop("evaluation_split", None)
    elif split_state == "null":
        conditions["evaluation_split"] = None
    else:
        conditions["evaluation_split"] = split
    if environment is not None:
        conditions["environment"] = environment
    if not dataset:
        conditions.pop("evaluation_dataset_artifact_id", None)
        conditions.pop("model_checkpoint_artifact_id", None)
        doc["artifacts"] = [
            artifact for artifact in doc["artifacts"]
            if artifact.get("kind") not in ("dataset", "checkpoint")
        ]
        doc["relationships"] = [
            relationship for relationship in doc["relationships"]
            if relationship.get("source") not in {
                "train-data", "validation-data", "test-data"
            }
        ]
    doc["results"][0]["id"] = result_id
    doc["results"][0]["run_id"] = run_id
    return doc


def write_doc(root: Path, name: str, doc: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_bytes(dump_raw(doc).encode("utf-8"))
    return path


def pair_argv(baseline: Path, target: Path, baseline_id="result", target_id="result", *extra):
    return [
        "comparability", str(baseline), str(target), "--preflight",
        "--baseline-result", baseline_id, "--target-result", target_id, *extra,
    ]


def ready_pair(tmp_path: Path):
    root = tmp_path / "pair"
    baseline = write_doc(root, "baseline.json", make_doc(run_id="base-run", result_id="base-result"))
    target = write_doc(root, "target.json", make_doc(run_id="target-run", result_id="target-result"))
    return baseline, target


# ---------------------------------------------------------------------------
# Golden list and pair text
# ---------------------------------------------------------------------------


def test_list_golden_using_the_ready_example(capsys):
    code = cli.main([
        "comparability", str(EXAMPLE / "baseline.json"),
        str(EXAMPLE / "target-comparable.json"), "--preflight",
    ])
    out = capsys.readouterr()
    assert (code, out.err) == (0, "")
    states = (
        "    dataset_identity=present, dataset_version=present, "
        "evaluation_protocol=present, evaluation_split=present, "
        "metric_definition=present; attachment=mappable"
    )
    assert out.out == (
        "comparability preflight: list\n"
        "baseline candidates:\n"
        "  baseline-run-result\n"
        f"{states}\n"
        "target candidates:\n"
        "  target-comparable-run-result\n"
        f"{states}\n"
        f"{FOOTER}\n"
    )


def test_ready_pair_golden_using_the_ready_example(capsys):
    code = cli.main([
        "comparability", str(EXAMPLE / "baseline.json"),
        str(EXAMPLE / "target-comparable.json"), "--preflight",
        "--baseline-result", "baseline-run-result",
        "--target-result", "target-comparable-run-result",
    ])
    out = capsys.readouterr()
    assert (code, out.err) == (0, "")
    assert out.out == (
        "comparability preflight: ready\n"
        "baseline result: baseline-run-result\n"
        "target result: target-comparable-run-result\n"
        "  dataset_identity: baseline=present, target=present\n"
        "  dataset_version: baseline=present, target=present\n"
        "  evaluation_protocol: baseline=present, target=present\n"
        "  evaluation_split: baseline=present, target=present\n"
        "  metric_definition: baseline=present, target=present\n"
        "dataset attachment: baseline=mappable, target=mappable\n"
        f"{PAIR_FOOTER}\n"
    )


def test_not_ready_pair_golden_using_the_insufficient_example(capsys):
    code = cli.main([
        "comparability", str(EXAMPLE / "baseline.json"),
        str(EXAMPLE / "target-insufficient.json"), "--preflight",
        "--baseline-result", "baseline-run-result",
        "--target-result", "target-insufficient-run-result",
    ])
    out = capsys.readouterr()
    assert (code, out.err) == (1, "")
    assert out.out == (
        "comparability preflight: not ready\n"
        "baseline result: baseline-run-result\n"
        "target result: target-insufficient-run-result\n"
        "  dataset_identity: baseline=present, target=present\n"
        "  dataset_version: baseline=present, target=present\n"
        "  evaluation_protocol: baseline=present, target=present\n"
        "  evaluation_split: baseline=present, target=missing\n"
        "  metric_definition: baseline=present, target=present\n"
        "dataset attachment: baseline=mappable, target=mappable\n"
        "reason: DIMENSION_NOT_USABLE:evaluation_split:baseline=present,target=missing\n"
        f"{PAIR_FOOTER}\n"
    )


# ---------------------------------------------------------------------------
# Shape and exit matrix
# ---------------------------------------------------------------------------


def test_exactly_one_selector_is_invalid_arguments(tmp_path):
    baseline, target = ready_pair(tmp_path)
    code, out, err = run_cli([
        "comparability", str(baseline), str(target), "--preflight",
        "--baseline-result", "base-result",
    ])
    assert (code, err) == (2, "")
    assert out == "ERROR INVALID_ARGUMENTS (exit 2)\n"


def test_preflight_json_is_a_fixed_text_refusal(tmp_path):
    baseline, target = ready_pair(tmp_path)
    code, out, err = run_cli([
        "comparability", str(baseline), str(target), "--preflight", "--json",
    ])
    assert (code, err) == (2, "")
    assert out == command.PREFLIGHT_JSON_REFUSAL + "\n"


def test_unknown_id_is_exit_two_and_id_free(tmp_path):
    baseline, target = ready_pair(tmp_path)
    code, out, err = run_cli([
        "comparability", str(baseline), str(target), "--preflight",
        "--baseline-result", "CANARY-UNKNOWN-ID", "--target-result", "target-result",
    ])
    assert (code, err) == (2, "")
    assert out == "ERROR RESULT_NOT_FOUND (baseline) (exit 2)\n"
    assert "CANARY-UNKNOWN-ID" not in out + err


def test_invalid_selector_is_invalid_arguments(tmp_path):
    baseline, target = ready_pair(tmp_path)
    code, out, err = run_cli([
        "comparability", str(baseline), str(target), "--preflight",
        "--baseline-result", "   ", "--target-result", "target-result",
    ])
    assert (code, err) == (2, "")
    assert out == "ERROR INVALID_ARGUMENTS (exit 2)\n"


def test_load_failure_is_exit_two_and_path_free(tmp_path):
    baseline, target = ready_pair(tmp_path)
    missing = tmp_path / "CANARY-MISSING-PATH" / "manifest.json"
    code, out, err = run_cli([
        "comparability", str(missing), str(target), "--preflight",
    ])
    assert (code, err) == (2, "")
    assert "MANIFEST_LOAD_FAILED" in out
    assert "CANARY-MISSING-PATH" not in out + err


def _doc_0_3(doc: dict) -> dict:
    out = copy.deepcopy(doc)
    out["schema_version"] = "0.3"
    return out


def _doc_0_4(doc: dict) -> dict:
    out = copy.deepcopy(doc)
    out["schema_version"] = "0.4"
    return out


def test_preflight_list_refuses_0_3_without_reporting_readiness(tmp_path):
    root = tmp_path / "pair-0.3-list"
    baseline = write_doc(
        root, "baseline.json", _doc_0_3(make_doc(run_id="base-run", result_id="base-result"))
    )
    target = write_doc(
        root, "target.json", make_doc(run_id="target-run", result_id="target-result")
    )
    before = (baseline.read_bytes(), target.read_bytes())

    code, out, err = run_cli(["comparability", str(baseline), str(target), "--preflight"])

    assert (code, err) == (2, "")
    assert out == "ERROR UNSUPPORTED_VERSION (baseline) (exit 2)\n"
    assert "preflight:" not in out
    assert "missing" not in out and "ready" not in out
    assert str(baseline) not in out and str(target) not in out
    assert (baseline.read_bytes(), target.read_bytes()) == before


def test_preflight_pair_refuses_0_3_without_reporting_readiness(tmp_path):
    root = tmp_path / "pair-0.3-pair"
    baseline = write_doc(
        root, "baseline.json", _doc_0_3(make_doc(run_id="base-run", result_id="base-result"))
    )
    target = write_doc(
        root, "target.json", _doc_0_3(make_doc(run_id="target-run", result_id="target-result"))
    )

    code, out, err = run_cli(pair_argv(baseline, target))

    assert (code, err) == (2, "")
    assert out == "ERROR UNSUPPORTED_VERSION (baseline) (exit 2)\n"
    assert "preflight:" not in out
    assert "missing" not in out and "ready" not in out
    assert str(baseline) not in out and str(target) not in out


def test_preflight_target_0_3_reports_target_side(tmp_path):
    root = tmp_path / "pair-0.3-target"
    baseline = write_doc(
        root, "baseline.json", make_doc(run_id="base-run", result_id="base-result")
    )
    target = write_doc(
        root, "target.json", _doc_0_3(make_doc(run_id="target-run", result_id="target-result"))
    )

    code, out, err = run_cli(pair_argv(baseline, target))

    assert (code, err) == (2, "")
    assert out == "ERROR UNSUPPORTED_VERSION (target) (exit 2)\n"
    assert str(baseline) not in out and str(target) not in out


def test_preflight_list_refuses_0_4_without_reporting_readiness(tmp_path):
    root = tmp_path / "pair-0.4-list"
    baseline = write_doc(
        root, "baseline.json", _doc_0_4(make_doc(run_id="base-run", result_id="base-result"))
    )
    target = write_doc(
        root, "target.json", make_doc(run_id="target-run", result_id="target-result")
    )
    before = (baseline.read_bytes(), target.read_bytes())

    code, out, err = run_cli(["comparability", str(baseline), str(target), "--preflight"])

    assert (code, err) == (2, "")
    assert out == "ERROR UNSUPPORTED_VERSION (baseline) (exit 2)\n"
    assert "preflight:" not in out
    assert "missing" not in out and "ready" not in out
    assert (baseline.read_bytes(), target.read_bytes()) == before


def test_preflight_pair_refuses_0_4_without_reporting_readiness(tmp_path):
    root = tmp_path / "pair-0.4-pair"
    baseline = write_doc(
        root, "baseline.json", _doc_0_4(make_doc(run_id="base-run", result_id="base-result"))
    )
    target = write_doc(
        root, "target.json", _doc_0_4(make_doc(run_id="target-run", result_id="target-result"))
    )

    code, out, err = run_cli(pair_argv(baseline, target))

    assert (code, err) == (2, "")
    assert out == "ERROR UNSUPPORTED_VERSION (baseline) (exit 2)\n"
    assert "preflight:" not in out
    assert "missing" not in out and "ready" not in out
    assert str(baseline) not in out and str(target) not in out


def test_preflight_target_0_4_reports_target_side(tmp_path):
    root = tmp_path / "pair-0.4-target"
    baseline = write_doc(
        root, "baseline.json", make_doc(run_id="base-run", result_id="base-result")
    )
    target = write_doc(
        root, "target.json", _doc_0_4(make_doc(run_id="target-run", result_id="target-result"))
    )

    code, out, err = run_cli(pair_argv(baseline, target))

    assert (code, err) == (2, "")
    assert out == "ERROR UNSUPPORTED_VERSION (target) (exit 2)\n"
    assert str(baseline) not in out and str(target) not in out


def test_grammar_failure_is_fixed_text(tmp_path):
    baseline, target = ready_pair(tmp_path)
    code, out, err = run_cli([
        "comparability", str(baseline), str(target), "--preflight",
        "--baseline-result", "CANARY-VALUE",
    ])
    assert (code, err) == (2, "")
    assert out == "ERROR INVALID_ARGUMENTS (exit 2)\n"
    assert "CANARY-VALUE" not in out


def test_invalid_arguments_do_not_write(tmp_path):
    baseline, target = ready_pair(tmp_path)
    before = {path.name: path.read_bytes() for path in (baseline, target)}
    run_cli(["comparability", str(baseline), str(target), "--preflight", "--json"])
    run_cli(["comparability", str(baseline), str(target), "--preflight",
             "--baseline-result", "only-one"])
    assert {path.name: path.read_bytes() for path in (baseline, target)} == before
    assert sorted(path.name for path in baseline.parent.iterdir()) == sorted(before)


# ---------------------------------------------------------------------------
# Three states, attachment, core checks, bridge failures
# ---------------------------------------------------------------------------


def test_explicit_null_is_recorded_unknown_and_not_ready(tmp_path):
    baseline, target = ready_pair(tmp_path)
    null_target = write_doc(
        tmp_path / "null", "target.json",
        make_doc(run_id="target-run", result_id="target-result", split_state="null"),
    )
    code, out, err = run_cli(pair_argv(baseline, null_target, "base-result", "target-result"))
    assert (code, err) == (1, "")
    assert "evaluation_split: baseline=present, target=recorded-unknown" in out
    assert "reason: DIMENSION_NOT_USABLE:evaluation_split:baseline=present,target=recorded-unknown" in out


def test_missing_dimension_is_not_ready(tmp_path):
    baseline, target = ready_pair(tmp_path)
    missing_target = write_doc(
        tmp_path / "missing", "target.json",
        make_doc(run_id="target-run", result_id="target-result", split_state="missing"),
    )
    code, out, err = run_cli(pair_argv(baseline, missing_target, "base-result", "target-result"))
    assert (code, err) == (1, "")
    assert "evaluation_split: baseline=present, target=missing" in out
    assert "reason: DIMENSION_NOT_USABLE:evaluation_split:baseline=present,target=missing" in out


def make_ambiguous_doc(
    *, run_id: str, result_id: str, blank_split: bool, name_only_metric: bool
) -> dict:
    doc = make_doc(run_id=run_id, result_id=result_id)
    # Only the selector is first-class; the run dimensions fall back to legacy
    # metadata so a blank legacy split and a name-only legacy metric-definition
    # can reach the core as AMBIGUOUS.
    doc["runs"][0]["conditions"] = {
        "evaluation_dataset_artifact_id": "test-data",
    }
    doc["runs"][0]["metadata"] = {
        "evaluation": {
            "split": "   " if blank_split else "test",
            "protocol": {"protocol_id": "holdout-v1", "version": 1},
        },
        "source_revision": "rev-1",
        "environment": {"python": "3.12"},
    }
    if name_only_metric:
        doc["results"][0].pop("metric_definition", None)
        doc["results"][0]["metadata"] = {"metric_definition": {"name": "accuracy"}}
    return doc


def test_ambiguous_values_are_present_and_reason_uses_usability(tmp_path):
    baseline = write_doc(
        tmp_path / "ambiguity", "baseline.json",
        make_ambiguous_doc(
            run_id="base-run", result_id="base-result",
            blank_split=True, name_only_metric=False,
        ),
    )
    target = write_doc(
        tmp_path / "ambiguity", "target.json",
        make_ambiguous_doc(
            run_id="target-run", result_id="target-result",
            blank_split=False, name_only_metric=True,
        ),
    )

    code, out, err = run_cli(pair_argv(baseline, target, "base-result", "target-result"))
    assert (code, err) == (1, "")
    # RFC recording states only: ambiguous/conflicting values are present.
    assert "evaluation_split: baseline=present, target=present" in out
    assert "metric_definition: baseline=present, target=present" in out
    assert "ambiguous" not in out.split("reason:", 1)[0]
    assert "reason: DIMENSION_NOT_USABLE:evaluation_split:baseline=ambiguous,target=present" in out

    # List mode also reports the three recording states, not the usability
    # tokens, for the same candidates.
    list_code, list_out, list_err = run_cli([
        "comparability", str(baseline), str(target), "--preflight",
    ])
    assert (list_code, list_err) == (0, "")
    assert "evaluation_split=present" in list_out
    assert "metric_definition=present" in list_out
    assert "ambiguous" not in list_out

    code, out, err = run_cli([
        "comparability", str(baseline), str(target),
        "--baseline-result", "base-result", "--target-result", "target-result",
        "--json",
    ])
    assert code == 2
    core = json.loads(out)["core"]
    assert core["decision"] == "INSUFFICIENT_EVIDENCE"
    assert any(
        reason.startswith("MANDATORY_DIMENSION_INSUFFICIENT:evaluation_split")
        for reason in core["reason_codes"]
    )
    assert any(
        reason.startswith("MANDATORY_DIMENSION_INSUFFICIENT:metric_definition")
        for reason in core["reason_codes"]
    )
    assert not any("CASE_OR_WHITESPACE" in reason for reason in core["reason_codes"])


def test_conflicting_state_is_present_but_not_ready(tmp_path, monkeypatch):
    """Synthetic projection: the bridge cannot emit CONFLICTING itself."""
    baseline = EXAMPLE / "baseline.json"
    target = EXAMPLE / "target-comparable.json"
    loaded_baseline = load_manifest(baseline)
    loaded_target = load_manifest(target)
    core = bridge.compare_recorded_conditions(
        loaded_baseline,
        loaded_target,
        baseline_result_id="baseline-run-result",
        target_result_id="target-comparable-run-result",
    )
    mutated = copy.deepcopy(core)
    row = next(
        item for item in mutated["dimensions"] if item["dimension"] == "evaluation_split"
    )
    row["baseline"] = {
        "present": True,
        "observation_count": 2,
        "null_observation_count": 0,
        "state": "CONFLICTING",
    }
    row["status"] = "INSUFFICIENT"
    mutated["decision"] = "INSUFFICIENT_EVIDENCE"
    mutated["reason_codes"] = sorted(
        (
            set(mutated["reason_codes"]) - {"ALL_MANDATORY_DIMENSIONS_MATCH"}
        )
        | {"MANDATORY_DIMENSION_INSUFFICIENT:evaluation_split"}
    )
    monkeypatch.setattr(
        command.bridge, "compare_recorded_conditions", lambda *a, **k: mutated
    )

    code, out, err = run_cli([
        "comparability", str(baseline), str(target), "--preflight",
        "--baseline-result", "baseline-run-result",
        "--target-result", "target-comparable-run-result",
    ])
    assert (code, err) == (1, "")
    assert "evaluation_split: baseline=present, target=present" in out
    assert "reason: DIMENSION_NOT_USABLE:evaluation_split:baseline=conflicting,target=present" in out


def test_combined_size_bound_is_exit_one_reason_target(tmp_path):
    def with_preprocessing(doc: dict) -> dict:
        for index in range(40):
            parent = "p" * 900 + f"-{index:03d}"
            doc["artifacts"].append(
                {
                    "id": parent,
                    "path": f"parents/{index}.bin",
                    "sha256": "a" * 64,
                    "size_bytes": 1,
                }
            )
            doc["relationships"].append(
                {"source": "test-data", "target": parent, "type": "derived_from"}
            )
        return doc

    baseline = write_doc(
        tmp_path / "combined", "baseline.json",
        with_preprocessing(make_doc(run_id="base-run", result_id="base-result")),
    )
    target = write_doc(
        tmp_path / "combined", "target.json",
        with_preprocessing(make_doc(run_id="target-run", result_id="target-result")),
    )

    code, out, err = run_cli(pair_argv(baseline, target, "base-result", "target-result"))

    assert (code, err) == (1, "")
    assert "comparability preflight: not ready" in out
    assert "reason: target: INPUT_BOUND_EXCEEDED" in out
    assert "  dataset_identity:" not in out


def test_attachment_not_mappable_is_reported_separately(tmp_path):
    baseline, target = ready_pair(tmp_path)
    detached = write_doc(
        tmp_path / "detached", "target.json",
        make_doc(run_id="target-run", result_id="target-result", dataset=False),
    )
    code, out, err = run_cli(pair_argv(baseline, detached, "base-result", "target-result"))
    assert (code, err) == (1, "")
    assert "dataset attachment: baseline=mappable, target=not-mappable" in out
    assert "reason: DIMENSION_NOT_USABLE:dataset_identity:" in out


def test_same_run_id_is_not_ready(tmp_path):
    baseline = write_doc(
        tmp_path / "same", "baseline.json",
        make_doc(run_id="same-run", result_id="base-result"),
    )
    target = write_doc(
        tmp_path / "same", "target.json",
        make_doc(run_id="same-run", result_id="target-result"),
    )
    code, out, err = run_cli(pair_argv(baseline, target, "base-result", "target-result"))
    assert (code, err) == (1, "")
    assert "reason: CORE_INPUT_REJECTED:SAME_RUN_ID" in out
    assert "  dataset_identity:" not in out


def test_core_number_limit_is_not_ready(tmp_path):
    baseline, target = ready_pair(tmp_path)
    huge = write_doc(
        tmp_path / "huge", "target.json",
        make_doc(run_id="target-run", result_id="target-result", environment={"x": 1e101}),
    )
    code, out, err = run_cli(pair_argv(baseline, huge, "base-result", "target-result"))
    assert (code, err) == (1, "")
    assert "reason: CORE_INPUT_REJECTED:NUMBER_LIMIT" in out


def test_bridge_error_is_exit_one_with_only_the_code(tmp_path):
    baseline, target = ready_pair(tmp_path)
    duplicate = make_doc(run_id="target-run", result_id="target-result")
    # A duplicate on a non-selected ID reaches the bridge's whole-side
    # uniqueness check as DUPLICATE_ID.
    duplicate["artifacts"].append(copy.deepcopy(duplicate["artifacts"][0]))
    duplicate_path = write_doc(tmp_path / "duplicate", "target.json", duplicate)

    code, out, err = run_cli(pair_argv(baseline, duplicate_path, "base-result", "target-result"))
    assert (code, err) == (1, "")
    assert "comparability preflight: not ready" in out
    assert "reason: target: DUPLICATE_ID" in out
    assert "  dataset_identity:" not in out
    assert "DUPLICATE_ID" in out


def test_duplicate_side_list_is_unavailable_and_exit_zero(tmp_path):
    baseline, duplicate = ready_pair(tmp_path)
    doc = make_doc(run_id="target-run", result_id="target-result")
    doc["results"].append(copy.deepcopy(doc["results"][0]))
    duplicate_path = write_doc(tmp_path / "dup-list", "target.json", doc)

    code, out, err = run_cli([
        "comparability", str(baseline), str(duplicate_path), "--preflight",
    ])
    assert (code, err) == (0, "")
    assert "baseline candidates:" in out
    assert "  unavailable: DUPLICATE_ID" in out
    assert "target-result" not in out


def test_cap_truncation_and_long_id_placeholder(tmp_path):
    baseline, target = ready_pair(tmp_path)
    doc = make_doc(run_id="many-run", result_id="r0")
    for index in range(1, 23):
        item = copy.deepcopy(doc["results"][0])
        item["id"] = f"r{index}"
        doc["results"].append(item)
    doc["results"][0]["id"] = "a" * 1025
    many = write_doc(tmp_path / "many", "many.json", doc)

    code, out, err = run_cli(["comparability", str(baseline), str(many), "--preflight"])
    assert (code, err) == (0, "")
    assert "<id longer than 1024 characters>" in out
    assert "a" * 1025 not in out
    assert "3 more not shown" in out
    lines = out.splitlines()
    target_start = lines.index("target candidates:")
    candidates = [
        line for line in lines[target_start + 1:]
        if line.startswith("  ") and not line.startswith("    ")
        and "more not shown" not in line
    ]
    assert len(candidates) == 20
    assert sum(
        1 for line in lines[target_start + 1:] if line.startswith("    ")
    ) == 20
    assert candidates[0] == "  <id longer than 1024 characters>"
    assert any(
        line == "    unavailable: INPUT_BOUND_EXCEEDED"
        for line in lines[target_start + 1:]
    )


# ---------------------------------------------------------------------------
# Agreement and the case/whitespace exception
# ---------------------------------------------------------------------------


def test_ready_pairs_compare_zero_or_one(tmp_path):
    baseline, target = ready_pair(tmp_path)
    code, out, err = run_cli([
        "comparability", str(baseline), str(target), "--preflight",
        "--baseline-result", "base-result", "--target-result", "target-result",
    ])
    assert (code, err) == (0, "")

    code, out, err = run_cli([
        "comparability", str(baseline), str(target),
        "--baseline-result", "base-result", "--target-result", "target-result",
        "--json",
    ])
    assert code == 0

    drifted = write_doc(
        tmp_path / "drift", "target.json",
        make_doc(run_id="target-run", result_id="target-result", split="validation"),
    )
    code, out, err = run_cli([
        "comparability", str(baseline), str(drifted), "--preflight",
        "--baseline-result", "base-result", "--target-result", "target-result",
    ])
    assert (code, err) == (0, "")
    code, out, err = run_cli([
        "comparability", str(baseline), str(drifted),
        "--baseline-result", "base-result", "--target-result", "target-result",
        "--json",
    ])
    assert code == 1


def test_case_whitespace_ready_but_comparison_insufficient(tmp_path):
    baseline, target = ready_pair(tmp_path)
    spaced = write_doc(
        tmp_path / "spaced", "target.json",
        make_doc(run_id="target-run", result_id="target-result", split=" TEST "),
    )
    code, out, err = run_cli(pair_argv(baseline, spaced, "base-result", "target-result"))
    assert (code, err) == (0, "")
    assert "comparability preflight: ready" in out
    assert "case or surrounding whitespace" in out

    code, out, err = run_cli([
        "comparability", str(baseline), str(spaced),
        "--baseline-result", "base-result", "--target-result", "target-result",
        "--json",
    ])
    assert code == 2
    envelope = json.loads(out)
    assert envelope["core"]["decision"] == "INSUFFICIENT_EVIDENCE"


# ---------------------------------------------------------------------------
# Privacy and read-only
# ---------------------------------------------------------------------------


def test_privacy_canaries_never_reach_output(tmp_path):
    baseline, target = ready_pair(tmp_path)
    canaries = (
        "CANARY-SPLIT-VALUE", "CANARY-METADATA-VALUE", "CANARY-ARTIFACT-PATH",
        "CANARY-ENVIRONMENT-VALUE", "CANARY-PROJECT-METADATA",
    )
    doc = make_doc(
        run_id="target-run", result_id="target-result", split="CANARY-SPLIT-VALUE",
        environment={"k": "CANARY-ENVIRONMENT-VALUE"},
    )
    doc["project"]["metadata"] = {"canary": "CANARY-PROJECT-METADATA"}
    doc["artifacts"][0]["path"] = "CANARY-ARTIFACT-PATH"
    doc["artifacts"][0]["metadata"] = {"canary": "CANARY-METADATA-VALUE"}
    canary_target = write_doc(tmp_path / "canary", "target.json", doc)

    outputs = [
        run_cli(["comparability", str(baseline), str(canary_target), "--preflight"]),
        run_cli(pair_argv(baseline, canary_target, "base-result", "target-result")),
        run_cli([
            "comparability", str(baseline), str(canary_target), "--preflight",
            "--baseline-result", "CANARY-ARG-SELECTOR", "--target-result", "target-result",
        ]),
        run_cli([
            "comparability", str(baseline), str(canary_target), "--preflight", "--json",
        ]),
    ]
    for code, out, err in outputs:
        combined = out + err
        for canary in canaries:
            assert canary not in combined
        assert "CANARY-ARG-SELECTOR" not in combined


def test_preflight_writes_nothing(tmp_path):
    baseline, target = ready_pair(tmp_path)
    before = {
        path.name: path.read_bytes()
        for path in baseline.parent.iterdir() if path.is_file()
    }
    run_cli(["comparability", str(baseline), str(target), "--preflight"])
    run_cli(pair_argv(baseline, target, "base-result", "target-result"))
    after = {
        path.name: path.read_bytes()
        for path in baseline.parent.iterdir() if path.is_file()
    }
    assert after == before


def test_ordinary_comparison_still_requires_both_selectors(tmp_path):
    baseline, target = ready_pair(tmp_path)
    code, out, err = run_cli([
        "comparability", str(baseline), str(target), "--json",
    ])
    assert (code, err) == (2, "")
    document = json.loads(out)
    assert document == {
        "format": command.ERROR_FORMAT,
        "ok": False,
        "exit_code": 2,
        "error": {"code": "INVALID_ARGUMENTS", "side": None},
    }


def test_list_reports_recorded_unknown_and_missing_states(tmp_path, capsys):
    baseline = write_doc(
        tmp_path / "b",
        "baseline.json",
        make_doc(run_id="base-run", result_id="base-result", split_state="null"),
    )
    target = write_doc(
        tmp_path / "t",
        "target.json",
        make_doc(run_id="target-run", result_id="target-result", split_state="missing"),
    )

    code = cli.main(["comparability", str(baseline), str(target), "--preflight"])
    out = capsys.readouterr().out

    assert code == 0
    assert "evaluation_split=recorded-unknown" in out
    assert "evaluation_split=missing" in out


def test_comparison_preserves_directory_records(tmp_path):
    directory = {
        "id": "data-dir",
        "path": "data",
        "artifact_type": "directory",
        "digest_format": "runevidence-directory-v1",
        "sha256": "0" * 64,
        "size_bytes": 0,
    }
    baseline_doc = make_doc(run_id="base-run", result_id="base-result")
    baseline_doc["artifacts"].append(dict(directory))
    target_doc = make_doc(run_id="target-run", result_id="target-result")
    target_doc["artifacts"].append(dict(directory))
    baseline = write_doc(tmp_path / "b", "baseline.json", baseline_doc)
    target = write_doc(tmp_path / "t", "target.json", target_doc)
    before_baseline = baseline.read_bytes()
    before_target = target.read_bytes()

    assert cli.main([
        "comparability", str(baseline), str(target), "--preflight"
    ]) == 0
    assert cli.main([
        "comparability", str(baseline), str(target), "--preflight",
        "--baseline-result", "base-result", "--target-result", "target-result",
    ]) == 0
    assert cli.main([
        "comparability", str(baseline), str(target),
        "--baseline-result", "base-result", "--target-result", "target-result",
    ]) == 0

    assert baseline.read_bytes() == before_baseline
    assert target.read_bytes() == before_target
    for path in (baseline, target):
        document = json.loads(path.read_text(encoding="utf-8"))
        record = next(a for a in document["artifacts"] if a["id"] == "data-dir")
        assert record["artifact_type"] == "directory"
        assert record["digest_format"] == "runevidence-directory-v1"
