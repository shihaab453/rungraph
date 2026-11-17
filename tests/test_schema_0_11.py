"""Schema 0.11 structured TF2 histogram model tests."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from runevidence.manifest import artifact_to_dict
from runevidence.models import (
    SCHEMA_VERSION_0_11,
    Manifest10,
    Manifest11,
    Tf2HistogramValue,
    TensorboardTf2HistogramLocator,
    json_schema_0_11,
)


def base_result(**changes):
    result = {
        "id": "h",
        "run_id": "run",
        "name": "layer @ 0 (tf2 histogram)",
        "value": {
            "kind": "tf2-histogram",
            "buckets": [{"left": 0.0, "right": 1.0, "count": 2.0}],
        },
        "artifact_id": "events",
        "locator": {
            "kind": "tensorboard-tf2-histogram",
            "tag": "layer",
            "step": 0,
            "occurrence": 0,
        },
    }
    result.update(changes)
    return result


def doc(**changes):
    document = {
        "schema_version": "0.11",
        "project": {"id": "p", "name": "p"},
        "runs": [{"id": "run"}],
        "artifacts": [
            {"id": "events", "path": "events.out.tfevents.x", "sha256": "0" * 64, "size_bytes": 1}
        ],
        "results": [base_result()],
    }
    document.update(changes)
    return document


def test_schema_0_11_round_trip():
    manifest = Manifest11.model_validate(doc())
    assert manifest.schema_version == SCHEMA_VERSION_0_11
    value = manifest.results[0].value
    assert isinstance(value, Tf2HistogramValue)
    assert value.buckets[0].left == 0.0
    locator = manifest.results[0].locator
    assert isinstance(locator, TensorboardTf2HistogramLocator)
    assert json.loads(manifest.model_dump_json())["schema_version"] == "0.11"


def test_zero_and_1024_buckets_are_accepted():
    assert Tf2HistogramValue.model_validate({"kind": "tf2-histogram", "buckets": []}).buckets == []
    buckets = [{"left": i, "right": i + 1, "count": 1} for i in range(1024)]
    value = Tf2HistogramValue.model_validate({"kind": "tf2-histogram", "buckets": buckets})
    assert len(value.buckets) == 1024


def test_1025_buckets_are_refused():
    buckets = [{"left": i, "right": i + 1, "count": 1} for i in range(1025)]
    with pytest.raises(ValidationError):
        Tf2HistogramValue.model_validate({"kind": "tf2-histogram", "buckets": buckets})


@pytest.mark.parametrize(
    "bucket",
    [
        {"left": 0, "right": 1, "count": -1},
        {"left": 1, "right": 0, "count": 1},
        {"left": float("nan"), "right": 1, "count": 1},
        {"left": 0, "right": float("inf"), "count": 1},
    ],
)
def test_invalid_buckets_are_refused(bucket):
    with pytest.raises(ValidationError):
        Tf2HistogramValue.model_validate({"kind": "tf2-histogram", "buckets": [bucket]})


def test_equal_edges_are_a_valid_single_value_histogram():
    value = Tf2HistogramValue.model_validate(
        {"kind": "tf2-histogram", "buckets": [{"left": 3, "right": 3, "count": 5}]}
    )
    assert value.buckets[0].left == value.buckets[0].right


@pytest.mark.parametrize(
    "locator",
    [
        None,
        {"kind": "tensorboard-tf2-histogram", "tag": "layer", "step": 0, "occurrence": 0},
    ],
)
def test_value_locator_matrix(locator):
    result = base_result(locator=locator)
    manifest = Manifest11.model_validate(doc(results=[result]))
    assert manifest.results[0].value.kind == "tf2-histogram"


def test_a_tf2_histogram_value_requires_its_locator_kind():
    with pytest.raises(ValidationError):
        Manifest11.model_validate(
            doc(results=[base_result(locator={"kind": "tensorboard-tensor", "tag": "layer", "step": 0, "occurrence": 0})])
        )


def test_schema_0_10_rejects_the_new_value_and_locator():
    with pytest.raises(ValidationError):
        Manifest10.model_validate(doc(schema_version="0.10"))
    result = base_result(
        value=0.5,
        locator={
            "kind": "tensorboard-tf2-histogram",
            "tag": "layer",
            "step": 0,
            "occurrence": 0,
        },
    )
    with pytest.raises(ValidationError):
        Manifest10.model_validate(doc(schema_version="0.10", results=[result]))


def test_a_locator_requires_artifact_id():
    with pytest.raises(ValidationError):
        Manifest11.model_validate(doc(results=[base_result(artifact_id=None)]))


def test_artifact_serialization_accepts_0_11():
    artifact = Manifest11.model_validate(doc()).artifacts[0]
    assert artifact_to_dict(artifact, schema_version="0.11")["path"] == artifact.path


def test_generated_schema_has_the_new_definitions():
    schema = json_schema_0_11()
    assert schema["properties"]["schema_version"]["const"] == "0.11"
    assert schema["$id"] == "urn:runevidence:manifest:0.11"
    assert "Tf2HistogramValue" in schema["$defs"]
    assert "TensorboardTf2HistogramLocator" in schema["$defs"]
    assert schema["$defs"]["Tf2HistogramValue"]["properties"]["buckets"]["maxItems"] == 1024
