"""Stage 3d-3: the sanitized ``runevidence conditions`` command surface.

These tests exercise the public route through ``cli.main`` (and one real
``python -m runevidence`` entry point). Every refused invocation must emit
exactly one fixed document with a closed code, exit 2, and no submitted token,
path, ID, field name, value or hash. Success JSON never contains a path;
success text may show only the resolved manifest and resolved backup.
"""

from __future__ import annotations

import errno
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from runevidence import cli
from runevidence import conditions_authoring as conditions_module
from runevidence.models import MANIFEST_FILENAME

MANIFEST = MANIFEST_FILENAME
OMIT = object()

RESULT_KEYS = {
    "format", "ok", "run", "changed", "dry_run", "backup",
    "set", "cleared", "set_count", "cleared_count",
}
RESULT_ORDER = [
    "format", "ok", "run", "changed", "dry_run", "backup",
    "set", "cleared", "set_count", "cleared_count",
]
ERROR_KEYS = {"format", "ok", "exit_code", "error"}
FIELDS = (
    "evaluation_split",
    "evaluation_protocol",
    "evaluation_dataset_artifact_id",
    "model_checkpoint_artifact_id",
    "source_revision",
    "environment",
)

# Values that must never travel through either envelope.
CANARY_VALUE = "CANARY-VALUE-SECRET"
CANARY_ID = "CANARY-ID-SECRET"
CANARY_RUN = "CANARY-RUN-SECRET"
CANARY_FIELD = "CANARY-FIELD-SECRET"
CANARY_PATH = "CANARY-PATH-SECRET"


# --------------------------------------------------------------------------
# Fixtures and helpers
# --------------------------------------------------------------------------


def base_doc(
    *,
    version="0.2",
    conditions=OMIT,
    run_id="r1",
    runs=None,
    artifacts=None,
    relationships=None,
) -> dict:
    doc: dict = {}
    if version is not OMIT:
        doc["schema_version"] = version
    doc["project"] = {"id": "proj-1", "name": "Project"}
    if runs is None:
        run: dict = {"id": run_id, "name": "Run"}
        if conditions is not OMIT:
            run["conditions"] = conditions
        runs = [run]
    doc["runs"] = runs
    doc["artifacts"] = [] if artifacts is None else artifacts
    doc["results"] = []
    doc["evidence"] = []
    doc["relationships"] = [] if relationships is None else relationships
    return doc


def selector_doc(*, conditions=OMIT, version="0.2") -> dict:
    artifacts = [
        {"id": "ds-1", "path": "data/train.csv", "kind": "dataset"},
        {"id": "ckpt-1", "path": "model.pt", "kind": "checkpoint"},
        {"id": "dir-1", "path": "data", "kind": "dataset", "artifact_type": "directory",
         "digest_format": "runevidence-directory-v1"},
    ]
    relationships = [
        {"source": "ds-1", "target": "r1", "type": "input_to"},
        {"source": "ckpt-1", "target": "r1", "type": "input_to"},
        {"source": "dir-1", "target": "r1", "type": "input_to"},
    ]
    return base_doc(conditions=conditions, artifacts=artifacts, relationships=relationships)


