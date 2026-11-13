"""Synthetic tests for the experimental comparability bridge (Stage 1).

These tests are synthetic-only. Every manifest is constructed in memory with
LoadedManifest(manifest=..., path=Path("/synthetic/..."), source_bytes=b"");
no disk manifest is read and no artifact path is opened.
"""

from __future__ import annotations

import builtins
import copy
import importlib
import inspect
import io as io_module
import json
import math
import os
import socket
import subprocess
import traceback
import urllib.request
from pathlib import Path

import pytest

from runevidence import _experimental_comparability_bridge as bridge
from runevidence import comparability
from runevidence.checks import run_checks
from runevidence.findings import Code
from runevidence.manifest import LoadedManifest, load_manifest
from runevidence.models import (
    Artifact,
    EvidenceReference,
    Manifest,
    Project,
    Relationship,
    Result,
    Run,
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
            Artifact(id="raw-shared", path="data/source.csv", sha256=HASH_PARENT),
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


def _pair() -> tuple[LoadedManifest, LoadedManifest]:
    return _loaded("b"), _loaded("t")


def _compare(baseline: LoadedManifest, target: LoadedManifest) -> dict:
    return bridge.compare_recorded_conditions(
        baseline,
        target,
        baseline_result_id=baseline.manifest.results[0].id,
        target_result_id=target.manifest.results[0].id,
    )


def _run(loaded: LoadedManifest) -> Run:
    return loaded.manifest.runs[0]


def _result(loaded: LoadedManifest) -> Result:
    return loaded.manifest.results[0]


def _dataset(loaded: LoadedManifest) -> Artifact:
    return next(a for a in loaded.manifest.artifacts if a.kind == "dataset")


def _checkpoint(loaded: LoadedManifest) -> Artifact:
    return next(a for a in loaded.manifest.artifacts if a.kind == "checkpoint")


def _dimension(projection: dict, name: str) -> dict:
    return next(row for row in projection["dimensions"] if row["dimension"] == name)


def _state(projection: dict, name: str, side: str) -> str:
    return _dimension(projection, name)[side]["state"]


def _error(call) -> bridge.ComparabilityMappingError:
    with pytest.raises(bridge.ComparabilityMappingError) as caught:
        call()
    return caught.value


def _run_metadata(loaded: LoadedManifest) -> dict:
    return _run(loaded).metadata


def _evaluation(loaded: LoadedManifest) -> dict:
    return _run_metadata(loaded)["evaluation"]


def _assert_decision(projection: dict, decision: str) -> None:
    assert projection["decision"] == decision, projection["reason_codes"]


# ------------------------------------------------------------- direction helpers


def _drop_dataset(loaded: LoadedManifest) -> None:
    dataset = _dataset(loaded)
    loaded.manifest.artifacts = [
        a for a in loaded.manifest.artifacts if a.id != dataset.id
    ]
    loaded.manifest.relationships = [
        r
        for r in loaded.manifest.relationships
        if r.source != dataset.id and r.target != dataset.id
    ]


def _drop_identity(loaded: LoadedManifest) -> None:
    _dataset(loaded).metadata.pop("dataset_id", None)


def _drop_version(loaded: LoadedManifest) -> None:
    _dataset(loaded).sha256 = None


def _drop_split(loaded: LoadedManifest) -> None:
    _evaluation(loaded).pop("split", None)


def _drop_metric_definition(loaded: LoadedManifest) -> None:
    _result(loaded).metadata.pop("metric_definition", None)


def _drop_protocol(loaded: LoadedManifest) -> None:
    _evaluation(loaded).pop("protocol", None)


def _drop_preprocessing(loaded: LoadedManifest) -> None:
    dataset = _dataset(loaded)
    loaded.manifest.relationships = [
        r
        for r in loaded.manifest.relationships
        if not (r.source == dataset.id and r.type.value == "derived_from")
    ]


def _drop_revision(loaded: LoadedManifest) -> None:
    _run_metadata(loaded).pop("source_revision", None)


def _drop_environment(loaded: LoadedManifest) -> None:
    _run_metadata(loaded).pop("environment", None)


def _drop_checkpoint(loaded: LoadedManifest) -> None:
    checkpoint = _checkpoint(loaded)
    loaded.manifest.artifacts = [
        a for a in loaded.manifest.artifacts if a.id != checkpoint.id
    ]
    loaded.manifest.relationships = [
        r for r in loaded.manifest.relationships if r.source != checkpoint.id
    ]


# ----------------------------------------------------------- default contract


def test_default_pair_is_comparable_and_policy_is_pinned():
    baseline, target = _pair()
    projection = _compare(baseline, target)
    _assert_decision(projection, COMPARABLE)
    assert projection["format"] == "pc27-comparability-public-v1"
    assert projection["prototype"] is True
    assert projection["integrated_into_rungraph"] is False
    assert projection["integration_status"] == "EXPERIMENTAL_CORE_ONLY"
    assert projection["input_rejected"] is False

    designations = {
        row["dimension"]: row["designation"] for row in projection["dimensions"]
    }
    assert set(designations) == MANDATORY | OPTIONAL
    assert {name for name, d in designations.items() if d == "mandatory"} == MANDATORY
    assert {name for name, d in designations.items() if d == "optional"} == OPTIONAL
    for row in projection["dimensions"]:
        assert row["status"] == "MATCH"


def test_output_is_exactly_the_core_public_projection():
    baseline, target = _pair()
    document = bridge._build_input(
        baseline,
        target,
        baseline_result_id="res-b",
        target_result_id="res-t",
    )
    expected = comparability.evaluate(document)
    actual = _compare(baseline, target)
    assert actual == expected
    assert set(actual) == {
        "format",
        "prototype",
        "integrated_into_rungraph",
        "integration_status",
        "decision",
        "reason_codes",
        "dimensions",
        "unknown_dimension_count",
        "unknown_dimensions",
        "input_rejected",
        "input_error_codes",
    }
    assert comparability.render(actual).startswith("{")


def test_policy_is_omitted_and_dimension_order_is_fixed():
    baseline, target = _pair()
    document = bridge._build_input(
        baseline,
        target,
        baseline_result_id="res-b",
        target_result_id="res-t",
    )
    assert "policy" not in document
    assert document["format"] == comparability.FORMAT_IN
    assert list(document["baseline"]["dimensions"]) == list(bridge.DIMENSION_ORDER)
    assert list(document["target"]["dimensions"]) == list(bridge.DIMENSION_ORDER)
    for dimension, entry in document["baseline"]["dimensions"].items():
        assert set(entry) == {"observations"}
        assert len(entry["observations"]) == 1
        assert set(entry["observations"][0]) == {"value", "field", "basis"}
        assert entry["observations"][0]["field"] == bridge.FIELDS[dimension]
        assert entry["observations"][0]["basis"] == bridge.BASIS[dimension]


# ------------------------------------------------------------------ selection


def test_selects_the_result_of_the_second_run():
    baseline, target = _pair()
    baseline.manifest.runs.insert(
        0,
        Run(
            id="run-first",
            metadata={
                "evaluation": {
                    "split": "train",
                    "protocol": {"protocol_id": "old"},
                },
                "source_revision": "old",
                "environment": {"python": "2.7"},
            },
        ),
    )
    baseline.manifest.results.insert(
        0,
        Result(id="res-first", run_id="run-first", name="loss"),
    )
    projection = bridge.compare_recorded_conditions(
        baseline,
        target,
        baseline_result_id="res-b",
        target_result_id="res-t",
    )
    _assert_decision(projection, COMPARABLE)
    assert _state(projection, "evaluation_split", "baseline") == "KNOWN"

    # Selecting the first run's result must not produce the same answer.
    other = bridge.compare_recorded_conditions(
        baseline,
        target,
        baseline_result_id="res-first",
        target_result_id="res-t",
    )
    assert other["decision"] != COMPARABLE


def test_missing_owning_run_is_reported():
    baseline, _ = _pair()
    _result(baseline).run_id = "run-missing"
    error = _error(lambda: _compare(baseline, _loaded("t")))
    assert error.code == bridge.RUN_NOT_FOUND
    assert error.side == "baseline"


def test_duplicate_run_ids_are_selection_ambiguous():
    baseline, target = _pair()
    baseline.manifest.runs.append(Run(id="run-b", metadata={}))
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.RUN_SELECTION_AMBIGUOUS
    assert error.side == "baseline"


def test_duplicate_result_ids_are_selection_ambiguous():
    baseline, target = _pair()
    baseline.manifest.results.append(
        Result(id="res-b", run_id="run-b", name="duplicate")
    )
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.RESULT_SELECTION_AMBIGUOUS
    assert error.side == "baseline"


@pytest.mark.parametrize(
    "duplicate_kind",
    ["project-run", "run-artifact", "result-evidence", "artifact-artifact"],
)
def test_cross_kind_and_same_kind_duplicate_ids(duplicate_kind):
    baseline, target = _pair()
    if duplicate_kind == "project-run":
        baseline.manifest.project.id = "run-b"
    elif duplicate_kind == "run-artifact":
        baseline.manifest.artifacts.append(
            Artifact(id="run-b", path="collision.bin")
        )
    elif duplicate_kind == "result-evidence":
        baseline.manifest.evidence.append(
            EvidenceReference(id="res-b", claim="synthetic")
        )
    else:
        baseline.manifest.artifacts.append(
            Artifact(id="ds-b", path="collision.bin")
        )
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.DUPLICATE_ID
    assert error.side == "baseline"


def test_dangling_result_of_target():
    baseline, target = _pair()
    baseline.manifest.relationships.append(
        Relationship(source="res-b", target="run-ghost", type="result_of")
    )
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.DANGLING_REFERENCE
    assert error.side == "baseline"


def test_wrong_kind_result_of_target():
    baseline, target = _pair()
    baseline.manifest.relationships.append(
        Relationship(source="res-b", target="ds-b", type="result_of")
    )
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.DANGLING_REFERENCE


def test_conflicting_result_of_other_run():
    baseline, target = _pair()
    baseline.manifest.runs.append(Run(id="run-other", metadata={}))
    baseline.manifest.relationships.append(
        Relationship(source="res-b", target="run-other", type="result_of")
    )
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.CONFLICTING_OWNERSHIP


def test_multiple_result_of_targets_conflict():
    baseline, target = _pair()
    baseline.manifest.runs.append(Run(id="run-other", metadata={}))
    baseline.manifest.relationships.extend(
        [
            Relationship(source="res-b", target="run-b", type="result_of"),
            Relationship(source="res-b", target="run-other", type="result_of"),
        ]
    )
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.CONFLICTING_OWNERSHIP


def test_repeated_result_of_edges_are_deduplicated():
    baseline, target = _pair()
    baseline.manifest.relationships.extend(
        [
            Relationship(source="res-b", target="run-b", type="result_of"),
            Relationship(source="res-b", target="run-b", type="result_of"),
        ]
    )
    _assert_decision(_compare(baseline, target), COMPARABLE)


def test_repeated_attachment_edges_are_deduplicated():
    baseline, target = _pair()
    baseline.manifest.relationships.extend(
        [
            Relationship(source="ds-b", target="run-b", type="input_to"),
            Relationship(source="ckpt-b", target="run-b", type="input_to"),
        ]
    )
    projection = _compare(baseline, target)
    _assert_decision(projection, COMPARABLE)
    assert _state(projection, "dataset_identity", "baseline") == "KNOWN"


def test_producer_only_attachment_is_not_consumption():
    baseline, target = _pair()
    dataset = _dataset(baseline)
    baseline.manifest.relationships = [
        r
        for r in baseline.manifest.relationships
        if not (r.source == dataset.id and r.type.value == "input_to")
    ]
    dataset.run_id = "run-b"  # produced by, not consumed by
    projection = _compare(baseline, target)
    _assert_decision(projection, INSUFFICIENT)
    row = _dimension(projection, "dataset_identity")
    assert row["baseline"]["present"] is False
    assert row["baseline"]["state"] == "MISSING"


@pytest.mark.parametrize("selector", ["", "   ", "\t\n"])
def test_blank_selector_is_invalid(selector):
    baseline, target = _pair()
    error = _error(
        lambda: bridge.compare_recorded_conditions(
            baseline,
            target,
            baseline_result_id=selector,
            target_result_id="res-t",
        )
    )
    assert error.code == bridge.INVALID_SELECTOR
    assert error.side == "baseline"


@pytest.mark.parametrize("selector", [None, 1, True, b"res-b", ["res-b"], {"id": "res-b"}])
def test_non_string_selector_is_invalid(selector):
    baseline, target = _pair()
    error = _error(
        lambda: bridge.compare_recorded_conditions(
            baseline,
            target,
            baseline_result_id="res-b",
            target_result_id=selector,
        )
    )
    assert error.code == bridge.INVALID_SELECTOR
    assert error.side == "target"


def test_unknown_selector_is_not_found():
    baseline, target = _pair()
    error = _error(
        lambda: bridge.compare_recorded_conditions(
            baseline,
            target,
            baseline_result_id="res-unknown",
            target_result_id="res-t",
        )
    )
    assert error.code == bridge.RESULT_NOT_FOUND
    assert error.side == "baseline"


def test_selector_is_not_trimmed():
    baseline, target = _pair()
    error = _error(
        lambda: bridge.compare_recorded_conditions(
            baseline,
            target,
            baseline_result_id=" res-b",
            target_result_id="res-t",
        )
    )
    assert error.code == bridge.RESULT_NOT_FOUND


def test_selector_is_not_case_folded():
    baseline, target = _pair()
    error = _error(
        lambda: bridge.compare_recorded_conditions(
            baseline,
            target,
            baseline_result_id="RES-B",
            target_result_id="res-t",
        )
    )
    assert error.code == bridge.RESULT_NOT_FOUND


def test_baseline_failure_wins_over_target_failure():
    baseline, target = _pair()
    error = _error(
        lambda: bridge.compare_recorded_conditions(
            baseline,
            target,
            baseline_result_id="missing-b",
            target_result_id="  ",
        )
    )
    assert error.code == bridge.RESULT_NOT_FOUND
    assert error.side == "baseline"


def test_target_structural_failure_reports_target():
    baseline, target = _pair()
    target.manifest.relationships.append(
        Relationship(source="ghost", target="run-t", type="input_to")
    )
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.DANGLING_REFERENCE
    assert error.side == "target"


def test_dangling_input_to_source():
    baseline, target = _pair()
    baseline.manifest.relationships.append(
        Relationship(source="ghost", target="run-b", type="input_to")
    )
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.DANGLING_REFERENCE


def test_wrong_kind_input_to_source():
    baseline, target = _pair()
    baseline.manifest.relationships.append(
        Relationship(source="res-b", target="run-b", type="input_to")
    )
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.DANGLING_REFERENCE


def test_dangling_derived_from_target():
    baseline, target = _pair()
    baseline.manifest.relationships.append(
        Relationship(source="ds-b", target="ghost", type="derived_from")
    )
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.DANGLING_REFERENCE


def test_wrong_kind_derived_from_target():
    baseline, target = _pair()
    baseline.manifest.relationships.append(
        Relationship(source="ds-b", target="run-b", type="derived_from")
    )
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.DANGLING_REFERENCE


def test_same_run_ids_across_manifests_preserve_core_rejection():
    baseline, target = _pair()
    _run(target).id = "run-b"
    _result(target).run_id = "run-b"
    for relationship in target.manifest.relationships:
        if relationship.target == "run-t":
            relationship.target = "run-b"
    projection = _compare(baseline, target)
    assert projection["input_rejected"] is True
    assert projection["decision"] == INSUFFICIENT
    assert "SAME_RUN_ID" in projection["input_error_codes"]
    assert "MALFORMED_INPUT" in projection["reason_codes"]


# ------------------------------------------------------ dataset and checkpoint


def test_dataset_identity_has_no_artifact_id_fallback():
    baseline, target = _pair()
    dataset = _dataset(baseline)
    dataset.id = "imagenet-semantic"
    for relationship in baseline.manifest.relationships:
        if relationship.source == "ds-b":
            relationship.source = "imagenet-semantic"
    dataset.metadata.pop("dataset_id")
    _dataset(target).metadata["dataset_id"] = "imagenet-semantic"
    projection = _compare(baseline, target)
    _assert_decision(projection, INSUFFICIENT)
    row = _dimension(projection, "dataset_identity")
    assert row["baseline"]["state"] == "MISSING"
    assert row["target"]["state"] == "KNOWN"


@pytest.mark.parametrize("absent", [True, False])
def test_dataset_identity_absent_or_null_is_missing(absent):
    baseline, target = _pair()
    if absent:
        _dataset(baseline).metadata.pop("dataset_id")
    else:
        _dataset(baseline).metadata["dataset_id"] = None
    projection = _compare(baseline, target)
    _assert_decision(projection, INSUFFICIENT)
    assert _state(projection, "dataset_identity", "baseline") == "MISSING"


@pytest.mark.parametrize("identity", ["", "   ", 5, True, [], {}, 1.5])
def test_dataset_identity_blank_or_wrong_type_is_rejected(identity):
    baseline, target = _pair()
    _dataset(baseline).metadata["dataset_id"] = identity
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert error.side == "baseline"


def test_dataset_version_missing_hash_is_missing():
    baseline, target = _pair()
    _dataset(baseline).sha256 = None
    _dataset(target).sha256 = None
    projection = _compare(baseline, target)
    _assert_decision(projection, INSUFFICIENT)
    assert _state(projection, "dataset_version", "baseline") == "MISSING"


def test_volatile_hash_is_ignored_and_never_causes_a_difference():
    baseline, target = _pair()
    for loaded, sha in ((baseline, HASH_DATASET), (target, HASH_OTHER)):
        dataset = _dataset(loaded)
        dataset.volatile = True
        dataset.sha256 = sha
    projection = _compare(baseline, target)
    _assert_decision(projection, INSUFFICIENT)
    row = _dimension(projection, "dataset_version")
    assert row["baseline"]["state"] == "MISSING"
    assert row["target"]["state"] == "MISSING"
    assert "MANDATORY_DIMENSION_INSUFFICIENT:dataset_version" in projection["reason_codes"]
    assert "MANDATORY_DIMENSION_DIFFERENT:dataset_version" not in projection["reason_codes"]


def test_directory_dataset_is_rejected():
    baseline, target = _pair()
    dataset = _dataset(baseline)
    dataset.artifact_type = "directory"
    dataset.digest_format = "runevidence-directory-v1"
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED


@pytest.mark.parametrize("same_semantic_id", [True, False])
@pytest.mark.parametrize("reversed_order", [False, True])
def test_two_datasets_are_unsupported(same_semantic_id, reversed_order):
    baseline, target = _pair()
    extra = Artifact(
        id="ds-extra",
        path="data/extra.csv",
        kind="dataset",
        sha256=HASH_OTHER,
        metadata={"dataset_id": "imagenet" if same_semantic_id else "other"},
    )
    baseline.manifest.artifacts.append(extra)
    baseline.manifest.relationships.append(
        Relationship(source="ds-extra", target="run-b", type="input_to")
    )
    if reversed_order:
        baseline.manifest.artifacts.reverse()
        baseline.manifest.relationships.reverse()
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED


def test_extra_unhashed_dataset_is_rejected_not_silently_dropped():
    baseline, target = _pair()
    baseline.manifest.artifacts.append(
        Artifact(
            id="ds-extra",
            path="data/extra.csv",
            kind="dataset",
            sha256=None,
            metadata={"dataset_id": "imagenet"},
        )
    )
    baseline.manifest.relationships.append(
        Relationship(source="ds-extra", target="run-b", type="input_to")
    )
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED


def test_reversed_preprocessing_direction_is_ignored():
    baseline, target = _pair()
    for loaded in (baseline, target):
        dataset = _dataset(loaded)
        loaded.manifest.relationships = [
            r
            for r in loaded.manifest.relationships
            if not (r.source == dataset.id and r.type.value == "derived_from")
        ]
        loaded.manifest.relationships.append(
            Relationship(
                source="raw-shared", target=dataset.id, type="derived_from"
            )
        )
    projection = _compare(baseline, target)
    _assert_decision(projection, COMPARABLE)
    row = _dimension(projection, "preprocessing")
    assert row["baseline"]["present"] is False
    assert row["baseline"]["state"] == "MISSING"
    assert row["target"]["present"] is False
    assert row["target"]["state"] == "MISSING"


def test_checkpoint_difference_is_optional():
    baseline, target = _pair()
    _checkpoint(target).sha256 = HASH_OTHER
    projection = _compare(baseline, target)
    _assert_decision(projection, COMPARABLE)
    assert _dimension(projection, "model_checkpoint")["status"] == "DIFFERENT"


def test_missing_checkpoint_is_optional():
    baseline, target = _pair()
    _drop_checkpoint(target)
    projection = _compare(baseline, target)
    _assert_decision(projection, COMPARABLE)
    row = _dimension(projection, "model_checkpoint")
    assert row["target"]["present"] is False
    assert row["target"]["state"] == "MISSING"


def test_checkpoint_without_hash_is_missing():
    baseline, target = _pair()
    _checkpoint(baseline).sha256 = None
    _checkpoint(target).sha256 = None
    projection = _compare(baseline, target)
    _assert_decision(projection, COMPARABLE)
    assert _state(projection, "model_checkpoint", "baseline") == "MISSING"
    assert _state(projection, "model_checkpoint", "target") == "MISSING"


def test_directory_checkpoint_is_rejected():
    baseline, target = _pair()
    checkpoint = _checkpoint(baseline)
    checkpoint.artifact_type = "directory"
    checkpoint.digest_format = "runevidence-directory-v1"
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED


def test_two_checkpoints_are_unsupported():
    baseline, target = _pair()
    baseline.manifest.artifacts.append(
        Artifact(
            id="ckpt-extra",
            path="models/extra.bin",
            kind="checkpoint",
            sha256=HASH_OTHER,
        )
    )
    baseline.manifest.relationships.append(
        Relationship(source="ckpt-extra", target="run-b", type="input_to")
    )
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED


def test_semantic_dataset_identity_matches_despite_local_ids():
    baseline, target = _pair()
    assert _dataset(baseline).id != _dataset(target).id
    projection = _compare(baseline, target)
    _assert_decision(projection, COMPARABLE)
    assert _state(projection, "dataset_identity", "baseline") == "KNOWN"


# ------------------------------------------------------------ metadata shapes


def test_split_absent_is_missing():
    baseline, target = _pair()
    _drop_split(baseline)
    projection = _compare(baseline, target)
    _assert_decision(projection, INSUFFICIENT)
    assert _state(projection, "evaluation_split", "baseline") == "MISSING"


def test_split_null_is_unknown():
    baseline, target = _pair()
    _evaluation(baseline)["split"] = None
    projection = _compare(baseline, target)
    _assert_decision(projection, INSUFFICIENT)
    row = _dimension(projection, "evaluation_split")
    assert row["baseline"]["state"] == "UNKNOWN"
    assert row["baseline"]["null_observation_count"] == 1


def test_split_blank_is_passed_through_as_ambiguous():
    baseline, target = _pair()
    _evaluation(baseline)["split"] = ""
    _evaluation(target)["split"] = ""
    projection = _compare(baseline, target)
    _assert_decision(projection, INSUFFICIENT)
    assert _state(projection, "evaluation_split", "baseline") == "AMBIGUOUS"


@pytest.mark.parametrize("split", [5, True, [], {}, 1.5])
def test_split_wrong_type_is_rejected(split):
    baseline, target = _pair()
    _evaluation(baseline)["split"] = split
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert error.side == "baseline"


@pytest.mark.parametrize("evaluation", [None, "test", [], 7, False])
def test_evaluation_container_must_be_an_object(evaluation):
    baseline, target = _pair()
    _run_metadata(baseline)["evaluation"] = evaluation
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert error.side == "baseline"


def test_protocol_absent_is_missing():
    baseline, target = _pair()
    _drop_protocol(baseline)
    projection = _compare(baseline, target)
    _assert_decision(projection, INSUFFICIENT)
    assert _state(projection, "evaluation_protocol", "baseline") == "MISSING"


def test_protocol_null_is_unknown():
    baseline, target = _pair()
    _evaluation(baseline)["protocol"] = None
    projection = _compare(baseline, target)
    _assert_decision(projection, INSUFFICIENT)
    assert _state(projection, "evaluation_protocol", "baseline") == "UNKNOWN"


@pytest.mark.parametrize(
    "protocol",
    [
        False,
        0,
        [None],
        "protocol",
        {},
        {"protocol_id": ""},
        {"protocol_id": "   "},
        {"protocol_id": None},
        {"protocol_id": 5},
        {"protocol_id": "p", "extra": 1},
        {"protocol_id": "p", "version": True},
        {"protocol_id": "p", "version": ""},
        {"protocol_id": "p", "version": [1]},
    ],
)
def test_protocol_bypass_values_are_rejected(protocol):
    baseline, target = _pair()
    _evaluation(baseline)["protocol"] = protocol
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert error.side == "baseline"


@pytest.mark.parametrize("version", [1, "1", "v1"])
def test_protocol_version_forms_are_accepted(version):
    baseline, target = _pair()
    _evaluation(baseline)["protocol"] = {"protocol_id": "p", "version": version}
    _evaluation(target)["protocol"] = {"protocol_id": "p", "version": version}
    _assert_decision(_compare(baseline, target), COMPARABLE)


@pytest.mark.parametrize("absent", [True, False])
def test_source_revision_absent_null_blank_and_wrong_type(absent):
    baseline, target = _pair()
    if absent:
        _drop_revision(baseline)
        projection = _compare(baseline, target)
        _assert_decision(projection, COMPARABLE)
        assert _state(projection, "source_revision", "baseline") == "MISSING"
    else:
        _run_metadata(baseline)["source_revision"] = None
        projection = _compare(baseline, target)
        _assert_decision(projection, COMPARABLE)
        assert _state(projection, "source_revision", "baseline") == "UNKNOWN"


def test_source_revision_blank_is_ambiguous():
    baseline, target = _pair()
    _run_metadata(baseline)["source_revision"] = "  "
    _run_metadata(target)["source_revision"] = "  "
    projection = _compare(baseline, target)
    _assert_decision(projection, COMPARABLE)
    assert _state(projection, "source_revision", "baseline") == "AMBIGUOUS"


@pytest.mark.parametrize("revision", [5, True, [], {}])
def test_source_revision_wrong_type_is_rejected(revision):
    baseline, target = _pair()
    _run_metadata(baseline)["source_revision"] = revision
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED


def test_environment_absent_is_missing_but_optional():
    baseline, target = _pair()
    _drop_environment(baseline)
    projection = _compare(baseline, target)
    _assert_decision(projection, COMPARABLE)
    assert _state(projection, "environment", "baseline") == "MISSING"


def test_environment_null_is_unknown_but_optional():
    baseline, target = _pair()
    _run_metadata(baseline)["environment"] = None
    projection = _compare(baseline, target)
    _assert_decision(projection, COMPARABLE)
    assert _state(projection, "environment", "baseline") == "UNKNOWN"


def test_environment_empty_object_is_rejected():
    baseline, target = _pair()
    _run_metadata(baseline)["environment"] = {}
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED


@pytest.mark.parametrize("environment", ["3.12", [], 7, False, {"python": object()}])
def test_environment_wrong_type_is_rejected(environment):
    baseline, target = _pair()
    _run_metadata(baseline)["environment"] = environment
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_environment_nonfinite_is_rejected(value):
    baseline, target = _pair()
    _run_metadata(baseline)["environment"] = {"python": value}
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED


def _nested(depth: int) -> dict:
    value = {"leaf": 1}
    for _ in range(depth - 1):
        value = {"n": value}
    return value


def test_environment_depth_boundary():
    baseline, target = _pair()
    _run_metadata(baseline)["environment"] = _nested(8)
    _run_metadata(target)["environment"] = _nested(8)
    _assert_decision(_compare(baseline, target), COMPARABLE)

    baseline, target = _pair()
    _run_metadata(baseline)["environment"] = _nested(9)
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED


def test_environment_serialized_size_bound():
    baseline, target = _pair()
    environment = {f"k{index:03d}": "v" * 150 for index in range(120)}
    _run_metadata(baseline)["environment"] = environment
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_BOUND_EXCEEDED
    assert error.side == "baseline"


def test_metric_definition_absent_is_missing():
    baseline, target = _pair()
    _drop_metric_definition(baseline)
    projection = _compare(baseline, target)
    _assert_decision(projection, INSUFFICIENT)
    assert _state(projection, "metric_definition", "baseline") == "MISSING"


def test_metric_definition_null_is_unknown():
    baseline, target = _pair()
    _result(baseline).metadata["metric_definition"] = None
    projection = _compare(baseline, target)
    _assert_decision(projection, INSUFFICIENT)
    assert _state(projection, "metric_definition", "baseline") == "UNKNOWN"


@pytest.mark.parametrize(
    "definition",
    [
        {},
        {"name": "accuracy"},
        {"display_name": "accuracy"},
        {"label": "acc"},
        {"name": "accuracy", "label": "acc"},
    ],
)
def test_metric_definition_diagnostics_are_ambiguous(definition):
    baseline, target = _pair()
    _result(baseline).metadata["metric_definition"] = copy.deepcopy(definition)
    _result(target).metadata["metric_definition"] = copy.deepcopy(definition)
    projection = _compare(baseline, target)
    _assert_decision(projection, INSUFFICIENT)
    assert _state(projection, "metric_definition", "baseline") == "AMBIGUOUS"


@pytest.mark.parametrize("definition", [{}, {"name": "accuracy"}])
def test_metric_definition_diagnostics_still_reach_the_core(definition):
    baseline, target = _pair()
    _result(baseline).metadata["metric_definition"] = copy.deepcopy(definition)
    _result(target).metadata["metric_definition"] = copy.deepcopy(definition)
    projection = _compare(baseline, target)
    assert projection["input_rejected"] is False
    _assert_decision(projection, INSUFFICIENT)
    assert _state(projection, "metric_definition", "baseline") == "AMBIGUOUS"


@pytest.mark.parametrize(
    "definition",
    [
        {"name": object()},
        {"display_name": {"nested": [object()]}},
        {"label": [1, object()]},
    ],
)
def test_metric_definition_name_only_non_json_value_is_rejected(definition):
    baseline, target = _pair()
    _result(baseline).metadata["metric_definition"] = definition
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert error.side == "baseline"
    assert error.args == (bridge.INPUT_SHAPE_UNSUPPORTED,)


def test_metric_definition_nested_non_json_value_is_rejected():
    baseline, target = _pair()
    _result(baseline).metadata["metric_definition"] = {
        "name": {"nested": {"deeper": [object()]}}
    }
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert error.side == "baseline"


def test_metric_definition_canonical_size_bound_before_deepcopy():
    baseline, target = _pair()
    _result(baseline).metadata["metric_definition"] = {
        "name": ["x" * 1024 for _ in range(70)]
    }
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_BOUND_EXCEEDED
    assert error.side == "baseline"
    assert error.args == (bridge.INPUT_BOUND_EXCEEDED,)


@pytest.mark.parametrize(
    "definition",
    [
        {"name": "accuracy", "note": ""},
        {"unit": None},
        {"irrelevant": False},
        {"definition_id": None},
        {"definition_id": ""},
        {"definition_id": "   "},
        {"definition_id": 5},
        {"definition_id": "d", "extra": 1},
        {"definition_id": "d", "higher_is_better": "yes"},
        {"definition_id": "d", "higher_is_better": 1},
        {"definition_id": "d", "averaging": ""},
        {"definition_id": "d", "averaging": None},
        {"definition_id": "d", "version": True},
        {"definition_id": "d", "version": ""},
        {"definition_id": "d", "version": [2]},
        ["name"],
        "accuracy",
        5,
        False,
    ],
)
def test_metric_definition_bypass_values_are_rejected(definition):
    baseline, target = _pair()
    _result(baseline).metadata["metric_definition"] = copy.deepcopy(definition)
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert error.side == "baseline"


@pytest.mark.parametrize(
    "definition",
    [
        {"definition_id": "d"},
        {"definition_id": "d", "higher_is_better": False},
        {"definition_id": "d", "averaging": "macro"},
        {"definition_id": "d", "version": 2},
        {"definition_id": "d", "version": "2"},
        {
            "definition_id": "d",
            "higher_is_better": True,
            "averaging": "macro",
            "version": 2,
        },
    ],
)
def test_metric_definition_supported_shapes_are_accepted(definition):
    baseline, target = _pair()
    _result(baseline).metadata["metric_definition"] = copy.deepcopy(definition)
    _result(target).metadata["metric_definition"] = copy.deepcopy(definition)
    _assert_decision(_compare(baseline, target), COMPARABLE)


def _nonfinite_in_run_extra(loaded):
    _run_metadata(loaded)["unrelated"] = {"value": math.nan}


def _nonfinite_in_result_metadata(loaded):
    _result(loaded).metadata["metric_definition"] = {"name": math.inf}


def _nonfinite_in_dataset_metadata(loaded):
    _dataset(loaded).metadata["unused"] = float("-inf")


@pytest.mark.parametrize(
    "inject",
    [
        _nonfinite_in_run_extra,
        _nonfinite_in_result_metadata,
        _nonfinite_in_dataset_metadata,
    ],
)
def test_nonfinite_selected_condition_metadata_is_rejected(inject):
    baseline, target = _pair()
    inject(baseline)
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert error.side == "baseline"


def test_unknown_metadata_keys_are_ignored():
    baseline, target = _pair()
    _run_metadata(baseline)["unrelated"] = {"nested": [1, 2, 3]}
    _result(baseline).metadata["another_unrelated"] = "ignored"
    _dataset(baseline).metadata["third_unrelated"] = False
    projection = _compare(baseline, target)
    _assert_decision(projection, COMPARABLE)
    assert projection["unknown_dimension_count"] == 0
    assert {row["dimension"] for row in projection["dimensions"]} == (
        MANDATORY | OPTIONAL
    )
    # The injected key names must not become dimensions.
    assert "unrelated" not in {
        row["dimension"] for row in projection["dimensions"]
    }


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_result_values_are_irrelevant(value):
    baseline, target = _pair()
    _result(baseline).value = value
    _result(target).value = value
    _assert_decision(_compare(baseline, target), COMPARABLE)


# --------------------------------------------------------------------- bounds


def test_observation_bound_is_enforced():
    observations = [
        {"value": index, "field": "f", "basis": "proposed_extension"}
        for index in range(17)
    ]
    document = {
        "format": bridge.FORMAT,
        "baseline": {
            "dimensions": {"evaluation_split": {"observations": observations}}
        },
        "target": {"dimensions": {}},
    }
    error = _error(lambda: bridge._require_bounds(document))
    assert error.code == bridge.INPUT_BOUND_EXCEEDED
    assert error.side == "baseline"


def test_string_bound_is_enforced_through_public_api():
    baseline, target = _pair()
    _evaluation(baseline)["split"] = "x" * 1025
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_BOUND_EXCEEDED
    assert error.side == "baseline"


def test_depth_bound_is_enforced():
    deep: list = []
    node = deep
    for _ in range(20):
        child: list = []
        node.append(child)
        node = child
    document = {
        "format": bridge.FORMAT,
        "baseline": {
            "dimensions": {
                "environment": {
                    "observations": [
                        {"value": deep, "field": "f", "basis": "proposed_extension"}
                    ]
                }
            }
        },
        "target": {"dimensions": {}},
    }
    error = _error(lambda: bridge._require_bounds(document))
    assert error.code == bridge.INPUT_BOUND_EXCEEDED
    assert error.side == "baseline"


def test_total_input_size_bound_is_enforced():
    baseline, target = _pair()
    dataset = _dataset(baseline)
    for index in range(70):
        artifact_id = f"{index:03d}" + "a" * 1021
        baseline.manifest.artifacts.append(
            Artifact(id=artifact_id, path=f"parents/{index}.csv")
        )
        baseline.manifest.relationships.append(
            Relationship(
                source=dataset.id, target=artifact_id, type="derived_from"
            )
        )
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_BOUND_EXCEEDED


def test_preprocessing_aggregate_bound_precedes_sort_and_copy(monkeypatch):
    baseline, target = _pair()
    dataset = _dataset(baseline)
    for index in range(70):
        artifact_id = f"{index:03d}" + "a" * 1021
        baseline.manifest.artifacts.append(
            Artifact(id=artifact_id, path=f"parents/{index}.csv")
        )
        baseline.manifest.relationships.append(
            Relationship(
                source=dataset.id, target=artifact_id, type="derived_from"
            )
        )

    calls: list[str] = []

    def trap(*args, **kwargs):
        calls.append("hook")
        raise AssertionError("sort/copy ran before the aggregate bound")

    monkeypatch.setattr(bridge, "_ordered_ids", trap)
    monkeypatch.setattr(bridge, "_copy_json_exact", trap)

    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_BOUND_EXCEEDED
    assert error.side == "baseline"
    assert calls == []


def test_enclosing_record_bound_precedes_copy_and_sort(monkeypatch):
    baseline, target = _pair()
    definition = {"name": ["x" * 1024 for _ in range(63)]}
    assert bridge._json_size(definition) <= bridge.MAX_INPUT_BYTES
    _result(baseline).metadata["metric_definition"] = definition

    calls: list[str] = []

    def trap(*args, **kwargs):
        calls.append("hook")
        raise AssertionError("copy/sort ran before the record size bound")

    monkeypatch.setattr(bridge, "_ordered_ids", trap)
    monkeypatch.setattr(bridge, "_copy_json_exact", trap)

    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_BOUND_EXCEEDED
    assert error.side == "baseline"
    assert calls == []


def _provisional(loaded, selector: str, side: str) -> dict:
    context = bridge._analyse_side(loaded, selector, side)
    plan = bridge._shape_side(context, side)
    return bridge._bound_side(plan, context, side)


def test_combined_overflow_precedes_all_copy_and_sort(monkeypatch):
    baseline, target = _pair()
    for loaded in (baseline, target):
        _drop_preprocessing(loaded)
        _result(loaded).metadata["metric_definition"] = {
            "name": ["x" * 1024 for _ in range(39)]
        }
        _result(loaded).name = "accuracy" + "a" * 43

    baseline_provisional = _provisional(baseline, "res-b", "baseline")
    target_provisional = _provisional(target, "res-t", "target")
    combined = {
        "format": bridge.FORMAT,
        "baseline": baseline_provisional,
        "target": target_provisional,
    }
    assert bridge._json_size(baseline_provisional) <= bridge.MAX_INPUT_BYTES
    assert bridge._json_size(target_provisional) <= bridge.MAX_INPUT_BYTES
    assert bridge._json_size(combined) == 82_824

    calls: list[str] = []

    def trap(*args, **kwargs):
        calls.append("hook")
        raise AssertionError("copy/sort ran before the combined bound")

    monkeypatch.setattr(bridge, "_copy_json_exact", trap)
    monkeypatch.setattr(bridge, "_ordered_ids", trap)

    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_BOUND_EXCEEDED
    assert error.side == "target"
    assert calls == []


def test_environment_size_bound_uses_unsorted_serialization(monkeypatch):
    baseline, target = _pair()
    environment = _oversized_environment()
    assert bridge._json_size(environment) == 19201
    _run_metadata(baseline)["environment"] = environment

    seen_sort_flags: list[object] = []
    original_dumps = json.dumps

    def spy(*args, **kwargs):
        seen_sort_flags.append(kwargs.get("sort_keys"))
        return original_dumps(*args, **kwargs)

    monkeypatch.setattr(json, "dumps", spy)
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_BOUND_EXCEEDED
    assert error.side == "baseline"
    assert seen_sort_flags
    assert True not in seen_sort_flags
    assert not hasattr(bridge, "_canonical")


def test_private_input_plain_json_is_insertion_order_independent():
    baseline_a, target_a = _pair()
    baseline_b, target_b = _pair()

    environment_a = {"outer": {"b": 2, "a": 1}, "z": 3}
    environment_b = {"z": 3, "outer": {"a": 1, "b": 2}}
    for loaded in (baseline_a, target_a):
        _run_metadata(loaded)["environment"] = copy.deepcopy(environment_a)
    for loaded in (baseline_b, target_b):
        _run_metadata(loaded)["environment"] = copy.deepcopy(environment_b)

    for loaded in (baseline_a, target_a):
        protocol = _evaluation(loaded)["protocol"]
        _evaluation(loaded)["protocol"] = {
            "version": protocol["version"],
            "protocol_id": protocol["protocol_id"],
        }
        definition = _result(loaded).metadata["metric_definition"]
        _result(loaded).metadata["metric_definition"] = {
            "higher_is_better": definition["higher_is_better"],
            "definition_id": definition["definition_id"],
        }

    for loaded in (baseline_b, target_b):
        protocol = _evaluation(loaded)["protocol"]
        _evaluation(loaded)["protocol"] = {
            "protocol_id": protocol["protocol_id"],
            "version": protocol["version"],
        }
        definition = _result(loaded).metadata["metric_definition"]
        _result(loaded).metadata["metric_definition"] = {
            "definition_id": definition["definition_id"],
            "higher_is_better": definition["higher_is_better"],
        }

    first = bridge._build_input(
        baseline_a,
        target_a,
        baseline_result_id="res-b",
        target_result_id="res-t",
    )
    second = bridge._build_input(
        baseline_b,
        target_b,
        baseline_result_id="res-b",
        target_result_id="res-t",
    )
    third = bridge._build_input(
        baseline_a,
        target_a,
        baseline_result_id="res-b",
        target_result_id="res-t",
    )
    assert json.dumps(first) == json.dumps(second)
    assert json.dumps(first) == json.dumps(third)


@pytest.mark.parametrize("value", [9007199254740992, 1e101])
def test_numeric_magnitude_is_left_to_the_core_number_limit(value):
    # Documented Stage 1 limitation: numeric magnitude is not a bridge bound.
    # Exact JSON numbers outside the core's NUMBER_LIMIT reach the core's
    # sanitised rejected-input projection, not a fixed mapping error.
    baseline, target = _pair()
    _result(baseline).metadata["metric_definition"] = {"name": value}
    projection = _compare(baseline, target)
    assert projection["input_rejected"] is True
    assert "NUMBER_LIMIT" in projection["input_error_codes"]
    assert projection["decision"] == INSUFFICIENT


# ------------------------------------------------------------------ decisions


MANDATORY_DROPS = {
    "dataset_identity": _drop_identity,
    "dataset_version": _drop_version,
    "evaluation_split": _drop_split,
    "metric_definition": _drop_metric_definition,
    "evaluation_protocol": _drop_protocol,
}


@pytest.mark.parametrize("side", ["baseline", "target"])
@pytest.mark.parametrize("dimension", sorted(MANDATORY))
def test_each_mandatory_dimension_missing_on_one_side(dimension, side):
    baseline, target = _pair()
    MANDATORY_DROPS[dimension](baseline if side == "baseline" else target)
    projection = _compare(baseline, target)
    _assert_decision(projection, INSUFFICIENT)
    assert _state(projection, dimension, side) == "MISSING"
    other = "target" if side == "baseline" else "baseline"
    assert _state(projection, dimension, other) == "KNOWN"


@pytest.mark.parametrize("dimension", sorted(MANDATORY))
def test_each_mandatory_dimension_missing_on_both_sides(dimension):
    baseline, target = _pair()
    MANDATORY_DROPS[dimension](baseline)
    MANDATORY_DROPS[dimension](target)
    projection = _compare(baseline, target)
    _assert_decision(projection, INSUFFICIENT)
    assert _dimension(projection, dimension)["status"] == "INSUFFICIENT"


def test_missing_plus_another_mandatory_difference_is_not_comparable():
    baseline, target = _pair()
    _drop_split(baseline)
    _evaluation(target)["protocol"] = {"protocol_id": "protocol-2"}
    projection = _compare(baseline, target)
    _assert_decision(projection, NOT_COMPARABLE)
    assert "MANDATORY_DIMENSION_INSUFFICIENT:evaluation_split" in projection["reason_codes"]
    assert (
        "MANDATORY_DIMENSION_DIFFERENT:evaluation_protocol"
        in projection["reason_codes"]
    )


def _differ_preprocessing(loaded):
    parent = next(a for a in loaded.manifest.artifacts if a.id == "raw-shared")
    parent.id = "raw-target-only"
    for relationship in loaded.manifest.relationships:
        if relationship.target == "raw-shared":
            relationship.target = "raw-target-only"


def _differ_revision(loaded):
    _run_metadata(loaded)["source_revision"] = "rev-2"


def _differ_environment(loaded):
    _run_metadata(loaded)["environment"] = {"python": "3.11"}


def _differ_checkpoint(loaded):
    _checkpoint(loaded).sha256 = HASH_OTHER


@pytest.mark.parametrize(
    "differ,dimension",
    [
        (_differ_preprocessing, "preprocessing"),
        (_differ_revision, "source_revision"),
        (_differ_environment, "environment"),
        (_differ_checkpoint, "model_checkpoint"),
    ],
)
def test_optional_differences_still_compare(differ, dimension):
    baseline, target = _pair()
    differ(target)
    projection = _compare(baseline, target)
    _assert_decision(projection, COMPARABLE)
    assert _dimension(projection, dimension)["status"] == "DIFFERENT"
    assert f"OPTIONAL_DIMENSION_DIFFERENT:{dimension}" in projection["reason_codes"]


def test_metric_display_name_differences_do_not_veto():
    baseline, target = _pair()
    _result(target).name = "loss"
    projection = _compare(baseline, target)
    _assert_decision(projection, COMPARABLE)
    assert "METRIC_NAME_DIFFERS" in projection["reason_codes"]


def test_metric_display_name_missing_does_not_change_the_decision():
    baseline, target = _pair()
    original = _result(baseline)
    baseline.manifest.results[0] = Result.model_construct(
        id=original.id,
        run_id=original.run_id,
        value=original.value,
        metadata=copy.deepcopy(original.metadata),
    )
    projection = _compare(baseline, target)
    _assert_decision(projection, COMPARABLE)
    assert "METRIC_NAME_MISSING:baseline" in projection["reason_codes"]


@pytest.mark.parametrize(
    "baseline_split,target_split,expected",
    [
        ("Test", "test", INSUFFICIENT),
        (" test ", "test", INSUFFICIENT),
        ("test", "TEST", INSUFFICIENT),
        (" test ", " test ", COMPARABLE),
        ("test", "other", NOT_COMPARABLE),
    ],
)
def test_top_level_case_and_whitespace_handling(
    baseline_split, target_split, expected
):
    baseline, target = _pair()
    _evaluation(baseline)["split"] = baseline_split
    _evaluation(target)["split"] = target_split
    projection = _compare(baseline, target)
    _assert_decision(projection, expected)
    if expected == INSUFFICIENT:
        assert (
            "MANDATORY_DIMENSION_INSUFFICIENT:evaluation_split"
            in projection["reason_codes"]
        )


def test_nested_case_differences_are_not_whitespace_normalised():
    baseline, target = _pair()
    _result(baseline).metadata["metric_definition"] = {
        "definition_id": "acc",
        "averaging": "Macro",
    }
    _result(target).metadata["metric_definition"] = {
        "definition_id": "acc",
        "averaging": "macro",
    }
    projection = _compare(baseline, target)
    _assert_decision(projection, NOT_COMPARABLE)
    assert _dimension(projection, "metric_definition")["status"] == "DIFFERENT"


def test_dictionary_key_order_is_irrelevant():
    baseline, target = _pair()
    _run_metadata(baseline)["environment"] = {"a": 1, "b": 2}
    _run_metadata(target)["environment"] = {"b": 2, "a": 1}
    projection = _compare(baseline, target)
    _assert_decision(projection, COMPARABLE)
    assert _dimension(projection, "environment")["status"] == "MATCH"


def test_list_order_is_significant():
    baseline, target = _pair()
    _run_metadata(baseline)["environment"] = {"tags": ["a", "b"]}
    _run_metadata(target)["environment"] = {"tags": ["b", "a"]}
    projection = _compare(baseline, target)
    _assert_decision(projection, COMPARABLE)
    assert _dimension(projection, "environment")["status"] == "DIFFERENT"


# ------------------------------------------------------------------- privacy

CANARIES = {
    "project": "PC26BRIDGECANARYPROJECT7Q",
    "run": "PC26BRIDGECANARYRUN7Q",
    "result": "PC26BRIDGECANARYRESULT7Q",
    "dataset": "PC26BRIDGECANARYDATASET7Q",
    "checkpoint": "PC26BRIDGECANARYCHECKPOINT7Q",
    "parent": "PC26BRIDGECANARYPARENT7Q",
    "name": "PC26BRIDGECANARYNAME7Q",
    "metakey": "PC26BRIDGECANARYMETAKEY7Q",
    "metavalue": "PC26BRIDGECANARYMETAVALUE7Q",
    "dataset_id": "PC26BRIDGECANARYDATASETID7Q",
    "split": "PC26BRIDGECANARYSPLIT7Q",
    "definition_id": "PC26BRIDGECANARYDEFINITION7Q",
    "protocol_id": "PC26BRIDGECANARYPROTOCOL7Q",
    "revision": "PC26BRIDGECANARYREVISION7Q",
    "hash": "d3adb33f" * 8,
    "path": "PC26BRIDGECANARYPATH7Q",
    "locator": "PC26BRIDGECANARYLOCATOR7Q",
    "note": "PC26BRIDGECANARYNOTE7Q",
    "loaded_path": "PC26BRIDGECANARYLOADEDPATH7Q",
    "source_bytes": "PC26BRIDGECANARYSOURCEBYTES7Q",
}
CANARY_VALUES = list(CANARIES.values())


def _canary_manifest(tag: str) -> Manifest:
    canary = CANARIES
    run_id = f"{canary['run']}-{tag}"
    dataset_id = f"{canary['dataset']}-{tag}"
    return Manifest(
        project=Project(id=f"{canary['project']}-{tag}", name=canary["name"]),
        runs=[
            Run(
                id=run_id,
                name=canary["name"],
                metadata={
                    "evaluation": {
                        "split": canary["split"],
                        "protocol": {
                            "protocol_id": canary["protocol_id"],
                            "version": 1,
                        },
                    },
                    "source_revision": canary["revision"],
                    "environment": {canary["metakey"]: canary["metavalue"]},
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
                id=f"{canary['parent']}-{tag}",
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
                claim=canary["name"],
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
                note=canary["note"],
            ),
            Relationship(
                source=dataset_id,
                target=f"{canary['parent']}-{tag}",
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


def _projection_texts(projection: dict) -> list[str]:
    return [
        json.dumps(projection, sort_keys=True, ensure_ascii=True),
        comparability.render(projection),
    ]


def test_privacy_canaries_absent_from_comparable_projection(capsys, caplog):
    baseline, target = _canary_pair()
    projection = _compare(baseline, target)
    _assert_decision(projection, COMPARABLE)
    captured = capsys.readouterr()
    _assert_no_canaries(
        _projection_texts(projection)
        + [captured.out, captured.err, caplog.text]
    )


def test_privacy_canaries_absent_from_insufficient_projection(capsys):
    baseline, target = _canary_pair()
    _drop_split(baseline)
    projection = _compare(baseline, target)
    _assert_decision(projection, INSUFFICIENT)
    captured = capsys.readouterr()
    _assert_no_canaries(
        _projection_texts(projection) + [captured.out, captured.err]
    )


def test_privacy_canaries_absent_from_different_projection(capsys):
    baseline, target = _canary_pair()
    _evaluation(target)["split"] = "a-different-value"
    projection = _compare(baseline, target)
    _assert_decision(projection, NOT_COMPARABLE)
    captured = capsys.readouterr()
    _assert_no_canaries(
        _projection_texts(projection) + [captured.out, captured.err]
    )


def test_privacy_canaries_absent_from_mapping_error(capsys):
    baseline, target = _canary_pair()
    _dataset(baseline).metadata["dataset_id"] = 5
    error = _error(lambda: _compare(baseline, target))
    captured = capsys.readouterr()
    _assert_no_canaries(
        [
            str(error),
            repr(error),
            repr(error.args),
            "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            ),
            captured.out,
            captured.err,
        ]
    )
    assert error.__cause__ is None
    assert error.__context__ is None


def test_privacy_canaries_absent_from_invalid_selector_error():
    baseline, target = _canary_pair()
    error = _error(
        lambda: bridge.compare_recorded_conditions(
            baseline,
            target,
            baseline_result_id=CANARIES["metavalue"] + " ",
            target_result_id="x",
        )
    )
    assert error.code == bridge.RESULT_NOT_FOUND
    _assert_no_canaries(
        [str(error), repr(error), repr(error.args), error.code, error.side or ""]
    )


def test_privacy_canaries_absent_from_internal_error(monkeypatch):
    baseline, target = _canary_pair()

    def boom(document):
        raise RuntimeError(CANARIES["metavalue"])

    monkeypatch.setattr(bridge.comparability, "evaluate", boom)
    try:
        bridge.compare_recorded_conditions(
            baseline,
            target,
            baseline_result_id=_result(baseline).id,
            target_result_id=_result(target).id,
        )
    except bridge.ComparabilityMappingError as error:
        texts = [
            str(error),
            repr(error),
            repr(error.args),
            "".join(
                traceback.format_exception(type(error), error, error.__traceback__)
            ),
        ]
        _assert_no_canaries(texts)
        assert error.code == bridge.INTERNAL_MAPPING_ERROR
        assert error.side is None
        assert error.__cause__ is None
        assert error.__context__ is None
        assert error.__suppress_context__ is True
    else:  # pragma: no cover
        pytest.fail("expected ComparabilityMappingError")


def test_mapping_error_has_fixed_text_and_no_retained_input():
    baseline, target = _pair()
    _dataset(baseline).metadata["dataset_id"] = " "
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert error.side == "baseline"
    assert error.args == (bridge.INPUT_SHAPE_UNSUPPORTED,)
    assert str(error) == bridge.INPUT_SHAPE_UNSUPPORTED
    assert error.__cause__ is None and error.__context__ is None


# ------------------------------------- second acceptance: keys, bounds, context


class _EvilKey(str):
    calls = 0

    def __deepcopy__(self, memo):
        type(self).calls += 1
        raise AssertionError("deepcopy hook ran")

    def __reduce_ex__(self, protocol):
        type(self).calls += 1
        raise AssertionError("pickle hook ran")


class _DeepcopyHookValue:
    calls = 0

    def __deepcopy__(self, memo):
        type(self).calls += 1
        raise AssertionError("deepcopy hook ran")


def _loaded_with_evil_key(location: str):
    raw = _make_manifest("b").model_dump(mode="json")
    if location == "protocol":
        raw["runs"][0]["metadata"]["evaluation"]["protocol"] = {
            _EvilKey("protocol_id"): "protocol-1"
        }
        container = raw["runs"][0]["metadata"]["evaluation"]["protocol"]
    else:
        raw["results"][0]["metadata"]["metric_definition"] = {
            _EvilKey("name"): "accuracy"
        }
        container = raw["results"][0]["metadata"]["metric_definition"]
    manifest = Manifest.model_validate(raw)
    key = next(iter(container))
    loaded = LoadedManifest(
        manifest=manifest,
        path=Path("/synthetic/evil-key/runevidence.json"),
        source_bytes=b"",
    )
    return loaded, key


@pytest.mark.parametrize("location", ["protocol", "metric_definition"])
def test_str_subclass_keys_are_rejected_without_hooks(location):
    _EvilKey.calls = 0
    baseline, key = _loaded_with_evil_key(location)
    assert type(key) is _EvilKey
    target = _loaded("t")
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert error.side == "baseline"
    assert _EvilKey.calls == 0


def _nested_lists(depth: int):
    value = 1
    for _ in range(depth):
        value = [value]
    return value


def _manual_definition_document(value) -> dict:
    return {
        "format": bridge.FORMAT,
        "baseline": {
            "dimensions": {
                "metric_definition": {
                    "observations": [
                        {
                            "value": value,
                            "field": "f",
                            "basis": "proposed_extension",
                        }
                    ]
                }
            }
        },
        "target": {"dimensions": {}},
    }


def test_metric_definition_depth_matches_core_boundary():
    # Value root is at document depth 6: nine nested lists put the innermost
    # scalar at depth 16 (core maximum); ten put it at depth 17.
    allowed = {"name": _nested_lists(9)}
    baseline, target = _pair()
    _result(baseline).metadata["metric_definition"] = copy.deepcopy(allowed)
    _result(target).metadata["metric_definition"] = copy.deepcopy(allowed)
    projection = _compare(baseline, target)
    assert projection["input_rejected"] is False
    assert "DEPTH_LIMIT" not in projection["input_error_codes"]
    document = bridge._build_input(
        baseline,
        target,
        baseline_result_id="res-b",
        target_result_id="res-t",
    )
    assert comparability.validate(document) == []

    deeper = {"name": _nested_lists(10)}
    baseline, target = _pair()
    _result(baseline).metadata["metric_definition"] = deeper
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_BOUND_EXCEEDED
    assert error.side == "baseline"
    # The core would reject this shape; the bridge must not hand it over.
    errors = comparability.validate(_manual_definition_document(deeper))
    assert any(entry["code"] == "DEPTH_LIMIT" for entry in errors)


def test_bounds_raise_before_copy_or_sort(monkeypatch):
    calls: list[str] = []

    def trap(*args, **kwargs):
        calls.append("hook")
        raise AssertionError("bounds must run before copy/sort")

    monkeypatch.setattr(copy, "deepcopy", trap)
    monkeypatch.setattr(bridge, "_copy_json_exact", trap)
    monkeypatch.setattr(bridge, "_ordered_ids", trap)

    cases = [
        {"name": "x" * 1025},
        {"definition_id": "x" * 1025},
    ]
    for definition in cases:
        baseline, target = _pair()
        _result(baseline).metadata["metric_definition"] = definition
        error = _error(lambda: _compare(baseline, target))
        assert error.code == bridge.INPUT_BOUND_EXCEEDED
        assert error.side == "baseline"

    baseline, target = _pair()
    _evaluation(baseline)["protocol"] = {"protocol_id": "x" * 1025}
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_BOUND_EXCEEDED
    assert calls == []


def test_custom_deepcopy_hook_value_is_rejected_shape(monkeypatch):
    calls: list[str] = []

    def trap(*args, **kwargs):
        calls.append("deepcopy")
        raise AssertionError("deepcopy must not be called")

    monkeypatch.setattr(copy, "deepcopy", trap)
    monkeypatch.setattr(bridge, "_copy_json_exact", trap)
    monkeypatch.setattr(bridge, "_ordered_ids", trap)
    _DeepcopyHookValue.calls = 0

    baseline, target = _pair()
    _result(baseline).metadata["metric_definition"] = {
        "name": _DeepcopyHookValue()
    }
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert error.side == "baseline"
    assert _DeepcopyHookValue.calls == 0
    assert calls == []


def _oversized_environment() -> dict:
    return {f"k{index:03d}": "v" * 150 for index in range(120)}


def test_baseline_bound_beats_target_selector():
    baseline, target = _pair()
    _evaluation(baseline)["split"] = "x" * 1025
    error = _error(
        lambda: bridge.compare_recorded_conditions(
            baseline,
            target,
            baseline_result_id="res-b",
            target_result_id="   ",
        )
    )
    assert error.code == bridge.INPUT_BOUND_EXCEEDED
    assert error.side == "baseline"


def test_baseline_bound_beats_target_shape():
    baseline, target = _pair()
    _evaluation(baseline)["split"] = "x" * 1025
    _evaluation(target)["split"] = []
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_BOUND_EXCEEDED
    assert error.side == "baseline"


@pytest.mark.parametrize("definition", [{"definition_id": ""}, {"unit": None}])
def test_baseline_shape_beats_environment_size(definition):
    baseline, target = _pair()
    _run_metadata(baseline)["environment"] = _oversized_environment()
    _result(baseline).metadata["metric_definition"] = definition
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert error.side == "baseline"


def test_target_bound_reports_target(monkeypatch):
    baseline, target = _pair()
    _evaluation(target)["split"] = "x" * 1025

    calls: list[str] = []

    def trap(*args, **kwargs):
        calls.append("hook")
        raise AssertionError("baseline copy/sort ran before target rejection")

    monkeypatch.setattr(bridge, "_copy_json_exact", trap)
    monkeypatch.setattr(bridge, "_ordered_ids", trap)

    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_BOUND_EXCEEDED
    assert error.side == "target"
    assert calls == []


def test_target_shape_beats_environment_size():
    baseline, target = _pair()
    _run_metadata(target)["environment"] = _oversized_environment()
    _result(target).metadata["metric_definition"] = {"definition_id": ""}
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert error.side == "target"


def test_baseline_shape_beats_target_bound():
    baseline, target = _pair()
    _result(baseline).metadata["metric_definition"] = {"definition_id": ""}
    _evaluation(target)["split"] = "x" * 1025
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert error.side == "baseline"


def _long_split(loaded):
    _evaluation(loaded)["split"] = "x" * 1025


def _long_revision(loaded):
    _run_metadata(loaded)["source_revision"] = "x" * 1025


def _long_dataset_id(loaded):
    _dataset(loaded).metadata["dataset_id"] = "x" * 1025


def _long_result_name(loaded):
    _result(loaded).name = "x" * 1025


def _long_parent_id(loaded):
    parent = next(a for a in loaded.manifest.artifacts if a.id == "raw-shared")
    parent.id = "p" * 1025
    for relationship in loaded.manifest.relationships:
        if relationship.target == "raw-shared":
            relationship.target = parent.id


def _long_parent_hash(loaded):
    parent = next(a for a in loaded.manifest.artifacts if a.id == "raw-shared")
    parent.sha256 = "x" * 1025


@pytest.mark.parametrize(
    "mutate",
    [
        _long_split,
        _long_revision,
        _long_dataset_id,
        _long_result_name,
        _long_parent_id,
        _long_parent_hash,
    ],
)
def test_selected_string_length_bound(mutate):
    baseline, target = _pair()
    mutate(baseline)
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_BOUND_EXCEEDED
    assert error.side == "baseline"


def test_environment_key_length_bound():
    baseline, target = _pair()
    _run_metadata(baseline)["environment"] = {"k" * 1025: 1}
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_BOUND_EXCEEDED
    assert error.side == "baseline"


@pytest.mark.parametrize("location", ["attached", "parent"])
def test_id_bound_precedes_sorting_and_copying(monkeypatch, location):
    baseline, target = _pair()
    if location == "attached":
        long_id = "a" * 1025
        dataset = _dataset(baseline)
        dataset.id = long_id
        for relationship in baseline.manifest.relationships:
            if relationship.source == "ds-b":
                relationship.source = long_id
    else:
        long_id = "p" * 1025
        parent = next(a for a in baseline.manifest.artifacts if a.id == "raw-shared")
        parent.id = long_id
        for relationship in baseline.manifest.relationships:
            if relationship.target == "raw-shared":
                relationship.target = long_id

    ordered_calls: list[object] = []

    def no_order(identifiers):
        ordered_calls.append(identifiers)
        raise AssertionError("sorting ran before the ID bound")

    monkeypatch.setattr(bridge, "_ordered_ids", no_order)
    error = _error(lambda: _compare(baseline, target))
    assert error.code == bridge.INPUT_BOUND_EXCEEDED
    assert error.side == "baseline"
    assert ordered_calls == []


def _loaded_with_long_selected_id(kind: str):
    raw = _make_manifest("b").model_dump(mode="json")
    long_id = "s" * 1025
    if kind == "run":
        raw["runs"][0]["id"] = long_id
        raw["results"][0]["run_id"] = long_id
        for relationship in raw["relationships"]:
            if relationship["target"] == "run-b":
                relationship["target"] = long_id
        selector = raw["results"][0]["id"]
    else:
        raw["results"][0]["id"] = long_id
        selector = long_id
    manifest = Manifest.model_validate(raw)
    loaded = LoadedManifest(
        manifest=manifest,
        path=Path("/synthetic/long-selected-id/runevidence.json"),
        source_bytes=b"",
    )
    return loaded, selector


@pytest.mark.parametrize("kind", ["run", "result"])
def test_selected_id_bound_precedes_copy_and_sort(monkeypatch, kind):
    baseline, selector = _loaded_with_long_selected_id(kind)
    if kind == "run":
        assert len(baseline.manifest.runs[0].id) == 1025
    else:
        assert len(baseline.manifest.results[0].id) == 1025
    target = _loaded("t")

    calls: list[str] = []

    def trap(*args, **kwargs):
        calls.append("hook")
        raise AssertionError("copy/sort ran before the selected-ID bound")

    monkeypatch.setattr(bridge, "_copy_json_exact", trap)
    monkeypatch.setattr(bridge, "_ordered_ids", trap)

    error = _error(
        lambda: bridge.compare_recorded_conditions(
            baseline,
            target,
            baseline_result_id=selector,
            target_result_id="res-t",
        )
    )
    assert error.code == bridge.INPUT_BOUND_EXCEEDED
    assert error.side == "baseline"
    assert calls == []


@pytest.mark.parametrize("kind", ["mapping", "internal"])
def test_caller_active_exception_is_suppressed(kind, monkeypatch):
    caller_canary = "PC26BRIDGECALLERCONTEXT7Q"
    internal_canary = "PC26BRIDGEINTERNALCONTEXT7Q"

    baseline, target = _pair()
    if kind == "mapping":
        _evaluation(baseline)["split"] = 5
    else:

        def boom(document):
            raise RuntimeError(internal_canary)

        monkeypatch.setattr(bridge.comparability, "evaluate", boom)

    try:
        raise RuntimeError(caller_canary)
    except RuntimeError:
        error = _error(lambda: _compare(baseline, target))

    assert error.__suppress_context__ is True
    assert error.__cause__ is None
    texts = [
        str(error),
        repr(error),
        repr(error.args),
        "".join(traceback.format_exception(type(error), error, error.__traceback__)),
    ]
    _assert_no_canaries(texts)
    blob = "\n".join(texts)
    assert caller_canary not in blob
    assert internal_canary not in blob


# ------------------------------------------------------------- no-I/O counter


def test_no_io_attempts_are_made(monkeypatch):
    baseline, target = _pair()
    expected = _compare(baseline, target)

    import runevidence.checks as checks_module
    import runevidence.manifest as manifest_module

    attempts: list[tuple[str, str]] = []

    def install(module, name):
        if not hasattr(module, name):
            return

        def trapped(*args, **kwargs):
            attempts.append((getattr(module, "__name__", "?"), name))
            raise RuntimeError("I/O ATTEMPT")

        monkeypatch.setattr(module, name, trapped)

    for module, names in (
        (builtins, ["open"]),
        (io_module, ["open"]),
        (os, [
            "open",
            "stat",
            "lstat",
            "scandir",
            "listdir",
            "readlink",
        ]),
        (socket, ["socket", "create_connection", "getaddrinfo"]),
        (urllib.request, ["urlopen"]),
        (subprocess, ["run", "Popen"]),
        (manifest_module, ["load_manifest"]),
        (checks_module, ["run_checks"]),
    ):
        for name in names:
            install(module, name)

    actual = _compare(baseline, target)
    assert attempts == []
    assert actual == expected


# ------------------------------------------------------- determinism / mutation


def test_permuted_collections_and_key_orders_are_byte_identical():
    baseline, target = _pair()
    first = _compare(baseline, target)

    baseline2, target2 = _pair()
    for loaded in (baseline2, target2):
        manifest = loaded.manifest
        manifest.runs.reverse()
        manifest.artifacts.reverse()
        manifest.results.reverse()
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
            evaluation = run.metadata.get("evaluation")
            if isinstance(evaluation, dict):
                run.metadata["evaluation"] = dict(
                    reversed(list(evaluation.items()))
                )
            environment = run.metadata.get("environment")
            if isinstance(environment, dict):
                run.metadata["environment"] = dict(
                    reversed(list(environment.items()))
                )
        for result in manifest.results:
            definition = result.metadata.get("metric_definition")
            if isinstance(definition, dict):
                result.metadata["metric_definition"] = dict(
                    reversed(list(definition.items()))
                )

    second = _compare(baseline2, target2)
    third = _compare(baseline2, target2)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert json.dumps(second, sort_keys=True) == json.dumps(third, sort_keys=True)


def test_calls_do_not_mutate_manifests():
    baseline, target = _pair()
    before = [
        baseline.manifest.model_dump(),
        target.manifest.model_dump(),
        baseline.path,
        target.path,
        baseline.source_bytes,
        target.source_bytes,
    ]
    _compare(baseline, target)
    after = [
        baseline.manifest.model_dump(),
        target.manifest.model_dump(),
        baseline.path,
        target.path,
        baseline.source_bytes,
        target.source_bytes,
    ]
    assert before == after


def test_private_input_does_not_alias_manifest_values():
    baseline, target = _pair()
    expected = _compare(baseline, target)
    document = bridge._build_input(
        baseline,
        target,
        baseline_result_id="res-b",
        target_result_id="res-t",
    )
    document["baseline"]["dimensions"]["environment"]["observations"][0][
        "value"
    ]["python"] = "MUTATED"
    document["baseline"]["dimensions"]["metric_definition"]["observations"][0][
        "value"
    ]["definition_id"] = "MUTATED"
    document["baseline"]["dimensions"]["preprocessing"]["observations"][0][
        "value"
    ][0]["id"] = "MUTATED"

    assert _run_metadata(baseline)["environment"]["python"] == "3.12"
    assert (
        _result(baseline).metadata["metric_definition"]["definition_id"]
        == "accuracy-v1"
    )
    assert _dataset(baseline).id == "ds-b"
    assert any(a.id == "raw-shared" for a in baseline.manifest.artifacts)
    assert _compare(baseline, target) == expected


# --------------------------------------------------------- import boundary


def test_bridge_is_not_imported_by_any_production_module():
    root = Path(bridge.__file__).resolve().parent
    assert root.name == "runevidence"
    # Stage 2a/2b allow exactly two production importers, both at the package root:
    # the command module and the public CLI. Nested modules stay forbidden.
    bridge_path = Path(bridge.__file__).resolve()
    allowed_importer = (root / "run_comparability.py").resolve()
    allowed_cli = (root / "cli.py").resolve()
    allowed = {bridge_path, allowed_importer, allowed_cli}
    for path in sorted(root.rglob("*.py")):
        if path.resolve() in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        assert "_experimental_comparability_bridge" not in text, path
    assert "_experimental_comparability_bridge" in allowed_importer.read_text(
        encoding="utf-8"
    )
    cli_text = allowed_cli.read_text(encoding="utf-8")
    assert "_experimental_comparability_bridge" not in cli_text
    assert "run_comparability" in cli_text
    assert "_experimental_comparability_bridge" not in (
        root / "__init__.py"
    ).read_text(encoding="utf-8")


def test_public_signature_is_exact():
    signature = inspect.signature(bridge.compare_recorded_conditions)
    parameters = signature.parameters
    assert list(parameters) == [
        "baseline",
        "target",
        "baseline_result_id",
        "target_result_id",
    ]
    assert parameters["baseline"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters["target"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    for name in ("baseline_result_id", "target_result_id"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
    assert not any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def test_docstring_declares_synthetic_status():
    text = bridge.__doc__ or ""
    assert "experimental" in text
    assert "synthetic-only" in text
    assert "not integrated into application workflows" in text
    assert "no production privacy, scientific-validity or runtime qualification" in text
    assert "compares" in text and "recorded conditions only" in text
    assert "No real-manifest qualification claim" in text


def test_error_catalogue_is_fixed():
    catalogue = {
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
    }
    assert len(catalogue) == 11
    assert Code.duplicate_id.value == "duplicate-id"
    assert load_manifest is not None and run_checks is not None
