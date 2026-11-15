"""The optional TensorBoard adapter: discovery, selection, identity, limits and
the core volatile-target hook.

The tests build event files themselves with a small independent writer (CRC32C,
varints, protobuf message bytes), so nothing here needs TensorBoard, TensorFlow,
protobuf or google-crc32c.
"""

from __future__ import annotations

import ast
import hashlib
import itertools
import json
import os
import re
import struct
import subprocess
import sys
from importlib.metadata import distribution, entry_points
from pathlib import Path

import pytest
from packaging.requirements import Requirement

pytest.importorskip(
    "runevidence_tensorboard", reason="install packages/runevidence-tensorboard to run these tests"
)

from runevidence import __version__ as core_version  # noqa: E402
from runevidence import hashing  # noqa: E402
from runevidence.adapters import AdapterError, available_adapters  # noqa: E402
from runevidence.adapters.generic import GenericAdapter  # noqa: E402
from runevidence.checks import EXIT_INVALID  # noqa: E402
from runevidence.checks import files as files_module  # noqa: E402
from runevidence.cli import _event_artifact_is_volatile, main  # noqa: E402
from runevidence.models import (  # noqa: E402
    MANIFEST_FILENAME,
    SCHEMA_VERSION_0_5,
    Artifact,
    Manifest05,
    Project,
    Run,
    TensorboardLocator,
)
from runevidence.tag_filter import TagFilter, TagPatternTooExpensive  # noqa: E402
from runevidence_tensorboard import TensorboardAdapter, __version__  # noqa: E402
from runevidence_tensorboard import adapter as adapter_module  # noqa: E402

PACKAGE = Path(__file__).resolve().parents[1]
MIB = 1024 * 1024
TAG_MESSAGE = "a scalar tag cannot be represented in schema 0.5; nothing was written"
PROFILE_MESSAGE = (
    "the event file does not satisfy the strict TensorBoard profile; nothing was written"
)
OVER_CAP_MESSAGE = "the event file exceeds the 128 MiB TensorBoard limit; nothing was written"
BUDGET_MESSAGE = (
    "the selected TensorBoard scalars exceed the 10,000-result or 16 MiB tag limit; "
    "nothing was written"
)

# --- independent event writer (test-only) ----------------------------------

_MASK_DELTA = 0xA282EAD8


def _build_crc32c_table() -> tuple[int, ...]:
    table = []
    polynomial = 0x82F63B78
    for index in range(256):
        crc = index
        for _ in range(8):
            crc = (crc >> 1) ^ polynomial if crc & 1 else crc >> 1
        table.append(crc)
    return tuple(table)


_CRC32C_TABLE = _build_crc32c_table()


