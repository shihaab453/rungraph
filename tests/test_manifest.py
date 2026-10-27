"""Loading and saving manifests: missing, unreadable, invalid, round trip."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runevidence.findings import Code, Severity
from runevidence.manifest import (
    ManifestLoadError,
    dump_manifest,
    load_manifest,
    save_manifest,
)
from runevidence.models import MANIFEST_FILENAME

MANIFEST = "runevidence.json"


def read_doc(root: Path) -> dict:
    return json.loads((root / MANIFEST).read_text(encoding="utf-8"))


def write_doc(root: Path, doc: dict) -> None:
    (root / MANIFEST).write_text(
        json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def load_error(target: Path) -> list:
    with pytest.raises(ManifestLoadError) as excinfo:
        load_manifest(target)
    return excinfo.value.findings


def test_missing_manifest_is_reported_not_found(tmp_path):
    findings = load_error(tmp_path)

    assert [f.code for f in findings] == [Code.manifest_not_found]
    assert findings[0].severity is Severity.error
    assert findings[0].hint is not None
    assert "runevidence init" in findings[0].hint


def test_invalid_json_is_unreadable(experiment):
    (experiment / MANIFEST).write_text("{not valid json", encoding="utf-8")

    findings = load_error(experiment)

    assert [f.code for f in findings] == [Code.manifest_unreadable]
    assert findings[0].severity is Severity.error


def test_non_utf8_bytes_are_unreadable(experiment):
    (experiment / MANIFEST).write_bytes(b"\xff\xfe\x00{\x00}\x00")

    findings = load_error(experiment)

    assert [f.code for f in findings] == [Code.manifest_unreadable]
    assert findings[0].severity is Severity.error


def test_unknown_field_is_schema_invalid(experiment):
    doc = read_doc(experiment)
    doc["artifacts"][0]["sha265"] = doc["artifacts"][0].pop("sha256")
    write_doc(experiment, doc)

    findings = load_error(experiment)

    assert [f.code for f in findings] == [Code.schema_invalid]
    assert "sha265" in findings[0].message


def test_bad_sha256_pattern_is_schema_invalid(experiment):
    doc = read_doc(experiment)
    doc["artifacts"][0]["sha256"] = "not-a-digest"
    write_doc(experiment, doc)

    findings = load_error(experiment)

    assert [f.code for f in findings] == [Code.schema_invalid]
    assert "sha256" in findings[0].message


def test_wrong_schema_version_is_schema_invalid(experiment):
    doc = read_doc(experiment)
    doc["schema_version"] = "9.9"
    write_doc(experiment, doc)

    findings = load_error(experiment)

    assert [f.code for f in findings] == [Code.schema_invalid]
    assert "schema_version" in findings[0].message


def test_load_accepts_either_a_folder_or_a_manifest_file(experiment):
    from_folder = load_manifest(experiment)
    from_file = load_manifest(experiment / MANIFEST_FILENAME)

    assert from_folder.path == from_file.path
    assert from_folder.root == experiment.resolve()
    assert from_folder.manifest.model_dump() == from_file.manifest.model_dump()


def test_save_manifest_round_trips_and_writes_lf_only(experiment, tmp_path):
    loaded = load_manifest(experiment)
    out = tmp_path / "saved" / MANIFEST
    out.parent.mkdir()

    save_manifest(loaded.manifest, out)

    raw = out.read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")
    assert raw.decode("utf-8") == dump_manifest(loaded.manifest)
    assert load_manifest(out).manifest.model_dump(mode="json") == loaded.manifest.model_dump(
        mode="json"
    )