def write_manifest(folder: Path, doc: dict | None = None, *, name: str = MANIFEST,
                   raw: str | bytes | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    if raw is not None:
        data = raw.encode("utf-8") if isinstance(raw, str) else raw
        path.write_bytes(data)
    else:
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8", newline="\n")
    return path


def write_patch(folder: Path, data, name: str = "patch.json") -> str:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8", newline="\n")
    return str(path)


def _run_cli_captured(argv) -> tuple[int, str, str]:
    import contextlib

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def disk_bytes(path: Path) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def temps(folder: Path) -> list[str]:
    return sorted(
        p.name for p in folder.iterdir()
        if p.name.startswith(f".{MANIFEST}.") and p.name.endswith(".tmp")
    )


def one_json(output: str) -> dict:
    document = json.loads(output)
    assert output.lstrip().startswith("{") and output.rstrip().endswith("}")
    return document


def assert_no_canaries(output: str, extra: tuple[str, ...] = ()) -> None:
    for canary in (CANARY_VALUE, CANARY_ID, CANARY_RUN, CANARY_FIELD, CANARY_PATH, *extra):
        if canary:
            assert canary not in output


def assert_error(output: str, code: str, extra_canaries: tuple[str, ...] = ()) -> None:
    document = one_json(output)
    assert set(document) == ERROR_KEYS
    assert document == {
        "format": "runevidence-conditions-error-v1",
        "ok": False,
        "exit_code": 2,
        "error": {"code": code},
    }
    assert document["ok"] is False
    assert type(document["exit_code"]) is int and document["exit_code"] == 2
    assert set(document["error"]) == {"code"}
    assert_no_canaries(output, extra_canaries)
    # Error documents never carry a field name, a value or a path.
    for field in FIELDS:
        assert field not in output


def assert_result(output: str, **fields) -> dict:
    document = one_json(output)
    assert set(document) == RESULT_KEYS
    assert list(document) == RESULT_ORDER
    expected = {
        "format": "runevidence-conditions-result-v1",
        "ok": True,
        "run": fields["run"],
        "changed": fields["changed"],
        "dry_run": fields["dry_run"],
        "backup": fields["backup"],
        "set": list(fields["set"]),
        "cleared": list(fields["cleared"]),
        "set_count": len(fields["set"]),
        "cleared_count": len(fields["cleared"]),
    }
    assert document == expected
    assert document["ok"] is True
    assert type(document["changed"]) is bool and type(document["dry_run"]) is bool
    assert type(document["set_count"]) is int and type(document["cleared_count"]) is int
    assert_no_canaries(output)
    return document


# --------------------------------------------------------------------------
# Success: real 0.2 changed set/clear, JSON and text
# --------------------------------------------------------------------------


@pytest.mark.parametrize("operand", ["folder", "file"])
def test_real_changed_json_is_closed_path_free_and_value_free(tmp_path, operand):
    folder = tmp_path / CANARY_PATH
    conditions = {
        "evaluation_split": "val",
        "environment": {"python": "3.12", "note": CANARY_VALUE},
    }
    path = write_manifest(folder, base_doc(conditions=conditions))
    original = disk_bytes(path)
    patch = write_patch(
        folder,
        json.dumps({"evaluation_split": "test", "source_revision": "rev-2"}),
    )
    argv = ["conditions", str(folder if operand == "folder" else path),
            "--run", "r1", "--patch", patch, "--clear", "environment", "--json"]

    code, out, err = _run_cli_captured(argv)

    assert (code, err) == (0, "")
    document = assert_result(
        out,
        run="r1",
        changed=True,
        dry_run=False,
        backup="created",
        set=["evaluation_split", "source_revision"],
        cleared=["environment"],
    )
    assert document["set"] == ["evaluation_split", "source_revision"]
    assert document["cleared"] == ["environment"]
    # No path, no value, no selector or artifact ID in the machine document.
    for forbidden in (str(folder), str(path), str(patch), CANARY_VALUE, "test",
                      "rev-2", "3.12", "ds-1", "ckpt-1"):
        assert forbidden not in out
    backup = path.resolve().with_name(f"{MANIFEST}.bak")
    assert disk_bytes(backup) == original
    written = json.loads(disk_bytes(path))
    assert written["runs"][0]["conditions"] == {
        "evaluation_split": "test",
        "source_revision": "rev-2",
    }
    assert names(folder) == ["patch.json", MANIFEST, f"{MANIFEST}.bak"]
    assert temps(folder) == []


@pytest.mark.parametrize("operand", ["folder", "file"])
def test_real_changed_text_shows_only_resolved_paths(tmp_path, operand):
    folder = tmp_path / "experiment"
    path = write_manifest(folder, base_doc(conditions={"environment": {"python": "3.12"}}))
    original = disk_bytes(path)
    resolved = path.resolve()
    backup = resolved.with_name(f"{MANIFEST}.bak")
    patch = write_patch(folder, json.dumps({"evaluation_split": "test"}))

    code, out, err = _run_cli_captured(
        ["conditions", str(folder if operand == "folder" else path),
         "--run", "r1", "--patch", patch]
    )

    assert (code, err) == (0, "")
    assert out == (
        f"Wrote {resolved} for run r1\n"
        "(set: evaluation_split; cleared: none).\n"
        f"Backup: {backup}\n"
    )
    assert_no_canaries(out)
    assert str(patch) not in out
    assert disk_bytes(backup) == original
    assert json.loads(disk_bytes(path))["runs"][0]["conditions"] == {
        "environment": {"python": "3.12"},
        "evaluation_split": "test",
    }


def test_text_reports_none_for_an_empty_operation_side(tmp_path):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc(conditions={"evaluation_split": "val"}))
    patch = write_patch(folder, json.dumps({"source_revision": "rev"}))

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch, "--clear", "evaluation_split"]
    )

    assert (code, err) == (0, "")
    assert "(set: source_revision; cleared: evaluation_split)." in out
    assert str(path.resolve()) in out


def test_byte_identical_backup_generation_is_reused(tmp_path):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc(conditions={"evaluation_split": "val"}))
    original = disk_bytes(path)
    existing = folder / f"{MANIFEST}.bak"
    existing.write_bytes(original)
    patch = write_patch(folder, json.dumps({"evaluation_split": "test"}))
    resolved = path.resolve()

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch]
    )

    assert (code, err) == (0, "")
    assert out == (
        f"Wrote {resolved} for run r1\n"
        "(set: evaluation_split; cleared: none).\n"
        f"Backup: {existing.resolve()}\n"
    )
    assert names(folder) == ["patch.json", MANIFEST, f"{MANIFEST}.bak"]
    assert disk_bytes(existing) == original
    assert json.loads(disk_bytes(path))["runs"][0]["conditions"]["evaluation_split"] == "test"


