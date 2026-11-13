"""Migration core (Stage 3d-2b): decode, preservation, backup, stale checks.

These tests exercise ``runevidence.migrate`` directly. The CLI route, parser
and pins belong to a later slice, so nothing here invokes the command line.
"""

from __future__ import annotations

import errno
import io
import json
import math
import os
import socket
import stat
import subprocess
from pathlib import Path

import pytest

from runevidence import adapters as adapters_module
from runevidence import checks, hashing
from runevidence import manifest as manifest_module
from runevidence import migrate as migrate_module
from runevidence.adapters import generic
from runevidence.migrate import (
    MIGRATION_ERROR_CODES,
    MigrationError,
    MigrationResult,
    decode_snapshot,
    migrate_manifest,
    strict_equal,
    without_schema_version,
)
from runevidence.models import MANIFEST_FILENAME, backup_generation

MANIFEST = MANIFEST_FILENAME
OMIT = object()


# --------------------------------------------------------------------------
# Fixtures and helpers
# --------------------------------------------------------------------------


def _rich_doc(*, version=OMIT) -> dict:
    """A schema-valid document exercising order, types, presence and numbers."""
    doc: dict = {}
    if version is not OMIT:
        doc["schema_version"] = version
    doc["project"] = {
        "id": "proj-1",
        "name": "Caf\u00e9 \u2713 \u6f22\u5b57",
        "tags": ["z", "a", "m"],
        "metadata": {
            "zeta": 1,
            "alpha": {"k": [3, 2, 1], "n": None, "empty": {}, "b": False},
        },
    }
    doc["runs"] = [
        {
            "id": "run-b",
            "name": "Run B",
            "params": {
                "lr": -0.0,
                "epochs": 0,
                "flag": False,
                "empty": "",
                "nil": None,
                "seq": [2, 1, 3],
                "nested": {"z": "last", "a": "first"},
            },
            "metadata": {"beta": [True, False], "alpha": {"y": 1, "x": 1.0}},
        },
        {"id": "run-a", "name": "Run A", "params": {}},
    ]
    doc["artifacts"] = [
        {
            "id": "art-1",
            "path": "data/file.bin",
            "sha256": "0" * 64,
            "size_bytes": 0,
            "metadata": {"note": "", "zero": 0, "none": None},
        }
    ]
    doc["results"] = [
        {"id": "res-nan", "run_id": "run-b", "name": "nan", "value": float("nan")},
        {"id": "res-inf", "run_id": "run-b", "name": "inf", "value": float("inf")},
        {
            "id": "res-ninf",
            "run_id": "run-b",
            "name": "ninf",
            "value": float("-inf"),
        },
        {"id": "res-negzero", "run_id": "run-b", "name": "negzero", "value": -0.0},
        {"id": "res-int", "run_id": "run-b", "name": "int", "value": 1},
        {"id": "res-float", "run_id": "run-b", "name": "float", "value": 1.0},
        {"id": "res-bool", "run_id": "run-b", "name": "bool", "value": True},
        {"id": "res-null", "run_id": "run-b", "name": "null", "value": None},
        {"id": "res-empty", "run_id": "run-b", "name": "empty", "value": ""},
    ]
    doc["evidence"] = [
        {
            "id": "ev-1",
            "claim": "A claim",
            "supports": ["res-int", "art-1"],
            "contradicts": [],
            "metadata": {"z": [1, 2], "a": {"k": "v"}},
        }
    ]
    doc["relationships"] = [
        {"source": "art-1", "target": "run-b", "type": "input_to"}
    ]
    return doc


def _write_doc(
    folder: Path,
    doc: dict | None = None,
    *,
    raw: str | None = None,
    name: str = MANIFEST,
    mode: int | None = None,
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    if raw is None:
        raw = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    path.write_text(raw, encoding="utf-8", newline="\n")
    if mode is not None and _modes_supported():
        path.chmod(mode)
    return path


def _disk_bytes(path: Path) -> bytes:
    """Read bytes without going through a possibly monkeypatched Path method."""
    with open(path, "rb") as fh:
        return fh.read()


def _folder_names(folder: Path) -> list[str]:
    # Use scandir so an os.listdir trap on the migration path cannot obscure
    # the post-condition assertions themselves.
    with os.scandir(folder) as entries:
        return [entry.name for entry in entries]


def _temp_names(folder: Path) -> list[str]:
    return sorted(
        name
        for name in _folder_names(folder)
        if name.startswith(f".{MANIFEST}.") and name.endswith(".tmp")
    )


def _names(folder: Path) -> list[str]:
    return sorted(_folder_names(folder))


def _containers(value: object) -> dict[int, object]:
    found: dict[int, object] = {}
    stack = [value]
    while stack:
        node = stack.pop()
        node_type = type(node)
        if node_type is dict:
            found[id(node)] = node
            stack.extend(node.values())
        elif node_type is list:
            found[id(node)] = node
            stack.extend(node)
    return found


def _shared_containers(left: object, right: object) -> set[int]:
    return set(_containers(left)) & set(_containers(right))


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symbolic links unavailable: {exc.__class__.__name__}")


def _hardlink_or_skip(link: Path, target: Path) -> None:
    try:
        os.link(target, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"hard links unavailable: {exc.__class__.__name__}")


def _modes_supported() -> bool:
    return os.name != "nt"


@pytest.fixture
def posix_modes(tmp_path):
    if os.name == "nt":
        pytest.skip("POSIX permission bits are not effective on Windows")
    probe = tmp_path / "mode-probe"
    probe.write_bytes(b"x")
    probe.chmod(0o600)
    try:
        observed = stat.S_IMODE(probe.stat().st_mode)
    except OSError as exc:  # pragma: no cover - unusual filesystem
        pytest.skip(f"cannot observe POSIX modes: {exc.__class__.__name__}")
    if observed != 0o600:
        pytest.skip("filesystem does not preserve POSIX permission bits")
    return True


def _expect_code(code: str, call) -> MigrationError:
    with pytest.raises(MigrationError) as excinfo:
        call()
    assert excinfo.value.code == code
    assert excinfo.value.code in MIGRATION_ERROR_CODES
    assert str(excinfo.value) == "migration failed"
    return excinfo.value


# --------------------------------------------------------------------------
# Happy paths, already-0.2 no-op, dry run and idempotence
# --------------------------------------------------------------------------


@pytest.mark.parametrize("version", [OMIT, "0.1"])
def test_valid_0_1_migrates_to_0_2_with_backup(tmp_path, version):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc(version=version))
    original = _disk_bytes(path)
    expected_backup = path.resolve().with_name(f"{MANIFEST}.bak")

    result = migrate_manifest(folder)

    assert result == MigrationResult(
        from_version="0.1",
        to_version="0.2",
        changed=True,
        dry_run=False,
        backup="created",
        backup_path=expected_backup,
    )
    emitted = json.loads(path.read_text(encoding="utf-8"))
    assert list(emitted)[0] == "schema_version"
    assert emitted["schema_version"] == "0.2"
    assert _disk_bytes(expected_backup) == original
    assert expected_backup.is_file()
    assert _names(folder) == [MANIFEST, f"{MANIFEST}.bak"]


