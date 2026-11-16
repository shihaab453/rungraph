"""Schema 0.9: canonical tensor digests, locators, dispatch, migration and shape."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from runevidence.manifest import load_manifest, validate_manifest
from runevidence.migrate import MigrationError, migrate_manifest
from runevidence.models import (
    Manifest09,
    TensorValue,
    is_manifest,
    json_schema_0_9,
)
from runevidence.run_comparability import _UNSUPPORTED_COMPARABILITY_SCHEMAS

DIGEST = "a" * 64


def tensor(**overrides):
    value = {
        "kind": "tensor",
        "dtype": "float32",
        "shape": [2, 3],
        "element_count": 6,
        "sha256": DIGEST,
    }
    value.update(overrides)
    return value


def document09(**overrides):
    document = {
        "schema_version": "0.9",
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "r", "name": "run"}],
        "artifacts": [
            {
                "id": "a",
                "path": "logs/e.tfevents",
                "sha256": DIGEST,
                "size_bytes": 1,
                "run_id": "r",
            }
        ],
        "results": [
            {
                "id": "res",
                "run_id": "r",
                "name": "logits @ 0 (tensor)",
                "value": tensor(),
                "artifact_id": "a",
                "locator": {
                    "kind": "tensorboard-tensor",
                    "tag": "logits",
                    "step": 0,
                    "occurrence": 0,
                },
            }
        ],
        "evidence": [],
    }
    document.update(overrides)
    return document


def test_manifest09_accepts_a_tensor():
    manifest = validate_manifest(document09())
    assert isinstance(manifest, Manifest09)
    assert isinstance(manifest.results[0].value, TensorValue)
    assert is_manifest(manifest)


def test_schema_08_rejects_a_tensor_locator():
    with pytest.raises(ValidationError):
        validate_manifest(document09(schema_version="0.8"))


@pytest.mark.parametrize(
    "overrides",
    [
        {"element_count": 7},
        {"element_count": 0},
        {"element_count": 1_000_001},
        {"shape": []},
        {"shape": [0]},
        {"shape": [-1]},
        {"shape": [1] * 9},
        {"shape": [2**31]},
        {"dtype": "float16"},
        {"sha256": DIGEST.upper()},
        {"sha256": "a" * 63},
        {"extra": 1},
    ],
)
def test_tensor_value_rejects_invalid(overrides):
    with pytest.raises(ValidationError):
        validate_manifest(
            document09(results=[{**document09()["results"][0], "value": tensor(**overrides)}])
        )


def test_tensor_value_accepts_each_dtype():
    for dtype in ("float32", "float64", "int32", "int64"):
        assert TensorValue.model_validate(tensor(dtype=dtype)).dtype == dtype


def test_tensor_value_locator_cross_rule():
    document = document09()
    document["results"][0]["locator"] = {
        "kind": "tensorboard-image",
        "tag": "t",
        "step": 0,
        "occurrence": 0,
    }
    with pytest.raises(ValidationError):
        validate_manifest(document)


def test_scalar_on_tensor_locator_is_schema_valid():
    document = document09()
    document["results"][0]["value"] = 1.5
    validate_manifest(document)


def test_manifest09_requires_artifact_id():
    document = document09()
    document["results"][0]["artifact_id"] = None
    with pytest.raises(ValidationError):
        validate_manifest(document)


def test_manifest09_admits_a_jsonl_locator():
    document = document09()
    document["results"][0] = {
        "id": "line",
        "run_id": "r",
        "name": "score",
        "value": 1.0,
        "artifact_id": "a",
        "locator": {"kind": "jsonl", "line": 1, "pointer": "/score"},
    }
    assert isinstance(validate_manifest(document), Manifest09)


def test_json_schema_09_shape():
    schema = json_schema_0_9()
    assert schema["$id"] == "urn:runevidence:manifest:0.9"
    defs = schema["$defs"]
    assert len(defs["Result09"]["properties"]["locator"]["oneOf"]) == 10
    assert len(defs["Result09"]["allOf"]) == 5
    refs = [
        entry["$ref"]
        for entry in defs["Result09"]["properties"]["value"]["anyOf"]
        if "$ref" in entry
    ]
    assert refs[-1] == "#/$defs/TensorValue"
    assert defs["TensorValue"]["required"] == [
        "kind",
        "dtype",
        "shape",
        "element_count",
        "sha256",
    ]
    assert defs["TensorValue"]["properties"]["shape"]["maxItems"] == 8
    assert defs["TensorValue"]["properties"]["element_count"]["maximum"] == 1_000_000


def test_json_schema_09_has_no_cross_kind_escape():
    schema = json_schema_0_9()
    document = document09()
    document["results"][0]["locator"] = {
        "kind": "tensorboard-audio",
        "tag": "t",
        "step": 0,
        "occurrence": 0,
    }
    assert list(Draft202012Validator(schema).iter_errors(document))
    document = document09()
    document["results"][0]["value"]["sha256"] = DIGEST.upper()
    assert list(Draft202012Validator(schema).iter_errors(document))


def test_migrate_08_to_09_is_identity(tmp_path):
    path = tmp_path / "runevidence.json"
    document = document09(schema_version="0.8")
    document["results"][0]["value"] = 1.0
    document["results"][0]["locator"] = {
        "kind": "tensorboard",
        "tag": "w",
        "step": 0,
        "occurrence": 0,
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    before = json.loads(path.read_text(encoding="utf-8"))
    assert migrate_manifest(path, target_version="0.9").changed
    after = json.loads(path.read_text(encoding="utf-8"))
    before["schema_version"] = "0.9"
    assert before == after


def test_load_manifest_dispatches_09(tmp_path):
    path = tmp_path / "runevidence.json"
    path.write_text(json.dumps(document09()), encoding="utf-8")
    assert isinstance(load_manifest(path).manifest, Manifest09)


def test_schema_09_is_unsupported_by_comparability():
    assert "0.9" in _UNSUPPORTED_COMPARABILITY_SCHEMAS


def test_migrate_09_to_08_is_a_downgrade(tmp_path):
    path = tmp_path / "runevidence.json"
    path.write_text(json.dumps(document09()), encoding="utf-8")
    with pytest.raises(MigrationError) as excinfo:
        migrate_manifest(path, target_version="0.8")
    assert excinfo.value.code == "DOWNGRADE_UNSUPPORTED"


def test_directory_hint_names_tensorboard_for_09():
    from runevidence.checks import results as results_module
    from runevidence.models import Manifest09

    class _DirectoryCheck:
        state = "directory"

    document = document09()
    document["artifacts"][0].update(
        {
            "path": "logs",
            "artifact_type": "directory",
            "kind": "directory",
            "digest_format": "runevidence-directory-v1",
        }
    )
    document["results"][0]["locator"] = {
        "kind": "tensorboard-tensor",
        "tag": "t",
        "step": 0,
        "occurrence": 0,
    }
    manifest = validate_manifest(document)
    assert isinstance(manifest, Manifest09)
    findings, _broken, _capped = results_module.check_targeted_results(
        manifest, manifest.artifacts[0], _DirectoryCheck()
    )
    hints = [finding.hint or "" for finding in findings]
    assert any("TensorBoard event" in hint for hint in hints), hints
