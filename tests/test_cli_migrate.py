"""Stage 3d-2c1: the sanitized ``runevidence migrate`` command surface.

These tests exercise the public route through ``cli.main`` (and one real
``python -m runevidence`` entry point). Every refused invocation must emit
exactly one fixed document with a closed code, exit 2, and no submitted token,
path, ID, value or hash. JSON success never contains a path; text success may
show only the resolved manifest and resolved backup.
"""

from __future__ import annotations

import contextlib
import errno
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from runevidence import cli
from runevidence import migrate as migrate_module
from runevidence.models import MANIFEST_FILENAME

MANIFEST = MANIFEST_FILENAME
OMIT = object()

RESULT_KEYS = {"format", "ok", "from", "to", "changed", "dry_run", "backup"}
ERROR_KEYS = {"format", "ok", "exit_code", "error"}

# Values that must never travel through either envelope.
CANARY_VALUE = "CANARY-VALUE-SECRET"
CANARY_ID = "CANARY-ID-SECRET"
CANARY_CLAIM = "CANARY-CLAIM-SECRET"
CANARY_PATH = "CANARY-PATH-SECRET"

BUSY_LINE = (
    "ERROR (BUSY): another RunEvidence writer is updating the manifest. "
    "Nothing was written."
)


# --------------------------------------------------------------------------
# Fixtures and helpers
# --------------------------------------------------------------------------


def rich_doc(*, version=OMIT) -> dict:
    doc: dict = {}
    if version is not OMIT:
        doc["schema_version"] = version
    doc["project"] = {
        "id": CANARY_ID,
        "name": CANARY_VALUE,
        "metadata": {"zeta": 1, "alpha": {"k": [3, 2, 1], "n": None}},
    }
    doc["runs"] = [
        {
            "id": "run-b",
            "name": "Run B",
            "params": {"epochs": 0, "flag": False, "nil": None},
            "metadata": {"z": 1, "a": 2},
        }
    ]
    doc["results"] = [
        {"id": "res-int", "run_id": "run-b", "name": "int", "value": 1}
    ]
    doc["evidence"] = [
        {
            "id": "ev-1",
            "claim": CANARY_CLAIM,
            "supports": ["res-int"],
            "contradicts": [],
        }
    ]
    return doc


