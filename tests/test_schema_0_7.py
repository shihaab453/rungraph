"""Schema 0.7: histogram values, locators, dispatch, migration and schema shape."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from runevidence.manifest import load_manifest, validate_manifest
from runevidence.migrate import migrate_manifest
from runevidence.run_comparability import _UNSUPPORTED_COMPARABILITY_SCHEMAS
from runevidence.models import (
    HistogramValue,
    Manifest07,
    Result07,
    TensorboardHistogramLocator,
    is_manifest,
    json_schema_0_7,
)


def histogram(**overrides):
    value = {
        "kind": "histogram",
        "min": 0.0,
        "max": 1.0,
        "num": 2.0,
        "sum": 0.5,
        "sum_squares": 0.25,
        "bucket_limit": [0.5],
        "bucket": [2.0],
    }
    value.update(overrides)
    return value


def document07(**overrides):
    document = {
        "schema_version": "0.7",
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "r", "name": "run"}],
        "artifacts": [
            {
                "id": "a",
                "path": "logs/e.tfevents",
                "sha256": "a" * 64,
                "size_bytes": 1,
                "run_id": "r",
            }
        ],
        "results": [
            {
                "id": "res",
                "run_id": "r",
                "name": "w @ 0 (histogram)",
                "value": histogram(),
                "artifact_id": "a",
                "locator": {
                    "kind": "tensorboard-histogram",
                    "tag": "w",
                    "step": 0,
                    "occurrence": 0,
                },
            }
        ],
        "evidence": [],
    }
    document.update(overrides)
    return document


def test_manifest07_accepts_a_histogram():
    manifest = validate_manifest(document07())
    assert isinstance(manifest, Manifest07)
    assert isinstance(manifest.results[0], Result07)
    assert isinstance(manifest.results[0].value, HistogramValue)
    assert isinstance(
        manifest.results[0].locator, TensorboardHistogramLocator
    )
    assert is_manifest(manifest)


def test_schema_06_rejects_a_histogram_locator():
    with pytest.raises(ValidationError):
        validate_manifest(document07(schema_version="0.6"))


def test_histogram_value_locator_cross_rule():
    document = document07()
    document["results"][0]["locator"] = "/value"
    with pytest.raises(ValidationError):
        validate_manifest(document)


@pytest.mark.parametrize(
    "overrides",
    [
        {"num": -1.0},
        {"bucket": [-1.0]},
        {"min": float("nan")},
        {"max": float("inf")},
        {"bucket_limit": [1.0, 0.0], "bucket": [1.0, 1.0]},
        {"bucket_limit": [0.0], "bucket": [1.0, 2.0]},
        {"num": True},
        {"num": "2"},
        {"num": 9007199254740993},
        {"bucket_limit": list(range(1025)), "bucket": [1.0] * 1025},
    ],
)
def test_histogram_value_rejects_invalid(overrides):
    with pytest.raises(ValidationError):
        validate_manifest(document07(results=[{**document07()["results"][0], "value": histogram(**overrides)}]))


def test_histogram_value_normalizes_negative_zero():
    value = HistogramValue.model_validate(
        histogram(min=-0.0, bucket_limit=[-0.0], bucket=[-0.0])
    )
    assert repr(value.min) in ("0.0", "-0.0")
    assert value.model_dump(mode="json")["min"] == 0.0
    assert value.model_dump(mode="json")["bucket"] == [0.0]


def test_json_schema_07_shape():
    schema = json_schema_0_7()
    assert schema["$id"] == "urn:runevidence:manifest:0.7"
    assert len(schema["$defs"]["Result07"]["properties"]["locator"]["oneOf"]) == 7
    assert "HistogramValue" in schema["$defs"]
    hist = schema["$defs"]["HistogramValue"]
    assert "kind" in hist["required"]
    assert hist["properties"]["bucket"]["items"]["minimum"] == 0
    assert hist["properties"]["bucket"]["maxItems"] == 1024
    assert "allOf" in schema["$defs"]["Result07"]


def test_json_schema_07_rejects_bad_histograms():
    schema = json_schema_0_7()
    document = document07()
    document["results"][0]["value"]["bucket"] = [-1.0]
    assert list(Draft202012Validator(schema).iter_errors(document))
    document = document07()
    del document["results"][0]["value"]["kind"]
    assert list(Draft202012Validator(schema).iter_errors(document))


def test_migrate_06_to_07_is_identity(tmp_path):
    path = tmp_path / "runevidence.json"
    document = document07(schema_version="0.6")
    document["results"][0]["locator"] = {
        "kind": "tensorboard",
        "tag": "w",
        "step": 0,
        "occurrence": 0,
    }
    document["results"][0]["value"] = 1.0
    path.write_text(json.dumps(document), encoding="utf-8")
    before = json.loads(path.read_text(encoding="utf-8"))
    result = migrate_manifest(path, target_version="0.7")
    after = json.loads(path.read_text(encoding="utf-8"))
    assert result.changed
    before["schema_version"] = "0.7"
    assert before == after


def test_load_manifest_dispatches_07(tmp_path):
    path = tmp_path / "runevidence.json"
    path.write_text(json.dumps(document07()), encoding="utf-8")
    loaded = load_manifest(path)
    assert isinstance(loaded.manifest, Manifest07)


def test_schema_07_is_unsupported_by_comparability():
    assert "0.7" in _UNSUPPORTED_COMPARABILITY_SCHEMAS


def test_migrate_07_to_06_is_a_downgrade(tmp_path):
    path = tmp_path / "runevidence.json"
    path.write_text(json.dumps(document07()), encoding="utf-8")
    from runevidence.migrate import MigrationError

    with pytest.raises(MigrationError) as excinfo:
        migrate_manifest(path, target_version="0.6")
    assert excinfo.value.code == "DOWNGRADE_UNSUPPORTED"
