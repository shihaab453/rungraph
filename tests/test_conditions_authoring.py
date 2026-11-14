"""Conditions authoring core (Stage 3d-3, slice 3d3-1).

These tests exercise ``runevidence.conditions_authoring`` directly. The CLI
route, parser, pins and documentation belong to later slices, so nothing here
invokes the command line.

The fixture style follows ``tests/test_migrate_core.py``: real temporary
manifests, real writes where the write path is under test, and monkeypatched
module helpers for failure injection. Every test is deterministic and uses
``tmp_path``.
"""

from __future__ import annotations

import builtins
import errno
import io
import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from runevidence import conditions_authoring as conditions_module
from runevidence.checks.conditions import check_conditions
from runevidence.conditions_authoring import (
    CONDITIONS_ERROR_CODES,
    MAX_ABS_FLOAT,
    MAX_CONTAINER_WIDTH,
    MAX_ENVIRONMENT_BYTES,
    MAX_ENVIRONMENT_DEPTH,
    MAX_NODES,
    MAX_PATCH_BYTES,
    MAX_PATCH_DEPTH,
    MAX_SAFE_INT,
    MAX_STRING_LENGTH,
    ConditionsError,
    ConditionsResult,
    author_conditions,
)
from runevidence.migrate import MigrationError
from runevidence.manifest import load_manifest
from runevidence.models import MANIFEST_FILENAME

MANIFEST = MANIFEST_FILENAME
OMIT = object()


# --------------------------------------------------------------------------
# Fixtures and helpers
# --------------------------------------------------------------------------


def _base_doc(
    *,
    version="0.2",
    conditions=OMIT,
    run_id="r1",
    runs=None,
    artifacts=None,
    relationships=None,
    extra_run=None,
) -> dict:
    doc: dict = {}
    if version is not OMIT:
        doc["schema_version"] = version
    doc["project"] = {"id": "proj-1", "name": "Project"}
    if runs is None:
        run = {"id": run_id, "name": "Run"}
        if extra_run:
            run.update(extra_run)
        if conditions is not OMIT:
            run["conditions"] = conditions
        runs = [run]
    doc["runs"] = runs
    doc["artifacts"] = [] if artifacts is None else artifacts
    doc["results"] = []
    doc["evidence"] = []
    doc["relationships"] = [] if relationships is None else relationships
    return doc


def _artifact(artifact_id: str, path: str, kind=None, *, directory=False) -> dict:
    entry: dict = {"id": artifact_id, "path": path, "kind": kind}
    if directory:
        entry["artifact_type"] = "directory"
        entry["digest_format"] = "runevidence-directory-v1"
    return entry


def _edge(source: str, target: str, kind: str = "input_to") -> dict:
    return {"source": source, "target": target, "type": kind}


def _write_doc(
    folder: Path,
    doc: dict | None = None,
    *,
    raw: str | bytes | None = None,
    name: str = MANIFEST,
    mode: int | None = None,
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    if raw is not None:
        data = raw.encode("utf-8") if isinstance(raw, str) else raw
        path.write_bytes(data)
    else:
        text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
    if mode is not None and os.name != "nt":
        path.chmod(mode)
    return path


def _write_patch(folder: Path, data: bytes | str, name: str = "patch.json") -> str:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    if isinstance(data, str):
        path.write_text(data, encoding="utf-8", newline="\n")
    else:
        path.write_bytes(data)
    return str(path)


def _bytes(path: Path) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _names(folder: Path) -> list[str]:
    return sorted(entry.name for entry in folder.iterdir())


def _temp_names(folder: Path) -> list[str]:
    return sorted(
        name
        for name in _names(folder)
        if name.startswith(f".{MANIFEST}.") and name.endswith(".tmp")
    )


def _is_staged_temp(path: Path) -> bool:
    return path.name.startswith(f".{MANIFEST}.") and path.name.endswith(".tmp")


def _author(
    path: Path,
    *,
    patch: str | None = None,
    clears=(),
    dry_run: bool = False,
    stdin=None,
    run_id: str = "r1",
) -> ConditionsResult:
    return author_conditions(
        path,
        run_id=run_id,
        patch_path=patch,
        clears=clears,
        dry_run=dry_run,
        stdin=stdin,
    )


def _expect(code: str, call) -> ConditionsError:
    with pytest.raises(ConditionsError) as excinfo:
        call()
    assert excinfo.value.code == code
    assert excinfo.value.code in CONDITIONS_ERROR_CODES
    assert str(excinfo.value) == "conditions update failed"
    return excinfo.value


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


def _env_size(value: object) -> int:
    text = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return len(text.encode("ascii"))


def _env_exact_size(target: int, *, max_string_length: int = MAX_STRING_LENGTH) -> dict:
    for length in range(0, max_string_length + 1):
        env = {"a": ["x" * max_string_length] * 15 + ["x" * length]}
        if _env_size(env) == target:
            return env
    raise AssertionError(f"no environment fixture of exactly {target} bytes")


def _env_of_nodes(total: int) -> dict:
    # env root (1) + outer list (1) + 128 inner lists (128) + scalars.
    scalars = total - 130
    assert scalars >= 0
    base, remainder = divmod(scalars, 128)
    counts = [base + 1] * remainder + [base] * (128 - remainder)
    env = {"a": [[0] * count for count in counts]}
    assert 1 + 1 + 128 + scalars == total
    return env


def _nested_environment(levels: int) -> dict:
    root: dict = {}
    current = root
    for _ in range(levels - 1):
        child: dict = {}
        current["a"] = child
        current = child
    current["a"] = "leaf"
    return root


def _padded_patch(total: int) -> bytes:
    body = b'{"source_revision":"rev"}'
    assert total >= len(body)
    return body + b" " * (total - len(body))


class _ReadTrap:
    """A binary stream that records and refuses unexpected reads."""

    def __init__(self, payload: bytes = b"") -> None:
        self.payload = payload
        self.reads: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.reads.append(size)
        return self.payload


class _NeverRead(_ReadTrap):
    def read(self, size: int = -1) -> bytes:  # pragma: no cover - must not run
        self.reads.append(size)
        raise AssertionError("the patch stream must not be read")


# --------------------------------------------------------------------------
# Patch parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"   \n\t",
        b"[]",
        b'["evaluation_split"]',
        b'"split"',
        b"1",
        b"1.5",
        b"true",
        b"null",
    ],
)
def test_top_level_must_be_an_object(tmp_path, raw):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    patch = _write_patch(folder, raw)
    _expect("PATCH_INVALID", lambda: _author(path, patch=patch))
    assert _names(path.parent) == ["patch.json", MANIFEST]


def test_bom_invalid_utf8_and_trailing_data_are_invalid(tmp_path):
    path = _write_doc(tmp_path / "exp", _base_doc())
    cases = [
        b"\xef\xbb\xbf{}",
        b'{"source_revision": "\xff"}',
        b'{"source_revision": "a"} {"source_revision": "b"}',
        b'{} trailing',
        b'{"source_revision": "a"}\x00',
    ]
    for index, raw in enumerate(cases):
        patch = _write_patch(tmp_path, raw, name=f"patch-{index}.json")
        _expect("PATCH_INVALID", lambda p=patch: _author(path, patch=p))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"source_revision": "a", "source_revision": "b"}',
        b'{"evaluation_protocol": {"protocol_id": "p", "protocol_id": "q"}}',
        b'{"environment": {"a": 1, "a": 2}}',
        b'{"environment": {"a": {"b": 1, "b": 2}}}',
    ],
)
def test_duplicate_keys_are_rejected_at_every_depth(tmp_path, raw):
    path = _write_doc(tmp_path / "exp", _base_doc())
    patch = _write_patch(tmp_path, raw)
    _expect("PATCH_INVALID", lambda: _author(path, patch=patch))


@pytest.mark.parametrize(
    "key",
    [
        "conditions",
        "run",
        "schema_version",
        "dataset_id",
        "metric_definition",
        "relationships",
        "unknown",
    ],
)
def test_unknown_top_level_keys_are_rejected(tmp_path, key):
    path = _write_doc(tmp_path / "exp", _base_doc())
    patch = _write_patch(tmp_path, json.dumps({key: 1}))
    _expect("PATCH_INVALID", lambda: _author(path, patch=patch))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"environment": {"n": NaN}}',
        b'{"environment": {"n": Infinity}}',
        b'{"environment": {"n": -Infinity}}',
        b'{"environment": {"n": 1e400}}',
        b'{"environment": {"n": -1e400}}',
    ],
)
def test_nonfinite_patch_numbers_are_invalid(tmp_path, raw):
    path = _write_doc(tmp_path / "exp", _base_doc())
    patch = _write_patch(tmp_path, raw)
    _expect("PATCH_INVALID", lambda: _author(path, patch=patch))


@pytest.mark.parametrize("size", [MAX_PATCH_BYTES, MAX_PATCH_BYTES + 1])
def test_patch_file_size_bound(tmp_path, size):
    path = _write_doc(tmp_path / "exp", _base_doc())
    patch = _write_patch(tmp_path, _padded_patch(size))
    if size == MAX_PATCH_BYTES:
        result = _author(path, patch=patch)
        assert result.changed is True
        assert _json(path)["runs"][0]["conditions"] == {
            "source_revision": "rev"
        }
    else:
        _expect("PATCH_INVALID", lambda: _author(path, patch=patch))
        assert _json(path)["runs"][0].get("conditions") is None


@pytest.mark.parametrize("size", [MAX_PATCH_BYTES, MAX_PATCH_BYTES + 1])
def test_patch_stdin_size_bound_reads_at_most_bound(tmp_path, size):
    path = _write_doc(tmp_path / "exp", _base_doc())
    stream = _ReadTrap(_padded_patch(size))
    if size == MAX_PATCH_BYTES:
        result = _author(path, patch="-", stdin=stream)
        assert result.changed is True
    else:
        _expect("PATCH_INVALID", lambda: _author(path, patch="-", stdin=stream))
    assert stream.reads == [MAX_PATCH_BYTES + 1]


