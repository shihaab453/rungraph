"""Directory scanner: native filesystem tests and injected race tests.

``test_native_*`` tests read real folders under pytest's tmp_path on this
machine. They skip, with the reason, where the platform or the user's
privileges cannot create the case. ``test_injected_*`` tests drive the scanner
through a fake in-memory backend to simulate races; they check the scanner's
decisions, not the operating system, and are not evidence of native safety.
"""

from __future__ import annotations

import argparse
import builtins
import errno
import hashlib
import inspect
import json
import os
import re
import socket
import stat
import subprocess
import sys
import threading
from dataclasses import asdict
from pathlib import Path

import pytest

import runevidence
import runevidence.directory_scanner as ds
from conftest import EXAMPLE, REPO, make_dir_link, remove_dir_link
from runevidence.directory_hashing import DirectoryLimits, encode_directory
from runevidence.directory_scanner import DirectoryScanError, native_scan_support, scan_directory


def test_windows_record_parser_bounds():
    import ctypes
    import struct
    import runevidence.directory_scanner as ds

    class Info(ctypes.Structure):
        _fields_ = [("NextEntryOffset", ctypes.c_uint32), ("FileIndex", ctypes.c_uint32),
                    ("times_and_sizes", ctypes.c_byte * 48), ("FileAttributes", ctypes.c_uint32),
                    ("FileNameLength", ctypes.c_uint32), ("EaSize", ctypes.c_uint32),
                    ("FileId", ctypes.c_int64)]

    def record(name=b"a\x00", next_offset=0):
        size = max(80 + len(name), next_offset)
        data = bytearray(size)
        struct.pack_into("<I", data, 0, next_offset)
        struct.pack_into("<I", data, 60, len(name))
        data[80:80 + len(name)] = name
        return bytes(data)

    parse = lambda data, length: ds._parse_windows_directory_records(data, length, Info, 80)
    valid = record()
    assert parse(valid, len(valid))[0][0] == "a"
    chain = record(next_offset=88) + record(b"b\x00")
    assert [r[0] for r in parse(chain, len(chain))] == ["a", "b"]
    malformed = [(valid, 0), (valid, len(valid) + 1), (valid[:79], 79),
                 (record(b""), 80), (record(b"x"), 81),
                 (record(b"\x00\xd8"), 82), (record(next_offset=81), 83),
                 (record(next_offset=80), 82), (record(next_offset=88), 88),
                 (record(next_offset=160), 160)]
    truncated = bytearray(valid)
    struct.pack_into("<I", truncated, 60, 4)
    malformed.append((bytes(truncated), len(truncated)))
    for data, length in malformed:
        with pytest.raises(ds._Failure):
            parse(data, length)


def test_posix_and_unknown_platforms_fail_closed(tmp_path, monkeypatch):
    import runevidence.directory_scanner as ds

    root = tmp_path / "root"
    root.mkdir()
    (root / "a").write_bytes(b"x")
    if sys.platform.startswith("linux"):
        reason = _release_root_gate_reason(root)
        if reason:
            pytest.skip(reason)
        if not FACILITIES:
            pytest.skip(f"native POSIX scanner facilities unavailable: {SUPPORT.reason}")
        # S3a: ext4 is release-qualified, so the production default policy now
        # scans a real ext4 root without the private qualification seam.
        assert scan_directory(root).digest.members == 1
        with ds._release_policy_for_tests():
            assert ds._SCAN_POLICY.get() is ds._RELEASE_POLICY
            assert scan_directory(root).digest.members == 1
    for platform in ("darwin", "unknown"):
        monkeypatch.setattr(ds.sys, "platform", platform)
        assert not ds.native_scan_support().supported
        with pytest.raises(DirectoryScanError) as error:
            ds.scan_directory(root)
        assert error.value.reason == "unsupported-platform"


def test_native_large_directory_enumeration(tmp_path):
    if not NATIVE_READY:
        pytest.skip(f"no native scanner facilities here: {SUPPORT.reason}")
    root = tmp_path / "root"
    root.mkdir()
    for index in range(900):
        (root / f"entry-{index:04d}-{'x' * 80}.txt").write_bytes(b"x")
    result = scan_directory(root)
    assert result.digest.members == 900
    assert result.digest.size_bytes == 900

VECTORS = json.loads(
    (Path(__file__).parent / "data" / "directory_digest_vectors.json").read_bytes().decode("utf-8")
)["vectors"]
SUPPORT = native_scan_support()
POSIX_FACILITIES: dict = {}


def _posix_facilities_ready() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    try:
        POSIX_FACILITIES.update(ds._posix_capabilities())
    except Exception:  # noqa: BLE001 - collection must not fail on a probe
        return False
    return all(POSIX_FACILITIES.get(key) for key in ds._POSIX_CAPABILITY_KEYS)


FACILITIES = _posix_facilities_ready()
NATIVE_READY = (sys.platform == "win32" and SUPPORT.supported) or FACILITIES
native = pytest.mark.skipif(not NATIVE_READY, reason=f"no native scanner facilities here: {SUPPORT.reason}")
windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only behaviour")
posix = pytest.mark.skipif(os.name != "posix", reason="POSIX-only behaviour")
linux = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux POSIX backend only")


def _native_root_gate_reason(path) -> str:
    """The ancestor-aware root-filesystem gate for real Linux scans."""
    if not sys.platform.startswith("linux") or not FACILITIES:
        return ""
    try:
        ds._preflight_mounts(ds._root_components(os.fspath(path)), ds._SCAN_POLICY.get())
    except ds.DirectoryScanError as exc:
        return f"native root filesystem gate: {exc.reason}: {exc}"
    except Exception as exc:  # noqa: BLE001 - a host gap skips, never errors setup
        return f"native root filesystem gate: {type(exc).__name__}: {exc}"
    return ""


def _release_root_gate_reason(path) -> str:
    """The ancestor-aware gate under the production release policy (S3a)."""
    if not sys.platform.startswith("linux") or not FACILITIES:
        return ""
    try:
        with ds._release_policy_for_tests():
            ds._preflight_mounts(ds._root_components(os.fspath(path)), ds._SCAN_POLICY.get())
    except ds.DirectoryScanError as exc:
        return f"release root filesystem gate: {exc.reason}: {exc}"
    except Exception as exc:  # noqa: BLE001 - a host gap skips, never errors setup
        return f"release root filesystem gate: {type(exc).__name__}: {exc}"
    return ""


@pytest.fixture(autouse=True)
def directory_scanner_test_policy():
    """Run this module under the private candidate-only qualification policy.

    The shipped runtime policy promotes ext4 (S3a) and refuses every other
    filesystem. Tests that must prove the production default enter
    ``ds._release_policy_for_tests()`` explicitly."""
    with ds._qualification_for_tests(ds.CANDIDATE_FSTYPES):
        yield


@pytest.fixture(autouse=True)
def _native_root_filesystem_gate(request, directory_scanner_test_policy):
    """Skip a real Linux scan when any ancestor is outside the effective policy."""
    if not request.node.name.startswith("test_native_"):
        return
    reason = _native_root_gate_reason(request.getfixturevalue("tmp_path"))
    if reason:
        pytest.skip(reason)
    if "committed_fixture" in request.node.name:
        reason = _native_root_gate_reason(EXAMPLE)
        if reason:
            pytest.skip(reason)



def build(root: Path, layout: list) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for entry in layout:
        target = root / entry["path"]
        if entry["type"] == "D":
            target.mkdir()
        else:
            target.write_bytes(bytes.fromhex(entry["content_hex"]))
    return root


def nested(root: Path) -> Path:
    return build(root, VECTORS[2]["layout"])


def scan_error(root, limits: DirectoryLimits = DirectoryLimits()) -> DirectoryScanError:
    with pytest.raises(DirectoryScanError) as excinfo:
        scan_directory(root, limits)
    return excinfo.value


def snapshot(root: Path) -> list:
    out = []
    for path in sorted(root.rglob("*")):
        st = path.lstat()
        content = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        out.append((path.relative_to(root).as_posix(), st.st_size, st.st_mtime_ns, content))
    return out


# --- environment and capability ---------------------------------------------


def test_capability_is_reported(record_property):
    record_property("scan_support", json.dumps(asdict(SUPPORT), sort_keys=True))
    if sys.platform == "win32":
        assert SUPPORT.supported, SUPPORT.reason
        assert SUPPORT.backend == "windows-nt-relative-open-reparse-point"
    elif sys.platform.startswith("linux"):
        missing = [key for key in ds._POSIX_CAPABILITY_KEYS if not POSIX_FACILITIES.get(key)]
        if missing:
            pytest.skip("native POSIX facilities unavailable: " + ", ".join(missing))
        assert SUPPORT.backend == "posix-openat-nofollow"
        # S3a promotes ext4, so backend/facility success now yields support.
        assert ds.RELEASE_QUALIFIED_FSTYPES == frozenset({"ext4"})
        assert SUPPORT.supported, SUPPORT.reason
        assert SUPPORT.reason == "ok"
    elif not SUPPORT.supported:
        assert SUPPORT.reason


def test_scanner_is_imported_from_this_checkout_or_an_install():
    # Running these tests against another checkout's source would make every
    # result meaningless; an installed package (site-packages) is fine.
    for module in (runevidence, ds):
        origin = Path(module.__file__).resolve()
        if "site-packages" not in origin.parts:
            assert origin.is_relative_to(REPO / "src"), origin


# --- native: digests --------------------------------------------------------


