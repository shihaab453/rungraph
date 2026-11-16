"""Tests for the Stage 2a comparability command module.

Synthetic in-memory manifests cover the envelope, error, privacy and
determinism contract. Temporary manifest files cover loader failure handling
and the two-load I/O boundary. No artifact path is ever opened.
"""

from __future__ import annotations

import builtins
import copy
import io as io_module
import json
import math
import os
import socket
import subprocess
import urllib.request
from pathlib import Path

import pytest

from runevidence import _experimental_comparability_bridge as bridge
from runevidence import comparability
from runevidence import run_comparability as command
from runevidence.manifest import LoadedManifest, dump_manifest
from runevidence.models import (
    Artifact,
    EvidenceReference,
    Manifest,
    Manifest04,
    Project,
    Relationship,
    Result,
    Run,
    RunConditions,
)

COMPARABLE = "COMPARABLE_RECORDED_CONDITIONS"
NOT_COMPARABLE = "NOT_COMPARABLE_RECORDED_CONDITIONS"
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"

HASH_DATASET = "a" * 64
HASH_CHECKPOINT = "b" * 64
HASH_PARENT = "c" * 64
HASH_OTHER = "e" * 64

MANDATORY = {
    "dataset_identity",
    "dataset_version",
    "evaluation_split",
    "metric_definition",
    "evaluation_protocol",
}
OPTIONAL = {
    "preprocessing",
    "source_revision",
    "model_checkpoint",
    "environment",
}


# --------------------------------------------------------------- fixtures


def _make_manifest(tag: str) -> Manifest:
    dataset_id = f"ds-{tag}"
    run_id = f"run-{tag}"
    return Manifest(
        project=Project(id=f"proj-{tag}", name=f"project {tag}"),
        runs=[
            Run(
                id=run_id,
                name=f"run {tag}",
                metadata={
                    "evaluation": {
                        "split": "test",
                        "protocol": {"protocol_id": "protocol-1", "version": 1},
                    },
                    "source_revision": "rev-1",
                    "environment": {"python": "3.12", "tags": ["cpu"]},
                },
            )
        ],
        artifacts=[
            Artifact(
                id=dataset_id,
                path=f"data/raw-{tag}.csv",
                kind="dataset",
                sha256=HASH_DATASET,
                metadata={"dataset_id": "imagenet"},
            ),
            Artifact(id="raw-shared", path="data/source-shared.csv", sha256=HASH_PARENT),
            Artifact(
                id=f"ckpt-{tag}",
                path=f"models/ckpt-{tag}.bin",
                kind="checkpoint",
                sha256=HASH_CHECKPOINT,
            ),
        ],
        results=[
            Result(
                id=f"res-{tag}",
                run_id=run_id,
                name="accuracy",
                value=0.9,
                metadata={
                    "metric_definition": {
                        "definition_id": "accuracy-v1",
                        "higher_is_better": True,
                    }
                },
            )
        ],
        relationships=[
            Relationship(source=dataset_id, target=run_id, type="input_to"),
            Relationship(source=f"ckpt-{tag}", target=run_id, type="input_to"),
            Relationship(source=dataset_id, target="raw-shared", type="derived_from"),
        ],
    )


def _loaded(tag: str) -> LoadedManifest:
    return LoadedManifest(
        manifest=_make_manifest(tag),
        path=Path(f"/synthetic/{tag}/runevidence.json"),
        source_bytes=b"",
    )


def _loaded_0_3(tag: str, *, split: str = "test") -> LoadedManifest:
    manifest = _make_manifest(tag)
    metadata = dict(manifest.runs[0].metadata)
    metadata["evaluation"] = {**metadata["evaluation"], "split": split}
    run = manifest.runs[0].model_copy(
        update={"conditions": RunConditions(evaluation_split=split), "metadata": metadata}
    )
    manifest = manifest.model_copy(
        update={"schema_version": "0.3", "runs": [run]}
    )
    return LoadedManifest(
        manifest=manifest,
        path=Path(f"/synthetic/{tag}/runevidence.json"),
        source_bytes=b"",
    )


def _loaded_0_4(tag: str) -> LoadedManifest:
    # A real Manifest04: the guard runs before any mapping, but the model type
    # must match what load_manifest returns for a 0.4 source.
    data = _make_manifest(tag).model_dump()
    data["schema_version"] = "0.4"
    manifest = Manifest04.model_validate(data)
    return LoadedManifest(
        manifest=manifest,
        path=Path(f"/synthetic/{tag}/runevidence.json"),
        source_bytes=b"",
    )


def _pair() -> tuple[LoadedManifest, LoadedManifest]:
    return _loaded("b"), _loaded("t")


def _run(loaded: LoadedManifest) -> Run:
    return loaded.manifest.runs[0]


def _result(loaded: LoadedManifest) -> Result:
    return loaded.manifest.results[0]


def _dataset(loaded: LoadedManifest) -> Artifact:
    return next(a for a in loaded.manifest.artifacts if a.kind == "dataset")


def _checkpoint(loaded: LoadedManifest) -> Artifact:
    return next(a for a in loaded.manifest.artifacts if a.kind == "checkpoint")


def _evaluation(loaded: LoadedManifest) -> dict:
    return _run(loaded).metadata["evaluation"]


def _dimension(core: dict, name: str) -> dict:
    return next(row for row in core["dimensions"] if row["dimension"] == name)


def _stub_loads(monkeypatch, baseline: LoadedManifest, target: LoadedManifest) -> dict:
    """Patch the module's manifest load, alternating baseline then target."""
    calls = {"count": 0}

    def fake(_path):
        calls["count"] += 1
        return baseline if calls["count"] % 2 == 1 else target

    monkeypatch.setattr(command.manifest, "load_manifest", fake)
    return calls


def _compare(
    monkeypatch,
    baseline: LoadedManifest,
    target: LoadedManifest,
    *,
    baseline_result_id: str | None = None,
    target_result_id: str | None = None,
) -> command.CommandOutcome:
    _stub_loads(monkeypatch, baseline, target)
    return command.compare_paths(
        Path("/synthetic/baseline.json"),
        Path("/synthetic/target.json"),
        baseline_result_id=(
            baseline_result_id
            if baseline_result_id is not None
            else baseline.manifest.results[0].id
        ),
        target_result_id=(
            target_result_id
            if target_result_id is not None
            else target.manifest.results[0].id
        ),
    )


def _expected_core(baseline: LoadedManifest, target: LoadedManifest) -> dict:
    document = bridge._build_input(
        baseline,
        target,
        baseline_result_id=baseline.manifest.results[0].id,
        target_result_id=target.manifest.results[0].id,
    )
    return comparability.evaluate(document)


def _write_manifest(path: Path, manifest: Manifest) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_manifest(manifest), encoding="utf-8")
    return path


def _assert_envelope(outcome: command.CommandOutcome, expected_core: dict) -> None:
    assert outcome.exit_code in (
        command.EXIT_COMPARABLE,
        command.EXIT_NOT_COMPARABLE,
        command.EXIT_UNAVAILABLE,
    )
    assert set(outcome.document) == {
        "format",
        "application_status",
        "core",
        "notice",
    }
    assert outcome.document["format"] == command.RESULT_FORMAT
    assert outcome.document["application_status"] == command.APPLICATION_STATUS
    assert outcome.document["notice"] == command.NOTICE
    assert outcome.document["core"] == expected_core
    assert set(outcome.document["core"]) == command.CORE_KEYS


def _assert_error(
    outcome: command.CommandOutcome,
    *,
    code: str,
    side: str | None,
    mapping_code: str | None = None,
) -> None:
    expected_error = {"code": code, "side": side}
    if mapping_code is not None:
        expected_error["mapping_code"] = mapping_code
    assert outcome.exit_code == 2
    assert outcome.document == {
        "format": command.ERROR_FORMAT,
        "ok": False,
        "exit_code": 2,
        "error": expected_error,
    }


# --------------------------------------------------------------- constants


def test_public_constants_and_outcome_type():
    assert command.RESULT_FORMAT == "runevidence-comparability-result-v1"
    assert command.ERROR_FORMAT == "runevidence-comparability-error-v1"
    assert command.APPLICATION_STATUS == "EXPERIMENTAL_READ_ONLY_CLI"
    assert command.NOTICE == (
        "Embedded prototype and integration flags describe the frozen PC26 "
        "core, not this CLI."
    )
    assert command.EXIT_COMPARABLE == 0
    assert command.EXIT_NOT_COMPARABLE == 1
    assert command.EXIT_UNAVAILABLE == 2
    outcome = command.CommandOutcome(exit_code=0, document={"a": 1}, text="x")
    assert outcome.exit_code == 0 and outcome.document == {"a": 1}
    assert outcome.text == "x"
    with pytest.raises(Exception):
        outcome.exit_code = 1