def test_json_reports_reused_backup(tmp_path):
    folder = tmp_path / CANARY_PATH
    write_manifest(folder, base_doc(conditions={"evaluation_split": "val"}))
    path = folder / MANIFEST
    (folder / f"{MANIFEST}.bak").write_bytes(disk_bytes(path))
    patch = write_patch(folder, json.dumps({"evaluation_split": "test"}))

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch, "--json"]
    )

    assert (code, err) == (0, "")
    assert_result(out, run="r1", changed=True, dry_run=False, backup="reused",
                  set=["evaluation_split"], cleared=[])


# --------------------------------------------------------------------------
# No-op, dry run, already set, clear-only and patch-only
# --------------------------------------------------------------------------


def test_patch_setting_the_current_value_is_a_success_no_op(tmp_path):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc(conditions={"evaluation_split": "test"}))
    before = disk_bytes(path)
    before_mtime = path.stat().st_mtime_ns
    patch = write_patch(folder, json.dumps({"evaluation_split": "test"}))
    resolved = path.resolve()

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch, "--json"]
    )

    assert (code, err) == (0, "")
    assert_result(out, run="r1", changed=False, dry_run=False, backup="none",
                  set=["evaluation_split"], cleared=[])
    assert disk_bytes(path) == before
    assert path.stat().st_mtime_ns == before_mtime
    assert names(folder) == ["patch.json", MANIFEST]
    assert temps(folder) == []

    text_code, text_out, text_err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch]
    )
    assert (text_code, text_err) == (0, "")
    assert text_out == (
        f"{resolved} already matches the requested state for run r1. "
        "Nothing was written.\n"
    )
    assert disk_bytes(path) == before


def test_empty_patch_with_no_clears_is_a_no_op(tmp_path):
    folder = tmp_path / CANARY_PATH
    write_manifest(folder, base_doc(conditions={"evaluation_split": "test"}))
    patch = write_patch(folder, "{}")

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch, "--json"]
    )

    assert (code, err) == (0, "")
    assert_result(out, run="r1", changed=False, dry_run=False, backup="none",
                  set=[], cleared=[])


def test_dry_run_changed_writes_nothing_and_reports_none(tmp_path):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc(conditions={"evaluation_split": "val"}))
    before = disk_bytes(path)
    before_mtime = path.stat().st_mtime_ns
    patch = write_patch(folder, json.dumps({"evaluation_split": "test"}))
    resolved = path.resolve()

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch, "--dry-run", "--json"]
    )

    assert (code, err) == (0, "")
    assert_result(out, run="r1", changed=True, dry_run=True, backup="none",
                  set=["evaluation_split"], cleared=[])
    assert disk_bytes(path) == before
    assert path.stat().st_mtime_ns == before_mtime
    assert names(folder) == ["patch.json", MANIFEST]
    assert temps(folder) == []

    text_code, text_out, text_err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch, "--dry-run"]
    )
    assert (text_code, text_err) == (0, "")
    assert text_out == (
        f"Would update {resolved} for run r1\n"
        "(set: evaluation_split; cleared: none). "
        "Dry run: nothing was written.\n"
    )
    assert disk_bytes(path) == before


def test_dry_run_no_op_says_dry_run_and_writes_nothing(tmp_path):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc(conditions={"evaluation_split": "test"}))
    before = disk_bytes(path)
    patch = write_patch(folder, json.dumps({"evaluation_split": "test"}))
    resolved = path.resolve()

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch, "--dry-run"]
    )

    assert (code, err) == (0, "")
    assert out == (
        f"{resolved} already matches the requested state for run r1. "
        "Dry run: nothing was written.\n"
    )
    assert disk_bytes(path) == before

    json_code, json_out, json_err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch, "--dry-run", "--json"]
    )
    assert (json_code, json_err) == (0, "")
    assert_result(json_out, run="r1", changed=False, dry_run=True, backup="none",
                  set=["evaluation_split"], cleared=[])


def test_clear_only_removes_the_child_and_keeps_others(tmp_path):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(
        folder,
        base_doc(conditions={"evaluation_split": "test", "environment": {"python": "3.12"}}),
    )
    resolved = path.resolve()

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--clear", "environment", "--json"]
    )

    assert (code, err) == (0, "")
    assert_result(out, run="r1", changed=True, dry_run=False, backup="created",
                  set=[], cleared=["environment"])
    conditions = json.loads(disk_bytes(path))["runs"][0]["conditions"]
    assert conditions == {"evaluation_split": "test"}
    assert names(folder) == [MANIFEST, f"{MANIFEST}.bak"]

    text_code, text_out, text_err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--clear", "evaluation_split"]
    )
    assert (text_code, text_err) == (0, "")
    assert "(set: none; cleared: evaluation_split)." in text_out
    assert "conditions" not in json.loads(disk_bytes(path))["runs"][0]
    assert str(resolved) in text_out