@native
@pytest.mark.parametrize("vector", VECTORS, ids=lambda v: v["name"])
def test_native_scan_reproduces_each_fixed_vector(tmp_path, vector):
    root = build(tmp_path / "root", vector["layout"])

    result = scan_directory(root)

    assert result.digest.sha256 == vector["sha256"]
    assert result.digest.size_bytes == vector["size_bytes"]
    assert encode_directory(result.records) == bytes.fromhex(vector["encoded_hex"])


@native
def test_native_moving_or_renaming_the_root_keeps_the_digest(tmp_path):
    first = scan_directory(nested(tmp_path / "a")).digest
    (tmp_path / "a").rename(tmp_path / "moved")
    (tmp_path / "deeper").mkdir()
    (tmp_path / "moved").rename(tmp_path / "deeper" / "b")

    assert scan_directory(tmp_path / "deeper" / "b").digest == first


@native
def test_native_file_bytes_are_hashed_raw(tmp_path):
    content = bytes(range(256)) + b"line\r\nline\n\x00"
    root = tmp_path / "root"
    root.mkdir()
    (root / "blob.bin").write_bytes(content)

    (record,) = scan_directory(root).records

    assert record.sha256 == hashlib.sha256(content).digest()
    assert record.size_bytes == len(content)


@native
def test_native_large_file_spans_several_read_chunks(tmp_path):
    content = os.urandom(ds._CHUNK * 2 + 17)
    root = tmp_path / "root"
    root.mkdir()
    (root / "big").write_bytes(content)

    assert scan_directory(root).records[0].sha256 == hashlib.sha256(content).digest()


@native
def test_native_member_changes_change_the_digest(tmp_path):
    root = nested(tmp_path / "root")
    digests = {scan_directory(root).digest.sha256}

    (root / "n" / "x.bin").write_bytes(b"\x00\x01\x02\xfe")
    digests.add(scan_directory(root).digest.sha256)
    (root / "n" / "x.bin").rename(root / "n" / "y.bin")
    digests.add(scan_directory(root).digest.sha256)
    (root / "another-empty").mkdir()
    digests.add(scan_directory(root).digest.sha256)
    (root / "empty").rmdir()
    digests.add(scan_directory(root).digest.sha256)

    assert len(digests) == 5


@native
def test_native_scan_does_not_modify_the_tree(tmp_path):
    root = nested(tmp_path / "root")
    before = snapshot(root)

    scan_directory(root)

    assert snapshot(root) == before


@native
def test_native_scanning_a_committed_fixture_leaves_it_untouched():
    data = EXAMPLE / "data"
    before = snapshot(data)

    result = scan_directory(data)

    assert snapshot(data) == before
    assert {r.path for r in result.records} == {p for p, *_ in before}


# --- native: root ------------------------------------------------------------


@native
def test_native_root_problems(tmp_path):
    assert scan_error(tmp_path / "absent").reason == "missing"
    assert scan_error(tmp_path / "absent" / "deeper").reason == "missing"
    (tmp_path / "file").write_bytes(b"x")
    assert scan_error(tmp_path / "file").reason == "not-a-directory"
    assert scan_error(tmp_path / "file" / "below").reason == "not-a-directory"
    assert scan_error("relative/path").reason == "invalid-root"
    assert scan_error(os.fspath(tmp_path) + os.sep + ".." + os.sep + tmp_path.name).reason == "invalid-root"


@native
def test_native_wrong_case_root_is_refused(tmp_path):
    (tmp_path / "Data").mkdir()
    (tmp_path / "Data" / "inner").mkdir()
    insensitive = (tmp_path / "data").exists()

    assert scan_error(tmp_path / "data").reason == ("unsafe" if insensitive else "missing")
    assert scan_error(tmp_path / "data" / "inner").reason == ("unsafe" if insensitive else "missing")
    scan_directory(tmp_path / "Data")


# --- native: names, collisions, limits -------------------------------------


@native
@pytest.mark.parametrize(
    "parts", [(".git",), ("venv",), ("__pycache__",), ("sub", "runevidence.json"), ("runevidence.json.bak.2",), ("caf\u00e9.txt",)]
)
def test_native_disallowed_names_are_unsafe(tmp_path, parts):
    root = tmp_path / "root"
    target = root.joinpath(*parts)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"x")

    err = scan_error(root)

    assert err.reason == "unsafe"
    assert err.path == "/".join(parts)


@native
def test_native_case_collision_where_the_filesystem_allows_it(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "A").write_bytes(b"1")
    (root / "a").write_bytes(b"2")
    if len(os.listdir(root)) == 1:
        pytest.skip("case-insensitive filesystem: 'A' and 'a' are the same file")

    assert scan_error(root).reason == "collision"


@native
def test_native_limits(tmp_path):
    root = nested(tmp_path / "root")

    assert scan_error(root, DirectoryLimits(depth=1)).reason == "limit-exceeded"
    assert scan_error(root, DirectoryLimits(members=2)).reason == "limit-exceeded"
    assert scan_error(root, DirectoryLimits(file_bytes=3)).reason == "limit-exceeded"
    assert scan_error(root, DirectoryLimits(total_file_bytes=3)).reason == "limit-exceeded"
    assert scan_error(root, DirectoryLimits(path_bytes=4)).reason == "limit-exceeded"
    assert scan_error(root, DirectoryLimits(encoded_bytes=40)).reason == "limit-exceeded"
    scan_directory(root, DirectoryLimits(depth=2, members=3, file_bytes=4, total_file_bytes=4))


# --- native: links and special files ----------------------------------------


@native
def test_native_directory_link_member_is_never_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"not part of the artifact")
    root = tmp_path / "root"
    root.mkdir()
    link = root / "linked"
    if not make_dir_link(link, outside):
        pytest.skip("this platform refused to create a directory link")
    try:
        err = scan_error(root)
    finally:
        remove_dir_link(link)

    assert (err.reason, err.path) == ("unsafe", "linked")


@native
def test_native_directory_link_as_root_or_ancestor_is_refused(tmp_path):
    real = tmp_path / "real"
    nested(real / "inner")
    link = tmp_path / "link"
    if not make_dir_link(link, real):
        pytest.skip("this platform refused to create a directory link")
    try:
        as_ancestor = scan_error(link / "inner")
        as_root = scan_error(link)
    finally:
        remove_dir_link(link)

    assert as_ancestor.reason == "unsafe"
    assert as_root.reason == "unsafe"


def _symlink_or_skip(link: Path, target: Path, **kwargs) -> None:
    try:
        os.symlink(target, link, **kwargs)
    except OSError as exc:  # WinError 1314 without Developer Mode or elevation
        pytest.skip(f"cannot create a symbolic link here: {exc}")


@native
@pytest.mark.parametrize("case", ["file", "directory", "dangling", "cyclic"])
def test_native_symbolic_link_members_are_unsafe(tmp_path, case):
    root = tmp_path / "root"
    root.mkdir()
    target = {
        "file": tmp_path / "outside-file",
        "directory": tmp_path / "outside-dir",
        "dangling": tmp_path / "does-not-exist",
        "cyclic": root / "loop",
    }[case]
    if case == "file":
        target.write_bytes(b"outside")
    if case == "directory":
        target.mkdir()
    _symlink_or_skip(root / "loop" if case == "cyclic" else root / "link", target,
                     target_is_directory=case in ("directory", "cyclic"))

    assert scan_error(root).reason == "unsafe"


@native
def test_native_hardlinked_members_are_unsafe(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"shared bytes")
    try:
        os.link(outside, root / "alias")
    except OSError as exc:
        pytest.skip(f"cannot create a hard link here: {exc}")

    err = scan_error(root)

    assert (err.reason, err.path) == ("unsafe", "alias")


