"""Schema 0.10: TF2 media locators, dispatch, migration and schema shape."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from runevidence.manifest import load_manifest, validate_manifest
from runevidence.migrate import MigrationError, migrate_manifest
from runevidence.models import (
    Manifest10,
    Result10,
    TensorboardTf2AudioLocator,
    TensorboardTf2ImageLocator,
    is_manifest,
    json_schema_0_10,
)
from runevidence.run_comparability import _UNSUPPORTED_COMPARABILITY_SCHEMAS

DIGEST = "a" * 64
IMAGE = {
    "kind": "image",
    "media_type": "image/png",
    "sha256": DIGEST,
    "size_bytes": 4321,
    "width": 64,
    "height": 64,
}


def document10(locator=None, value=None, **overrides):
    document = {
        "schema_version": "0.10",
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
                "name": "samples @ 3 (tf2 image) element 0",
                "value": value or IMAGE,
                "artifact_id": "a",
                "locator": locator
                or {
                    "kind": "tensorboard-tf2-image",
                    "tag": "samples",
                    "step": 3,
                    "occurrence": 0,
                    "element": 0,
                },
            }
        ],
        "evidence": [],
    }
    document.update(overrides)
    return document


def test_manifest10_accepts_tf2_image_and_audio():
    manifest = validate_manifest(document10())
    assert isinstance(manifest, Manifest10)
    assert isinstance(manifest.results[0], Result10)
    assert isinstance(manifest.results[0].locator, TensorboardTf2ImageLocator)
    audio = document10(
        locator={
            "kind": "tensorboard-tf2-audio",
            "tag": "clip",
            "step": 4,
            "occurrence": 0,
            "element": 2,
        },
        value={
            "kind": "audio",
            "media_type": "audio/wav",
            "sha256": DIGEST,
            "size_bytes": 44,
        },
    )
    manifest = validate_manifest(audio)
    assert isinstance(manifest.results[0].locator, TensorboardTf2AudioLocator)
    assert is_manifest(manifest)


def test_element_is_required_and_bounded():
    for element in (None, -1, 64):
        locator = {
            "kind": "tensorboard-tf2-image",
            "tag": "samples",
            "step": 3,
            "occurrence": 0,
        }
        if element is not None:
            locator["element"] = element
        with pytest.raises(ValidationError):
            validate_manifest(document10(locator=locator))


def test_frozen_schemas_still_reject_the_new_kinds_and_element():
    for version in ("0.8", "0.9"):
        with pytest.raises(ValidationError):
            validate_manifest(document10(schema_version=version))
    legacy = document10(schema_version="0.9")
    legacy["results"][0]["value"] = 1.0
    legacy["results"][0]["locator"] = {
        "kind": "tensorboard-image",
        "tag": "t",
        "step": 0,
        "occurrence": 0,
        "element": 0,
    }
    with pytest.raises(ValidationError):
        validate_manifest(legacy)


def test_value_locator_matrix_accepts_both_media_kinds():
    validate_manifest(
        document10(
            locator={
                "kind": "tensorboard-image",
                "tag": "t",
                "step": 0,
                "occurrence": 0,
            }
        )
    )
    validate_manifest(
        document10(
            locator={
                "kind": "tensorboard-tf2-image",
                "tag": "t",
                "step": 0,
                "occurrence": 0,
                "element": 0,
            }
        )
    )
    with pytest.raises(ValidationError):
        validate_manifest(
            document10(
                locator={
                    "kind": "tensorboard-tf2-audio",
                    "tag": "t",
                    "step": 0,
                    "occurrence": 0,
                    "element": 0,
                }
            )
        )


def test_json_schema_10_shape():
    schema = json_schema_0_10()
    assert schema["$id"] == "urn:runevidence:manifest:0.10"
    defs = schema["$defs"]
    assert len(defs["Result10"]["properties"]["locator"]["oneOf"]) == 12
    assert len(defs["Result10"]["allOf"]) == 5
    for name in ("TensorboardTf2ImageLocator", "TensorboardTf2AudioLocator"):
        assert defs[name]["properties"]["element"]["maximum"] == 63
        assert "element" in defs[name]["required"]


def test_migrate_09_to_10_is_identity(tmp_path):
    path = tmp_path / "runevidence.json"
    document = document10(schema_version="0.9")
    document["results"][0]["value"] = 1.0
    document["results"][0]["locator"] = {
        "kind": "tensorboard",
        "tag": "w",
        "step": 0,
        "occurrence": 0,
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    before = json.loads(path.read_text(encoding="utf-8"))
    assert migrate_manifest(path, target_version="0.10").changed
    after = json.loads(path.read_text(encoding="utf-8"))
    before["schema_version"] = "0.10"
    assert before == after


def test_load_manifest_dispatches_10(tmp_path):
    path = tmp_path / "runevidence.json"
    path.write_text(json.dumps(document10()), encoding="utf-8")
    assert isinstance(load_manifest(path).manifest, Manifest10)


def test_schema_10_is_supported_by_comparability():
    assert "0.10" not in _UNSUPPORTED_COMPARABILITY_SCHEMAS


@pytest.mark.parametrize("target", ["0.9", "0.8", "0.3"])
def test_migrate_10_down_is_a_downgrade(tmp_path, target):
    path = tmp_path / "runevidence.json"
    path.write_text(json.dumps(document10()), encoding="utf-8")
    with pytest.raises(MigrationError) as excinfo:
        migrate_manifest(path, target_version=target)
    assert excinfo.value.code == "DOWNGRADE_UNSUPPORTED"
