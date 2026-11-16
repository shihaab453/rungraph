"""Schema 0.8: media reference values, locators, dispatch, migration and shape."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from runevidence.manifest import load_manifest, validate_manifest
from runevidence.migrate import MigrationError, migrate_manifest
from runevidence.models import (
    AudioValue,
    HistogramValue,
    ImageValue,
    Manifest08,
    Result08,
    TensorboardImageLocator,
    is_manifest,
    json_schema_0_8,
)
from runevidence.run_comparability import _UNSUPPORTED_COMPARABILITY_SCHEMAS

DIGEST = "a" * 64


def image(**overrides):
    value = {
        "kind": "image",
        "media_type": "image/png",
        "sha256": DIGEST,
        "size_bytes": 4321,
        "width": 64,
        "height": 64,
    }
    value.update(overrides)
    return value


def audio(**overrides):
    value = {
        "kind": "audio",
        "media_type": "audio/wav",
        "sha256": DIGEST,
        "size_bytes": 88244,
    }
    value.update(overrides)
    return value


def document08(**overrides):
    document = {
        "schema_version": "0.8",
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
                "name": "samples @ 0 (image)",
                "value": image(),
                "artifact_id": "a",
                "locator": {
                    "kind": "tensorboard-image",
                    "tag": "samples",
                    "step": 0,
                    "occurrence": 0,
                },
            }
        ],
        "evidence": [],
    }
    document.update(overrides)
    return document


def test_manifest08_accepts_media():
    manifest = validate_manifest(document08())
    assert isinstance(manifest, Manifest08)
    assert isinstance(manifest.results[0], Result08)
    assert isinstance(manifest.results[0].value, ImageValue)
    assert isinstance(manifest.results[0].locator, TensorboardImageLocator)
    assert is_manifest(manifest)


def test_manifest08_accepts_audio_and_histogram_and_scalar():
    document = document08()
    document["results"] = [
        {
            "id": "aud",
            "run_id": "r",
            "name": "clip @ 0 (audio)",
            "value": audio(),
            "artifact_id": "a",
            "locator": {
                "kind": "tensorboard-audio",
                "tag": "clip",
                "step": 0,
                "occurrence": 0,
            },
        },
        {
            "id": "hist",
            "run_id": "r",
            "name": "w @ 0 (histogram)",
            "value": {
                "kind": "histogram",
                "min": 0.0,
                "max": 1.0,
                "num": 2.0,
                "sum": 1.0,
                "sum_squares": 1.0,
                "bucket_limit": [0.5],
                "bucket": [2.0],
            },
            "artifact_id": "a",
            "locator": {
                "kind": "tensorboard-histogram",
                "tag": "w",
                "step": 0,
                "occurrence": 0,
            },
        },
        {
            "id": "ptr",
            "run_id": "r",
            "name": "score",
            "value": 1.5,
            "artifact_id": "a",
            "locator": "/score",
        },
        {
            "id": "scalar-on-image",
            "run_id": "r",
            "name": "weird",
            "value": 1.5,
            "artifact_id": "a",
            "locator": {
                "kind": "tensorboard-image",
                "tag": "samples",
                "step": 0,
                "occurrence": 0,
            },
        },
    ]
    manifest = validate_manifest(document)
    assert isinstance(manifest, Manifest08)
    assert isinstance(manifest.results[1].value, HistogramValue)
    assert isinstance(manifest.results[2].value, float)


def test_schema_07_rejects_media_locators():
    with pytest.raises(ValidationError):
        validate_manifest(document08(schema_version="0.7"))


@pytest.mark.parametrize(
    ("value", "locator_kind"),
    [("image", "tensorboard-audio"), ("audio", "tensorboard-image")],
)
def test_media_value_locator_cross_rule(value, locator_kind):
    document = document08()
    document["results"][0]["value"] = image() if value == "image" else audio()
    document["results"][0]["locator"] = {
        "kind": locator_kind,
        "tag": "t",
        "step": 0,
        "occurrence": 0,
    }
    with pytest.raises(ValidationError):
        validate_manifest(document)


def test_media_value_locator_cross_rule_reports_result_location():
    document = document08()
    document["results"][0]["locator"] = {
        "kind": "tensorboard-audio",
        "tag": "t",
        "step": 0,
        "occurrence": 0,
    }
    with pytest.raises(ValidationError) as excinfo:
        validate_manifest(document)
    assert any("results" in str(error["loc"]) for error in excinfo.value.errors())


def test_manifest08_requires_artifact_id_for_non_null_locator():
    document = document08()
    document["results"][0]["artifact_id"] = None
    with pytest.raises(ValidationError):
        validate_manifest(document)


def test_manifest08_admits_a_jsonl_locator():
    document = document08()
    document["results"][0] = {
        "id": "line",
        "run_id": "r",
        "name": "score",
        "value": 1.0,
        "artifact_id": "a",
        "locator": {"kind": "jsonl", "line": 1, "pointer": "/score"},
    }
    manifest = validate_manifest(document)
    assert isinstance(manifest, Manifest08)
    assert manifest.results[0].locator.kind == "jsonl"


@pytest.mark.parametrize(
    "overrides",
    [
        {"sha256": DIGEST.upper()},
        {"sha256": "a" * 63},
        {"size_bytes": 0},
        {"size_bytes": 16_777_217},
        {"size_bytes": True},
        {"width": 0},
        {"height": 65_536},
        {"media_type": "image/jpeg"},
        {"kind": "png"},
        {"extra": 1},
    ],
)
def test_image_value_rejects_invalid(overrides):
    with pytest.raises(ValidationError):
        validate_manifest(
            document08(
                results=[{**document08()["results"][0], "value": image(**overrides)}]
            )
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"sha256": DIGEST.upper()},
        {"size_bytes": 0},
        {"media_type": "audio/mpeg"},
        {"sample_rate": 44100.0},
    ],
)
def test_audio_value_rejects_invalid(overrides):
    document = document08()
    document["results"][0]["value"] = audio(**overrides)
    document["results"][0]["locator"] = {
        "kind": "tensorboard-audio",
        "tag": "t",
        "step": 0,
        "occurrence": 0,
    }
    with pytest.raises(ValidationError):
        validate_manifest(document)


def test_image_value_rejects_missing_key():
    value = image()
    del value["width"]
    with pytest.raises(ValidationError):
        validate_manifest(
            document08(results=[{**document08()["results"][0], "value": value}])
        )


def test_media_values_are_models():
    assert isinstance(ImageValue.model_validate(image()), ImageValue)
    assert isinstance(AudioValue.model_validate(audio()), AudioValue)


def test_json_schema_08_shape():
    schema = json_schema_0_8()
    assert schema["$id"] == "urn:runevidence:manifest:0.8"
    defs = schema["$defs"]
    assert len(defs["Result08"]["properties"]["locator"]["oneOf"]) == 9
    refs = [
        entry["$ref"]
        for entry in defs["Result08"]["properties"]["value"]["anyOf"]
        if "$ref" in entry
    ]
    assert refs == [
        "#/$defs/HistogramValue",
        "#/$defs/ImageValue",
        "#/$defs/AudioValue",
    ]
    assert len(defs["Result08"]["allOf"]) == 4
    assert defs["ImageValue"]["required"] == [
        "kind",
        "media_type",
        "sha256",
        "size_bytes",
        "width",
        "height",
    ]
    assert defs["AudioValue"]["required"] == [
        "kind",
        "media_type",
        "sha256",
        "size_bytes",
    ]
    assert defs["ImageValue"]["properties"]["sha256"]["pattern"] == "^[0-9a-f]{64}$"
    assert "0.8" in defs["TensorboardImageLocator"]["properties"]["kind"]["description"]
    assert "0.7" not in defs["TensorboardAudioLocator"]["description"]


def test_json_schema_08_rejects_cross_kind_values():
    schema = json_schema_0_8()
    validator = Draft202012Validator(schema)
    document = document08()
    document["results"][0]["locator"] = {
        "kind": "tensorboard-audio",
        "tag": "t",
        "step": 0,
        "occurrence": 0,
    }
    assert list(validator.iter_errors(document))
    document = document08()
    document["results"][0]["value"]["sha256"] = DIGEST.upper()
    assert list(validator.iter_errors(document))


def test_json_schema_08_accepts_a_scalar_on_a_media_locator():
    schema = json_schema_0_8()
    document = document08()
    document["results"][0]["value"] = 1.5
    assert not list(Draft202012Validator(schema).iter_errors(document))


def test_migrate_07_to_08_is_identity(tmp_path):
    path = tmp_path / "runevidence.json"
    document = document08(schema_version="0.7")
    document["results"][0]["value"] = 1.0
    document["results"][0]["locator"] = {
        "kind": "tensorboard",
        "tag": "w",
        "step": 0,
        "occurrence": 0,
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    before = json.loads(path.read_text(encoding="utf-8"))
    result = migrate_manifest(path, target_version="0.8")
    after = json.loads(path.read_text(encoding="utf-8"))
    assert result.changed
    before["schema_version"] = "0.8"
    assert before == after


def test_load_manifest_dispatches_08(tmp_path):
    path = tmp_path / "runevidence.json"
    path.write_text(json.dumps(document08()), encoding="utf-8")
    loaded = load_manifest(path)
    assert isinstance(loaded.manifest, Manifest08)


def test_schema_08_is_supported_by_comparability():
    assert "0.8" not in _UNSUPPORTED_COMPARABILITY_SCHEMAS


def test_migrate_08_to_07_is_a_downgrade(tmp_path):
    path = tmp_path / "runevidence.json"
    path.write_text(json.dumps(document08()), encoding="utf-8")
    with pytest.raises(MigrationError) as excinfo:
        migrate_manifest(path, target_version="0.7")
    assert excinfo.value.code == "DOWNGRADE_UNSUPPORTED"