@native
@posix
def test_native_fifo_is_unsafe_and_not_opened(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    try:
        os.mkfifo(root / "pipe")  # opening it for reading would block
    except OSError as exc:
        pytest.skip(f"cannot create a FIFO here: {exc}")

    assert scan_error(root).reason == "unsafe"


@native
@posix
def test_native_unix_socket_is_unsafe(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    try:
        sock = socket.socket(socket.AF_UNIX)
    except OSError as exc:
        pytest.skip(f"cannot create a Unix socket here: {exc}")
    try:
        try:
            sock.bind(os.fspath(root / "s"))
        except OSError as exc:
            pytest.skip(f"cannot bind a Unix socket here: {exc}")
        assert scan_error(root).reason == "unsafe"
    finally:
        sock.close()


# --- native: permissions and concurrent writers ------------------------------


def _deny_read(path: Path):
    """Deny reading ``path`` for the current user; returns an undo function."""
    if os.name == "nt":
        # (RD) only: read data / list folder, the right the scanner asks for.
        # Denying generic read (R) on a folder also stops icacls from undoing it.
        deny = subprocess.run(["icacls", os.fspath(path), "/deny", "*S-1-1-0:(RD)"], capture_output=True, text=True)
        if deny.returncode != 0:
            pytest.skip(f"icacls could not deny access: {deny.stdout or deny.stderr}")

        def undo():
            done = subprocess.run(["icacls", os.fspath(path), "/remove:d", "*S-1-1-0"], capture_output=True, text=True)
            assert done.returncode == 0, f"could not restore access to {path}: {done.stdout}{done.stderr}"

        return undo
    if os.geteuid() == 0:
        pytest.skip("root ignores permission bits")
    mode = path.stat().st_mode
    path.chmod(0)
    return lambda: path.chmod(stat.S_IMODE(mode))


@native
@pytest.mark.parametrize("what", ["file", "folder"])
def test_native_unreadable_member_is_unreadable(tmp_path, what):
    root = tmp_path / "root"
    root.mkdir()
    target = root / "locked"
    if what == "file":
        target.write_bytes(b"x")
    else:
        target.mkdir()
    undo = _deny_read(target)
    try:
        err = scan_error(root)
    finally:
        undo()

    assert (err.reason, err.path) == ("unreadable", "locked")


@native
@windows
def test_native_file_open_for_writing_elsewhere_is_unstable(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with open(root / "busy.log", "ab") as writer:
        writer.write(b"still being written")
        err = scan_error(root)

    assert (err.reason, err.path) == ("unstable", "busy.log")
    scan_directory(root)


@native
@windows
def test_native_held_handles_block_rename_and_write(tmp_path):
    # The guarantees the Windows backend relies on, checked against the OS.
    root = nested(tmp_path / "root")
    (tmp_path.parent / f"{tmp_path.name}-free").mkdir()
    stray = tmp_path.parent / f"{tmp_path.name}-free" / "stray"
    stray.write_bytes(b"x")
    backend = ds._native_backend()
    held, _ = backend.open_root(os.fspath(root))
    try:
        with pytest.raises(PermissionError):
            root.rename(tmp_path / "renamed")
        with pytest.raises(PermissionError):  # nothing can be moved into a held folder
            stray.rename(root / "moved-in")
        with pytest.raises(PermissionError):  # or hard-linked into one
            os.link(stray, root / "linked-in")
        with pytest.raises(PermissionError):  # an ancestor cannot be renamed either
            (tmp_path).rename(tmp_path.parent / (tmp_path.name + "-renamed"))
        # ...but other programs can still save into ancestors during a scan.
        stray.rename(tmp_path / "saved-into-ancestor")
        (tmp_path / "saved-into-ancestor").rename(stray)
        folder, _ = backend.open_dir(held[-1], "n")
        child, _ = backend.open_file(folder, "x.bin")
        try:
            with pytest.raises(PermissionError):
                open(root / "n" / "x.bin", "ab").close()
            with pytest.raises(PermissionError):
                (root / "n" / "x.bin").unlink()
            with pytest.raises(PermissionError):
                (root / "n").rename(root / "m")
        finally:
            backend.close(child)
            backend.close(folder)
    finally:
        for handle in reversed(held):
            backend.close(handle)
    root.rename(tmp_path / "renamed")


# --- native objects, injected timing: swap a member just before it is opened --


class SwapBefore:
    """The real backend, with ``action`` run once just before ``method``
    opens ``name``. The OS objects are real; only the timing is arranged."""

    def __init__(self, method: str, name: str, action, backend=None):
        self.backend = backend or ds._native_backend()
        self.name = self.backend.name
        self.trigger = (method, name)
        self.action = action
        self.reads = 0

    def __getattr__(self, attr):
        return getattr(self.backend, attr)

    def _maybe(self, method, name):
        if (method, name) == self.trigger:
            self.trigger = None
            self.action()

    def open_dir(self, handle, name):
        self._maybe("open_dir", name)
        return self.backend.open_dir(handle, name)

    def open_file(self, handle, name):
        self._maybe("open_file", name)
        return self.backend.open_file(handle, name)

    def read(self, handle, size):
        self.reads += 1
        return self.backend.read(handle, size)


def away(tmp_path: Path) -> Path:
    # Outside every folder the scan holds open: on Windows a held folder
    # refuses to be the destination of a rename.
    folder = tmp_path.parent / f"{tmp_path.name}-away"
    folder.mkdir(exist_ok=True)
    return folder


def swap_error(backend: SwapBefore, root: Path) -> DirectoryScanError:
    with pytest.raises(DirectoryScanError) as excinfo:
        ds._scan(backend, os.fspath(root), DirectoryLimits())
    return excinfo.value


@native
def test_native_folder_swapped_for_a_link_just_before_open_is_not_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"not part of the artifact")
    root = tmp_path / "root"
    (root / "d").mkdir(parents=True)
    (root / "d" / "inner").write_bytes(b"inside")
    link = root / "d"
    made = []

    def swap():
        (root / "d").rename(away(tmp_path) / "d")
        made.append(make_dir_link(link, outside))

    backend = SwapBefore("open_dir", "d", swap)
    try:
        err = swap_error(backend, root)
    finally:
        if made and made[0]:
            remove_dir_link(link)
    if not made[0]:
        pytest.skip("this platform refused to create a directory link")

    assert (err.reason, err.path) == ("unstable", "d")
    assert "became a link" in str(err)  # the link itself was opened, not its target
    assert backend.reads == 0


@native
def test_native_file_replaced_just_before_open_is_not_read(tmp_path):
    outside = tmp_path / "outside"
    outside.write_bytes(b"not part of the artifact")
    root = tmp_path / "root"
    root.mkdir()
    (root / "a").write_bytes(b"inside")
    how = []

    def swap():
        (root / "a").rename(away(tmp_path) / "a")
        try:
            os.link(outside, root / "a")  # POSIX allows this; Windows refuses it in a held folder
            how.append("hard link to an outside file")
        except OSError:
            (root / "a").write_bytes(b"different file, same name")
            how.append("new file")

    backend = SwapBefore("open_file", "a", swap)
    err = swap_error(backend, root)

    assert how
    assert (err.reason, err.path) == ("unstable", "a"), how
    assert backend.reads == 0


# --- injected: a fake backend that simulates races ---------------------------


class FakeFS:
    """In-memory tree. Handles are member paths ('' is the root). ``hooks``
    maps (method, path) to a callback run once just before that call."""

    supports_mount_identity = False  # a fake is a non-mount backend by default
    mount_id = None  # optional consistent mount id for mount-capability tests

    def __init__(self, tree: dict):
        self.nodes = {"": ["D", b"", 1, 0, 1, 7]}  # kind, content, id, mtime, links, volume
        self.next_id = 2
        for path, content in tree.items():
            self.add(path, content)
        self.hooks: dict = {}
        self.calls: list = []
        self.offsets: dict = {}
        self.mounts: dict = {}  # member path -> mount id (falls back to mount_id)
        self.name = "fake"

    def add(self, path, content, kind=None):
        kind = kind or ("D" if content is None else "F")
        self.nodes[path] = [kind, content or b"", self.next_id, 0, 1, 7]
        self.next_id += 1

    def _hook(self, method, path):
        self.calls.append((method, path))
        hook = self.hooks.pop((method, path), None)
        if hook:
            hook(self)

    def _mount(self, path):
        if not self.supports_mount_identity:
            return None
        return self.mounts.get(path, self.mount_id)

    def _info(self, path):
        kind, content, ident, mtime, links, volume = self.nodes[path]
        return ds._Info(kind, (volume, ident), len(content) if kind == "F" else 0, (mtime,), links,
                        self._mount(path))

    def _join(self, parent, name):
        return f"{parent}/{name}" if parent else name

    def open_root(self, root):
        self._hook("open_root", "")
        return [""], self._info("")

    def list(self, handle, budget):
        self._hook("list", handle)
        prefix = handle + "/" if handle else ""
        names = [p[len(prefix):] for p in self.nodes if p and p.startswith(prefix) and "/" not in p[len(prefix):]]
        if len(names) > budget:
            return None
        return [ds._Entry(n, self.nodes[prefix + n][0], (self.nodes[prefix + n][5], self.nodes[prefix + n][2]),
                          self._mount(prefix + n)) for n in names]

    def _open(self, handle, name, method):
        path = self._join(handle, name)
        self._hook(method, path)
        if path not in self.nodes:
            raise ds._Failure("not-found", "gone")
        if isinstance(self.nodes[path][0], ds._Failure):
            raise self.nodes[path][0]
        return path, self._info(path)

    def open_dir(self, handle, name):
        return self._open(handle, name, "open_dir")

    def open_file(self, handle, name):
        return self._open(handle, name, "open_file")

    def stat(self, handle):
        self._hook("stat", handle)
        return self._info(handle)

    def read(self, handle, size):
        self._hook("read", handle)
        offset = self.offsets.get(handle, 0)
        chunk = self.nodes[handle][1][offset : offset + size]
        self.offsets[handle] = offset + len(chunk)
        return chunk

    def close(self, handle):
        self.offsets.pop(handle, None)


def fake_scan(fs: FakeFS, limits: DirectoryLimits = DirectoryLimits()):
    return ds._scan(fs, "/fake", limits)


def fake_error(fs: FakeFS, limits: DirectoryLimits = DirectoryLimits()) -> DirectoryScanError:
    with pytest.raises(DirectoryScanError) as excinfo:
        fake_scan(fs, limits)
    return excinfo.value


def vector_fs(vector) -> FakeFS:
    return FakeFS({e["path"]: None if e["type"] == "D" else bytes.fromhex(e["content_hex"]) for e in vector["layout"]})


@pytest.mark.parametrize("vector", VECTORS, ids=lambda v: v["name"])
def test_injected_fake_tree_reproduces_each_vector(vector):
    assert fake_scan(vector_fs(vector)).digest.sha256 == vector["sha256"]


def test_injected_entry_swapped_for_a_link_before_open_is_never_read():
    fs = FakeFS({"a": b"data"})
    fs.hooks[("open_file", "a")] = lambda f: f.nodes["a"].__setitem__(0, "L")

    err = fake_error(fs)

    assert (err.reason, err.path) == ("unstable", "a")
    assert ("read", "a") not in fs.calls


def test_injected_entry_replaced_by_another_file_before_open():
    fs = FakeFS({"a": b"data"})
    fs.hooks[("open_file", "a")] = lambda f: f.nodes["a"].__setitem__(2, 999)

    assert fake_error(fs).reason == "unstable"


def test_injected_folder_replaced_by_a_file_before_open():
    fs = FakeFS({"d": None})
    fs.hooks[("open_dir", "d")] = lambda f: f.nodes["d"].__setitem__(0, "F")

    assert fake_error(fs).reason == "unstable"


@pytest.mark.parametrize("change", [b"data+more", b"da"])
def test_injected_file_changing_size_during_read(change):
    fs = FakeFS({"a": b"data"})
    fs.hooks[("read", "a")] = lambda f: f.nodes["a"].__setitem__(1, change)

    assert fake_error(fs).reason == "unstable"


def test_injected_file_metadata_changing_during_read():
    fs = FakeFS({"a": b"data"})
    fs.hooks[("read", "a")] = lambda f: f.nodes["a"].__setitem__(3, 5)

    assert fake_error(fs).reason == "unstable"


def test_injected_member_added_while_folder_is_read():
    fs = FakeFS({"d": None, "d/a": b"1"})
    fs.hooks[("open_file", "d/a")] = lambda f: f.add("d/b", b"2")

    err = fake_error(fs)

    assert (err.reason, err.path) == ("unstable", "d")


def test_injected_change_after_first_pass_is_caught_by_the_second():
    fs = FakeFS({"a": b"1", "b": b"2"})

    def touch_after_first_pass(f):
        f.nodes["a"][3] = 9

    fs.hooks[("open_root", "")] = lambda f: f.hooks.__setitem__(("open_root", ""), touch_after_first_pass)

    assert fake_error(fs).reason == "unstable"


def test_injected_root_replaced_between_passes():
    fs = FakeFS({"a": b"1"})
    fs.hooks[("open_root", "")] = lambda f: f.hooks.__setitem__(
        ("open_root", ""), lambda g: g.nodes[""].__setitem__(2, 4242)
    )

    assert fake_error(fs).reason == "unstable"


@pytest.mark.parametrize(
    "name,reason",
    [("CON", "unsafe"), ("lpt1.txt", "unsafe"), ("a\\b", "unsafe"), ("x:y", "unsafe"), ("trailing.", "unsafe"),
     ("trailing ", "unsafe"), ("caf\u00e9", "unsafe"), ("bad\ud800", "unsafe"), ("tab\there", "unsafe"),
     (".GIT", "unsafe"), ("RunEvidence.json", "unsafe")],
)
def test_injected_disallowed_names_are_refused_before_opening(name, reason):
    fs = FakeFS({name: b"x"})

    err = fake_error(fs)

    assert (err.reason, err.path) == (reason, name)
    assert not [c for c in fs.calls if c[0] in ("open_file", "open_dir")]


def test_injected_case_collision_is_refused_before_opening():
    fs = FakeFS({"Model.bin": b"1", "model.bin": b"2"})

    assert fake_error(fs).reason == "collision"
    assert not [c for c in fs.calls if c[0] == "open_file"]


@pytest.mark.parametrize("kind", ["L", "S"])
def test_injected_links_and_special_entries_are_never_opened(kind):
    fs = FakeFS({})
    fs.add("odd", b"", kind=kind)

    assert fake_error(fs).reason == "unsafe"
    assert ("open_file", "odd") not in fs.calls and ("open_dir", "odd") not in fs.calls


def test_injected_hardlinked_file_is_refused_before_reading():
    fs = FakeFS({"a": b"data"})
    fs.nodes["a"][4] = 2

    assert fake_error(fs).reason == "unsafe"
    assert ("read", "a") not in fs.calls


@pytest.mark.parametrize("links,reason", [(0, "unstable"), (2, "unsafe")])
def test_injected_file_with_non_single_link_count_is_refused_before_reading(links, reason, monkeypatch):
    fs = FakeFS({"a": b"data", "b": b"keep"})
    fs.nodes["a"][4] = links
    digest_calls = []
    real_digest = ds.directory_digest

    def recording_digest(records, limits):
        digest_calls.append(records)
        return real_digest(records, limits)

    monkeypatch.setattr(ds, "directory_digest", recording_digest)

    err = fake_error(fs)

    # links == 0 is an unlinked-but-open object (a race); links > 1 is the
    # hardlink policy refusal. Neither reads content or builds a digest.
    assert (err.reason, err.path) == (reason, "a")
    assert ("read", "a") not in fs.calls
    assert digest_calls == []


def test_injected_member_on_another_volume_is_unsafe():
    fs = FakeFS({"mnt": None})
    fs.nodes["mnt"][5] = 8

    assert fake_error(fs).reason == "unsafe"


@pytest.mark.parametrize(
    "code,reason", [("denied", "unreadable"), ("in-use", "unstable"), ("not-found", "unstable"), ("other", "unreadable")]
)
def test_injected_open_failures_are_classified(code, reason):
    fs = FakeFS({"a": b"data"})
    fs.nodes["a"][0] = ds._Failure(code, "simulated")  # opening it raises this
    fs.list = lambda handle, budget: [ds._Entry("a", "F", (7, fs.nodes["a"][2]))] if not handle else []

    assert fake_error(fs).reason == reason


def test_injected_tree_exactly_at_the_member_limit_scans():
    fs = FakeFS({"d": None, "d/a": b"1", "d/b": b"2"})

    assert fake_scan(fs, DirectoryLimits(members=3)).digest.members == 3


def test_injected_member_added_to_a_full_tree_is_unstable_not_a_limit():
    fs = FakeFS({"d": None, "d/a": b"1"})
    fs.hooks[("open_file", "d/a")] = lambda f: f.add("d/b", b"2")

    err = fake_error(fs, DirectoryLimits(members=2))

    assert (err.reason, err.path) == ("unstable", "d")


def test_injected_listing_is_bounded_by_the_member_budget():
    fs = FakeFS({f"m{i}": b"" for i in range(5)})
    budgets = []
    real_list = fs.list
    fs.list = lambda handle, budget: budgets.append(budget) or real_list(handle, budget)

    assert fake_error(fs, DirectoryLimits(members=4)).reason == "limit-exceeded"
    assert budgets == [4]


def test_unsupported_platform_fails_closed(monkeypatch):
    monkeypatch.setattr(sys, "platform", "plan9")
    monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)

    support = native_scan_support()
    with pytest.raises(DirectoryScanError) as excinfo:
        scan_directory(Path("/anything"))

    assert not support.supported
    assert excinfo.value.reason == "unsupported-platform"


def test_windows_without_the_nt_api_fails_closed(monkeypatch):
    def unavailable():
        raise OSError("ntdll not loadable")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ds, "_WindowsBackend", unavailable)

    with pytest.raises(DirectoryScanError) as excinfo:
        scan_directory("C:\\anything")

    assert excinfo.value.reason == "unsupported-platform"