def _crc32c(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc = _CRC32C_TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def _masked_crc32c(data: bytes) -> int:
    crc = _crc32c(data)
    return (((crc >> 15) | (crc << 17)) + _MASK_DELTA) & 0xFFFFFFFF


def _varint(value: int) -> bytes:
    if value < 0:
        value += 1 << 64
    encoded = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            encoded.append(byte | 0x80)
        else:
            encoded.append(byte)
            return bytes(encoded)


def _key(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _varint_field(field: int, value: int) -> bytes:
    return _key(field, 0) + _varint(value)


def _bytes_field(field: int, payload: bytes) -> bytes:
    return _key(field, 2) + _varint(len(payload)) + payload


def _float_field(field: int, value: float) -> bytes:
    return _key(field, 5) + struct.pack("<f", value)


def _record(payload: bytes, *, data_crc: int | None = None) -> bytes:
    header = struct.pack("<Q", len(payload))
    return (
        header
        + struct.pack("<I", _masked_crc32c(header))
        + payload
        + struct.pack("<I", _masked_crc32c(payload) if data_crc is None else data_crc)
    )


def _records(*payloads: bytes) -> bytes:
    return b"".join(_record(payload) for payload in payloads)


def _value(tag: bytes, *, simple_value: float | None = None, tensor_bytes: bytes | None = None) -> bytes:
    parts = [_bytes_field(1, tag)]
    if simple_value is not None:
        parts.append(_float_field(2, simple_value))
    if tensor_bytes is not None:
        parts.append(_bytes_field(8, tensor_bytes))
    return b"".join(parts)


def _summary(*values: bytes) -> bytes:
    return b"".join(_bytes_field(1, value) for value in values)


def _event(
    *,
    step: int | None = None,
    summary_bytes: bytes | None = None,
    file_version: bytes | None = None,
) -> bytes:
    parts = []
    if step is not None:
        parts.append(_varint_field(2, step))
    if file_version is not None:
        parts.append(_bytes_field(3, file_version))
    if summary_bytes is not None:
        parts.append(_bytes_field(5, summary_bytes))
    return b"".join(parts)


def _scalar_event(tag: bytes, number: float, *, step: int | None = None) -> bytes:
    return _event(step=step, summary_bytes=_summary(_value(tag, simple_value=number)))




def _string_tensor(text: bytes, *, dtype: int = 7, extra: bytes = b"", shape: bytes = b"") -> bytes:
    parts = [_varint_field(1, dtype)]
    if shape:
        parts.append(_bytes_field(2, shape))
    parts.append(extra)
    parts.append(_bytes_field(8, text))
    return b"".join(parts)


def _string_event(tag: bytes, text: bytes, *, step: int = 0, tensor: bytes | None = None) -> bytes:
    payload = tensor if tensor is not None else _string_tensor(text)
    return _event(step=step, summary_bytes=_summary(_value(tag, tensor_bytes=payload)))

def _tensor_scalar(number: float) -> bytes:
    """A rank-0 DT_FLOAT TensorProto with one float_val (a plugin pseudo-scalar)."""
    tensor = _varint_field(1, 1) + _bytes_field(2, b"") + _float_field(5, number)
    return tensor


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _extract(root: Path, tag_filter=None):
    adapter = TensorboardAdapter()
    return adapter.extract(
        root,
        adapter.discover(root),
        project_id="tensorboard-example",
        project_name="Event folder",
        tag_filter=tag_filter,
    )


def _by_path(manifest):
    return {artifact.path: artifact for artifact in manifest.artifacts}


def _by_id(manifest):
    return {artifact.id: artifact for artifact in manifest.artifacts}


def _short(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _art_id(path: str) -> str:
    return f"tb-art-{_short(path.encode('utf-8'))}"


def _run_id(dir_rel: str) -> str:
    return f"tb-run-{_short(dir_rel.encode('utf-8'))}"


def _name_digest(artifact_id: str, occurrence: int, kind: str = "scalar") -> str:
    payload = artifact_id.encode("utf-8") + b"\x00" + str(occurrence).encode("ascii")
    if kind == "text":
        payload += b"\x00text"
    return _short(payload)


def _res_id(run_id: str, artifact_id: str, tag: str, step: int, occurrence: int) -> str:
    payload = (
        run_id.encode("utf-8")
        + b"\x00"
        + artifact_id.encode("utf-8")
        + b"\x00"
        + tag.encode("utf-8")
        + b"\x00"
        + str(step).encode("ascii")
        + b"\x00"
        + str(occurrence).encode("ascii")
    )
    return f"tb-res-{hashlib.sha256(payload).hexdigest()[:16]}"


# --- package and registration ----------------------------------------------


def test_entry_point_version_and_dependencies():
    assert available_adapters()["tensorboard"] is TensorboardAdapter
    assert __version__ == "0.1.1"
    dist = distribution("runevidence-tensorboard")
    assert dist.version == "0.1.1"
    core_dependency = next(
        Requirement(raw) for raw in dist.requires or [] if Requirement(raw).name == "runevidence"
    )
    assert core_version in core_dependency.specifier
    assert "0.1.0" not in core_dependency.specifier
    assert "0.2.0" not in core_dependency.specifier

    base = [
        req.split(";")[0].replace(" ", "").lower()
        for req in (dist.requires or [])
        if "extra" not in req.lower()
    ]
    assert "runevidence>=0.1.1,<0.2.0" in base or "runevidence<0.2.0,>=0.1.1" in base
    assert not any(
        req.startswith(("tensorflow", "tensorboard", "protobuf", "google-crc32c")) for req in base
    )

    eps = [ep for ep in entry_points(group="runevidence.adapters") if ep.name == "tensorboard"]
    assert len(eps) == 1 and eps[0].value == "runevidence_tensorboard:TensorboardAdapter"


def test_package_does_not_import_tensorflow_protobuf_or_crc_libraries():
    forbidden = ("tensorflow", "tensorboard", "google.protobuf", "google_crc32c")
    for path in (PACKAGE / "src" / "runevidence_tensorboard").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any(
                name == bad or name.startswith(bad + ".") for name in names for bad in forbidden
            ), path


# --- discovery --------------------------------------------------------------


def test_discovery_excludes_own_files_and_records_everything_else(tmp_path):
    root = tmp_path / "experiment"
    _write(root / "notes.txt", b"hello\n")
    _write(root / "sub" / "metrics.json", b'{"a": 1}\n')
    _write(root / "logs" / "events.out.tfevents.1", _records(_scalar_event(b"loss", 0.5)))
    _write(root / "logs" / "EVENTS.OUT.TFEVENTS.2", _records(_scalar_event(b"loss", 0.25)))
    # A folder named .tfevents is not an event file and never makes a child one.
    _write(root / ".tfevents" / "notes.txt", b"not an event file\n")
    _write(root / MANIFEST_FILENAME, b"{}")
    _write(root / (MANIFEST_FILENAME + ".bak"), b"{}")
    _write(root / (MANIFEST_FILENAME + ".bak.2"), b"{}")

    adapter = TensorboardAdapter()
    discovery = adapter.discover(root)

    assert discovery.files == [
        ".tfevents/notes.txt",
        "logs/EVENTS.OUT.TFEVENTS.2",
        "logs/events.out.tfevents.1",
        "notes.txt",
        "sub/metrics.json",
    ]  # fmt: skip
    manifest = adapter.extract(
        root, discovery, project_id="discovered", project_name="Discovered"
    )
    assert [artifact.path for artifact in manifest.artifacts] == discovery.files
    assert [run.name for run in manifest.runs] == ["logs"]
    assert ".tfevents/notes.txt" not in {run.name for run in manifest.runs}


# --- identity ---------------------------------------------------------------


def test_non_event_artifact_ids_equal_the_generic_adapter(tmp_path):
    root = tmp_path / "experiment"
    _write(root / "README.md", b"readme\n")
    _write(root / "sub" / "notes.txt", b"notes\n")
    _write(root / "events.out.tfevents.1", _records(_scalar_event(b"loss", 0.5, step=1)))

    generic = GenericAdapter()
    generic_manifest = generic.extract(
        root,
        generic.discover(root),
        project_id="cmp",
        project_name="Compare",
    )
    manifest = _extract(root)
    generic_ids = _by_path(generic_manifest)
    actual = _by_path(manifest)

    for path in ("README.md", "sub/notes.txt"):
        assert actual[path].id == generic_ids[path].id
        assert actual[path].run_id is None

    event_path = "events.out.tfevents.1"
    assert actual[event_path].id == _art_id(event_path)
    assert actual[event_path].run_id == _run_id(".")
    assert actual[event_path].sha256 == hashlib.sha256(
        (root / event_path).read_bytes()
    ).hexdigest()
    assert actual[event_path].size_bytes == (root / event_path).stat().st_size
    assert [run.name for run in manifest.runs] == ["."]


def test_fixed_ids_follow_the_documented_hash_formulas(tmp_path):
    root = tmp_path / "experiment"
    path = "logs/events.out.tfevents.1"
    _write(root / path, _records(_scalar_event(b"loss/val", 0.5, step=-3)))
    manifest = _extract(root)

    run = manifest.runs[0]
    artifact = manifest.artifacts[0]
    result = manifest.results[0]
    assert run.id == "tb-run-" + hashlib.sha256(b"logs").hexdigest()[:16]
    assert artifact.id == "tb-art-" + hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
    expected = (
        run.id.encode("utf-8")
        + b"\x00"
        + artifact.id.encode("utf-8")
        + b"\x00"
        + b"loss/val"
        + b"\x00"
        + b"-3"
        + b"\x00"
        + b"0"
    )
    assert result.id == "tb-res-" + hashlib.sha256(expected).hexdigest()[:16]
    assert result.id == _res_id(run.id, artifact.id, "loss/val", -3, 0)
    assert result.run_id == run.id
    assert result.artifact_id == artifact.id
    assert isinstance(result.locator, TensorboardLocator)
    assert result.locator.tag == "loss/val"
    assert result.locator.step == -3
    assert result.locator.occurrence == 0
    assert result.name == "loss/val @ -3"


def test_two_event_files_are_two_runs_and_identical_tags_do_not_collide(tmp_path):
    root = tmp_path / "experiment"
    _write(root / "events.out.tfevents.1", _records(_scalar_event(b"loss", 1.5, step=0)))
    _write(
        root / "logs" / "events.out.tfevents.2",
        _records(_scalar_event(b"loss", 2.5, step=0)),
    )
    manifest = _extract(root)

    assert [run.name for run in manifest.runs] == [".", "logs"]
    assert len({run.id for run in manifest.runs}) == 2
    assert len({result.id for result in manifest.results}) == 2
    assert len({result.artifact_id for result in manifest.results}) == 2
    assert [result.value for result in manifest.results] == [1.5, 2.5]
    assert [result.name for result in manifest.results] == ["loss @ 0", "loss @ 0"]

    for result in manifest.results:
        assert result.id == _res_id(result.run_id, result.artifact_id, "loss", 0, 0)


def test_result_names_are_step_qualified_and_occurrence_suffixes_are_sparse(tmp_path):
    root = tmp_path / "experiment"
    _write(
        root / "events.out.tfevents.1",
        _records(
            _event(
                step=2,
                summary_bytes=_summary(
                    _value(b"acc", simple_value=0.5),
                    _value(b"acc", simple_value=0.25),
                    _value(b"acc", simple_value=0.125),
                ),
            ),
            _scalar_event(b"acc", 0.75, step=5),
        ),
    )
    manifest = _extract(root)

    assert [result.name for result in manifest.results] == [
        "acc @ 2",
        "acc @ 2 #1",
        "acc @ 2 #2",
        "acc @ 5",
    ]
    assert [result.locator.occurrence for result in manifest.results] == [0, 1, 2, 0]
    assert [result.value for result in manifest.results] == [0.5, 0.25, 0.125, 0.75]


def test_scalar_order_exact_tags_steps_and_occurrence_numbering(tmp_path):
    root = tmp_path / "experiment"
    unicode_tag = "loss/é".encode("utf-8")
    _write(
        root / "events.out.tfevents.1",
        _records(
            _event(
                step=7,
                summary_bytes=_summary(
                    _value(b"z/one", simple_value=1.0),
                    _value(b"a/two", simple_value=2.0),
                ),
            ),
            _event(
                step=-3,
                summary_bytes=_summary(
                    _value(b"z/one", simple_value=3.0),
                    _value(unicode_tag, simple_value=4.0),
                ),
            ),
            _event(summary_bytes=_summary(_value(b"z/one", simple_value=5.0))),
            _scalar_event(b"z/one", 6.0, step=7),
            # A plugin pseudo-scalar (rank-0 DT_FLOAT tensor) is selected too.
            _event(
                step=7,
                summary_bytes=_summary(
                    _value(b"plugin/score", tensor_bytes=_tensor_scalar(2.5))
                ),
            ),
        ),
    )
    manifest = _extract(root)

    expected = [
        ("z/one", 7, 0, 1.0),
        ("a/two", 7, 0, 2.0),
        ("z/one", -3, 0, 3.0),
        ("loss/é", -3, 0, 4.0),
        ("z/one", 0, 0, 5.0),
        ("z/one", 7, 1, 6.0),
        ("plugin/score", 7, 0, 2.5),
    ]
    assert [
        (result.locator.tag, result.locator.step, result.locator.occurrence, result.value)
        for result in manifest.results
    ] == expected
    assert manifest.results[3].locator.tag.encode("utf-8") == unicode_tag
    assert manifest.results[5].name == "z/one @ 7 #1"


def test_fixed_ids_are_reserved_before_generic_allocation(tmp_path):
    root = tmp_path / "experiment"
    event_path = "events.out.tfevents.1"
    _write(root / event_path, _records(_scalar_event(b"loss", 0.5)))
    decoy = _art_id(event_path)
    _write(root / decoy, b"an ordinary file whose name is a fixed id\n")
    manifest = _extract(root)
    artifacts = _by_path(manifest)

    assert artifacts[event_path].id == decoy
    assert artifacts[decoy].id == decoy + "-2"


def test_fixed_id_collision_is_refused_with_the_fixed_message(tmp_path):
    root = tmp_path / "experiment"
    event_path = "events.out.tfevents.1"
    _write(root / event_path, _records(_scalar_event(b"loss", 0.5)))
    adapter = TensorboardAdapter()
    discovery = adapter.discover(root)
    with pytest.raises(AdapterError) as excinfo:
        adapter.extract(
            root,
            discovery,
            project_id=_art_id(event_path),
            project_name="Collision",
        )
    assert str(excinfo.value) == (
        "a generated id collides with an existing id; nothing was written"
    )


# --- empty and header-only files -------------------------------------------


def test_empty_and_header_only_files_are_artifacts_without_results(tmp_path):
    root = tmp_path / "experiment"
    _write(root / "empty.tfevents", b"")
    _write(
        root / "header.tfevents",
        _records(_event(file_version=b"brain.Event:2")),
    )
    manifest = _extract(root)

    assert isinstance(manifest, Manifest05)
    assert [artifact.path for artifact in manifest.artifacts] == [
        "empty.tfevents",
        "header.tfevents",
    ]
    assert [run.name for run in manifest.runs] == ["."]
    assert manifest.results == []
    assert manifest.evidence == []


def test_zero_event_files_is_not_an_error(tmp_path):
    root = tmp_path / "experiment"
    _write(root / "notes.txt", b"nothing to see\n")
    manifest = _extract(root)

    assert manifest.runs == []
    assert manifest.results == []
    assert [artifact.path for artifact in manifest.artifacts] == ["notes.txt"]
    assert manifest.artifacts[0].id == "notes.txt"
    assert manifest.artifacts[0].run_id is None


# --- refusals ---------------------------------------------------------------


@pytest.mark.parametrize(
    "tag_bytes",
    [b"", b"\xff\xfe", b"a" * 4097],
    ids=["empty", "invalid-utf8", "4097-bytes"],
)
def test_unrepresentable_tag_is_refused_exactly(tmp_path, tag_bytes):
    root = tmp_path / "experiment"
    _write(root / "events.out.tfevents.1", _records(_scalar_event(tag_bytes, 0.5)))
    with pytest.raises(AdapterError) as excinfo:
        _extract(root)
    assert str(excinfo.value) == TAG_MESSAGE


@pytest.mark.parametrize("mutation", ["truncated", "bad-data-crc", "trailing"])
def test_malformed_event_file_is_the_profile_message(tmp_path, mutation):
    root = tmp_path / "experiment"
    payload = _scalar_event(b"loss", 0.5)
    if mutation == "truncated":
        data = _record(payload)[:-3]
    elif mutation == "bad-data-crc":
        data = _record(payload, data_crc=0)
    else:
        data = _record(payload) + b"\x00"
    _write(root / "events.out.tfevents.1", data)
    with pytest.raises(AdapterError) as excinfo:
        _extract(root)
    assert str(excinfo.value) == PROFILE_MESSAGE


def test_missing_event_file_after_discovery_is_the_profile_message(tmp_path):
    root = tmp_path / "experiment"
    _write(root / "events.out.tfevents.1", _records(_scalar_event(b"loss", 0.5)))
    adapter = TensorboardAdapter()
    discovery = adapter.discover(root)
    (root / "events.out.tfevents.1").unlink()
    with pytest.raises(AdapterError) as excinfo:
        adapter.extract(root, discovery, project_id="p", project_name="P")
    assert str(excinfo.value) == PROFILE_MESSAGE


@pytest.mark.parametrize("state", ["changed", "refused-path", "refused-alias", "escapes-root"])
def test_refused_snapshot_states_are_the_profile_message(tmp_path, monkeypatch, state):
    root = tmp_path / "experiment"
    _write(root / "events.out.tfevents.1", _records(_scalar_event(b"loss", 0.5)))
    monkeypatch.setattr(
        adapter_module,
        "_read_snapshot_parts",
        lambda *args, **kwargs: files_module.SnapshotRead(state),
    )
    with pytest.raises(AdapterError) as excinfo:
        _extract(root)
    assert str(excinfo.value) == PROFILE_MESSAGE


def test_over_cap_event_file_is_refused_with_the_128_mib_message(tmp_path):
    root = tmp_path / "experiment"
    path = root / "events.out.tfevents.1"
    path.parent.mkdir(parents=True)
    with path.open("wb") as handle:
        handle.truncate(129 * MIB)  # sparse: no extra disk or memory cost
    with pytest.raises(AdapterError) as excinfo:
        _extract(root)
    assert str(excinfo.value) == OVER_CAP_MESSAGE


def test_result_budget_accepts_10000_and_refuses_10001(tmp_path):
    root = tmp_path / "experiment"
    path = root / "events.out.tfevents.1"
    _write(path, _records(*[_scalar_event(b"m", float(index), step=index) for index in range(10_000)]))
    manifest = _extract(root)
    assert len(manifest.results) == 10_000
    assert manifest.results[-1].locator.step == 9_999

    with path.open("ab") as handle:
        handle.write(_record(_scalar_event(b"m", 1.0, step=10_000)))
    with pytest.raises(AdapterError) as excinfo:
        _extract(root)
    assert str(excinfo.value) == BUDGET_MESSAGE


def test_tag_budget_accepts_16_mib_and_refuses_one_byte_over(tmp_path):
    root = tmp_path / "experiment"
    path = root / "events.out.tfevents.1"
    tag = b"t" * 4096
    _write(
        path,
        _records(*[_scalar_event(tag, 0.5, step=index) for index in range(4096)]),
    )
    manifest = _extract(root)
    assert len(manifest.results) == 4096
    assert manifest.results[0].locator.tag == "t" * 4096

    with path.open("ab") as handle:
        handle.write(_record(_scalar_event(tag, 0.5, step=4096)))
    with pytest.raises(AdapterError) as excinfo:
        _extract(root)
    assert str(excinfo.value) == BUDGET_MESSAGE


# --- one protected read -----------------------------------------------------


def test_event_file_is_read_once_and_never_through_sha256_file(tmp_path, monkeypatch):
    root = tmp_path / "experiment"
    path = root / "events.out.tfevents.1"
    data = _records(_scalar_event(b"loss", 0.5, step=1))
    _write(path, data)

    calls = []
    opens = []
    real_read = files_module._read_snapshot_parts
    real_open = files_module._open_protected

    def counting_read(root_arg, parts, *, cap=None, tensorboard_raised=False):
        calls.append((parts, cap, tensorboard_raised))
        return real_read(root_arg, parts, cap=cap, tensorboard_raised=tensorboard_raised)

    def counting_open(root_arg, parts):
        opens.append(parts)
        return real_open(root_arg, parts)

    def forbidden(*args, **kwargs):
        raise AssertionError("event files must not use this read path")

    monkeypatch.setattr(adapter_module, "_read_snapshot_parts", counting_read)
    monkeypatch.setattr(files_module, "_open_protected", counting_open)
    monkeypatch.setattr(hashing, "sha256_file", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)

    manifest = _extract(root)

    assert len(calls) == 1
    assert calls[0][0] == ("events.out.tfevents.1",)
    assert calls[0][1] == 128 * MIB
    assert calls[0][2] is True
    assert len(opens) == 1
    assert manifest.artifacts[0].sha256 == hashlib.sha256(data).hexdigest()
    assert manifest.artifacts[0].size_bytes == len(data)
    assert manifest.results[0].value == 0.5


# --- CLI integration and the volatile hook ---------------------------------


def _write_event_folder(root: Path) -> Path:
    _write(
        root / "logs" / "events.out.tfevents.1",
        _records(
            _event(file_version=b"brain.Event:2"),
            _scalar_event(b"loss", 0.5, step=1),
            _scalar_event(b"loss", 0.25, step=2),
            _scalar_event(b"eval/acc", 0.75, step=2),
        ),
    )
    _write(root / "notes.txt", b"not an event file\n")
    return root


def test_cli_init_and_validate_accept_a_generated_event_folder(tmp_path, capsys):
    root = _write_event_folder(tmp_path / "experiment")
    assert main(["init", str(root), "--adapter", "tensorboard"]) == 0
    capsys.readouterr()

    document = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert document["schema_version"] == "0.5"
    assert [run["name"] for run in document["runs"]] == ["logs"]
    assert len(document["results"]) == 3
    assert document["results"][0]["locator"] == {
        "kind": "tensorboard",
        "tag": "loss",
        "step": 1,
        "occurrence": 0,
    }
    assert document["results"][0]["name"] == "loss @ 1"

    assert main(["validate", str(root), "--strict"]) == 0


def test_cli_init_volatile_event_artifact_is_refused_before_any_write(tmp_path, capsys):
    root = (_write_event_folder(tmp_path / "experiment")).resolve()
    existing = root / MANIFEST_FILENAME
    existing.write_text(
        json.dumps({"schema_version": "0.1", "project": {"id": "old", "name": "old"}}),
        encoding="utf-8",
    )
    before = existing.read_bytes()

    assert (
        main(
            [
                "init",
                str(root),
                "--adapter",
                "tensorboard",
                "--volatile",
                "*.tfevents*",
                "--force",
            ]
        )
        == EXIT_INVALID
    )
    assert capsys.readouterr().out == (
        f"ERROR: adapter 'tensorboard' cannot read {root}: "
        "an event artifact cannot be volatile; nothing was written\n"
    )
    assert existing.read_bytes() == before
    assert not (root / (MANIFEST_FILENAME + ".bak")).exists()
    assert sorted(path.name for path in root.iterdir()) == [
        "logs",
        "notes.txt",
        MANIFEST_FILENAME,
    ]


def test_cli_init_volatile_non_event_artifact_is_still_allowed(tmp_path, capsys):
    root = _write_event_folder(tmp_path / "experiment")
    assert (
        main(
            [
                "init",
                str(root),
                "--adapter",
                "tensorboard",
                "--volatile",
                "notes.txt",
            ]
        )
        == 0
    )
    capsys.readouterr()
    document = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    artifacts = {artifact["path"]: artifact for artifact in document["artifacts"]}
    assert artifacts["notes.txt"]["volatile"] is True
    assert artifacts["notes.txt"]["sha256"] is None
    assert "volatile" not in artifacts["logs/events.out.tfevents.1"]


def test_cli_init_generic_volatile_behavior_is_unchanged(tmp_path, capsys):
    root = _write_event_folder(tmp_path / "experiment")
    assert (
        main(
            [
                "init",
                str(root),
                "--adapter",
                "generic",
                "--volatile",
                "*.tfevents*",
            ]
        )
        == 0
    )
    capsys.readouterr()
    document = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert document["schema_version"] == "0.1"
    artifacts = {artifact["path"]: artifact for artifact in document["artifacts"]}
    assert artifacts["logs/events.out.tfevents.1"]["volatile"] is True
    assert artifacts["logs/events.out.tfevents.1"]["sha256"] is None


# --- multi-file runs --------------------------------------------------------


def test_two_files_in_one_directory_share_a_run_and_get_qualified_names(tmp_path):
    root = tmp_path / "experiment"
    _write(root / "logs" / "a.tfevents", _records(_scalar_event(b"loss", 1.0, step=1)))
    _write(root / "logs" / "z.tfevents", _records(_scalar_event(b"loss", 2.0, step=1)))
    manifest = _extract(root)

    assert [run.name for run in manifest.runs] == ["logs"]
    assert len(manifest.runs) == 1
    assert len({artifact.run_id for artifact in manifest.artifacts}) == 1
    assert len({result.id for result in manifest.results}) == 2
    assert [result.value for result in manifest.results] == [1.0, 2.0]
    for result, basename in zip(manifest.results, ("a.tfevents", "z.tfevents")):
        digest = _name_digest(result.artifact_id, 0)
        assert result.name == f"loss @ 1 [{basename}] #{digest}"


def test_nested_directories_are_separate_runs_and_root_is_dot(tmp_path):
    root = tmp_path / "experiment"
    _write(root / "e.tfevents", _records(_scalar_event(b"loss", 0.1)))
    _write(root / "logs" / "a.tfevents", _records(_scalar_event(b"loss", 0.2)))
    _write(root / "logs" / "sub" / "b.tfevents", _records(_scalar_event(b"loss", 0.3)))
    manifest = _extract(root)

    assert [run.name for run in manifest.runs] == [".", "logs", "logs/sub"]
    assert [run.id for run in manifest.runs] == [
        _run_id("."),
        _run_id("logs"),
        _run_id("logs/sub"),
    ]


def test_one_file_per_directory_keeps_bare_names(tmp_path):
    root = tmp_path / "experiment"
    _write(root / "train" / "e.tfevents", _records(_scalar_event(b"loss", 1.0, step=4)))
    _write(root / "validation" / "e.tfevents", _records(_scalar_event(b"loss", 2.0, step=4)))
    manifest = _extract(root)

    assert [result.name for result in manifest.results] == ["loss @ 4", "loss @ 4"]


def test_occurrences_are_per_file_and_stay_distinct_across_files(tmp_path):
    root = tmp_path / "experiment"
    _write(
        root / "logs" / "a.tfevents",
        _records(
            _scalar_event(b"loss", 1.0, step=2),
            _scalar_event(b"loss", 1.5, step=2),
        ),
    )
    _write(root / "logs" / "b.tfevents", _records(_scalar_event(b"loss", 2.0, step=2)))
    manifest = _extract(root)

    assert [result.locator.occurrence for result in manifest.results] == [0, 1, 0]
    assert len({result.name for result in manifest.results}) == 3
    assert len({result.id for result in manifest.results}) == 3


# --- filtering --------------------------------------------------------------


def test_filter_selects_tags_and_leaves_occurrence_alone(tmp_path):
    root = tmp_path / "experiment"
    _write(
        root / "events.out.tfevents.1",
        _records(
            _event(
                step=3,
                summary_bytes=_summary(
                    _value(b"loss", simple_value=0.5),
                    _value(b"acc", simple_value=0.25),
                    _value(b"loss", simple_value=0.75),
                ),
            )
        ),
    )
    manifest = _extract(root, TagFilter(["loss"]))
    assert [result.locator.tag for result in manifest.results] == ["loss", "loss"]
    assert [result.locator.occurrence for result in manifest.results] == [0, 1]
    assert [result.name for result in manifest.results] == ["loss @ 3", "loss @ 3 #1"]


def test_filter_skips_unrepresentable_tags_instead_of_refusing(tmp_path):
    root = tmp_path / "experiment"
    _write(
        root / "events.out.tfevents.1",
        _records(
            _scalar_event(b"\xff", 0.5),
            _scalar_event(b"good", 1.0),
        ),
    )
    manifest = _extract(root, TagFilter(["*"]))
    assert [result.locator.tag for result in manifest.results] == ["good"]

    with pytest.raises(AdapterError) as excinfo:
        _extract(root)
    assert str(excinfo.value) == TAG_MESSAGE


def test_filter_budget_is_the_core_exception_not_an_adapter_error(tmp_path):
    root = tmp_path / "experiment"
    _write(root / "events.out.tfevents.1", _records(_scalar_event(b"ab", 0.5)))
    with pytest.raises(TagPatternTooExpensive):
        _extract(root, TagFilter(["ab"], budget=1))


def test_profile_failure_outranks_too_expensive(tmp_path):
    root = tmp_path / "experiment"
    _write(root / "a.tfevents", _records(_scalar_event(b"ab", 1.0)))
    _write(root / "z.tfevents", _record(_scalar_event(b"loss", 0.5))[:-3])
    with pytest.raises(AdapterError) as excinfo:
        _extract(root, TagFilter(["ab"], budget=1))
    assert str(excinfo.value) == PROFILE_MESSAGE


# --- path and collision refusals -------------------------------------------


@pytest.mark.skipif(adapter_module.os.name == "nt", reason="POSIX-only path rule")
def test_posix_event_path_with_a_backslash_is_refused_before_any_read(tmp_path):
    root = tmp_path / "experiment"
    adapter = TensorboardAdapter()
    discovery = adapter.discover(root)
    discovery.files = ["logs/a\\b.tfevents"]
    with pytest.raises(AdapterError) as excinfo:
        adapter.extract(root, discovery, project_id="p", project_name="P")
    assert str(excinfo.value) == adapter_module.BACKSLASH_MESSAGE


def test_surrogate_path_is_refused_before_any_read(tmp_path):
    adapter = TensorboardAdapter()
    discovery = adapter.discover(tmp_path)
    discovery.files = ["logs/\udcff.tfevents"]
    with pytest.raises(AdapterError) as excinfo:
        adapter.extract(tmp_path, discovery, project_id="p", project_name="P")
    assert str(excinfo.value) == adapter_module.SURROGATE_MESSAGE


def test_duplicate_generated_result_name_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter_module, "_name_digest", lambda artifact_id, occurrence, kind="scalar": "0" * 16)
    root = tmp_path / "experiment"
    _write(
        root / "logs" / "a.tfevents",
        _records(
            _scalar_event(b"loss", 1.0, step=1),
            _scalar_event(b"loss", 1.5, step=1),
        ),
    )
    _write(root / "logs" / "z.tfevents", _records(_scalar_event(b"loss", 2.0, step=1)))
    with pytest.raises(AdapterError) as excinfo:
        _extract(root)
    assert str(excinfo.value) == adapter_module.NAME_MESSAGE


def test_duplicate_generated_artifact_id_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter_module, "_artifact_id", lambda path: "tb-art-fixed")
    root = tmp_path / "experiment"
    _write(root / "logs" / "a.tfevents", _records(_scalar_event(b"loss", 1.0)))
    _write(root / "logs" / "z.tfevents", _records(_scalar_event(b"loss", 2.0)))
    with pytest.raises(AdapterError) as excinfo:
        _extract(root)
    assert str(excinfo.value) == adapter_module.COLLISION_MESSAGE


# --- CLI --tags -------------------------------------------------------------


def _two_tag_folder(tmp_path):
    root = tmp_path / "experiment"
    _write(
        root / "logs" / "e.tfevents",
        _records(
            _event(
                step=0,
                summary_bytes=_summary(
                    _value(b"loss", simple_value=0.5),
                    _value(b"acc", simple_value=0.25),
                ),
            )
        ),
    )
    return root


def test_cli_tags_selects_and_prints_no_warning(tmp_path, capsys):
    root = _two_tag_folder(tmp_path)
    assert main(["init", str(root), "--adapter", "tensorboard", "--tags", "loss"]) == 0
    out = capsys.readouterr().out
    assert "WARNING" not in out
    document = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert [result["locator"]["tag"] for result in document["results"]] == ["loss"]


def test_cli_tags_warns_after_a_successful_write(tmp_path, capsys):
    root = _two_tag_folder(tmp_path)
    assert main(["init", str(root), "--adapter", "tensorboard", "--tags", "nomatch"]) == 0
    out = capsys.readouterr().out
    assert (
        "WARNING: --tags pattern 'nomatch' matches no representable supported scalar tag"
        in out
    )
    document = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert document["results"] == []


def test_cli_tags_invalid_pattern_writes_nothing(tmp_path, capsys):
    root = _two_tag_folder(tmp_path)
    assert main(["init", str(root), "--adapter", "tensorboard", "--tags", ""]) == 2
    out = capsys.readouterr().out
    assert "ERROR: invalid --tags pattern. Nothing was written." in out
    assert not (root / MANIFEST_FILENAME).exists()


def test_cli_tags_unsupported_adapter_writes_nothing(tmp_path, capsys):
    root = _two_tag_folder(tmp_path)
    assert main(["init", str(root), "--adapter", "generic", "--tags", "loss"]) == 2
    out = capsys.readouterr().out
    assert (
        "ERROR: adapter 'generic' does not support --tags. Nothing was written." in out
    )
    assert not (root / MANIFEST_FILENAME).exists()


def test_cli_tags_contract_failure_writes_nothing(monkeypatch, tmp_path, capsys):
    class NoParameter:
        name = "fake"
        supports_tag_filters = True

        def extract(self, root, discovery, *, project_id, project_name):
            raise AssertionError("must not be called")

    from runevidence import cli as cli_module

    assert not cli_module._accepts_tag_filter(NoParameter())
    monkeypatch.setattr(cli_module, "get_adapter", lambda name: NoParameter())
    root = _two_tag_folder(tmp_path)
    assert main(["init", str(root), "--adapter", "fake", "--tags", "loss"]) == 2
    out = capsys.readouterr().out
    assert "ERROR: adapter 'fake' cannot apply --tags. Nothing was written." in out
    assert not (root / MANIFEST_FILENAME).exists()


def test_cli_tags_too_expensive_writes_nothing(tmp_path, capsys):
    root = tmp_path / "experiment"
    tags = [("a" * 4095) + ch for ch in "cdefghijklmnopqr"]
    _write(
        root / "e.tfevents",
        _records(*(_scalar_event(tag.encode("utf-8"), 0.5) for tag in tags)),
    )
    pattern = "*" + ("a" * 1020) + "?b*"
    assert main(["init", str(root), "--adapter", "tensorboard", "--tags", pattern]) == 2
    out = capsys.readouterr().out
    assert "ERROR: --tags pattern is too expensive. Nothing was written." in out
    assert not (root / MANIFEST_FILENAME).exists()


def test_cli_volatile_refuses_an_empty_event_file(tmp_path, capsys):
    root = tmp_path / "experiment"
    _write(root / "logs" / "empty.tfevents", b"")
    assert (
        main(
            [
                "init",
                str(root),
                "--adapter",
                "tensorboard",
                "--volatile",
                "*.tfevents*",
            ]
        )
        == 2
    )
    out = capsys.readouterr().out
    assert "an event artifact cannot be volatile; nothing was written" in out
    assert not (root / MANIFEST_FILENAME).exists()


def test_real_tensorboard_fixtures_in_one_directory_share_a_run(tmp_path):
    data = Path(__file__).resolve().parents[3] / "tests" / "data" / "tensorboard"
    root = tmp_path / "experiment"
    (root / "logs").mkdir(parents=True)
    for name in ("legacy-simple-value.tfevents", "tensor-content.tfevents"):
        (root / "logs" / name).write_bytes((data / name).read_bytes())
    manifest = _extract(root)
    assert [run.name for run in manifest.runs] == ["logs"]
    assert {artifact.path for artifact in manifest.artifacts} == {
        "logs/legacy-simple-value.tfevents",
        "logs/tensor-content.tfevents",
    }
    assert manifest.results


def test_discovery_order_cannot_change_ids_names_or_output(tmp_path):
    root = tmp_path / "experiment"
    _write(root / "logs" / "a.tfevents", _records(_scalar_event(b"loss", 1.0, step=1)))
    _write(root / "logs" / "z.tfevents", _records(_scalar_event(b"loss", 2.0, step=1)))
    adapter = TensorboardAdapter()
    discovery = adapter.discover(root)
    first = adapter.extract(
        root, discovery, project_id="p", project_name="P"
    ).model_dump()
    discovery.files.reverse()
    second = adapter.extract(
        root, discovery, project_id="p", project_name="P"
    ).model_dump()
    assert first == second


def test_sibling_insertion_makes_a_bare_name_qualified_without_changing_ids(tmp_path):
    root = tmp_path / "experiment"
    _write(root / "logs" / "z.tfevents", _records(_scalar_event(b"loss", 1.5, step=1)))
    before = _extract(root)
    assert [result.name for result in before.results] == ["loss @ 1"]
    old_id = before.results[0].id
    old_artifact = before.results[0].artifact_id

    _write(root / "logs" / "a.tfevents", _records(_scalar_event(b"loss", 0.5, step=1)))
    after = _extract(root)
    assert [run.name for run in after.runs] == ["logs"]
    assert [result.name for result in after.results] == [
        f"loss @ 1 [a.tfevents] #{_name_digest(after.results[0].artifact_id, 0)}",
        f"loss @ 1 [z.tfevents] #{_name_digest(old_artifact, 0)}",
    ]
    z_result = next(result for result in after.results if result.artifact_id == old_artifact)
    assert z_result.id == old_id
    assert z_result.value == 1.5


def test_cli_tags_repeatable_or_and_leading_dash(tmp_path, capsys):
    root = _two_tag_folder(tmp_path)
    assert (
        main(
            [
                "init",
                str(root),
                "--adapter",
                "tensorboard",
                "--tags",
                "loss",
                "--tags=-acc",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "WARNING: --tags pattern '-acc'" in out
    document = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert [result["locator"]["tag"] for result in document["results"]] == ["loss"]


def test_result_cap_counts_only_selected_scalars(tmp_path):
    root = tmp_path / "experiment"
    records = [_scalar_event(b"skip", index * 0.5, step=index) for index in range(10_001)]
    records.append(_scalar_event(b"keep", 1.0))
    _write(root / "events.out.tfevents.1", _records(*records))

    with pytest.raises(AdapterError) as excinfo:
        _extract(root)
    assert str(excinfo.value) == BUDGET_MESSAGE

    manifest = _extract(root, TagFilter(["keep"]))
    assert [result.locator.tag for result in manifest.results] == ["keep"]


def test_crafted_delimiter_tags_never_collide(tmp_path):
    root = tmp_path / "experiment"
    a = "logs/q.tfevents] @ 5 [r.tfevents"
    b = "logs/r.tfevents"
    _write(
        root / a,
        _records(
            _scalar_event(b"loss", 1.0, step=1),
            _scalar_event(b"loss @ 1 [q.tfevents]", 5.0, step=5),
        ),
    )
    _write(
        root / b,
        _records(
            _scalar_event(b"loss", 2.0, step=1),
            _scalar_event(b"loss @ 1 [q.tfevents]", 6.0, step=5),
        ),
    )
    manifest = _extract(root)
    assert len(manifest.results) == 4
    assert len({result.name for result in manifest.results}) == 4
    assert len({result.id for result in manifest.results}) == 4


def test_exact_result_and_tag_byte_boundaries(tmp_path):
    root = tmp_path / "experiment"
    ten_thousand = [
        _scalar_event(f"tag{i}".encode(), float(i), step=i) for i in range(10_000)
    ]
    _write(root / "ten-k.tfevents", _records(*ten_thousand))
    manifest = _extract(root)
    assert len(manifest.results) == 10_000

    exact = [
        _scalar_event(("x" * 4092 + f"{i:04d}").encode(), 1.0, step=i)
        for i in range(4096)
    ]
    _write(root / "exact-16mib.tfevents", _records(*exact))
    manifest = _extract(root, TagFilter(["x*"]))
    assert len(manifest.results) == 4096

    extra = [*exact, _scalar_event(b"y", 1.0, step=9999)]
    _write(root / "over-16mib.tfevents", _records(*extra))
    with pytest.raises(AdapterError) as excinfo:
        _extract(root, TagFilter(["x*", "y"]))
    assert str(excinfo.value) == BUDGET_MESSAGE


def test_surrogate_path_outranks_a_posix_backslash_event_path(tmp_path):
    adapter = TensorboardAdapter()
    discovery = adapter.discover(tmp_path)
    discovery.files = ["logs/\udcff.tfevents", "logs/a\\b.tfevents"]
    with pytest.raises(AdapterError) as excinfo:
        adapter.extract(tmp_path, discovery, project_id="p", project_name="P")
    assert str(excinfo.value) == adapter_module.SURROGATE_MESSAGE


@pytest.mark.skipif(adapter_module.os.name == "nt", reason="POSIX-only path rule")
def test_backslash_path_outranks_a_corrupt_file_without_reading_it(tmp_path):
    root = tmp_path / "experiment"
    _write(root / "logs" / "broken.tfevents", b"not an event file")
    adapter = TensorboardAdapter()
    discovery = adapter.discover(root)
    discovery.files = ["logs/a\\b.tfevents", "logs/broken.tfevents"]
    with pytest.raises(AdapterError) as excinfo:
        adapter.extract(root, discovery, project_id="p", project_name="P")
    assert str(excinfo.value) == adapter_module.BACKSLASH_MESSAGE


def test_reinit_produces_byte_identical_manifests(tmp_path, capsys):
    root = _two_tag_folder(tmp_path)
    assert main(["init", str(root), "--adapter", "tensorboard", "--tags", "loss"]) == 0
    capsys.readouterr()
    first = (root / MANIFEST_FILENAME).read_bytes()
    (root / MANIFEST_FILENAME).unlink()
    assert main(["init", str(root), "--adapter", "tensorboard", "--tags", "loss"]) == 0
    capsys.readouterr()
    assert (root / MANIFEST_FILENAME).read_bytes() == first


@pytest.mark.skipif(adapter_module.os.name == "nt", reason="POSIX-only path rule")
def test_cli_refuses_a_posix_event_path_with_a_backslash(tmp_path, capsys):
    root = tmp_path / "experiment"
    _write(root / "logs" / "a\\b.tfevents", _records(_scalar_event(b"loss", 0.5)))
    assert main(["init", str(root), "--adapter", "tensorboard"]) == 2
    out = capsys.readouterr().out
    assert "an event path contains a backslash; nothing was written" in out
    assert not (root / MANIFEST_FILENAME).exists()


@pytest.mark.skipif(adapter_module.os.name == "nt", reason="POSIX-only surrogate path")
def test_cli_refuses_a_surrogate_path(tmp_path, capsys):
    root = tmp_path / "experiment"
    (root / "logs").mkdir(parents=True)
    name = adapter_module.os.fsdecode(b"bad-\xff.tfevents")
    (root / "logs" / name).write_bytes(_records(_scalar_event(b"loss", 0.5)))
    assert main(["init", str(root), "--adapter", "tensorboard"]) == 2
    out = capsys.readouterr().out
    assert "a path is not valid UTF-8; nothing was written" in out
    assert not (root / MANIFEST_FILENAME).exists()


def test_delete_then_add_keeps_a_bare_name_but_changes_the_id(tmp_path):
    root = tmp_path / "experiment"
    _write(root / "logs" / "z.tfevents", _records(_scalar_event(b"loss", 1.0, step=1)))
    before = _extract(root)
    assert [result.name for result in before.results] == ["loss @ 1"]
    old_id = before.results[0].id

    (root / "logs" / "z.tfevents").unlink()
    _write(root / "logs" / "a.tfevents", _records(_scalar_event(b"loss", 2.0, step=1)))
    after = _extract(root)
    assert [result.name for result in after.results] == ["loss @ 1"]
    assert after.results[0].id != old_id
    assert after.results[0].value == 2.0


def test_nul_in_a_tag_keeps_the_id_payload_unambiguous(tmp_path):
    root = tmp_path / "experiment"
    _write(
        root / "events.out.tfevents.1",
        _records(
            _scalar_event(b"loss", 1.0, step=1),
            _scalar_event(b"loss\x00raw", 2.0, step=1),
        ),
    )
    manifest = _extract(root)
    assert [result.locator.tag for result in manifest.results] == ["loss", "loss\x00raw"]
    assert len({result.id for result in manifest.results}) == 2
    assert len({result.name for result in manifest.results}) == 2


def test_non_ascii_directory_names_are_recorded_verbatim(tmp_path):
    root = tmp_path / "experiment"
    _write(root / "lögs" / "e.tfevents", _records(_scalar_event(b"loss", 0.5, step=1)))
    manifest = _extract(root)
    assert [run.name for run in manifest.runs] == ["lögs"]
    assert manifest.runs[0].id == _run_id("lögs")


def test_absolute_root_does_not_change_ids_names_or_digests(tmp_path):
    first_root = tmp_path / "one" / "experiment"
    second_root = tmp_path / "two" / "experiment"
    for root in (first_root, second_root):
        _write(root / "logs" / "a.tfevents", _records(_scalar_event(b"loss", 0.5, step=1)))
    first = _extract(first_root).model_dump()
    second = _extract(second_root).model_dump()
    assert first == second


# --- remaining RFC vectors --------------------------------------------------


def test_volatile_predicate_ignores_directory_artifacts():
    directory = Manifest05(
        schema_version=SCHEMA_VERSION_0_5,
        project=Project(id="p", name="P"),
        runs=[],
        artifacts=[
            Artifact(
                id="d",
                path="x.tfevents",
                artifact_type="directory",
                digest_format="runevidence-directory-v1",
            )
        ],
        results=[],
        evidence=[],
    )
    assert not _event_artifact_is_volatile(directory, ["x.tfevents"])

    file_artifact = Manifest05(
        schema_version=SCHEMA_VERSION_0_5,
        project=Project(id="p", name="P"),
        runs=[Run(id="r", name=".")],
        artifacts=[Artifact(id="f", path="x.tfevents", sha256="a" * 64, size_bytes=1)],
        results=[],
        evidence=[],
    )
    assert _event_artifact_is_volatile(file_artifact, ["x.tfevents"])


def test_signature_failure_maps_to_the_contract_error(monkeypatch, tmp_path, capsys):
    class BrokenSignature:
        name = "fake"
        supports_tag_filters = True
        extract = 42

    from runevidence import cli as cli_module

    monkeypatch.setattr(cli_module, "get_adapter", lambda name: BrokenSignature())
    root = _two_tag_folder(tmp_path)
    assert main(["init", str(root), "--adapter", "fake", "--tags", "loss"]) == 2
    out = capsys.readouterr().out
    assert "ERROR: adapter 'fake' cannot apply --tags. Nothing was written." in out


def test_adapter_internal_type_error_is_not_mislabeled(monkeypatch, tmp_path):
    class InternalTypeError:
        name = "fake"
        supports_tag_filters = True

        def discover(self, root):
            return adapter_module.Discovery(root=root, files=[])

        def extract(self, root, discovery, *, project_id, project_name, tag_filter=None):
            raise TypeError("adapter bug")

    from runevidence import cli as cli_module

    monkeypatch.setattr(cli_module, "get_adapter", lambda name: InternalTypeError())
    root = _two_tag_folder(tmp_path)
    with pytest.raises(TypeError, match="adapter bug"):
        main(["init", str(root), "--adapter", "fake", "--tags", "loss"])


def test_restart_fixture_passes_compare_matrix_and_leaderboard(tmp_path, capsys):
    root = tmp_path / "experiment"
    for run, base in (("trainA", 1.0), ("trainB", 2.0)):
        for name, delta in (("a.tfevents", 0.0), ("z.tfevents", 0.5)):
            _write(
                root / run / name,
                _records(_scalar_event(b"loss", base + delta, step=1)),
            )
    assert main(["init", str(root), "--adapter", "tensorboard"]) == 0
    capsys.readouterr()
    document = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    runs = {run["name"]: run["id"] for run in document["runs"]}
    metric = document["results"][0]["name"]
    assert main(["compare", "--base", runs["trainA"], "--target", runs["trainB"], str(root)]) == 0
    capsys.readouterr()
    assert main(["matrix", str(root)]) == 0
    capsys.readouterr()
    assert main(["leaderboard", "--metric", metric, "--direction", "min", str(root)]) == 0


def test_cross_run_qualified_names_do_not_align_on_different_paths(tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    for root in (first, second):
        _write(root / "trainA" / "e.tfevents", _records(_scalar_event(b"loss", 1.0, step=1)))
        _write(root / "trainA" / "z.tfevents", _records(_scalar_event(b"loss", 2.0, step=1)))
    first_names = {result.name for result in _extract(first).results}
    second_names = {result.name for result in _extract(second).results}
    assert first_names == second_names  # same relative paths align

    other = tmp_path / "three"
    _write(other / "trainB" / "e.tfevents", _records(_scalar_event(b"loss", 1.0, step=1)))
    _write(other / "trainB" / "z.tfevents", _records(_scalar_event(b"loss", 2.0, step=1)))
    other_names = {result.name for result in _extract(other).results}
    assert first_names.isdisjoint(other_names)


def test_a_directory_named_tfevents_is_not_an_event_file(tmp_path):
    root = tmp_path / "experiment"
    _write(root / "x.tfevents" / "notes.txt", b"not an event file\n")
    manifest = _extract(root)
    assert manifest.runs == []
    assert [artifact.path for artifact in manifest.artifacts] == ["x.tfevents/notes.txt"]

    _write(root / "x.tfevents" / "e.tfevents", _records(_scalar_event(b"loss", 0.5)))
    manifest = _extract(root)
    assert [run.name for run in manifest.runs] == ["x.tfevents"]


def test_result_cap_does_not_outrank_a_profile_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter_module, "MAX_RESULTS", 1)
    root = tmp_path / "experiment"
    _write(
        root / "a.tfevents",
        _records(
            _scalar_event(b"loss", 1.0, step=1),
            _scalar_event(b"loss", 2.0, step=2),
        ),
    )
    _write(root / "z.tfevents", _record(_scalar_event(b"loss", 3.0))[:-3])
    with pytest.raises(AdapterError) as excinfo:
        _extract(root)
    assert str(excinfo.value) == PROFILE_MESSAGE


REVIEW_PATTERN = "*" + ("a" * 1020) + "?b*"
REVIEW_TAGS = tuple(("a" * 4095) + ch for ch in "cdefghijklmnopq")


def test_budget_accumulates_across_files_with_repeated_pattern(tmp_path):
    root = tmp_path / "experiment"
    for index in range(3):
        chunk = REVIEW_TAGS[index * 5 : (index + 1) * 5]
        _write(
            root / f"e{index}.tfevents",
            _records(*(_scalar_event(tag.encode(), 1.0, step=0) for tag in chunk)),
        )
    manifest = _extract(root, TagFilter([REVIEW_PATTERN, REVIEW_PATTERN]))
    assert manifest.results == []

    _write(
        root / "e3.tfevents",
        _records(_scalar_event(("a" * 4095 + "r").encode(), 1.0, step=0)),
    )
    with pytest.raises(TagPatternTooExpensive):
        _extract(root, TagFilter([REVIEW_PATTERN, REVIEW_PATTERN]))


def test_real_fixture_twice_in_one_directory_gets_qualified_names(tmp_path):
    data = Path(__file__).resolve().parents[3] / "tests" / "data" / "tensorboard"
    root = tmp_path / "experiment"
    (root / "logs").mkdir(parents=True)
    original = (data / "legacy-simple-value.tfevents").read_bytes()
    (root / "logs" / "a.tfevents").write_bytes(original)
    (root / "logs" / "z.tfevents").write_bytes(original)
    manifest = _extract(root)
    assert [run.name for run in manifest.runs] == ["logs"]
    assert manifest.results
    assert all(" [" in result.name and " #" in result.name for result in manifest.results)
    assert len({result.name for result in manifest.results}) == len(manifest.results)


def _reference_matches(pattern: str, tag: str) -> bool:
    tokens = []
    escaped = False
    for char in pattern:
        if escaped:
            tokens.append(re.escape(char))
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "*":
            tokens.append(".*")
        elif char == "?":
            tokens.append(".")
        else:
            tokens.append(re.escape(char))
    if escaped:
        raise ValueError("trailing backslash")
    return re.fullmatch("".join(tokens), tag, re.DOTALL) is not None


def test_matcher_agrees_with_a_reference_implementation():
    alphabet = ["a", "b", "*", "?", "\\"]
    tags = ["a", "b", "ab", "ba", "aab", "abb", "aba", "bbb", "a?b", "a*b"]
    for length in (1, 2, 3):
        for parts in itertools.product(alphabet, repeat=length):
            pattern = "".join(parts)
            if pattern.endswith("\\"):
                continue
            try:
                tag_filter = TagFilter([pattern])
            except InvalidTagPattern:
                continue
            for tag in tags:
                assert tag_filter.matches(tag) == _reference_matches(pattern, tag), (
                    pattern,
                    tag,
                )


def test_capable_adapter_receives_one_tag_filter(monkeypatch, tmp_path):
    class Recording:
        name = "record"
        supports_tag_filters = True

        def __init__(self):
            self.generic = GenericAdapter()
            self.seen = "unset"

        def discover(self, root):
            return self.generic.discover(root)

        def extract(self, root, discovery, *, project_id, project_name, tag_filter=None):
            self.seen = tag_filter
            return self.generic.extract(
                root, discovery, project_id=project_id, project_name=project_name
            )

    from runevidence import cli as cli_module

    adapter = Recording()
    monkeypatch.setattr(cli_module, "get_adapter", lambda name: adapter)
    root = tmp_path / "experiment"
    _write(root / "notes.txt", b"x\n")
    assert main(["init", str(root), "--adapter", "record", "--tags", "*"]) == 0
    assert isinstance(adapter.seen, TagFilter)


def test_legacy_adapter_is_called_without_a_tag_filter(monkeypatch, tmp_path):
    class Legacy:
        name = "legacy"
        supports_tag_filters = False

        def __init__(self):
            self.generic = GenericAdapter()
            self.called = False

        def discover(self, root):
            return self.generic.discover(root)

        def extract(self, root, discovery, *, project_id, project_name):
            self.called = True
            return self.generic.extract(
                root, discovery, project_id=project_id, project_name=project_name
            )

    from runevidence import cli as cli_module

    adapter = Legacy()
    monkeypatch.setattr(cli_module, "get_adapter", lambda name: adapter)
    root = tmp_path / "experiment"
    _write(root / "notes.txt", b"x\n")
    assert main(["init", str(root), "--adapter", "legacy"]) == 0
    assert adapter.called


def test_loading_with_a_core_without_the_tag_filter_fails():
    code = (
        "import sys\n"
        "sys.modules['runevidence.tag_filter'] = None\n"
        "import runevidence_tensorboard.adapter\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    assert result.returncode != 0
    assert "tag_filter" in result.stderr


def test_cli_warning_order_ascii_escaping_and_repeats(tmp_path, capsys):
    root = _two_tag_folder(tmp_path)
    patterns = ["nomatch'one", "loss", "nomatch'one", "é?"]
    argv = ["init", str(root), "--adapter", "tensorboard"]
    for pattern in patterns:
        argv += ["--tags", pattern]
    assert main(argv) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("WARNING:")]
    assert len(lines) == 3
    assert '"nomatch\'one"' in lines[0]
    assert '"nomatch\'one"' in lines[1]
    assert "'\\xe9?'" in lines[2]


# --- string tensors (schema 0.6) -------------------------------------------


def _string_folder(tmp_path):
    root = tmp_path / "experiment"
    _write(
        root / "logs" / "e.tfevents",
        _records(
            _scalar_event(b"loss", 0.5, step=1),
            _string_event(b"config", b"lr=3e-4\nbatch=64", step=0),
        ),
    )
    return root


def test_strings_are_ignored_without_the_flag(tmp_path, capsys):
    root = _string_folder(tmp_path)
    assert main(["init", str(root), "--adapter", "tensorboard"]) == 0
    capsys.readouterr()
    document = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert document["schema_version"] == "0.5"
    assert [r["locator"]["kind"] for r in document["results"]] == ["tensorboard"]


def test_include_strings_writes_schema_06_and_validates(tmp_path, capsys):
    root = _string_folder(tmp_path)
    assert (
        main(["init", str(root), "--adapter", "tensorboard", "--include-strings"]) == 0
    )
    capsys.readouterr()
    document = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert document["schema_version"] == "0.6"
    text = [r for r in document["results"] if r["locator"]["kind"] == "tensorboard-string"]
    assert len(text) == 1
    assert text[0]["name"] == "config @ 0 (text)"
    assert text[0]["value"] == "lr=3e-4\nbatch=64"
    assert text[0]["id"].startswith("tb-res-")
    assert main(["validate", str(root)]) == 0


def test_text_value_mismatch_is_result_value_mismatch(tmp_path, capsys):
    root = _string_folder(tmp_path)
    assert (
        main(["init", str(root), "--adapter", "tensorboard", "--include-strings"]) == 0
    )
    capsys.readouterr()
    path = root / MANIFEST_FILENAME
    document = json.loads(path.read_text(encoding="utf-8"))
    for result in document["results"]:
        if result["locator"]["kind"] == "tensorboard-string":
            result["value"] = "different"
    path.write_text(json.dumps(document), encoding="utf-8")
    assert main(["validate", str(root)]) == 1
    out = capsys.readouterr().out
    assert "result-value-mismatch" in out or "does not match the recorded value" in out


def test_nonconforming_string_tensors_are_skipped(tmp_path, capsys):
    root = tmp_path / "experiment"
    rank2_shape = _bytes_field(2, _bytes_field(2, _varint_field(2, 2)))  # dim size 2
    typed = _float_field(5, 1.5)
    _write(
        root / "e.tfevents",
        _records(
            _event(summary_bytes=_summary(_value(b"rank2", tensor_bytes=_string_tensor(b"x", shape=rank2_shape)))),
            _event(summary_bytes=_summary(_value(b"typed", tensor_bytes=_string_tensor(b"x", extra=typed)))),
            _event(summary_bytes=_summary(_value(b"two", tensor_bytes=_varint_field(1, 7) + _bytes_field(8, b"a") + _bytes_field(8, b"b")))),
            _event(summary_bytes=_summary(_value(b"content", tensor_bytes=_varint_field(1, 7) + _bytes_field(4, b"x")))),
            _string_event(b"good", b"ok"),
        ),
    )
    assert (
        main(["init", str(root), "--adapter", "tensorboard", "--include-strings"]) == 0
    )
    capsys.readouterr()
    document = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert [r["locator"]["tag"] for r in document["results"]] == ["good"]
    assert main(["validate", str(root)]) == 0


def test_text_tag_filter_and_no_match_warning(tmp_path, capsys):
    root = _string_folder(tmp_path)
    assert (
        main(
            [
                "init",
                str(root),
                "--adapter",
                "tensorboard",
                "--include-strings",
                "--tags",
                "config",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "WARNING" not in out
    document = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert [r["locator"]["kind"] for r in document["results"]] == ["tensorboard-string"]


def test_text_display_is_truncated_in_human_output(tmp_path, capsys):
    root = tmp_path / "experiment"
    long_text = "é" * 200
    _write(
        root / "train" / "a.tfevents",
        _records(_string_event(b"cfg", long_text.encode("utf-8"))),
    )
    _write(
        root / "train" / "z.tfevents",
        _records(_string_event(b"cfg", long_text.encode("utf-8"))),
    )
    assert (
        main(["init", str(root), "--adapter", "tensorboard", "--include-strings"]) == 0
    )
    capsys.readouterr()
    assert main(["matrix", str(root)]) == 0
    out = capsys.readouterr().out
    assert "..." in out
    assert long_text not in out
    assert main(["matrix", str(root), "--json"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert any(
        cell.get("value") == long_text
        for row in document["runs"]
        for cell in row["values"]
    )


def test_schema_06_text_locator_requires_artifact_id(tmp_path, capsys):
    root = _string_folder(tmp_path)
    assert (
        main(["init", str(root), "--adapter", "tensorboard", "--include-strings"]) == 0
    )
    path = root / MANIFEST_FILENAME
    document = json.loads(path.read_text(encoding="utf-8"))
    for result in document["results"]:
        if result["locator"]["kind"] == "tensorboard-string":
            result["artifact_id"] = None
    path.write_text(json.dumps(document), encoding="utf-8")
    assert main(["validate", str(root)]) == 2
    assert "schema-invalid" in capsys.readouterr().out


def test_migrate_05_to_06_is_identity(tmp_path, capsys):
    root = _string_folder(tmp_path)
    assert main(["init", str(root), "--adapter", "tensorboard"]) == 0
    capsys.readouterr()
    path = root / MANIFEST_FILENAME
    before = json.loads(path.read_text(encoding="utf-8"))
    assert main(["migrate", str(root), "--to", "0.6"]) == 0
    after = json.loads(path.read_text(encoding="utf-8"))
    assert after["schema_version"] == "0.6"
    before["schema_version"] = "0.6"
    assert before == after


def test_include_strings_unsupported_adapter_is_refused(tmp_path, capsys):
    root = _string_folder(tmp_path)
    assert (
        main(["init", str(root), "--adapter", "generic", "--include-strings"]) == 2
    )
    out = capsys.readouterr().out
    assert (
        "ERROR: adapter 'generic' does not support --include-strings. Nothing was written."
        in out
    )
    assert not (root / MANIFEST_FILENAME).exists()


def test_migrate_06_to_05_is_a_downgrade(tmp_path, capsys):
    root = _string_folder(tmp_path)
    assert (
        main(["init", str(root), "--adapter", "tensorboard", "--include-strings"]) == 0
    )
    capsys.readouterr()
    assert main(["migrate", str(root), "--to", "0.5"]) == 2
    assert "DOWNGRADE_UNSUPPORTED" in capsys.readouterr().out


def test_string_only_log_warns_as_scalar_without_the_flag(tmp_path, capsys):
    root = tmp_path / "experiment"
    _write(
        root / "e.tfevents",
        _records(_string_event(b"config", b"x", step=0)),
    )
    assert (
        main(["init", str(root), "--adapter", "tensorboard", "--tags", "config"]) == 0
    )
    out = capsys.readouterr().out
    assert "matches no representable supported scalar tag" in out
    document = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert document["schema_version"] == "0.5"
    assert document["results"] == []


def test_string_profile_wire_and_size_edges(tmp_path, capsys):
    root = tmp_path / "experiment"
    _write(
        root / "a.tfevents",
        _records(
            _string_event(b"ok64k", b"a" * 65536),
            _string_event(b"too_big", b"a" * 65537),
            _event(
                summary_bytes=_summary(
                    _value(
                        b"typed7",
                        tensor_bytes=_string_tensor(b"x", extra=_varint_field(7, 1)),
                    )
                )
            ),
        ),
    )
    assert (
        main(["init", str(root), "--adapter", "tensorboard", "--include-strings"]) == 0
    )
    capsys.readouterr()
    document = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert [r["locator"]["tag"] for r in document["results"]] == ["ok64k"]

    _write(
        root / "b.tfevents",
        _records(
            _event(
                summary_bytes=_summary(
                    _value(
                        b"bad_wire",
                        tensor_bytes=_string_tensor(b"x", extra=_key(10, 3)),
                    )
                )
            )
        ),
    )
    (root / MANIFEST_FILENAME).unlink()
    assert (
        main(["init", str(root), "--adapter", "tensorboard", "--include-strings"]) == 2
    )


def test_text_only_event_file_is_volatile_refused(tmp_path, capsys):
    root = tmp_path / "experiment"
    _write(root / "logs" / "e.tfevents", _records(_string_event(b"cfg", b"x")))
    assert (
        main(
            [
                "init",
                str(root),
                "--adapter",
                "tensorboard",
                "--include-strings",
                "--volatile",
                "*.tfevents*",
            ]
        )
        == 2
    )
    assert "an event artifact cannot be volatile" in capsys.readouterr().out


def test_init_force_refuses_an_existing_06_manifest(tmp_path, capsys):
    root = _string_folder(tmp_path)
    assert (
        main(["init", str(root), "--adapter", "tensorboard", "--include-strings"]) == 0
    )
    capsys.readouterr()
    assert (
        main(
            [
                "init",
                str(root),
                "--adapter",
                "tensorboard",
                "--include-strings",
                "--force",
            ]
        )
        == 2
    )
    assert "declares schema 0.6" in capsys.readouterr().out


def test_add_accepts_06_and_rehash_refuses_text_locators(tmp_path, capsys):
    root = _string_folder(tmp_path)
    assert (
        main(["init", str(root), "--adapter", "tensorboard", "--include-strings"]) == 0
    )
    capsys.readouterr()
    _write(root / "notes.txt", b"note\n")
    assert main(["add", str(root), str(root / "notes.txt")]) == 0
    capsys.readouterr()
    document = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert document["schema_version"] == "0.6"
    assert main(["rehash", str(root), "--all"]) == 2
    out = capsys.readouterr().out
    assert "LOCATOR_REFRESH_UNSUPPORTED" in out


def test_text_truncation_boundary_is_120_code_points(tmp_path, capsys):
    for length, shortened in ((120, False), (121, True)):
        root = tmp_path / f"exp{length}"
        text = "a" * length
        _write(
            root / "train" / "e.tfevents",
            _records(_string_event(b"cfg", text.encode())),
        )
        assert (
            main(["init", str(root), "--adapter", "tensorboard", "--include-strings"])
            == 0
        )
        capsys.readouterr()
        document = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        run_id = document["runs"][0]["id"]
        assert main(["run", "--id", run_id, str(root)]) == 0
        out = capsys.readouterr().out
        if shortened:
            assert "..." in out
            assert text not in out
        else:
            assert text in out


def test_06_tensorboard_cap_uses_the_value_wording(tmp_path, capsys, monkeypatch):
    import runevidence.checks as checks_module

    root = _string_folder(tmp_path)
    assert (
        main(["init", str(root), "--adapter", "tensorboard", "--include-strings"]) == 0
    )
    capsys.readouterr()
    document = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    artifact_id = document["artifacts"][0]["id"]
    monkeypatch.setattr(
        checks_module,
        "_tensorboard_cap",
        lambda manifest: (True, frozenset({artifact_id})),
    )
    assert main(["validate", str(root)]) == 1
    out = capsys.readouterr().out
    assert "no TensorBoard value was read" in out