def test_migration_preserves_loader_decoded_values_types_and_order(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    original = _disk_bytes(path)
    baseline = decode_snapshot(original)

    migrate_manifest(path)

    after = decode_snapshot(_disk_bytes(path))
    assert list(after)[0] == "schema_version"
    assert after["schema_version"] == "0.2"
    assert strict_equal(without_schema_version(baseline), without_schema_version(after))

    run = after["runs"][0]
    assert list(run["params"]) == [
        "lr", "epochs", "flag", "empty", "nil", "seq", "nested",
    ]
    assert run["params"]["nil"] is None
    assert run["params"]["empty"] == ""
    assert run["params"]["flag"] is False
    assert type(run["params"]["flag"]) is bool
    assert run["params"]["epochs"] == 0
    assert type(run["params"]["epochs"]) is int
    assert type(after["results"][3]["value"]) is float
    assert math.copysign(1.0, after["results"][3]["value"]) == -1.0
    assert math.isnan(after["results"][0]["value"])
    assert math.isinf(after["results"][1]["value"])
    assert after["results"][1]["value"] > 0
    assert math.isinf(after["results"][2]["value"])
    assert after["results"][2]["value"] < 0
    assert after["results"][4]["value"] == 1
    assert type(after["results"][4]["value"]) is int
    assert after["results"][5]["value"] == 1.0
    assert type(after["results"][5]["value"]) is float
    assert after["results"][6]["value"] is True
    assert after["results"][7]["value"] is None
    assert after["project"]["name"] == "Caf\u00e9 \u2713 \u6f22\u5b57"
    assert after["evidence"][0]["metadata"]["z"] == [1, 2]


def test_already_0_2_is_a_noop_without_any_writer_helper(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc(version="0.2"))
    before = _disk_bytes(path)
    before_mtime = path.stat().st_mtime_ns
    calls: list[str] = []

    def deny(name):
        def fail(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} must not be called")

        return fail

    monkeypatch.setattr(migrate_module, "dump_raw", deny("dump_raw"))
    monkeypatch.setattr(migrate_module, "save_raw", deny("save_raw"))
    monkeypatch.setattr(
        migrate_module, "_backup_for_migration", deny("_backup_for_migration")
    )
    monkeypatch.setattr(migrate_module.os, "listdir", deny("os.listdir"))

    result = migrate_manifest(folder)

    assert result == MigrationResult("0.2", "0.2", False, False, "none", None)
    assert calls == []
    assert _disk_bytes(path) == before
    assert path.stat().st_mtime_ns == before_mtime
    assert _names(folder) == [MANIFEST]


@pytest.mark.parametrize("dry_run", [True, False])
def test_already_0_2_dry_run_is_a_noop(tmp_path, monkeypatch, dry_run):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc(version="0.2"))
    before = _disk_bytes(path)
    before_mtime = path.stat().st_mtime_ns

    monkeypatch.setattr(
        migrate_module,
        "save_raw",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("save_raw called")),
    )
    monkeypatch.setattr(
        migrate_module,
        "_backup_for_migration",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("_backup_for_migration called")
        ),
    )

    result = migrate_manifest(folder, dry_run=dry_run)

    assert result.changed is False
    assert result.dry_run is dry_run
    assert result.backup == "none"
    assert result.backup_path is None
    assert _disk_bytes(path) == before
    assert path.stat().st_mtime_ns == before_mtime