# --- capability: missing facilities and probe failures -----------------------


@linux
def test_support_fails_closed_if_the_release_set_were_empty(monkeypatch):
    monkeypatch.setattr(ds, "RELEASE_QUALIFIED_FSTYPES", frozenset())

    support = native_scan_support()

    assert support.supported is False
    assert "release-qualified" in support.reason


@linux
def test_missing_posix_flag_is_unsupported(monkeypatch):
    monkeypatch.delattr(os, "O_PATH", raising=False)

    support = native_scan_support()

    assert not support.supported
    with pytest.raises(DirectoryScanError) as excinfo:
        scan_directory(Path("/anything"))
    assert excinfo.value.reason == "unsupported-platform"


@linux
def test_capability_probe_oserror_never_raises(monkeypatch):
    def unreadable(*args, **kwargs):
        raise OSError("synthetic: /proc is not mounted")

    monkeypatch.setattr(ds, "_read_mountinfo_text", unreadable)

    support = native_scan_support()  # must not raise during collection
    with pytest.raises(DirectoryScanError) as excinfo:
        scan_directory(Path("/anything"))

    assert not support.supported
    assert support.reason
    assert excinfo.value.reason == "unsupported-platform"


@linux
def test_mountinfo_facility_errors_are_unsupported_not_missing(monkeypatch):
    def unreadable(*args, **kwargs):
        raise OSError(errno.ENOENT, "no mountinfo")

    monkeypatch.setattr(ds, "_read_mountinfo_text", unreadable)
    with pytest.raises(DirectoryScanError) as missing:
        ds._mountinfo()
    assert missing.value.reason == "unsupported-platform"

    monkeypatch.setattr(ds, "_read_mountinfo_text", lambda *a, **k: "not a mount table")
    with pytest.raises(DirectoryScanError) as malformed:
        ds._mountinfo()
    assert malformed.value.reason == "unsupported-filesystem"


# --- policy and the private qualification seam -------------------------------


def test_candidate_policy_accepts_only_candidates():
    policy = ds._candidate_policy(["ext4", "tmpfs"])

    assert policy.fstypes == frozenset({"ext4", "tmpfs"})
    for refused in ("nfs4", "nfs", "ext3", "ext2", "9p", "bogus", ""):
        with pytest.raises(ValueError):
            ds._candidate_policy([refused])
    with pytest.raises(ValueError):
        ds._candidate_policy([])


def test_release_policy_promotes_only_ext4_and_the_seam_token_resets():
    release = ds.ScanPolicy(ds.RELEASE_QUALIFIED_FSTYPES)
    assert ds.RELEASE_QUALIFIED_FSTYPES == frozenset({"ext4"})
    assert release.fstypes == frozenset({"ext4"})
    assert ds.CANDIDATE_FSTYPES == frozenset(
        {"ext4", "xfs", "btrfs", "f2fs", "tmpfs", "overlay", "zfs"}
    )
    assert release.allows("ext4")
    for refused in ds.CANDIDATE_FSTYPES - {"ext4"}:
        assert not release.allows(refused)
    for refused in ("ext2", "ext3", "nfs4", "unknownfs", ""):
        assert not release.allows(refused)

    before = ds._SCAN_POLICY.get()
    with ds._qualification_for_tests(["ext4"]):
        assert ds._SCAN_POLICY.get().allows("ext4")
    assert ds._SCAN_POLICY.get() is before

    with ds._release_policy_for_tests():
        assert ds._SCAN_POLICY.get() is ds._RELEASE_POLICY
    assert ds._SCAN_POLICY.get() is before


