"""Stage 3b2a: first-class schema 0.2 bridge resolution.

These tests are synthetic-only unless they explicitly say otherwise. Every
manifest is constructed in memory; no artifact path is opened. The separate
0.2 example has its own CLI test module.
"""

from __future__ import annotations

import builtins
import copy
import io as io_module
import json
import socket
import urllib.request
from pathlib import Path

import pytest
from pydantic import ValidationError

from runevidence import _experimental_comparability_bridge as bridge
from runevidence import run_comparability as command
from runevidence.manifest import (
    LoadedManifest,
    dump_manifest,
    load_manifest,
)
from runevidence.models import (
    Artifact,
    EvaluationProtocol,
    Manifest,
    MetricDefinition,
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

_UNSET = object()

LEGACY_RUN_METADATA = {
    "evaluation": {
        "split": "test",
        "protocol": {"protocol_id": "protocol-1", "version": 1},
    },
    "source_revision": "rev-1",
    "environment": {"python": "3.12", "tags": ["cpu"]},
}
LEGACY_METRIC_METADATA = {
    "metric_definition": {
        "definition_id": "accuracy-v1",
        "higher_is_better": True,
    }
}
METRIC = MetricDefinition(definition_id="accuracy-v1", higher_is_better=True)
PROTOCOL = EvaluationProtocol(protocol_id="protocol-1", version=1)


# ------------------------------------------------------------------ builders


def full_conditions(tag: str, **overrides) -> RunConditions:
    values = {
        "evaluation_split": "test",
        "evaluation_protocol": PROTOCOL,
        "evaluation_dataset_artifact_id": f"ds-{tag}",
        "model_checkpoint_artifact_id": f"ckpt-{tag}",
        "source_revision": "rev-1",
        "environment": {"python": "3.12", "tags": ["cpu"]},
    }
    values.update(overrides)
    return RunConditions(**values)


def conditions_without(tag: str, *names: str, **overrides) -> RunConditions:
    values = {
        "evaluation_split": "test",
        "evaluation_protocol": PROTOCOL,
        "evaluation_dataset_artifact_id": f"ds-{tag}",
        "model_checkpoint_artifact_id": f"ckpt-{tag}",
        "source_revision": "rev-1",
        "environment": {"python": "3.12", "tags": ["cpu"]},
    }
    for name in names:
        values.pop(name)
    values.update(overrides)
    return RunConditions(**values)


def dataset(
    identifier: str,
    *,
    first_class_id=_UNSET,
    legacy_id=_UNSET,
    sha256: str | None = HASH_DATASET,
    volatile: bool = False,
    artifact_type: str = "file",
    metadata=_UNSET,
) -> Artifact:
    kwargs = {
        "id": identifier,
        "path": f"data/{identifier}.csv",
        "kind": "dataset",
        "sha256": sha256,
        "volatile": volatile,
        "artifact_type": artifact_type,
    }
    if artifact_type == "directory":
        kwargs["digest_format"] = "runevidence-directory-v1"
    if first_class_id is not _UNSET:
        kwargs["dataset_id"] = first_class_id
    if metadata is _UNSET:
        metadata = {}
        if legacy_id is not _UNSET:
            metadata = {"dataset_id": legacy_id}
    kwargs["metadata"] = metadata
    return Artifact(**kwargs)


def checkpoint(
    identifier: str,
    *,
    sha256: str | None = HASH_CHECKPOINT,
    volatile: bool = False,
    artifact_type: str = "file",
) -> Artifact:
    kwargs = {
        "id": identifier,
        "path": f"models/{identifier}.bin",
        "kind": "checkpoint",
        "sha256": sha256,
        "volatile": volatile,
        "artifact_type": artifact_type,
    }
    if artifact_type == "directory":
        kwargs["digest_format"] = "runevidence-directory-v1"
    return Artifact(**kwargs)


def default_artifacts(tag: str, *, both_labels: bool = False):
    label_kwargs = (
        {"first_class_id": "imagenet", "legacy_id": "imagenet"}
        if both_labels
        else {"first_class_id": "imagenet"}
    )
    return [
        dataset(f"ds-{tag}", **label_kwargs),
        checkpoint(f"ckpt-{tag}"),
        Artifact(id="raw-shared", path="data/source.csv", sha256=HASH_PARENT),
    ]


def legacy_artifacts(tag: str):
    return [
        dataset(f"ds-{tag}", legacy_id="imagenet"),
        checkpoint(f"ckpt-{tag}"),
        Artifact(id="raw-shared", path="data/source.csv", sha256=HASH_PARENT),
    ]


def default_relationships(tag: str):
    return [
        Relationship(source=f"ds-{tag}", target=f"run-{tag}", type="input_to"),
        Relationship(source=f"ckpt-{tag}", target=f"run-{tag}", type="input_to"),
        Relationship(source=f"ds-{tag}", target="raw-shared", type="derived_from"),
    ]


def make_manifest(
    tag: str,
    *,
    schema: str = "0.2",
    conditions=_UNSET,
    run_metadata=_UNSET,
    metric=_UNSET,
    result_metadata=_UNSET,
    artifacts=_UNSET,
    relationships=_UNSET,
    project_id: str | None = None,
    run_name: str | None = None,
    result_name: str = "accuracy",
) -> Manifest:
    run_id = f"run-{tag}"
    if run_metadata is _UNSET:
        run_metadata = {}
    if result_metadata is _UNSET:
        result_metadata = {}
    if artifacts is _UNSET:
        artifacts = (
            default_artifacts(tag)
            if schema == "0.2"
            else legacy_artifacts(tag)
        )
    if relationships is _UNSET:
        relationships = default_relationships(tag)

    run_kwargs = {}
    if conditions is not _UNSET:
        run_kwargs["conditions"] = conditions
    result_kwargs = {}
    if metric is not _UNSET:
        result_kwargs["metric_definition"] = metric

    return Manifest(
        schema_version=schema,
        project=Project(id=project_id or f"proj-{tag}", name=f"project {tag}"),
        runs=[
            Run(
                id=run_id,
                name=run_name or f"run {tag}",
                metadata=copy.deepcopy(run_metadata),
                **run_kwargs,
            )
        ],
        artifacts=artifacts,
        results=[
            Result(
                id=f"res-{tag}",
                run_id=run_id,
                name=result_name,
                value=0.9,
                metadata=copy.deepcopy(result_metadata),
                **result_kwargs,
            )
        ],
        relationships=relationships,
    )


def first_class(tag: str) -> Manifest:
    return make_manifest(
        tag, conditions=full_conditions(tag), metric=copy.deepcopy(METRIC)
    )


def legacy(tag: str, *, schema: str = "0.2") -> Manifest:
    return make_manifest(
        tag,
        schema=schema,
        run_metadata=LEGACY_RUN_METADATA,
        result_metadata=LEGACY_METRIC_METADATA,
        artifacts=legacy_artifacts(tag) if schema == "0.2" else _UNSET,
        conditions=_UNSET,
        metric=_UNSET,
    )


def mixed(tag: str) -> Manifest:
    return make_manifest(
        tag,
        conditions=full_conditions(tag),
        run_metadata=LEGACY_RUN_METADATA,
        metric=copy.deepcopy(METRIC),
        result_metadata=LEGACY_METRIC_METADATA,
        artifacts=default_artifacts(tag, both_labels=True),
    )


def loaded(manifest: Manifest, tag: str) -> LoadedManifest:
    return LoadedManifest(
        manifest=manifest,
        path=Path(f"/synthetic/{tag}/runevidence.json"),
        source_bytes=b"",
    )


def pair(builder=first_class):
    return loaded(builder("b"), "b"), loaded(builder("t"), "t")


def compare(baseline, target) -> dict:
    return bridge.compare_recorded_conditions(
        baseline,
        target,
        baseline_result_id=baseline.manifest.results[0].id,
        target_result_id=target.manifest.results[0].id,
    )


def build_input(baseline, target) -> dict:
    return bridge._build_input(
        baseline,
        target,
        baseline_result_id=baseline.manifest.results[0].id,
        target_result_id=target.manifest.results[0].id,
    )


def error(call) -> bridge.ComparabilityMappingError:
    with pytest.raises(bridge.ComparabilityMappingError) as caught:
        call()
    return caught.value


def dimension(projection: dict, name: str) -> dict:
    return next(row for row in projection["dimensions"] if row["dimension"] == name)


def state(projection: dict, name: str, side: str) -> str:
    return dimension(projection, name)[side]["state"]


def observation_count(document: dict, name: str, side: str = "baseline") -> int:
    return len(document[side]["dimensions"][name]["observations"])


def observation_value(document: dict, name: str, side: str = "baseline"):
    return document[side]["dimensions"][name]["observations"][0]["value"]


def assert_comparable(projection: dict) -> None:
    assert projection["decision"] == COMPARABLE, projection["reason_codes"]


# ------------------------------------------------------- 0.2 decision surface


def test_first_class_pair_is_comparable_and_uses_all_dimensions():
    projection = compare(*pair())
    assert_comparable(projection)
    for row in projection["dimensions"]:
        assert row["status"] == "MATCH", row["dimension"]
    assert state(projection, "dataset_identity", "baseline") == "KNOWN"
    assert state(projection, "dataset_version", "baseline") == "KNOWN"
    assert state(projection, "model_checkpoint", "baseline") == "KNOWN"
    assert state(projection, "metric_definition", "baseline") == "KNOWN"
    assert state(projection, "preprocessing", "baseline") == "KNOWN"


def test_first_class_difference_is_not_comparable():
    baseline, target = pair()
    target.manifest.runs[0].conditions.evaluation_split = "validation"
    projection = compare(baseline, target)
    assert projection["decision"] == NOT_COMPARABLE
    assert dimension(projection, "evaluation_split")["status"] == "DIFFERENT"


def test_first_class_missing_is_insufficient():
    baseline = loaded(first_class("b"), "b")
    target_manifest = make_manifest(
        "t",
        conditions=RunConditions(
            evaluation_protocol=PROTOCOL,
            source_revision="rev-1",
            environment={"python": "3.12", "tags": ["cpu"]},
            evaluation_dataset_artifact_id="ds-t",
            model_checkpoint_artifact_id="ckpt-t",
        ),
        metric=copy.deepcopy(METRIC),
    )
    projection = compare(baseline, loaded(target_manifest, "t"))
    assert projection["decision"] == INSUFFICIENT
    assert state(projection, "evaluation_split", "target") == "MISSING"


# ---------------------------------------------------- fallback and presence


def test_conditions_absent_falls_back_to_legacy_metadata():
    projection = compare(loaded(legacy("b"), "b"), loaded(legacy("t"), "t"))
    assert_comparable(projection)
    assert state(projection, "evaluation_split", "baseline") == "KNOWN"
    assert state(projection, "metric_definition", "baseline") == "KNOWN"


def test_empty_conditions_falls_back_for_every_dimension():
    baseline_manifest = make_manifest(
        "b",
        conditions=RunConditions(),
        run_metadata=LEGACY_RUN_METADATA,
        result_metadata=LEGACY_METRIC_METADATA,
        artifacts=legacy_artifacts("b"),
    )
    projection = compare(
        loaded(baseline_manifest, "b"), loaded(first_class("t"), "t")
    )
    assert_comparable(projection)
    assert state(projection, "evaluation_split", "baseline") == "KNOWN"
    assert state(projection, "environment", "baseline") == "KNOWN"


def test_omitted_conditions_member_falls_back_independently():
    baseline_manifest = make_manifest(
        "b",
        conditions=RunConditions(
            evaluation_split="test",
            evaluation_protocol=PROTOCOL,
            source_revision="rev-1",
            evaluation_dataset_artifact_id="ds-b",
            model_checkpoint_artifact_id="ckpt-b",
        ),
        run_metadata=LEGACY_RUN_METADATA,
        result_metadata=LEGACY_METRIC_METADATA,
        artifacts=legacy_artifacts("b"),
    )
    projection = compare(
        loaded(baseline_manifest, "b"), loaded(first_class("t"), "t")
    )
    assert_comparable(projection)
    assert state(projection, "environment", "baseline") == "KNOWN"


def test_absent_conditions_can_coexist_with_first_class_artifact_and_result_fields():
    baseline_manifest = make_manifest(
        "b",
        run_metadata=LEGACY_RUN_METADATA,
        metric=copy.deepcopy(METRIC),
        result_metadata={},
    )
    projection = compare(
        loaded(baseline_manifest, "b"), loaded(first_class("t"), "t")
    )
    assert_comparable(projection)
    document = build_input(
        loaded(baseline_manifest, "b"), loaded(first_class("t"), "t")
    )
    assert observation_value(document, "dataset_identity") == "imagenet"
    assert observation_value(document, "metric_definition") == {
        "definition_id": "accuracy-v1",
        "higher_is_better": True,
    }


def test_omitted_nested_protocol_and_metric_members_are_not_null():
    baseline_manifest = make_manifest(
        "b",
        conditions=full_conditions(
            "b",
            evaluation_protocol=EvaluationProtocol(protocol_id="protocol-1"),
        ),
        metric=MetricDefinition(definition_id="accuracy-v1"),
    )
    document = build_input(
        loaded(baseline_manifest, "b"), loaded(first_class("t"), "t")
    )
    protocol = observation_value(document, "evaluation_protocol")
    definition = observation_value(document, "metric_definition")
    assert protocol == {"protocol_id": "protocol-1"}
    assert definition == {"definition_id": "accuracy-v1"}


@pytest.mark.parametrize(
    "field",
    ["evaluation_split", "evaluation_protocol", "source_revision", "environment"],
)
def test_explicit_null_condition_is_unknown_without_fallback(field):
    values = {
        "evaluation_split": "test",
        "evaluation_protocol": PROTOCOL,
        "evaluation_dataset_artifact_id": "ds-b",
        "model_checkpoint_artifact_id": "ckpt-b",
        "source_revision": "rev-1",
        "environment": {"python": "3.12"},
    }
    values[field] = None
    baseline_manifest = make_manifest(
        "b", conditions=RunConditions(**values), run_metadata={}
    )
    document = build_input(loaded(baseline_manifest, "b"), loaded(first_class("t"), "t"))
    assert observation_count(document, field) == 1
    assert observation_value(document, field) is None


def test_explicit_null_metric_definition_is_unknown_without_fallback():
    baseline_manifest = make_manifest(
        "b",
        conditions=full_conditions("b"),
        metric=None,
        result_metadata={},
    )
    document = build_input(loaded(baseline_manifest, "b"), loaded(first_class("t"), "t"))
    assert observation_value(document, "metric_definition") is None


def test_explicit_null_dimension_with_legacy_value_conflicts():
    baseline_manifest = make_manifest(
        "b",
        conditions=full_conditions("b", evaluation_split=None),
        run_metadata=LEGACY_RUN_METADATA,
    )
    failure = error(
        lambda: compare(loaded(baseline_manifest, "b"), loaded(first_class("t"), "t"))
    )
    assert failure.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert failure.side == "baseline"


def test_first_class_empty_environment_is_unsupported():
    baseline_manifest = make_manifest(
        "b", conditions=full_conditions("b", environment={})
    )
    failure = error(
        lambda: compare(loaded(baseline_manifest, "b"), loaded(first_class("t"), "t"))
    )
    assert failure.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert failure.side == "baseline"


def test_legacy_empty_environment_is_unsupported_in_0_2():
    metadata = copy.deepcopy(LEGACY_RUN_METADATA)
    metadata["environment"] = {}
    baseline_manifest = make_manifest(
        "b", run_metadata=metadata, artifacts=legacy_artifacts("b")
    )
    failure = error(
        lambda: compare(loaded(baseline_manifest, "b"), loaded(first_class("t"), "t"))
    )
    assert failure.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert failure.side == "baseline"


@pytest.mark.parametrize(
    "conditions",
    [
        pytest.param(None, id="conditions-null"),
        pytest.param(
            {"evaluation_dataset_artifact_id": None}, id="dataset-selector-null"
        ),
        pytest.param(
            {"model_checkpoint_artifact_id": None}, id="checkpoint-selector-null"
        ),
        pytest.param({"evaluation_protocol": {}}, id="protocol-empty"),
        pytest.param(
            {"evaluation_protocol": {"protocol_id": "p", "version": None}},
            id="protocol-version-null",
        ),
        pytest.param(
            {"evaluation_split": ""}, id="split-blank"
        ),
    ],
)
def test_invalid_0_2_parent_and_nested_nulls_are_schema_errors(conditions):
    document = {
        "schema_version": "0.2",
        "project": {"id": "proj", "name": "Project"},
        "runs": [{"id": "run", "metadata": {}, "conditions": conditions}],
    }
    with pytest.raises(ValidationError):
        Manifest.model_validate(document)


def test_dump_reload_preserves_omitted_null_and_empty_presence(tmp_path):
    manifest = make_manifest(
        "b",
        conditions=RunConditions(evaluation_split=None, environment={}),
        run_metadata={},
    )
    path = tmp_path / "runevidence.json"
    path.write_text(dump_manifest(manifest), encoding="utf-8")
    raw = json.loads(path.read_text(encoding="utf-8"))

    assert "evaluation_split" in raw["runs"][0]["conditions"]
    assert raw["runs"][0]["conditions"]["evaluation_split"] is None
    assert raw["runs"][0]["conditions"]["environment"] == {}
    assert "source_revision" not in raw["runs"][0]["conditions"]

    reloaded = load_manifest(path).manifest
    fields = reloaded.runs[0].conditions.model_fields_set
    assert "evaluation_split" in fields
    assert "environment" in fields
    assert "source_revision" not in fields

    failure = error(lambda: compare(loaded(reloaded, "b"), loaded(first_class("t"), "t")))
    assert failure.code == bridge.INPUT_SHAPE_UNSUPPORTED


# ------------------------------------------- duplicate equality and conflicts


def test_equal_first_class_and_legacy_collapse_to_one_observation():
    document = build_input(loaded(mixed("b"), "b"), loaded(first_class("t"), "t"))
    for name in (
        "dataset_identity",
        "evaluation_split",
        "evaluation_protocol",
        "source_revision",
        "environment",
        "metric_definition",
    ):
        assert observation_count(document, name) == 1, name


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda m: setattr(m.runs[0].conditions, "evaluation_split", "train"),
            id="split",
        ),
        pytest.param(
            lambda m: setattr(
                m.runs[0].conditions,
                "evaluation_protocol",
                EvaluationProtocol(protocol_id="other-protocol", version=1),
            ),
            id="protocol",
        ),
        pytest.param(
            lambda m: setattr(m.runs[0].conditions, "source_revision", "rev-2"),
            id="source-revision",
        ),
        pytest.param(
            lambda m: setattr(
                m.runs[0].conditions, "environment", {"python": "3.11"}
            ),
            id="environment",
        ),
        pytest.param(
            lambda m: setattr(
                m.results[0],
                "metric_definition",
                MetricDefinition(definition_id="accuracy-v1", higher_is_better=False),
            ),
            id="metric-definition",
        ),
        pytest.param(
            lambda m: setattr(m.artifacts[0], "dataset_id", "other-label"),
            id="dataset-identity",
        ),
    ],
)
def test_unequal_first_class_and_legacy_fail(mutate):
    baseline = mixed("b")
    mutate(baseline)
    failure = error(lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t")))
    assert failure.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert failure.side == "baseline"


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda m: m.runs[0].metadata["evaluation"].__setitem__("split", 5),
            id="split-type",
        ),
        pytest.param(
            lambda m: m.runs[0].metadata["evaluation"].__setitem__(
                "protocol", {"protocol_id": ""}
            ),
            id="protocol-blank-id",
        ),
        pytest.param(
            lambda m: m.runs[0].metadata.__setitem__("environment", []),
            id="environment-type",
        ),
        pytest.param(
            lambda m: m.runs[0].metadata["evaluation"].__setitem__(
                "protocol", {"protocol_id": "p", "unknown": 1}
            ),
            id="protocol-extra-key",
        ),
        pytest.param(
            lambda m: m.results[0].metadata.__setitem__(
                "metric_definition", {"definition_id": ""}
            ),
            id="metric-definition-malformed",
        ),
        pytest.param(
            lambda m: m.artifacts[0].metadata.__setitem__("dataset_id", ""),
            id="dataset-identity-blank",
        ),
    ],
)
def test_malformed_legacy_duplicate_fails(mutate):
    baseline = mixed("b")
    mutate(baseline)
    failure = error(lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t")))
    assert failure.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert failure.side == "baseline"


def test_protocol_and_environment_are_whole_values_never_merged():
    baseline = mixed("b")
    baseline.runs[0].conditions.evaluation_protocol = EvaluationProtocol(
        protocol_id="protocol-1", version=2
    )
    failure = error(lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t")))
    assert failure.code == bridge.INPUT_SHAPE_UNSUPPORTED

    baseline = mixed("b")
    baseline.runs[0].conditions.environment = {
        "python": "3.12",
        "tags": ["cpu"],
        "extra": "only-first-class",
    }
    failure = error(lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t")))
    assert failure.code == bridge.INPUT_SHAPE_UNSUPPORTED


def test_canonical_equality_key_order_numeric_and_bool_rules():
    # Object key order is irrelevant and 1 == 1.0 by exact rational value.
    baseline = make_manifest(
        "b",
        conditions=full_conditions("b", environment={"a": 1, "b": {"x": 1}}),
        run_metadata={
            **copy.deepcopy(LEGACY_RUN_METADATA),
            "environment": {"b": {"x": 1.0}, "a": 1.0},
        },
        metric=copy.deepcopy(METRIC),
    )
    target = first_class("t")
    target.runs[0].conditions.environment = {"a": 1, "b": {"x": 1}}
    projection = compare(loaded(baseline, "b"), loaded(target, "t"))
    assert_comparable(projection)
    document = build_input(loaded(baseline, "b"), loaded(target, "t"))
    assert observation_count(document, "environment") == 1

    # A bool is not the number 1.
    baseline = make_manifest(
        "b",
        conditions=full_conditions("b", environment={"value": True}),
        run_metadata={
            **copy.deepcopy(LEGACY_RUN_METADATA),
            "environment": {"value": 1},
        },
    )
    failure = error(
        lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t"))
    )
    assert failure.code == bridge.INPUT_SHAPE_UNSUPPORTED

    # List order remains significant.
    baseline = make_manifest(
        "b",
        conditions=full_conditions("b", environment={"tags": ["cpu", "gpu"]}),
        run_metadata={
            **copy.deepcopy(LEGACY_RUN_METADATA),
            "environment": {"tags": ["gpu", "cpu"]},
        },
    )
    failure = error(
        lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t"))
    )
    assert failure.code == bridge.INPUT_SHAPE_UNSUPPORTED


# --------------------------------------------------------------- selectors


def test_explicit_dataset_selector_selects_among_multiple_datasets():
    baseline = make_manifest(
        "b",
        conditions=full_conditions("b", evaluation_dataset_artifact_id="ds-alt-b"),
        artifacts=[
            dataset("ds-b", first_class_id="primary", sha256=HASH_DATASET),
            dataset("ds-alt-b", first_class_id="selected", sha256=HASH_OTHER),
            Artifact(id="parent-b", path="p", sha256=HASH_PARENT),
            checkpoint("ckpt-b"),
        ],
        relationships=[
            Relationship(source="ds-b", target="run-b", type="input_to"),
            Relationship(source="ds-alt-b", target="run-b", type="input_to"),
            Relationship(source="ckpt-b", target="run-b", type="input_to"),
            Relationship(source="ds-alt-b", target="parent-b", type="derived_from"),
        ],
    )
    document = build_input(loaded(baseline, "b"), loaded(first_class("t"), "t"))
    assert observation_value(document, "dataset_identity") == "selected"
    assert observation_value(document, "dataset_version") == HASH_OTHER
    assert [item["id"] for item in observation_value(document, "preprocessing")] == [
        "parent-b"
    ]


def test_multiple_datasets_without_selector_are_unsupported():
    baseline = make_manifest(
        "b",
        conditions=conditions_without("b", "evaluation_dataset_artifact_id"),
        artifacts=[
            dataset("ds-b", first_class_id="one"),
            dataset("ds-extra-b", first_class_id="two", sha256=HASH_OTHER),
            checkpoint("ckpt-b"),
        ],
        relationships=[
            Relationship(source="ds-b", target="run-b", type="input_to"),
            Relationship(source="ds-extra-b", target="run-b", type="input_to"),
            Relationship(source="ckpt-b", target="run-b", type="input_to"),
        ],
    )
    failure = error(
        lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t"))
    )
    assert failure.code == bridge.INPUT_SHAPE_UNSUPPORTED


def test_multiple_datasets_are_allowed_with_an_explicit_selector():
    baseline = make_manifest(
        "b",
        conditions=full_conditions("b", evaluation_dataset_artifact_id="ds-extra-b"),
        artifacts=[
            dataset("ds-b", first_class_id="one"),
            dataset("ds-extra-b", first_class_id="two", sha256=HASH_OTHER),
            checkpoint("ckpt-b"),
        ],
        relationships=[
            Relationship(source="ds-b", target="run-b", type="input_to"),
            Relationship(source="ds-extra-b", target="run-b", type="input_to"),
            Relationship(source="ckpt-b", target="run-b", type="input_to"),
        ],
    )
    projection = compare(loaded(baseline, "b"), loaded(first_class("t"), "t"))
    assert projection["input_rejected"] is False
    document = build_input(loaded(baseline, "b"), loaded(first_class("t"), "t"))
    assert observation_value(document, "dataset_identity") == "two"


def test_missing_explicit_selector_artifact_never_falls_back():
    baseline = make_manifest(
        "b",
        conditions=full_conditions("b", evaluation_dataset_artifact_id="not-here"),
    )
    failure = error(
        lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t"))
    )
    assert failure.code == bridge.DANGLING_REFERENCE
    assert failure.side == "baseline"


def test_wrong_kind_dataset_selector_fails():
    baseline = make_manifest(
        "b", conditions=full_conditions("b", evaluation_dataset_artifact_id="ckpt-b")
    )
    failure = error(
        lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t"))
    )
    assert failure.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert failure.side == "baseline"


def test_unattached_explicit_selector_fails():
    baseline = make_manifest(
        "b",
        conditions=full_conditions("b", evaluation_dataset_artifact_id="loose-b"),
        artifacts=[
            dataset("ds-b", first_class_id="imagenet"),
            dataset("loose-b", first_class_id="loose"),
            checkpoint("ckpt-b"),
        ],
        relationships=[
            Relationship(source="ds-b", target="run-b", type="input_to"),
            Relationship(source="ckpt-b", target="run-b", type="input_to"),
        ],
    )
    failure = error(
        lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t"))
    )
    assert failure.code == bridge.DANGLING_REFERENCE
    assert failure.side == "baseline"


def test_produced_only_artifact_does_not_satisfy_a_selector():
    produced = dataset("produced-b", first_class_id="produced")
    produced.run_id = "run-b"
    baseline = make_manifest(
        "b",
        conditions=full_conditions("b", evaluation_dataset_artifact_id="produced-b"),
        artifacts=[dataset("ds-b", first_class_id="imagenet"), produced, checkpoint("ckpt-b")],
        relationships=[
            Relationship(source="ds-b", target="run-b", type="input_to"),
            Relationship(source="ckpt-b", target="run-b", type="input_to"),
        ],
    )
    failure = error(
        lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t"))
    )
    assert failure.code == bridge.DANGLING_REFERENCE
    assert failure.side == "baseline"


def test_selector_syntax_is_rechecked_defensively():
    baseline = first_class("b")
    baseline.runs[0].conditions.evaluation_dataset_artifact_id = "bad id"
    failure = error(
        lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t"))
    )
    assert failure.code == bridge.INVALID_SELECTOR
    assert failure.side == "baseline"


def test_unselected_dataset_metadata_is_still_scanned():
    extra = dataset("ds-extra-b", first_class_id="extra")
    extra.metadata["note"] = float("nan")
    baseline = make_manifest(
        "b",
        conditions=full_conditions("b", evaluation_dataset_artifact_id="ds-b"),
        artifacts=[
            dataset("ds-b", first_class_id="imagenet"),
            extra,
            checkpoint("ckpt-b"),
        ],
        relationships=[
            Relationship(source="ds-b", target="run-b", type="input_to"),
            Relationship(source="ds-extra-b", target="run-b", type="input_to"),
            Relationship(source="ckpt-b", target="run-b", type="input_to"),
        ],
    )
    failure = error(
        lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t"))
    )
    assert failure.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert failure.side == "baseline"


def test_unselected_dataset_graph_structure_is_still_validated():
    baseline = make_manifest(
        "b",
        conditions=full_conditions("b", evaluation_dataset_artifact_id="ds-b"),
        artifacts=[
            dataset("ds-b", first_class_id="imagenet"),
            dataset("ds-extra-b", first_class_id="extra"),
            checkpoint("ckpt-b"),
        ],
        relationships=[
            Relationship(source="ds-b", target="run-b", type="input_to"),
            Relationship(source="ds-extra-b", target="run-b", type="input_to"),
            Relationship(source="ckpt-b", target="run-b", type="input_to"),
            Relationship(source="ds-extra-b", target="missing-parent", type="derived_from"),
        ],
    )
    failure = error(
        lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t"))
    )
    assert failure.code == bridge.DANGLING_REFERENCE
    assert failure.side == "baseline"


def test_explicit_checkpoint_selector_selects_among_multiple_checkpoints():
    baseline = make_manifest(
        "b",
        conditions=full_conditions("b", model_checkpoint_artifact_id="ckpt-alt-b"),
        artifacts=[
            dataset("ds-b", first_class_id="imagenet"),
            checkpoint("ckpt-b", sha256=HASH_CHECKPOINT),
            checkpoint("ckpt-alt-b", sha256=HASH_OTHER),
        ],
        relationships=[
            Relationship(source="ds-b", target="run-b", type="input_to"),
            Relationship(source="ckpt-b", target="run-b", type="input_to"),
            Relationship(source="ckpt-alt-b", target="run-b", type="input_to"),
        ],
    )
    document = build_input(loaded(baseline, "b"), loaded(first_class("t"), "t"))
    assert observation_value(document, "model_checkpoint") == HASH_OTHER


def test_multiple_checkpoints_without_selector_are_unsupported():
    baseline = make_manifest(
        "b",
        conditions=conditions_without("b", "model_checkpoint_artifact_id"),
        artifacts=[
            dataset("ds-b", first_class_id="imagenet"),
            checkpoint("ckpt-b"),
            checkpoint("ckpt-alt-b", sha256=HASH_OTHER),
        ],
        relationships=[
            Relationship(source="ds-b", target="run-b", type="input_to"),
            Relationship(source="ckpt-b", target="run-b", type="input_to"),
            Relationship(source="ckpt-alt-b", target="run-b", type="input_to"),
        ],
    )
    failure = error(
        lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t"))
    )
    assert failure.code == bridge.INPUT_SHAPE_UNSUPPORTED


def test_wrong_kind_checkpoint_selector_fails():
    baseline = make_manifest(
        "b", conditions=full_conditions("b", model_checkpoint_artifact_id="ds-b")
    )
    failure = error(
        lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t"))
    )
    assert failure.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert failure.side == "baseline"


def test_missing_checkpoint_selector_never_falls_back():
    baseline = make_manifest(
        "b",
        conditions=full_conditions("b", model_checkpoint_artifact_id="not-here"),
    )
    failure = error(
        lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t"))
    )
    assert failure.code == bridge.DANGLING_REFERENCE
    assert failure.side == "baseline"


def test_produced_only_checkpoint_does_not_satisfy_a_selector():
    produced = checkpoint("produced-b")
    produced.run_id = "run-b"
    baseline = make_manifest(
        "b",
        conditions=full_conditions("b", model_checkpoint_artifact_id="produced-b"),
        artifacts=[dataset("ds-b", first_class_id="imagenet"), produced],
        relationships=[
            Relationship(source="ds-b", target="run-b", type="input_to"),
        ],
    )
    failure = error(
        lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t"))
    )
    assert failure.code == bridge.DANGLING_REFERENCE
    assert failure.side == "baseline"


# ---------------------------------------------- selected file observations


def test_selected_dataset_volatile_or_unhashed_version_is_missing():
    for volatile, sha in ((True, HASH_DATASET), (False, None)):
        baseline = make_manifest(
            "b",
            conditions=full_conditions("b", evaluation_dataset_artifact_id="ds-b"),
            artifacts=[
                dataset("ds-b", first_class_id="imagenet", volatile=volatile, sha256=sha),
                checkpoint("ckpt-b"),
            ],
            relationships=[
                Relationship(source="ds-b", target="run-b", type="input_to"),
                Relationship(source="ckpt-b", target="run-b", type="input_to"),
            ],
        )
        document = build_input(loaded(baseline, "b"), loaded(first_class("t"), "t"))
        assert "dataset_version" not in document["baseline"]["dimensions"]


def test_selected_checkpoint_volatile_or_unhashed_version_is_missing():
    for volatile, sha in ((True, HASH_CHECKPOINT), (False, None)):
        baseline = make_manifest(
            "b",
            conditions=full_conditions("b", model_checkpoint_artifact_id="ckpt-b"),
            artifacts=[
                dataset("ds-b", first_class_id="imagenet"),
                checkpoint("ckpt-b", volatile=volatile, sha256=sha),
            ],
            relationships=[
                Relationship(source="ds-b", target="run-b", type="input_to"),
                Relationship(source="ckpt-b", target="run-b", type="input_to"),
            ],
        )
        document = build_input(loaded(baseline, "b"), loaded(first_class("t"), "t"))
        assert "model_checkpoint" not in document["baseline"]["dimensions"]


def test_selected_directory_dataset_or_checkpoint_is_rejected():
    for selector, kind in (
        ("evaluation_dataset_artifact_id", "dataset"),
        ("model_checkpoint_artifact_id", "checkpoint"),
    ):
        artifacts = [dataset("ds-b", first_class_id="imagenet"), checkpoint("ckpt-b")]
        if kind == "dataset":
            artifacts[0] = dataset(
                "ds-b", first_class_id="imagenet", artifact_type="directory"
            )
        else:
            artifacts[1] = checkpoint("ckpt-b", artifact_type="directory")
        baseline = make_manifest(
            "b",
            conditions=full_conditions("b", **{selector: "ds-b" if kind == "dataset" else "ckpt-b"}),
            artifacts=artifacts,
            relationships=[
                Relationship(source="ds-b", target="run-b", type="input_to"),
                Relationship(source="ckpt-b", target="run-b", type="input_to"),
            ],
        )
        failure = error(
            lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t"))
        )
        assert failure.code == bridge.INPUT_SHAPE_UNSUPPORTED
        assert failure.side == "baseline"


# --------------------------------------------------------------- precedence


def test_baseline_selector_failure_wins_over_target_failure():
    baseline = make_manifest(
        "b",
        conditions=full_conditions("b", evaluation_dataset_artifact_id="not-here"),
    )
    failure = error(
        lambda: bridge.compare_recorded_conditions(
            loaded(baseline, "b"),
            loaded(first_class("t"), "t"),
            baseline_result_id="res-b",
            target_result_id="   ",
        )
    )
    assert failure.code == bridge.DANGLING_REFERENCE
    assert failure.side == "baseline"


def test_baseline_bound_beats_target_selector_failure():
    baseline = first_class("b")
    baseline.runs[0].conditions.evaluation_split = "x" * 1025
    failure = error(
        lambda: bridge.compare_recorded_conditions(
            loaded(baseline, "b"),
            loaded(first_class("t"), "t"),
            baseline_result_id="res-b",
            target_result_id="   ",
        )
    )
    assert failure.code == bridge.INPUT_BOUND_EXCEEDED
    assert failure.side == "baseline"


def test_baseline_bound_beats_target_shape():
    baseline = first_class("b")
    baseline.runs[0].conditions.evaluation_split = "x" * 1025
    target = first_class("t")
    target.runs[0].conditions.evaluation_split = 5
    failure = error(lambda: compare(loaded(baseline, "b"), loaded(target, "t")))
    assert failure.code == bridge.INPUT_BOUND_EXCEEDED
    assert failure.side == "baseline"


def test_baseline_shape_beats_target_bound():
    baseline = mixed("b")
    baseline.runs[0].metadata["evaluation"]["protocol"] = {"protocol_id": ""}
    target = first_class("t")
    target.runs[0].conditions.evaluation_split = "x" * 1025
    failure = error(lambda: compare(loaded(baseline, "b"), loaded(target, "t")))
    assert failure.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert failure.side == "baseline"


def test_baseline_conflict_beats_target_selector_failure():
    baseline = mixed("b")
    baseline.runs[0].conditions.evaluation_split = "train"
    failure = error(
        lambda: bridge.compare_recorded_conditions(
            loaded(baseline, "b"),
            loaded(first_class("t"), "t"),
            baseline_result_id="res-b",
            target_result_id="   ",
        )
    )
    assert failure.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert failure.side == "baseline"


def test_target_conflict_reports_target():
    target = mixed("t")
    target.runs[0].conditions.evaluation_split = "train"
    failure = error(
        lambda: compare(loaded(first_class("b"), "b"), loaded(target, "t"))
    )
    assert failure.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert failure.side == "target"


def test_target_bound_reports_target():
    target = first_class("t")
    target.runs[0].conditions.evaluation_split = "x" * 1025
    failure = error(
        lambda: compare(loaded(first_class("b"), "b"), loaded(target, "t"))
    )
    assert failure.code == bridge.INPUT_BOUND_EXCEEDED
    assert failure.side == "target"


def test_conflict_candidate_bound_wins_over_conflict(monkeypatch):
    baseline = mixed("b")
    baseline.runs[0].metadata["evaluation"]["split"] = "x" * 1025
    calls: list[str] = []

    def trap(*args, **kwargs):
        calls.append("copy")
        raise AssertionError("materialization ran")

    monkeypatch.setattr(bridge, "_copy_json_exact", trap)
    monkeypatch.setattr(bridge, "_ordered_ids", trap)

    failure = error(lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t")))
    assert failure.code == bridge.INPUT_BOUND_EXCEEDED
    assert failure.side == "baseline"
    assert calls == []


def test_oversized_environment_candidate_wins_over_conflict():
    baseline = mixed("b")
    baseline.runs[0].conditions.environment = {"blob": "x" * 17000}
    failure = error(lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t")))
    assert failure.code == bridge.INPUT_BOUND_EXCEEDED
    assert failure.side == "baseline"


def test_conflict_fails_before_materialization(monkeypatch):
    baseline = mixed("b")
    baseline.runs[0].conditions.source_revision = "different"
    calls: list[str] = []
    monkeypatch.setattr(bridge, "_copy_json_exact", lambda *a, **k: calls.append("copy"))
    monkeypatch.setattr(bridge, "_ordered_ids", lambda *a, **k: calls.append("sort"))

    failure = error(lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t")))
    assert failure.code == bridge.INPUT_SHAPE_UNSUPPORTED
    assert calls == []


def _nested_lists(depth: int):
    value = "leaf"
    for _ in range(depth):
        value = [value]
    return value


def _long_parent_artifacts(count: int = 80):
    artifacts = []
    relationships = []
    for index in range(count):
        identifier = "p" * 1020 + f"{index:04d}"
        artifacts.append(
            Artifact(id=identifier, path=f"parents/{index}", sha256=HASH_PARENT)
        )
        relationships.append(
            Relationship(source="ds-b", target=identifier, type="derived_from")
        )
    return artifacts, relationships


@pytest.mark.parametrize("side", ["baseline", "target"])
def test_deep_legacy_metric_definition_is_a_bound_not_an_internal_error(side):
    deep = {"name": _nested_lists(550)}
    baseline = mixed("b")
    target = mixed("t")
    if side == "baseline":
        baseline.results[0].metadata["metric_definition"] = deep
    else:
        target.results[0].metadata["metric_definition"] = deep

    failure = error(lambda: compare(loaded(baseline, "b"), loaded(target, "t")))
    assert failure.code == bridge.INPUT_BOUND_EXCEEDED
    assert failure.side == side
    assert failure.code != bridge.INTERNAL_MAPPING_ERROR


def test_oversized_environment_is_a_bound_not_a_conflict():
    # First-class oversized environment.
    baseline = mixed("b")
    baseline.runs[0].conditions.environment = {"blob": "x" * 17000}
    failure = error(lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t")))
    assert failure.code == bridge.INPUT_BOUND_EXCEEDED
    assert failure.side == "baseline"

    # Legacy oversized environment, necessarily conflicting with the small
    # first-class value. The bound still wins.
    baseline = mixed("b")
    baseline.runs[0].metadata["environment"] = {"blob": "x" * 17000}
    failure = error(lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t")))
    assert failure.code == bridge.INPUT_BOUND_EXCEEDED
    assert failure.side == "baseline"


@pytest.mark.parametrize("with_conflict", [False, True])
def test_aggregate_record_bound_precedes_source_revision_conflict(with_conflict):
    baseline = mixed("b")
    artifacts, relationships = _long_parent_artifacts()
    baseline.artifacts.extend(artifacts)
    baseline.relationships.extend(relationships)
    if with_conflict:
        baseline.runs[0].conditions.source_revision = "rev-2"

    failure = error(lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t")))
    assert failure.code == bridge.INPUT_BOUND_EXCEEDED
    assert failure.side == "baseline"


def test_baseline_bound_beats_target_conflict():
    baseline = mixed("b")
    baseline.results[0].metadata["metric_definition"] = {
        "name": _nested_lists(550)
    }
    target = mixed("t")
    target.runs[0].conditions.source_revision = "rev-2"
    failure = error(lambda: compare(loaded(baseline, "b"), loaded(target, "t")))
    assert failure.code == bridge.INPUT_BOUND_EXCEEDED
    assert failure.side == "baseline"


def test_mixed_0_1_bound_beats_target_0_2_conflict():
    baseline = loaded(legacy("b", schema="0.1"), "b")
    baseline.manifest.runs[0].metadata["evaluation"]["split"] = "x" * 1025
    target = mixed("t")
    target.runs[0].conditions.source_revision = "rev-2"
    failure = error(lambda: compare(baseline, loaded(target, "t")))
    assert failure.code == bridge.INPUT_BOUND_EXCEEDED
    assert failure.side == "baseline"



# -------------------------------------------------------- mixed 0.1 / 0.2


def test_mixed_legacy_baseline_and_first_class_target_are_comparable():
    projection = compare(loaded(legacy("b"), "b"), loaded(first_class("t"), "t"))
    assert_comparable(projection)


def test_mixed_first_class_baseline_and_legacy_target_are_comparable():
    projection = compare(loaded(first_class("b"), "b"), loaded(legacy("t"), "t"))
    assert_comparable(projection)


def test_mixed_versions_keep_baseline_first_precedence():
    baseline = first_class("b")
    baseline.runs[0].conditions.evaluation_split = "x" * 1025
    target = legacy("t")
    target.runs[0].metadata["evaluation"]["split"] = 5
    failure = error(lambda: compare(loaded(baseline, "b"), loaded(target, "t")))
    assert failure.code == bridge.INPUT_BOUND_EXCEEDED
    assert failure.side == "baseline"


def test_0_1_ignores_a_constructed_first_class_metric_definition():
    manifest = legacy("b", schema="0.1")
    manifest.results[0].metric_definition = MetricDefinition(
        definition_id="constructed", higher_is_better=False
    )
    manifest.results[0].__pydantic_fields_set__.add("metric_definition")
    document = build_input(loaded(manifest, "b"), loaded(legacy("t"), "t"))
    assert observation_value(document, "metric_definition") == {
        "definition_id": "accuracy-v1",
        "higher_is_better": True,
    }


# --------------------------------------------------------------- privacy


CANARIES = (
    "STAGE3B2ACANARY",
    "secret-dataset",
    "secret-protocol",
    "secret-revision",
    "secret-environment",
    "secret-metric",
    "secret-run",
    "secret-result",
)


def test_conflict_error_contains_no_first_class_values():
    baseline = mixed("b")
    baseline.runs[0].name = "secret-run"
    baseline.results[0].id = "secret-result"
    baseline.results[0].run_id = "run-b"
    baseline.runs[0].conditions.evaluation_split = "STAGE3B2ACANARY"
    baseline.artifacts[0].dataset_id = "secret-dataset"
    baseline.runs[0].conditions.evaluation_protocol = EvaluationProtocol(
        protocol_id="secret-protocol"
    )
    baseline.runs[0].conditions.source_revision = "secret-revision"
    baseline.runs[0].conditions.environment = {"secret": "secret-environment"}

    failure = error(lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t")))
    assert failure.code == bridge.INPUT_SHAPE_UNSUPPORTED
    rendered = f"{failure!r} {failure} {failure.args!r} {failure.code} {failure.side}"
    for secret in CANARIES:
        assert secret not in rendered


def test_selector_error_contains_no_selector_value():
    baseline = first_class("b")
    baseline.runs[0].conditions.evaluation_dataset_artifact_id = "STAGE3B2ACANARY"
    failure = error(lambda: compare(loaded(baseline, "b"), loaded(first_class("t"), "t")))
    assert failure.code == bridge.DANGLING_REFERENCE
    rendered = f"{failure!r} {failure} {failure.args!r} {failure.code} {failure.side}"
    assert "STAGE3B2ACANARY" not in rendered


def test_comparable_projection_contains_no_values_or_ids():
    baseline = first_class("b")
    baseline.runs[0].name = "secret-run"
    baseline.results[0].name = "secret-result-metric-name"
    projection = compare(loaded(baseline, "b"), loaded(first_class("t"), "t"))
    rendered = json.dumps(projection, sort_keys=True)
    for secret in ("secret-run", "secret-result-metric-name", "imagenet", HASH_DATASET):
        assert secret not in rendered


def test_cli_0_2_conflict_is_sanitized(tmp_path):
    baseline = mixed("b")
    baseline.runs[0].conditions.evaluation_split = "STAGE3B2ACANARY"
    baseline_path = tmp_path / "baseline.json"
    target_path = tmp_path / "target.json"
    baseline_path.write_text(dump_manifest(baseline), encoding="utf-8")
    target_path.write_text(dump_manifest(first_class("t")), encoding="utf-8")

    outcome = command.compare_paths(
        baseline_path,
        target_path,
        baseline_result_id="res-b",
        target_result_id="res-t",
    )
    assert outcome.exit_code == 2
    assert outcome.document["error"] == {
        "code": command.MAPPING_FAILED,
        "side": "baseline",
        "mapping_code": bridge.INPUT_SHAPE_UNSUPPORTED,
    }
    rendered = json.dumps(outcome.document, sort_keys=True) + outcome.text
    for secret in CANARIES:
        assert secret not in rendered


# ------------------------------------------------------------------ no I/O


def test_0_2_comparison_performs_no_io(monkeypatch):
    def trap(*args, **kwargs):
        raise AssertionError("I/O attempted")

    monkeypatch.setattr(builtins, "open", trap)
    monkeypatch.setattr(io_module, "open", trap)
    monkeypatch.setattr(Path, "open", trap)
    monkeypatch.setattr(Path, "read_bytes", trap)
    monkeypatch.setattr(Path, "read_text", trap)
    monkeypatch.setattr(socket, "socket", trap)
    monkeypatch.setattr(urllib.request, "urlopen", trap)

    projection = compare(*pair())
    assert_comparable(projection)


# ------------------------------------------------------------- determinism


def test_permuted_collection_and_key_order_is_byte_identical():
    def arrange(env, reverse: bool) -> tuple[LoadedManifest, LoadedManifest]:
        baseline = make_manifest(
            "b",
            conditions=full_conditions(
                "b", environment=env, evaluation_dataset_artifact_id="ds-b"
            ),
            artifacts=[
                dataset("ds-b", first_class_id="imagenet"),
                dataset("ds-alt-b", first_class_id="other", sha256=HASH_OTHER),
                checkpoint("ckpt-b"),
            ],
            relationships=[
                Relationship(source="ds-b", target="run-b", type="input_to"),
                Relationship(source="ds-alt-b", target="run-b", type="input_to"),
                Relationship(source="ckpt-b", target="run-b", type="input_to"),
            ],
        )
        target = first_class("t")
        if reverse:
            baseline.artifacts.reverse()
            baseline.relationships.reverse()
        return loaded(baseline, "b"), loaded(target, "t")

    env_a = {"python": "3.12", "tags": ["cpu"], "cuda": "12"}
    env_b = {"cuda": "12", "tags": ["cpu"], "python": "3.12"}
    first = build_input(*arrange(env_a, False))
    second = build_input(*arrange(env_b, True))
    assert json.dumps(first, sort_keys=False) == json.dumps(second, sort_keys=False)


def test_dump_reload_keeps_0_2_interpretation(tmp_path):
    baseline = mixed("b")
    target = first_class("t")
    baseline_path = tmp_path / "baseline.json"
    target_path = tmp_path / "target.json"
    baseline_path.write_text(dump_manifest(baseline), encoding="utf-8")
    target_path.write_text(dump_manifest(target), encoding="utf-8")
    projection = compare(load_manifest(baseline_path), load_manifest(target_path))
    assert_comparable(projection)
