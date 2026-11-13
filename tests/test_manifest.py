"""Loading and saving manifests: missing, unreadable, invalid, round trip."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydantic import ValidationError

from runevidence.findings import Code, Severity
from runevidence.manifest import (
    ManifestLoadError,
    artifact_to_dict,
    dump_manifest,
    load_manifest,
    save_manifest,
)
from runevidence.models import MANIFEST_FILENAME, Artifact, json_schema

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
    assert "sha265" not in findings[0].message


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


# artifact_type / digest_format. Directory records are a delivered surface: the
# model and schema accept them, --directory-artifacts checks them, and
# add --directory / rehash --directory author them.

DIRECTORY = {"artifact_type": "directory", "digest_format": "runevidence-directory-v1"}
DIGEST = "ab" * 32
OLD_FILE_KEYS = ["id", "path", "sha256", "size_bytes", "kind", "run_id", "metadata"]

VALID_TYPE_AND_FORMAT = [
    {},
    {"artifact_type": "file"},
    {"digest_format": None},
    {"artifact_type": "file", "digest_format": None},
    DIRECTORY,
    {**DIRECTORY, "volatile": True},
]
INVALID_TYPE_AND_FORMAT = [
    {"artifact_type": "directory"},
    {"artifact_type": "directory", "digest_format": None},
    {"artifact_type": "directory", "volatile": True},
    {"digest_format": "runevidence-directory-v1"},
    {"artifact_type": "file", "digest_format": "runevidence-directory-v1"},
    {"artifact_type": "directory", "digest_format": "runevidence-directory-v2"},
    {"artifact_type": "file", "digest_format": ""},
    {"artifact_type": "folder"},
    {"artifact_type": "Directory", "digest_format": "runevidence-directory-v1"},
    {"artifact_type": None},
]


def test_artifacts_default_to_file_with_no_digest_format():
    art = Artifact(id="a", path="a.txt")

    assert art.artifact_type == "file"
    assert art.digest_format is None


def test_existing_manifests_load_as_files_and_rewrite_without_new_fields(experiment):
    loaded = load_manifest(experiment)

    assert {a.artifact_type for a in loaded.manifest.artifacts} == {"file"}
    for entry in json.loads(dump_manifest(loaded.manifest))["artifacts"]:
        assert "artifact_type" not in entry
        assert "digest_format" not in entry


def test_file_serialization_keeps_its_old_keys_and_order():
    art = Artifact(id="a", path="a.txt", sha256=DIGEST.upper(), size_bytes=3, kind="metrics")

    data = artifact_to_dict(art)

    assert list(data) == OLD_FILE_KEYS
    assert data["sha256"] == DIGEST
    # Spelling out the default is accepted, and written back without it.
    assert artifact_to_dict(Artifact(id="a", path="a.txt", artifact_type="file")) == artifact_to_dict(
        Artifact(id="a", path="a.txt")
    )


def test_directory_records_write_both_fields_and_round_trip():
    art = Artifact(id="w", path="outputs/weights", sha256=DIGEST.upper(), size_bytes=4, **DIRECTORY)

    data = artifact_to_dict(art)

    assert list(data) == ["id", "path", "artifact_type", "digest_format", *OLD_FILE_KEYS[2:]]
    assert data["sha256"] == DIGEST
    assert Artifact.model_validate(data) == art


@pytest.mark.parametrize("fields", VALID_TYPE_AND_FORMAT, ids=str)
def test_compatible_type_and_format_loads(experiment, fields):
    doc = read_doc(experiment)
    doc["artifacts"][0].update(fields)
    write_doc(experiment, doc)

    load_manifest(experiment)


@pytest.mark.parametrize("fields", INVALID_TYPE_AND_FORMAT, ids=str)
def test_incompatible_or_unknown_type_and_format_is_schema_invalid(experiment, fields):
    doc = read_doc(experiment)
    doc["artifacts"][0].update(fields)
    write_doc(experiment, doc)

    findings = load_error(experiment)

    assert {f.code for f in findings} == {Code.schema_invalid}
    assert f"(id {doc['artifacts'][0]['id']!r})" not in findings[0].message


def test_directory_records_still_forbid_unknown_fields(experiment):
    doc = read_doc(experiment)
    doc["artifacts"][0].update(DIRECTORY, members=[])
    write_doc(experiment, doc)

    findings = load_error(experiment)

    assert [f.code for f in findings] == [Code.schema_invalid]
    assert "members" not in findings[0].message


def _accepts(schema: dict, value: object) -> bool:
    """Just enough JSON Schema to evaluate the artifact type/format rules.
    jsonschema is not a dependency, so this raises on any other keyword
    instead of silently passing it."""
    for key, rule in schema.items():
        if key in ("title", "description", "default", "then", "else"):
            continue
        if key == "type":
            ok = {"string": isinstance(value, str), "null": value is None, "object": isinstance(value, dict)}[rule]
        elif key == "const":
            ok = type(value) is type(rule) and value == rule
        elif key == "enum":
            ok = value in rule
        elif key == "anyOf":
            ok = any(_accepts(option, value) for option in rule)
        elif key == "required":
            ok = all(name in value for name in rule)
        elif key == "properties":
            ok = all(_accepts(sub, value[name]) for name, sub in rule.items() if name in value)
        elif key == "if":
            ok = _accepts(schema.get("then" if _accepts(rule, value) else "else", {}), value)
        else:
            raise AssertionError(f"unsupported schema keyword {key!r}")
        if not ok:
            return False
    return True


def test_generated_schema_and_model_agree_on_type_and_format():
    art = json_schema()["$defs"]["Artifact"]
    rules = {
        "type": "object",
        "properties": {name: art["properties"][name] for name in ("artifact_type", "digest_format")},
        **{key: art[key] for key in ("if", "then", "else")},
    }

    for fields in VALID_TYPE_AND_FORMAT + INVALID_TYPE_AND_FORMAT:
        entry = {"id": "a", "path": "a", **fields}
        try:
            Artifact.model_validate(entry)
            runtime = True
        except ValidationError:
            runtime = False
        assert runtime is (fields in VALID_TYPE_AND_FORMAT), fields
        assert _accepts(rules, entry) is runtime, fields