@linux
def test_seam_token_does_not_leak_to_another_thread_and_resets_after_errors():
    before = ds._SCAN_POLICY.get()
    seen = {}
    try:
        with ds._qualification_for_tests(["ext4"]):
            thread = threading.Thread(target=lambda: seen.setdefault("policy", ds._SCAN_POLICY.get()))
            thread.start()
            thread.join()
            raise RuntimeError("synthetic")
    except RuntimeError:
        pass

    assert seen["policy"] is ds._RELEASE_POLICY
    assert ds._SCAN_POLICY.get() is before


@linux
def test_seam_is_candidate_only_and_the_release_policy_scans_ext4(tmp_path):
    reason = _release_root_gate_reason(tmp_path)
    if reason:
        pytest.skip(reason)
    root = tmp_path / "root"
    root.mkdir()
    (root / "a").write_bytes(b"x")

    # The production policy promotes ext4, so no seam is needed for this root.
    with ds._release_policy_for_tests():
        release_result = scan_directory(root)
    assert release_result.digest.members == 1

    # The seam still works for candidate evidence and matches the release scan.
    result = ds._scan_with_test_policy(root, ds.CANDIDATE_FSTYPES)
    assert result.digest == release_result.digest
    with ds._qualification_for_tests(ds.CANDIDATE_FSTYPES):
        assert scan_directory(root).digest == result.digest

    # The seam cannot promote an unqualified type: naming only tmpfs refuses
    # this ext4 root, exactly like the release policy would for a tmpfs root.
    with pytest.raises(DirectoryScanError) as refused:
        ds._scan_with_test_policy(root, ["tmpfs"])
    assert refused.value.reason == "unsupported-filesystem"


def test_no_public_option_enables_the_qualification_seam():
    from runevidence.cli import build_parser

    parser = build_parser()
    options = set()
    subparsers = [action for action in parser._actions if isinstance(action, argparse._SubParsersAction)]
    for group in subparsers:
        for subparser in group.choices.values():
            options.update(option for action in subparser._actions for option in action.option_strings)
    options.update(option for action in parser._actions for option in action.option_strings)

    forbidden = {
        "--backend", "--posix", "--qualify", "--qualification", "--fstype",
        "--filesystem", "--test-policy", "--allow-filesystem", "--force-backend",
    }
    assert not options & forbidden
    assert list(inspect.signature(scan_directory).parameters) == ["root", "limits"]
    assert list(inspect.signature(native_scan_support).parameters) == []
    assert not hasattr(runevidence, "qualification_for_tests")
    assert not hasattr(runevidence, "_qualification_for_tests")


# --- filesystem policy: mountinfo parsing and matching -----------------------


def _mount_table(*rows):
    """rows are (mount id, parent id, mount point, fstype)."""
    text = "\n".join(
        f"{mount_id} {parent_id} 0:{mount_id} / {mount_point} rw shared:1 - {fstype} src rw"
        for mount_id, parent_id, mount_point, fstype in rows
    )
    return ds._parse_mountinfo(text)


def test_mountinfo_parses_fields_and_decodes_octal_escapes():
    table = _mount_table(
        (29, 1, "/", "ext4"),
        (36, 29, "/data\\040set", "ext4"),
        (37, 29, "/a\\011b\\012c\\134d", "tmpfs"),
    )

    assert table[29].mount_point == "/" and table[29].fstype == "ext4"
    assert table[36].mount_point == "/data set"
    assert table[37].mount_point == "/a\tb\nc\\d"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "36 29 0:28 / /data rw\n",
        "x 29 0:28 / /data rw - ext4 src rw\n",
        "36 y 0:28 / /data rw - ext4 src rw\n",
        "36 29 0:28 / data rw - ext4 src rw\n",
        "36 29 0:28 / /a\\04 rw - ext4 src rw\n",
        "36 29 0:28 / /a rw - ext4 src rw\n36 30 0:29 / /b rw - tmpfs src rw\n",
        "0 29 0:28 / /a rw - ext4 src rw\n",
    ],
)
def test_mountinfo_malformed_records_are_refused(text):
    with pytest.raises(ds._MountError):
        ds._parse_mountinfo(text)


def test_mount_point_matching_is_component_boundary_exact():
    root_and_mnt = _mount_table((29, 1, "/", "ext4"), (40, 29, "/mnt", "tmpfs"))
    assert ds._covering_mount(root_and_mnt, "/mnt/data").mount_id == 40
    assert ds._covering_mount(root_and_mnt, "/mnt/database").mount_id == 40
    assert ds._covering_mount(root_and_mnt, "/mnt").mount_id == 40
    assert ds._covering_mount(root_and_mnt, "/mntdata").mount_id == 29

    nested = _mount_table((29, 1, "/", "ext4"), (40, 29, "/mnt/data", "ext4"))
    assert ds._covering_mount(nested, "/mnt/data/child").mount_id == 40
    assert ds._covering_mount(nested, "/mnt/database").mount_id == 29

    foo = _mount_table((29, 1, "/", "ext4"), (40, 29, "/foo", "ext4"))
    assert ds._covering_mount(foo, "/foo").mount_id == 40
    assert ds._covering_mount(foo, "/foo/bar").mount_id == 40
    assert ds._covering_mount(foo, "/foobar").mount_id == 29


def test_no_covering_mount_is_refused_not_assigned_to_root():
    table = _mount_table((40, 29, "/mnt", "ext4"))

    with pytest.raises(ds._MountError):
        ds._covering_mount(table, "/elsewhere")


def test_stacked_mount_topmost_wins_over_the_allowed_lower_mount():
    table = _mount_table(
        (29, 1, "/", "ext4"), (36, 29, "/data", "ext4"), (48, 36, "/data", "nfs4")
    )

    assert ds._covering_mount(table, "/data/art").mount_id == 48
    with pytest.raises(DirectoryScanError) as excinfo:
        ds._preflight_mounts(["data", "art"], ds.ScanPolicy(ds.CANDIDATE_FSTYPES), table)
    assert excinfo.value.reason == "unsupported-filesystem"
    assert "nfs4" in str(excinfo.value)


def test_stacked_mount_without_a_unique_topmost_is_ambiguous():
    table = _mount_table(
        (29, 1, "/", "ext4"), (36, 29, "/data", "ext4"), (48, 29, "/data", "tmpfs")
    )
    with pytest.raises(ds._MountError):
        ds._covering_mount(table, "/data")

    cyclic = _mount_table(
        (29, 1, "/", "ext4"), (36, 48, "/data", "ext4"), (48, 36, "/data", "tmpfs")
    )
    with pytest.raises(ds._MountError):
        ds._covering_mount(cyclic, "/data")


def test_ancestor_preflight_refuses_an_ancestor_on_a_refused_filesystem():
    table = _mount_table(
        (29, 1, "/", "ext4"), (40, 29, "/mnt", "nfs4"), (41, 40, "/mnt/data", "ext4")
    )

    with pytest.raises(DirectoryScanError) as excinfo:
        ds._preflight_mounts(["mnt", "data", "art"], ds.ScanPolicy(ds.CANDIDATE_FSTYPES), table)

    assert excinfo.value.reason == "unsupported-filesystem"
    assert "'/mnt'" in str(excinfo.value)
    assert "nfs4" in str(excinfo.value)


def test_ancestors_above_the_root_are_exempt_from_mount_id_equality():
    table = _mount_table(
        (29, 1, "/", "ext4"), (764, 29, "/home", "ext4"), (55, 764, "/home/user", "ext4")
    )
    checked, chosen = ds._preflight_mounts(["home", "user", "project"], ds.ScanPolicy(ds.CANDIDATE_FSTYPES), table)
    preflight = ds._Mount(99, 55, "/home/user/project", "ext4")

    ds._validate_root_mount(99, preflight, {99: preflight}, ds.ScanPolicy(ds.CANDIDATE_FSTYPES))

    assert chosen.mount_id == 55
    assert [mount.mount_id for _, mount in checked] == [29, 764, 55, 55]


@pytest.mark.parametrize(
    "actual,fresh,expected",
    [
        (36, {36: ds._Mount(36, 29, "/data", "ext4")}, "ok"),
        # A mount was added between preflight and open: the actual id is new.
        (48, {36: ds._Mount(36, 29, "/data", "ext4"), 48: ds._Mount(48, 36, "/data", "nfs4")},
         "unsupported-filesystem"),
        # A replacement mount reused the same numeric id; only the fresh table
        # exposes that it is now nfs4.
        (36, {36: ds._Mount(36, 29, "/data", "nfs4")}, "unsupported-filesystem"),
        # The proven mount is absent from the fresh table (lazily detached).
        (36, {48: ds._Mount(48, 36, "/data", "ext4")}, "unsupported-filesystem"),
        # Same id and type, but a different mount point.
        (36, {36: ds._Mount(36, 29, "/elsewhere", "ext4")}, "unsupported-filesystem"),
    ],
)
def test_post_open_fresh_table_validation(actual, fresh, expected):
    preflight = ds._Mount(36, 29, "/data", "ext4")
    policy = ds.ScanPolicy(ds.CANDIDATE_FSTYPES)

    if expected == "ok":
        assert ds._validate_root_mount(actual, preflight, fresh, policy).mount_id == 36
    else:
        with pytest.raises(DirectoryScanError) as excinfo:
            ds._validate_root_mount(actual, preflight, fresh, policy)
        assert excinfo.value.reason == expected