def test_public_function_signature_is_exact():
    import inspect

    signature = inspect.signature(command.compare_paths)
    parameters = signature.parameters
    assert list(parameters) == [
        "baseline",
        "target",
        "baseline_result_id",
        "target_result_id",
    ]
    for name in ("baseline_result_id", "target_result_id"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
    assert not any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def test_module_docstring_documents_scope_and_exclusions():
    text = command.__doc__ or ""
    assert "Stage 2a" in text
    assert "docs/comparability-stage2-privacy-cli-rfc.md" in text
    assert "complete manifests" in text
    assert "hostile Python objects" in text
    assert "model_construct" in text
    assert "shared or remotely" in text
    assert "callable comparison service" in text
    assert "debuggers" in text
    assert "command history" in text
    assert "timing" in text
    assert "read-only" in text


# ---------------------------------------------------------- result envelope


def test_comparable_envelope_is_exact_and_core_matches_evaluate(monkeypatch):
    baseline, target = _pair()
    expected = _expected_core(baseline, target)
    outcome = _compare(monkeypatch, baseline, target)
    assert outcome.exit_code == 0
    _assert_envelope(outcome, expected)
    assert outcome.document["core"]["decision"] == COMPARABLE
    assert outcome.document["core"]["input_rejected"] is False
    assert type(outcome.text) is str


def test_not_comparable_returns_exit_one_envelope(monkeypatch):
    baseline, target = _pair()
    _evaluation(target)["split"] = "train"
    expected = _expected_core(baseline, target)
    outcome = _compare(monkeypatch, baseline, target)
    assert outcome.exit_code == 1
    _assert_envelope(outcome, expected)
    assert outcome.document["core"]["decision"] == NOT_COMPARABLE
    assert "MANDATORY_DIMENSION_DIFFERENT:evaluation_split" in (
        outcome.document["core"]["reason_codes"]
    )


def test_insufficient_returns_exit_two_envelope(monkeypatch):
    baseline, target = _pair()
    _evaluation(baseline).pop("split")
    expected = _expected_core(baseline, target)
    outcome = _compare(monkeypatch, baseline, target)
    assert outcome.exit_code == 2
    _assert_envelope(outcome, expected)
    assert outcome.document["core"]["decision"] == INSUFFICIENT
    assert outcome.document["core"]["input_rejected"] is False


@pytest.mark.parametrize(
    "baseline_split,target_split",
    [("test", "Test"), (" test ", "test")],
)
def test_case_or_whitespace_only_split_is_insufficient_not_internal(
    monkeypatch, baseline_split, target_split
):
    baseline, target = _pair()
    _evaluation(baseline)["split"] = baseline_split
    _evaluation(target)["split"] = target_split
    expected = _expected_core(baseline, target)
    outcome = _compare(monkeypatch, baseline, target)
    assert outcome.exit_code == 2
    _assert_envelope(outcome, expected)
    core = outcome.document["core"]
    assert core["decision"] == INSUFFICIENT
    row = _dimension(core, "evaluation_split")
    assert row["status"] == "INSUFFICIENT"
    assert row["baseline"]["state"] == "KNOWN"
    assert row["target"]["state"] == "KNOWN"
    assert "MANDATORY_DIMENSION_INSUFFICIENT:evaluation_split" in (
        core["reason_codes"]
    )
    assert outcome.document["format"] == command.RESULT_FORMAT


def test_core_rejection_returns_envelope_not_error_document(monkeypatch):
    baseline, target = _pair()
    _run(target).id = "run-b"
    _result(target).run_id = "run-b"
    for relationship in target.manifest.relationships:
        if relationship.target == "run-t":
            relationship.target = "run-b"
    expected = _expected_core(baseline, target)
    outcome = _compare(monkeypatch, baseline, target)
    assert outcome.exit_code == 2
    _assert_envelope(outcome, expected)
    core = outcome.document["core"]
    assert core["input_rejected"] is True
    assert "SAME_RUN_ID" in core["input_error_codes"]
    assert "MALFORMED_INPUT" in core["reason_codes"]
    assert outcome.document["format"] == command.RESULT_FORMAT


def test_core_number_limit_rejection_returns_envelope(monkeypatch):
    baseline, target = _pair()
    _result(baseline).metadata["metric_definition"] = {
        "name": 9007199254740992
    }
    expected = _expected_core(baseline, target)
    outcome = _compare(monkeypatch, baseline, target)
    assert outcome.exit_code == 2
    _assert_envelope(outcome, expected)
    assert outcome.document["core"]["input_rejected"] is True
    assert "NUMBER_LIMIT" in outcome.document["core"]["input_error_codes"]


def test_display_name_difference_disclosed_only_as_reason(monkeypatch):
    baseline, target = _pair()
    _result(baseline).name = "METRIC-NAME-BASELINE-CANARY"
    _result(target).name = "METRIC-NAME-TARGET-CANARY"
    expected = _expected_core(baseline, target)
    outcome = _compare(monkeypatch, baseline, target)
    assert outcome.exit_code == 0
    _assert_envelope(outcome, expected)
    assert outcome.document["core"]["decision"] == COMPARABLE
    assert "METRIC_NAME_DIFFERS" in outcome.document["core"]["reason_codes"]
    blob = json.dumps(outcome.document, ensure_ascii=False) + outcome.text
    assert "METRIC-NAME-BASELINE-CANARY" not in blob
    assert "METRIC-NAME-TARGET-CANARY" not in blob


def test_unrelated_nonfinite_metadata_is_shape_unsupported(monkeypatch):
    baseline, target = _pair()
    _run(baseline).metadata["unrelated"] = {"value": math.nan}
    outcome = _compare(monkeypatch, baseline, target)
    _assert_error(
        outcome,
        code=command.MAPPING_FAILED,
        side="baseline",
        mapping_code=bridge.INPUT_SHAPE_UNSUPPORTED,
    )
    assert outcome.text == (
        "ERROR MAPPING_FAILED (baseline, INPUT_SHAPE_UNSUPPORTED) (exit 2)"
    )


def test_duplicate_ids_outside_selected_subgraph_are_rejected(monkeypatch):
    baseline, target = _pair()
    baseline.manifest.artifacts.append(
        Artifact(id="outside-dup", path="data/one.csv")
    )
    baseline.manifest.artifacts.append(
        Artifact(id="outside-dup", path="data/two.csv")
    )
    outcome = _compare(monkeypatch, baseline, target)
    _assert_error(
        outcome,
        code=command.MAPPING_FAILED,
        side="baseline",
        mapping_code=bridge.DUPLICATE_ID,
    )


# --------------------------------------------------------- application errors


def test_baseline_load_failure_shape(tmp_path):
    missing = tmp_path / "missing-baseline.json"
    outcome = command.compare_paths(
        missing,
        tmp_path / "missing-target.json",
        baseline_result_id="res-b",
        target_result_id="res-t",
    )
    _assert_error(outcome, code=command.MANIFEST_LOAD_FAILED, side="baseline")
    assert outcome.text == "ERROR MANIFEST_LOAD_FAILED (baseline) (exit 2)"
    assert set(outcome.document["error"]) == {"code", "side"}


def test_target_load_failure_shape(tmp_path):
    baseline = _write_manifest(tmp_path / "baseline.json", _make_manifest("b"))
    outcome = command.compare_paths(
        baseline,
        tmp_path / "missing-target.json",
        baseline_result_id="res-b",
        target_result_id="res-t",
    )
    _assert_error(outcome, code=command.MANIFEST_LOAD_FAILED, side="target")
    assert outcome.text == "ERROR MANIFEST_LOAD_FAILED (target) (exit 2)"


def test_0_3_baseline_compares_first_class_conditions(monkeypatch):
    baseline = _loaded_0_3("b", split="validation")
    target = _loaded("t")
    outcome = _compare(monkeypatch, baseline, target)
    assert outcome.exit_code == command.EXIT_NOT_COMPARABLE
    assert "NOT_COMPARABLE_RECORDED_CONDITIONS" in json.dumps(outcome.document)


def test_0_3_target_compares(monkeypatch):
    baseline, _ = _pair()
    outcome = _compare(monkeypatch, baseline, _loaded_0_3("t"))
    assert outcome.exit_code == command.EXIT_COMPARABLE
    assert "COMPARABLE_RECORDED_CONDITIONS" in json.dumps(outcome.document)


def test_0_3_both_compare(monkeypatch):
    outcome = _compare(monkeypatch, _loaded_0_3("b"), _loaded_0_3("t"))
    assert outcome.exit_code == command.EXIT_COMPARABLE


def test_0_4_baseline_compares(monkeypatch):
    outcome = _compare(monkeypatch, _loaded_0_4("b"), _loaded("t"))
    assert outcome.exit_code == command.EXIT_COMPARABLE


def test_0_4_target_compares(monkeypatch):
    baseline, _ = _pair()
    outcome = _compare(monkeypatch, baseline, _loaded_0_4("t"))
    assert outcome.exit_code == command.EXIT_COMPARABLE


def test_0_4_both_compare(monkeypatch):
    outcome = _compare(monkeypatch, _loaded_0_4("b"), _loaded_0_4("t"))
    assert outcome.exit_code == command.EXIT_COMPARABLE


def test_mapping_failure_shape_and_text(monkeypatch):
    baseline, target = _pair()
    outcome = _compare(
        monkeypatch,
        baseline,
        target,
        baseline_result_id="res-missing",
    )
    _assert_error(
        outcome,
        code=command.MAPPING_FAILED,
        side="baseline",
        mapping_code=bridge.RESULT_NOT_FOUND,
    )
    assert set(outcome.document["error"]) == {"code", "side", "mapping_code"}
    assert outcome.text == (
        "ERROR MAPPING_FAILED (baseline, RESULT_NOT_FOUND) (exit 2)"
    )


def test_mapping_failure_with_null_side_has_fixed_text(monkeypatch):
    baseline, target = _pair()

    def fake(*args, **kwargs):
        raise bridge.ComparabilityMappingError(
            bridge.INTERNAL_MAPPING_ERROR, None
        )

    monkeypatch.setattr(command.bridge, "compare_recorded_conditions", fake)
    outcome = _compare(monkeypatch, baseline, target)
    _assert_error(
        outcome,
        code=command.MAPPING_FAILED,
        side=None,
        mapping_code=bridge.INTERNAL_MAPPING_ERROR,
    )
    assert outcome.text == (
        "ERROR MAPPING_FAILED (INTERNAL_MAPPING_ERROR) (exit 2)"
    )


def test_mapping_error_catalogue_and_side_are_validated(monkeypatch):
    baseline, target = _pair()

    def fake_code(*args, **kwargs):
        raise bridge.ComparabilityMappingError("NOT-A-BRIDGE-CODE", "baseline")

    monkeypatch.setattr(command.bridge, "compare_recorded_conditions", fake_code)
    outcome = _compare(monkeypatch, baseline, target)
    _assert_error(outcome, code=command.INTERNAL_ERROR, side=None)

    def fake_side(*args, **kwargs):
        raise bridge.ComparabilityMappingError(
            bridge.INPUT_SHAPE_UNSUPPORTED, "middleware"
        )

    monkeypatch.setattr(command.bridge, "compare_recorded_conditions", fake_side)
    outcome = _compare(monkeypatch, baseline, target)
    _assert_error(outcome, code=command.INTERNAL_ERROR, side=None)

    def fake_nonstring_side(*args, **kwargs):
        raise bridge.ComparabilityMappingError(
            bridge.INPUT_SHAPE_UNSUPPORTED, 3
        )

    monkeypatch.setattr(
        command.bridge, "compare_recorded_conditions", fake_nonstring_side
    )
    outcome = _compare(monkeypatch, baseline, target)
    _assert_error(outcome, code=command.INTERNAL_ERROR, side=None)


@pytest.mark.parametrize(
    "mapping_case",
    [
        bridge.INVALID_SELECTOR,
        bridge.RESULT_NOT_FOUND,
        bridge.RESULT_SELECTION_AMBIGUOUS,
        bridge.RUN_NOT_FOUND,
        bridge.RUN_SELECTION_AMBIGUOUS,
        bridge.DUPLICATE_ID,
        bridge.DANGLING_REFERENCE,
        bridge.CONFLICTING_OWNERSHIP,
        bridge.INPUT_SHAPE_UNSUPPORTED,
        bridge.INPUT_BOUND_EXCEEDED,
        bridge.INTERNAL_MAPPING_ERROR,
    ],
)
def test_every_mapping_code_is_sanitized(monkeypatch, mapping_case):
    baseline, target = _pair()
    baseline_result_id = "res-b"
    target_result_id = "res-t"
    expected_side: str | None = "baseline"

    if mapping_case == bridge.INVALID_SELECTOR:
        baseline_result_id = ""
    elif mapping_case == bridge.RESULT_NOT_FOUND:
        baseline_result_id = "res-missing"
    elif mapping_case == bridge.RESULT_SELECTION_AMBIGUOUS:
        baseline.manifest.results.append(
            Result(id="res-b", run_id="run-b", name="duplicate")
        )
    elif mapping_case == bridge.RUN_NOT_FOUND:
        _result(baseline).run_id = "run-missing"
    elif mapping_case == bridge.RUN_SELECTION_AMBIGUOUS:
        baseline.manifest.runs.append(Run(id="run-b", metadata={}))
    elif mapping_case == bridge.DUPLICATE_ID:
        baseline.manifest.artifacts.append(
            Artifact(id="outside-dup", path="data/one.csv")
        )
        baseline.manifest.artifacts.append(
            Artifact(id="outside-dup", path="data/two.csv")
        )
    elif mapping_case == bridge.DANGLING_REFERENCE:
        baseline.manifest.relationships.append(
            Relationship(source="ghost", target="run-b", type="input_to")
        )
    elif mapping_case == bridge.CONFLICTING_OWNERSHIP:
        baseline.manifest.runs.append(Run(id="run-other", metadata={}))
        baseline.manifest.relationships.append(
            Relationship(source="res-b", target="run-other", type="result_of")
        )
    elif mapping_case == bridge.INPUT_SHAPE_UNSUPPORTED:
        _dataset(baseline).metadata["dataset_id"] = 5
    elif mapping_case == bridge.INPUT_BOUND_EXCEEDED:
        _evaluation(baseline)["split"] = "x" * 1025
    else:
        expected_side = None

        def boom(_document):
            raise RuntimeError("INTERNAL-MAPPING-CANARY")

        monkeypatch.setattr(command.bridge.comparability, "evaluate", boom)

    outcome = _compare(
        monkeypatch,
        baseline,
        target,
        baseline_result_id=baseline_result_id,
        target_result_id=target_result_id,
    )
    _assert_error(
        outcome,
        code=command.MAPPING_FAILED,
        side=expected_side,
        mapping_code=mapping_case,
    )
    assert "INTERNAL-MAPPING-CANARY" not in json.dumps(outcome.document)
    assert "INTERNAL-MAPPING-CANARY" not in outcome.text


def test_invalid_arguments_shape():
    outcome = command.invalid_arguments()
    _assert_error(outcome, code=command.INVALID_ARGUMENTS, side=None)
    assert outcome.text == "ERROR INVALID_ARGUMENTS (exit 2)"
    assert set(outcome.document["error"]) == {"code", "side"}


def test_injected_internal_error_has_no_message(monkeypatch):
    baseline, target = _pair()

    def boom(*args, **kwargs):
        raise RuntimeError("INTERNAL-CANARY-7Q")

    monkeypatch.setattr(command.bridge, "compare_recorded_conditions", boom)
    outcome = _compare(monkeypatch, baseline, target)
    _assert_error(outcome, code=command.INTERNAL_ERROR, side=None)
    assert "INTERNAL-CANARY-7Q" not in json.dumps(outcome.document)
    assert "INTERNAL-CANARY-7Q" not in outcome.text


def test_render_failure_is_internal_error(monkeypatch):
    baseline, target = _pair()

    def boom(_core):
        raise RuntimeError("RENDER-INTERNAL-CANARY")

    monkeypatch.setattr(command, "_render", boom)
    outcome = _compare(monkeypatch, baseline, target)
    _assert_error(outcome, code=command.INTERNAL_ERROR, side=None)
    assert "RENDER-INTERNAL-CANARY" not in json.dumps(outcome.document)
    assert "RENDER-INTERNAL-CANARY" not in outcome.text


def test_base_exceptions_are_not_caught(monkeypatch):
    baseline, target = _pair()

    def boom(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(command.bridge, "compare_recorded_conditions", boom)
    with pytest.raises(KeyboardInterrupt):
        _compare(monkeypatch, baseline, target)


def test_unexpected_loader_exception_is_internal_error(monkeypatch):
    def boom(_path):
        raise RuntimeError("LOADER-INTERNAL-CANARY")

    monkeypatch.setattr(command.manifest, "load_manifest", boom)
    outcome = command.compare_paths(
        Path("/synthetic/b.json"),
        Path("/synthetic/t.json"),
        baseline_result_id="res-b",
        target_result_id="res-t",
    )
    _assert_error(outcome, code=command.INTERNAL_ERROR, side=None)
    assert "LOADER-INTERNAL-CANARY" not in json.dumps(outcome.document)


# --------------------------------------------------------------- precedence


def test_baseline_load_precedes_target_load_and_mapping(tmp_path):
    outcome = command.compare_paths(
        tmp_path / "missing-baseline.json",
        tmp_path / "missing-target.json",
        baseline_result_id="missing-selector",
        target_result_id="missing-selector",
    )
    _assert_error(outcome, code=command.MANIFEST_LOAD_FAILED, side="baseline")


def test_target_load_precedes_mapping(monkeypatch, tmp_path):
    baseline = _write_manifest(tmp_path / "baseline.json", _make_manifest("b"))
    calls = {"count": 0}
    original = command.manifest.load_manifest

    def counted(path):
        calls["count"] += 1
        return original(path)

    monkeypatch.setattr(command.manifest, "load_manifest", counted)
    outcome = command.compare_paths(
        baseline,
        tmp_path / "missing-target.json",
        baseline_result_id="missing-selector",
        target_result_id="missing-selector",
    )
    _assert_error(outcome, code=command.MANIFEST_LOAD_FAILED, side="target")
    assert calls["count"] == 2


def test_baseline_load_precedes_mapping_failure(monkeypatch):
    baseline, target = _pair()
    _dataset(baseline).metadata["dataset_id"] = 5

    def one_load(path):
        raise command.manifest.ManifestLoadError([])

    monkeypatch.setattr(command.manifest, "load_manifest", one_load)
    outcome = command.compare_paths(
        Path("/synthetic/b.json"),
        Path("/synthetic/t.json"),
        baseline_result_id="res-b",
        target_result_id="res-t",
    )
    _assert_error(outcome, code=command.MANIFEST_LOAD_FAILED, side="baseline")


# ------------------------------------------------------------ loader failures


SECRET = "LOADER-SECRET-CANARY-7Q"


def _deep_json(depth: int) -> bytes:
    return (
        '{"project": {"id": "p", "name": "' + SECRET + '"}, "deep":'
        + "[" * depth
        + "]" * depth
        + "}"
    ).encode()


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "directory",
        "invalid_utf8",
        "invalid_json",
        "duplicate_keys",
        "schema_secret_key",
        "schema_secret_value",
        "deep_nesting",
    ],
)
def test_load_failures_are_fixed_and_leak_free(tmp_path, kind):
    path = tmp_path / "baseline.json"
    if kind == "missing":
        pass
    elif kind == "directory":
        path.mkdir()
    elif kind == "invalid_utf8":
        path.write_bytes(
            b'{"project": {"id": "p", "name": "' + SECRET.encode() + b'\xff"}}'
        )
    elif kind == "invalid_json":
        path.write_text(
            '{"project": {"id": "p", "name": "' + SECRET + '"', encoding="utf-8"
        )
    elif kind == "duplicate_keys":
        path.write_text(
            '{"project": {"id": "p", "name": "' + SECRET + '"},'
            ' "project": {"id": "q", "name": "other"}}',
            encoding="utf-8",
        )
    elif kind == "schema_secret_key":
        data = _make_manifest("b").model_dump(mode="json")
        data[SECRET] = "extra"
        path.write_text(json.dumps(data), encoding="utf-8")
    elif kind == "schema_secret_value":
        data = _make_manifest("b").model_dump(mode="json")
        data["project"]["tags"] = [SECRET]
        data["runs"][0]["started_at"] = SECRET
        path.write_text(json.dumps(data), encoding="utf-8")
    else:
        path.write_bytes(_deep_json(5000))

    outcome = command.compare_paths(
        path,
        tmp_path / "target.json",
        baseline_result_id="res-b",
        target_result_id="res-t",
    )
    _assert_error(outcome, code=command.MANIFEST_LOAD_FAILED, side="baseline")
    texts = [
        json.dumps(outcome.document, ensure_ascii=True),
        json.dumps(outcome.document, ensure_ascii=False),
        outcome.text,
        str(outcome.document),
        repr(outcome.document),
        repr(outcome),
    ]
    for value in texts:
        assert SECRET not in value