def test_clear_of_absent_child_from_empty_object_removes_the_parent(tmp_path):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc(conditions={}))

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--clear", "environment", "--json"]
    )

    assert (code, err) == (0, "")
    assert_result(out, run="r1", changed=True, dry_run=False, backup="created",
                  set=[], cleared=["environment"])
    assert "conditions" not in json.loads(disk_bytes(path))["runs"][0]


def test_patch_only_creates_conditions_and_writes_the_value(tmp_path):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc())
    patch = write_patch(folder, json.dumps({"environment": {"python": "3.10"}}))

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch, "--json"]
    )

    assert (code, err) == (0, "")
    assert_result(out, run="r1", changed=True, dry_run=False, backup="created",
                  set=["environment"], cleared=[])
    run = json.loads(disk_bytes(path))["runs"][0]
    assert run["conditions"] == {"environment": {"python": "3.10"}}
    assert list(run)[-1] == "conditions"


# --------------------------------------------------------------------------
# Patch source: ./- is a file, - is binary stdin
# --------------------------------------------------------------------------


def test_dash_dot_slash_reads_a_file_named_dash(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "exp"
    path = write_manifest(folder, base_doc())
    (tmp_path / "-").write_text(
        json.dumps({"source_revision": "from-file"}), encoding="utf-8", newline="\n"
    )

    code, out, err = _run_cli_captured(
        ["conditions", "exp", "--run", "r1", "--patch", "./-", "--json"]
    )

    assert (code, err) == (0, "")
    assert_result(out, run="r1", changed=True, dry_run=False, backup="created",
                  set=["source_revision"], cleared=[])
    assert json.loads(disk_bytes(path))["runs"][0]["conditions"] == {
        "source_revision": "from-file",
    }


def test_dash_reads_binary_stdin(tmp_path, monkeypatch):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc())
    payload = json.dumps({"environment": {"python": "3.10"}}).encode("utf-8")

    class Stdin:
        def __init__(self, data: bytes):
            self.buffer = io.BytesIO(data)

    monkeypatch.setattr(sys, "stdin", Stdin(payload))

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", "-", "--json"]
    )

    assert (code, err) == (0, "")
    assert_result(out, run="r1", changed=True, dry_run=False, backup="created",
                  set=["environment"], cleared=[])
    assert json.loads(disk_bytes(path))["runs"][0]["conditions"] == {
        "environment": {"python": "3.10"},
    }


def test_closed_stdin_is_sanitized_read_failed(tmp_path, monkeypatch):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc())
    before = disk_bytes(path)

    # With stdin closed (``<&-``), Python sets sys.stdin to None.
    monkeypatch.setattr(sys, "stdin", None)

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", "-", "--json"]
    )

    assert (code, err) == (2, "")
    assert_error(out, "READ_FAILED", (str(folder), str(path)))
    assert disk_bytes(path) == before
    assert names(folder) == [MANIFEST]
    assert temps(folder) == []


# --------------------------------------------------------------------------
# Grammar: unknown, duplicate, missing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "template",
    [
        ["conditions", "{path}", "--patch", "{patch}", "--json"],  # missing --run
        ["conditions", "{path}", "--run", "r1", "--json"],  # neither operation
        ["conditions", "{path}", "--run", "r1", "--patch", "{patch}", "--patch", "{patch}", "--json"],
        ["conditions", "{path}", "--run", "r1", "--clear", "environment", "--clear", "environment", "--json"],
        ["conditions", "{path}", "--run", "r1", "--clear", "not_a_field", "--json"],
        ["conditions", "{path}", "--run", "r1", "--clear", CANARY_FIELD, "--json"],
        ["conditions", "{path}", "--run", "r1", "--patch", "{patch}", "extra", "--json"],
        ["conditions", "{path}", "--run", "r1", "--patch", "{patch}", "--force", "--json"],
        ["conditions", "--run", "r1", "--patch", "{patch}", "--json"],  # missing PATH
        ["conditions", "{path}", "--run", "--patch", "{patch}", "--json"],  # missing run value
    ],
)
def test_grammar_failures_are_sanitized_invalid_arguments(tmp_path, template):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc())
    before = disk_bytes(path)
    patch = write_patch(folder, json.dumps({"evaluation_split": "test"}))
    argv = [part.format(path=path, patch=patch) for part in template]

    code, out, err = _run_cli_captured(argv)

    assert (code, err) == (2, "")
    assert_error(out, "INVALID_ARGUMENTS", (CANARY_FIELD,))
    assert "usage:" not in out
    assert "Traceback" not in out
    assert disk_bytes(path) == before
    assert names(folder) == ["patch.json", MANIFEST]


def test_grammar_failure_without_json_is_fixed_text(tmp_path):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc())

    code, out, err = _run_cli_captured(["conditions", str(folder), "--run", "r1"])

    assert (code, err) == (2, "")
    assert out == (
        "ERROR (INVALID_ARGUMENTS): the conditions command line is invalid. "
        "Nothing was written.\n"
    )
    assert str(folder) not in out and str(path) not in out