def write_manifest(
    folder: Path,
    *,
    version=OMIT,
    name: str = MANIFEST,
    raw: str | None = None,
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    if raw is None:
        raw = json.dumps(rich_doc(version=version), indent=2, ensure_ascii=False) + "\n"
    path.write_text(raw, encoding="utf-8", newline="\n")
    return path


def run_cli(argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def disk_bytes(path: Path) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def temps(folder: Path) -> list[str]:
    return sorted(
        p.name
        for p in folder.iterdir()
        if p.name.startswith(f".{MANIFEST}.") and p.name.endswith(".tmp")
    )


def one_json(output: str) -> dict:
    document = json.loads(output)
    assert output.lstrip().startswith("{") and output.rstrip().endswith("}")
    return document


def assert_no_canaries(output: str, extra: tuple[str, ...] = ()) -> None:
    for canary in (CANARY_VALUE, CANARY_ID, CANARY_CLAIM, *extra):
        assert canary not in output


def assert_error(output: str, code: str, extra_canaries: tuple[str, ...] = ()) -> None:
    document = one_json(output)
    assert set(document) == ERROR_KEYS
    assert document == {
        "format": "runevidence-migrate-error-v1",
        "ok": False,
        "exit_code": 2,
        "error": {"code": code},
    }
    assert document["ok"] is False
    assert type(document["exit_code"]) is int and document["exit_code"] == 2
    assert set(document["error"]) == {"code"}
    assert_no_canaries(output, extra_canaries)


def assert_result(output: str, **fields) -> None:
    document = one_json(output)
    assert set(document) == RESULT_KEYS
    expected = {
        "format": "runevidence-migrate-result-v1",
        "ok": True,
        "from": fields["from"],
        "to": fields["to"],
        "changed": fields["changed"],
        "dry_run": fields["dry_run"],
        "backup": fields["backup"],
    }
    assert document == expected
    assert document["ok"] is True
    assert type(document["changed"]) is bool and type(document["dry_run"]) is bool
    assert_no_canaries(output)


# --------------------------------------------------------------------------
# Success: real migration, JSON and text
# --------------------------------------------------------------------------


@pytest.mark.parametrize("operand", ["folder", "file"])
def test_real_0_1_json_success_is_closed_and_path_free(tmp_path, operand):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder)
    original = disk_bytes(path)
    argv = ["migrate", str(folder if operand == "folder" else path), "--to", "0.2", "--json"]

    code, out, err = run_cli(argv)

    assert (code, err) == (0, "")
    assert_result(out, **{"from": "0.1", "to": "0.2", "changed": True,
                          "dry_run": False, "backup": "created"})
    backup = path.resolve().with_name(f"{MANIFEST}.bak")
    assert disk_bytes(backup) == original
    assert json.loads(disk_bytes(path))["schema_version"] == "0.2"
    assert str(folder) not in out and str(path) not in out and str(backup) not in out
    assert "0" * 64 not in out
    assert names(folder) == [MANIFEST, f"{MANIFEST}.bak"]
    assert temps(folder) == []


@pytest.mark.parametrize("operand", ["folder", "file"])
def test_real_0_1_text_success_shows_only_resolved_paths(tmp_path, operand):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder)
    original = disk_bytes(path)
    resolved = path.resolve()
    backup = resolved.with_name(f"{MANIFEST}.bak")

    code, out, err = run_cli(
        ["migrate", str(folder if operand == "folder" else path), "--to", "0.2"]
    )

    assert (code, err) == (0, "")
    assert out == f"Wrote {resolved} (schema 0.1 -> 0.2). Backup: {backup}.\n"
    assert_no_canaries(out)
    assert disk_bytes(backup) == original
    assert json.loads(disk_bytes(path))["schema_version"] == "0.2"


# --------------------------------------------------------------------------
# Success: already 0.2, dry run, reuse and idempotence
# --------------------------------------------------------------------------


@pytest.mark.parametrize("dry_run", [False, True])
def test_already_0_2_is_a_success_no_op(tmp_path, dry_run):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, version="0.2")
    before = disk_bytes(path)
    before_mtime = path.stat().st_mtime_ns
    resolved = path.resolve()
    argv = ["migrate", str(folder), "--to", "0.2", "--json"]
    if dry_run:
        argv.append("--dry-run")

    code, out, err = run_cli(argv)

    assert (code, err) == (0, "")
    assert_result(out, **{"from": "0.2", "to": "0.2", "changed": False,
                          "dry_run": dry_run, "backup": "none"})
    assert disk_bytes(path) == before
    assert path.stat().st_mtime_ns == before_mtime
    assert names(folder) == [MANIFEST]

    text_code, text_out, text_err = run_cli(
        ["migrate", str(folder), "--to", "0.2"] + (["--dry-run"] if dry_run else [])
    )
    assert (text_code, text_err) == (0, "")
    assert text_out == f"{resolved} already declares schema 0.2. Nothing was written.\n"


def test_0_1_dry_run_json_and_text_write_nothing(tmp_path):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder)
    before = disk_bytes(path)
    before_mtime = path.stat().st_mtime_ns
    resolved = path.resolve()

    code, out, err = run_cli(["migrate", str(folder), "--to", "0.2", "--dry-run", "--json"])

    assert (code, err) == (0, "")
    assert_result(out, **{"from": "0.1", "to": "0.2", "changed": True,
                          "dry_run": True, "backup": "none"})
    assert disk_bytes(path) == before
    assert path.stat().st_mtime_ns == before_mtime
    assert names(folder) == [MANIFEST]
    assert temps(folder) == []

    text_code, text_out, text_err = run_cli(
        ["migrate", str(folder), "--to", "0.2", "--dry-run"]
    )
    assert (text_code, text_err) == (0, "")
    assert text_out == (
        f"Would declare schema 0.2 for {resolved} (0.1 -> 0.2). "
        "Dry run: nothing was written.\n"
    )
    assert_no_canaries(text_out)
    assert disk_bytes(path) == before