def test_unreadable_manifest_is_fixed_and_leak_free(tmp_path):
    path = tmp_path / (SECRET + ".json")
    path.write_text(dump_manifest(_make_manifest("b")), encoding="utf-8")
    path.chmod(0)
    try:
        if os.access(path, os.R_OK):
            pytest.skip("file permissions are not enforced in this environment")
        outcome = command.compare_paths(
            path,
            tmp_path / "target.json",
            baseline_result_id="res-b",
            target_result_id="res-t",
        )
    finally:
        path.chmod(0o600)
    _assert_error(outcome, code=command.MANIFEST_LOAD_FAILED, side="baseline")
    assert SECRET not in json.dumps(outcome.document)
    assert SECRET not in outcome.text


def test_real_manifests_return_the_same_core_as_in_memory_pair(tmp_path):
    baseline = _write_manifest(tmp_path / "baseline.json", _make_manifest("b"))
    target = _write_manifest(tmp_path / "target.json", _make_manifest("t"))
    expected = _expected_core(_loaded("b"), _loaded("t"))
    outcome = command.compare_paths(
        baseline,
        target,
        baseline_result_id="res-b",
        target_result_id="res-t",
    )
    assert outcome.exit_code == 0
    _assert_envelope(outcome, expected)


# ------------------------------------------------------------------ privacy


