"""Schema 0.6: string-tensor locators, dispatch, migration and schema shape."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from runevidence.manifest import artifact_to_dict, load_manifest, validate_manifest
from runevidence.migrate import migrate_manifest
from runevidence.run_claim import _display_details
from runevidence.models import (
    Artifact,
    Manifest06,
    Result06,
    TensorboardStringLocator,
    is_manifest,
    json_schema_0_6,
)


def _manifest06(**overrides):
    document = {
        "schema_version": "0.6",
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
                "name": "cfg @ 0 (text)",
                "value": "hello",
                "artifact_id": "a",
                "locator": {
                    "kind": "tensorboard-string",
                    "tag": "cfg",
                    "step": 0,
                    "occurrence": 0,
                },
            }
        ],
        "evidence": [],
    }
    document.update(overrides)
    return document


def test_manifest06_accepts_a_string_locator():
    manifest = validate_manifest(_manifest06())
    assert isinstance(manifest, Manifest06)
    assert isinstance(manifest.results[0], Result06)
    assert isinstance(manifest.results[0].locator, TensorboardStringLocator)
    assert is_manifest(manifest)


def test_schema_05_rejects_a_string_locator():
    document = _manifest06(schema_version="0.5")
    with pytest.raises(ValidationError):
        validate_manifest(document)


def test_schema_06_requires_artifact_id_for_a_text_locator():
    document = _manifest06()
    document["results"][0]["artifact_id"] = None
    with pytest.raises(ValidationError):
        validate_manifest(document)


def test_schema_06_admits_every_05_locator():
    document = _manifest06()
    document["results"].append(
        {
            "id": "ptr",
            "run_id": "r",
            "name": "pointer",
            "value": 1.0,
            "artifact_id": "a",
            "locator": "/loss",
        }
    )
    document["results"].append(
        {
            "id": "tb",
            "run_id": "r",
            "name": "loss @ 0",
            "value": 1.0,
            "artifact_id": "a",
            "locator": {
                "kind": "tensorboard",
                "tag": "loss",
                "step": 0,
                "occurrence": 0,
            },
        }
    )
    manifest = validate_manifest(document)
    assert isinstance(manifest, Manifest06)


def test_json_schema_06_keeps_the_jsonl_pointer_constraint():
    document = _manifest06()
    document["results"][0]["locator"] = {
        "kind": "jsonl",
        "line": 1,
        "pointer": "not/a/pointer",
    }
    schema = json_schema_0_6()
    errors = list(Draft202012Validator(schema).iter_errors(document))
    assert errors


def test_json_schema_06_descriptions_name_06():
    schema = json_schema_0_6()
    for name in ("JsonlLocator", "CsvCellLocator", "TensorboardStringLocator"):
        description = schema["$defs"][name]["description"]
        assert "0.6" in description
        assert "0.3" not in description and "0.4" not in description and "0.5" not in description


def test_json_schema_06_shape():
    schema = json_schema_0_6()
    assert schema["$id"] == "urn:runevidence:manifest:0.6"
    assert len(schema["$defs"]["Result06"]["properties"]["locator"]["oneOf"]) == 6
    assert schema["$defs"]["Result06"]["then"]["required"] == ["artifact_id"]
    assert "TensorboardStringLocator" in schema["$defs"]


def test_migrate_05_to_06_is_identity(tmp_path):
    path = tmp_path / "runevidence.json"
    document = _manifest06(schema_version="0.5")
    document["results"][0]["locator"] = {
        "kind": "tensorboard",
        "tag": "cfg",
        "step": 0,
        "occurrence": 0,
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    before = json.loads(path.read_text(encoding="utf-8"))
    result = migrate_manifest(path, target_version="0.6")
    after = json.loads(path.read_text(encoding="utf-8"))
    assert result.changed
    before["schema_version"] = "0.6"
    assert before == after


def test_load_manifest_dispatches_06(tmp_path):
    path = tmp_path / "runevidence.json"
    path.write_text(json.dumps(_manifest06()), encoding="utf-8")
    loaded = load_manifest(path)
    assert isinstance(loaded.manifest, Manifest06)


def test_artifact_to_dict_accepts_06():
    artifact = Artifact(id="a", path="x", sha256="a" * 64, size_bytes=1)
    assert artifact_to_dict(artifact, schema_version="0.6")["id"] == "a"


def test_claim_display_hides_a_text_tag():
    displayed = _display_details(
        {
            "locator": {
                "kind": "tensorboard-string",
                "tag": "SECRET_TAG",
                "step": 0,
                "occurrence": 0,
            },
            "value": "x" * 300,
        }
    )
    assert displayed["locator"] == "tensorboard-string:step=0,occurrence=0"
    assert "SECRET_TAG" not in str(displayed)