def test_dry_run_reports_change_and_writes_nothing(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    before = _disk_bytes(path)
    before_mtime = path.stat().st_mtime_ns
    calls: list[str] = []

    def deny(name):
        def fail(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} must not be called")

        return fail

    monkeypatch.setattr(migrate_module, "save_raw", deny("save_raw"))
    monkeypatch.setattr(
        migrate_module, "_backup_for_migration", deny("_backup_for_migration")
    )
    monkeypatch.setattr(migrate_module.os, "listdir", deny("os.listdir"))

    result = migrate_manifest(folder, dry_run=True)

    assert result == MigrationResult("0.1", "0.2", True, True, "none", None)
    assert calls == []
    assert _disk_bytes(path) == before
    assert path.stat().st_mtime_ns == before_mtime
    assert _names(folder) == [MANIFEST]
    assert _temp_names(folder) == []


def test_load_manifest_is_called_exactly_once(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    real_load = migrate_module.load_manifest
    calls: list[Path] = []

    def counting_load(target):
        calls.append(Path(target))
        return real_load(target)

    monkeypatch.setattr(migrate_module, "load_manifest", counting_load)

    migrate_manifest(path)

    assert len(calls) == 1


def test_numeric_loader_normalizations_are_preserved(tmp_path):
    folder = tmp_path / "exp"
    raw = (
        '{"project": {"id": "p", "name": "P", "metadata": {'
        '"big": 1.7976931348623157e308, "tiny": 1e-400, "int_zero": -0, '
        '"neg_zero": -0.0, "exp": 1e2, "one": 1, "one_float": 1.0}}, '
        '"results": [{"id": "r", "run_id": "p", "name": "n", "value": -0.0}]}\n'
    )
    path = _write_doc(folder, raw=raw)
    baseline = decode_snapshot(_disk_bytes(path))

    migrate_manifest(path)

    after = decode_snapshot(_disk_bytes(path))
    assert strict_equal(without_schema_version(baseline), without_schema_version(after))
    metadata = after["project"]["metadata"]
    assert list(metadata) == [
        "big", "tiny", "int_zero", "neg_zero", "exp", "one", "one_float",
    ]
    assert type(metadata["big"]) is float and metadata["big"] > 1e308
    assert metadata["tiny"] == 0.0 and type(metadata["tiny"]) is float
    assert metadata["int_zero"] == 0 and type(metadata["int_zero"]) is int
    assert metadata["neg_zero"] == 0.0
    assert math.copysign(1.0, metadata["neg_zero"]) == -1.0
    assert metadata["exp"] == 100.0 and type(metadata["exp"]) is float
    assert metadata["one"] == 1 and type(metadata["one"]) is int
    assert metadata["one_float"] == 1.0 and type(metadata["one_float"]) is float
    assert math.copysign(1.0, after["results"][0]["value"]) == -1.0


def test_migration_prints_nothing_on_success_or_failure(tmp_path, capsys, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())

    migrate_manifest(path)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

    failing = _write_doc(tmp_path / "failing", _rich_doc())
    monkeypatch.setattr(
        migrate_module,
        "save_raw",
        lambda *a, **k: (_ for _ in ()).throw(OSError(errno.EIO, "silent")),
    )
    _expect_code("WRITE_FAILED", lambda: migrate_manifest(failing))
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_result_is_frozen_and_error_codes_are_closed():
    result = MigrationResult("0.1", "0.2", True, False, "created", None)
    with pytest.raises(Exception) as excinfo:
        result.changed = False
    assert excinfo.type.__name__ == "FrozenInstanceError"

    assert set(MIGRATION_ERROR_CODES) == {
        "MANIFEST_LOAD_FAILED",
        "SERIALIZATION_FAILED",
        "READ_FAILED",
        "STALE_MANIFEST",
        "WRITE_FAILED",
        "RESERVED_PATH",
        "LOCATOR_MIGRATION_UNSUPPORTED",
        "DOWNGRADE_UNSUPPORTED",
        "INTERNAL_ERROR",
    }
    with pytest.raises(ValueError):
        MigrationError("NOT_A_CODE")


def test_second_migration_is_idempotent(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())

    first = migrate_manifest(folder)
    assert first.changed is True
    migrated = _disk_bytes(path)
    migrated_mtime = path.stat().st_mtime_ns

    second = migrate_manifest(folder)

    assert second == MigrationResult("0.2", "0.2", False, False, "none", None)
    assert _disk_bytes(path) == migrated
    assert path.stat().st_mtime_ns == migrated_mtime
    assert _names(folder) == [MANIFEST, f"{MANIFEST}.bak"]


# --------------------------------------------------------------------------
# decode_snapshot and strict_equal
# --------------------------------------------------------------------------


def test_decode_snapshot_returns_fresh_independent_graphs():
    data = (json.dumps(_rich_doc(), ensure_ascii=False) + "\n").encode("utf-8")

    first = decode_snapshot(data)
    second = decode_snapshot(data)

    assert type(first) is dict
    assert first is not second
    assert strict_equal(first, second)
    assert _shared_containers(first, second) == set()

    second["project"]["metadata"]["alpha"]["k"].append(99)
    assert first["project"]["metadata"]["alpha"]["k"] == [3, 2, 1]


def test_decode_snapshot_uses_the_loader_json_hooks():
    with pytest.raises(ValueError):
        decode_snapshot(b'{"a": 1, "a": 2}')
    with pytest.raises(ValueError):
        decode_snapshot(b'{"x": NaN}')
    with pytest.raises(ValueError):
        decode_snapshot(b'{"x": Infinity}')
    with pytest.raises(ValueError):
        decode_snapshot(b'{"x": 1e9999}')

    allowed = decode_snapshot(
        b'{"results": [{"id": "r", "run_id": "x", "name": "n", "value": -Infinity}]}'
    )
    assert math.isinf(allowed["results"][0]["value"])
    assert allowed["results"][0]["value"] < 0


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ({"a": 1, "b": 2}, {"a": 1, "b": 2}, True),
        ({"a": 1, "b": 2}, {"b": 2, "a": 1}, False),
        ([1, 2, 3], [1, 2, 3], True),
        ([1, 2, 3], [3, 2, 1], False),
        ([1, 2], [1, 2, 3], False),
        (True, 1, False),
        (False, 0, False),
        (1, 1.0, False),
        (0, 0.0, False),
        (1, 1, True),
        ("1", 1, False),
        (None, None, True),
        (None, False, False),
        (float("nan"), float("nan"), True),
        (float("nan"), float("inf"), False),
        (float("inf"), float("inf"), True),
        (float("inf"), float("-inf"), False),
        (-0.0, 0.0, False),
        (-0.0, -0.0, True),
        (0.0, 0.0, True),
        (1.5, 1.5, True),
        ({"x": [1, {"y": None}]}, {"x": [1, {"y": None}]}, True),
        ({"x": [1, {"y": None}]}, {"x": [1, {"y": 0}]}, False),
        ((), (), False),
        ({"a": 1}, {"a": 1, "b": 2}, False),
    ],
)
def test_strict_equal_contract(left, right, expected):
    assert strict_equal(left, right) is expected
    assert strict_equal(right, left) is expected


def test_strict_equal_is_iterative_for_deep_values():
    left: object = 0
    right: object = 0
    for _ in range(3000):
        left = {"n": left}
        right = {"n": right}

    assert strict_equal(left, right) is True
    assert strict_equal(left, {"n": right}) is False


def test_without_schema_version_returns_ordered_copy_without_mutating():
    original = {"schema_version": "0.1", "z": 1, "a": {"k": 2}}
    result = without_schema_version(original)

    assert list(result) == ["z", "a"]
    assert result is not original
    assert original == {"schema_version": "0.1", "z": 1, "a": {"k": 2}}
    assert result["a"] is original["a"]

    absent = {"z": 1, "a": 2}
    copy = without_schema_version(absent)
    assert copy == absent
    assert copy is not absent


def test_strict_equal_negative_controls_for_preservation():
    baseline = decode_snapshot(
        (json.dumps(_rich_doc(), ensure_ascii=False) + "\n").encode("utf-8")
    )

    import copy

    for mutate in (
        lambda doc: doc["project"]["metadata"]["alpha"]["k"].append(4),
        lambda doc: doc["runs"][0]["params"].__setitem__("epochs", 0.0),
        lambda doc: doc["runs"][0]["params"].__setitem__("flag", 1),
        lambda doc: doc["results"][0].__setitem__("value", float("inf")),
        lambda doc: doc["results"][3].__setitem__("value", 0.0),
        lambda doc: doc["runs"][0]["params"].__setitem__("nil", ""),
        lambda doc: doc["runs"][0]["params"].__setitem__("empty", None),
        lambda doc: doc["runs"][0]["params"]["seq"].reverse(),
        lambda doc: doc["runs"][0].__setitem__(
            "params", dict(reversed(list(doc["runs"][0]["params"].items())))
        ),
    ):
        mutated = copy.deepcopy(baseline)
        mutate(mutated)
        assert strict_equal(baseline, mutated) is False


def test_migration_builds_the_candidate_from_an_independent_decode(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    decodes: list[dict] = []
    candidates: list[dict] = []
    real_decode = migrate_module.decode_snapshot
    real_dump = migrate_module.dump_raw

    def recording_decode(data):
        value = real_decode(data)
        decodes.append(value)
        return value

    def recording_dump(data):
        candidates.append(data)
        return real_dump(data)

    monkeypatch.setattr(migrate_module, "decode_snapshot", recording_decode)
    monkeypatch.setattr(migrate_module, "dump_raw", recording_dump)

    migrate_manifest(path)

    source_a = decodes[0]
    source_b = decodes[1]
    candidate = candidates[0]
    assert source_a is not source_b
    assert _shared_containers(source_a, source_b) == set()
    assert _shared_containers(source_a, candidate) == set()
    # The candidate is allowed to reference source_b's containers.
    assert _shared_containers(source_b, candidate)
    # Negative control: the structural check notices aliasing.
    aliased = {"project": source_a["project"]}
    assert _shared_containers(source_a, aliased)


# --------------------------------------------------------------------------
# Serialization eligibility
# --------------------------------------------------------------------------

LONE_SURROGATE_RAW = (
    '{"project": {"id": "proj-1", "name": "bad \\ud800 value"}, '
    '"runs": []}\n'
)


@pytest.mark.parametrize("dry_run", [True, False])
def test_lone_surrogate_is_serialization_failed_without_writes(
    tmp_path, monkeypatch, dry_run
):
    folder = tmp_path / "exp"
    path = _write_doc(folder, raw=LONE_SURROGATE_RAW)
    before = _disk_bytes(path)
    listdir_calls: list[str] = []

    def deny_listdir(*args, **kwargs):
        listdir_calls.append("called")
        raise AssertionError("backup enumeration must not run")

    monkeypatch.setattr(migrate_module.os, "listdir", deny_listdir)
    monkeypatch.setattr(
        migrate_module,
        "save_raw",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("save_raw called")),
    )

    error = _expect_code(
        "SERIALIZATION_FAILED", lambda: migrate_manifest(path, dry_run=dry_run)
    )

    assert "d800" not in str(error)
    assert str(path) not in str(error)
    assert "surrogate" not in str(error)
    assert _disk_bytes(path) == before
    assert _names(folder) == [MANIFEST]
    assert _temp_names(folder) == []
    assert listdir_calls == []