CANARIES = {
    "project": "PC27CMDCANARYPROJECT7Q",
    "run": "PC27CMDCANARYRUN7Q",
    "result": "PC27CMDCANARYRESULT7Q",
    "dataset": "PC27CMDCANARYDATASET7Q",
    "checkpoint": "PC27CMDCANARYCHECKPOINT7Q",
    "parent": "PC27CMDCANARYPARENT7Q",
    "name": "PC27CMDCANARYNAME7Q",
    "metakey": "PC27CMDCANARYMETAKEY7Q",
    "metavalue": "PC27CMDCANARYMETAVALUE7Q",
    "dataset_id": "PC27CMDCANARYDATASETID7Q",
    "split": "PC27CMDCANARYSPLIT7Q",
    "definition_id": "PC27CMDCANARYDEFINITION7Q",
    "protocol_id": "PC27CMDCANARYPROTOCOL7Q",
    "revision": "PC27CMDCANARYREVISION7Q",
    "hash": "d3adb33f" * 8,
    "path": "PC27CMDCANARYPATH7Q",
    "locator": "PC27CMDCANARYLOCATOR7Q",
    "note": "PC27CMDCANARYNOTE7Q",
    "claim": "PC27CMDCANARYCLAIM7Q",
    "loaded_path": "PC27CMDCANARYLOADEDPATH7Q",
    "source_bytes": "PC27CMDCANARYSOURCEBYTES7Q",
    # Printable/Unicode/quote/newline and terminal-control variants.
    "unicode": "PC27CANARY-Δ-日本語-7Q",
    "quotes": 'PC27CANARY-"-\\-7Q',
    "newlines": "PC27CANARY-LINE1\nLINE2-7Q",
    "terminal": "PC27CANARY-\x1b[31mRED\x1b[0m-\x07-7Q",
}
CANARY_VALUES = list(CANARIES.values())


