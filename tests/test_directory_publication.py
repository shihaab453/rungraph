"""Evidence for the internal S4-1 guarded-publication core.

No command imports ``runevidence.directory_publication``; this module is the
only new surface. The tests use real files, the real scanner, the real
``os.replace`` and the real ``fsync`` on a real ext4 temp folder where the host
provides one. A skip records missing evidence, never a pass:

* no ext4 temp folder -> every real-write/crash/lock test skips;
* no ``setfacl`` -> ACL preservation skips;
* no ``FS_IOC_GETFLAGS``/project-ID ioctl -> the flag/project-ID evidence skips
  (the implementation narrows G6 for project ID and refuses for inode flags);
* no second unprivileged user this process can become -> the live second-user
  access check skips; the same test still inspects mode/ACL at every boundary;
* no SELinux or permission to set synthetic ``security.*``/``system.*`` xattrs
  -> that refusal test skips.

Injected failures and monkeypatched helpers establish protocol order, not
power-loss durability on every device. A crash test kills a real subprocess; a
process crash keeps the page cache and is deliberately not described as a
power-loss proof.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import shutil
import signal
import stat
import struct
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import runevidence
from runevidence import directory_publication as dp
from runevidence import directory_scanner as ds
from runevidence.directory_authoring import (
    DirectoryAuthoringError,
    propose_add_directory,
    propose_rehash_directory,
)
from runevidence.directory_scanner import scan_directory
from runevidence.manifest import dump_raw, load_manifest, load_raw
from runevidence.models import MANIFEST_FILENAME, Manifest

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
TIMEOUT = 180
SETFACL = shutil.which("setfacl")
GETFACL = shutil.which("getfacl")

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="S4-1 guarded publication is Linux-only"
)


# --- fixtures and helpers ----------------------------------------------------


def _host_fstype(path: Path) -> str:
    try:
        parts = ds._root_components(str(path))
        table = dp._mount_table()
        _, mount = ds._preflight_mounts(parts, dp._release_policy(), table)
        return mount.fstype
    except Exception as exc:  # noqa: BLE001 - a probe gap is a skip, never a test error
        return f"<{type(exc).__name__}>"


@pytest.fixture
def ext4(tmp_path: Path) -> Path:
    fstype = _host_fstype(tmp_path)
    if fstype != "ext4":
        pytest.skip(f"host temp is {fstype!r}, not release-qualified ext4: ext4 evidence missing")
    probe = tmp_path / ".flags-probe"
    probe.write_bytes(b"probe")
    try:
        if not ioctl_available(probe):
            pytest.skip("FS_IOC_GETFLAGS unavailable: inode-flag evidence missing (S4-1 refuses closed)")
    finally:
        probe.unlink()
    return tmp_path


def base_raw(*artifacts, runs=(), results=(), evidence=()):
    return {
        "project": {"name": "Demo", "id": "demo"},
        "schema_version": "0.1",
        "runs": list(runs),
        "artifacts": [{"path": "notes.txt", "kind": "doc", "id": "notes"}, *artifacts],
        "results": list(results),
        "evidence": list(evidence),
    }


def make_exp(root: Path, *, files=None, mode=0o644, raw=None, name="exp", folder="data") -> tuple[Path, dict]:
    exp = root / name
    exp.mkdir()
    data = exp / folder
    data.mkdir()
    for rel, content in (files or {"a.txt": b"hello"}).items():
        target = data.joinpath(*rel.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    raw = base_raw() if raw is None else raw
    manifest = exp / MANIFEST_FILENAME
    manifest.write_bytes(dump_raw(raw).encode("utf-8"))
    os.chmod(manifest, mode)
    return exp, raw


@pytest.fixture
def exp(ext4: Path, tmp_path: Path) -> tuple[Path, dict]:
    return make_exp(tmp_path)


def names(exp: Path) -> list[str]:
    return sorted(os.listdir(exp))


def manifest_bytes(exp: Path) -> bytes:
    return (exp / MANIFEST_FILENAME).read_bytes()


def manifest_json(exp: Path) -> dict:
    return json.loads(manifest_bytes(exp).decode("utf-8"))


def assert_complete_manifest(exp: Path, *, old: bytes = None, new: bool = False) -> dict:
    data = manifest_bytes(exp)
    if old is not None:
        assert data == old
    raw = json.loads(data.decode("utf-8"))
    Manifest.model_validate(raw)  # a complete, valid document, never a torn one
    if new:
        assert any(a.get("id") == "data" for a in raw.get("artifacts", []))
    return raw


def add_proposal(exp: Path, *, path="data", artifact_id="data", kind=None, run_id=None):
    loaded = load_manifest(exp)
    return loaded, propose_add_directory(
        loaded, load_raw(loaded), path=path, artifact_id=artifact_id, kind=kind, run_id=run_id
    )


def publish_add(exp: Path, *, path="data", artifact_id="data", kind=None, run_id=None):
    with dp.manifest_admission_lock(exp) as locked:
        loaded = dp.load_manifest_under_lock(locked, exp)
        proposal = propose_add_directory(
            loaded, load_raw(loaded), path=path, artifact_id=artifact_id, kind=kind, run_id=run_id
        )
        return dp.publish_directory_proposal(locked, proposal)


def publish_rehash(exp: Path, artifact_id="data"):
    with dp.manifest_admission_lock(exp) as locked:
        loaded = dp.load_manifest_under_lock(locked, exp)
        proposal = propose_rehash_directory(loaded, load_raw(loaded), artifact_id=artifact_id)
        return dp.publish_directory_proposal(locked, proposal)


def dry_run(exp: Path, *, path="data", artifact_id="data"):
    loaded = load_manifest(exp)
    proposal = propose_add_directory(
        loaded, load_raw(loaded), path=path, artifact_id=artifact_id
    )
    return dp.publish_directory_proposal(None, proposal, dry_run=True, loaded=loaded)


def stage_cells(exp: Path) -> list[str]:
    return [name for name in os.listdir(exp) if name.startswith(dp.CELL_PREFIX)]


def backups(exp: Path) -> list[str]:
    return [name for name in os.listdir(exp) if name.startswith(MANIFEST_FILENAME + ".bak")]


def snapshot_names_types(exp: Path) -> dict:
    out = {}
    for name in os.listdir(exp):
        st = os.lstat(exp / name)
        out[name] = stat.S_IFMT(st.st_mode)
    return out


def tree_snapshot(root: Path) -> dict:
    """Explicit content/metadata; atime is deliberately excluded (reads may
    update it at the kernel's discretion)."""
    out = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            st = os.lstat(path)
            rel = str(path.relative_to(root))
            entry = {
                "mode": stat.S_IMODE(st.st_mode),
                "uid": st.st_uid,
                "gid": st.st_gid,
                "size": st.st_size,
                "mtime_ns": st.st_mtime_ns,
                "xattrs": {
                    attr: os.getxattr(path, attr) for attr in sorted(os.listxattr(path))
                },
            }
            if stat.S_ISREG(st.st_mode):
                entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            out[rel] = entry
    return out


def set_inode_flags(path: Path, flags: int) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        fcntl.ioctl(fd, 0x40086602, struct.pack("<I", flags) + b"\0\0\0\0")
    finally:
        os.close(fd)


def get_inode_flags(path: Path) -> int:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        raw = fcntl.ioctl(fd, 0x80086601, b"\0" * 8)
        return struct.unpack("<I", raw[:4])[0]
    finally:
        os.close(fd)


def ioctl_available(path: Path) -> bool:
    try:
        get_inode_flags(path)
    except OSError:
        return False
    return True


def refused(reason, call, *, phase=None):
    with pytest.raises(dp.DirectoryPublicationError) as info:
        call()
    assert info.value.reason == reason, str(info.value)
    if phase is not None:
        assert info.value.phase == phase
    return info.value


CANARIES = ("/abs/root-canary", "member-canary.txt", "strerror-canary", "ino=424242", "sha256-canary")


# --- subprocess drivers -------------------------------------------------------

_CRASH_DRIVER = r"""
import os, sys
repo, target, path, artifact_id, crash = sys.argv[1:6]
sys.path.insert(0, repo)
from runevidence import directory_publication as dp
from runevidence.manifest import load_raw
from runevidence.directory_authoring import propose_add_directory

def boom(*args, **kwargs):
    os._exit(97)

if crash.startswith("fsync:"):
    wanted = crash.split(":", 1)[1]
    real = dp._fsync
    def crash_fsync(fd, *, role):
        if role == wanted:
            os._exit(97)
        return real(fd, role=role)
    dp._fsync = crash_fsync
elif crash != "none":
    setattr(dp, crash, boom)

with dp.manifest_admission_lock(target) as locked:
    loaded = dp.load_manifest_under_lock(locked, target)
    proposal = propose_add_directory(loaded, load_raw(loaded), path=path, artifact_id=artifact_id)
    dp.publish_directory_proposal(locked, proposal)
os._exit(0)
"""

_HOLDER_DRIVER = r"""
import os, sys
repo, target, path, artifact_id, mode = sys.argv[1:6]
sys.path.insert(0, repo)
from runevidence import directory_publication as dp
from runevidence.manifest import load_raw
from runevidence.directory_authoring import propose_add_directory

with dp.manifest_admission_lock(target) as locked:
    print("locked", flush=True)
    sys.stdin.readline()
    if mode == "add":
        loaded = dp.load_manifest_under_lock(locked, target)
        proposal = propose_add_directory(loaded, load_raw(loaded), path=path, artifact_id=artifact_id)
        dp.publish_directory_proposal(locked, proposal)
print("done", flush=True)
"""


def run_child(code: str, *args: str, timeout: int = TIMEOUT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-B", "-c", code, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def run_crash(exp: Path, crash: str, *, path="data", artifact_id="data") -> subprocess.CompletedProcess:
    return run_child(_CRASH_DRIVER, str(SRC), str(exp), path, artifact_id, crash)


_FIFO_DRIVER = r"""
import os, sys
repo, target, path, artifact_id = sys.argv[1:5]
sys.path.insert(0, repo)
from runevidence import directory_publication as dp
from runevidence.manifest import load_raw
from runevidence.directory_authoring import propose_add_directory

manifest = os.path.join(target, "runevidence.json")
with dp.manifest_admission_lock(target) as locked:
    loaded = dp.load_manifest_under_lock(locked, target)
    dp.preflight_directory_publication(locked)
    proposal = propose_add_directory(loaded, load_raw(loaded), path=path, artifact_id=artifact_id)
    os.unlink(manifest)
    os.mkfifo(manifest)
    try:
        dp.publish_directory_proposal(locked, proposal)
    except dp.DirectoryPublicationError as exc:
        print(exc.reason, exc.phase, flush=True)
        os._exit(0)
print("no-refusal", flush=True)
os._exit(1)
"""


def run_fifo_swap(exp: Path, *, timeout: int = 20) -> subprocess.CompletedProcess:
    return run_child(_FIFO_DRIVER, str(SRC), str(exp), "data", "data", timeout=timeout)


def open_fd_count() -> int:
    return len(os.listdir("/proc/self/fd"))


class Holder:
    def __init__(self, exp: Path, *, mode="none"):
        self.proc = subprocess.Popen(
            [sys.executable, "-B", "-c", _HOLDER_DRIVER, str(SRC), str(exp), "data", "data", mode],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        line = self.proc.stdout.readline().strip()
        assert line == "locked", (line, self.proc.stderr.read() if self.proc.poll() is not None else "")

    def release(self) -> None:
        if self.proc.poll() is None:
            self.proc.stdin.write("\n")
            self.proc.stdin.flush()
        self.proc.wait(timeout=TIMEOUT)

    def kill(self) -> None:
        if self.proc.poll() is None:
            os.kill(self.proc.pid, signal.SIGKILL)
        self.proc.wait(timeout=TIMEOUT)


# --- lock: acquisition, self-busy, winner, SIGKILL, fork/exec -----------------


def test_lock_acquires_a_directory_fd_without_artifacts(exp):
    (directory, _) = exp
    before = snapshot_names_types(directory)
    with dp.manifest_admission_lock(directory) as locked:
        st = os.fstat(locked.fd)
        assert stat.S_ISDIR(st.st_mode)
        assert (st.st_dev, st.st_ino) == locked.identity
        assert fcntl.fcntl(locked.fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
        assert locked.pid == os.getpid()
    assert snapshot_names_types(directory) == before
    assert not any(name.endswith(".lock") or name == "lock" for name in names(directory))


def test_lock_acquisition_is_separate_from_loading(ext4):
    folder = ext4 / "no-manifest"
    folder.mkdir()
    with dp.manifest_admission_lock(folder) as locked:
        assert locked.loaded is None
        refused("not-found", lambda: dp.load_manifest_under_lock(locked, folder))
    # The lock is released; a writer can proceed once a manifest exists.
    raw = base_raw()
    (folder / MANIFEST_FILENAME).write_bytes(dump_raw(raw).encode())
    with dp.manifest_admission_lock(folder) as locked:
        loaded = dp.load_manifest_under_lock(locked, folder)
        assert loaded.source_bytes == dump_raw(raw).encode()


def test_second_acquisition_in_the_same_process_is_busy(exp):
    (directory, _) = exp
    with dp.manifest_admission_lock(directory):
        refused("busy", lambda: dp.manifest_admission_lock(directory).__enter__())


def test_second_acquisition_in_another_process_is_busy_while_winner_holds(exp):
    (directory, _) = exp
    holder = Holder(directory)
    try:
        refused("busy", lambda: dp.manifest_admission_lock(directory).__enter__())
        assert holder.proc.poll() is None, "the winner must still hold the lock when the loser is refused"
    finally:
        holder.release()
    # The winner released; acquisition succeeds now.
    with dp.manifest_admission_lock(directory):
        pass


def test_busy_refusal_happens_before_any_scan(exp, monkeypatch):
    (directory, _) = exp
    calls = []
    real = ds.scan_directory
    monkeypatch.setattr(ds, "scan_directory", lambda *a, **k: calls.append(a) or real(*a, **k))
    holder = Holder(directory)
    try:
        refused("busy", lambda: publish_add(directory))
        assert calls == []
        assert not stage_cells(directory) and not backups(directory)
    finally:
        holder.release()


def test_a_symlink_alias_to_the_manifest_is_accepted_and_the_target_is_replaced(exp):
    (directory, _) = exp
    alias = directory / "alias.json"
    alias.symlink_to(directory / MANIFEST_FILENAME)
    loaded = load_manifest(alias)
    with dp.manifest_admission_lock(alias) as locked:
        assert locked.target == (directory / MANIFEST_FILENAME).resolve()
        loaded = dp.load_manifest_under_lock(locked, alias)
        proposal = propose_add_directory(loaded, load_raw(loaded), path="data", artifact_id="data")
        assert dp.publish_directory_proposal(locked, proposal).status == "written"
    assert alias.is_symlink()
    assert_complete_manifest(directory, new=True)


def test_a_hardlinked_manifest_is_refused(ext4, tmp_path):
    exp, _ = make_exp(tmp_path)
    os.link(exp / MANIFEST_FILENAME, tmp_path / "other-name.json")
    before = manifest_bytes(exp)
    refused("invalid-path", lambda: dp.manifest_admission_lock(exp).__enter__())
    assert (tmp_path / "other-name.json").read_bytes() == before


def test_two_processes_serialize_two_adds(ext4, tmp_path):
    exp, _ = make_exp(tmp_path)
    (exp / "data2").mkdir()
    holder = Holder(exp, mode="add")
    try:
        refused("busy", lambda: publish_add(exp, path="data2", artifact_id="second"))
        assert holder.proc.poll() is None
    finally:
        holder.release()
    assert holder.proc.returncode == 0, holder.proc.stderr.read()
    assert any(a["id"] == "data" for a in manifest_json(exp)["artifacts"])
    result = publish_add(exp, path="data2", artifact_id="second")
    assert result.status == "written"
    assert {a["id"] for a in manifest_json(exp)["artifacts"]} >= {"data", "second"}


def test_sigkill_releases_the_lock_and_leaves_no_lock_artifact(exp):
    (directory, _) = exp
    holder = Holder(directory)
    holder.kill()
    assert holder.proc.returncode == -signal.SIGKILL
    with dp.manifest_admission_lock(directory):
        pass
    assert not any("cooperative-writer.lock" in name for name in names(directory))


def test_forked_child_cannot_use_the_handle(exp):
    if not hasattr(os, "fork"):
        pytest.skip("no os.fork on this platform")
    (directory, _) = exp
    with dp.manifest_admission_lock(directory) as locked:
        pid = os.fork()
        if pid == 0:  # pragma: no cover - child branch
            try:
                locked.verify_owner()
            except dp.DirectoryPublicationError as exc:
                os._exit(0 if exc.reason == "lock-ownership-lost" else 1)
            except BaseException:
                os._exit(2)
            os._exit(3)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0, "a forked child must not treat the handle as usable"
        # The parent still holds the one acquisition and can publish.
        loaded = dp.load_manifest_under_lock(locked, directory)
        proposal = propose_add_directory(loaded, load_raw(loaded), path="data", artifact_id="data")
        assert dp.publish_directory_proposal(locked, proposal).status == "written"


def test_exec_does_not_inherit_the_lock_descriptor(exp):
    (directory, _) = exp
    code = (
        "import os,sys\n"
        "try:\n"
        "    target=os.readlink(f'/proc/self/fd/{sys.argv[1]}')\n"
        "except OSError:\n"
        "    print('closed')\n"
        "else:\n"
        "    print('open', target)\n"
    )
    with dp.manifest_admission_lock(directory) as locked:
        fd = locked.fd
        assert fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
        # close_fds=False is deliberate: the O_CLOEXEC bit, not subprocess's
        # default close, must close the lock descriptor across exec.
        inherited = subprocess.run(
            [sys.executable, "-I", "-S", "-B", "-c", code, str(fd)],
            capture_output=True, text=True, timeout=TIMEOUT, close_fds=False,
        )
        assert inherited.returncode == 0, inherited.stderr
        assert inherited.stdout.strip() == "closed", inherited.stdout

        # Control: an inheritable duplicate of the same directory fd survives
        # the same close_fds=False exec, proving the check can see inheritance.
        duplicate = os.dup(fd)
        try:
            os.set_inheritable(duplicate, True)
            assert not fcntl.fcntl(duplicate, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
            control = subprocess.run(
                [sys.executable, "-I", "-S", "-B", "-c", code, str(duplicate)],
                capture_output=True, text=True, timeout=TIMEOUT, close_fds=False,
            )
        finally:
            os.close(duplicate)
        assert control.returncode == 0, control.stderr
        assert control.stdout.startswith("open "), control.stdout


def test_the_protocol_never_forks_or_spawns():
    source = (Path(runevidence.__file__).parent / "directory_publication.py").read_text(encoding="utf-8")
    for forbidden in ("subprocess", "multiprocessing", "os.fork", "os.spawn", "os.exec", "Popen"):
        assert forbidden not in source, forbidden


# --- dry run: no lock, no fsync, no artifacts --------------------------------


def test_dry_run_takes_no_lock_and_no_fsync_and_writes_nothing(exp, monkeypatch):
    (directory, _) = exp
    before = snapshot_names_types(directory)
    before_bytes = manifest_bytes(directory)

    def fail(*args, **kwargs):
        raise AssertionError("a dry run must not fsync")

    flocked = []
    real_flock = fcntl.flock
    monkeypatch.setattr(dp, "_fsync", fail)
    monkeypatch.setattr(fcntl, "flock", lambda *a, **k: flocked.append(a) or real_flock(*a, **k))
    result = dry_run(directory)
    assert result.status == "dry-run"
    assert result.backup_path is None
    assert flocked == []
    assert snapshot_names_types(directory) == before
    assert manifest_bytes(directory) == before_bytes


def test_dry_run_while_another_process_holds_the_lock(exp):
    (directory, _) = exp
    holder = Holder(directory)
    try:
        result = dry_run(directory)
        assert result.status == "dry-run"
        assert not stage_cells(directory) and not backups(directory)
    finally:
        holder.release()


def test_dry_run_does_not_require_write_permission(ext4, tmp_path):
    exp, _ = make_exp(tmp_path)
    manifest = exp / MANIFEST_FILENAME
    old = manifest.read_bytes()
    os.chmod(manifest, 0o444)
    os.chmod(exp, 0o555)
    try:
        assert dry_run(exp).status == "dry-run"
        assert manifest.read_bytes() == old
        assert not stage_cells(exp) and not backups(exp)
    finally:
        os.chmod(exp, 0o755)
        os.chmod(manifest, 0o644)


def test_dry_run_unchanged_rehash_reports_unchanged(exp):
    (directory, _) = exp
    publish_add(directory)
    loaded = load_manifest(directory)
    proposal = propose_rehash_directory(loaded, load_raw(loaded), artifact_id="data")
    result = dp.publish_directory_proposal(None, proposal, dry_run=True, loaded=loaded)
    assert result.status == "unchanged"


# --- core flow: add/rehash, digest/size, idempotence, scans ------------------


def test_successful_add_on_ext4_writes_the_record_and_a_fresh_backup(exp):
    (directory, raw) = exp
    old = manifest_bytes(directory)
    expected = scan_directory(directory / "data").digest
    result = publish_add(directory, kind="dataset")
    assert result.status == "written" and result.written
    assert result.new_sha256 == expected.sha256
    assert result.new_size == expected.size_bytes == 5
    assert result.members == expected.members == 1
    assert result.backup_path == directory / (MANIFEST_FILENAME + ".bak")
    assert result.backup_path.read_bytes() == old
    raw_new = assert_complete_manifest(directory, new=True)
    entry = next(a for a in raw_new["artifacts"] if a["id"] == "data")
    assert entry["sha256"] == expected.sha256 and entry["size_bytes"] == 5
    assert entry["artifact_type"] == "directory" and entry["kind"] == "dataset"
    assert entry["run_id"] is None
    assert sorted(names(directory)) == ["data", "runevidence.json", "runevidence.json.bak"]
    assert stat.S_IMODE((directory / "runevidence.json.bak").stat().st_mode) == 0o600


def test_rehash_changes_only_digest_and_size_and_is_idempotent(exp):
    (directory, _) = exp
    publish_add(directory)
    first = manifest_json(directory)
    (directory / "data" / "a.txt").write_bytes(b"hello world")
    changed = publish_rehash(directory)
    second = manifest_json(directory)
    assert changed.status == "written"
    old_entry = first["artifacts"][-1]
    new_entry = second["artifacts"][-1]
    assert new_entry["sha256"] != old_entry["sha256"]
    assert new_entry["size_bytes"] == 11
    assert {k: v for k, v in new_entry.items() if k not in ("sha256", "size_bytes")} == {
        k: v for k, v in old_entry.items() if k not in ("sha256", "size_bytes")
    }
    before = sorted(names(directory))
    unchanged = publish_rehash(directory)
    assert unchanged.status == "unchanged" and unchanged.backup_path is None
    assert sorted(names(directory)) == before  # no cell, no new backup


def test_unchanged_rehash_creates_no_cell_or_backup(exp):
    (directory, _) = exp
    publish_add(directory)
    (directory / (MANIFEST_FILENAME + ".bak")).unlink()
    result = publish_rehash(directory)
    assert result.status == "unchanged"
    assert not stage_cells(directory) and not backups(directory)


def test_add_is_not_idempotent_and_leaves_bytes_untouched(exp):
    (directory, _) = exp
    publish_add(directory)
    before = manifest_bytes(directory)
    names_before = sorted(names(directory))
    with pytest.raises(DirectoryAuthoringError) as info:
        publish_add(directory)
    assert info.value.reason == "duplicate-id"
    assert manifest_bytes(directory) == before
    assert sorted(names(directory)) == names_before


def test_proposal_refusals_are_fixed_and_map_to_publication_reasons():
    mapped = dp._map_authoring(
        DirectoryAuthoringError("duplicate-id", "the canary member-canary.txt at /abs/root-canary")
    )
    assert mapped.reason == "duplicate-id" and mapped.phase == "before-cell"
    assert "member-canary" not in str(mapped) and "/abs/root-canary" not in str(mapped)
    assert dp._map_authoring(DirectoryAuthoringError("scan-failed", "x")).reason == "recheck-failed"
    assert dp._map_authoring(DirectoryAuthoringError("limit-exceeded", "x")).reason == "limit-exceeded"


def test_real_write_performs_exactly_two_native_scans(ext4, tmp_path, monkeypatch):
    exp, _ = make_exp(tmp_path)
    calls = []
    real = ds.scan_directory

    def counting(root, *args, **kwargs):
        calls.append(Path(root))
        return real(root, *args, **kwargs)

    monkeypatch.setattr(ds, "scan_directory", counting)
    publish_add(exp)
    assert len(calls) == 2, calls
    calls.clear()
    other, _ = make_exp(tmp_path, name="exp2", folder="other")
    loaded = load_manifest(other)
    proposal = propose_add_directory(loaded, load_raw(loaded), path="other", artifact_id="other")
    assert dp.publish_directory_proposal(None, proposal, dry_run=True, loaded=loaded).status == "dry-run"
    assert len(calls) == 2, calls


def test_fsync_order_is_source_then_staged_metadata_then_backup_then_replace(exp, monkeypatch):
    (directory, _) = exp
    roles = []
    real = dp._fsync

    def recording(fd, *, role):
        roles.append(role)
        return real(fd, role=role)

    monkeypatch.setattr(dp, "_fsync", recording)
    publish_add(directory)
    assert roles == [
        "source-file", "source-parent", "staged", "staged-metadata",
        "backup", "backup-parent", "replace-parent",
    ], roles


def test_preflight_can_run_before_the_proposal_scan(exp, monkeypatch):
    (directory, _) = exp
    calls = []
    roles = []
    real_scan = ds.scan_directory
    real_fsync = dp._fsync
    monkeypatch.setattr(ds, "scan_directory", lambda *a, **k: calls.append(a) or real_scan(*a, **k))
    monkeypatch.setattr(dp, "_fsync", lambda fd, *, role: roles.append(role) or real_fsync(fd, role=role))

    with dp.manifest_admission_lock(directory) as locked:
        loaded = dp.load_manifest_under_lock(locked, directory)
        preflight = dp.preflight_directory_publication(locked)
        assert preflight.metadata.mode == 0o644
        assert calls == []
        proposal = propose_add_directory(loaded, load_raw(loaded), path="data", artifact_id="data")
        assert dp.publish_directory_proposal(locked, proposal).status == "written"
    assert roles.count("source-file") == 1 and roles.count("source-parent") == 1
    assert roles == [
        "source-file", "source-parent", "staged", "staged-metadata",
        "backup", "backup-parent", "replace-parent",
    ]


def test_preflight_refuses_a_read_only_manifest_before_scanning(ext4, tmp_path, monkeypatch):
    exp, _ = make_exp(tmp_path, mode=0o444)
    calls = []
    real = ds.scan_directory
    monkeypatch.setattr(ds, "scan_directory", lambda *a, **k: calls.append(a) or real(*a, **k))
    with dp.manifest_admission_lock(exp) as locked:
        dp.load_manifest_under_lock(locked, exp)
        refused("permission-denied", lambda: dp.preflight_directory_publication(locked), phase="before-cell")
    assert calls == []


def test_source_durability_failure_refuses_before_any_cell(exp, monkeypatch):
    (directory, _) = exp
    before = manifest_bytes(directory)
    real = dp._fsync

    def failing(fd, *, role):
        if role == "source-file":
            raise OSError(errno.EIO, "injected source fsync failure")
        return real(fd, role=role)

    monkeypatch.setattr(dp, "_fsync", failing)
    refused("source-durability-failed", lambda: publish_add(directory), phase="before-cell")
    assert manifest_bytes(directory) == before
    assert not stage_cells(directory) and not backups(directory)


def test_staging_fsync_failure_refuses_and_cleans_the_cell(exp, monkeypatch):
    (directory, _) = exp
    before = manifest_bytes(directory)
    real = dp._fsync

    def failing(fd, *, role):
        if role == "staged":
            raise OSError(errno.EIO, "injected staged fsync failure")
        return real(fd, role=role)

    monkeypatch.setattr(dp, "_fsync", failing)
    refused("write-failed", lambda: publish_add(directory), phase="cell-or-backup")
    assert manifest_bytes(directory) == before
    assert not stage_cells(directory) and not backups(directory)


def test_final_parent_fsync_failure_reports_written_plus_durability_unknown(exp, monkeypatch):
    (directory, _) = exp
    real = dp._fsync

    def failing(fd, *, role):
        if role == "replace-parent":
            raise OSError(errno.EIO, "injected parent fsync failure")
        return real(fd, role=role)

    monkeypatch.setattr(dp, "_fsync", failing)
    result = publish_add(directory)
    assert result.status == "written" and result.warnings == ("durability-unknown",)
    assert_complete_manifest(directory, new=True)


# --- metadata: mode, owner/group, ACL, xattr, flags, project ID --------------


@pytest.mark.parametrize("mode", [0o600, 0o640, 0o660, 0o755])
def test_mode_uid_and_gid_are_preserved_exactly(ext4, tmp_path, mode):
    exp, _ = make_exp(tmp_path, mode=mode)
    manifest = exp / MANIFEST_FILENAME
    result = publish_add(exp)
    assert result.status == "written"
    st = manifest.stat()
    assert stat.S_IMODE(st.st_mode) == mode
    assert (st.st_uid, st.st_gid) == (os.geteuid(), os.getegid())


def test_unsupported_platform_is_refused_without_fallback(exp, monkeypatch):
    (directory, _) = exp
    monkeypatch.setattr(dp, "_platform_supported", lambda: False)
    refused("unsupported-platform", lambda: publish_add(directory), phase="before-cell")
    refused("unsupported-platform", lambda: dry_run(directory), phase="before-cell")


def test_read_only_manifest_is_refused_before_the_checked_rebuild(ext4, tmp_path, monkeypatch):
    exp, _ = make_exp(tmp_path, mode=0o444)
    before = manifest_bytes(exp)
    calls = []
    real = ds.scan_directory
    monkeypatch.setattr(ds, "scan_directory", lambda *a, **k: calls.append(a) or real(*a, **k))
    refused("permission-denied", lambda: publish_add(exp), phase="before-cell")
    # The proposal itself scans once; the permission check refuses before the
    # checked rebuild, the cell and any write.
    assert len(calls) == 1
    assert manifest_bytes(exp) == before
    assert not stage_cells(exp) and not backups(exp)


@pytest.mark.skipif(SETFACL is None or GETFACL is None, reason="setfacl/getfacl unavailable: ACL evidence missing")
def test_access_acl_is_copied_exactly(exp):
    (directory, _) = exp
    manifest = directory / MANIFEST_FILENAME
    subprocess.run([SETFACL, "-m", "u:65534:r", str(manifest)], check=True)
    before = subprocess.run([GETFACL, "-cp", str(manifest)], capture_output=True, text=True, check=True).stdout
    publish_add(directory)
    after = subprocess.run([GETFACL, "-cp", str(manifest)], capture_output=True, text=True, check=True).stdout
    assert after == before
    assert "user:nobody:r--" in after


@pytest.mark.skipif(SETFACL is None, reason="setfacl unavailable: default-ACL evidence missing")
def test_inherited_default_acl_cannot_reach_the_cell_or_staged_file(exp, monkeypatch):
    (directory, _) = exp
    # The folder grants a second user (nobody, 65534) full access by default.
    subprocess.run([SETFACL, "-d", "-m", "u:65534:rwx", str(directory)], check=True)
    source = directory / MANIFEST_FILENAME
    source_acl = None
    if GETFACL:
        source_acl = subprocess.run([GETFACL, "-cp", str(source)], capture_output=True, text=True).stdout
    boundaries = []

    def private_cell(locked, cell_name, cell_fd):
        st = os.fstat(cell_fd)
        access = os.getxattr(cell_fd, "system.posix_acl_access") if "system.posix_acl_access" in os.listxattr(cell_fd) else None
        assert stat.S_IMODE(st.st_mode) == 0o700
        assert access is None or dp._acl_is_owner_only(access)
        boundaries.append("cell")

    def private_file(cell_fd, staged_fd):
        st = os.fstat(staged_fd)
        access = os.getxattr(staged_fd, "system.posix_acl_access") if "system.posix_acl_access" in os.listxattr(staged_fd) else None
        assert stat.S_IMODE(st.st_mode) in (0o600, 0o640, 0o644)
        assert access is None or dp._acl_is_owner_only(access)
        boundaries.append("file")

    def private_after_metadata(cell_fd, staged_fd):
        st = os.fstat(staged_fd)
        assert stat.S_IMODE(st.st_mode) == stat.S_IMODE(source.stat().st_mode)
        boundaries.append("metadata")

    def private_backup(locked, cell_fd):
        boundaries.append("backup")

    def private_backup_created(cell_fd, backup_fd):
        st = os.fstat(backup_fd)
        attrs = os.listxattr(backup_fd)
        assert stat.S_IMODE(st.st_mode) == 0o600
        assert "system.posix_acl_access" not in attrs
        assert not any(a.startswith("user.") for a in attrs)
        boundaries.append("backup-created")

    monkeypatch.setattr(dp, "_boundary_cell_created", private_cell)
    monkeypatch.setattr(dp, "_boundary_staged_created", private_file)
    monkeypatch.setattr(dp, "_boundary_staged_written", private_file)
    monkeypatch.setattr(dp, "_boundary_staged_metadata", private_after_metadata)
    monkeypatch.setattr(dp, "_boundary_backup_created", private_backup_created)
    monkeypatch.setattr(dp, "_boundary_backup_written", private_backup)
    result = publish_add(directory)
    assert result.status == "written"
    assert boundaries == ["cell", "file", "file", "metadata", "backup-created", "backup"], boundaries
    assert not stage_cells(directory)
    if GETFACL:
        final = subprocess.run([GETFACL, "-cp", str(source)], capture_output=True, text=True).stdout
        assert final == source_acl


def _can_drop_to_second_uid() -> bool:
    try:
        proc = subprocess.run(
            ["setpriv", "--reuid=65534", "--regid=65534", "--clear-groups", "true"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def _second_user_can_read(path: Path) -> bool:
    proc = subprocess.run(
        ["setpriv", "--reuid=65534", "--regid=65534", "--clear-groups", "test", "-r", str(path)],
        capture_output=True, text=True, timeout=10,
    )
    assert proc.returncode in (0, 1), proc.stderr
    return proc.returncode == 0


def _owner_only(path: Path, expected_mode: int) -> None:
    st = os.lstat(path)
    assert stat.S_IMODE(st.st_mode) == expected_mode, (path.name, oct(stat.S_IMODE(st.st_mode)))
    if "system.posix_acl_access" in os.listxattr(path):
        assert dp._acl_is_owner_only(os.getxattr(path, "system.posix_acl_access"))


def test_second_user_access_at_every_publication_boundary(ext4, tmp_path, monkeypatch):
    """Exercise cell, staged-file and backup exposure at each boundary.

    Where this process can drop to uid 65534, every boundary is probed live.
    Where it cannot (unprivileged host), the same boundaries are still checked
    by mode/ACL and the missing live-user evidence is reported as a skip."""
    exp, _ = make_exp(tmp_path, mode=0o600)
    live = _can_drop_to_second_uid()
    state = {"cell": None, "staged": None}
    live_results = []

    def check(path: Path, mode: int, where: str) -> None:
        _owner_only(path, mode)
        if live:
            assert _second_user_can_read(path) is False, f"second user can read {where}"
            live_results.append(where)

    def cell_ready(locked, cell_name, cell_fd):
        state["cell"] = exp / cell_name
        check(state["cell"], 0o700, "cell")

    def staged_created(cell_fd, staged_fd):
        state["staged"] = state["cell"] / "new"
        check(state["staged"], 0o600, "staged-created")

    def staged_written(cell_fd, staged_fd):
        check(state["staged"], 0o600, "staged-written")

    def staged_metadata(cell_fd, staged_fd):
        check(state["staged"], 0o600, "staged-metadata")

    def backup_created(cell_fd, backup_fd):
        check(state["cell"] / "backup", 0o600, "backup-created")

    monkeypatch.setattr(dp, "_boundary_cell_created", cell_ready)
    monkeypatch.setattr(dp, "_boundary_staged_created", staged_created)
    monkeypatch.setattr(dp, "_boundary_staged_written", staged_written)
    monkeypatch.setattr(dp, "_boundary_staged_metadata", staged_metadata)
    monkeypatch.setattr(dp, "_boundary_backup_created", backup_created)
    assert publish_add(exp).status == "written"
    assert not stage_cells(exp)
    if live:
        assert live_results == [
            "cell", "staged-created", "staged-written", "staged-metadata", "backup-created",
        ], live_results
    else:
        pytest.skip(
            "cannot drop to a second uid: live second-user access evidence missing "
            "(mode/ACL checks ran at every publication boundary)"
        )


def test_inherited_xattrs_are_removed_before_the_first_content_write(exp, monkeypatch):
    """M7: stripping must happen before content, not just by the end.

    The observation hooks fail the test if the injected xattr is still visible
    at the cell-ready boundary or at the first write to the staged fd. The
    companion mutation below disables the stripper and proves these hooks see
    the xattr, so deleting the removal cannot silently pass.
    """
    (directory, _) = exp
    seen = {"cell": None, "staged": None, "first_write_saw_xattr": None}

    def inject_cell(locked, cell_name, cell_fd):
        os.setxattr(cell_fd, "user.inherited", b"cell")

    def check_cell(locked, cell_name, cell_fd):
        seen["cell"] = "user.inherited" in os.listxattr(cell_fd)
        assert seen["cell"] is False, "the cell still has an inherited xattr before content"

    def inject_staged(cell_fd, staged_fd):
        seen["staged"] = staged_fd
        os.setxattr(staged_fd, "user.inherited", b"staged")

    real_write = dp._write_all

    def checking_write(fd, data):
        if fd == seen["staged"] and seen["first_write_saw_xattr"] is None:
            seen["first_write_saw_xattr"] = "user.inherited" in os.listxattr(fd)
            assert seen["first_write_saw_xattr"] is False, (
                "the staged file still has an inherited xattr at the first content write"
            )
        return real_write(fd, data)

    monkeypatch.setattr(dp, "_boundary_cell_before_restrict", inject_cell)
    monkeypatch.setattr(dp, "_boundary_cell_created", check_cell)
    monkeypatch.setattr(dp, "_boundary_staged_created", inject_staged)
    monkeypatch.setattr(dp, "_write_all", checking_write)
    assert publish_add(directory).status == "written"
    assert seen["cell"] is False
    assert seen["first_write_saw_xattr"] is False
    assert "user.inherited" not in os.listxattr(directory / MANIFEST_FILENAME)


def test_m7_mutation_disabling_stripping_is_caught_before_content(exp, monkeypatch):
    """Deleting the strippers must be caught: the injected attributes are then
    visible at the first write/cell-ready check, and publication refuses."""
    (directory, _) = exp
    seen = {"cell": None, "staged": None, "first_write_saw_xattr": None}

    def inject_cell(locked, cell_name, cell_fd):
        os.setxattr(cell_fd, "user.inherited", b"cell")

    def check_cell(locked, cell_name, cell_fd):
        seen["cell"] = "user.inherited" in os.listxattr(cell_fd)

    def inject_staged(cell_fd, staged_fd):
        seen["staged"] = staged_fd
        os.setxattr(staged_fd, "user.inherited", b"staged")

    real_write = dp._write_all

    def checking_write(fd, data):
        if fd == seen["staged"] and seen["first_write_saw_xattr"] is None:
            seen["first_write_saw_xattr"] = "user.inherited" in os.listxattr(fd)
        return real_write(fd, data)

    monkeypatch.setattr(dp, "_remove_cell_xattrs", lambda fd: None)
    monkeypatch.setattr(dp, "_remove_inherited_xattrs", lambda fd, source: None)
    monkeypatch.setattr(dp, "_boundary_cell_before_restrict", inject_cell)
    monkeypatch.setattr(dp, "_boundary_cell_created", check_cell)
    monkeypatch.setattr(dp, "_boundary_staged_created", inject_staged)
    monkeypatch.setattr(dp, "_write_all", checking_write)
    refused("metadata-unsupported", lambda: publish_add(directory), phase="cell-or-backup")
    assert seen["cell"] is True
    assert seen["first_write_saw_xattr"] is True
    assert not stage_cells(directory) and not backups(directory)


def test_m18_cell_inherited_xattrs_are_stripped_before_content(exp, monkeypatch):
    """M18: a cell made under a permissive inherited environment must be clean
    before the staged file is created."""
    (directory, _) = exp
    seen = {"before": None, "after": None}

    def inject(locked, cell_name, cell_fd):
        os.setxattr(cell_fd, "user.m18", b"cell-canary")
        seen["before"] = "user.m18" in os.listxattr(cell_fd)

    def observe(locked, cell_name, cell_fd):
        seen["after"] = "user.m18" in os.listxattr(cell_fd)

    monkeypatch.setattr(dp, "_boundary_cell_before_restrict", inject)
    monkeypatch.setattr(dp, "_boundary_cell_created", observe)
    assert publish_add(directory).status == "written"
    assert seen == {"before": True, "after": False}, seen


def test_m18_mutation_without_the_cell_strip_is_detected(exp, monkeypatch):
    (directory, _) = exp
    seen = {"after": None}

    def inject(locked, cell_name, cell_fd):
        os.setxattr(cell_fd, "user.m18", b"cell-canary")

    def observe(locked, cell_name, cell_fd):
        seen["after"] = "user.m18" in os.listxattr(cell_fd)

    monkeypatch.setattr(dp, "_remove_cell_xattrs", lambda fd: None)
    monkeypatch.setattr(dp, "_boundary_cell_before_restrict", inject)
    monkeypatch.setattr(dp, "_boundary_cell_created", observe)
    assert publish_add(directory).status == "written"
    assert seen["after"] is True, "the M18 check must fail when cell stripping is removed"


def test_m23_backup_inherited_xattrs_are_stripped_before_publication(exp, monkeypatch):
    """M23: the backup must carry no inherited non-security xattr when the
    .bak.N name is published."""
    (directory, _) = exp
    seen = {"before": None, "created": None, "written": None}

    def inject(cell_fd, backup_fd):
        os.setxattr(backup_fd, "user.m23", b"backup-canary")
        seen["before"] = "user.m23" in os.listxattr(backup_fd)

    def created(cell_fd, backup_fd):
        seen["created"] = "user.m23" in os.listxattr(backup_fd)

    def written(locked, cell_fd):
        cell = next(directory.glob(dp.CELL_PREFIX + "*"))
        backup = cell / dp.BACKUP_TEMP_NAME
        attrs = os.listxattr(backup) if backup.exists() else []
        seen["written"] = "user.m23" in attrs

    monkeypatch.setattr(dp, "_boundary_backup_before_restrict", inject)
    monkeypatch.setattr(dp, "_boundary_backup_created", created)
    monkeypatch.setattr(dp, "_boundary_backup_written", written)
    assert publish_add(directory).status == "written"
    assert seen == {"before": True, "created": False, "written": False}, seen
    assert "user.m23" not in os.listxattr(directory / (MANIFEST_FILENAME + ".bak"))


def test_m23_mutation_without_the_backup_strip_is_detected(exp, monkeypatch):
    (directory, _) = exp
    seen = {"written": None}

    def inject(cell_fd, backup_fd):
        os.setxattr(backup_fd, "user.m23", b"backup-canary")

    def written(locked, cell_fd):
        cell = next(directory.glob(dp.CELL_PREFIX + "*"))
        backup = cell / dp.BACKUP_TEMP_NAME
        seen["written"] = "user.m23" in os.listxattr(backup) if backup.exists() else False

    monkeypatch.setattr(dp, "_remove_all_non_security_xattrs", lambda fd: None)
    monkeypatch.setattr(dp, "_boundary_backup_before_restrict", inject)
    monkeypatch.setattr(dp, "_boundary_backup_written", written)
    assert publish_add(directory).status == "written"
    assert seen["written"] is True, "the M23 check must fail when backup stripping is removed"


def test_inherited_non_security_xattrs_are_removed_before_content(exp, monkeypatch):
    (directory, _) = exp
    manifest = directory / MANIFEST_FILENAME
    real_cell_hook = dp._boundary_cell_before_restrict

    def inject_cell(locked, cell_name, cell_fd):
        os.setxattr(cell_fd, "user.inherited", b"cell")
        real_cell_hook(locked, cell_name, cell_fd)

    def inject_file(cell_fd, staged_fd):
        os.setxattr(staged_fd, "user.inherited", b"file")

    monkeypatch.setattr(dp, "_boundary_cell_before_restrict", inject_cell)
    monkeypatch.setattr(dp, "_boundary_staged_created", inject_file)
    assert publish_add(directory).status == "written"
    assert "user.inherited" not in os.listxattr(manifest)


def test_source_user_xattr_is_copied_to_the_manifest(exp):
    (directory, _) = exp
    manifest = directory / MANIFEST_FILENAME
    os.setxattr(manifest, "user.evidence", b"keep-me")
    publish_add(directory)
    assert os.getxattr(manifest, "user.evidence") == b"keep-me"
    # The backup is a recovery copy: 0600 with no custom xattr.
    assert "user.evidence" not in os.listxattr(directory / (MANIFEST_FILENAME + ".bak"))


def test_security_xattr_is_never_deleted_and_fails_closed_if_unverifiable(exp, monkeypatch):
    (directory, _) = exp
    manifest = directory / MANIFEST_FILENAME
    if "security.selinux" in os.listxattr(manifest):
        publish_add(directory)
        assert "security.selinux" in os.listxattr(manifest)
        return
    removed = []
    real_remove = os.removexattr

    def record_remove(path, name, *args, **kwargs):
        removed.append(name)
        return real_remove(path, name, *args, **kwargs)

    monkeypatch.setattr(os, "removexattr", record_remove)
    real_capture = dp._capture_metadata
    injected = {"fd": None}

    def capture(fd):
        snapshot = real_capture(fd)
        if injected["fd"] is not None and fd == injected["fd"]:
            return dp.MetadataSnapshot(
                snapshot.mode, snapshot.uid, snapshot.gid,
                tuple(sorted({*snapshot.xattrs, ("security.selinux", b"policy-label")})),
                snapshot.inode_flags, snapshot.project, snapshot.notes,
            )
        return snapshot

    def inject(cell_fd, staged_fd):
        injected["fd"] = staged_fd

    monkeypatch.setattr(dp, "_capture_metadata", capture)
    monkeypatch.setattr(dp, "_boundary_staged_written", inject)
    refused("metadata-unsupported", lambda: publish_add(directory), phase="cell-or-backup")
    assert "security.selinux" not in removed
    assert "security.selinux" not in os.listxattr(manifest)


def test_unknown_system_xattr_namespace_refuses():
    snapshot = dp.MetadataSnapshot(
        0o600, os.geteuid(), os.getegid(),
        (("system.unknown", b"x"),), 0x80000, None, (),
    )
    with pytest.raises(dp.DirectoryPublicationError) as info:
        dp._preflight_metadata(snapshot)
    assert info.value.reason == "metadata-unsupported"


def test_inode_user_settable_flags_are_preserved_exactly(exp):
    (directory, _) = exp
    manifest = directory / MANIFEST_FILENAME
    if not ioctl_available(manifest):
        pytest.skip("FS_IOC_GETFLAGS unavailable: inode-flag evidence missing")
    flags = get_inode_flags(manifest)
    set_inode_flags(manifest, flags | 0x40)  # FS_NODUMP_FL, user-settable
    result = publish_add(directory)
    assert result.status == "written"
    assert get_inode_flags(manifest) & 0x40
    assert result.inode_flags & 0x40


def test_immutable_append_and_unknown_inode_flags_refuse(exp, monkeypatch):
    (directory, _) = exp
    for bad in (0x10, 0x20, 0x80000000):
        with pytest.raises(dp.DirectoryPublicationError) as info:
            dp._check_flags(bad)
        assert info.value.reason == "metadata-unsupported"


def test_unavailable_inode_flags_refuse_closed(ext4, tmp_path, monkeypatch):
    exp, _ = make_exp(tmp_path)
    real = dp._read_inode_flags

    def unavailable(fd):
        raise dp.DirectoryPublicationError("metadata-unsupported")

    monkeypatch.setattr(dp, "_read_inode_flags", unavailable)
    refused("metadata-unsupported", lambda: publish_add(exp), phase="before-cell")
    assert not stage_cells(exp) and not backups(exp)


def test_project_id_is_detected_and_preserved_or_narrowed(exp):
    (directory, _) = exp
    manifest = directory / MANIFEST_FILENAME
    fd = os.open(manifest, os.O_RDONLY | os.O_CLOEXEC)
    try:
        project = dp._read_project(fd)
    finally:
        os.close(fd)
    result = publish_add(directory)
    assert result.status == "written"
    if project is None:
        assert any("project-id" in note for note in result.notes)
    else:
        assert result.project_id == project[0]


def test_project_id_exdev_is_a_fixed_write_failure(exp, monkeypatch):
    (directory, _) = exp
    before = manifest_bytes(directory)

    def exdev(*args, **kwargs):
        raise OSError(errno.EXDEV, "injected project-id mismatch")

    monkeypatch.setattr(dp, "_link", exdev)
    error = refused("write-failed", lambda: publish_add(directory), phase="cell-or-backup")
    assert error.note == "project-id-mismatch"
    assert "project-id-mismatch" in str(error)
    assert manifest_bytes(directory) == before
    assert not backups(directory)
    assert not stage_cells(directory)


def test_replace_exdev_is_a_fixed_write_failure(exp, monkeypatch):
    (directory, _) = exp
    before = manifest_bytes(directory)
    monkeypatch.setattr(dp, "_replace_manifest", lambda *a, **k: (_ for _ in ()).throw(OSError(errno.EXDEV, "x")))
    error = refused("write-failed", lambda: publish_add(directory), phase="at-replace")
    assert error.note == "project-id-mismatch"
    assert manifest_bytes(directory) == before
    # The complete backup was already published and is preserved.
    assert (directory / (MANIFEST_FILENAME + ".bak")).read_bytes() == before


# --- descriptors: O_RDWR/pread and dir_fd parent swap ------------------------


def test_staging_and_backup_descriptors_are_open_read_write(exp, monkeypatch):
    (directory, _) = exp
    seen = []

    def staged(cell_fd, staged_fd):
        seen.append(("staged", fcntl.fcntl(staged_fd, fcntl.F_GETFL) & os.O_ACCMODE))

    def backup(cell_fd, backup_fd):
        seen.append(("backup", fcntl.fcntl(backup_fd, fcntl.F_GETFL) & os.O_ACCMODE))

    monkeypatch.setattr(dp, "_boundary_staged_written", staged)
    monkeypatch.setattr(dp, "_boundary_backup_created", backup)
    publish_add(directory)
    assert seen == [("staged", os.O_RDWR), ("backup", os.O_RDWR)], seen


def test_staged_and_backup_bytes_are_read_back_with_pread(exp, monkeypatch):
    (directory, _) = exp
    reads = []
    real = dp._verify_read_back

    def recording(fd, expected):
        reads.append(len(expected))
        assert fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDWR
        return real(fd, expected)

    monkeypatch.setattr(dp, "_verify_read_back", recording)
    publish_add(directory)
    assert len(reads) == 2, reads  # the staged manifest and the exact old backup bytes
    source = (Path(runevidence.__file__).parent / "directory_publication.py").read_text(encoding="utf-8")
    assert "os.pread(" in source and "O_WRONLY" not in source


def test_one_handle_one_flock_acquisition_per_operation(exp, monkeypatch):
    (directory, _) = exp
    acquisitions = []
    real = fcntl.flock

    def counting(fd, operation):
        if operation & fcntl.LOCK_EX:
            acquisitions.append(fd)
        return real(fd, operation)

    monkeypatch.setattr(fcntl, "flock", counting)
    publish_add(directory)
    assert len(acquisitions) == 1, acquisitions


def test_lock_is_released_in_finally_after_an_exception(exp):
    (directory, _) = exp

    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        with dp.manifest_admission_lock(directory):
            raise Boom()
    with dp.manifest_admission_lock(directory):
        pass


def test_replaced_manifest_identity_is_stale_source(exp, monkeypatch):
    (directory, _) = exp
    real_check = dp.check_directory_proposal

    def replace_after_check(loaded, proposal, **kwargs):
        checked = real_check(loaded, proposal, **kwargs)
        path = directory / MANIFEST_FILENAME
        replacement = directory / "replacement.json"
        replacement.write_bytes(manifest_bytes(directory))
        os.replace(replacement, path)
        return checked

    monkeypatch.setattr(dp, "check_directory_proposal", replace_after_check)
    refused("stale-source", lambda: publish_add(directory), phase="before-cell")
    assert not stage_cells(directory) and not backups(directory)


def test_project_id_mismatch_is_refused_before_scanning(exp, monkeypatch):
    if os.geteuid() == 0:
        pytest.skip("root can set a project ID; the non-root preflight refusal is not exercised")
    (directory, _) = exp
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    manifest_fd = os.open(directory / MANIFEST_FILENAME, os.O_RDONLY | os.O_CLOEXEC)
    try:
        monkeypatch.setattr(dp, "_read_project", lambda target: (1234, False))
        fake = dp.MetadataSnapshot(
            0o600, os.geteuid(), os.getegid(), (),
            dp._read_inode_flags(manifest_fd), (5678, False), (),
        )
        locked = dp.LockedManifestFolder(directory, directory, fd, (0, 0), os.getpid())
        with pytest.raises(dp.DirectoryPublicationError) as info:
            dp._preflight_project(locked, fake)
        assert info.value.reason == "metadata-unsupported"
    finally:
        os.close(manifest_fd)
        os.close(fd)


def test_forcing_a_write_only_staging_descriptor_refuses_cleanly(exp, monkeypatch):
    (directory, _) = exp
    before = manifest_bytes(directory)
    real_open = os.open

    def write_only(path, flags, *args, **kwargs):
        if path == dp.STAGED_NAME and kwargs.get("dir_fd") is not None:
            flags = (flags & ~os.O_ACCMODE) | os.O_WRONLY
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", write_only)
    error = refused("write-failed", lambda: publish_add(directory), phase="cell-or-backup")
    assert "EBADF" not in str(error) and "Bad file descriptor" not in str(error)
    assert manifest_bytes(directory) == before
    assert not stage_cells(directory)


def test_m10_parent_swap_to_a_different_inode_refuses_and_cannot_redirect(exp, monkeypatch):
    """M10: the original path must name a *different* inode when the guard runs.

    The replacement folder is built beside the experiment and then renamed onto
    the original path, so the parent-identity comparison is genuinely
    exercised. Without that check the publication would report success here."""
    (directory, _) = exp
    old_bytes = manifest_bytes(directory)
    replacement = directory.parent / "replacement"
    moved = directory.parent / "moved"
    marker = dump_raw(base_raw()).encode()
    state = {}

    def swap(locked, backup_path):
        state["backup"] = backup_path
        state["identity"] = locked.identity
        replacement.mkdir()
        (replacement / MANIFEST_FILENAME).write_bytes(marker)
        replacement_st = os.stat(replacement)
        state["replacement_identity"] = (replacement_st.st_dev, replacement_st.st_ino)
        os.rename(directory, moved)
        # The original path now names the impostor, not a missing path.
        os.replace(replacement, directory)

    monkeypatch.setattr(dp, "_boundary_backup_linked", swap)
    try:
        with dp.manifest_admission_lock(directory) as locked:
            loaded = dp.load_manifest_under_lock(locked, directory)
            proposal = propose_add_directory(loaded, load_raw(loaded), path="data", artifact_id="data")
            with pytest.raises(dp.DirectoryPublicationError) as info:
                dp.publish_directory_proposal(locked, proposal)
        assert info.value.reason == "lock-ownership-lost"
        assert info.value.phase == "cell-or-backup"
        # The original path is now the replacement inode, and it differs from
        # the identity the writer locked.
        impostor = os.stat(directory)
        assert (impostor.st_dev, impostor.st_ino) == state["replacement_identity"]
        assert state["replacement_identity"] != state["identity"]
        assert (os.stat(moved).st_dev, os.stat(moved).st_ino) == state["identity"]
        # Nothing was written through the new path/inode.
        assert (directory / MANIFEST_FILENAME).read_bytes() == marker
        assert not stage_cells(directory) and not backups(directory)
        # The old locked inode still has the untouched manifest and a complete backup.
        assert (moved / MANIFEST_FILENAME).read_bytes() == old_bytes
        assert (moved / (MANIFEST_FILENAME + ".bak")).read_bytes() == old_bytes
        assert not stage_cells(moved)
    finally:
        # The original path is the impostor after a successful swap; make it
        # empty and put the locked folder back so pytest's cleanup is simple.
        if directory.exists() and os.stat(directory).st_ino != state.get("identity", (None, -1))[1]:
            for child in directory.iterdir():
                if child.is_dir():
                    for grandchild in child.iterdir():
                        grandchild.unlink()
                    child.rmdir()
                else:
                    child.unlink()
            directory.rmdir()
        if moved.exists():
            os.rename(moved, directory)


def test_stale_source_before_staging_refuses_without_a_cell(exp, monkeypatch):
    (directory, _) = exp
    old = manifest_bytes(directory)
    real_check = dp.check_directory_proposal

    def rewrite_after_check(loaded, proposal, **kwargs):
        checked = real_check(loaded, proposal, **kwargs)
        (directory / MANIFEST_FILENAME).write_bytes(old + b" ")
        return checked

    monkeypatch.setattr(dp, "check_directory_proposal", rewrite_after_check)
    refused("stale-source", lambda: publish_add(directory), phase="before-cell")
    assert manifest_bytes(directory) == old + b" "
    assert not stage_cells(directory) and not backups(directory)


def test_stale_metadata_before_staging_refuses_without_a_cell(exp, monkeypatch):
    (directory, _) = exp
    real_check = dp.check_directory_proposal

    def chmod_after_check(loaded, proposal, **kwargs):
        checked = real_check(loaded, proposal, **kwargs)
        os.chmod(directory / MANIFEST_FILENAME, 0o600)
        return checked

    monkeypatch.setattr(dp, "check_directory_proposal", chmod_after_check)
    refused("stale-metadata", lambda: publish_add(directory), phase="before-cell")
    assert not stage_cells(directory) and not backups(directory)


def test_final_guard_metadata_failure_after_backup_leaves_the_backup(exp, monkeypatch):
    (directory, _) = exp
    old = manifest_bytes(directory)

    def chmod_after_backup(locked, bak_name):
        os.chmod(directory / MANIFEST_FILENAME, 0o600)

    monkeypatch.setattr(dp, "_boundary_backup_linked", chmod_after_backup)
    refused("stale-metadata", lambda: publish_add(directory), phase="cell-or-backup")
    assert manifest_bytes(directory) == old
    assert (directory / (MANIFEST_FILENAME + ".bak")).read_bytes() == old
    assert not stage_cells(directory)


def test_cell_restriction_failure_refuses_before_any_content(exp, monkeypatch):
    if SETFACL is None:
        pytest.skip("setfacl unavailable: inherited-default-ACL restriction evidence missing")
    (directory, _) = exp
    subprocess.run([SETFACL, "-d", "-m", "u:65534:rwx", str(directory)], check=True)
    real_setxattr = os.setxattr

    def deny_acl(fd, name, value, *args, **kwargs):
        if name == "system.posix_acl_access":
            raise OSError(errno.EPERM, "injected restriction failure")
        return real_setxattr(fd, name, value, *args, **kwargs)

    monkeypatch.setattr(os, "setxattr", deny_acl)
    error = refused("metadata-unsupported", lambda: publish_add(directory), phase="cell-or-backup")
    assert "The manifest was not changed" in str(error)
    assert not stage_cells(directory) and not backups(directory)


def test_backup_link_generation_race_retries_the_next_number(exp, monkeypatch):
    (directory, _) = exp
    old = manifest_bytes(directory)
    real = dp._link
    raced = {"done": False}

    def racing(src, dst, *, src_dir_fd, dst_dir_fd):
        if not raced["done"]:
            raced["done"] = True
            raise FileExistsError(errno.EEXIST, "injected generation race")
        return real(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(dp, "_link", racing)
    result = publish_add(directory)
    assert result.status == "written"
    assert result.backup_path.name == MANIFEST_FILENAME + ".bak.2"
    assert result.backup_path.read_bytes() == old


def test_selinux_label_preservation_or_explicit_skip(exp):
    (directory, _) = exp
    manifest = directory / MANIFEST_FILENAME
    try:
        label = os.getxattr(manifest, "security.selinux")
    except OSError:
        pytest.skip("no SELinux label on this host: SELinux preservation evidence missing")
    before = os.listxattr(manifest)
    publish_add(directory)
    if "security.selinux" in before:
        assert os.getxattr(manifest, "security.selinux") == label
    assert "security.selinux" not in [
        name for name in os.listxattr(manifest) if name not in before
    ]


def test_manifest_hardlink_created_after_load_is_stale(exp, monkeypatch):
    (directory, _) = exp
    real_check = dp.check_directory_proposal

    def link_after_check(loaded, proposal, **kwargs):
        checked = real_check(loaded, proposal, **kwargs)
        os.link(directory / MANIFEST_FILENAME, directory.parent / "sneaky.json")
        return checked

    monkeypatch.setattr(dp, "check_directory_proposal", link_after_check)
    refused("stale-source", lambda: publish_add(directory), phase="before-cell")
    assert not stage_cells(directory) and not backups(directory)


def test_backup_directory_at_a_candidate_generation_is_skipped(exp):
    (directory, _) = exp
    (directory / (MANIFEST_FILENAME + ".bak")).write_bytes(b"{}")
    (directory / (MANIFEST_FILENAME + ".bak.2")).mkdir()
    result = publish_add(directory)
    assert result.backup_path.name == MANIFEST_FILENAME + ".bak.3"
    assert (directory / (MANIFEST_FILENAME + ".bak.2")).is_dir()


def test_read_only_parent_refuses_before_scanning(ext4, tmp_path):
    exp, _ = make_exp(tmp_path)
    before = manifest_bytes(exp)
    os.chmod(exp, 0o555)
    try:
        refused("permission-denied", lambda: publish_add(exp), phase="before-cell")
    finally:
        os.chmod(exp, 0o755)
    assert manifest_bytes(exp) == before
    assert not stage_cells(exp) and not backups(exp)


def test_final_guard_retains_a_competitor_manifest_and_the_backup(exp, monkeypatch):
    (directory, _) = exp
    old = manifest_bytes(directory)
    competitor = old.replace(b'"Demo"', b'"Compet"')

    def write_competitor(locked, bak_name):
        (directory / MANIFEST_FILENAME).write_bytes(competitor)

    monkeypatch.setattr(dp, "_boundary_backup_linked", write_competitor)
    refused("stale-source", lambda: publish_add(directory), phase="cell-or-backup")
    assert manifest_bytes(directory) == competitor
    assert (directory / (MANIFEST_FILENAME + ".bak")).read_bytes() == old
    assert not stage_cells(directory)


def test_publication_never_reads_the_manifest_by_path(exp, monkeypatch):
    (directory, _) = exp

    def boom(self):
        raise AssertionError("the publication path must not read the manifest by path")

    monkeypatch.setattr(Path, "read_bytes", boom)
    result = publish_add(directory)
    monkeypatch.undo()
    assert result.status == "written"
    assert_complete_manifest(directory, new=True)


def test_manifest_name_swapped_to_fifo_refuses_promptly(ext4, tmp_path):
    exp, _ = make_exp(tmp_path)
    proc = run_fifo_swap(exp, timeout=20)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert proc.stdout.split()[0] == "stale-source", proc.stdout
    assert stat.S_ISFIFO(os.lstat(exp / MANIFEST_FILENAME).st_mode)
    assert not stage_cells(exp) and not backups(exp)


def test_dry_run_refuses_a_fifo_manifest_name_promptly(ext4, tmp_path):
    exp, _ = make_exp(tmp_path)
    loaded = load_manifest(exp)
    proposal = propose_add_directory(loaded, load_raw(loaded), path="data", artifact_id="data")
    manifest = exp / MANIFEST_FILENAME
    manifest.unlink()
    os.mkfifo(manifest)
    refused(
        "stale-source",
        lambda: dp.publish_directory_proposal(None, proposal, dry_run=True, loaded=loaded),
        phase="before-cell",
    )
    assert not stage_cells(exp) and not backups(exp)


def test_cell_open_failure_cleans_the_empty_cell_and_keeps_the_phase(exp, monkeypatch):
    (directory, _) = exp
    before = manifest_bytes(directory)
    real_open = os.open
    fds_before = open_fd_count()

    def failing(path, flags, *args, **kwargs):
        if isinstance(path, str) and path.startswith(dp.CELL_PREFIX) and kwargs.get("dir_fd") is not None:
            raise OSError(errno.EMFILE, "injected cell open failure")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", failing)
    error = refused("write-failed", lambda: publish_add(directory), phase="cell-or-backup")
    assert "The manifest was not changed" in str(error)
    assert manifest_bytes(directory) == before
    assert stage_cells(directory) == [] and backups(directory) == []
    assert open_fd_count() <= fds_before


def test_cell_identity_stat_failure_cleans_the_empty_cell(exp, monkeypatch):
    (directory, _) = exp
    before = manifest_bytes(directory)
    real_stat = os.stat
    fds_before = open_fd_count()

    def failing(path, *args, dir_fd=None, **kwargs):
        if isinstance(path, str) and path.startswith(dp.CELL_PREFIX) and dir_fd is not None:
            raise OSError(errno.EIO, "injected cell stat failure")
        return real_stat(path, *args, dir_fd=dir_fd, **kwargs)

    monkeypatch.setattr(os, "stat", failing)
    error = refused("write-failed", lambda: publish_add(directory), phase="cell-or-backup")
    assert "The manifest was not changed" in str(error)
    assert manifest_bytes(directory) == before
    assert stage_cells(directory) == [] and backups(directory) == []
    assert open_fd_count() <= fds_before


def test_cell_creation_boundary_failure_closes_the_fd_and_removes_the_cell(exp, monkeypatch):
    (directory, _) = exp
    before = manifest_bytes(directory)
    fds_before = open_fd_count()

    def explode(*args, **kwargs):
        raise RuntimeError("injected cell boundary failure")

    monkeypatch.setattr(dp, "_boundary_cell_before_restrict", explode)
    error = refused("INTERNAL_ERROR", lambda: publish_add(directory), phase="cell-or-backup")
    assert "The manifest was not changed" in str(error)
    assert "Nothing was written" not in str(error)
    assert manifest_bytes(directory) == before
    assert stage_cells(directory) == [] and backups(directory) == []
    assert open_fd_count() <= fds_before


def test_cell_created_boundary_failure_cleans_up_after_create_cell(exp, monkeypatch):
    (directory, _) = exp
    before = manifest_bytes(directory)
    fds_before = open_fd_count()

    def explode(*args, **kwargs):
        raise RuntimeError("injected post-create boundary failure")

    monkeypatch.setattr(dp, "_boundary_cell_created", explode)
    error = refused("INTERNAL_ERROR", lambda: publish_add(directory), phase="cell-or-backup")
    assert "The manifest was not changed" in str(error)
    assert manifest_bytes(directory) == before
    assert stage_cells(directory) == [] and backups(directory) == []
    assert open_fd_count() <= fds_before


def test_cell_mkdir_failure_reports_the_cell_phase(exp, monkeypatch):
    (directory, _) = exp
    before = manifest_bytes(directory)

    def failing(name, mode, *, dir_fd=None):
        raise OSError(errno.EACCES, "injected mkdir failure")

    monkeypatch.setattr(os, "mkdir", failing)
    error = refused("write-failed", lambda: publish_add(directory), phase="cell-or-backup")
    assert "The manifest was not changed" in str(error)
    assert manifest_bytes(directory) == before
    assert stage_cells(directory) == [] and backups(directory) == []


# --- backup: publication, generations, failures, exactness -------------------


def test_backup_uses_newest_generation_plus_one_not_the_first_gap(exp):
    (directory, _) = exp
    manifest = directory / MANIFEST_FILENAME
    old = manifest.read_bytes()
    stale = b"{}\n"
    (directory / (MANIFEST_FILENAME + ".bak")).write_bytes(stale)
    (directory / (MANIFEST_FILENAME + ".bak.3")).write_bytes(stale)
    result = publish_add(directory)
    assert result.backup_path.name == MANIFEST_FILENAME + ".bak.4"
    assert result.backup_path.read_bytes() == old
    assert (directory / (MANIFEST_FILENAME + ".bak")).read_bytes() == stale
    assert (directory / (MANIFEST_FILENAME + ".bak.3")).read_bytes() == stale


def test_backup_skips_a_symlink_at_a_candidate_name(exp):
    (directory, _) = exp
    manifest = directory / MANIFEST_FILENAME
    old = manifest.read_bytes()
    (directory / (MANIFEST_FILENAME + ".bak")).write_bytes(b"{}")
    sentinel = directory.parent / "sentinel"
    sentinel.write_bytes(b"target")
    link = directory / (MANIFEST_FILENAME + ".bak.2")
    link.symlink_to(sentinel)
    result = publish_add(directory)
    assert result.backup_path.name == MANIFEST_FILENAME + ".bak.3"
    assert link.is_symlink() and link.read_bytes() == b"target"
    assert result.backup_path.read_bytes() == old


def test_backup_pre_link_failure_publishes_no_generation(exp, monkeypatch):
    (directory, _) = exp
    before = manifest_bytes(directory)
    real = dp._fsync

    def failing(fd, *, role):
        if role == "backup":
            raise OSError(errno.EIO, "injected backup fsync")
        return real(fd, role=role)

    monkeypatch.setattr(dp, "_fsync", failing)
    refused("write-failed", lambda: publish_add(directory), phase="cell-or-backup")
    assert manifest_bytes(directory) == before
    assert not backups(directory)
    assert not stage_cells(directory)


def test_backup_post_link_fsync_failure_preserves_a_complete_backup(exp, monkeypatch):
    (directory, _) = exp
    before = manifest_bytes(directory)
    real = dp._fsync

    def failing(fd, *, role):
        if role == "backup-parent":
            raise OSError(errno.EIO, "injected backup-parent fsync")
        return real(fd, role=role)

    monkeypatch.setattr(dp, "_fsync", failing)
    error = refused("backup-durability-failed", lambda: publish_add(directory), phase="cell-or-backup")
    assert "backup may remain" in str(error)
    assert manifest_bytes(directory) == before
    backup = directory / (MANIFEST_FILENAME + ".bak")
    assert backup.read_bytes() == before
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert not stage_cells(directory)


def test_a_failed_cell_copy_unlink_is_a_cleanup_warning_not_a_lost_backup(exp, monkeypatch):
    (directory, _) = exp
    old = manifest_bytes(directory)
    real_unlink = os.unlink

    def deny_backup_temp(name, *args, dir_fd=None, **kwargs):
        if name == dp.BACKUP_TEMP_NAME and dir_fd is not None:
            raise OSError(errno.EPERM, "injected unlink failure")
        return real_unlink(name, *args, dir_fd=dir_fd, **kwargs)

    monkeypatch.setattr(os, "unlink", deny_backup_temp)
    result = publish_add(directory)
    assert result.status == "written"
    assert result.warnings == ("cleanup-warning",)
    assert result.backup_path.read_bytes() == old
    assert complete_manifest_ok(directory)


def complete_manifest_ok(exp: Path) -> bool:
    raw = json.loads(manifest_bytes(exp).decode("utf-8"))
    Manifest.model_validate(raw)
    return True


def test_no_partial_backup_name_is_ever_published(exp, monkeypatch):
    (directory, _) = exp
    before = manifest_bytes(directory)
    states = []

    def inspect_pre_link(locked, cell_fd):
        states.append(sorted(os.listdir(directory)))
        assert not backups(directory)

    monkeypatch.setattr(dp, "_boundary_backup_written", inspect_pre_link)
    publish_add(directory)
    assert states and not any(any(name.endswith(".tmp") for name in state) for state in states)
    assert (directory / (MANIFEST_FILENAME + ".bak")).read_bytes() == before


def test_existing_backup_with_a_permissive_mode_is_never_reused(exp):
    (directory, _) = exp
    manifest = directory / MANIFEST_FILENAME
    old = manifest.read_bytes()
    existing = directory / (MANIFEST_FILENAME + ".bak")
    existing.write_bytes(b"old")
    os.chmod(existing, 0o644)
    result = publish_add(directory)
    assert result.backup_path.name == MANIFEST_FILENAME + ".bak.2"
    assert result.backup_path.read_bytes() == old
    assert stat.S_IMODE(result.backup_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(existing.stat().st_mode) == 0o644


# --- crash injection: real subprocess termination ---------------------------


@pytest.mark.parametrize(
    "boundary,expect_cell,expect_backup",
    [
        ("_boundary_cell_created", True, False),
        ("_boundary_staged_created", True, False),
        ("_boundary_staged_written", True, False),
        ("_boundary_cell_restricted", True, False),
        ("_boundary_backup_written", True, False),
        ("_boundary_backup_linked", True, True),
        ("_boundary_after_replace", True, True),
        ("_boundary_after_parent_fsync", True, True),
    ],
)
def test_subprocess_crash_at_each_boundary_leaves_old_or_new_complete(
    ext4, tmp_path, boundary, expect_cell, expect_backup
):
    exp, _ = make_exp(tmp_path)
    old = manifest_bytes(exp)
    proc = run_crash(exp, boundary)
    assert proc.returncode == 97, (proc.returncode, proc.stdout, proc.stderr)

    current = manifest_bytes(exp)
    if boundary in ("_boundary_after_replace", "_boundary_after_parent_fsync"):
        assert current != old
        assert_complete_manifest(exp, new=True)
    else:
        assert current == old
        assert_complete_manifest(exp, old=old)
    if expect_backup:
        backup = exp / (MANIFEST_FILENAME + ".bak")
        assert backup.read_bytes() == old
        assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    else:
        assert not backups(exp)
    assert bool(stage_cells(exp)) is expect_cell
    # The kernel released the lock: the next writer proceeds immediately.
    with dp.manifest_admission_lock(exp):
        pass


def test_process_crash_is_not_described_as_power_loss(ext4, tmp_path):
    """A process crash keeps the page cache; the old source stays readable.
    This is not a power-loss proof and does not claim old-or-new recovery
    without the source-durability precondition."""
    exp, _ = make_exp(tmp_path)
    old = manifest_bytes(exp)
    proc = run_crash(exp, "_boundary_staged_written")
    assert proc.returncode == 97
    assert manifest_bytes(exp) == old
    assert not backups(exp)


def test_crash_inside_backup_cell_write_leaves_no_backup_name(ext4, tmp_path):
    exp, _ = make_exp(tmp_path)
    old = manifest_bytes(exp)
    proc = run_crash(exp, "fsync:backup")
    assert proc.returncode == 97
    assert manifest_bytes(exp) == old
    assert not any(name.startswith(MANIFEST_FILENAME + ".bak") for name in names(exp))
    assert stage_cells(exp)


def test_crash_after_backup_before_replace_keeps_a_complete_backup(ext4, tmp_path):
    exp, _ = make_exp(tmp_path)
    old = manifest_bytes(exp)
    proc = run_crash(exp, "_boundary_backup_linked")
    assert proc.returncode == 97
    assert manifest_bytes(exp) == old
    assert (exp / (MANIFEST_FILENAME + ".bak")).read_bytes() == old
    cell = exp / stage_cells(exp)[0]
    assert (cell / dp.STAGED_NAME).exists()


# --- three-phase INTERNAL_ERROR boundary ------------------------------------


def test_unexpected_before_cell_is_phase_one(exp, monkeypatch):
    (directory, _) = exp
    before = manifest_bytes(directory)

    def explode(fd):
        raise RuntimeError(" ".join(CANARIES))

    monkeypatch.setattr(dp, "_capture_metadata", explode)
    error = refused("INTERNAL_ERROR", lambda: publish_add(directory), phase="before-cell")
    assert "Nothing was written" in str(error)
    for canary in CANARIES:
        assert canary not in str(error)
    assert manifest_bytes(directory) == before
    assert not stage_cells(directory) and not backups(directory)


def test_unexpected_at_step_11_final_guard_is_phase_two(exp, monkeypatch):
    (directory, _) = exp
    before = manifest_bytes(directory)

    def explode(*args, **kwargs):
        raise RuntimeError(" ".join(CANARIES))

    monkeypatch.setattr(dp, "_boundary_final_guard", explode)
    error = refused("INTERNAL_ERROR", lambda: publish_add(directory), phase="cell-or-backup")
    assert "The manifest was not changed; a backup or staging folder may remain" in str(error)
    assert "Nothing was written" not in str(error)
    for canary in CANARIES:
        assert canary not in str(error)
    assert manifest_bytes(directory) == before
    assert (directory / (MANIFEST_FILENAME + ".bak")).read_bytes() == before


def test_unexpected_just_before_replace_is_phase_three(exp, monkeypatch):
    (directory, _) = exp
    before = manifest_bytes(directory)

    def explode(*args, **kwargs):
        raise RuntimeError(" ".join(CANARIES))

    monkeypatch.setattr(dp, "_replace_manifest", explode)
    error = refused("INTERNAL_ERROR", lambda: publish_add(directory), phase="at-replace")
    assert "may have been replaced" in str(error)
    for canary in CANARIES:
        assert canary not in str(error)
    assert manifest_bytes(directory) == before
    assert (directory / (MANIFEST_FILENAME + ".bak")).read_bytes() == before


def test_expected_refusals_are_fixed_and_free_of_internal_detail(exp):
    from runevidence.directory_apply import DirectoryApplyError

    mapped = dp._map_apply(DirectoryApplyError("stale-tree", "canary member-canary.txt at /abs/root-canary"))
    assert mapped.reason == "stale-tree" and mapped.phase == "before-cell"
    assert "Traceback" not in str(mapped)
    assert "/abs/root-canary" not in str(mapped)
    assert "member-canary" not in str(mapped)


# --- backup/manifest race negative controls ----------------------------------


def test_nonparticipating_writer_lost_is_named_as_lost(exp, monkeypatch):
    """The final-window lost update is a documented limitation: competitor B is
    lost and the command still succeeds with its own bytes. The test is named
    ``lost`` rather than ``safe`` on purpose."""
    (directory, _) = exp
    old = manifest_bytes(directory)
    competitor = old.replace(b'"Demo"', b'"Compet"')

    def write_competitor(locked, backup_path):
        (directory / MANIFEST_FILENAME).write_bytes(competitor)

    # This writer runs after the final guard; the protocol's last observation
    # was before it, so its bytes are lost to os.replace.
    monkeypatch.setattr(dp, "_boundary_before_replace", write_competitor)
    result = publish_add(directory)
    assert result.status == "written"
    assert manifest_bytes(directory) != competitor
    assert_complete_manifest(directory, new=True)


def test_tree_change_after_final_scan_is_not_detected(exp):
    """No third scan is claimed: a change after the checked rebuild is recorded
    stale by design, not "fixed" here."""
    (directory, _) = exp
    publish_add(directory)
    recorded = manifest_json(directory)["artifacts"][-1]["sha256"]
    (directory / "data" / "a.txt").write_bytes(b"changed afterwards")
    assert scan_directory(directory / "data").digest.sha256 != recorded
    # No S4-1 API revalidates the tree; validate --directory-artifacts owns it.
    assert manifest_json(directory)["artifacts"][-1]["sha256"] == recorded


# --- mount-info classification ----------------------------------------------


def test_manifest_folder_is_classified_by_mountinfo_mount_id(ext4, tmp_path):
    exp, _ = make_exp(tmp_path)
    source = (Path(runevidence.__file__).parent / "directory_publication.py").read_text(encoding="utf-8")
    assert "statvfs" not in source and "statfs" not in source
    fd = os.open(exp, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        mount_id = dp._proven_mount_id(fd)
        assert mount_id > 0
        assert dp._mount_table()[mount_id].fstype == "ext4"
    finally:
        os.close(fd)
    assert publish_add(exp).status == "written"


def test_a_non_release_qualified_manifest_folder_is_refused(exp, monkeypatch):
    (directory, _) = exp
    fake = {1: ds._Mount(1, 0, "/", "tmpfs")}
    monkeypatch.setattr(dp, "_mount_table", lambda: fake)
    monkeypatch.setattr(dp, "_proven_mount_id", lambda fd: 1)
    refused("unsupported-filesystem", lambda: dp.manifest_admission_lock(directory).__enter__())
    assert not stage_cells(directory) and not backups(directory)


def test_a_real_tmpfs_manifest_folder_is_refused_or_skipped(ext4, tmp_path):
    if os.geteuid() != 0:
        pytest.skip("no mount privilege: real non-ext4 mount evidence missing")
    mountpoint = tmp_path / "mnt"
    mountpoint.mkdir()
    mounted = subprocess.run(
        ["mount", "-t", "tmpfs", "tmpfs", str(mountpoint)], capture_output=True, text=True
    )
    if mounted.returncode != 0:
        pytest.skip("no mount privilege: real non-ext4 mount evidence missing")
    try:
        (mountpoint / MANIFEST_FILENAME).write_bytes(dump_raw(base_raw()).encode())
        refused("unsupported-filesystem", lambda: dp.manifest_admission_lock(mountpoint).__enter__())
    finally:
        subprocess.run(["umount", str(mountpoint)], capture_output=True, text=True)


# --- no explicit tree mutations ---------------------------------------------


def test_successful_write_does_not_mutate_the_scanned_tree(exp):
    (directory, _) = exp
    (directory / "data" / "sub").mkdir()
    (directory / "data" / "sub" / "b.bin").write_bytes(b"\x00\x01")
    before = tree_snapshot(directory / "data")
    publish_add(directory)
    after = tree_snapshot(directory / "data")
    assert after == before


# --- limits and shape refusals ----------------------------------------------


def test_root_depth_limit_is_refused_before_any_cell_or_scan(exp, monkeypatch):
    (directory, _) = exp
    calls = []
    real = ds.scan_directory
    monkeypatch.setattr(ds, "scan_directory", lambda *a, **k: calls.append(a) or real(*a, **k))
    deep = "/".join(["d"] * 65)
    with pytest.raises(DirectoryAuthoringError) as info:
        publish_add(directory, path=deep)
    assert info.value.reason == "limit-exceeded"
    assert calls == []
    assert not stage_cells(directory) and not backups(directory)


def test_member_limit_overflow_refuses_as_scan_failure(ext4, tmp_path):
    exp, _ = make_exp(tmp_path, files={})
    data = exp / "data"
    for index in range(10_001):
        (data / f"d{index:05d}").mkdir()
    with pytest.raises(DirectoryAuthoringError) as info:
        publish_add(exp)
    assert info.value.reason == "scan-failed"
    assert info.value.scan_reason == "limit-exceeded"
    assert not stage_cells(exp) and not backups(exp)


def test_proposal_shape_refusals_stay_internal(exp):
    (directory, _) = exp
    with pytest.raises(DirectoryAuthoringError) as info:
        publish_add(directory, artifact_id="notes")
    assert info.value.reason == "duplicate-id"
    assert not stage_cells(directory) and not backups(directory)


def test_rehash_of_a_file_artifact_is_not_a_directory_artifact(ext4, tmp_path):
    exp, _ = make_exp(tmp_path)
    loaded = load_manifest(exp)
    proposal = None
    with pytest.raises(DirectoryAuthoringError) as info:
        propose_rehash_directory(loaded, load_raw(loaded), artifact_id="notes")
    assert info.value.reason == "not-a-directory-artifact"
    assert not stage_cells(exp) and not backups(exp)


# --- internal-only guard -----------------------------------------------------


def test_importing_the_cli_does_not_import_publication(ext4, tmp_path):
    exp, _ = make_exp(tmp_path)
    code = textwrap.dedent(f"""\
        import sys
        sys.path.insert(0, {str(SRC)!r})
        import runevidence.cli, runevidence.checks, runevidence.manifest
        from runevidence.manifest import load_manifest
        from runevidence.checks import run_checks
        run_checks(load_manifest({str(exp)!r}))
        print("runevidence.directory_publication" in sys.modules)
    """)
    proc = subprocess.run(
        [sys.executable, "-B", "-c", code],
        capture_output=True, text=True, timeout=TIMEOUT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "False", proc.stdout


def test_importing_publication_does_not_import_the_cli(ext4):
    code = textwrap.dedent(f"""\
        import sys
        sys.path.insert(0, {str(SRC)!r})
        import runevidence.directory_publication
        print("runevidence.cli" in sys.modules)
    """)
    proc = subprocess.run(
        [sys.executable, "-B", "-c", code],
        capture_output=True, text=True, timeout=TIMEOUT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "False", proc.stdout


def test_the_module_is_not_exported_from_the_package():
    code = textwrap.dedent(f"""\
        import sys
        sys.path.insert(0, {str(SRC)!r})
        import runevidence
        print("runevidence.directory_publication" in sys.modules,
              hasattr(runevidence, "publish_directory_proposal"),
              "directory_publication" in getattr(runevidence, "__all__", ()))
    """)
    proc = subprocess.run(
        [sys.executable, "-B", "-c", code],
        capture_output=True, text=True, timeout=TIMEOUT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["False", "False", "False"], proc.stdout


def test_no_public_cli_flag_or_route_was_added(ext4, tmp_path):
    from runevidence import cli

    exp, _ = make_exp(tmp_path)
    (exp / "notes.txt").write_bytes(b"note")
    parser = cli.build_parser()
    help_text = parser.format_help()
    assert "--directory" not in help_text
    assert cli.main(["validate", str(exp)]) == 0


def test_load_under_lock_refuses_a_fifo_swap_without_blocking(exp):
    """A FIFO swapped in before the held load must not wait under the lock."""
    root, _raw = exp
    script = textwrap.dedent(
        """
        import os, sys
        sys.path.insert(0, sys.argv[1])
        from runevidence import directory_publication as dp
        with dp.manifest_admission_lock(sys.argv[2]) as locked:
            os.unlink(locked.target)
            os.mkfifo(locked.target)
            try:
                dp.load_manifest_under_lock(locked)
            except dp.DirectoryPublicationError as exc:
                sys.stdout.write("REFUSED %s\\n" % exc.reason)
        """
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(SRC), str(root)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert result.stdout.strip() == "REFUSED stale-source"
    assert result.stderr == ""
    # Nothing was written and the folder lock was released with the process.
    assert not (root / (MANIFEST_FILENAME + ".bak")).exists()
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)