def test_deep_nesting_is_patch_invalid_not_internal(tmp_path):
    path = _write_doc(tmp_path / "exp", _base_doc())
    raw = b'{"environment":' + b"[" * 20000 + b"0" + b"]" * 20000 + b"}"
    assert len(raw) < MAX_PATCH_BYTES
    patch = _write_patch(tmp_path, raw)
    _expect("PATCH_INVALID", lambda: _author(path, patch=patch))


def test_integer_parse_limit_is_patch_invalid(tmp_path):
    path = _write_doc(tmp_path / "exp", _base_doc())
    # 5000 digits exceed CPython's default 4300-digit conversion limit on
    # 3.10.7+/3.12; if a runtime disables the limit, the magnitude check still
    # refuses it. 4300 digits parse but exceed MAX_SAFE_INT at the bound.
    for digits in (5000, 4300):
        raw = b'{"environment": {"n": ' + b"9" * digits + b"}}"
        patch = _write_patch(tmp_path, raw, name=f"patch-{digits}.json")
        _expect("PATCH_INVALID", lambda p=patch: _author(path, patch=p))


def test_dash_dot_slash_is_a_file_and_dash_is_stdin(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = _write_doc(tmp_path / "exp", _base_doc())
    file_patch = tmp_path / "-"
    file_patch.write_bytes(b'{"source_revision": "from-file"}')
    stream = _ReadTrap(b'{"source_revision": "from-stdin"}')

    result = _author(path, patch="./-", stdin=stream)
    assert result.changed is True
    assert stream.reads == []
    assert _json(path)["runs"][0]["conditions"]["source_revision"] == "from-file"

    result = _author(path, patch="-", stdin=stream)
    assert result.changed is True
    assert stream.reads == [MAX_PATCH_BYTES + 1]
    assert _json(path)["runs"][0]["conditions"]["source_revision"] == "from-stdin"


def test_clears_only_reads_no_patch_and_no_stdin(tmp_path):
    path = _write_doc(
        tmp_path / "exp",
        _base_doc(conditions={"source_revision": "abc"}),
    )
    trap = _NeverRead()
    result = _author(
        path, patch=None, clears=["source_revision"], stdin=trap
    )
    assert result.changed is True
    assert trap.reads == []
    assert "conditions" not in _json(path)["runs"][0]


def test_patch_is_read_only_after_version_and_run_selection(tmp_path, monkeypatch):
    def deny(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("patch must not be read")

    monkeypatch.setattr(conditions_module, "_read_patch", deny)

    old = _write_doc(tmp_path / "old", _base_doc(version="0.1"))
    _expect(
        "UNSUPPORTED_VERSION",
        lambda: _author(old, patch=str(tmp_path / "missing-patch.json")),
    )

    current = _write_doc(tmp_path / "current", _base_doc())
    _expect(
        "RUN_SELECTION_INVALID",
        lambda: _author(current, patch="-", run_id="absent"),
    )


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_patch_read_failures_are_read_failed(tmp_path, kind):
    path = _write_doc(tmp_path / "exp", _base_doc())
    if kind == "missing":
        patch = str(tmp_path / "does-not-exist.json")
    else:
        folder = tmp_path / "patch-dir"
        folder.mkdir()
        patch = str(folder)
    _expect("READ_FAILED", lambda: _author(path, patch=patch))


def test_patch_read_error_from_stdin_is_read_failed(tmp_path):
    path = _write_doc(tmp_path / "exp", _base_doc())

    class Broken:
        def read(self, size=-1):
            raise OSError(errno.EIO, "boom")

    _expect("READ_FAILED", lambda: _author(path, patch="-", stdin=Broken()))


def test_closed_stdin_is_read_failed(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    before = _bytes(path)
    monkeypatch.setattr(sys, "stdin", None)

    _expect("READ_FAILED", lambda: _author(path, patch="-"))

    assert _bytes(path) == before
    assert _names(folder) == [MANIFEST]


def test_closed_injected_stdin_is_read_failed(tmp_path):
    path = _write_doc(tmp_path / "exp", _base_doc())
    stream = io.BytesIO(b"{}")
    stream.close()
    _expect("READ_FAILED", lambda: _author(path, patch="-", stdin=stream))


def test_text_stdin_is_read_failed(tmp_path, monkeypatch):
    path = _write_doc(tmp_path / "exp", _base_doc())
    _expect(
        "READ_FAILED",
        lambda: _author(path, patch="-", stdin=io.StringIO("{}")),
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    _expect("READ_FAILED", lambda: _author(path, patch="-"))


def test_binary_stdin_stream_still_works(tmp_path):
    path = _write_doc(tmp_path / "exp", _base_doc())
    stream = io.BytesIO(b'{"source_revision": "rev"}')
    result = _author(path, patch="-", stdin=stream)
    assert result.changed is True
    assert _json(path)["runs"][0]["conditions"] == {"source_revision": "rev"}


# --------------------------------------------------------------------------
# Set, clear and null matrix
# --------------------------------------------------------------------------


def test_set_creates_conditions_and_persists_the_value(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())

    result = _author(path, patch=_write_patch(folder, '{"evaluation_split": "test"}'))

    assert result == ConditionsResult(
        run_id="r1",
        changed=True,
        dry_run=False,
        backup="created",
        backup_path=path.resolve().with_name(f"{MANIFEST}.bak"),
        set_fields=("evaluation_split",),
        cleared_fields=(),
    )
    run = _json(path)["runs"][0]
    assert run["conditions"] == {"evaluation_split": "test"}


@pytest.mark.parametrize(
    "field",
    [
        "evaluation_split",
        "evaluation_protocol",
        "source_revision",
        "environment",
    ],
)
def test_null_is_an_explicit_unknown_for_nullable_fields(tmp_path, field):
    path = _write_doc(tmp_path / "exp", _base_doc())
    patch = _write_patch(tmp_path, json.dumps({field: None}))
    result = _author(path, patch=patch)
    assert result.changed is True
    assert result.set_fields == (field,)
    assert field in _json(path)["runs"][0]["conditions"]
    assert _json(path)["runs"][0]["conditions"][field] is None


def test_null_where_absent_is_a_change(tmp_path):
    path = _write_doc(tmp_path / "exp", _base_doc())
    patch = _write_patch(tmp_path, '{"source_revision": null}')
    result = _author(path, patch=patch)
    assert result.changed is True
    assert _json(path)["runs"][0]["conditions"] == {"source_revision": None}


@pytest.mark.parametrize(
    "field", ["evaluation_dataset_artifact_id", "model_checkpoint_artifact_id"]
)
def test_selector_null_is_patch_invalid(tmp_path, field):
    path = _write_doc(tmp_path / "exp", _base_doc())
    before = _bytes(path)
    patch = _write_patch(tmp_path, json.dumps({field: None}))
    _expect("PATCH_INVALID", lambda: _author(path, patch=patch))
    assert _bytes(path) == before


@pytest.mark.parametrize(
    "value",
    [123, True, ["d1"], {"id": "d1"}, "", " d1", "-d1", "d1 d2", "\u00e9"],
)
def test_selector_wrong_type_or_pattern_is_patch_invalid(tmp_path, value):
    path = _write_doc(tmp_path / "exp", _base_doc())
    before = _bytes(path)
    patch = _write_patch(
        tmp_path, json.dumps({"evaluation_dataset_artifact_id": value})
    )
    _expect("PATCH_INVALID", lambda: _author(path, patch=patch))
    assert _bytes(path) == before


@pytest.mark.parametrize(
    "value",
    ["", "   ", "\t\n"],
)
@pytest.mark.parametrize("field", ["evaluation_split", "source_revision"])
def test_blank_condition_strings_are_invalid(tmp_path, field, value):
    path = _write_doc(tmp_path / "exp", _base_doc())
    patch = _write_patch(tmp_path, json.dumps({field: value}))
    _expect("PATCH_INVALID", lambda: _author(path, patch=patch))


@pytest.mark.parametrize(
    "protocol",
    [
        {},
        {"version": 1},
        {"protocol_id": ""},
        {"protocol_id": "   "},
        {"protocol_id": "p", "version": None},
        {"protocol_id": "p", "version": True},
        {"protocol_id": "p", "version": 1.0},
        {"protocol_id": "p", "unknown": "x"},
    ],
)
def test_invalid_protocol_shapes_are_patch_invalid(tmp_path, protocol):
    path = _write_doc(tmp_path / "exp", _base_doc())
    patch = _write_patch(
        tmp_path, json.dumps({"evaluation_protocol": protocol})
    )
    _expect("PATCH_INVALID", lambda: _author(path, patch=patch))


@pytest.mark.parametrize(
    "protocol",
    [
        {"protocol_id": "p"},
        {"protocol_id": "p", "version": 1},
        {"protocol_id": "p", "version": "1"},
    ],
)
def test_valid_protocols_are_written(tmp_path, protocol):
    path = _write_doc(tmp_path / "exp", _base_doc())
    patch = _write_patch(
        tmp_path, json.dumps({"evaluation_protocol": protocol})
    )
    _author(path, patch=patch)
    assert _json(path)["runs"][0]["conditions"]["evaluation_protocol"] == protocol


@pytest.mark.parametrize(
    "environment",
    [
        {},
        [],
        "text",
        1,
        True,
    ],
)
def test_invalid_environment_shapes_are_refused(tmp_path, environment):
    path = _write_doc(tmp_path / "exp", _base_doc())
    patch = _write_patch(tmp_path, json.dumps({"environment": environment}))
    _expect("PATCH_INVALID", lambda: _author(path, patch=patch))


def test_nested_environment_null_member_is_an_ordinary_value(tmp_path):
    path = _write_doc(tmp_path / "exp", _base_doc())
    environment = {"python": "3.12", "gpu": None, "nested": {"b": [1, None]}}
    patch = _write_patch(tmp_path, json.dumps({"environment": environment}))
    _author(path, patch=patch)
    assert _json(path)["runs"][0]["conditions"]["environment"] == environment


def test_clear_removes_a_child_and_restores_fallback(tmp_path):
    path = _write_doc(
        tmp_path / "exp",
        _base_doc(
            conditions={
                "evaluation_split": "test",
                "source_revision": "abc",
                "environment": {"x": 1},
            }
        ),
    )
    result = _author(path, clears=["source_revision"])
    assert result.cleared_fields == ("source_revision",)
    conditions = _json(path)["runs"][0]["conditions"]
    assert conditions == {
        "evaluation_split": "test",
        "environment": {"x": 1},
    }


def test_clearing_the_last_child_removes_the_parent(tmp_path):
    path = _write_doc(
        tmp_path / "exp", _base_doc(conditions={"source_revision": "abc"})
    )
    result = _author(path, clears=["source_revision"])
    assert result.changed is True
    assert "conditions" not in _json(path)["runs"][0]


def test_empty_conditions_plus_clear_absent_removes_parent_and_changes(tmp_path):
    path = _write_doc(tmp_path / "exp", _base_doc(conditions={}))
    result = _author(path, clears=["evaluation_split"])
    assert result.changed is True
    assert "conditions" not in _json(path)["runs"][0]


def test_set_on_existing_empty_conditions_parent_keeps_parent_position(tmp_path):
    folder = tmp_path / "exp"
    run = {"id": "r1", "conditions": {}, "name": "Run"}
    path = _write_doc(folder, _base_doc(runs=[run]))
    patch = _write_patch(folder, '{"source_revision": "abc"}')
    result = _author(path, patch=patch)
    assert result.changed is True
    written = _json(path)["runs"][0]
    assert list(written) == ["id", "conditions", "name"]
    assert written["conditions"] == {"source_revision": "abc"}


def test_clear_absent_with_no_parent_is_a_noop(tmp_path):
    path = _write_doc(tmp_path / "exp", _base_doc())
    before = _bytes(path)
    result = _author(path, clears=["evaluation_split"])
    assert result.changed is False
    assert result.backup == "none"
    assert _bytes(path) == before


def test_clear_absent_with_other_children_is_a_noop(tmp_path):
    path = _write_doc(
        tmp_path / "exp",
        _base_doc(conditions={"source_revision": "abc"}),
    )
    before = _bytes(path)
    result = _author(path, clears=["evaluation_split"])
    assert result.changed is False
    assert _bytes(path) == before


def test_empty_patch_leaves_existing_empty_conditions_untouched(tmp_path):
    path = _write_doc(tmp_path / "exp", _base_doc(conditions={}))
    before = _bytes(path)
    before_mtime = path.stat().st_mtime_ns
    result = _author(path, patch=_write_patch(tmp_path, "{}"))
    assert result.changed is False
    assert result.set_fields == ()
    assert result.cleared_fields == ()
    assert _bytes(path) == before
    assert path.stat().st_mtime_ns == before_mtime


def test_same_value_set_is_a_noop(tmp_path):
    path = _write_doc(
        tmp_path / "exp",
        _base_doc(conditions={"evaluation_split": "test"}),
    )
    before = _bytes(path)
    patch = _write_patch(tmp_path, '{"evaluation_split": "test"}')
    result = _author(path, patch=patch)
    assert result.changed is False
    assert result.set_fields == ("evaluation_split",)
    assert _bytes(path) == before


def test_nonfixed_order_same_value_set_keeps_exact_order(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(
        folder,
        _base_doc(
            conditions={
                "environment": {"x": 1},
                "evaluation_split": "test",
                "source_revision": "abc",
            }
        ),
    )
    before = _bytes(path)
    patch = _write_patch(folder, '{"source_revision": "abc"}')
    result = _author(path, patch=patch)
    assert result.changed is False
    assert _bytes(path) == before


def test_same_value_and_changed_child_preserve_positions(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(
        folder,
        _base_doc(
            conditions={
                "environment": {"x": 1},
                "evaluation_split": "test",
                "source_revision": "abc",
            }
        ),
    )
    patch = _write_patch(
        folder,
        '{"source_revision": "abc", "evaluation_split": "validation"}',
    )
    result = _author(path, patch=patch)
    assert result.changed is True
    conditions = _json(path)["runs"][0]["conditions"]
    assert list(conditions) == [
        "environment",
        "evaluation_split",
        "source_revision",
    ]
    assert conditions["evaluation_split"] == "validation"
    assert conditions["environment"] == {"x": 1}
    assert conditions["source_revision"] == "abc"


def test_new_children_append_in_fixed_order(tmp_path):
    path = _write_doc(
        tmp_path / "exp",
        _base_doc(conditions={"source_revision": "abc"}),
    )
    patch = _write_patch(
        tmp_path,
        json.dumps(
            {
                "environment": {"x": 1},
                "evaluation_split": "test",
                "evaluation_protocol": {"protocol_id": "p"},
            }
        ),
    )
    result = _author(path, patch=patch)
    assert result.set_fields == (
        "evaluation_split",
        "evaluation_protocol",
        "environment",
    )
    assert list(_json(path)["runs"][0]["conditions"]) == [
        "source_revision",
        "evaluation_split",
        "evaluation_protocol",
        "environment",
    ]


def test_existing_conditions_parent_keeps_its_key_index(tmp_path):
    folder = tmp_path / "exp"
    run = {
        "id": "r1",
        "conditions": {"source_revision": "abc"},
        "name": "Run",
        "params": {"seed": 1},
    }
    path = _write_doc(folder, _base_doc(runs=[run]))
    patch = _write_patch(folder, '{"evaluation_split": "test"}')
    _author(path, patch=patch)
    written = _json(path)["runs"][0]
    assert list(written) == ["id", "conditions", "name", "params"]
    assert list(written["conditions"]) == [
        "source_revision",
        "evaluation_split",
    ]


def test_new_conditions_parent_is_the_last_run_key(tmp_path):
    folder = tmp_path / "exp"
    run = {"id": "r1", "name": "Run", "params": {"seed": 1}}
    path = _write_doc(folder, _base_doc(runs=[run]))
    patch = _write_patch(folder, '{"source_revision": "abc"}')
    _author(path, patch=patch)
    written = _json(path)["runs"][0]
    assert list(written) == ["id", "name", "params", "conditions"]


def test_set_and_clear_arrays_use_fixed_field_order(tmp_path):
    path = _write_doc(
        tmp_path / "exp",
        _base_doc(
            conditions={
                "environment": {"x": 1},
                "source_revision": "abc",
            }
        ),
    )
    patch = _write_patch(
        tmp_path,
        json.dumps(
            {
                "source_revision": "def",
                "evaluation_split": "test",
                "evaluation_protocol": {"protocol_id": "p"},
            }
        ),
    )
    result = _author(path, patch=patch, clears=["environment"])
    assert result.set_fields == (
        "evaluation_split",
        "evaluation_protocol",
        "source_revision",
    )
    assert result.cleared_fields == ("environment",)
    assert list(_json(path)["runs"][0]["conditions"]) == [
        "source_revision",
        "evaluation_split",
        "evaluation_protocol",
    ]


def test_multiple_clears_supplied_out_of_order_use_fixed_order(tmp_path):
    path = _write_doc(
        tmp_path / "exp",
        _base_doc(
            conditions={
                "evaluation_split": "test",
                "source_revision": "abc",
                "environment": {"x": 1},
                "evaluation_protocol": {"protocol_id": "p"},
            }
        ),
    )
    result = _author(
        path,
        clears=["environment", "source_revision", "evaluation_split"],
    )
    assert result.cleared_fields == (
        "evaluation_split",
        "source_revision",
        "environment",
    )
    assert _json(path)["runs"][0]["conditions"] == {
        "evaluation_protocol": {"protocol_id": "p"}
    }


def test_patch_and_clear_conflict_refuses_the_whole_patch(tmp_path):
    path = _write_doc(tmp_path / "exp", _base_doc())
    before = _bytes(path)
    patch = _write_patch(tmp_path, '{"evaluation_split": "test"}')
    _expect(
        "PATCH_INVALID",
        lambda: _author(path, patch=patch, clears=["evaluation_split"]),
    )
    assert _bytes(path) == before


def test_conditions_is_never_written_as_null(tmp_path):
    path = _write_doc(
        tmp_path / "exp", _base_doc(conditions={"source_revision": "abc"})
    )
    _author(path, clears=["source_revision"])
    assert "conditions" not in _json(path)["runs"][0]


def test_no_operation_at_all_is_a_caller_contract_error(tmp_path):
    path = _write_doc(tmp_path / "exp", _base_doc())
    _expect("INTERNAL_ERROR", lambda: _author(path))


# --------------------------------------------------------------------------
# Preservation-proof negative controls
# --------------------------------------------------------------------------


def test_preservation_rejects_shared_containers_directly():
    doc = _base_doc(conditions={"source_revision": "abc"})
    _expect(
        "INTERNAL_ERROR",
        lambda: conditions_module._prove_preservation(
            doc, doc, 0, {"evaluation_split": "test"}, ()
        ),
    )


def test_preservation_rejects_a_moved_parent(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    run = {
        "id": "r1",
        "conditions": {"source_revision": "abc"},
        "name": "Run",
    }
    path = _write_doc(folder, _base_doc(runs=[run]))
    before = _bytes(path)
    real_build = conditions_module._build_candidate

    def moving_build(source, run_index, run_id, patch, cleared):
        candidate = real_build(source, run_index, run_id, patch, cleared)
        target = candidate["runs"][run_index]
        conditions = target.pop("conditions")
        target["conditions"] = conditions
        return candidate

    monkeypatch.setattr(conditions_module, "_build_candidate", moving_build)
    patch = _write_patch(folder, '{"evaluation_split": "test"}')
    _expect("INTERNAL_ERROR", lambda: _author(path, patch=patch))
    assert _bytes(path) == before


def test_preservation_rejects_an_unrelated_mutation(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    before = _bytes(path)
    real_build = conditions_module._build_candidate

    def mutating_build(source, run_index, run_id, patch, cleared):
        candidate = real_build(source, run_index, run_id, patch, cleared)
        candidate["runs"][run_index]["name"] = "other"
        return candidate

    monkeypatch.setattr(conditions_module, "_build_candidate", mutating_build)
    patch = _write_patch(folder, '{"evaluation_split": "test"}')
    _expect("INTERNAL_ERROR", lambda: _author(path, patch=patch))
    assert _bytes(path) == before


# --------------------------------------------------------------------------
# Selector validation
# --------------------------------------------------------------------------


def _selector_doc(*, artifacts=None, relationships=None, conditions=OMIT) -> dict:
    return _base_doc(
        conditions=conditions,
        artifacts=artifacts
        if artifacts is not None
        else [_artifact("d1", "data/d1.csv", "dataset")],
        relationships=relationships
        if relationships is not None
        else [_edge("d1", "r1")],
    )


def test_valid_dataset_and_checkpoint_selectors_are_accepted(tmp_path):
    folder = tmp_path / "exp"
    doc = _selector_doc(
        artifacts=[
            _artifact("d1", "data/d1.csv", "dataset"),
            _artifact("c1", "models/c1.pt", "checkpoint"),
        ],
        relationships=[_edge("d1", "r1"), _edge("c1", "r1")],
    )
    path = _write_doc(folder, doc)
    patch = _write_patch(
        folder,
        '{"evaluation_dataset_artifact_id": "d1", '
        '"model_checkpoint_artifact_id": "c1"}',
    )
    result = _author(path, patch=patch)
    assert result.set_fields == (
        "evaluation_dataset_artifact_id",
        "model_checkpoint_artifact_id",
    )
    written = _json(path)
    assert written["runs"][0]["conditions"] == {
        "evaluation_dataset_artifact_id": "d1",
        "model_checkpoint_artifact_id": "c1",
    }
    assert written["relationships"] == doc["relationships"]


@pytest.mark.parametrize(
    "artifacts,relationships",
    [
        ([], []),
        ([_artifact("d1", "data/d1.csv", "dataset")], []),
        (
            [_artifact("d1", "data/d1.csv", "dataset")],
            [_edge("d1", "r1", "produced_by")],
        ),
        (
            [_artifact("d1", "data/d1.csv", "dataset")],
            [_edge("d1", "r2")],
        ),
    ],
)
def test_invalid_dataset_selectors_are_selector_invalid(
    tmp_path, artifacts, relationships
):
    folder = tmp_path / "exp"
    path = _write_doc(
        folder,
        _base_doc(
            runs=[{"id": "r1"}, {"id": "r2"}],
            artifacts=artifacts,
            relationships=relationships,
        ),
    )
    patch = _write_patch(
        folder, '{"evaluation_dataset_artifact_id": "d1"}'
    )
    _expect("SELECTOR_INVALID", lambda: _author(path, patch=patch))


def test_wrong_kind_selector_is_selector_invalid(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(
        folder,
        _selector_doc(artifacts=[_artifact("d1", "x", "checkpoint")]),
    )
    patch = _write_patch(
        folder, '{"evaluation_dataset_artifact_id": "d1"}'
    )
    _expect("SELECTOR_INVALID", lambda: _author(path, patch=patch))


def test_directory_artifact_selector_is_selector_invalid(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(
        folder,
        _selector_doc(
            artifacts=[_artifact("d1", "data/dir", "dataset", directory=True)]
        ),
    )
    patch = _write_patch(
        folder, '{"evaluation_dataset_artifact_id": "d1"}'
    )
    _expect("SELECTOR_INVALID", lambda: _author(path, patch=patch))


def test_repeated_input_to_edges_with_one_correct_are_accepted(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(
        folder,
        _selector_doc(
            relationships=[_edge("d1", "r2"), _edge("d1", "r1")]
        ),
    )
    patch = _write_patch(
        folder, '{"evaluation_dataset_artifact_id": "d1"}'
    )
    assert _author(path, patch=patch).changed is True


def test_untouched_invalid_selector_does_not_block_another_edit(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(
        folder,
        _base_doc(
            conditions={"evaluation_dataset_artifact_id": "missing"},
            artifacts=[],
            relationships=[],
        ),
    )
    patch = _write_patch(folder, '{"source_revision": "abc"}')
    result = _author(path, patch=patch)
    assert result.changed is True
    conditions = _json(path)["runs"][0]["conditions"]
    assert conditions["evaluation_dataset_artifact_id"] == "missing"
    assert conditions["source_revision"] == "abc"


def test_invalid_selector_in_another_run_does_not_block(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(
        folder,
        _base_doc(
            runs=[
                {"id": "r1"},
                {
                    "id": "r2",
                    "conditions": {
                        "evaluation_dataset_artifact_id": "missing"
                    },
                },
            ],
            artifacts=[],
        ),
    )
    patch = _write_patch(folder, '{"source_revision": "abc"}')
    assert _author(path, patch=patch).changed is True
    empty_patch = _write_patch(folder, "{}", name="empty.json")
    assert _author(path, patch=empty_patch).changed is False


def test_clearing_an_invalid_selector_is_a_valid_repair(tmp_path):
    path = _write_doc(
        tmp_path / "exp",
        _base_doc(
            conditions={"evaluation_dataset_artifact_id": "missing"},
            artifacts=[],
        ),
    )
    result = _author(path, clears=["evaluation_dataset_artifact_id"])
    assert result.changed is True
    assert "conditions" not in _json(path)["runs"][0]


def test_same_value_valid_selector_is_a_noop(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(
        folder,
        _selector_doc(
            conditions={"evaluation_dataset_artifact_id": "d1"}
        ),
    )
    before = _bytes(path)
    patch = _write_patch(
        folder, '{"evaluation_dataset_artifact_id": "d1"}'
    )
    result = _author(path, patch=patch)
    assert result.changed is False
    assert _bytes(path) == before


def test_same_value_invalid_selector_is_selector_invalid(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(
        folder,
        _base_doc(
            conditions={"evaluation_dataset_artifact_id": "missing"},
            artifacts=[],
        ),
    )
    before = _bytes(path)
    patch = _write_patch(
        folder, '{"evaluation_dataset_artifact_id": "missing"}'
    )
    _expect("SELECTOR_INVALID", lambda: _author(path, patch=patch))
    assert _bytes(path) == before


def test_duplicate_artifact_id_resolves_by_first_occurrence(tmp_path):
    folder = tmp_path / "exp"
    first_wrong = _selector_doc(
        artifacts=[
            _artifact("d1", "a", "checkpoint"),
            _artifact("d1", "b", "dataset"),
        ]
    )
    path = _write_doc(folder / "wrong", first_wrong)
    patch = _write_patch(
        folder, '{"evaluation_dataset_artifact_id": "d1"}'
    )
    _expect("SELECTOR_INVALID", lambda: _author(path, patch=patch))

    first_right = _selector_doc(
        artifacts=[
            _artifact("d1", "a", "dataset"),
            _artifact("d1", "b", "checkpoint"),
        ]
    )
    path = _write_doc(folder / "right", first_right)
    result = _author(path, patch=patch)
    assert result.changed is True
    assert (
        _json(path)["runs"][0]["conditions"][
            "evaluation_dataset_artifact_id"
        ]
        == "d1"
    )


_SELECTOR_EQUIVALENCE_CASES = [
    (
        "valid",
        [_artifact("d1", "data/d1.csv", "dataset")],
        [_edge("d1", "r1")],
        "accepted",
    ),
    ("missing", [], [], "SELECTOR_INVALID"),
    (
        "directory",
        [_artifact("d1", "data/d1", "dataset", directory=True)],
        [_edge("d1", "r1")],
        "SELECTOR_INVALID",
    ),
    (
        "wrong-kind",
        [_artifact("d1", "models/d1.pt", "checkpoint")],
        [_edge("d1", "r1")],
        "SELECTOR_INVALID",
    ),
    (
        "no-input-to",
        [_artifact("d1", "data/d1.csv", "dataset")],
        [],
        "SELECTOR_INVALID",
    ),
    (
        "duplicate-first-wrong-kind",
        [
            _artifact("d1", "models/d1.pt", "checkpoint"),
            _artifact("d1", "data/d1.csv", "dataset"),
        ],
        [_edge("d1", "r1")],
        "SELECTOR_INVALID",
    ),
    (
        "duplicate-first-right-kind",
        [
            _artifact("d1", "data/d1.csv", "dataset"),
            _artifact("d1", "models/d1.pt", "checkpoint"),
        ],
        [_edge("d1", "r1")],
        "accepted",
    ),
]


@pytest.mark.parametrize(
    "case,artifacts,relationships,expected",
    _SELECTOR_EQUIVALENCE_CASES,
    ids=[case[0] for case in _SELECTOR_EQUIVALENCE_CASES],
)
def test_authoring_selector_verdict_matches_check_conditions(
    tmp_path, case, artifacts, relationships, expected
):
    folder = tmp_path / case
    path = _write_doc(
        folder,
        _base_doc(
            conditions={"evaluation_dataset_artifact_id": "d1"},
            artifacts=artifacts,
            relationships=relationships,
        ),
    )
    patch = _write_patch(
        folder, '{"evaluation_dataset_artifact_id": "d1"}'
    )
    try:
        result = _author(path, patch=patch)
    except ConditionsError as exc:
        author_verdict = exc.code
    else:
        # A same-value valid selector is a successful no-op.
        assert result.changed is False
        author_verdict = "accepted"

    findings = check_conditions(load_manifest(path).manifest)
    check_verdict = "SELECTOR_INVALID" if findings else "accepted"

    assert author_verdict == expected
    assert check_verdict == expected
    assert author_verdict == check_verdict


# --------------------------------------------------------------------------
# Comparison bounds on patch-supplied values
# --------------------------------------------------------------------------


def test_safe_integer_bound(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    accepted = _write_patch(
        folder,
        json.dumps({"environment": {"n": MAX_SAFE_INT}}),
        name="ok.json",
    )
    _author(path, patch=accepted)
    assert _json(path)["runs"][0]["conditions"]["environment"]["n"] == MAX_SAFE_INT

    for index, value in enumerate(
        [MAX_SAFE_INT + 1, -(MAX_SAFE_INT + 1)]
    ):
        patch = _write_patch(
            folder,
            json.dumps({"environment": {"n": value}}),
            name=f"bad-{index}.json",
        )
        _expect("PATCH_INVALID", lambda p=patch: _author(path, patch=p))


def test_protocol_version_integer_bound(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    ok = _write_patch(
        folder,
        json.dumps(
            {"evaluation_protocol": {"protocol_id": "p", "version": MAX_SAFE_INT}}
        ),
        name="ok.json",
    )
    _author(path, patch=ok)
    bad = _write_patch(
        folder,
        json.dumps(
            {
                "evaluation_protocol": {
                    "protocol_id": "p",
                    "version": MAX_SAFE_INT + 1,
                }
            }
        ),
        name="bad.json",
    )
    _expect("PATCH_INVALID", lambda: _author(path, patch=bad))


def test_float_magnitude_bound(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    ok = _write_patch(
        folder,
        json.dumps({"environment": {"n": MAX_ABS_FLOAT}}),
        name="ok.json",
    )
    _author(path, patch=ok)
    for index, value in enumerate([1e300, -1e300]):
        bad = _write_patch(
            folder,
            json.dumps({"environment": {"n": value}}),
            name=f"bad-{index}.json",
        )
        _expect("PATCH_INVALID", lambda p=bad: _author(path, patch=p))


def test_container_width_bound(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    accepted = {f"k{index}": 0 for index in range(MAX_CONTAINER_WIDTH)}
    ok = _write_patch(
        folder, json.dumps({"environment": accepted}), name="ok.json"
    )
    _author(path, patch=ok)
    assert list(_json(path)["runs"][0]["conditions"]["environment"]) == list(
        accepted
    )

    too_wide = {f"k{index}": 0 for index in range(MAX_CONTAINER_WIDTH + 1)}
    bad = _write_patch(
        folder, json.dumps({"environment": too_wide}), name="bad.json"
    )
    _expect("PATCH_INVALID", lambda: _author(path, patch=bad))

    wide_list = [0] * (MAX_CONTAINER_WIDTH + 1)
    bad_list = _write_patch(
        folder,
        json.dumps({"environment": {"a": wide_list}}),
        name="bad-list.json",
    )
    _expect("PATCH_INVALID", lambda: _author(path, patch=bad_list))


def test_per_value_node_count_bound(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    ok = _write_patch(
        folder,
        json.dumps({"environment": _env_of_nodes(MAX_NODES)}),
        name="nodes-ok.json",
    )
    _author(path, patch=ok)

    too_many = _write_patch(
        folder,
        json.dumps({"environment": _env_of_nodes(MAX_NODES + 1)}),
        name="nodes-bad.json",
    )
    _expect("PATCH_INVALID", lambda: _author(path, patch=too_many))


@pytest.mark.parametrize("field", ["evaluation_split", "source_revision"])
def test_scalar_string_length_bound(tmp_path, field):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    ok = _write_patch(
        folder,
        json.dumps({field: "x" * MAX_STRING_LENGTH}),
        name="ok.json",
    )
    _author(path, patch=ok)
    bad = _write_patch(
        folder,
        json.dumps({field: "x" * (MAX_STRING_LENGTH + 1)}),
        name="bad.json",
    )
    _expect("PATCH_INVALID", lambda: _author(path, patch=bad))


def test_environment_key_and_value_string_length_bound(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    key_ok = _write_patch(
        folder,
        json.dumps({"environment": {"k" * MAX_STRING_LENGTH: 1}}),
        name="key-ok.json",
    )
    _author(path, patch=key_ok)
    key_bad = _write_patch(
        folder,
        json.dumps({"environment": {"k" * (MAX_STRING_LENGTH + 1): 1}}),
        name="key-bad.json",
    )
    _expect("PATCH_INVALID", lambda: _author(path, patch=key_bad))

    value_bad = _write_patch(
        folder,
        json.dumps(
            {"environment": {"a": "v" * (MAX_STRING_LENGTH + 1)}}
        ),
        name="value-bad.json",
    )
    _expect("PATCH_INVALID", lambda: _author(path, patch=value_bad))


def test_patch_lone_surrogate_is_patch_invalid(tmp_path):
    path = _write_doc(tmp_path / "exp", _base_doc())
    patch = _write_patch(tmp_path, b'{"source_revision": "\\ud800"}')
    _expect("PATCH_INVALID", lambda: _author(path, patch=patch))


def test_environment_depth_bound(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    ok = _write_patch(
        folder,
        json.dumps(
            {"environment": _nested_environment(MAX_ENVIRONMENT_DEPTH)}
        ),
        name="depth-ok.json",
    )
    _author(path, patch=ok)

    bad = _write_patch(
        folder,
        json.dumps(
            {"environment": _nested_environment(MAX_ENVIRONMENT_DEPTH + 1)}
        ),
        name="depth-bad.json",
    )
    _expect("PATCH_INVALID", lambda: _author(path, patch=bad))


def test_patch_container_depth_bound_accepts_exactly_nine():
    nine = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": 1}}}}}}}}
    ten = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": 1}}}}}}}}}
    # Root patch object + environment root + seven nested objects = 9 levels.
    conditions_module._check_patch_depth({"environment": nine})
    with pytest.raises(ConditionsError) as excinfo:
        conditions_module._check_patch_depth({"environment": ten})
    assert excinfo.value.code == "PATCH_INVALID"


def test_environment_compact_size_bound(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    ok = _write_patch(
        folder,
        json.dumps(
            {"environment": _env_exact_size(MAX_ENVIRONMENT_BYTES)}
        ),
        name="size-ok.json",
    )
    _author(path, patch=ok)
    written = _json(path)["runs"][0]["conditions"]["environment"]
    assert _env_size(written) == MAX_ENVIRONMENT_BYTES

    bad = _write_patch(
        folder,
        json.dumps(
            {"environment": _env_exact_size(MAX_ENVIRONMENT_BYTES + 1)}
        ),
        name="size-bad.json",
    )
    _expect("PATCH_INVALID", lambda: _author(path, patch=bad))


def test_hard_coded_authoring_bound_constants():
    assert MAX_PATCH_BYTES == 65536
    assert MAX_PATCH_DEPTH == 9
    assert MAX_STRING_LENGTH == 1024
    assert MAX_ENVIRONMENT_DEPTH == 8
    assert MAX_ENVIRONMENT_BYTES == 16384
    assert MAX_SAFE_INT == 9007199254740991
    assert MAX_ABS_FLOAT == 1e100
    assert MAX_CONTAINER_WIDTH == 128
    assert MAX_NODES == 4096


def test_width_literal_bound_128_and_129(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())

    at_limit = {f"k{index}": 0 for index in range(128)}
    ok = _write_patch(
        folder, json.dumps({"environment": at_limit}), name="width-ok.json"
    )
    _author(path, patch=ok)
    assert len(_json(path)["runs"][0]["conditions"]["environment"]) == 128

    over = {f"k{index}": 0 for index in range(129)}
    bad = _write_patch(
        folder, json.dumps({"environment": over}), name="width-bad.json"
    )
    _expect("PATCH_INVALID", lambda: _author(path, patch=bad))

    ok_list = _write_patch(
        folder,
        json.dumps({"environment": {"a": [0] * 128}}),
        name="width-list-ok.json",
    )
    _author(path, patch=ok_list)
    bad_list = _write_patch(
        folder,
        json.dumps({"environment": {"a": [0] * 129}}),
        name="width-list-bad.json",
    )
    _expect("PATCH_INVALID", lambda: _author(path, patch=bad_list))


def test_nodes_literal_bound_4096_and_4097(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())

    ok = _write_patch(
        folder,
        json.dumps({"environment": _env_of_nodes(4096)}),
        name="nodes-ok.json",
    )
    _author(path, patch=ok)

    bad = _write_patch(
        folder,
        json.dumps({"environment": _env_of_nodes(4097)}),
        name="nodes-bad.json",
    )
    _expect("PATCH_INVALID", lambda: _author(path, patch=bad))


def test_string_literal_bound_1024_and_1025(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())

    ok = _write_patch(
        folder,
        json.dumps({"evaluation_split": "x" * 1024}),
        name="string-ok.json",
    )
    _author(path, patch=ok)

    bad = _write_patch(
        folder,
        json.dumps({"evaluation_split": "x" * 1025}),
        name="string-bad.json",
    )
    _expect("PATCH_INVALID", lambda: _author(path, patch=bad))


def test_safe_integer_literal_bound_9007199254740991_and_9007199254740992(
    tmp_path,
):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())

    ok = _write_patch(
        folder,
        json.dumps({"environment": {"n": 9007199254740991}}),
        name="int-ok.json",
    )
    _author(path, patch=ok)
    assert (
        _json(path)["runs"][0]["conditions"]["environment"]["n"]
        == 9007199254740991
    )

    bad = _write_patch(
        folder,
        json.dumps({"environment": {"n": 9007199254740992}}),
        name="int-bad.json",
    )
    _expect("PATCH_INVALID", lambda: _author(path, patch=bad))


def test_float_literal_bound_1e100_and_1e300(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())

    ok = _write_patch(
        folder,
        json.dumps({"environment": {"n": 1e100}}),
        name="float-ok.json",
    )
    _author(path, patch=ok)
    assert _json(path)["runs"][0]["conditions"]["environment"]["n"] == 1e100

    bad = _write_patch(
        folder,
        json.dumps({"environment": {"n": 1e300}}),
        name="float-bad.json",
    )
    _expect("PATCH_INVALID", lambda: _author(path, patch=bad))


def test_environment_size_literal_bound_16384_and_16385(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())

    ok = _write_patch(
        folder,
        json.dumps(
            {
                "environment": _env_exact_size(
                    16384, max_string_length=1024
                )
            }
        ),
        name="env-ok.json",
    )
    _author(path, patch=ok)
    written = _json(path)["runs"][0]["conditions"]["environment"]
    assert _env_size(written) == 16384

    bad = _write_patch(
        folder,
        json.dumps(
            {
                "environment": _env_exact_size(
                    16385, max_string_length=1024
                )
            }
        ),
        name="env-bad.json",
    )
    _expect("PATCH_INVALID", lambda: _author(path, patch=bad))


def test_patch_size_literal_bound_65536_and_65537(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())

    ok = _write_patch(
        folder, _padded_patch(65536), name="patch-ok.json"
    )
    result = _author(path, patch=ok)
    assert result.changed is True
    assert _json(path)["runs"][0]["conditions"] == {
        "source_revision": "rev"
    }

    bad = _write_patch(
        folder, _padded_patch(65537), name="patch-bad.json"
    )
    _expect("PATCH_INVALID", lambda: _author(path, patch=bad))


def test_untouched_out_of_bounds_value_does_not_block_another_edit(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(
        folder,
        _base_doc(conditions={"environment": {"n": MAX_SAFE_INT + 1}}),
    )
    patch = _write_patch(folder, '{"source_revision": "abc"}')
    result = _author(path, patch=patch)
    assert result.changed is True
    conditions = _json(path)["runs"][0]["conditions"]
    assert conditions["environment"] == {"n": MAX_SAFE_INT + 1}
    assert conditions["source_revision"] == "abc"

    resupplied = _write_patch(
        folder,
        json.dumps({"environment": {"n": MAX_SAFE_INT + 1}}),
        name="resupply.json",
    )
    _expect("PATCH_INVALID", lambda: _author(path, patch=resupplied))


def test_protocol_version_int_and_string_are_distinct(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    _author(
        path,
        patch=_write_patch(
            folder,
            '{"evaluation_protocol": {"protocol_id": "p", "version": 1}}',
            name="int.json",
        ),
    )
    assert _json(path)["runs"][0]["conditions"]["evaluation_protocol"] == {
        "protocol_id": "p",
        "version": 1,
    }
    _author(
        path,
        patch=_write_patch(
            folder,
            '{"evaluation_protocol": {"protocol_id": "p", "version": "1"}}',
            name="str.json",
        ),
    )
    assert _json(path)["runs"][0]["conditions"]["evaluation_protocol"] == {
        "protocol_id": "p",
        "version": "1",
    }
    for index, raw in enumerate(
        [
            '{"evaluation_protocol": {"protocol_id": "p", "version": 1.0}}',
            '{"evaluation_protocol": {"protocol_id": "p", "version": true}}',
            '{"evaluation_protocol": {"protocol_id": "p", "version": null}}',
        ]
    ):
        bad = _write_patch(folder, raw, name=f"bad-{index}.json")
        _expect("PATCH_INVALID", lambda p=bad: _author(path, patch=p))


# --------------------------------------------------------------------------
# Version and run selection
# --------------------------------------------------------------------------


@pytest.mark.parametrize("version", ["0.1", OMIT])
def test_schema_0_1_or_omitted_is_unsupported(tmp_path, version):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc(version=version))
    before = _bytes(path)
    before_mtime = path.stat().st_mtime_ns
    patch = _write_patch(folder, '{"evaluation_split": "test"}')
    _expect("UNSUPPORTED_VERSION", lambda: _author(path, patch=patch))
    assert _bytes(path) == before
    assert path.stat().st_mtime_ns == before_mtime
    assert _names(path.parent) == ["patch.json", MANIFEST]


def test_schema_0_1_stdin_is_not_consumed(tmp_path):
    path = _write_doc(tmp_path / "exp", _base_doc(version="0.1"))
    trap = _NeverRead()
    _expect(
        "UNSUPPORTED_VERSION",
        lambda: _author(path, patch="-", stdin=trap, dry_run=True),
    )
    assert trap.reads == []


def test_explicit_0_4_is_unsupported_version(tmp_path):
    # 0.4 loads (csv-cell slice C phase 1), but conditions authoring remains
    # 0.2-only. The version carrier selects the 0.4 command text.
    path = _write_doc(tmp_path / "exp", _base_doc(version="0.4"))
    patch = _write_patch(tmp_path, '{"evaluation_split": "test"}')
    error = _expect("UNSUPPORTED_VERSION", lambda: _author(path, patch=patch))
    assert error.version == "0.4"


def test_explicit_0_3_version_carrier_is_0_3(tmp_path):
    path = _write_doc(tmp_path / "exp", _base_doc(version="0.3"))
    patch = _write_patch(tmp_path, '{"evaluation_split": "test"}')
    error = _expect("UNSUPPORTED_VERSION", lambda: _author(path, patch=patch))
    assert error.version == "0.3"


def test_explicit_0_3_is_unsupported_version(tmp_path):
    # 0.3 loads (locator slice A), but conditions authoring remains 0.2-only.
    path = _write_doc(tmp_path / "exp", _base_doc(version="0.3"))
    patch = _write_patch(tmp_path, '{"evaluation_split": "test"}')
    _expect("UNSUPPORTED_VERSION", lambda: _author(path, patch=patch))


def test_explicit_null_conditions_source_is_manifest_load_failed(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc(conditions=None))
    before = _bytes(path)
    patch = _write_patch(folder, '{"evaluation_split": "test"}')
    _expect("MANIFEST_LOAD_FAILED", lambda: _author(path, patch=patch))
    assert _bytes(path) == before
    assert _names(folder) == ["patch.json", MANIFEST]


def test_0_1_with_conditions_is_manifest_load_failed(tmp_path):
    path = _write_doc(
        tmp_path / "exp",
        _base_doc(version="0.1", conditions={"evaluation_split": "test"}),
    )
    patch = _write_patch(tmp_path, '{"source_revision": "abc"}')
    _expect("MANIFEST_LOAD_FAILED", lambda: _author(path, patch=patch))


def test_zero_run_matches_is_run_selection_invalid(tmp_path):
    path = _write_doc(tmp_path / "exp", _base_doc())
    _expect(
        "RUN_SELECTION_INVALID",
        lambda: _author(path, clears=["source_revision"], run_id="other"),
    )


def test_multiple_run_matches_is_run_selection_invalid(tmp_path):
    path = _write_doc(
        tmp_path / "exp",
        _base_doc(runs=[{"id": "r1"}, {"id": "r1"}]),
    )
    _expect(
        "RUN_SELECTION_INVALID",
        lambda: _author(path, clears=["source_revision"]),
    )


def test_near_miss_run_selection_is_invalid(tmp_path):
    path = _write_doc(tmp_path / "exp", _base_doc(run_id="Baseline"))
    for candidate in ["baseline", "Baseline ", " Base", "Basel"]:
        _expect(
            "RUN_SELECTION_INVALID",
            lambda value=candidate: _author(
                path, clears=["source_revision"], run_id=value
            ),
        )


def test_unique_run_among_duplicate_other_ids_is_editable(tmp_path):
    path = _write_doc(
        tmp_path / "exp",
        _base_doc(runs=[{"id": "r1"}, {"id": "r2"}, {"id": "r2"}]),
    )
    result = _author(path, clears=["evaluation_split"], run_id="r1")
    assert result.changed is False


def test_folder_and_manifest_file_operands_are_equivalent(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    patch = _write_patch(folder, '{"evaluation_split": "test"}')
    result_from_folder = _author(folder, patch=patch)
    assert result_from_folder.changed is True
    assert "conditions" in _json(path)["runs"][0]


# --------------------------------------------------------------------------
# Paths and reserved targets
# --------------------------------------------------------------------------


def test_reserved_backup_operand_is_refused_before_load(tmp_path):
    folder = tmp_path / "exp"
    _write_doc(folder, _base_doc())
    backup = folder / f"{MANIFEST}.bak"
    backup.write_text("{ this is not json", encoding="utf-8")
    before = _bytes(backup)
    _expect(
        "RESERVED_PATH",
        lambda: _author(backup, clears=["source_revision"]),
    )
    assert _bytes(backup) == before


@pytest.mark.parametrize("suffix", [".bak", ".bak.2"])
def test_regular_backup_generations_are_reserved(tmp_path, suffix):
    folder = tmp_path / "exp"
    _write_doc(folder, _base_doc())
    backup = folder / f"{MANIFEST}{suffix}"
    backup.write_bytes(_bytes(folder / MANIFEST))
    _expect(
        "RESERVED_PATH",
        lambda: _author(backup, clears=["source_revision"]),
    )


def test_manifest_symlinked_to_backup_name_is_reserved(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    backup = folder / f"{MANIFEST}.bak"
    path.unlink()
    try:
        os.symlink(backup, path)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symbolic links unavailable: {exc.__class__.__name__}")
    _expect(
        "RESERVED_PATH",
        lambda: _author(path, clears=["source_revision"]),
    )


def test_backup_symlink_to_manifest_is_allowed_and_not_reused(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    original = _bytes(path)
    link = folder / f"{MANIFEST}.bak"
    try:
        os.symlink(path.name, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symbolic links unavailable: {exc.__class__.__name__}")
    patch = _write_patch(folder, '{"evaluation_split": "test"}')
    result = _author(path, patch=patch)
    assert result.changed is True
    assert result.backup == "created"
    assert result.backup_path.name == f"{MANIFEST}.bak.2"
    assert _bytes(folder / f"{MANIFEST}.bak.2") == original
    assert link.is_symlink()


def test_missing_manifest_and_folder_are_load_failures(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    _expect(
        "MANIFEST_LOAD_FAILED",
        lambda: _author(empty, clears=["source_revision"]),
    )
    _expect(
        "MANIFEST_LOAD_FAILED",
        lambda: _author(tmp_path / "absent.json", clears=["source_revision"]),
    )


# --------------------------------------------------------------------------
# Dry run, no-op and the write path
# --------------------------------------------------------------------------


def test_dry_run_changed_writes_nothing_and_creates_no_backup(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    before = _bytes(path)
    before_mtime = path.stat().st_mtime_ns
    calls: list[str] = []

    def deny(name):
        def fail(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} must not be called")

        return fail

    monkeypatch.setattr(conditions_module, "_target_mode", deny("_target_mode"))
    monkeypatch.setattr(
        conditions_module, "_backup_for_migration", deny("_backup_for_migration")
    )
    monkeypatch.setattr(conditions_module, "save_raw", deny("save_raw"))

    patch = _write_patch(folder, '{"evaluation_split": "test"}')
    result = _author(path, patch=patch, dry_run=True)

    assert result == ConditionsResult(
        run_id="r1",
        changed=True,
        dry_run=True,
        backup="none",
        backup_path=None,
        set_fields=("evaluation_split",),
        cleared_fields=(),
    )
    assert calls == []
    assert _bytes(path) == before
    assert path.stat().st_mtime_ns == before_mtime
    assert _names(folder) == ["patch.json", MANIFEST]
    assert _temp_names(folder) == []


def test_dry_run_noop_writes_nothing(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(
        folder, _base_doc(conditions={"evaluation_split": "test"})
    )
    before = _bytes(path)
    calls: list[str] = []

    def deny(name):
        def fail(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} must not be called")

        return fail

    monkeypatch.setattr(conditions_module, "_read_pre_backup", deny("_read_pre_backup"))
    monkeypatch.setattr(
        conditions_module, "_backup_for_migration", deny("_backup_for_migration")
    )
    monkeypatch.setattr(conditions_module, "save_raw", deny("save_raw"))

    patch = _write_patch(folder, '{"evaluation_split": "test"}')
    result = _author(path, patch=patch, dry_run=True)
    assert result.changed is False
    assert result.dry_run is True
    assert result.backup == "none"
    assert calls == []
    assert _bytes(path) == before


def test_noop_skips_preservation_serialization_and_stale_read(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(
        folder, _base_doc(conditions={"evaluation_split": "test"})
    )
    calls: list[str] = []

    def deny(name):
        def fail(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} must not be called")

        return fail

    for name in (
        "_prove_preservation",
        "_serialize_candidate",
        "_read_pre_backup",
        "_target_mode",
        "_backup_for_migration",
        "save_raw",
    ):
        monkeypatch.setattr(conditions_module, name, deny(name))

    patch = _write_patch(folder, '{"evaluation_split": "test"}')
    result = _author(path, patch=patch)
    assert result.changed is False
    assert result.cleared_fields == ()
    assert calls == []


def test_real_change_creates_one_backup_and_preserves_mode(tmp_path, posix_modes):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc(), mode=0o600)
    original = _bytes(path)
    patch = _write_patch(folder, '{"evaluation_split": "test"}')

    result = _author(path, patch=patch)

    backup = path.resolve().with_name(f"{MANIFEST}.bak")
    assert result.backup == "created"
    assert result.backup_path == backup
    assert _bytes(backup) == original
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert _json(path)["runs"][0]["conditions"] == {
        "evaluation_split": "test"
    }


def test_newer_backup_generation_is_created_when_newest_is_ineligible(
    tmp_path, posix_modes
):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc(), mode=0o640)
    original = _bytes(path)
    newer = folder / f"{MANIFEST}.bak.2"
    newer.write_bytes(b"unrelated")
    patch = _write_patch(folder, '{"evaluation_split": "test"}')
    result = _author(path, patch=patch)
    assert result.backup == "created"
    assert result.backup_path.name == f"{MANIFEST}.bak.3"
    assert _bytes(newer) == b"unrelated"
    assert _bytes(folder / f"{MANIFEST}.bak.3") == original


def test_eligible_backup_is_reused(tmp_path, posix_modes):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc(), mode=0o600)
    original = _bytes(path)
    backup = folder / f"{MANIFEST}.bak"
    backup.write_bytes(original)
    backup.chmod(0o600)
    patch = _write_patch(folder, '{"evaluation_split": "test"}')

    result = _author(path, patch=patch)

    assert result.backup == "reused"
    assert result.backup_path == path.resolve().with_name(f"{MANIFEST}.bak")
    assert _bytes(backup) == original
    assert not (folder / f"{MANIFEST}.bak.2").exists()


def test_backup_with_different_bytes_is_not_reused(tmp_path, posix_modes):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc(), mode=0o600)
    backup = folder / f"{MANIFEST}.bak"
    backup.write_bytes(b"older generation")
    backup.chmod(0o600)
    patch = _write_patch(folder, '{"evaluation_split": "test"}')
    result = _author(path, patch=patch)
    assert result.backup == "created"
    assert result.backup_path.name == f"{MANIFEST}.bak.2"


def test_backup_with_wider_mode_is_not_reused(tmp_path, posix_modes):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc(), mode=0o600)
    backup = folder / f"{MANIFEST}.bak"
    backup.write_bytes(_bytes(path))
    backup.chmod(0o666)
    patch = _write_patch(folder, '{"evaluation_split": "test"}')
    result = _author(path, patch=patch)
    assert result.backup == "created"
    assert result.backup_path.name == f"{MANIFEST}.bak.2"


def test_hardlink_backup_alias_is_not_reused(tmp_path, posix_modes):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc(), mode=0o600)
    backup = folder / f"{MANIFEST}.bak"
    try:
        os.link(path, backup)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"hard links unavailable: {exc.__class__.__name__}")
    patch = _write_patch(folder, '{"evaluation_split": "test"}')
    result = _author(path, patch=patch)
    assert result.backup == "created"
    assert result.backup_path.name == f"{MANIFEST}.bak.2"


def test_save_raw_oserror_with_unchanged_bytes_is_write_failed(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    before = _bytes(path)
    patch = _write_patch(folder, '{"evaluation_split": "test"}')

    def fail(*args, **kwargs):
        raise OSError(errno.EIO, "silent")

    monkeypatch.setattr(conditions_module, "save_raw", fail)
    _expect("WRITE_FAILED", lambda: _author(path, patch=patch))
    assert _bytes(path) == before
    assert _temp_names(folder) == []


def test_save_raw_oserror_with_changed_bytes_is_stale(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    patch = _write_patch(folder, '{"evaluation_split": "test"}')

    def fail(data, target, *, expected_bytes):
        target.write_bytes(b"concurrent writer")
        raise OSError(errno.EIO, "silent")

    monkeypatch.setattr(conditions_module, "save_raw", fail)
    _expect("STALE_MANIFEST", lambda: _author(path, patch=patch))
    assert path.read_bytes() == b"concurrent writer"


def test_save_raw_oserror_with_failed_diagnostic_read_is_write_failed(
    tmp_path, monkeypatch
):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    patch = _write_patch(folder, '{"evaluation_split": "test"}')

    def fail(data, target, *, expected_bytes):
        target.unlink()
        raise OSError(errno.EIO, "silent")

    monkeypatch.setattr(conditions_module, "save_raw", fail)
    _expect("WRITE_FAILED", lambda: _author(path, patch=patch))
    assert not path.exists()


def test_post_replace_cleanup_error_is_a_committed_success(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    original = _bytes(path)
    patch = _write_patch(folder, '{"evaluation_split": "test"}')

    real_replace = os.replace
    real_unlink = Path.unlink
    state = {"replaced": False}

    def tracking_replace(source, target, *args, **kwargs):
        outcome = real_replace(source, target, *args, **kwargs)
        state["replaced"] = True
        return outcome

    def failing_cleanup(self, *args, **kwargs):
        if state["replaced"] and _is_staged_temp(self):
            raise OSError(errno.EIO, "cleanup failed")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(os, "replace", tracking_replace)
    monkeypatch.setattr(Path, "unlink", failing_cleanup)

    result = _author(path, patch=patch)

    assert result.changed is True
    assert result.backup == "created"
    assert result.backup_path is not None
    assert _bytes(result.backup_path) == original
    assert _json(path)["runs"][0]["conditions"] == {
        "evaluation_split": "test"
    }
    assert _temp_names(folder) == []


def test_pre_replace_cleanup_error_is_write_failed(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    original = _bytes(path)
    patch = _write_patch(folder, '{"evaluation_split": "test"}')

    real_unlink = Path.unlink

    def failing_replace(source, target, *args, **kwargs):
        raise OSError(errno.EIO, "replace failed")

    def failing_cleanup(self, *args, **kwargs):
        if _is_staged_temp(self):
            raise OSError(errno.EIO, "cleanup failed")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(os, "replace", failing_replace)
    monkeypatch.setattr(Path, "unlink", failing_cleanup)

    _expect("WRITE_FAILED", lambda: _author(path, patch=patch))
    assert _bytes(path) == original
    assert _bytes(path.resolve().with_name(f"{MANIFEST}.bak")) == original
    assert len(_temp_names(folder)) == 1


def test_pre_backup_stale_is_detected_without_a_backup(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    patch = _write_patch(folder, '{"evaluation_split": "test"}')
    real_read = conditions_module._read_pre_backup

    def stale(target):
        real_read(target)
        target.write_bytes(b"changed under us")
        return real_read(target)

    monkeypatch.setattr(conditions_module, "_read_pre_backup", stale)
    _expect("STALE_MANIFEST", lambda: _author(path, patch=patch))
    assert path.read_bytes() == b"changed under us"
    assert _names(folder) == ["patch.json", MANIFEST]


def test_pre_backup_read_failure_is_read_failed(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    patch = _write_patch(folder, '{"evaluation_split": "test"}')

    def fail(target):
        raise MigrationError("READ_FAILED")

    monkeypatch.setattr(conditions_module, "_read_pre_backup", fail)
    _expect("READ_FAILED", lambda: _author(path, patch=patch))
    assert _names(folder) == ["patch.json", MANIFEST]


def test_backup_creation_failure_is_write_failed(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    before = _bytes(path)
    patch = _write_patch(folder, '{"evaluation_split": "test"}')

    def fail(target, source_bytes, source_mode):
        raise MigrationError("WRITE_FAILED")

    monkeypatch.setattr(conditions_module, "_backup_for_migration", fail)
    _expect("WRITE_FAILED", lambda: _author(path, patch=patch))
    assert _bytes(path) == before
    assert _names(folder) == ["patch.json", MANIFEST]


def test_source_lone_surrogate_fails_serialization_without_a_backup(tmp_path):
    folder = tmp_path / "exp"
    raw = (
        '{"schema_version": "0.2", "project": {"id": "p", "name": "P", '
        '"metadata": {"s": "\\ud800"}}, "runs": [{"id": "r1"}]}\n'
    )
    path = _write_doc(folder, raw=raw)
    before = _bytes(path)
    patch = _write_patch(folder, '{"evaluation_split": "test"}')
    _expect("SERIALIZATION_FAILED", lambda: _author(path, patch=patch))
    assert _bytes(path) == before
    assert _names(folder) == ["patch.json", MANIFEST]


def test_noop_on_lone_surrogate_source_returns_before_serialization(tmp_path):
    folder = tmp_path / "exp"
    raw = (
        '{"schema_version": "0.2", "project": {"id": "p", "name": "P", '
        '"metadata": {"s": "\\ud800"}}, "runs": [{"id": "r1"}]}\n'
    )
    path = _write_doc(folder, raw=raw)
    result = _author(path, patch=_write_patch(folder, "{}"))
    assert result.changed is False


def test_edit_removing_the_only_lone_surrogate_succeeds(tmp_path):
    folder = tmp_path / "exp"
    raw = (
        '{"schema_version": "0.2", "project": {"id": "p", "name": "P"}, '
        '"runs": [{"id": "r1", "conditions": {"environment": {"s": "\\ud800"}}}]}\n'
    )
    path = _write_doc(folder, raw=raw)
    result = _author(path, clears=["environment"])
    assert result.changed is True
    assert "conditions" not in _json(path)["runs"][0]


def test_failed_patch_leaves_no_temp_and_manifest_unchanged(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    before = _bytes(path)
    patch = _write_patch(folder, '{"evaluation_split": "test", "unknown": 1}')
    _expect("PATCH_INVALID", lambda: _author(path, patch=patch))
    assert _bytes(path) == before
    assert _temp_names(folder) == []


# --------------------------------------------------------------------------
# Reused-helper error mapping by code only
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "helper,code",
    [
        ("_reject_reserved_target", "RESERVED_PATH"),
        ("load_manifest", "MANIFEST_LOAD_FAILED"),
        ("_serialize_candidate", "SERIALIZATION_FAILED"),
        ("_read_pre_backup", "READ_FAILED"),
        ("_target_mode", "WRITE_FAILED"),
        ("_prove_preservation", "INTERNAL_ERROR"),
    ],
)
def test_migration_error_codes_translate_by_code(tmp_path, monkeypatch, helper, code):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    patch = _write_patch(folder, '{"evaluation_split": "test"}')

    def fail(*args, **kwargs):
        raise MigrationError(code)

    monkeypatch.setattr(conditions_module, helper, fail)
    error = _expect(code, lambda: _author(path, patch=patch))
    assert code not in str(error)


def test_unknown_migration_code_maps_to_internal_error(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    patch = _write_patch(folder, '{"evaluation_split": "test"}')
    error = MigrationError("READ_FAILED")
    error.code = "FUTURE_CODE"

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(conditions_module, "_target_mode", fail)
    _expect("INTERNAL_ERROR", lambda: _author(path, patch=patch))


def test_result_is_frozen_and_error_catalogue_is_closed():
    result = ConditionsResult("r1", False, False, "none", None, (), ())
    with pytest.raises(Exception) as excinfo:
        result.changed = True
    assert excinfo.type.__name__ == "FrozenInstanceError"

    assert CONDITIONS_ERROR_CODES == {
        "UNSUPPORTED_VERSION",
        "RESERVED_PATH",
        "MANIFEST_LOAD_FAILED",
        "RUN_SELECTION_INVALID",
        "PATCH_INVALID",
        "SELECTOR_INVALID",
        "SERIALIZATION_FAILED",
        "READ_FAILED",
        "STALE_MANIFEST",
        "WRITE_FAILED",
        "INTERNAL_ERROR",
    }
    with pytest.raises(ValueError):
        ConditionsError("INVALID_ARGUMENTS")


# --------------------------------------------------------------------------
# Privacy and no-I/O
# --------------------------------------------------------------------------


def test_core_prints_nothing_on_success_or_failure(tmp_path, capsys):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    patch = _write_patch(folder, '{"evaluation_split": "test"}')
    _author(path, patch=patch)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""

    bad = _write_patch(folder, b'{"evaluation_split": "\\ud800"}', name="bad.json")
    _expect("PATCH_INVALID", lambda: _author(path, patch=bad))
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_canary_values_never_reach_results_or_errors(tmp_path):
    canary = "CANARY-7f3a9c-SECRET"
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())

    patch = _write_patch(
        folder, json.dumps({"environment": {"api_key": canary}})
    )
    result = _author(path, patch=patch)
    rendered = repr(result)
    assert canary not in rendered
    assert set(result.set_fields) == {"environment"}
    assert canary not in str(result.backup_path)

    bad = _write_patch(
        folder,
        json.dumps({"environment": {"api_key": canary, "n": MAX_SAFE_INT + 1}}),
        name="bad.json",
    )
    error = _expect("PATCH_INVALID", lambda: _author(path, patch=bad))
    assert canary not in str(error)
    assert canary not in repr(error)


def test_error_text_never_contains_paths_or_selector_ids(tmp_path):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc(), name=MANIFEST)
    secret_selector = "missing-artifact-secret"
    patch = _write_patch(
        folder,
        json.dumps({"evaluation_dataset_artifact_id": secret_selector}),
    )
    error = _expect("SELECTOR_INVALID", lambda: _author(path, patch=patch))
    assert str(folder) not in str(error)
    assert secret_selector not in str(error)
    assert "evaluation_dataset_artifact_id" not in str(error)


def _install_io_spy(monkeypatch) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    real_builtins_open = builtins.open
    real_io_open = io.open
    real_read_bytes = Path.read_bytes
    real_stat = os.stat
    real_lstat = os.lstat
    real_listdir = os.listdir

    def recording_builtins_open(file, *args, **kwargs):
        events.append(("open", os.fspath(file)))
        return real_builtins_open(file, *args, **kwargs)

    def recording_io_open(file, *args, **kwargs):
        events.append(("open", os.fspath(file)))
        return real_io_open(file, *args, **kwargs)

    def recording_read_bytes(self):
        events.append(("read_bytes", os.fspath(self)))
        return real_read_bytes(self)

    def recording_stat(file, *args, **kwargs):
        events.append(("stat", os.fspath(file)))
        return real_stat(file, *args, **kwargs)

    def recording_lstat(file, *args, **kwargs):
        events.append(("lstat", os.fspath(file)))
        return real_lstat(file, *args, **kwargs)

    def recording_listdir(file="."):
        events.append(("listdir", os.fspath(file)))
        return real_listdir(file)

    monkeypatch.setattr(builtins, "open", recording_builtins_open)
    monkeypatch.setattr(io, "open", recording_io_open)
    monkeypatch.setattr(Path, "read_bytes", recording_read_bytes)
    monkeypatch.setattr(os, "stat", recording_stat)
    monkeypatch.setattr(os, "lstat", recording_lstat)
    monkeypatch.setattr(os, "listdir", recording_listdir)
    return events


def test_no_artifact_bytes_are_opened_or_statted(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    artifact_path = "secret/artifact.bin"
    path = _write_doc(
        folder,
        _base_doc(
            artifacts=[_artifact("d1", artifact_path, "dataset")],
            relationships=[_edge("d1", "r1")],
        ),
    )
    patch = _write_patch(
        folder, '{"evaluation_dataset_artifact_id": "d1"}'
    )
    events = _install_io_spy(monkeypatch)

    _author(path, patch=patch)

    assert not [item for _, item in events if item.endswith("artifact.bin")]


@pytest.mark.parametrize(
    "mode,condition_present,expected_listdir",
    [("real", False, 1), ("noop", True, 0), ("dry-run", False, 0)],
)
def test_no_artifact_io_event_counts(
    tmp_path, monkeypatch, mode, condition_present, expected_listdir
):
    folder = tmp_path / "exp"
    artifact_path = "secret/artifact.bin"
    path = _write_doc(
        folder,
        _base_doc(
            conditions=(
                {"evaluation_dataset_artifact_id": "d1"}
                if condition_present
                else OMIT
            ),
            artifacts=[_artifact("d1", artifact_path, "dataset")],
            relationships=[_edge("d1", "r1")],
        ),
    )
    patch = _write_patch(
        folder, '{"evaluation_dataset_artifact_id": "d1"}'
    )
    events = _install_io_spy(monkeypatch)

    result = _author(path, patch=patch, dry_run=mode == "dry-run")

    assert result.changed is (mode != "noop")
    listdirs = [item for kind, item in events if kind == "listdir"]
    assert len(listdirs) == expected_listdir
    if listdirs:
        assert Path(listdirs[0]) == folder.resolve()
    assert not [item for _, item in events if item.endswith("artifact.bin")]
    assert any(kind in {"open", "read_bytes"} for kind, _ in events)


def test_no_subprocess_network_or_application_environment(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    patch = _write_patch(folder, '{"evaluation_split": "test"}')

    def deny(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("forbidden operation")

    monkeypatch.setattr(subprocess, "Popen", deny)
    monkeypatch.setattr(socket, "socket", deny)
    monkeypatch.setattr(os, "getenv", deny)

    result = _author(path, patch=patch)
    assert result.changed is True


def test_only_one_manifest_load_and_one_patch_read(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    patch = _write_patch(folder, '{"evaluation_split": "test"}')
    load_calls: list[Path] = []
    read_calls: list[str] = []
    real_load = conditions_module.load_manifest
    real_read = conditions_module._read_patch

    def counting_load(target):
        load_calls.append(Path(target))
        return real_load(target)

    def counting_read(patch_path, stdin):
        read_calls.append(patch_path)
        return real_read(patch_path, stdin)

    monkeypatch.setattr(conditions_module, "load_manifest", counting_load)
    monkeypatch.setattr(conditions_module, "_read_patch", counting_read)

    _author(path, patch=patch)
    assert len(load_calls) == 1
    assert read_calls == [patch]


def test_dry_run_performs_the_pre_backup_read(tmp_path, monkeypatch):
    folder = tmp_path / "exp"
    path = _write_doc(folder, _base_doc())
    patch = _write_patch(folder, '{"evaluation_split": "test"}')
    reads: list[Path] = []
    real_read = conditions_module._read_pre_backup

    def counting_read(target):
        reads.append(target)
        return real_read(target)

    monkeypatch.setattr(conditions_module, "_read_pre_backup", counting_read)
    _author(path, patch=patch, dry_run=True)
    assert reads == [path.resolve()]


def test_python_versions_supported_by_this_suite():
    assert sys.version_info >= (3, 10)
