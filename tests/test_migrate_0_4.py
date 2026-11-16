"""Schema 0.4 migration gate: 0.1/0.2 -> 0.4 with the locator pre-check,
0.3 -> 0.4 as a version-only bump, and 0.4 downgrades refused.

Migration is manifest-only and transactional. The locator pre-check refuses the
whole migration before any backup or write. csv-cell verification is not part
of this phase.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from runevidence import cli
from runevidence import migrate as migrate_module
from runevidence.manifest import dump_raw

MANIFEST = "runevidence.json"


def doc(
    version: str = "0.2",
    *,
    locator="/v",
    artifact_id="a",
    artifact=None,
    result_id="r",
    run_id="run",
) -> dict:
    if artifact is None:
        artifact = {
            "id": "a",
            "path": "data.json",
            "sha256": "a" * 64,
            "size_bytes": 8,
        }
    document = {
        "schema_version": version,
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": run_id, "name": "Run"}],
        "artifacts": [artifact],
        "results": [
            {
                "id": result_id,
                "run_id": run_id,
                "name": "accuracy",
                "value": 0.9,
                "artifact_id": artifact_id,
            }
        ],
        "evidence": [],
    }
    if version in ("0.2", "0.3"):
        document["relationships"] = []
    if locator is not ...:
        document["results"][0]["locator"] = locator
    return document


def write(folder: Path, value: dict, name: str = MANIFEST) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(dump_raw(value).encode("utf-8"))
    return path


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def backups(folder: Path) -> list[str]:
    return sorted(
        path.name for path in folder.iterdir()
        if path.name == MANIFEST + ".bak" or path.name.startswith(MANIFEST + ".bak.")
    )


def snapshot(folder: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(folder.iterdir())}


def assert_no_writes(folder: Path, before: dict[str, bytes]) -> None:
    assert snapshot(folder) == before
    assert backups(folder) == []
    assert not [name for name in folder.iterdir() if name.name.endswith(".tmp")]


@pytest.mark.parametrize("source", ["0.1", "0.2"])
def test_clean_0_1_and_0_2_migrate_to_0_4(tmp_path, source):
    folder = tmp_path / "exp"
    original = doc(source, locator="/v")
    path = write(folder, original)
    before = path.read_bytes()

    code, out, err = run(["migrate", str(folder), "--to", "0.4", "--json"])

    assert (code, err) == (0, "")
    result = json.loads(out)
    assert result == {
        "format": "runevidence-migrate-result-v1",
        "ok": True,
        "from": source,
        "to": "0.4",
        "changed": True,
        "dry_run": False,
        "backup": "created",
    }
    migrated = json.loads(path.read_bytes())
    assert migrated["schema_version"] == "0.4"
    assert migrated["results"][0]["locator"] == "/v"
    assert (
        {key: value for key, value in migrated.items() if key != "schema_version"}
        == {key: value for key, value in json.loads(before).items() if key != "schema_version"}
    )
    assert (folder / (MANIFEST + ".bak")).read_bytes() == before


OFFENDERS = {
    "prefixed-pointer": {"locator": "jsonl:/x"},
    "invalid-pointer-escape": {"locator": "/x~2"},
    "missing-artifact-id": {"locator": "/x", "artifact_id": None},
    "non-json-string-target": {
        "artifact": {
            "id": "a",
            "path": "data.csv",
            "sha256": "a" * 64,
            "size_bytes": 8,
        }
    },
    "metrics-csv-string-target": {
        "artifact": {
            "id": "a",
            "path": "metrics/metrics.csv",
            "sha256": "a" * 64,
            "size_bytes": 8,
            "kind": "metrics",
        }
    },
    "volatile-artifact": {
        "artifact": {
            "id": "a",
            "path": "data.json",
            "sha256": "a" * 64,
            "size_bytes": 8,
            "volatile": True,
        }
    },
    "no-digest-artifact": {
        "artifact": {"id": "a", "path": "data.json", "sha256": None}
    },
    "directory-artifact": {
        "artifact": {
            "id": "a",
            "path": "data",
            "artifact_type": "directory",
            "digest_format": "runevidence-directory-v1",
        }
    },
}


@pytest.mark.parametrize("source", ["0.1", "0.2"])
@pytest.mark.parametrize("label", tuple(OFFENDERS))
def test_locator_precheck_refuses_0_1_and_0_2_to_0_4_before_writes(tmp_path, source, label):
    folder = tmp_path / "exp"
    value = doc(source, **OFFENDERS[label])
    # Add a second, valid result: no partial migration may occur.
    value["results"].append(
        {
            "id": "valid",
            "run_id": "run",
            "name": "accuracy",
            "value": 0.9,
            "artifact_id": "a",
            "locator": "",
        }
    )
    write(folder, value)
    before = snapshot(folder)

    code, out, err = run(["migrate", str(folder), "--to", "0.4", "--json"])

    assert (code, err) == (2, "")
    assert json.loads(out)["error"]["code"] == "LOCATOR_MIGRATION_UNSUPPORTED"
    assert_no_writes(folder, before)


def test_defensive_object_locator_precheck_refuses():
    class FakeResult:
        artifact_id = "a"
        locator = object()

    class FakeManifest:
        artifacts: list = []
        results = [FakeResult()]

    with pytest.raises(migrate_module.MigrationError) as info:
        migrate_module._check_locator_migration(FakeManifest())

    assert info.value.code == "LOCATOR_MIGRATION_UNSUPPORTED"


def test_0_4_precheck_text_names_schema_0_4_and_is_value_free(tmp_path):
    folder = tmp_path / "exp"
    write(folder, doc("0.2", locator="jsonl:/CANARY-LOCATOR"))
    before = snapshot(folder)

    code, out, err = run(["migrate", str(folder), "--to", "0.4"])

    assert (code, err) == (2, "")
    assert out == (
        "ERROR (LOCATOR_MIGRATION_UNSUPPORTED): the manifest contains a locator "
        "that schema 0.4 cannot accept or verify. Nothing was written.\n"
    )
    assert "CANARY-LOCATOR" not in out
    assert_no_writes(folder, before)


def test_0_3_to_0_4_is_version_only_and_preserves_locators(tmp_path):
    folder = tmp_path / "exp"
    original = doc("0.3", locator="/v")
    original["artifacts"] = [
        {"id": "a", "path": "data.json", "sha256": "a" * 64, "size_bytes": 8},
        {
            "id": "b",
            "path": "data.jsonl",
            "sha256": "b" * 64,
            "size_bytes": 8,
        },
    ]
    original["results"] = [
        {
            "id": "string",
            "run_id": "run",
            "name": "accuracy",
            "value": 0.9,
            "artifact_id": "a",
            "locator": "/v",
        },
        {
            "id": "object",
            "run_id": "run",
            "name": "loss",
            "value": 0.1,
            "artifact_id": "b",
            "locator": {"kind": "jsonl", "line": 2, "pointer": "/loss"},
        },
    ]
    path = write(folder, original)
    before = path.read_bytes()

    code, out, err = run(["migrate", str(folder), "--to", "0.4", "--json"])

    assert (code, err) == (0, "")
    result = json.loads(out)
    assert (result["from"], result["to"], result["backup"]) == ("0.3", "0.4", "created")
    migrated = json.loads(path.read_bytes())
    assert migrated["schema_version"] == "0.4"
    assert migrated["results"][0]["locator"] == "/v"
    assert migrated["results"][1]["locator"] == {"kind": "jsonl", "line": 2, "pointer": "/loss"}
    assert (
        {key: value for key, value in migrated.items() if key != "schema_version"}
        == {key: value for key, value in json.loads(before).items() if key != "schema_version"}
    )
    assert (folder / (MANIFEST + ".bak")).read_bytes() == before


def test_0_3_to_0_4_skips_the_locator_precheck_for_a_csv_artifact(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    original = doc("0.3", locator="/v")
    original["artifacts"] = [
        {"id": "a", "path": "metrics.csv", "sha256": "a" * 64, "size_bytes": 8}
    ]
    path = write(folder, original)
    calls: list[str] = []

    def deny(manifest):
        calls.append("precheck")
        raise AssertionError("0.3 -> 0.4 must skip the locator pre-check")

    monkeypatch.setattr(migrate_module, "_check_locator_migration", deny)

    code, out, err = run(["migrate", str(folder), "--to", "0.4", "--json"])

    assert (code, err) == (0, "")
    assert calls == []
    assert json.loads(path.read_bytes())["schema_version"] == "0.4"


@pytest.mark.parametrize("target", ["0.3", "0.2"])
def test_0_4_downgrades_are_refused_without_internal_error(tmp_path, target):
    folder = tmp_path / "exp"
    path = write(folder, doc("0.4", locator="/v"))
    before = snapshot(folder)

    code, out, err = run(["migrate", str(folder), "--to", target, "--json"])

    assert (code, err) == (2, "")
    assert json.loads(out)["error"]["code"] == "DOWNGRADE_UNSUPPORTED"
    assert_no_writes(folder, before)


def test_0_4_downgrade_text_is_fixed(tmp_path):
    folder = tmp_path / "exp"
    write(folder, doc("0.4", locator="/v"))
    before = snapshot(folder)

    code, out, err = run(["migrate", str(folder), "--to", "0.3"])

    assert (code, err) == (2, "")
    assert out == (
        "ERROR (DOWNGRADE_UNSUPPORTED): downgrade is not supported. "
        "Nothing was written.\n"
    )
    assert_no_writes(folder, before)


def test_unsupported_target_text_names_0_5(tmp_path):
    folder = tmp_path / "exp"
    path = write(folder, doc("0.2"))
    before = path.read_bytes()

    code, out, err = run(["migrate", str(folder), "--to", "0.9"])

    assert (code, err) == (2, "")
    assert out == (
        "ERROR (UNSUPPORTED_TARGET): only --to 0.2, --to 0.3, --to 0.4, --to 0.5, "
        "--to 0.6, --to 0.7 or --to 0.8 is supported; downgrade and other versions are not "
        "available. Nothing was written.\n"
    )
    assert path.read_bytes() == before


def test_migrate_help_and_to_help_name_0_5():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(["migrate", "--help"])
    text = out.getvalue()
    assert (code, err.getvalue()) == (0, "")
    assert "target schema version (0.2, 0.3, 0.4, 0.5, 0.6, 0.7 or 0.8)" in text

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(["--help"])
    assert (code, err.getvalue()) == (0, "")
    # The top-level list wraps long command help; compare normalized whitespace.
    assert "declare an existing manifest as schema 0.2, 0.3, 0.4, 0.5, 0.6, 0.7 or 0.8" in " ".join(
        out.getvalue().split()
    )


def test_lone_surrogate_in_a_0_3_string_locator_is_serialization_failed(tmp_path):
    # The RFC's exact vector: a schema-valid 0.3 string pointer that is not
    # serializable. It must fail before the backup enumeration or any write.
    folder = tmp_path / "exp"
    folder.mkdir()
    path = folder / MANIFEST
    document = doc("0.3", locator="/\ud800")
    path.write_bytes(
        (json.dumps(document, indent=2, ensure_ascii=True) + "\n").encode("ascii")
    )
    before = snapshot(folder)

    code, out, err = run(["migrate", str(folder), "--to", "0.4", "--json"])

    assert (code, err) == (2, "")
    assert json.loads(out)["error"]["code"] == "SERIALIZATION_FAILED"
    assert_no_writes(folder, before)


def test_lone_surrogate_elsewhere_is_serialization_failed_before_backup(tmp_path):
    folder = tmp_path / "exp"
    folder.mkdir()
    path = folder / MANIFEST
    raw = (
        '{"schema_version": "0.3", "project": {"id": "p", "name": "bad \\ud800"}, '
        '"runs": [], "artifacts": [], "results": [], "evidence": []}\n'
    )
    path.write_bytes(raw.encode("ascii"))
    before = snapshot(folder)

    code, out, err = run(["migrate", str(folder), "--to", "0.4", "--json"])

    assert (code, err) == (2, "")
    assert json.loads(out)["error"]["code"] == "SERIALIZATION_FAILED"
    assert_no_writes(folder, before)