def _canary_manifest(tag: str) -> Manifest:
    canary = CANARIES
    run_id = f"{canary['run']}-{tag}"
    dataset_id = f"{canary['dataset']}-{tag}"
    parent_id = f"{canary['parent']}-{tag}"
    return Manifest(
        project=Project(
            id=f"{canary['project']}-{tag}",
            name=canary["name"],
            description=canary["newlines"],
            tags=[canary["unicode"]],
            metadata={
                canary["metakey"]: canary["metavalue"],
                "terminal": canary["terminal"],
            },
        ),
        runs=[
            Run(
                id=run_id,
                name=canary["name"],
                params={canary["metakey"]: canary["metavalue"]},
                metadata={
                    "evaluation": {
                        "split": canary["split"],
                        "protocol": {
                            "protocol_id": canary["protocol_id"],
                            "version": 1,
                        },
                    },
                    "source_revision": canary["revision"],
                    "environment": {
                        canary["metakey"]: canary["metavalue"],
                        "unicode": canary["unicode"],
                    },
                    f"{canary['metakey']}-run": canary["metavalue"],
                },
            )
        ],
        artifacts=[
            Artifact(
                id=dataset_id,
                path=f"{canary['path']}/{tag}.csv",
                kind="dataset",
                sha256=canary["hash"],
                metadata={
                    "dataset_id": canary["dataset_id"],
                    canary["metakey"]: canary["metavalue"],
                },
            ),
            Artifact(
                id=parent_id,
                path=f"{canary['path']}/raw.csv",
                sha256=canary["hash"],
                metadata={f"{canary['metakey']}-parent": canary["metavalue"]},
            ),
            Artifact(
                id=f"{canary['checkpoint']}-{tag}",
                path=f"{canary['path']}/ckpt.bin",
                kind="checkpoint",
                sha256=canary["hash"],
                metadata={canary["metakey"]: canary["metavalue"]},
            ),
        ],
        results=[
            Result(
                id=f"{canary['result']}-{tag}",
                run_id=run_id,
                name=canary["name"],
                value=0.5,
                locator=f"/{canary['locator']}",
                metadata={
                    "metric_definition": {
                        "definition_id": canary["definition_id"]
                    },
                    f"{canary['metakey']}-result": canary["metavalue"],
                },
            )
        ],
        evidence=[
            EvidenceReference(
                id=f"evidence-{tag}",
                claim=canary["claim"],
                source=canary["quotes"],
                metadata={canary["metakey"]: canary["metavalue"]},
            )
        ],
        relationships=[
            Relationship(
                source=dataset_id,
                target=run_id,
                type="input_to",
                note=canary["note"],
            ),
            Relationship(
                source=f"{canary['checkpoint']}-{tag}",
                target=run_id,
                type="input_to",
                note=canary["terminal"],
            ),
            Relationship(
                source=dataset_id,
                target=parent_id,
                type="derived_from",
                note=canary["note"],
            ),
        ],
    )


def _canary_pair() -> tuple[LoadedManifest, LoadedManifest]:
    def loaded(tag: str) -> LoadedManifest:
        return LoadedManifest(
            manifest=_canary_manifest(tag),
            path=Path(
                f"/synthetic/{CANARIES['loaded_path']}/{tag}/runevidence.json"
            ),
            source_bytes=(CANARIES["source_bytes"] + "-" + tag).encode(),
        )

    return loaded("b"), loaded("t")


def _assert_no_canaries(texts) -> None:
    blob = "\n".join(texts)
    for value in CANARY_VALUES:
        assert value not in blob, value


def _privacy_texts(outcome: command.CommandOutcome) -> list[str]:
    texts = [
        json.dumps(outcome.document, sort_keys=True, ensure_ascii=True),
        json.dumps(outcome.document, sort_keys=True, ensure_ascii=False),
        outcome.text,
        str(outcome.document),
        repr(outcome.document),
        repr(outcome),
    ]
    if "error" in outcome.document:
        texts.extend(
            [
                str(outcome.document["error"]),
                repr(outcome.document["error"]),
            ]
        )
    return texts


def _assert_capture_clean(capsys, caplog, outcome) -> None:
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert caplog.text == ""
    _assert_no_canaries(
        _privacy_texts(outcome) + [captured.out, captured.err, caplog.text]
    )


def test_privacy_canaries_absent_from_comparable(capsys, caplog, monkeypatch):
    caplog.set_level(0)
    baseline, target = _canary_pair()
    outcome = _compare(monkeypatch, baseline, target)
    assert outcome.exit_code == 0
    _assert_envelope(outcome, _expected_core(baseline, target))
    _assert_capture_clean(capsys, caplog, outcome)


def test_privacy_canaries_absent_from_not_comparable(capsys, caplog, monkeypatch):
    caplog.set_level(0)
    baseline, target = _canary_pair()
    _evaluation(target)["split"] = "a-different-value"
    outcome = _compare(monkeypatch, baseline, target)
    assert outcome.exit_code == 1
    _assert_capture_clean(capsys, caplog, outcome)


def test_privacy_canaries_absent_from_insufficient(capsys, caplog, monkeypatch):
    caplog.set_level(0)
    baseline, target = _canary_pair()
    _evaluation(baseline).pop("split")
    outcome = _compare(monkeypatch, baseline, target)
    assert outcome.exit_code == 2
    assert outcome.document["core"]["decision"] == INSUFFICIENT
    _assert_capture_clean(capsys, caplog, outcome)


def test_privacy_canaries_absent_from_core_rejection(
    capsys, caplog, monkeypatch
):
    caplog.set_level(0)
    baseline, target = _canary_pair()
    _run(target).id = _run(baseline).id
    _result(target).run_id = _run(baseline).id
    for relationship in target.manifest.relationships:
        if relationship.target.startswith(CANARIES["run"] + "-t"):
            relationship.target = _run(baseline).id
    outcome = _compare(monkeypatch, baseline, target)
    assert outcome.exit_code == 2
    assert outcome.document["core"]["input_rejected"] is True
    _assert_capture_clean(capsys, caplog, outcome)


def test_privacy_canaries_absent_from_mapping_error(
    capsys, caplog, monkeypatch
):
    caplog.set_level(0)
    baseline, target = _canary_pair()
    _dataset(baseline).metadata["dataset_id"] = 5
    outcome = _compare(monkeypatch, baseline, target)
    _assert_error(
        outcome,
        code=command.MAPPING_FAILED,
        side="baseline",
        mapping_code=bridge.INPUT_SHAPE_UNSUPPORTED,
    )
    _assert_capture_clean(capsys, caplog, outcome)


def test_privacy_canaries_absent_from_load_error(capsys, caplog, tmp_path):
    caplog.set_level(0)
    path = tmp_path / (CANARIES["path"] + ".json")
    path.write_text(
        '{"project": {"id": "p", "name": "' + CANARIES["metavalue"] + '"}',
        encoding="utf-8",
    )
    outcome = command.compare_paths(
        path,
        tmp_path / (CANARIES["loaded_path"] + ".json"),
        baseline_result_id=CANARIES["locator"],
        target_result_id=CANARIES["claim"],
    )
    _assert_error(outcome, code=command.MANIFEST_LOAD_FAILED, side="baseline")
    _assert_capture_clean(capsys, caplog, outcome)


def test_privacy_canaries_absent_from_internal_error(
    capsys, caplog, monkeypatch
):
    caplog.set_level(0)
    baseline, target = _canary_pair()

    def boom(*args, **kwargs):
        raise RuntimeError(CANARIES["metavalue"])

    monkeypatch.setattr(command.bridge, "compare_recorded_conditions", boom)
    outcome = _compare(monkeypatch, baseline, target)
    _assert_error(outcome, code=command.INTERNAL_ERROR, side=None)
    _assert_capture_clean(capsys, caplog, outcome)


# ------------------------------------------------------------------- no I/O


def _install_trap(monkeypatch, attempts, module, name):
    if not hasattr(module, name):
        return

    def trapped(*args, **kwargs):
        attempts.append((getattr(module, "__name__", "?"), name))
        raise RuntimeError("I/O ATTEMPT")

    monkeypatch.setattr(module, name, trapped)


def test_no_io_attempts_when_loads_are_stubbed(monkeypatch):
    baseline, target = _pair()
    expected = _expected_core(baseline, target)
    _stub_loads(monkeypatch, baseline, target)

    import runevidence.checks as checks_module

    attempts: list[tuple[str, str]] = []
    for module, names in (
        (builtins, ["open"]),
        (io_module, ["open"]),
        (os, ["open", "stat", "lstat", "scandir", "listdir", "readlink"]),
        (socket, ["socket", "create_connection", "getaddrinfo"]),
        (urllib.request, ["urlopen"]),
        (subprocess, ["run", "Popen"]),
        (command.manifest, ["save_manifest", "save_raw", "load_raw"]),
        (checks_module, ["run_checks"]),
    ):
        for name in names:
            _install_trap(monkeypatch, attempts, module, name)

    try:
        outcome = command.compare_paths(
            Path("/synthetic/b.json"),
            Path("/synthetic/t.json"),
            baseline_result_id="res-b",
            target_result_id="res-t",
        )
    finally:
        # Restore global traps before any assertion so pytest can report.
        monkeypatch.undo()
    assert attempts == []
    assert outcome.exit_code == 0
    _assert_envelope(outcome, expected)


def test_no_io_attempts_when_loads_are_stubbed_full(monkeypatch):
    """Repeat without the bridge private-builder exception above."""
    baseline, target = _pair()
    expected = _expected_core(baseline, target)
    _stub_loads(monkeypatch, baseline, target)

    import pathlib

    attempts: list[tuple[str, str]] = []
    for module, names in (
        (builtins, ["open"]),
        (io_module, ["open"]),
        (os, ["open", "stat", "lstat", "scandir", "listdir", "readlink"]),
        (socket, ["socket", "create_connection", "getaddrinfo"]),
        (urllib.request, ["urlopen"]),
        (subprocess, ["run", "Popen"]),
        (command.manifest, ["save_manifest", "save_raw", "load_raw"]),
        (pathlib.Path, ["read_bytes", "read_text", "write_bytes", "write_text", "iterdir", "glob"]),
    ):
        for name in names:
            _install_trap(monkeypatch, attempts, module, name)

    try:
        outcome = command.compare_paths(
            Path("/synthetic/b.json"),
            Path("/synthetic/t.json"),
            baseline_result_id="res-b",
            target_result_id="res-t",
        )
    finally:
        # Restore global traps before any assertion so pytest can report.
        monkeypatch.undo()
    assert attempts == []
    assert outcome.exit_code == 0
    _assert_envelope(outcome, expected)