@pytest.mark.parametrize(
    "template",
    [
        ["conditions", "{path}", "--run", "r1", "--patch", "{patch}", "--json"],
        ["conditions", "--json", "{path}", "--run", "r1", "--patch", "{patch}"],
        ["conditions", "{path}", "--json", "--run", "r1", "--patch", "{patch}"],
    ],
)
def test_json_flag_position_does_not_change_the_document(tmp_path, template):
    folder = tmp_path / CANARY_PATH
    write_manifest(folder, base_doc())
    patch = write_patch(folder, json.dumps({"evaluation_split": "test"}))
    argv = [part.format(path=folder, patch=patch) for part in template]

    code, out, err = _run_cli_captured(argv)

    assert (code, err) == (0, "")
    assert_result(out, run="r1", changed=True, dry_run=False, backup="created",
                  set=["evaluation_split"], cleared=[])


def test_help_exits_zero_and_writes_nothing(tmp_path):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc())
    before = disk_bytes(path)

    code, out, err = _run_cli_captured(["conditions", "--help"])

    assert (code, err) == (0, "")
    assert out.startswith("usage: runevidence conditions")
    assert "--run RUN_ID" in out
    assert "--patch FILE" in out
    assert "--clear FIELD" in out
    assert disk_bytes(path) == before
    assert names(folder) == [MANIFEST]


def test_conditions_version_is_invalid_arguments(tmp_path):
    folder = tmp_path / CANARY_PATH
    write_manifest(folder, base_doc())

    code, out, err = _run_cli_captured(["conditions", "--version", "--json"])

    assert (code, err) == (2, "")
    assert_error(out, "INVALID_ARGUMENTS")
    assert "0.1.1" not in out


# --------------------------------------------------------------------------
# Refusals: version, reserved path, load, run selection
# --------------------------------------------------------------------------


@pytest.mark.parametrize("version", ["0.1", OMIT])
def test_0_1_and_omitted_version_refuse_before_reading_the_patch(tmp_path, version):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc(version=version))
    before = disk_bytes(path)

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch",
         str(folder / "does-not-exist.json"), "--json"]
    )

    assert (code, err) == (2, "")
    assert_error(out, "UNSUPPORTED_VERSION")
    assert disk_bytes(path) == before
    assert names(folder) == [MANIFEST]

    text_code, text_out, text_err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--clear", "environment"]
    )
    assert (text_code, text_err) == (2, "")
    assert text_out == (
        "ERROR (UNSUPPORTED_VERSION): the conditions command requires an explicit "
        "schema 0.2 manifest. A schema 0.1 manifest can be migrated with "
        "runevidence migrate PATH --to 0.2; schema 0.3 has no downgrade path. "
        "Nothing was written.\n"
    )
    assert str(folder) not in text_out


def test_reserved_backup_target_is_refused_before_load(tmp_path, monkeypatch):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc(conditions={"evaluation_split": "val"}),
                          name=f"{MANIFEST}.bak")
    before = disk_bytes(path)
    patch = write_patch(folder, json.dumps({"evaluation_split": "test"}))
    calls: list[str] = []

    def deny_load(*args, **kwargs):
        calls.append("load")
        raise AssertionError("load must not run for a reserved path")

    monkeypatch.setattr(conditions_module, "load_manifest", deny_load)

    code, out, err = _run_cli_captured(
        ["conditions", str(path), "--run", "r1", "--patch", patch, "--json"]
    )

    assert (code, err) == (2, "")
    assert_error(out, "RESERVED_PATH")
    assert calls == []
    assert disk_bytes(path) == before
    assert names(folder) == ["patch.json", f"{MANIFEST}.bak"]


def test_missing_manifest_is_manifest_load_failed(tmp_path):
    folder = tmp_path / CANARY_PATH
    folder.mkdir()
    patch = write_patch(folder, json.dumps({"evaluation_split": "test"}))

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch, "--json"]
    )

    assert (code, err) == (2, "")
    assert_error(out, "MANIFEST_LOAD_FAILED")


def test_bad_manifest_json_is_manifest_load_failed(tmp_path):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, raw="{not json")
    before = disk_bytes(path)
    patch = write_patch(folder, json.dumps({"evaluation_split": "test"}))

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch, "--json"]
    )

    assert (code, err) == (2, "")
    assert_error(out, "MANIFEST_LOAD_FAILED")
    assert disk_bytes(path) == before


def test_future_schema_version_is_manifest_load_failed(tmp_path):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc(version="0.4"))
    before = disk_bytes(path)
    patch = write_patch(tmp_path, json.dumps({"evaluation_split": "test"}))

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch, "--json"]
    )

    assert (code, err) == (2, "")
    assert_error(out, "MANIFEST_LOAD_FAILED")
    assert disk_bytes(path) == before
    assert names(folder) == [MANIFEST]

    text_code, text_out, text_err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--clear", "environment"]
    )
    assert (text_code, text_err) == (2, "")
    assert text_out == (
        "ERROR (MANIFEST_LOAD_FAILED): the manifest could not be loaded and "
        "validated. Nothing was written.\n"
    )
    assert str(folder) not in text_out
    assert "0.4" not in text_out
    assert disk_bytes(path) == before
    assert names(folder) == [MANIFEST]


