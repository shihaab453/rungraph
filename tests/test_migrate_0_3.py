"""Schema 0.3 migration gate: additive 0.1/0.2 -> 0.3 and locator pre-checks.

Migration is manifest-only and transactional. The locator pre-check refuses the
whole migration before any backup or write. JSONL verification is not part of
this slice.
"""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path

import pytest

from runevidence import cli
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
    if version == "0.2":
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


def test_0_2_to_0_3_is_additive_and_backed_up(tmp_path):
    folder = tmp_path / "exp"
    original = doc("0.2", locator="/v")
    path = write(folder, original)
    before = path.read_bytes()

    code, out, err = run(["migrate", str(folder), "--to", "0.3", "--json"])

    assert (code, err) == (0, "")
    result = json.loads(out)
    assert result == {
        "format": "runevidence-migrate-result-v1",
        "ok": True,
        "from": "0.2",
        "to": "0.3",
        "changed": True,
        "dry_run": False,
        "backup": "created",
    }
    migrated = json.loads(path.read_bytes())
    original_doc = json.loads(before)
    assert migrated["schema_version"] == "0.3"
    assert migrated["results"][0]["locator"] == "/v"
    assert (
        {key: value for key, value in migrated.items() if key != "schema_version"}
        == {key: value for key, value in original_doc.items() if key != "schema_version"}
    )
    assert (folder / (MANIFEST + ".bak")).read_bytes() == before


def test_0_1_to_0_3_is_additive(tmp_path):
    folder = tmp_path / "exp"
    original = doc("0.1", locator="/v")
    path = write(folder, original)

    code, out, err = run(["migrate", str(folder), "--to", "0.3", "--json"])

    assert (code, err) == (0, "")
    assert json.loads(out)["from"] == "0.1"
    migrated = json.loads(path.read_bytes())
    assert migrated["schema_version"] == "0.3"
    assert migrated["results"][0]["locator"] == "/v"
    assert (folder / (MANIFEST + ".bak")).read_bytes() == dump_raw(original).encode()


def test_0_3_is_idempotent(tmp_path):
    folder = tmp_path / "exp"
    path = write(folder, doc("0.3", locator="/v"))
    before = path.read_bytes()

    code, out, err = run(["migrate", str(folder), "--to", "0.3", "--json"])

    assert (code, err) == (0, "")
    result = json.loads(out)
    assert (result["from"], result["to"], result["changed"], result["backup"]) == (
        "0.3", "0.3", False, "none",
    )
    assert path.read_bytes() == before
    assert backups(folder) == []


def test_0_3_to_0_2_is_refused_without_writing(tmp_path):
    folder = tmp_path / "exp"
    path = write(folder, doc("0.3", locator="/v"))
    before = path.read_bytes()

    code, out, err = run(["migrate", str(folder), "--to", "0.2", "--json"])

    assert (code, err) == (2, "")
    assert json.loads(out)["error"]["code"] == "DOWNGRADE_UNSUPPORTED"
    assert path.read_bytes() == before
    assert backups(folder) == []


def test_0_2_to_0_3_dry_run_writes_nothing(tmp_path):
    folder = tmp_path / "exp"
    path = write(folder, doc("0.2", locator="/v"))
    before = path.read_bytes()

    code, out, err = run(["migrate", str(folder), "--to", "0.3", "--dry-run", "--json"])

    assert (code, err) == (0, "")
    result = json.loads(out)
    assert (result["from"], result["to"], result["dry_run"], result["backup"]) == (
        "0.2", "0.3", True, "none",
    )
    assert path.read_bytes() == before
    assert backups(folder) == []


OFFENDERS = {
    "prefixed-pointer": {"locator": "jsonl:/x"},
    "invalid-pointer-escape": {"locator": "/x~2"},
    "missing-artifact-id": {"locator": "/x", "artifact_id": None},
    "non-json-artifact": {
        "artifact": {
            "id": "a",
            "path": "data.csv",
            "sha256": "a" * 64,
            "size_bytes": 8,
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


@pytest.mark.parametrize("label", tuple(OFFENDERS))
def test_locator_offenders_refuse_the_whole_migration(tmp_path, label):
    folder = tmp_path / "exp"
    value = doc("0.2", **OFFENDERS[label])
    # Add a second, valid result: no partial migration may occur.
    value["results"].append(
        {
            "id": "valid",
            "run_id": "run",
            "name": "accuracy",
            "value": 0.9,
            "artifact_id": "a",
            "locator": "/other",
        }
    )
    path = write(folder, value)
    before = path.read_bytes()

    code, out, err = run(["migrate", str(folder), "--to", "0.3", "--json"])

    assert (code, err) == (2, "")
    assert json.loads(out)["error"]["code"] == "LOCATOR_MIGRATION_UNSUPPORTED"
    assert path.read_bytes() == before
    assert backups(folder) == []
    assert not [name for name in path.parent.iterdir() if name.name.endswith(".tmp")]


def test_offender_text_is_fixed_and_value_free(tmp_path):
    folder = tmp_path / "exp"
    path = write(folder, doc("0.2", locator="jsonl:/CANARY-LOCATOR"))
    before = path.read_bytes()

    code, out, err = run(["migrate", str(folder), "--to", "0.3"])

    assert (code, err) == (2, "")
    assert out == (
        "ERROR (LOCATOR_MIGRATION_UNSUPPORTED): the manifest contains a locator "
        "that schema 0.3 cannot accept or verify. Nothing was written.\n"
    )
    assert "CANARY-LOCATOR" not in out
    assert path.read_bytes() == before


def test_empty_pointer_with_artifact_is_allowed(tmp_path):
    folder = tmp_path / "exp"
    path = write(folder, doc("0.2", locator=""))

    code, out, err = run(["migrate", str(folder), "--to", "0.3"])

    assert (code, err) == (0, "")
    assert json.loads(path.read_bytes())["results"][0]["locator"] == ""


def test_uppercase_json_extension_is_allowed(tmp_path):
    folder = tmp_path / "exp"
    path = write(
        folder,
        doc("0.2", artifact={"id": "a", "path": "DATA.JSON", "sha256": "a" * 64, "size_bytes": 8}),
    )

    code, out, err = run(["migrate", str(folder), "--to", "0.3"])

    assert (code, err) == (0, "")


def test_dangling_artifact_id_is_a_later_validation_concern(tmp_path):
    # The migration gate's closed classes do not include a dangling artifact ID;
    # 0.3 loads it and validate later reports the reference error.
    folder = tmp_path / "exp"
    path = write(folder, doc("0.2", locator="/v", artifact_id="missing"))

    code, out, err = run(["migrate", str(folder), "--to", "0.3"])

    assert (code, err) == (0, "")
    assert json.loads(path.read_bytes())["schema_version"] == "0.3"


@pytest.mark.parametrize("target", ["0.1", "0.11", "", "1.0", "0.2.0"])
def test_unsupported_targets_are_refused(tmp_path, target):
    folder = tmp_path / "exp"
    path = write(folder, doc("0.2"))
    before = path.read_bytes()

    code, out, err = run(["migrate", str(folder), "--to", target, "--json"])

    assert (code, err) == (2, "")
    assert json.loads(out)["error"]["code"] == "UNSUPPORTED_TARGET"
    assert path.read_bytes() == before


def test_migrate_help_mentions_0_3():
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(["migrate", "--help"])
    assert (code, err.getvalue()) == (0, "")
    assert "0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9 or 0.10" in " ".join(out.getvalue().split())