def test_integration_reads_only_the_two_manifests(tmp_path, monkeypatch):
    root = tmp_path / "experiment"
    baseline = _write_manifest(root / "baseline.json", _make_manifest("b"))
    target = _write_manifest(root / "target.json", _make_manifest("t"))
    # Tempting artifact bytes that must never be opened.
    artifact = root / "data" / "raw-b.csv"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("ARTIFACT-CANARY-NOT-READ", encoding="utf-8")

    def norm(value):
        return os.path.abspath(os.fspath(value))

    allowed = {
        norm(baseline),
        norm(target),
        os.path.realpath(baseline),
        os.path.realpath(target),
    }
    root_prefix = norm(root) + os.sep
    attempts: list[str] = []
    calls = {"count": 0}
    original_load = command.manifest.load_manifest
    original_open = io_module.open
    original_stat = Path.stat
    original_readlink = os.readlink

    def counted_load(path):
        calls["count"] += 1
        return original_load(path)

    def guarded_open(file, *args, **kwargs):
        if isinstance(file, (str, bytes, os.PathLike)):
            path = norm(file)
            if path in allowed:
                return original_open(file, *args, **kwargs)
            if path.startswith(root_prefix):
                attempts.append("artifact-open")
                raise AssertionError("artifact opened")
        return original_open(file, *args, **kwargs)

    def guarded_stat(self, *args, **kwargs):
        path = norm(self)
        if path in allowed or path == root_prefix[:-1]:
            return original_stat(self, *args, **kwargs)
        if path.startswith(root_prefix):
            attempts.append("artifact-stat")
            raise AssertionError("artifact stat")
        return original_stat(self, *args, **kwargs)

    def guarded_readlink(path, *args, **kwargs):
        candidate = norm(path)
        if candidate in allowed or candidate == root_prefix[:-1]:
            return original_readlink(path, *args, **kwargs)
        if candidate.startswith(root_prefix):
            attempts.append("artifact-readlink")
            raise AssertionError("artifact readlink")
        return original_readlink(path, *args, **kwargs)

    def trapped(*args, **kwargs):
        attempts.append("trap")
        raise AssertionError("I/O attempted")

    monkeypatch.setattr(command.manifest, "load_manifest", counted_load)
    monkeypatch.setattr(io_module, "open", guarded_open)
    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(Path, "stat", guarded_stat)
    monkeypatch.setattr(os, "readlink", guarded_readlink)
    for module, names in (
        (os, ["scandir", "listdir"]),
        (socket, ["socket", "create_connection", "getaddrinfo"]),
        (urllib.request, ["urlopen"]),
        (subprocess, ["run", "Popen"]),
        (command.manifest, ["save_manifest", "save_raw"]),
    ):
        for name in names:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, trapped)

    expected = _expected_core(_loaded("b"), _loaded("t"))
    try:
        outcome = command.compare_paths(
            baseline,
            target,
            baseline_result_id="res-b",
            target_result_id="res-t",
        )
    finally:
        # Restore global traps before any assertion so pytest can report.
        monkeypatch.undo()
    assert calls["count"] == 2
    assert attempts == []
    assert outcome.exit_code == 0
    _assert_envelope(outcome, expected)


# ---------------------------------------------------- determinism / mutation


def test_repeated_calls_are_byte_identical(monkeypatch):
    baseline, target = _pair()
    _stub_loads(monkeypatch, baseline, target)
    first = command.compare_paths(
        Path("/synthetic/b.json"),
        Path("/synthetic/t.json"),
        baseline_result_id="res-b",
        target_result_id="res-t",
    )
    second = command.compare_paths(
        Path("/synthetic/b.json"),
        Path("/synthetic/t.json"),
        baseline_result_id="res-b",
        target_result_id="res-t",
    )
    assert json.dumps(first.document, sort_keys=True) == json.dumps(
        second.document, sort_keys=True
    )
    assert first.text == second.text
    assert first.exit_code == second.exit_code


def test_permuted_collections_and_key_orders_are_identical(monkeypatch):
    baseline, target = _pair()
    first = _compare(monkeypatch, baseline, target)

    baseline2, target2 = _pair()
    for loaded in (baseline2, target2):
        manifest = loaded.manifest
        manifest.runs.reverse()
        manifest.artifacts.reverse()
        manifest.results.reverse()
        manifest.evidence.reverse()
        manifest.relationships.reverse()
        for obj in [
            manifest.project,
            *manifest.runs,
            *manifest.artifacts,
            *manifest.results,
            *manifest.evidence,
        ]:
            metadata = getattr(obj, "metadata", None)
            if isinstance(metadata, dict) and metadata:
                obj.metadata = dict(reversed(list(metadata.items())))
        for run in manifest.runs:
            for key in ("evaluation", "environment"):
                value = run.metadata.get(key)
                if isinstance(value, dict):
                    run.metadata[key] = dict(reversed(list(value.items())))
        for result in manifest.results:
            definition = result.metadata.get("metric_definition")
            if isinstance(definition, dict):
                result.metadata["metric_definition"] = dict(
                    reversed(list(definition.items()))
                )

    second = _compare(monkeypatch, baseline2, target2)
    assert json.dumps(first.document, sort_keys=True) == json.dumps(
        second.document, sort_keys=True
    )
    assert first.text == second.text


def test_calls_do_not_mutate_manifests(monkeypatch):
    baseline, target = _pair()
    before = [
        baseline.manifest.model_dump(),
        target.manifest.model_dump(),
        baseline.manifest.model_dump(mode="json"),
        target.manifest.model_dump(mode="json"),
        baseline.path,
        target.path,
        baseline.source_bytes,
        target.source_bytes,
    ]
    _compare(monkeypatch, baseline, target)
    after = [
        baseline.manifest.model_dump(),
        target.manifest.model_dump(),
        baseline.manifest.model_dump(mode="json"),
        target.manifest.model_dump(mode="json"),
        baseline.path,
        target.path,
        baseline.source_bytes,
        target.source_bytes,
    ]
    assert before == after


# ------------------------------------------------------------- text renderer


def test_comparable_text_is_exact():
    # Build the expected text without relying on the renderer under test.
    lines = [
        "Comparability of recorded conditions: " + COMPARABLE,
        "Application status: EXPERIMENTAL_READ_ONLY_CLI",
        "Mandatory dimensions:",
    ]
    for name in (
        "dataset_identity",
        "dataset_version",
        "evaluation_protocol",
        "evaluation_split",
        "metric_definition",
    ):
        lines.append(
            "  %s: MATCH (baseline observations 1, nulls 0; "
            "target observations 1, nulls 0)" % name
        )
    lines.append("Optional dimensions:")
    for name in (
        "environment",
        "model_checkpoint",
        "preprocessing",
        "source_revision",
    ):
        lines.append(
            "  %s: MATCH (baseline observations 1, nulls 0; "
            "target observations 1, nulls 0)" % name
        )
    lines.extend(
        [
            "Reason codes: ALL_MANDATORY_DIMENSIONS_MATCH",
            "Unknown dimensions: 0",
            command.NOTICE,
            command.DISCLAIMER,
        ]
    )
    expected = "\n".join(lines)
    baseline, target = _pair()
    core = _expected_core(baseline, target)
    assert command._render(core) == expected
    assert "unknown_" not in expected