def test_schema_0_3_is_unsupported_for_conditions_authoring(tmp_path):
    # 0.3 loads (locator slice A), but conditions authoring remains 0.2-only.
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc(version="0.3"))
    before = disk_bytes(path)
    patch = write_patch(tmp_path, json.dumps({"evaluation_split": "test"}))

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch, "--json"]
    )

    assert (code, err) == (2, "")
    assert_error(out, "UNSUPPORTED_VERSION")
    assert disk_bytes(path) == before
    assert names(folder) == [MANIFEST]


def test_zero_run_matches_is_run_selection_invalid(tmp_path):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc())
    before = disk_bytes(path)
    patch = write_patch(folder, json.dumps({"evaluation_split": "test"}))

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", CANARY_RUN, "--patch", patch, "--json"]
    )

    assert (code, err) == (2, "")
    assert_error(out, "RUN_SELECTION_INVALID")
    assert CANARY_RUN not in out
    assert disk_bytes(path) == before


def test_multiple_run_matches_are_run_selection_invalid(tmp_path):
    folder = tmp_path / CANARY_PATH
    runs = [{"id": "dup", "name": "one"}, {"id": "dup", "name": "two"}]
    path = write_manifest(folder, base_doc(runs=runs))
    before = disk_bytes(path)
    patch = write_patch(folder, json.dumps({"evaluation_split": "test"}))

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "dup", "--patch", patch, "--json"]
    )

    assert (code, err) == (2, "")
    assert_error(out, "RUN_SELECTION_INVALID")
    assert disk_bytes(path) == before


# --------------------------------------------------------------------------
# Refusals: patch content and selectors
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        b"",  # empty file
        b"null",  # not an object
        b"[]",  # array
        b'{"evaluation_split": "a", "evaluation_split": "b"}',  # duplicate key
        b'{"unknown": 1}',  # unknown key
        b'{"conditions": {}}',  # not a conditions wrapper
        b'{"evaluation_split": NaN}',  # non-finite
        b'{"environment": {"python": 1e300}}',  # float magnitude
        b'{"evaluation_protocol": {}}',  # protocol_id missing
        b'{"environment": {}}',  # present-empty environment refused
        b'{"environment": {"n": 9007199254740992}}',  # integer magnitude
        b'{"evaluation_split": "\\ud800"}',  # lone surrogate
        b'{"evaluation_dataset_artifact_id": null}',  # selector null
        b'{"evaluation_split": "a"} trailing',  # trailing data
        b"\xef\xbb\xbf{}",  # UTF-8 BOM
    ],
)
def test_invalid_patch_content_is_patch_invalid(tmp_path, payload):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc())
    before = disk_bytes(path)
    patch = write_patch(folder, payload)

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch, "--json"]
    )

    assert (code, err) == (2, "")
    assert_error(out, "PATCH_INVALID")
    assert disk_bytes(path) == before
    assert names(folder) == ["patch.json", MANIFEST]
    assert temps(folder) == []


def test_patch_and_clear_for_the_same_field_is_patch_invalid(tmp_path):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc(conditions={"evaluation_split": "val"}))
    before = disk_bytes(path)
    patch = write_patch(folder, json.dumps({"evaluation_split": "test"}))

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch,
         "--clear", "evaluation_split", "--json"]
    )

    assert (code, err) == (2, "")
    assert_error(out, "PATCH_INVALID")
    assert disk_bytes(path) == before


@pytest.mark.parametrize(
    "patch_value, extra_canary",
    [
        ({"evaluation_dataset_artifact_id": CANARY_ID}, CANARY_ID),  # missing
        ({"evaluation_dataset_artifact_id": "ckpt-1"}, "ckpt-1"),  # wrong kind
        ({"model_checkpoint_artifact_id": "ds-1"}, "ds-1"),  # wrong kind
        ({"evaluation_dataset_artifact_id": "dir-1"}, "dir-1"),  # directory
        ({"evaluation_dataset_artifact_id": "ds-2"}, "ds-2"),  # no input_to edge
    ],
)
def test_patch_supplied_selector_failures_are_selector_invalid(tmp_path, patch_value, extra_canary):
    doc = selector_doc()
    if "ds-2" in patch_value.values():
        doc["artifacts"].append({"id": "ds-2", "path": "data/other.csv", "kind": "dataset"})
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, doc)
    before = disk_bytes(path)
    patch = write_patch(folder, json.dumps(patch_value))

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch, "--json"]
    )

    assert (code, err) == (2, "")
    assert_error(out, "SELECTOR_INVALID", (extra_canary,))
    assert extra_canary not in out
    assert disk_bytes(path) == before