# --- mount-id proof ----------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("mnt_id:\t29\n", 29),
        ("pos:\t0\nmnt_id: 36\n", 36),
        ("mnt_id: 0\n", None),
        ("mnt_id: 000\n", None),
        ("mnt_id:\n", None),
        ("mnt_id: abc\n", None),
        ("mnt_id: -3\n", None),
        ("nothing here\n", None),
    ],
)
def test_fdinfo_mount_id_parsing(text, expected):
    assert ds._parse_fdinfo_mount_id(text) == expected


def test_mount_id_proof_uses_statx_only_when_fdinfo_does_not_prove():
    import runevidence.directory_scanner as ds

    original_fdinfo, original_statx = ds._mount_id_from_fdinfo, ds._mount_id_from_statx
    try:
        ds._mount_id_from_fdinfo = lambda fd: None
        ds._mount_id_from_statx = lambda fd: 36
        assert ds._prove_mount_id(7) == 36

        ds._mount_id_from_fdinfo = lambda fd: 29
        ds._mount_id_from_statx = lambda fd: 48
        assert ds._prove_mount_id(7) == 29

        ds._mount_id_from_fdinfo = lambda fd: None
        ds._mount_id_from_statx = lambda fd: None
        with pytest.raises(DirectoryScanError) as excinfo:
            ds._prove_mount_id(7)
        assert excinfo.value.reason == "unsupported-platform"
    finally:
        ds._mount_id_from_fdinfo, ds._mount_id_from_statx = original_fdinfo, original_statx


@pytest.mark.parametrize(
    "fields,expected",
    [
        ({"mask": ds._STATX_MNT_ID, "mnt_id": 36}, 36),
        ({"mask": ds._STATX_MTIME, "mnt_id": 36}, None),
        ({"mask": ds._STATX_MNT_ID_UNIQUE, "mnt_id": 36}, None),
        ({"mask": ds._STATX_MNT_ID | ds._STATX_MTIME, "mnt_id": 0}, None),
    ],
)
def test_statx_mount_id_requires_the_classic_mask_and_a_nonzero_id(monkeypatch, fields, expected):
    fields = dict(fields, mtime_ns=1, ctime_ns=1)
    monkeypatch.setattr(ds, "_statx_fields", lambda fd, name=None: fields)

    assert ds._mount_id_from_statx(3) == expected


@linux
def test_valid_statx_mount_id_is_accepted_when_fdinfo_does_not_prove(tmp_path, monkeypatch):
    reason = _native_root_gate_reason(tmp_path)
    if reason:
        pytest.skip(reason)
    if ds._statx_function() is None:
        pytest.skip("libc statx is unavailable here")
    root = tmp_path / "root"
    root.mkdir()
    (root / "a").write_bytes(b"x")
    monkeypatch.setattr(ds, "_mount_id_from_fdinfo", lambda fd: None)

    result = scan_directory(root)

    assert result.digest.members == 1


@linux
def test_no_mount_id_source_at_scan_time_is_unsupported_platform(tmp_path, monkeypatch):
    reason = _native_root_gate_reason(tmp_path)
    if reason:
        pytest.skip(reason)
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr(ds, "_mount_id_from_fdinfo", lambda fd: None)
    monkeypatch.setattr(ds, "_mount_id_from_statx", lambda fd: None)

    assert scan_error(root).reason == "unsupported-platform"


@linux
def test_missing_nanosecond_resolution_is_unsupported(tmp_path, monkeypatch):
    reason = _native_root_gate_reason(tmp_path)
    if reason:
        pytest.skip(reason)
    root = tmp_path / "root"
    root.mkdir()
    (root / "a").write_bytes(b"x")
    coarse = {"mask": ds._STATX_MTIME | ds._STATX_CTIME, "mtime_ns": 0, "ctime_ns": 0, "mnt_id": 29}
    monkeypatch.setattr(ds, "_statx_fields", lambda fd, name=None: coarse)

    err = scan_error(root)

    assert err.reason == "unsupported-filesystem"
    assert "nanoseconds" in str(err)


# --- fake backends: mount capability and consistency -------------------------


def test_non_mount_backend_with_none_mount_is_valid():
    fs = FakeFS({"a": b"data"})

    assert fake_scan(fs).digest.members == 1


def test_mount_capable_backend_with_none_mount_is_a_hard_refusal():
    fs = FakeFS({"a": b"data"})
    fs.supports_mount_identity = True
    fs.mount_id = None

    err = fake_error(fs)

    assert err.reason == "unsupported-platform"


def test_descendant_mount_id_mismatch_is_unsafe():
    fs = FakeFS({"d": None, "d/a": b"1"})
    fs.supports_mount_identity = True
    fs.mount_id = 7
    fs.mounts["d"] = 8

    assert fake_error(fs).reason == "unsafe"


def test_inconsistent_mount_values_between_open_and_stat_are_unstable():
    fs = FakeFS({"a": b"data"})
    fs.supports_mount_identity = True
    fs.mount_id = 7
    real_info = fs._info

    def stat_with_a_different_mount(handle):
        info = real_info(handle)
        return ds._Info(info.kind, info.identity, info.size, info.times, info.links, 8)

    fs.stat = stat_with_a_different_mount

    assert fake_error(fs).reason == "unstable"


def test_fake_mount_values_are_consistent_across_listing_and_open():
    fs = FakeFS({"d": None, "d/a": b"1"})
    fs.supports_mount_identity = True
    fs.mount_id = 7
    opened: dict = {}
    listed: dict = {}

    real_info = fs._info

    def info_with_mount_log(path):
        value = real_info(path)
        opened.setdefault(path, set()).add(value.mount)
        return value

    real_list = fs.list

    def list_with_mount_log(handle, budget):
        values = real_list(handle, budget)
        if values is not None:
            for entry in values:
                listed.setdefault(handle or ".", set()).add(entry.mount)
        return values

    fs._info = info_with_mount_log
    fs.list = list_with_mount_log

    result = fake_scan(fs)

    assert result.digest.members == 2
    assert opened and all(mounts == {7} for mounts in opened.values())
    assert listed and all(mounts == {7} for mounts in listed.values())


def test_failure_codes_map_without_key_errors():
    cases = {
        errno.ENAMETOOLONG: "limit-exceeded",
        errno.EMFILE: "unreadable",
        errno.ENFILE: "unreadable",
        errno.ENOMEM: "unreadable",
        errno.EXDEV: "unsupported-filesystem",
    }
    for code, reason in cases.items():
        failure = ds._posix_failure(OSError(code, "synthetic"))
        assert ds._root_error(failure, "here").reason == reason
        assert ds._member_error(failure, "member").reason in (
            reason, "unsafe", "limit-exceeded", "unreadable",
        )


# --- native: Linux filesystem evidence --------------------------------------


@native
@posix
def test_native_empty_root_reproduces_the_empty_vector(tmp_path):
    root = tmp_path / "root"
    root.mkdir()

    result = scan_directory(root)

    assert result.digest.members == 0
    assert result.digest.sha256 == VECTORS[0]["sha256"]
    assert encode_directory(result.records) == bytes.fromhex(VECTORS[0]["encoded_hex"])


@native
def test_native_scans_are_deterministic(tmp_path):
    root = nested(tmp_path / "root")

    first = scan_directory(root)
    second = scan_directory(root)

    assert first == second
    assert encode_directory(first.records) == encode_directory(second.records)


@native
@pytest.mark.parametrize(
    "root",
    ["/", "/tmp/", "relative/path", "/tmp/./data", "/tmp/../data", "/tmp/data\x00x"],
)
@linux
def test_native_invalid_roots_are_refused_before_any_scan(root):
    assert scan_error(root).reason == "invalid-root"


@native
@linux
def test_native_root_component_cap_is_limit_exceeded():
    too_deep = "/" + "/".join(["d"] * (ds._ROOT_COMPONENT_CAP + 1))

    assert scan_error(too_deep).reason == "limit-exceeded"


@native
@linux
def test_native_ancestor_readdir_cap_is_limit_exceeded(tmp_path):
    parent = tmp_path / "huge"
    parent.mkdir()
    for index in range(ds._ANCESTOR_ENTRY_CAP):
        (parent / f"f{index}").mkdir()
    target = None
    for index in range(ds._ANCESTOR_ENTRY_CAP, ds._ANCESTOR_ENTRY_CAP + 256):
        candidate = parent / f"z{index}"
        candidate.mkdir()
        if os.listdir(parent).index(candidate.name) >= ds._ANCESTOR_ENTRY_CAP:
            target = candidate
            break
    if target is None:
        pytest.skip("the host's readdir order did not expose a late ancestor name")

    assert scan_error(target).reason == "limit-exceeded"


@native
@linux
def test_native_ancestor_readdir_stops_at_an_early_exact_name(monkeypatch):
    class Entry:
        def __init__(self, name):
            self.name = name

    def stream():
        for name in ("f0", "f1", "f2", "target"):
            yield Entry(name)
        raise AssertionError("the spelling check read past the exact name")

    class Scan:
        def __enter__(self):
            return stream()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(ds.os, "scandir", lambda fd: Scan())

    ds._PosixBackend(ds.ScanPolicy(ds.CANDIDATE_FSTYPES))._check_spelling(0, "target")


@native
@linux
def test_native_ancestor_names_are_not_profile_checked(tmp_path):
    root = tmp_path / "caf\u00e9" / "CON" / "a b" / "root"
    root.mkdir(parents=True)
    (root / "x").write_bytes(b"1")

    result = scan_directory(root)

    assert [record.path for record in result.records] == ["x"]