def test_byte_identical_backup_generation_is_reused(tmp_path):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder)
    original = disk_bytes(path)
    existing = folder / f"{MANIFEST}.bak"
    existing.write_bytes(original)

    code, out, err = run_cli(["migrate", str(folder), "--to", "0.2", "--json"])

    assert (code, err) == (0, "")
    assert_result(out, **{"from": "0.1", "to": "0.2", "changed": True,
                          "dry_run": False, "backup": "reused"})
    assert names(folder) == [MANIFEST, f"{MANIFEST}.bak"]
    assert disk_bytes(existing) == original
    assert json.loads(disk_bytes(path))["schema_version"] == "0.2"


def test_second_invocation_is_idempotent(tmp_path):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder)
    first_code, first_out, _ = run_cli(["migrate", str(folder), "--to", "0.2", "--json"])
    assert first_code == 0
    migrated = disk_bytes(path)
    migrated_mtime = path.stat().st_mtime_ns

    code, out, err = run_cli(["migrate", str(folder), "--to", "0.2", "--json"])

    assert (code, err) == (0, "")
    assert_result(out, **{"from": "0.2", "to": "0.2", "changed": False,
                          "dry_run": False, "backup": "none"})
    assert disk_bytes(path) == migrated
    assert path.stat().st_mtime_ns == migrated_mtime
    assert names(folder) == [MANIFEST, f"{MANIFEST}.bak"]


# --------------------------------------------------------------------------
# Refusals: version, grammar, reserved path, load, serialization
# --------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["0.1", "0.4", "", "1.0", "0.2.0"])
def test_unsupported_target_refused_without_touching_anything(tmp_path, target):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder)
    before = disk_bytes(path)

    code, out, err = run_cli(["migrate", str(folder), "--to", target, "--json"])

    assert (code, err) == (2, "")
    assert_error(out, "UNSUPPORTED_TARGET", (target,) if target else ())
    assert disk_bytes(path) == before
    assert names(folder) == [MANIFEST]

    text_code, text_out, text_err = run_cli(["migrate", str(folder), "--to", target])
    assert (text_code, text_err) == (2, "")
    assert "UNSUPPORTED_TARGET" in text_out
    assert str(folder) not in text_out and str(path) not in text_out
    assert_no_canaries(text_out, (target,) if target else ())
    assert disk_bytes(path) == before


INVALID_ARGUMENT_ARGV = [
    ["migrate", "{path}", "--json"],  # missing --to
    ["migrate", "{path}", "--to"],  # missing --to value
    ["migrate", "--to", "0.2", "--json"],  # missing PATH
    ["migrate", "{path}", "--to", "0.2", "extra", "--json"],  # extra operand
    ["migrate", "{path}", "--to", "0.2", "--force", "--json"],  # unknown option
    ["migrate", "--to", "0.2", "--json", "--CANARY-TOKEN-SECRET"],  # option-like path
]


@pytest.mark.parametrize("template", INVALID_ARGUMENT_ARGV)
def test_grammar_failures_are_sanitized_invalid_arguments(tmp_path, template):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder)
    before = disk_bytes(path)
    argv = [part.format(path=path) for part in template]

    code, out, err = run_cli(argv)

    assert (code, err) == (2, "")
    if "--json" in argv:
        assert_error(out, "INVALID_ARGUMENTS", ("CANARY-TOKEN-SECRET",))
    else:
        assert "INVALID_ARGUMENTS" in out
        assert str(folder) not in out and str(path) not in out
    assert "usage:" not in out and "error:" not in out.lower().replace("error (", "")
    assert disk_bytes(path) == before
    assert names(folder) == [MANIFEST]