@pytest.mark.parametrize("dry_run", [True, False])
def test_dump_recursion_is_serialization_failed(tmp_path, monkeypatch, dry_run):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    before = _disk_bytes(path)

    def boom(value):
        raise RecursionError("simulated deep metadata")

    monkeypatch.setattr(migrate_module, "dump_raw", boom)

    _expect_code(
        "SERIALIZATION_FAILED", lambda: migrate_manifest(path, dry_run=dry_run)
    )

    assert _disk_bytes(path) == before
    assert _names(folder) == [MANIFEST]
    assert _temp_names(folder) == []


@pytest.mark.parametrize("dry_run", [True, False])
def test_emitted_decode_recursion_is_serialization_failed(
    tmp_path, monkeypatch, dry_run
):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    before = _disk_bytes(path)
    real_decode = migrate_module.decode_snapshot
    calls = {"count": 0}

    def flaky_decode(data):
        calls["count"] += 1
        if calls["count"] == 3:
            raise RecursionError("simulated emitted decode recursion")
        return real_decode(data)

    monkeypatch.setattr(migrate_module, "decode_snapshot", flaky_decode)

    _expect_code(
        "SERIALIZATION_FAILED", lambda: migrate_manifest(path, dry_run=dry_run)
    )

    assert calls["count"] == 3
    assert _disk_bytes(path) == before
    assert _names(folder) == [MANIFEST]


def test_serialization_error_does_not_leak_exception_text(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())

    def boom(value):
        raise RecursionError("SECRET-VALUE-" + str(path))

    monkeypatch.setattr(migrate_module, "dump_raw", boom)

    error = _expect_code("SERIALIZATION_FAILED", lambda: migrate_manifest(path))
    assert "SECRET" not in str(error)
    assert str(path) not in str(error)


# --------------------------------------------------------------------------
# Reserved paths and backup generation namespace
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", [f"{MANIFEST}.bak", f"{MANIFEST}.bak.2", f"{MANIFEST}.bak.10"])
def test_backup_named_operand_is_reserved_before_load(tmp_path, monkeypatch, name):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc(), name=name)
    before = _disk_bytes(path)

    def deny_load(*args, **kwargs):
        raise AssertionError("load_manifest must not run for a reserved path")

    monkeypatch.setattr(migrate_module, "load_manifest", deny_load)

    _expect_code("RESERVED_PATH", lambda: migrate_manifest(path))

    assert _disk_bytes(path) == before
    assert _names(folder) == [name]


def test_symlink_operand_resolving_into_backup_namespace_is_reserved(
    tmp_path, monkeypatch
):
    folder = tmp_path / "exp"
    backup_named = _write_doc(folder, _rich_doc(), name=f"{MANIFEST}.bak")
    before = _disk_bytes(backup_named)
    _symlink_or_skip(folder / MANIFEST, backup_named)

    monkeypatch.setattr(
        migrate_module,
        "load_manifest",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("load_manifest must not run")
        ),
    )

    _expect_code("RESERVED_PATH", lambda: migrate_manifest(folder))

    assert _disk_bytes(backup_named) == before
    assert os.path.islink(folder / MANIFEST)


def test_backup_named_symlink_to_manifest_is_allowed_but_not_reused(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    original = _disk_bytes(path)
    backup_link = folder / f"{MANIFEST}.bak"
    _symlink_or_skip(backup_link, path)

    # The reserved check sees the resolved basename, so a backup-named
    # symlink pointing at the manifest is allowed as an operand.
    result = migrate_manifest(backup_link)

    assert result.backup == "created"
    assert result.backup_path == path.resolve().with_name(f"{MANIFEST}.bak.2")
    assert os.path.islink(backup_link)
    assert os.readlink(backup_link) == str(path)
    assert _disk_bytes(folder / f"{MANIFEST}.bak.2") == original
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == "0.2"


def test_hardlink_alias_of_target_is_not_reused_as_backup(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    original = _disk_bytes(path)
    alias = folder / f"{MANIFEST}.bak"
    _hardlink_or_skip(alias, path)
    original_inode = os.stat(alias).st_ino

    result = migrate_manifest(folder)

    assert result.backup == "created"
    assert result.backup_path == path.resolve().with_name(f"{MANIFEST}.bak.2")
    assert os.stat(alias).st_ino == original_inode
    assert _disk_bytes(alias) == original
    assert _disk_bytes(folder / f"{MANIFEST}.bak.2") == original


def test_byte_identical_permission_subset_generation_is_reused(tmp_path, posix_modes):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc(), mode=0o644)
    original = _disk_bytes(path)
    backup = folder / f"{MANIFEST}.bak"
    backup.write_bytes(original)
    backup.chmod(0o600)

    result = migrate_manifest(folder)

    assert result.backup == "reused"
    assert result.backup_path == backup
    assert not (folder / f"{MANIFEST}.bak.2").exists()
    assert _disk_bytes(backup) == original
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600


def test_different_bytes_generation_is_not_reused(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    original = _disk_bytes(path)
    backup = folder / f"{MANIFEST}.bak"
    backup.write_bytes(b"prior generation\n")

    result = migrate_manifest(folder)

    assert result.backup == "created"
    assert result.backup_path == path.resolve().with_name(f"{MANIFEST}.bak.2")
    assert _disk_bytes(backup) == b"prior generation\n"
    assert _disk_bytes(folder / f"{MANIFEST}.bak.2") == original


def test_more_permissive_identical_generation_is_left_untouched(
    tmp_path, posix_modes
):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc(), mode=0o600)
    original = _disk_bytes(path)
    backup = folder / f"{MANIFEST}.bak"
    backup.write_bytes(original)
    backup.chmod(0o644)

    result = migrate_manifest(folder)

    assert result.backup == "created"
    assert result.backup_path == path.resolve().with_name(f"{MANIFEST}.bak.2")
    assert stat.S_IMODE(backup.stat().st_mode) == 0o644
    assert _disk_bytes(backup) == original
    created = folder / f"{MANIFEST}.bak.2"
    assert _disk_bytes(created) == original
    assert (stat.S_IMODE(created.stat().st_mode) & ~0o600) == 0


def test_symlink_with_equal_bytes_is_not_reused(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    original = _disk_bytes(path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_bytes(original)
    _symlink_or_skip(folder / f"{MANIFEST}.bak", elsewhere)

    result = migrate_manifest(folder)

    assert result.backup == "created"
    assert result.backup_path == path.resolve().with_name(f"{MANIFEST}.bak.2")
    assert os.path.islink(folder / f"{MANIFEST}.bak")
    assert _disk_bytes(elsewhere) == original
    assert _disk_bytes(folder / f"{MANIFEST}.bak.2") == original


# --------------------------------------------------------------------------
# Backup creation permissions, collision and partial-write failures
# --------------------------------------------------------------------------


def test_backup_keeps_restrictive_source_mode(tmp_path, posix_modes):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc(), mode=0o600)
    old_umask = os.umask(0o022)
    try:
        result = migrate_manifest(folder)
    finally:
        os.umask(old_umask)

    assert result.backup == "created"
    assert stat.S_IMODE(result.backup_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_backup_under_restrictive_umask_is_not_wider_than_source(
    tmp_path, posix_modes
):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc(), mode=0o640)
    old_umask = os.umask(0o077)
    try:
        result = migrate_manifest(folder)
    finally:
        os.umask(old_umask)

    assert result.backup == "created"
    created_mode = stat.S_IMODE(result.backup_path.stat().st_mode)
    assert created_mode == 0o600
    assert (created_mode & ~0o640) == 0


def test_read_only_source_gets_read_only_backup(tmp_path, posix_modes):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc(), mode=0o444)
    result = migrate_manifest(folder)

    assert result.backup == "created"
    assert stat.S_IMODE(result.backup_path.stat().st_mode) == 0o444
    assert stat.S_IMODE(path.stat().st_mode) == 0o444


def test_permission_violation_after_create_is_write_failed_and_removed(
    tmp_path, monkeypatch, posix_modes
):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc(), mode=0o600)
    before = _disk_bytes(path)

    def wide_mode(fd):
        return 0o602

    monkeypatch.setattr(migrate_module, "_file_mode", wide_mode)

    _expect_code("WRITE_FAILED", lambda: migrate_manifest(path))

    assert _disk_bytes(path) == before
    assert not (folder / f"{MANIFEST}.bak").exists()
    assert _names(folder) == [MANIFEST]


