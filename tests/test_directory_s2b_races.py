"""S2b race, injection and determinism evidence for the native scanner.

These tests use real objects and real timing hooks under the private
candidate-only qualification seam. They are not a hostile-writer proof; they
show that the scanner's checks fire at the intended boundaries and that a
changing tree never yields a partial digest.

The S1 matrix and scanner-test pin stay untouched: this module is separate
test evidence and does not edit ``test_directory_scanner.py``.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
import threading
import time
from pathlib import Path

import pytest

import runevidence.directory_scanner as ds
from conftest import REPO, native_test
from runevidence.directory_hashing import DirectoryLimits, encode_directory

native = native_test
linux = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux POSIX backend only")

VECTORS = json.loads(
    (Path(__file__).parent / "data" / "directory_digest_vectors.json").read_bytes().decode("utf-8")
)["vectors"]


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


class BackendAtCall:
    """The real backend, with one action run just before the ``at``th call of
    ``method``. The OS objects are real; only the timing is arranged."""

    def __init__(self, backend, method: str, action, at: int = 1):
        self.backend = backend
        self.method = method
        self.action = action
        self.at = at
        self.calls: dict[str, int] = {}

    def __getattr__(self, attr):
        return getattr(self.backend, attr)

    def _call(self, method, *args, **kwargs):
        count = self.calls.get(method, 0) + 1
        self.calls[method] = count
        if method == self.method and count == self.at:
            self.action()
        return getattr(self.backend, method)(*args, **kwargs)

    def open_root(self, *args, **kwargs):
        return self._call("open_root", *args, **kwargs)

    def open_dir(self, *args, **kwargs):
        return self._call("open_dir", *args, **kwargs)

    def open_file(self, *args, **kwargs):
        return self._call("open_file", *args, **kwargs)

    def read(self, *args, **kwargs):
        return self._call("read", *args, **kwargs)

    def stat(self, *args, **kwargs):
        return self._call("stat", *args, **kwargs)

    def list(self, *args, **kwargs):
        return self._call("list", *args, **kwargs)

    def close(self, *args, **kwargs):
        return self._call("close", *args, **kwargs)


class TrackingBackend:
    """Forward everything, counting descriptors the shared walk holds open."""

    def __init__(self, backend):
        self.backend = backend
        self.held: set = set()
        self.peak = 0

    def __getattr__(self, attr):
        return getattr(self.backend, attr)

    def _add(self, handle):
        if isinstance(handle, list):
            for item in handle:
                self.held.add(item)
        else:
            self.held.add(handle)
        self.peak = max(self.peak, len(self.held))

    def open_root(self, root):
        held, info = self.backend.open_root(root)
        self._add(held)
        return held, info

    def open_dir(self, handle, name):
        child, info = self.backend.open_dir(handle, name)
        self._add(child)
        return child, info

    def open_file(self, handle, name):
        child, info = self.backend.open_file(handle, name)
        self._add(child)
        return child, info

    def close(self, handle):
        self.held.discard(handle)
        self.backend.close(handle)


@native
@linux
def test_native_file_replaced_after_probe_before_read_returns_no_partial_digest(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a").write_bytes(b"old content")
    (root / "keep").write_bytes(b"stable sibling")
    replacement = b"new content with another length"

    def replace_name():
        (root / "a").rename(tmp_path / "old-a")
        (root / "a").write_bytes(replacement)

    backend = BackendAtCall(ds._native_backend(), "read", replace_name)
    digest_calls = []
    real_digest = ds.directory_digest

    def recording_digest(records, limits):
        digest_calls.append(records)
        return real_digest(records, limits)

    monkeypatch.setattr(ds, "directory_digest", recording_digest)

    with pytest.raises(ds.DirectoryScanError) as error:
        ds._scan(backend, os.fspath(root), DirectoryLimits())

    assert error.value.reason == "unstable"
    # The old pinned inode was read, then the name was replaced; the second
    # listing notices, so no digest is ever built from the changed tree.
    assert backend.calls.get("read", 0) >= 1
    assert digest_calls == []
    assert (tmp_path / "old-a").read_bytes() == b"old content"
    assert (root / "a").read_bytes() == replacement


@native
@linux
@pytest.mark.parametrize(
    "case,reason",
    [
        ("missing", "missing"),
        ("file", "not-a-directory"),
        ("link", "unsafe"),
        ("unreadable", "unreadable"),
    ],
)
def test_native_root_error_at_the_second_pass_is_reported_without_a_digest(
    tmp_path, monkeypatch, case, reason
):
    if case == "unreadable" and os.geteuid() == 0:
        pytest.skip("root ignores permission bits")
    root = tmp_path / "root"
    root.mkdir()
    (root / "a").write_bytes(b"x")
    mode = stat.S_IMODE(root.lstat().st_mode)

    def break_root():
        if case == "missing":
            root.rename(tmp_path / "gone")
        elif case == "file":
            root.rename(tmp_path / "gone")
            root.write_bytes(b"not a directory")
        elif case == "link":
            root.rename(tmp_path / "gone")
            os.symlink(tmp_path / "gone", root)
        else:
            root.chmod(0)

    backend = BackendAtCall(ds._native_backend(), "open_root", break_root, at=2)
    digest_calls = []
    real_digest = ds.directory_digest

    def recording_digest(records, limits):
        digest_calls.append(records)
        return real_digest(records, limits)

    monkeypatch.setattr(ds, "directory_digest", recording_digest)
    try:
        with pytest.raises(ds.DirectoryScanError) as error:
            ds._scan(backend, os.fspath(root), DirectoryLimits())
    finally:
        if case == "unreadable":
            root.chmod(mode)

    assert error.value.reason == reason
    assert digest_calls == []


@native
@linux
def test_native_directory_renamed_out_and_back_between_passes_is_unstable(tmp_path):
    # A rename changes ctime, so the child directory's second-pass metadata
    # differs. A rename that returns within a single coarse timestamp tick can
    # restore every compared field and is invisible by design (documented
    # residual); this test synchronizes to a new tick. A fully forged ABA
    # (restored identity, times and content) remains out of scope.
    root = tmp_path / "root"
    (root / "d").mkdir(parents=True)
    (root / "d" / "a").write_bytes(b"inside")
    away_from_root = tmp_path / "away"
    away_from_root.mkdir()

    def rename_out_and_back():
        baseline = os.stat(root / "d", follow_symlinks=False)
        away = away_from_root / "d"
        (root / "d").rename(away)
        # The kernel stamps ctime from a coarse clock. If the rename landed in
        # the same tick as the first-pass observation, rename again until the
        # directory's own metadata is stat-visible beyond that value, then
        # leave it renamed back. Bounded, monotonic, no arbitrary sleep.
        deadline = time.monotonic() + 2.0
        while True:
            current = os.stat(away, follow_symlinks=False)
            if current.st_mtime_ns > baseline.st_mtime_ns or current.st_ctime_ns > baseline.st_ctime_ns:
                break
            if time.monotonic() >= deadline:
                pytest.skip(
                    "the host timestamp clock did not advance within 2 s; "
                    "cannot synchronize the rename ABA to a new tick"
                )
            away.rename(root / "d")
            (root / "d").rename(away)
        away.rename(root / "d")

    backend = BackendAtCall(ds._native_backend(), "open_root", rename_out_and_back, at=2)

    with pytest.raises(ds.DirectoryScanError) as error:
        ds._scan(backend, os.fspath(root), DirectoryLimits())

    assert error.value.reason == "unstable"
    assert (root / "d" / "a").read_bytes() == b"inside"


@native
@linux
def test_native_concurrent_mutation_is_unstable_and_returns_no_partial_digest(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    for index in range(200):
        (root / f"f{index:03d}").write_bytes(b"x" * 64)

    stop = threading.Event()
    first_mutation = threading.Event()
    mutations = []

    def churn():
        # Create-only churn: an unlink while a file is held open would surface
        # as "0 hard links", which this test deliberately leaves to the
        # existing link-count policy rather than treating as a race assertion.
        index = 0
        while not stop.is_set() and index < 1000:
            target = root / f"churn-{index:04d}"
            try:
                target.write_bytes(b"churn")
                mutations.append(index)
                first_mutation.set()
            except OSError:
                pass
            index += 1

    thread = threading.Thread(target=churn, daemon=True)
    thread.start()
    assert first_mutation.wait(5), "the concurrent mutation thread never ran"
    digest_calls = []
    real_digest = ds.directory_digest

    def recording_digest(records, limits):
        digest_calls.append(records)
        return real_digest(records, limits)

    monkeypatch.setattr(ds, "directory_digest", recording_digest)
    try:
        try:
            result = ds.scan_directory(root)
        except ds.DirectoryScanError as error:
            # A changing tree is unstable, never a different "valid" digest.
            assert error.reason == "unstable", (error.reason, str(error))
            assert digest_calls == []
        else:
            # If the mutation happened to fall between both complete passes,
            # the returned digest must still describe exactly the records.
            assert digest_calls == [result.records]
            assert result.digest == real_digest(result.records, DirectoryLimits())
    finally:
        stop.set()
        thread.join(5)
    assert mutations


@native
@linux
def test_native_root_mount_type_change_before_the_second_pass_is_refused(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a").write_bytes(b"x")
    table = ds._mountinfo()
    covering = ds._covering_mount(table, os.fspath(root))
    altered = dict(table)
    altered[covering.mount_id] = ds._Mount(
        covering.mount_id, covering.parent_id, covering.mount_point, "nfs4"
    )

    def change_mount_type():
        monkeypatch.setattr(ds, "_mountinfo", lambda: altered)

    backend = BackendAtCall(ds._native_backend(), "open_root", change_mount_type, at=2)

    with pytest.raises(ds.DirectoryScanError) as error:
        ds._scan(backend, os.fspath(root), DirectoryLimits())

    assert error.value.reason == "unsupported-filesystem"
    assert "nfs4" in str(error.value)


@native
@linux
def test_native_repeated_scans_are_deterministic_and_stable_in_descriptors(tmp_path):
    root = nested(tmp_path / "root")

    def descriptor_count():
        return len(os.listdir("/proc/self/fd"))

    before = descriptor_count()
    results = [ds.scan_directory(root) for _ in range(3)]
    after = descriptor_count()

    encoded = [encode_directory(result.records) for result in results]
    assert len(set(encoded)) == 1
    assert len({result.digest.sha256 for result in results}) == 1
    assert all(result == results[0] for result in results)
    assert after == before

    tracker = TrackingBackend(ds._native_backend())
    tracked = ds._scan(tracker, os.fspath(root), DirectoryLimits())
    assert tracker.held == set()
    assert tracker.peak <= len(ds._root_components(os.fspath(root))) + 64 + 4
    assert tracked == results[0]


def test_packaging_configuration_excludes_tests_docs_examples_and_evidence():
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    sdist = re.search(r"\[tool\.hatch\.build\.targets\.sdist\](.*?)(?=\n\[|\Z)", pyproject, re.S)
    wheel = re.search(r"\[tool\.hatch\.build\.targets\.wheel\](.*?)(?=\n\[|\Z)", pyproject, re.S)
    assert sdist and wheel
    for token in ("tests", "docs", "examples", "schema", "directory_scanner_filesystem_matrix",
                  "directory_digest_vectors"):
        assert token not in sdist.group(1), token
        assert token not in wheel.group(1), token
    assert 'packages = ["src/runevidence"]' in wheel.group(1)
    assert "only-include" in sdist.group(1)