@pytest.mark.parametrize(
    "template",
    [
        ["migrate", "{path}", "--to", "0.2", "--json"],
        ["migrate", "--json", "{path}", "--to", "0.2"],
        ["migrate", "{path}", "--json", "--to", "0.2"],
    ],
)
def test_json_flag_position_does_not_change_the_document(tmp_path, template):
    folder = tmp_path / CANARY_PATH
    write_manifest(folder)
    argv = [part.format(path=folder) for part in template]

    code, out, err = run_cli(argv)

    assert (code, err) == (0, "")
    assert_result(out, **{"from": "0.1", "to": "0.2", "changed": True,
                          "dry_run": False, "backup": "created"})
    assert str(folder) not in out


def test_reserved_backup_target_is_refused_before_load(tmp_path, monkeypatch):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, name=f"{MANIFEST}.bak")
    before = disk_bytes(path)
    calls: list[str] = []

    def deny_load(*args, **kwargs):
        calls.append("load")
        raise AssertionError("load must not run for a reserved path")

    monkeypatch.setattr(migrate_module, "load_manifest", deny_load)

    code, out, err = run_cli(["migrate", str(path), "--to", "0.2", "--json"])

    assert (code, err) == (2, "")
    assert_error(out, "RESERVED_PATH")
    assert calls == []
    assert disk_bytes(path) == before
    assert names(folder) == [f"{MANIFEST}.bak"]

    text_code, text_out, text_err = run_cli(["migrate", str(path), "--to", "0.2"])
    assert (text_code, text_err) == (2, "")
    assert "RESERVED_PATH" in text_out
    assert str(folder) not in text_out and str(path) not in text_out


def test_missing_manifest_is_sanitized_load_failure(tmp_path):
    missing = tmp_path / CANARY_PATH / MANIFEST
    missing.parent.mkdir()

    code, out, err = run_cli(["migrate", str(missing), "--to", "0.2", "--json"])

    assert (code, err) == (2, "")
    assert_error(out, "MANIFEST_LOAD_FAILED")
    assert str(missing) not in out and str(missing.parent) not in out

    text_code, text_out, text_err = run_cli(["migrate", str(missing), "--to", "0.2"])
    assert (text_code, text_err) == (2, "")
    assert "MANIFEST_LOAD_FAILED" in text_out
    assert str(missing) not in text_out and str(missing.parent) not in text_out


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the S4-2a admission lock is Linux-only (no Windows parity claim)",
)
def test_missing_folder_keeps_manifest_load_failed(tmp_path):
    # L1 pin: the pre-admission shape check preserves the legacy error for a
    # path whose folder does not exist; it must not become LOCK_UNAVAILABLE.
    missing = tmp_path / CANARY_PATH / MANIFEST

    code, out, err = run_cli(["migrate", str(missing), "--to", "0.2", "--json"])
    assert (code, err) == (2, "")
    assert_error(out, "MANIFEST_LOAD_FAILED")
    assert str(missing) not in out and str(missing.parent) not in out
    assert "LOCK_UNAVAILABLE" not in out and "BUSY" not in out

    code, out, err = run_cli(["migrate", str(missing), "--to", "0.2"])
    assert (code, err) == (2, "")
    assert out == (
        "ERROR (MANIFEST_LOAD_FAILED): the manifest could not be loaded and "
        "validated. Nothing was written.\n"
    )


def test_bad_json_is_sanitized_load_failure(tmp_path):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder, raw='{"project": {"id": "p"\n')
    before = disk_bytes(path)

    code, out, err = run_cli(["migrate", str(folder), "--to", "0.2", "--json"])

    assert (code, err) == (2, "")
    assert_error(out, "MANIFEST_LOAD_FAILED")
    assert disk_bytes(path) == before
    assert names(folder) == [MANIFEST]