def test_valid_selectors_are_accepted_and_never_echoed(tmp_path):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, selector_doc())
    patch = write_patch(folder, json.dumps({
        "evaluation_dataset_artifact_id": "ds-1",
        "model_checkpoint_artifact_id": "ckpt-1",
    }))

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch, "--json"]
    )

    assert (code, err) == (0, "")
    assert_result(out, run="r1", changed=True, dry_run=False, backup="created",
                  set=["evaluation_dataset_artifact_id", "model_checkpoint_artifact_id"],
                  cleared=[])
    assert "ds-1" not in out and "ckpt-1" not in out
    conditions = json.loads(disk_bytes(path))["runs"][0]["conditions"]
    assert conditions["evaluation_dataset_artifact_id"] == "ds-1"
    assert conditions["model_checkpoint_artifact_id"] == "ckpt-1"


def test_re_supplying_a_same_value_invalid_selector_is_selector_invalid(tmp_path):
    folder = tmp_path / CANARY_PATH
    doc = selector_doc(conditions={"evaluation_dataset_artifact_id": "ds-2"})
    doc["artifacts"].append({"id": "ds-2", "path": "data/other.csv", "kind": "dataset"})
    path = write_manifest(folder, doc)
    before = disk_bytes(path)
    patch = write_patch(folder, json.dumps({"evaluation_dataset_artifact_id": "ds-2"}))

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch, "--json"]
    )

    assert (code, err) == (2, "")
    assert_error(out, "SELECTOR_INVALID")
    assert disk_bytes(path) == before


# --------------------------------------------------------------------------
# Refusals: serialization, read, stale, write and internal failures
# --------------------------------------------------------------------------


def test_patch_file_read_failure_is_read_failed(tmp_path):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc())
    before = disk_bytes(path)

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch",
         str(folder / "missing-patch.json"), "--json"]
    )

    assert (code, err) == (2, "")
    assert_error(out, "READ_FAILED")
    assert disk_bytes(path) == before


def test_serialization_failure_is_serialization_failed(tmp_path, monkeypatch):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc(conditions={"evaluation_split": "val"}))
    before = disk_bytes(path)
    patch = write_patch(folder, json.dumps({"evaluation_split": "test"}))

    def fail(candidate):
        raise UnicodeEncodeError("utf-8", "x", 0, 1, "cannot encode")

    monkeypatch.setattr(conditions_module, "dump_raw", fail)

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch, "--json"]
    )

    assert (code, err) == (2, "")
    assert_error(out, "SERIALIZATION_FAILED")
    assert disk_bytes(path) == before


def test_pre_backup_read_failure_is_read_failed(tmp_path, monkeypatch):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc(conditions={"evaluation_split": "val"}))
    before = disk_bytes(path)
    patch = write_patch(folder, json.dumps({"evaluation_split": "test"}))

    def fail(target):
        raise conditions_module.ConditionsError("READ_FAILED")

    monkeypatch.setattr(conditions_module, "_read_pre_backup", fail)

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch, "--json"]
    )

    assert (code, err) == (2, "")
    assert_error(out, "READ_FAILED")
    assert disk_bytes(path) == before
    assert names(folder) == ["patch.json", MANIFEST]


def test_pre_backup_stale_read_is_stale_manifest(tmp_path, monkeypatch):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc(conditions={"evaluation_split": "val"}))
    before = disk_bytes(path)
    patch = write_patch(folder, json.dumps({"evaluation_split": "test"}))

    monkeypatch.setattr(conditions_module, "_read_pre_backup", lambda target: b"different")

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch, "--json"]
    )

    assert (code, err) == (2, "")
    assert_error(out, "STALE_MANIFEST")
    assert disk_bytes(path) == before
    assert names(folder) == ["patch.json", MANIFEST]


def test_save_raw_oserror_with_unchanged_bytes_is_write_failed(tmp_path, monkeypatch):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc(conditions={"evaluation_split": "val"}))
    before = disk_bytes(path)
    patch = write_patch(folder, json.dumps({"evaluation_split": "test"}))

    def fail(candidate, target, *, expected_bytes):
        raise OSError(errno.EIO, "write failed")

    monkeypatch.setattr(conditions_module, "save_raw", fail)

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch, "--json"]
    )

    assert (code, err) == (2, "")
    assert_error(out, "WRITE_FAILED")
    assert disk_bytes(path) == before
    assert names(folder) == ["patch.json", MANIFEST, f"{MANIFEST}.bak"]


def test_unexpected_exception_is_internal_error(tmp_path, monkeypatch):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc())
    before = disk_bytes(path)
    patch = write_patch(folder, json.dumps({"evaluation_split": "test"}))

    def boom(*args, **kwargs):
        raise RuntimeError(f"{CANARY_PATH} {CANARY_RUN} {CANARY_FIELD}")

    monkeypatch.setattr(cli, "author_conditions", boom)

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch, "--json"]
    )

    assert (code, err) == (2, "")
    assert_error(out, "INTERNAL_ERROR")
    assert disk_bytes(path) == before