@native
@linux
def test_native_invalid_utf8_member_name_is_unsafe(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    raw = os.fsencode(root) + b"/bad\xff"
    fd = os.open(raw, os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, b"x")
    finally:
        os.close(fd)

    assert scan_error(root).reason == "unsafe"


@native
@posix
def test_native_file_swapped_to_fifo_before_open_is_not_read(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a").write_bytes(b"inside")

    def swap():
        (root / "a").unlink()
        os.mkfifo(root / "a")

    backend = SwapBefore("open_file", "a", swap)
    try:
        err = swap_error(backend, root)
    except OSError as exc:
        pytest.skip(f"cannot replace the file with a FIFO here: {exc}")

    assert (err.reason, err.path) == ("unstable", "a")
    assert backend.reads == 0


@native
@linux
def test_native_mount_ids_are_consistent_across_operations(tmp_path):
    root = nested(tmp_path / "root")
    backend = ds._native_backend()
    held, info = backend.open_root(os.fspath(root))
    try:
        assert info.mount is not None
        assert backend.stat(held[-1]).mount == info.mount
        entries = backend.list(held[-1], 100)
        assert entries is not None
        entry_mounts = {entry.mount for entry in entries}
        if ds._statx_function() is not None:
            assert entry_mounts == {info.mount}
        else:
            assert entry_mounts <= {None, info.mount}
        folder, folder_info = backend.open_dir(held[-1], "n")
        try:
            assert folder_info.mount == info.mount
            child, child_info = backend.open_file(folder, "x.bin")
            try:
                assert child_info.mount == info.mount
                assert backend.stat(child).mount == info.mount
            finally:
                backend.close(child)
        finally:
            backend.close(folder)
    finally:
        for handle in reversed(held):
            backend.close(handle)


@linux
def test_root_filesystem_gate_names_the_refused_ancestor_and_type(monkeypatch):
    table = _mount_table((29, 1, "/", "overlay"), (40, 29, "/tmp", "tmpfs"))
    monkeypatch.setattr(ds, "_mountinfo", lambda: table)

    with ds._qualification_for_tests(["tmpfs"]):
        reason = _native_root_gate_reason("/tmp/experiment/root")

    assert "native root filesystem gate" in reason
    assert "unsupported-filesystem" in reason
    assert "'/'" in reason and "overlay" in reason


@pytest.mark.parametrize(
    "fstype",
    [
        "ext2", "ext3", "virtiofs", "fuse-overlayfs", "fuse.sshfs", "squashfs", "erofs",
        "iso9660", "udf", "exfat", "ntfs3", "bcachefs", "nfs", "nfs4", "cifs", "smb3",
        "9p", "ceph", "afs", "proc", "sysfs", "cgroup2", "devpts", "devtmpfs", "debugfs",
        "tracefs", "securityfs", "configfs", "fusectl", "bpf", "mqueue", "hugetlbfs",
        "pstore", "autofs", "ramfs", "unknownfs",
    ],
)
def test_every_type_outside_the_allowlist_is_refused(fstype):
    table = _mount_table((29, 1, "/", "ext4"), (40, 29, "/data", fstype))

    with pytest.raises(DirectoryScanError) as excinfo:
        ds._preflight_mounts(["data"], ds.ScanPolicy(ds.CANDIDATE_FSTYPES), table)

    assert excinfo.value.reason == "unsupported-filesystem"
    assert fstype in str(excinfo.value)


def test_release_policy_allows_only_ext4_at_preflight():
    table = _mount_table((29, 1, "/", "ext4"), (40, 29, "/data", "ext4"))

    checked, chosen = ds._preflight_mounts(["data", "art"], ds._RELEASE_POLICY, table)

    assert chosen.mount_id == 40
    assert [mount.fstype for _, mount in checked] == ["ext4", "ext4", "ext4"]


@pytest.mark.parametrize(
    "fstype",
    sorted(ds.CANDIDATE_FSTYPES - {"ext4"}) + ["ext2", "ext3", "nfs4", "unknownfs"],
)
def test_release_policy_refuses_every_non_promoted_filesystem(fstype):
    table = _mount_table((29, 1, "/", "ext4"), (40, 29, "/data", fstype))

    with pytest.raises(DirectoryScanError) as excinfo:
        ds._preflight_mounts(["data"], ds._RELEASE_POLICY, table)

    assert excinfo.value.reason == "unsupported-filesystem"
    assert fstype in str(excinfo.value)


@linux
def test_environment_variables_cannot_change_the_release_policy(tmp_path, monkeypatch):
    reason = _release_root_gate_reason(tmp_path)
    if reason:
        pytest.skip(reason)
    root = tmp_path / "root"
    root.mkdir()
    (root / "a").write_bytes(b"x")
    monkeypatch.setenv("RUNEVIDENCE_DIRECTORY_BACKEND", "posix")
    monkeypatch.setenv("RUNEVIDENCE_DIRECTORY_FILESYSTEM", "tmpfs")
    monkeypatch.setenv("RUNEVIDENCE_DIRECTORY_QUALIFY", "tmpfs")

    with ds._release_policy_for_tests():
        # The environment cannot widen the exact allowlist; ext4 still scans.
        assert ds._SCAN_POLICY.get().fstypes == frozenset({"ext4"})
        assert scan_directory(root).digest.members == 1
        with pytest.raises(DirectoryScanError) as refused:
            ds._preflight_mounts(["root"], ds._SCAN_POLICY.get(), _mount_table((29, 1, "/", "tmpfs")))
        assert refused.value.reason == "unsupported-filesystem"


# --- injected POSIX OS failures become DirectoryScanError --------------------


@linux
@pytest.mark.parametrize(
    "failure",
    [
        PermissionError(errno.EACCES, "synthetic denied"),
        OSError(errno.EMFILE, "synthetic descriptor exhaustion"),
        OSError(errno.EIO, "synthetic I/O failure"),
    ],
)
def test_initial_root_open_os_error_is_normalized(tmp_path, monkeypatch, failure):
    reason = _native_root_gate_reason(tmp_path)
    if reason:
        pytest.skip(reason)
    root = tmp_path / "root"
    root.mkdir()
    backend = ds._native_backend()
    real_open = ds.os.open

    def failing_open(path, flags, *args, **kwargs):
        if path == "/" and not flags & os.O_PATH:
            raise failure
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(ds.os, "open", failing_open)

    with pytest.raises(DirectoryScanError) as excinfo:
        ds._scan(backend, os.fspath(root), DirectoryLimits())

    assert excinfo.value.reason == "unreadable"


@linux
@pytest.mark.parametrize("code", [errno.EACCES, errno.EMFILE, errno.EIO])
def test_reopen_fstat_os_error_is_normalized(tmp_path, monkeypatch, code):
    reason = _native_root_gate_reason(tmp_path)
    if reason:
        pytest.skip(reason)
    root = tmp_path / "root"
    root.mkdir()
    backend = ds._native_backend()
    real_open = ds.os.open
    real_fstat = ds.os.fstat
    reopened: set[int] = set()

    def tracking_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if isinstance(path, str) and path.startswith("/proc/self/fd/"):
            reopened.add(fd)
        return fd

    def failing_fstat(fd):
        if fd in reopened:
            raise OSError(code, "synthetic fstat failure")
        return real_fstat(fd)

    monkeypatch.setattr(ds.os, "open", tracking_open)
    monkeypatch.setattr(ds.os, "fstat", failing_fstat)

    with pytest.raises(DirectoryScanError) as excinfo:
        ds._scan(backend, os.fspath(root), DirectoryLimits())

    assert reopened
    assert excinfo.value.reason == "unreadable"


# --- wiring: mount validation, non-FILE reopen, ancestor exemption -----------


def _stateful_root_mountinfo(monkeypatch, backend, parts, preflight, fresh, fresh_after, real_id):
    """Return mountinfo by scan phase, not by a blind call cycle.

    ``fresh_after`` selects the post-open state that returns the nfs4 table:
    ``len(parts) - 1`` is the line-1220 read before the root reopen;
    ``len(parts)`` is the line-1229 read after it. Every preflight and every
    other read returns the ext4 table."""
    state = {"preflight_done": False, "passes": 0, "reopens": 0, "fresh_used": False}

    def mountinfo():
        if not state["preflight_done"]:
            state["preflight_done"] = True
            return preflight
        if (
            state["passes"] == 1
            and state["reopens"] == fresh_after
            and not state["fresh_used"]
        ):
            state["fresh_used"] = True
            return fresh
        return preflight

    real_open_root = backend.open_root

    def open_root(path):
        state["passes"] += 1
        return real_open_root(path)

    real_reopen = backend._reopen

    def reopen(*args, **kwargs):
        try:
            return real_reopen(*args, **kwargs)
        finally:
            state["reopens"] += 1

    backend.open_root = open_root
    backend._reopen = reopen
    monkeypatch.setattr(ds, "_mountinfo", mountinfo)
    monkeypatch.setattr(ds, "_prove_mount_id", lambda fd: real_id)
    return state


@linux
def test_open_root_validates_the_proven_root_against_the_fresh_table(tmp_path, monkeypatch):
    reason = _native_root_gate_reason(tmp_path)
    if reason:
        pytest.skip(reason)
    root = tmp_path / "root"
    root.mkdir()
    (root / "a").write_bytes(b"x")
    parts = ds._root_components(os.fspath(root))
    backend = ds._native_backend()
    fd = os.open(os.fspath(root), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        real_id = ds._prove_mount_id(fd)
    finally:
        os.close(fd)
    preflight = ds._parse_mountinfo(f"{real_id} 1 0:{real_id} / / rw - ext4 src rw")
    fresh = ds._parse_mountinfo(f"{real_id} 1 0:{real_id} / / rw - nfs4 src rw")
    state = _stateful_root_mountinfo(
        monkeypatch, backend, parts, preflight, fresh, len(parts) - 1, real_id
    )

    with pytest.raises(DirectoryScanError) as excinfo:
        ds._scan(backend, os.fspath(root), DirectoryLimits())

    assert excinfo.value.reason == "unsupported-filesystem"
    # The fresh post-open fstype check, not a preflight refusal.
    assert "is now 'nfs4'" in str(excinfo.value)
    assert state["fresh_used"] and state["passes"] == 1


@linux
def test_open_root_revalidates_after_the_post_info_mount_read(tmp_path, monkeypatch):
    reason = _native_root_gate_reason(tmp_path)
    if reason:
        pytest.skip(reason)
    root = tmp_path / "root"
    root.mkdir()
    (root / "a").write_bytes(b"x")
    parts = ds._root_components(os.fspath(root))
    backend = ds._native_backend()
    fd = os.open(os.fspath(root), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        real_id = ds._prove_mount_id(fd)
    finally:
        os.close(fd)
    preflight = ds._parse_mountinfo(f"{real_id} 1 0:{real_id} / / rw - ext4 src rw")
    fresh = ds._parse_mountinfo(f"{real_id} 1 0:{real_id} / / rw - nfs4 src rw")
    # The line-1229 read happens after the root reopen, so reopens == len(parts).
    state = _stateful_root_mountinfo(
        monkeypatch, backend, parts, preflight, fresh, len(parts), real_id
    )

    with pytest.raises(DirectoryScanError) as excinfo:
        ds._scan(backend, os.fspath(root), DirectoryLimits())

    assert excinfo.value.reason == "unsupported-filesystem"
    assert "is now 'nfs4'" in str(excinfo.value)
    assert state["fresh_used"] and state["passes"] == 1


@linux
def test_open_file_does_not_reopen_a_non_file_probe(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a").write_bytes(b"inside")
    reason = _native_root_gate_reason(root)
    if reason:
        pytest.skip(reason)
    state = {"swapped": False, "reopens": 0}
    real_open = ds.os.open

    def counting_open(path, flags, *args, **kwargs):
        if (
            state["swapped"]
            and isinstance(path, str)
            and path.startswith("/proc/self/fd/")
            and not flags & os.O_PATH
        ):
            state["reopens"] += 1
        return real_open(path, flags, *args, **kwargs)

    def swap():
        (root / "a").unlink()
        os.mkfifo(root / "a")
        state["swapped"] = True

    backend = ds._native_backend()
    monkeypatch.setattr(ds.os, "open", counting_open)
    wrapper = SwapBefore("open_file", "a", swap, backend=backend)
    try:
        err = swap_error(wrapper, root)
    except OSError as exc:
        pytest.skip(f"cannot replace the file with a FIFO here: {exc}")

    assert (err.reason, err.path) == ("unstable", "a")
    assert wrapper.reads == 0
    assert state["reopens"] == 0


@linux
def test_open_root_does_not_require_ancestor_mount_equality(tmp_path, monkeypatch):
    reason = _native_root_gate_reason(tmp_path)
    if reason:
        pytest.skip(reason)
    root = tmp_path / "root"
    root.mkdir()
    (root / "a").write_bytes(b"x")
    parts = ds._root_components(os.fspath(root))
    fd = os.open(os.fspath(root), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        real_id = ds._prove_mount_id(fd)
    finally:
        os.close(fd)
    table = ds._parse_mountinfo(f"{real_id} 1 0:{real_id} / / rw - ext4 src rw")
    backend = ds._native_backend()
    monkeypatch.setattr(ds, "_mountinfo", lambda: table)
    calls = {"count": 0}

    def prove(fd):
        calls["count"] += 1
        if calls["count"] < len(parts):
            return real_id + 1000 + calls["count"]
        return real_id

    monkeypatch.setattr(ds, "_prove_mount_id", prove)

    result = ds._scan(backend, os.fspath(root), DirectoryLimits())

    assert result.digest.members == 1
    assert calls["count"] > len(parts)


@linux
def test_open_root_requires_the_stat_and_probe_identity_to_match(tmp_path, monkeypatch):
    reason = _native_root_gate_reason(tmp_path)
    if reason:
        pytest.skip(reason)
    root = tmp_path / "root"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    backend = ds._native_backend()
    real_probe = backend._probe

    def swapped_probe(parent, name):
        fd = real_probe(parent, name)
        if name == root.name:
            os.close(fd)
            return os.open(os.fspath(other), os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
        return fd

    backend._probe = swapped_probe

    with pytest.raises(DirectoryScanError) as excinfo:
        ds._scan(backend, os.fspath(root), DirectoryLimits())

    assert excinfo.value.reason == "unstable"


def test_listing_and_open_mount_values_must_agree():
    fs = FakeFS({"a": b"data"})
    fs.supports_mount_identity = True
    fs.mount_id = 7
    real_list = fs.list

    def list_with_one_wrong_member_mount(handle, budget):
        values = real_list(handle, budget)
        if values is None:
            return None
        return [
            ds._Entry(entry.name, entry.kind, entry.identity, 8 if entry.name == "a" else entry.mount)
            for entry in values
        ]

    # Only the listing claims mount 8. open_file, stat and every _Info for
    # "a" still say 7, so no later metadata comparison can produce unstable.
    fs.list = list_with_one_wrong_member_mount

    err = fake_error(fs)

    assert err.reason == "unstable"
    assert "changed mount" in str(err)
    assert ("read", "a") not in fs.calls


# --- filesystem qualification matrix (test evidence only) --------------------

MATRIX_PATH = Path(__file__).parent / "data" / "directory_scanner_filesystem_matrix.json"


def _matrix_payload_hash(data: dict) -> str:
    payload = {key: value for key, value in data.items() if key != "matrix_payload_sha256"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="POSIX-only evidence: the filesystem matrix records an ext4 Linux checkout",
)
def test_filesystem_matrix_evidence_shape_and_hashes():
    data = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))

    assert data["format"] == "runevidence.directory-scanner-filesystem-matrix/v1"
    assert data["milestone"] == "M41-SCANNER-S3A"
    assert data["fstype"] == "ext4" and data["fstype"] in ds.CANDIDATE_FSTYPES
    assert ds.RELEASE_QUALIFIED_FSTYPES == frozenset({"ext4"})
    assert data["scan_policy"]["release_qualified_fstypes"] == ["ext4"]
    assert data["scan_policy"]["promoted"] is True
    assert data["promotion"]["slice"] == "M41-SCANNER-S3A"
    assert data["promotion"]["fstypes"] == ["ext4"]
    assert data["promotion"]["public_flag"] is False
    assert "test evidence" in data["promotion"]["basis"]
    assert data["kernel"] and isinstance(data["kernel"], str)

    mount = data["mount"]
    assert isinstance(mount["mount_id"], int) and mount["mount_id"] > 0
    assert isinstance(mount["parent_id"], int) and mount["parent_id"] >= 0
    assert re.fullmatch(r"\d+:\d+", mount["major_minor"])
    assert mount["mount_point"].startswith("/")
    assert mount["source"] and mount["fstype"] == data["fstype"]

    assert any(version.startswith("3.12.") for version in data["python"])
    assert any(version.startswith("3.10.") for version in data["python"])
    assert data["pytest"]
    assert "pytest" in data["command"] and "test_directory_scanner.py" in data["command"]
    assert data["selection"] == "tests/test_directory_scanner.py"

    counts = data["counts"]
    assert counts["selected"] == counts["passed"] + counts["skipped"] + counts["failed"] + counts["errors"]
    assert counts["passed"] > 0 and counts["failed"] == 0 and counts["errors"] == 0

    nodes = data["node_ids"]
    assert nodes and all(isinstance(node, str) for node in nodes)
    assert all(node.startswith(data["selection"] + "::") for node in nodes)
    assert "tests/test_directory_scanner.py::test_native_scan_reproduces_each_fixed_vector[empty]" in nodes

    hashes = data["hashes"]
    assert hashes["scanner_sha256"] == hashlib.sha256(Path(ds.__file__).read_bytes()).hexdigest()
    assert hashes["tests_sha256"] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    assert hashes["vectors_sha256"] == hashlib.sha256(
        (Path(__file__).parent / "data" / "directory_digest_vectors.json").read_bytes()
    ).hexdigest()
    assert data["matrix_payload_sha256"] == _matrix_payload_hash(data)
    assert data["limitations"]


@linux
def test_filesystem_matrix_is_evidence_only_and_not_packaged(tmp_path, monkeypatch):
    reason = _native_root_gate_reason(tmp_path)
    if reason:
        pytest.skip(reason)
    scanner_source = Path(ds.__file__).read_text(encoding="utf-8")
    assert "directory_scanner_filesystem_matrix" not in scanner_source
    assert "tests/data" not in scanner_source

    real_open = builtins.open

    def guarded_open(file, *args, **kwargs):
        if "directory_scanner_filesystem_matrix" in str(file):
            raise AssertionError("the scanner read the test evidence matrix")
        return real_open(file, *args, **kwargs)

    root = tmp_path / "root"
    root.mkdir()
    (root / "a").write_bytes(b"x")
    monkeypatch.setattr(builtins, "open", guarded_open)

    assert scan_directory(root).digest.members == 1

    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    sdist = re.search(r"\[tool\.hatch\.build\.targets\.sdist\](.*?)(?=\n\[|\Z)", pyproject, re.S)
    wheel = re.search(r"\[tool\.hatch\.build\.targets\.wheel\](.*?)(?=\n\[|\Z)", pyproject, re.S)
    assert sdist and "tests" not in sdist.group(1)
    assert wheel and 'packages = ["src/runevidence"]' in wheel.group(1)
    assert not any("tests" in line for line in sdist.group(1).splitlines())