def test_lone_surrogate_is_serialization_failure_without_writes(tmp_path):
    folder = tmp_path / CANARY_PATH
    raw = (
        '{"project": {"id": "proj-1", "name": "CANARY-\\ud800-SECRET"}, '
        '"runs": []}\n'
    )
    path = write_manifest(folder, raw=raw)
    before = disk_bytes(path)

    code, out, err = run_cli(["migrate", str(folder), "--to", "0.2", "--json"])

    assert (code, err) == (2, "")
    assert_error(out, "SERIALIZATION_FAILED", ("ud800", "surrogate"))
    assert disk_bytes(path) == before
    assert names(folder) == [MANIFEST]

    text_code, text_out, text_err = run_cli(["migrate", str(folder), "--to", "0.2"])
    assert (text_code, text_err) == (2, "")
    assert "SERIALIZATION_FAILED" in text_out
    assert str(folder) not in text_out


# --------------------------------------------------------------------------
# Refusals: stale, read, write and internal failures (monkeypatched)
# --------------------------------------------------------------------------


def test_late_stale_observation_is_sanitized_and_keeps_backup(tmp_path, monkeypatch):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder)
    original = disk_bytes(path)
    external = b'{"project": {"id": "p", "name": "EXTERNAL-CANARY"}}\n'
    real_save = migrate_module.save_raw

    def racing_save(data, target, *, expected_bytes):
        with open(target, "wb") as fh:
            fh.write(external)
        return real_save(data, target, expected_bytes=expected_bytes)

    monkeypatch.setattr(migrate_module, "save_raw", racing_save)

    code, out, err = run_cli(["migrate", str(folder), "--to", "0.2", "--json"])

    assert (code, err) == (2, "")
    assert_error(out, "STALE_MANIFEST", ("EXTERNAL-CANARY",))
    assert disk_bytes(path) == external
    assert disk_bytes(folder / f"{MANIFEST}.bak") == original
    assert temps(folder) == []


def test_pre_backup_read_failure_is_sanitized(tmp_path, monkeypatch):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder)
    resolved = path.resolve()
    before = disk_bytes(path)
    real_read = Path.read_bytes
    calls = {"count": 0}

    def failing_read(self):
        if Path(self) == resolved:
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError(errno.EACCES, f"SECRET {self}")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", failing_read)

    code, out, err = run_cli(["migrate", str(folder), "--to", "0.2", "--json"])

    assert (code, err) == (2, "")
    assert_error(out, "READ_FAILED", (str(folder), "SECRET"))
    assert calls["count"] == 2
    assert disk_bytes(path) == before
    assert names(folder) == [MANIFEST]


def test_write_failure_is_sanitized_and_keeps_backup(tmp_path, monkeypatch):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder)
    original = disk_bytes(path)

    def failing_save(data, target, *, expected_bytes):
        raise OSError(errno.EIO, "SECRET path " + str(target))

    monkeypatch.setattr(migrate_module, "save_raw", failing_save)

    code, out, err = run_cli(["migrate", str(folder), "--to", "0.2", "--json"])

    assert (code, err) == (2, "")
    assert_error(out, "WRITE_FAILED", (str(path), "SECRET"))
    assert disk_bytes(path) == original
    assert disk_bytes(folder / f"{MANIFEST}.bak") == original

    text_code, text_out, text_err = run_cli(["migrate", str(folder), "--to", "0.2"])
    assert (text_code, text_err) == (2, "")
    assert "WRITE_FAILED" in text_out
    assert "SECRET" not in text_out and str(path) not in text_out


def test_unexpected_exception_is_sanitized_internal_error(tmp_path, monkeypatch):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder)
    before = disk_bytes(path)

    def boom(*args, **kwargs):
        raise RuntimeError("SECRET internal " + str(path))

    monkeypatch.setattr(cli, "migrate_manifest", boom)

    code, out, err = run_cli(["migrate", str(folder), "--to", "0.2", "--json"])

    assert (code, err) == (2, "")
    assert_error(out, "INTERNAL_ERROR", (str(path), "SECRET", "RuntimeError"))
    assert disk_bytes(path) == before
    assert names(folder) == [MANIFEST]