def test_internal_error_text_is_one_fixed_line(tmp_path, monkeypatch):
    folder = tmp_path / CANARY_PATH
    write_manifest(folder, base_doc())
    patch = write_patch(folder, json.dumps({"evaluation_split": "test"}))

    monkeypatch.setattr(conditions_module, "dump_raw",
                        lambda candidate: (_ for _ in ()).throw(RuntimeError("boom")))

    code, out, err = _run_cli_captured(
        ["conditions", str(folder), "--run", "r1", "--patch", patch]
    )

    assert (code, err) == (2, "")
    assert out == (
        "ERROR (INTERNAL_ERROR): the conditions update failed internally; "
        "the manifest was not replaced.\n"
    )
    assert "boom" not in out


# --------------------------------------------------------------------------
# S4-2a admission-lock busy outcome
# --------------------------------------------------------------------------


BUSY_LINE = (
    "ERROR (BUSY): another RunEvidence writer is updating the manifest. "
    "Nothing was written."
)


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the S4-2a admission lock is Linux-only (no Windows parity claim)",
)
def test_busy_json_is_the_closed_path_free_envelope(tmp_path):
    from runevidence import directory_publication as dp

    folder = tmp_path / CANARY_PATH
    path = write_manifest(
        folder, base_doc(conditions={"environment": {"CANARY_ENV": 1}})
    )
    original = disk_bytes(path)

    with dp.builtin_writer_admission_lock(folder):
        code, out, err = _run_cli_captured(
            ["conditions", str(folder), "--run", "r1", "--clear", "environment", "--json"]
        )

    assert (code, err) == (2, "")
    assert_error(out, "BUSY")
    assert str(folder) not in out and str(path) not in out
    assert "environment" not in out and "CANARY_ENV" not in out
    assert disk_bytes(path) == original
    assert names(folder) == [MANIFEST]


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the S4-2a admission lock is Linux-only (no Windows parity claim)",
)
def test_busy_text_is_one_fixed_line_and_a_dry_run_takes_no_lock(tmp_path):
    from runevidence import directory_publication as dp

    folder = tmp_path / CANARY_PATH
    path = write_manifest(
        folder, base_doc(conditions={"environment": {"CANARY_ENV": 1}})
    )
    original = disk_bytes(path)

    with dp.builtin_writer_admission_lock(folder):
        code, out, err = _run_cli_captured(
            ["conditions", str(folder), "--run", "r1", "--clear", "environment"]
        )
        assert (code, err) == (2, "")
        assert out == BUSY_LINE + "\n"
        # No lock for a dry run: it succeeds while the writer lock is held.
        code, dry_out, err = _run_cli_captured(
            ["conditions", str(folder), "--run", "r1", "--clear", "environment",
             "--dry-run"]
        )

    assert (code, err) == (0, "")
    assert "Dry run: nothing was written." in dry_out
    assert BUSY_LINE not in dry_out
    assert disk_bytes(path) == original


# --------------------------------------------------------------------------
# Real entry point
# --------------------------------------------------------------------------


def test_real_module_entry_point_success_and_refusal(tmp_path):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, base_doc(conditions={"evaluation_split": "val"}))
    patch = write_patch(folder, json.dumps({"evaluation_split": "test"}))
    env = dict(os.environ)
    root = Path(__file__).resolve().parent.parent
    env["PYTHONPATH"] = str(root / "src") + os.pathsep + env.get("PYTHONPATH", "")

    completed = subprocess.run(
        [sys.executable, "-m", "runevidence", "conditions", str(folder),
         "--run", "r1", "--patch", patch, "--json"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    document = one_json(completed.stdout)
    assert document["format"] == "runevidence-conditions-result-v1"
    assert document["changed"] is True
    assert str(folder) not in completed.stdout
    assert json.loads(disk_bytes(path))["runs"][0]["conditions"]["evaluation_split"] == "test"

    refused = subprocess.run(
        [sys.executable, "-m", "runevidence", "conditions", str(folder),
         "--run", "nope", "--patch", patch, "--json"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path),
    )

    assert refused.returncode == 2
    assert refused.stderr == ""
    error = one_json(refused.stdout)
    assert error == {
        "format": "runevidence-conditions-error-v1",
        "ok": False,
        "exit_code": 2,
        "error": {"code": "RUN_SELECTION_INVALID"},
    }
    assert "nope" not in refused.stdout


def test_conditions_preserves_directory_artifacts(tmp_path):
    folder = tmp_path / "exp"
    folder.mkdir()
    path = folder / MANIFEST
    path.write_text(json.dumps(selector_doc()), encoding="utf-8", newline="\n")
    patch = tmp_path / "patch.json"
    patch.write_text('{"source_revision": "rev-2"}\n', encoding="utf-8")

    code, out, err = _run_cli_captured([
        "conditions", str(folder), "--run", "r1", "--patch", str(patch)
    ])

    assert (code, err) == (0, "")
    after = json.loads(path.read_text(encoding="utf-8"))
    directory = next(a for a in after["artifacts"] if a["id"] == "dir-1")
    assert directory["artifact_type"] == "directory"
    assert directory["digest_format"] == "runevidence-directory-v1"