def test_fsync_failure_is_write_failed_and_removes_incomplete_file(
    tmp_path, monkeypatch
):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    before = _disk_bytes(path)

    def broken_fsync(fd):
        raise OSError(errno.EIO, "simulated fsync failure")

    monkeypatch.setattr(migrate_module.os, "fsync", broken_fsync)

    _expect_code("WRITE_FAILED", lambda: migrate_manifest(path))

    assert _disk_bytes(path) == before
    assert _names(folder) == [MANIFEST]


def test_backup_create_permission_denial_is_write_failed(
    tmp_path, monkeypatch, posix_modes
):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    before = _disk_bytes(path)
    real_open = os.open
    backup_name = f"{MANIFEST}.bak"

    def denied_open(pathname, flags, mode=0o777, *, dir_fd=None):
        if Path(os.fspath(pathname)).name == backup_name:
            raise OSError(errno.EACCES, "simulated denial")
        return real_open(pathname, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(migrate_module.os, "open", denied_open)

    _expect_code("WRITE_FAILED", lambda: migrate_manifest(path))

    assert _disk_bytes(path) == before
    assert not (folder / backup_name).exists()
    assert _names(folder) == [MANIFEST]


def test_concurrent_backup_name_collision_is_write_failed_and_intact(
    tmp_path, monkeypatch, posix_modes
):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    before = _disk_bytes(path)
    competitor = folder / f"{MANIFEST}.bak"
    competitor.write_bytes(b"competitor bytes\n")
    competitor.chmod(0o600)
    real_listdir = os.listdir

    def hide_competitor(folder_name):
        if Path(folder_name) == folder:
            return [
                name
                for name in real_listdir(folder_name)
                if name != f"{MANIFEST}.bak"
            ]
        return real_listdir(folder_name)

    monkeypatch.setattr(migrate_module.os, "listdir", hide_competitor)

    _expect_code("WRITE_FAILED", lambda: migrate_manifest(path))

    assert _disk_bytes(path) == before
    assert _disk_bytes(competitor) == b"competitor bytes\n"
    assert stat.S_IMODE(competitor.stat().st_mode) == 0o600


def test_partial_backup_write_removes_only_the_new_incomplete_file(
    tmp_path, monkeypatch
):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    before = _disk_bytes(path)
    prior = folder / f"{MANIFEST}.bak"
    prior.write_bytes(b"prior generation\n")
    real_write = os.write
    calls = {"count": 0}

    def partial_write(fd, data):
        calls["count"] += 1
        if calls["count"] == 1:
            return real_write(fd, bytes(data)[: max(1, len(data) // 2)])
        raise OSError(errno.EIO, "simulated partial write")

    monkeypatch.setattr(migrate_module.os, "write", partial_write)

    _expect_code("WRITE_FAILED", lambda: migrate_manifest(path))

    assert calls["count"] == 2
    assert not (folder / f"{MANIFEST}.bak.2").exists()
    assert _disk_bytes(prior) == b"prior generation\n"
    assert _disk_bytes(path) == before


def test_backup_readback_mismatch_removes_only_the_new_file(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    before = _disk_bytes(path)
    prior = folder / f"{MANIFEST}.bak"
    prior.write_bytes(b"prior generation\n")
    real_read = os.read
    calls = {"count": 0}

    def broken_read(fd, size):
        calls["count"] += 1
        if calls["count"] == 1:
            return b"not the source bytes"
        return b""

    monkeypatch.setattr(migrate_module.os, "read", broken_read)

    _expect_code("WRITE_FAILED", lambda: migrate_manifest(path))

    assert calls["count"] == 2
    assert not (folder / f"{MANIFEST}.bak.2").exists()
    assert _disk_bytes(prior) == b"prior generation\n"
    assert _disk_bytes(path) == before


# --------------------------------------------------------------------------
# Stale checkpoints and late non-committed failure classification
# --------------------------------------------------------------------------


def test_pre_backup_stale_observation_creates_no_backup(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    external = b'{"project": {"id": "external", "name": "changed"}}\n'
    real_read_bytes = Path.read_bytes
    calls = {"count": 0}

    def racing_read(self):
        if self == path:
            calls["count"] += 1
            if calls["count"] == 2:  # load, then the pre-backup read
                with open(self, "wb") as fh:
                    fh.write(external)
                return external
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", racing_read)

    _expect_code("STALE_MANIFEST", lambda: migrate_manifest(path))

    assert calls["count"] == 2
    assert _disk_bytes(path) == external
    assert _names(folder) == [MANIFEST]
    assert _temp_names(folder) == []


def test_dry_run_performs_the_pre_backup_stale_check(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    external = b'{"project": {"id": "external", "name": "changed"}}\n'
    real_read_bytes = Path.read_bytes
    calls = {"count": 0}
    listdir_calls: list[str] = []

    def racing_read(self):
        if self == path:
            calls["count"] += 1
            if calls["count"] == 2:
                with open(self, "wb") as fh:
                    fh.write(external)
                return external
        return real_read_bytes(self)

    def deny_listdir(*args, **kwargs):
        listdir_calls.append("called")
        raise AssertionError("dry run must not enumerate backups")

    monkeypatch.setattr(Path, "read_bytes", racing_read)
    monkeypatch.setattr(migrate_module.os, "listdir", deny_listdir)

    _expect_code("STALE_MANIFEST", lambda: migrate_manifest(path, dry_run=True))

    assert calls["count"] == 2
    assert _disk_bytes(path) == external
    assert _names(folder) == [MANIFEST]
    assert listdir_calls == []


def test_pre_backup_read_failure_is_read_failed_without_backup(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    before = _disk_bytes(path)
    real_read_bytes = Path.read_bytes
    calls = {"count": 0}

    def failing_read(self):
        if self == path:
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError(errno.EACCES, "simulated denial")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", failing_read)

    _expect_code("READ_FAILED", lambda: migrate_manifest(path))

    assert calls["count"] == 2
    assert _disk_bytes(path) == before
    assert _names(folder) == [MANIFEST]


def test_late_stale_after_real_save_raw_keeps_completed_backup(
    tmp_path, monkeypatch
):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    original = _disk_bytes(path)
    external = b'{"project": {"id": "external", "name": "changed"}}\n'
    real_save_raw = migrate_module.save_raw

    def racing_save(data, target, *, expected_bytes):
        with open(target, "wb") as fh:
            fh.write(external)
        return real_save_raw(data, target, expected_bytes=expected_bytes)

    monkeypatch.setattr(migrate_module, "save_raw", racing_save)

    _expect_code("STALE_MANIFEST", lambda: migrate_manifest(path))

    assert _disk_bytes(path) == external
    backup = folder / f"{MANIFEST}.bak"
    assert _disk_bytes(backup) == original
    assert _temp_names(folder) == []


def test_late_replacement_failure_followed_by_change_is_stale(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    original = _disk_bytes(path)
    external = b'{"project": {"id": "external", "name": "changed"}}\n'

    def failing_save(data, target, *, expected_bytes):
        with open(target, "wb") as fh:
            fh.write(external)
        raise OSError(errno.EIO, "simulated replacement failure")

    monkeypatch.setattr(migrate_module, "save_raw", failing_save)

    _expect_code("STALE_MANIFEST", lambda: migrate_manifest(path))

    assert _disk_bytes(path) == external
    assert _disk_bytes(folder / f"{MANIFEST}.bak") == original


def test_late_unchanged_observation_is_write_failed(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    original = _disk_bytes(path)

    def failing_save(data, target, *, expected_bytes):
        raise OSError(errno.EIO, "SECRET path " + str(target))

    monkeypatch.setattr(migrate_module, "save_raw", failing_save)

    error = _expect_code("WRITE_FAILED", lambda: migrate_manifest(path))

    assert "SECRET" not in str(error)
    assert str(path) not in str(error)
    assert _disk_bytes(path) == original
    assert _disk_bytes(folder / f"{MANIFEST}.bak") == original


def test_late_diagnostic_read_failure_is_write_failed(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    original = _disk_bytes(path)
    real_read_bytes = Path.read_bytes
    calls = {"count": 0}

    def failing_diagnostic(self):
        if self == path:
            calls["count"] += 1
            if calls["count"] == 3:  # load, pre-backup, diagnostic
                raise OSError(errno.EIO, "simulated diagnostic failure")
        return real_read_bytes(self)

    def failing_save(data, target, *, expected_bytes):
        raise OSError(errno.EIO, "simulated save failure")

    monkeypatch.setattr(migrate_module, "save_raw", failing_save)
    monkeypatch.setattr(Path, "read_bytes", failing_diagnostic)

    _expect_code("WRITE_FAILED", lambda: migrate_manifest(path))

    assert calls["count"] == 3
    assert _disk_bytes(path) == original
    assert _disk_bytes(folder / f"{MANIFEST}.bak") == original


def test_late_failure_leaves_existing_generations_untouched(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    original = _disk_bytes(path)
    prior = folder / f"{MANIFEST}.bak"
    prior.write_bytes(b"prior generation\n")

    def failing_save(data, target, *, expected_bytes):
        raise OSError(errno.EIO, "simulated save failure")

    monkeypatch.setattr(migrate_module, "save_raw", failing_save)

    _expect_code("WRITE_FAILED", lambda: migrate_manifest(path))

    assert _disk_bytes(prior) == b"prior generation\n"
    assert _disk_bytes(folder / f"{MANIFEST}.bak.2") == original
    assert _disk_bytes(path) == original
    assert _temp_names(folder) == []


# --------------------------------------------------------------------------
# No-I/O and filesystem allowlist
# --------------------------------------------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.ops: list[tuple[str, str]] = []

    def add(self, kind: str, path) -> None:
        try:
            raw = os.fspath(path)
        except TypeError:
            raw = str(path)
        self.ops.append((kind, str(raw)))

    def paths(self, *kinds: str) -> list[str]:
        return [path for kind, path in self.ops if not kinds or kind in kinds]

    def saw(self, path: Path) -> bool:
        needle = str(path)
        return any(recorded == needle for _, recorded in self.ops)

    def count(self, kind: str, path: Path) -> int:
        needle = str(path)
        return sum(1 for record_kind, recorded in self.ops if record_kind == kind and recorded == needle)

    def disallowed(self, folder: Path, manifest: Path) -> list[tuple[str, str]]:
        """Path operations outside the manifest/backup/staging allowlist."""
        bad: list[tuple[str, str]] = []
        for kind, recorded in self.ops:
            if kind not in _PATH_KINDS:
                continue
            path = Path(recorded)
            if _allowed_migration_target(path, folder, manifest):
                continue
            if kind in ("stat", "lstat") and _is_ancestor(path, folder):
                continue
            bad.append((kind, recorded))
        return bad


_PATH_KINDS = frozenset(
    {
        "stat",
        "lstat",
        "listdir",
        "os.open",
        "os.replace.src",
        "os.replace.dst",
        "read_bytes",
        "io.open",
        "builtins.open",
        "scandir",
    }
)


def _is_ancestor(path: Path, folder: Path) -> bool:
    try:
        folder.relative_to(path)
    except ValueError:
        return False
    return True


def _allowed_migration_target(path: Path, folder: Path, manifest: Path) -> bool:
    if path == folder or path == manifest:
        return True
    if path.parent == folder:
        if path.name == manifest.name or backup_generation(path.name) is not None:
            return True
        if path.name.startswith(f".{manifest.name}.") and path.name.endswith(".tmp"):
            return True
    return False


def _install_no_io_traps(monkeypatch, recorder: _Recorder, folder: Path, manifest: Path):
    """Record filesystem access and trap every forbidden capability."""
    real_stat = os.stat
    real_lstat = os.lstat
    real_listdir = os.listdir
    real_os_open = os.open
    real_os_close = os.close
    real_read = os.read
    real_write = os.write
    real_fsync = os.fsync
    real_replace = os.replace
    real_read_bytes = Path.read_bytes
    real_io_open = io.open
    real_builtin_open = open

    def guard(kind, path):
        # Record first; the assertions after the call decide what was allowed.
        # Ancestor stats during path resolution are permitted; a probe for an
        # artifact is recorded and therefore detectable.
        recorder.add(kind, path)

    def stat(path, *args, **kwargs):
        guard("stat", path)
        return real_stat(path, *args, **kwargs)

    def lstat(path, *args, **kwargs):
        guard("lstat", path)
        return real_lstat(path, *args, **kwargs)

    def listdir(path="."):
        recorder.add("listdir", path)
        if Path(os.fspath(path)) != folder:
            raise AssertionError("directory enumeration outside the target folder")
        return real_listdir(path)

    def os_open(path, flags, mode=0o777, *, dir_fd=None):
        guard("os.open", path)
        return real_os_open(path, flags, mode, dir_fd=dir_fd)

    def os_close(fd):
        recorder.add("os.close", str(fd))
        return real_os_close(fd)

    def os_read(fd, size):
        recorder.add("os.read", str(fd))
        return real_read(fd, size)

    def os_write(fd, data):
        recorder.add("os.write", str(fd))
        return real_write(fd, data)

    def os_fsync(fd):
        recorder.add("os.fsync", str(fd))
        return real_fsync(fd)

    def os_replace(src, dst):
        guard("os.replace.src", src)
        guard("os.replace.dst", dst)
        return real_replace(src, dst)

    def read_bytes(self):
        recorder.add("read_bytes", self)
        return real_read_bytes(self)

    def io_open(file, *args, **kwargs):
        guard("io.open", file)
        return real_io_open(file, *args, **kwargs)

    def builtin_open(file, *args, **kwargs):
        guard("builtins.open", file)
        return real_builtin_open(file, *args, **kwargs)

    def scandir(*args, **kwargs):
        recorder.add("scandir", args[0] if args else ".")
        raise AssertionError("forbidden directory scan")

    monkeypatch.setattr(os, "stat", stat)
    monkeypatch.setattr(os, "lstat", lstat)
    monkeypatch.setattr(os, "listdir", listdir)
    monkeypatch.setattr(os, "open", os_open)
    monkeypatch.setattr(os, "close", os_close)
    monkeypatch.setattr(os, "read", os_read)
    monkeypatch.setattr(os, "write", os_write)
    monkeypatch.setattr(os, "fsync", os_fsync)
    monkeypatch.setattr(os, "replace", os_replace)
    monkeypatch.setattr(os, "scandir", scandir)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(io, "open", io_open)
    monkeypatch.setattr("builtins.open", builtin_open)

    def boom(*args, **kwargs):
        raise AssertionError("forbidden capability used")

    monkeypatch.setattr(checks, "run_checks", boom)
    monkeypatch.setattr(hashing, "sha256_file", boom)
    monkeypatch.setattr(adapters_module, "available_adapters", boom)
    monkeypatch.setattr(generic.GenericAdapter, "discover", boom)
    monkeypatch.setattr(generic.GenericAdapter, "extract", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(os, "getenv", boom)


def test_success_uses_only_the_manifest_backup_and_staging_allowlist(
    tmp_path, monkeypatch
):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    manifest = path.resolve()
    artifact = folder / "data" / "file.bin"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"artifact bytes")
    artifact_resolved = artifact.resolve()

    recorder = _Recorder()
    _install_no_io_traps(monkeypatch, recorder, folder.resolve(), manifest)
    try:
        result = migrate_manifest(folder)

        assert result.changed is True
        assert result.backup == "created"

        # Exactly one backup enumeration of the target folder.
        assert recorder.count("listdir", folder.resolve()) == 1
        # Load read + pre-backup read + save_raw's final check. No diagnostic.
        assert recorder.count("read_bytes", manifest) == 3
        assert recorder.paths("scandir") == []
        assert recorder.disallowed(folder.resolve(), manifest) == []
        assert not recorder.saw(artifact)
        assert not recorder.saw(artifact_resolved)

        # Negative control: the allowlist catches an artifact access.
        artifact.read_bytes()
        artifact.stat()
        assert recorder.saw(artifact)
        assert recorder.disallowed(folder.resolve(), manifest)
    finally:
        monkeypatch.undo()

    assert _disk_bytes(folder / f"{MANIFEST}.bak") is not None
    assert json.loads(_disk_bytes(path))["schema_version"] == "0.2"


def test_late_failure_adds_only_the_diagnostic_read(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    manifest = path.resolve()
    artifact = folder / "data" / "file.bin"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"artifact bytes")
    recorder = _Recorder()

    def failing_save(data, target, *, expected_bytes):
        raise OSError(errno.EIO, "simulated save failure")

    _install_no_io_traps(monkeypatch, recorder, folder.resolve(), manifest)
    monkeypatch.setattr(migrate_module, "save_raw", failing_save)
    try:
        _expect_code("WRITE_FAILED", lambda: migrate_manifest(path))

        # Load + pre-backup + the one conditional diagnostic read.
        assert recorder.count("read_bytes", manifest) == 3
        assert recorder.count("listdir", folder.resolve()) == 1
        assert recorder.disallowed(folder.resolve(), manifest) == []
        assert not recorder.saw(artifact)
    finally:
        monkeypatch.undo()

    assert _disk_bytes(folder / f"{MANIFEST}.bak") is not None


# --------------------------------------------------------------------------
# Reviewer hardening: cleanup-only success, proof regressions, load failures
# --------------------------------------------------------------------------


def test_committed_replace_with_cleanup_only_failure_is_success(
    tmp_path, monkeypatch
):
    """A committed replacement wins even when temp cleanup then fails.

    ``manifest.save_raw`` builds its staged temp with the module ``Path``
    binding, so replacing that binding instruments the temp cleanup only.
    """
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    original = _disk_bytes(path)
    unlinks: list[Path] = []
    diagnostics: list[Path] = []

    class StagedPath(type(Path())):
        def unlink(self, missing_ok: bool = False) -> None:
            unlinks.append(self)
            raise OSError(errno.EIO, "simulated cleanup failure")

    monkeypatch.setattr(manifest_module, "Path", StagedPath)
    monkeypatch.setattr(
        migrate_module, "_read_diagnostic", lambda target: diagnostics.append(target)
    )

    result = migrate_manifest(path)

    assert result == MigrationResult(
        "0.1",
        "0.2",
        True,
        False,
        "created",
        path.resolve().with_name(f"{MANIFEST}.bak"),
    )
    assert json.loads(_disk_bytes(path))["schema_version"] == "0.2"
    assert _disk_bytes(path.resolve().with_name(f"{MANIFEST}.bak")) == original
    assert len(unlinks) == 1 and not unlinks[0].exists()
    assert diagnostics == []


def _mutation(kind: str, candidate: dict) -> dict:
    if kind == "nested-value":
        candidate["project"]["metadata"]["zeta"] = 999
    elif kind == "int-to-float":
        assert candidate["runs"][0]["params"]["epochs"] == 0
        candidate["runs"][0]["params"]["epochs"] = 0.0
    elif kind == "reorder-keys":
        params = candidate["runs"][0]["params"]
        candidate["runs"][0]["params"] = dict(reversed(list(params.items())))
    else:  # pragma: no cover - parameter guard
        raise AssertionError(kind)
    return candidate


@pytest.mark.parametrize("kind", ["nested-value", "int-to-float", "reorder-keys"])
def test_candidate_proof_regression_is_internal_error_without_write(
    tmp_path, monkeypatch, kind
):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    original = _disk_bytes(path)
    original_mtime = path.stat().st_mtime_ns
    real_candidate = migrate_module._candidate_from
    save_calls: list[Path] = []

    def broken_candidate(source):
        return _mutation(kind, real_candidate(source))

    def deny_save(*args, **kwargs):
        save_calls.append(Path(args[1]))
        raise AssertionError("save_raw must not run for a failed proof")

    monkeypatch.setattr(migrate_module, "_candidate_from", broken_candidate)
    monkeypatch.setattr(migrate_module, "save_raw", deny_save)

    error = _expect_code("INTERNAL_ERROR", lambda: migrate_manifest(path))

    assert str(path) not in str(error)
    assert save_calls == []
    assert _disk_bytes(path) == original
    assert path.stat().st_mtime_ns == original_mtime
    assert _names(folder) == [MANIFEST]
    assert _temp_names(folder) == []


LOAD_FAILURE_PAYLOADS = {
    "bad-utf8": b'{"project": {"id": "p", "name": "\xff\xfe"}}\n',
    "bad-json": b'{"project": {"id": "p", "name": "P"}\n',
    "duplicate-keys": (
        b'{"project": {"id": "p", "name": "P"}, '
        b'"project": {"id": "p", "name": "P"}}\n'
    ),
    "nonfinite-outside-results": (
        b'{"project": {"id": "p", "name": "P"}, '
        b'"runs": [{"id": "r", "params": {"x": NaN}}]}\n'
    ),
}


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "directory-without-manifest",
        "bad-utf8",
        "bad-json",
        "duplicate-keys",
        "nonfinite-outside-results",
        "symlink-loop",
    ],
)
def test_load_failures_are_manifest_load_failed_without_writes(tmp_path, kind):
    folder = tmp_path / "exp"
    if kind == "missing":
        before: list[str] = []
    elif kind == "directory-without-manifest":
        folder.mkdir()
        (folder / "notes.txt").write_bytes(b"synthetic notes\n")
        before = _names(folder)
    elif kind == "symlink-loop":
        folder.mkdir()
        _symlink_or_skip(folder / MANIFEST, Path(MANIFEST))
        before = _names(folder)
    else:
        folder.mkdir()
        (folder / MANIFEST).write_bytes(LOAD_FAILURE_PAYLOADS[kind])
        before = _names(folder)

    error = _expect_code("MANIFEST_LOAD_FAILED", lambda: migrate_manifest(folder))

    assert str(folder) not in str(error)
    assert str(folder / MANIFEST) not in str(error)
    if folder.exists():
        assert _names(folder) == before
        assert _temp_names(folder) == []
    else:
        assert before == []


def test_serialization_failure_uses_only_the_load_read(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    manifest = path.resolve()
    original = _disk_bytes(path)
    artifact = folder / "data" / "file.bin"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"artifact bytes")

    def boom(value):
        raise RecursionError("simulated deep metadata")

    recorder = _Recorder()
    _install_no_io_traps(monkeypatch, recorder, folder.resolve(), manifest)
    monkeypatch.setattr(migrate_module, "dump_raw", boom)
    try:
        _expect_code("SERIALIZATION_FAILED", lambda: migrate_manifest(path))

        assert recorder.count("read_bytes", manifest) == 1
        assert recorder.count("listdir", folder.resolve()) == 0
        assert recorder.disallowed(folder.resolve(), manifest) == []
        assert not recorder.saw(artifact)
    finally:
        monkeypatch.undo()

    assert _disk_bytes(path) == original
    assert _names(folder) == ["data", MANIFEST]


def test_read_failure_adds_only_the_failing_pre_backup_read(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    manifest = path.resolve()
    original = _disk_bytes(path)
    artifact = folder / "data" / "file.bin"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"artifact bytes")
    recorder = _Recorder()
    _install_no_io_traps(monkeypatch, recorder, folder.resolve(), manifest)
    trapped_read = Path.read_bytes
    calls = {"count": 0}

    def failing_pre_backup(self):
        if Path(self) == manifest:
            calls["count"] += 1
            if calls["count"] == 2:
                recorder.add("read_bytes", self)
                raise OSError(errno.EACCES, "simulated denial")
        return trapped_read(self)

    monkeypatch.setattr(Path, "read_bytes", failing_pre_backup)
    try:
        _expect_code("READ_FAILED", lambda: migrate_manifest(path))

        assert calls["count"] == 2
        assert recorder.count("read_bytes", manifest) == 2
        assert recorder.count("listdir", folder.resolve()) == 0
        assert recorder.disallowed(folder.resolve(), manifest) == []
        assert not recorder.saw(artifact)
    finally:
        monkeypatch.undo()

    assert _disk_bytes(path) == original
    assert _names(folder) == ["data", MANIFEST]


def test_backup_write_failure_stays_inside_the_allowlist(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _rich_doc())
    manifest = path.resolve()
    original = _disk_bytes(path)
    artifact = folder / "data" / "file.bin"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"artifact bytes")
    recorder = _Recorder()
    _install_no_io_traps(monkeypatch, recorder, folder.resolve(), manifest)
    trapped_open = os.open
    backup_name = f"{MANIFEST}.bak"

    def deny_backup(pathname, flags, mode=0o777, *, dir_fd=None):
        if Path(os.fspath(pathname)).name == backup_name:
            recorder.add("os.open", pathname)
            raise OSError(errno.EACCES, "simulated denial")
        return trapped_open(pathname, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(migrate_module.os, "open", deny_backup)
    try:
        _expect_code("WRITE_FAILED", lambda: migrate_manifest(path))

        assert recorder.count("listdir", folder.resolve()) == 1
        assert recorder.disallowed(folder.resolve(), manifest) == []
        assert not recorder.saw(artifact)
    finally:
        monkeypatch.undo()

    assert _disk_bytes(path) == original
    assert not (folder / backup_name).exists()
    assert _names(folder) == ["data", MANIFEST]