def test_insufficient_text_is_exact(monkeypatch):
    baseline, target = _pair()
    _evaluation(baseline).pop("split")
    outcome = _compare(monkeypatch, baseline, target)
    lines = [
        "Comparability of recorded conditions: " + INSUFFICIENT,
        "Application status: EXPERIMENTAL_READ_ONLY_CLI",
        "Mandatory dimensions:",
        "  dataset_identity: MATCH (baseline observations 1, nulls 0; "
        "target observations 1, nulls 0)",
        "  dataset_version: MATCH (baseline observations 1, nulls 0; "
        "target observations 1, nulls 0)",
        "  evaluation_protocol: MATCH (baseline observations 1, nulls 0; "
        "target observations 1, nulls 0)",
        "  evaluation_split: INSUFFICIENT (baseline observations 0, nulls 0; "
        "target observations 1, nulls 0)",
        "  metric_definition: MATCH (baseline observations 1, nulls 0; "
        "target observations 1, nulls 0)",
        "Optional dimensions:",
        "  environment: MATCH (baseline observations 1, nulls 0; "
        "target observations 1, nulls 0)",
        "  model_checkpoint: MATCH (baseline observations 1, nulls 0; "
        "target observations 1, nulls 0)",
        "  preprocessing: MATCH (baseline observations 1, nulls 0; "
        "target observations 1, nulls 0)",
        "  source_revision: MATCH (baseline observations 1, nulls 0; "
        "target observations 1, nulls 0)",
        "Reason codes: MANDATORY_DIMENSION_INSUFFICIENT:evaluation_split",
        "Unknown dimensions: 0",
        command.NOTICE,
        command.DISCLAIMER,
    ]
    assert outcome.text == "\n".join(lines)
    assert "unknown_" not in outcome.text
    assert "imagenet" not in outcome.text
    assert "accuracy" not in outcome.text


def test_text_includes_core_input_errors_only_when_present(monkeypatch):
    baseline, target = _pair()
    _run(target).id = "run-b"
    _result(target).run_id = "run-b"
    for relationship in target.manifest.relationships:
        if relationship.target == "run-t":
            relationship.target = "run-b"
    outcome = _compare(monkeypatch, baseline, target)
    assert outcome.exit_code == 2
    assert "Core input errors: SAME_RUN_ID" in outcome.text
    # A clean comparable result has no core-error line.
    baseline2, target2 = _pair()
    clean = _compare(monkeypatch, baseline2, target2)
    assert "Core input errors:" not in clean.text


# ---------------------------------------------------------- negative controls


def _valid_core(baseline, target) -> dict:
    return copy.deepcopy(_expected_core(baseline, target))


def _fake_projection_outcome(monkeypatch, mutate) -> command.CommandOutcome:
    baseline, target = _pair()
    fake = _valid_core(baseline, target)
    mutate(fake)
    monkeypatch.setattr(
        command.bridge, "compare_recorded_conditions", lambda *a, **k: fake
    )
    return _compare(monkeypatch, baseline, target)


def _set_insufficient_row(
    core: dict,
    dimension: str,
    side: str,
    state: str,
    observations: int,
    nulls: int,
) -> None:
    """Make one row INSUFFICIENT and keep decision/reason consistency."""
    row = _dimension(core, dimension)
    row["status"] = comparability.INSUFF
    row[side] = {
        "present": state != comparability.MISSING,
        "state": state,
        "observation_count": observations,
        "null_observation_count": nulls,
    }
    if row["designation"] == "mandatory":
        core["decision"] = comparability.INSUFFICIENT
        core["reason_codes"] = [
            f"MANDATORY_DIMENSION_INSUFFICIENT:{dimension}"
        ]
    else:
        core["decision"] = comparability.COMPARABLE
        core["reason_codes"] = sorted(
            {
                "ALL_MANDATORY_DIMENSIONS_MATCH",
                f"OPTIONAL_DIMENSION_INSUFFICIENT:{dimension}",
            }
        )
    core["unknown_dimension_count"] = 0
    core["unknown_dimensions"] = []
    core["input_rejected"] = False
    core["input_error_codes"] = []


def test_validation_rejects_empty_dimensions_for_comparable(monkeypatch):
    def mutate(core):
        core["dimensions"] = []

    outcome = _fake_projection_outcome(monkeypatch, mutate)
    _assert_error(outcome, code=command.INTERNAL_ERROR, side=None)
    assert outcome.text == "ERROR INTERNAL_ERROR (exit 2)"


def test_validation_rejects_duplicate_and_missing_dimensions(monkeypatch):
    def mutate(core):
        rows = [
            copy.deepcopy(row)
            for row in core["dimensions"]
            if row["dimension"] != "evaluation_split"
        ]
        duplicate = next(
            row for row in core["dimensions"] if row["dimension"] == "dataset_version"
        )
        rows.append(copy.deepcopy(duplicate))
        core["dimensions"] = rows

    outcome = _fake_projection_outcome(monkeypatch, mutate)
    _assert_error(outcome, code=command.INTERNAL_ERROR, side=None)


def test_validation_rejects_reordered_dimensions(monkeypatch):
    def mutate(core):
        core["dimensions"].reverse()

    outcome = _fake_projection_outcome(monkeypatch, mutate)
    _assert_error(outcome, code=command.INTERNAL_ERROR, side=None)


def test_validation_rejects_absent_side_with_observations(monkeypatch):
    def mutate(core):
        row = _dimension(core, "dataset_identity")
        row["status"] = comparability.INSUFF
        row["baseline"] = {
            "present": False,
            "state": comparability.MISSING,
            "observation_count": 16,
            "null_observation_count": 0,
        }
        core["decision"] = comparability.INSUFFICIENT
        core["reason_codes"] = [
            "MANDATORY_DIMENSION_INSUFFICIENT:dataset_identity"
        ]
        core["unknown_dimension_count"] = 0
        core["unknown_dimensions"] = []
        core["input_rejected"] = False
        core["input_error_codes"] = []

    outcome = _fake_projection_outcome(monkeypatch, mutate)
    _assert_error(outcome, code=command.INTERNAL_ERROR, side=None)


def test_validation_rejects_accepted_input_errors(monkeypatch):
    def mutate(core):
        core["input_error_codes"] = ["SAME_RUN_ID"]

    outcome = _fake_projection_outcome(monkeypatch, mutate)
    _assert_error(outcome, code=command.INTERNAL_ERROR, side=None)


def test_validation_rejects_not_comparable_without_difference(monkeypatch):
    def mutate(core):
        core["decision"] = comparability.NOT_COMPARABLE

    outcome = _fake_projection_outcome(monkeypatch, mutate)
    _assert_error(outcome, code=command.INTERNAL_ERROR, side=None)


def test_validation_rejects_unknown_presence_contradiction(monkeypatch):
    def mutate(core):
        core["unknown_dimensions"] = [
            {
                "reference": "unknown_0001",
                "status": "UNRECOGNIZED",
                "present_on": ["baseline"],
                "baseline": {
                    "present": True,
                    "observation_count": 1,
                    "null_observation_count": 0,
                },
                "target": {
                    "present": True,
                    "observation_count": 1,
                    "null_observation_count": 0,
                },
            }
        ]
        core["unknown_dimension_count"] = 1
        core["decision"] = comparability.INSUFFICIENT
        core["reason_codes"] = ["UNRECOGNIZED_DIMENSION_PRESENT"]

    outcome = _fake_projection_outcome(monkeypatch, mutate)
    _assert_error(outcome, code=command.INTERNAL_ERROR, side=None)


@pytest.mark.parametrize(
    "violation",
    [
        "match-with-non-known-side",
        "unknown-nulls-mismatch",
        "known-with-all-nulls",
        "wrong-designation",
        "comparable-with-mandatory-insufficient",
        "rejected-with-dimensions",
        "rejected-without-input-errors",
        "difference-without-row-reason",
        "unknown-without-reason",
        "unknown-non-sequential-reference",
    ],
)
def test_projection_invariant_violations_are_internal_errors(
    monkeypatch, violation
):
    def mutate(core):
        if violation == "match-with-non-known-side":
            core["dimensions"][0]["target"] = {
                "present": False,
                "state": comparability.MISSING,
                "observation_count": 0,
                "null_observation_count": 0,
            }
        elif violation == "unknown-nulls-mismatch":
            row = _dimension(core, "dataset_identity")
            row["status"] = comparability.INSUFF
            row["baseline"] = {
                "present": True,
                "state": comparability.UNKNOWN,
                "observation_count": 2,
                "null_observation_count": 1,
            }
            core["decision"] = comparability.INSUFFICIENT
            core["reason_codes"] = [
                "MANDATORY_DIMENSION_INSUFFICIENT:dataset_identity"
            ]
        elif violation == "known-with-all-nulls":
            core["dimensions"][0]["baseline"] = {
                "present": True,
                "state": comparability.KNOWN,
                "observation_count": 2,
                "null_observation_count": 2,
            }
        elif violation == "wrong-designation":
            core["dimensions"][0]["designation"] = "optional"
        elif violation == "comparable-with-mandatory-insufficient":
            row = _dimension(core, "dataset_identity")
            row["status"] = comparability.INSUFF
            row["baseline"] = {
                "present": False,
                "state": comparability.MISSING,
                "observation_count": 0,
                "null_observation_count": 0,
            }
            core["reason_codes"] = [
                "ALL_MANDATORY_DIMENSIONS_MATCH",
                "MANDATORY_DIMENSION_INSUFFICIENT:dataset_identity",
            ]
        elif violation == "rejected-with-dimensions":
            core["input_rejected"] = True
        elif violation == "rejected-without-input-errors":
            core.update(
                {
                    "input_rejected": True,
                    "decision": comparability.INSUFFICIENT,
                    "reason_codes": ["MALFORMED_INPUT"],
                    "dimensions": [],
                    "unknown_dimensions": [],
                    "unknown_dimension_count": 0,
                    "input_error_codes": [],
                }
            )
        elif violation == "difference-without-row-reason":
            row = _dimension(core, "dataset_identity")
            row["status"] = comparability.DIFFERENT
            core["decision"] = comparability.NOT_COMPARABLE
            core["reason_codes"] = []
        else:
            core["unknown_dimensions"] = [
                {
                    "reference": (
                        "unknown_0002"
                        if violation == "unknown-non-sequential-reference"
                        else "unknown_0001"
                    ),
                    "status": "UNRECOGNIZED",
                    "present_on": ["baseline"],
                    "baseline": {
                        "present": True,
                        "observation_count": 1,
                        "null_observation_count": 0,
                    },
                    "target": {
                        "present": False,
                        "observation_count": 0,
                        "null_observation_count": 0,
                    },
                }
            ]
            core["unknown_dimension_count"] = 1
            core["decision"] = comparability.INSUFFICIENT
            core["reason_codes"] = (
                []
                if violation == "unknown-without-reason"
                else ["UNRECOGNIZED_DIMENSION_PRESENT"]
            )

    outcome = _fake_projection_outcome(monkeypatch, mutate)
    _assert_error(outcome, code=command.INTERNAL_ERROR, side=None)