# --------------------------------------------------------------------------
# Help and the real module entry point
# --------------------------------------------------------------------------


def test_migrate_help_exits_zero_without_dispatch(tmp_path, monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("help dispatched cmd_migrate")

    monkeypatch.setattr(cli, "cmd_migrate", denied)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(["migrate", "--help"])

    assert (code, err.getvalue()) == (0, "")
    text = out.getvalue()
    assert text.startswith("usage: runevidence migrate")
    assert "--to VERSION" in text and "--dry-run" in text and "--json" in text


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the S4-2a admission lock is Linux-only (no Windows parity claim)",
)
def test_busy_json_is_the_closed_path_free_envelope(tmp_path):
    from runevidence import directory_publication as dp

    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder)
    original = disk_bytes(path)

    with dp.builtin_writer_admission_lock(folder):
        code, out, err = run_cli(
            ["migrate", str(folder), "--to", "0.2", "--json"]
        )

    assert (code, err) == (2, "")
    assert_error(out, "BUSY")
    assert str(folder) not in out and str(path) not in out
    assert disk_bytes(path) == original
    assert names(folder) == [MANIFEST]

    # Success output is unchanged once the cooperating writer releases it.
    code, out, err = run_cli(["migrate", str(folder), "--to", "0.2", "--json"])
    assert (code, err) == (0, "")
    assert_result(out, **{"from": "0.1", "to": "0.2", "changed": True,
                          "dry_run": False, "backup": "created"})


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the S4-2a admission lock is Linux-only (no Windows parity claim)",
)
def test_busy_text_is_one_fixed_line_and_a_dry_run_takes_no_lock(tmp_path):
    from runevidence import directory_publication as dp

    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder)
    original = disk_bytes(path)

    with dp.builtin_writer_admission_lock(folder):
        code, out, err = run_cli(["migrate", str(folder), "--to", "0.2"])
        assert (code, err) == (2, "")
        assert out == BUSY_LINE + "\n"
        # A dry run never takes the lock, so it succeeds while it is held.
        code, dry_out, err = run_cli(
            ["migrate", str(folder), "--to", "0.2", "--dry-run"]
        )

    assert (code, err) == (0, "")
    assert "Dry run: nothing was written." in dry_out
    assert BUSY_LINE not in dry_out
    assert disk_bytes(path) == original


def test_real_module_entry_point_migrates_a_copy(tmp_path):
    folder = tmp_path / CANARY_PATH
    path = write_manifest(folder)
    original = disk_bytes(path)

    completed = subprocess.run(
        [sys.executable, "-m", "runevidence", "migrate", str(folder), "--to", "0.2", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert_result(completed.stdout, **{"from": "0.1", "to": "0.2", "changed": True,
                                       "dry_run": False, "backup": "created"})
    assert str(folder) not in completed.stdout
    assert json.loads(disk_bytes(path))["schema_version"] == "0.2"
    assert disk_bytes(folder / f"{MANIFEST}.bak") == original


def test_migrate_preserves_directory_artifacts(tmp_path):
    folder = tmp_path / "exp"
    doc = rich_doc()
    doc["artifacts"] = [
        {
            "id": "data-dir",
            "path": "data",
            "artifact_type": "directory",
            "digest_format": "runevidence-directory-v1",
        }
    ]
    path = write_manifest(folder, raw=json.dumps(doc, indent=2) + "\n")

    code, out, err = run_cli(["migrate", str(folder), "--to", "0.2"])

    assert (code, err) == (0, "")
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["schema_version"] == "0.2"
    assert after["artifacts"][0]["artifact_type"] == "directory"
    assert after["artifacts"][0]["digest_format"] == "runevidence-directory-v1"