@pytest.mark.parametrize("side", ["baseline", "target"])
@pytest.mark.parametrize("dimension", ["dataset_identity", "source_revision"])
@pytest.mark.parametrize(
    "observations,nulls",
    [(1, 0), (2, 1), (16, 15)],
)
def test_conflicting_with_fewer_than_two_non_null_observations_is_rejected(
    monkeypatch, side, dimension, observations, nulls
):
    def mutate(core):
        _set_insufficient_row(
            core,
            dimension,
            side,
            comparability.CONFLICTING,
            observations,
            nulls,
        )

    outcome = _fake_projection_outcome(monkeypatch, mutate)
    _assert_error(outcome, code=command.INTERNAL_ERROR, side=None)


@pytest.mark.parametrize("side", ["baseline", "target"])
@pytest.mark.parametrize("dimension", ["dataset_identity", "source_revision"])
@pytest.mark.parametrize(
    "observations,nulls",
    [(2, 0), (16, 14)],
)
def test_conflicting_with_two_non_null_observations_is_accepted(
    monkeypatch, side, dimension, observations, nulls
):
    def mutate(core):
        _set_insufficient_row(
            core,
            dimension,
            side,
            comparability.CONFLICTING,
            observations,
            nulls,
        )

    outcome = _fake_projection_outcome(monkeypatch, mutate)
    assert outcome.document["format"] == command.RESULT_FORMAT
    row = _dimension(outcome.document["core"], dimension)
    assert row["status"] == comparability.INSUFF
    assert row[side]["state"] == comparability.CONFLICTING
    assert row[side]["observation_count"] == observations
    assert row[side]["null_observation_count"] == nulls
    assert outcome.exit_code == (
        2 if dimension == "dataset_identity" else 0
    )


@pytest.mark.parametrize(
    "dimension",
    [
        "metric_definition",
        "evaluation_protocol",
        "environment",
        "preprocessing",
    ],
)
def test_both_known_insufficient_for_non_string_dimension_is_rejected(
    monkeypatch, dimension
):
    def mutate(core):
        _set_insufficient_row(
            core, dimension, "baseline", comparability.KNOWN, 1, 0
        )

    outcome = _fake_projection_outcome(monkeypatch, mutate)
    _assert_error(outcome, code=command.INTERNAL_ERROR, side=None)


@pytest.mark.parametrize(
    "dimension",
    ["evaluation_split", "source_revision", "dataset_identity"],
)
def test_both_known_insufficient_for_top_level_string_dimension_is_accepted(
    monkeypatch, dimension
):
    def mutate(core):
        _set_insufficient_row(
            core, dimension, "baseline", comparability.KNOWN, 1, 0
        )

    outcome = _fake_projection_outcome(monkeypatch, mutate)
    assert outcome.document["format"] == command.RESULT_FORMAT
    row = _dimension(outcome.document["core"], dimension)
    assert row["status"] == comparability.INSUFF
    assert row["baseline"]["state"] == comparability.KNOWN
    assert row["target"]["state"] == comparability.KNOWN
    assert outcome.exit_code == (
        2 if dimension in ("evaluation_split", "dataset_identity") else 0
    )


@pytest.mark.parametrize("side", ["baseline", "target"])
@pytest.mark.parametrize(
    "state,observations,nulls",
    [
        (comparability.MISSING, 0, 0),
        (comparability.UNKNOWN, 1, 1),
    ],
)
def test_non_string_insufficient_with_one_side_not_known_is_accepted(
    monkeypatch, side, state, observations, nulls
):
    def mutate(core):
        _set_insufficient_row(
            core, "metric_definition", side, state, observations, nulls
        )

    outcome = _fake_projection_outcome(monkeypatch, mutate)
    assert outcome.document["format"] == command.RESULT_FORMAT
    row = _dimension(outcome.document["core"], "metric_definition")
    assert row["status"] == comparability.INSUFF
    assert row[side]["state"] == state
    assert outcome.exit_code == 2


def test_fake_projection_with_raw_reason_value_is_rejected(monkeypatch):
    baseline, target = _pair()
    fake = _valid_core(baseline, target)
    fake["reason_codes"] = [CANARIES["metavalue"]]

    monkeypatch.setattr(
        command.bridge, "compare_recorded_conditions", lambda *a, **k: fake
    )
    outcome = _compare(monkeypatch, baseline, target)
    _assert_error(outcome, code=command.INTERNAL_ERROR, side=None)
    assert CANARIES["metavalue"] not in json.dumps(outcome.document)
    assert CANARIES["metavalue"] not in outcome.text


def test_fake_projection_with_raw_dimension_name_is_rejected(monkeypatch):
    baseline, target = _pair()
    fake = _valid_core(baseline, target)
    fake["dimensions"][0]["dimension"] = CANARIES["path"]

    monkeypatch.setattr(
        command.bridge, "compare_recorded_conditions", lambda *a, **k: fake
    )
    outcome = _compare(monkeypatch, baseline, target)
    _assert_error(outcome, code=command.INTERNAL_ERROR, side=None)
    assert CANARIES["path"] not in json.dumps(outcome.document)


def test_fake_projection_with_raw_error_code_is_rejected(monkeypatch):
    baseline, target = _pair()
    fake = _valid_core(baseline, target)
    fake["input_error_codes"] = [CANARIES["claim"]]

    monkeypatch.setattr(
        command.bridge, "compare_recorded_conditions", lambda *a, **k: fake
    )
    outcome = _compare(monkeypatch, baseline, target)
    _assert_error(outcome, code=command.INTERNAL_ERROR, side=None)
    assert CANARIES["claim"] not in json.dumps(outcome.document)


def test_fake_projection_missing_or_extra_key_is_rejected(monkeypatch):
    baseline, target = _pair()
    for mutate in ("missing", "extra"):
        fake = _valid_core(baseline, target)
        if mutate == "missing":
            fake.pop("notice", None)
            fake.pop("decision", None)
        else:
            fake["raw_value"] = CANARIES["metavalue"]
        monkeypatch.setattr(
            command.bridge,
            "compare_recorded_conditions",
            lambda *a, **k: fake,
        )
        outcome = _compare(monkeypatch, baseline, target)
        _assert_error(outcome, code=command.INTERNAL_ERROR, side=None)
        assert CANARIES["metavalue"] not in json.dumps(outcome.document)


def test_detailed_core_result_is_rejected(monkeypatch):
    baseline, target = _pair()
    document = bridge._build_input(
        baseline,
        target,
        baseline_result_id="res-b",
        target_result_id="res-t",
    )
    detailed = comparability._evaluate_detailed(document)
    assert detailed["decision"] == COMPARABLE
    monkeypatch.setattr(
        command.bridge,
        "compare_recorded_conditions",
        lambda *a, **k: detailed,
    )
    outcome = _compare(monkeypatch, baseline, target)
    _assert_error(outcome, code=command.INTERNAL_ERROR, side=None)
    # The detailed result carried raw values; none may be rendered.
    assert "imagenet" not in json.dumps(outcome.document)
    assert "imagenet" not in outcome.text
