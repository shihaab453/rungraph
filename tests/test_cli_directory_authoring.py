"""S4-2b evidence: public add/rehash ``--directory`` (Linux/ext4, text only).

These tests exercise the public composition of the accepted S4-1 core and the
S4-2a admission lock. Real-write tests require an ext4 temp folder and the
inode-flag ioctl; a missing facility is recorded as skipped evidence, never a
pass. Shape/refusal tests do not scan or write.

No CAS, tree-freeze, ACL/second-user, batch, Windows/macOS or directory-R5
coverage is claimed here.
"""

from __future__ import annotations

import pytest

# This module imports ``fcntl`` directly and ``directory_publication``, which
# imports it at module level. Windows cannot import it, so collection skips
# instead of erroring; the CLI's lazy import of that module already fails
# closed off POSIX.
pytest.importorskip("fcntl")

import contextlib
import errno
import fcntl
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

from runevidence import cli
from runevidence import directory_publication as dp
from runevidence import directory_scanner as ds
from runevidence.manifest import dump_raw
from runevidence.models import MANIFEST_FILENAME

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux-only evidence")

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
MANIFEST = MANIFEST_FILENAME
BUSY_TEXT = (
    "ERROR (BUSY): another RunEvidence writer is updating the manifest. "
    "Nothing was written."
)
LOCK_UNAVAILABLE_TEXT = (
    "ERROR (LOCK_UNAVAILABLE): the manifest folder could not be locked. "
    "Nothing was written."
)
INTERNAL_BEFORE = (
    "ERROR (INTERNAL_ERROR): the directory update failed internally. "
    "Nothing was written."
)
INTERNAL_CELL = (
    "ERROR (INTERNAL_ERROR): the directory update failed internally. "
    "The manifest was not changed; a backup or staging folder may remain."
)
INTERNAL_REPLACE = (
    "ERROR (INTERNAL_ERROR): the directory update failed internally after the "
    "manifest may have been replaced. Run runevidence validate PATH "
    "--directory-artifacts."
)


def _host_fstype(path: Path) -> str:
    try:
        parts = ds._root_components(str(path))
        table = dp._mount_table()
        _, mount = ds._preflight_mounts(parts, dp._release_policy(), table)
        return mount.fstype
    except Exception as exc:  # noqa: BLE001 - a probe gap is a skip
        return f"<{type(exc).__name__}>"


def _ioctl_available(path: Path) -> bool:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.ioctl(fd, dp._FS_IOC_GETFLAGS, b"\0" * 8)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)


@pytest.fixture
def ext4(tmp_path: Path) -> Path:
    fstype = _host_fstype(tmp_path)
    if fstype != "ext4":
        pytest.skip(f"host temp is {fstype!r}, not ext4: real-write evidence missing")
    probe = tmp_path / ".flags-probe"
    probe.write_bytes(b"probe")
    try:
        if not _ioctl_available(probe):
            pytest.skip("FS_IOC_GETFLAGS unavailable: metadata preflight evidence missing")
    finally:
        probe.unlink()
    return tmp_path


def make_exp(
    tmp_path: Path,
    *,
    with_run: bool = False,
    file_artifact: bool = False,
    directory_artifact: bool = False,
    volatile_directory: bool = False,
) -> Path:
    root = tmp_path / "exp"
    root.mkdir()
    (root / "data" / "sub").mkdir(parents=True)
    (root / "data" / "a.txt").write_bytes(b"hello\n")
    (root / "data" / "sub" / "b.bin").write_bytes(b"1234")
    if file_artifact:
        (root / "notes.txt").write_bytes(b"notes\n")
    doc = {
        "project": {"id": "demo", "name": "Demo"},
        "schema_version": "0.1",
        "runs": [{"id": "run-1", "name": "Run"}] if with_run else [],
        "artifacts": [],
        "results": [],
        "evidence": [],
    }
    if file_artifact:
        doc["artifacts"].append(
            {
                "id": "notes",
                "path": "notes.txt",
                "sha256": hashlib.sha256(b"notes\n").hexdigest(),
                "size_bytes": 6,
            }
        )
    if directory_artifact:
        doc["artifacts"].append(
            {
                "id": "data",
                "path": "data",
                "artifact_type": "directory",
                "digest_format": "runevidence-directory-v1",
                "sha256": "0" * 64,
                "size_bytes": 1,
                "volatile": True,
            }
        )
        if not volatile_directory:
            doc["artifacts"][-1]["volatile"] = False
    (root / MANIFEST).write_bytes(dump_raw(doc).encode("utf-8"))
    return root


def run_cli(argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


def raw_manifest(root: Path) -> dict:
    return json.loads((root / MANIFEST).read_text(encoding="utf-8"))


def names(root: Path) -> list[str]:
    return sorted(path.name for path in root.iterdir())


def cells(root: Path) -> list[str]:
    return [name for name in names(root) if name.startswith(dp.CELL_PREFIX)]


def temps(root: Path) -> list[str]:
    return [name for name in names(root) if name.endswith(".tmp")]


def backups(root: Path) -> list[str]:
    return [name for name in names(root) if name == MANIFEST + ".bak" or name.startswith(MANIFEST + ".bak.")]


def add_dir(root: Path, directory: str, *extra: str):
    return run_cli(
        ["add", str(root), directory, "--directory", "--id", "data", *extra]
    )


# ---------------------------------------------------------------------------
# Real add / rehash on ext4
# ---------------------------------------------------------------------------


def test_add_directory_end_to_end(ext4, monkeypatch):
    root = make_exp(ext4)
    original = (root / MANIFEST).read_bytes()
    monkeypatch.chdir(root)

    code, out, err = add_dir(root, "data")

    assert (code, err) == (0, "")
    assert "  data: data  sha256 " in out
    assert "3 members" in out
    assert "Added 1 directory artifact" in out
    assert f"Wrote {root / MANIFEST}" in out
    assert f"Saved the previous manifest to {root / (MANIFEST + '.bak')}" in out
    assert f"Next: runevidence validate {root} --directory-artifacts" in out
    assert cells(root) == [] and temps(root) == []
    assert (root / (MANIFEST + ".bak")).read_bytes() == original

    raw = raw_manifest(root)
    entry = raw["artifacts"][0]
    assert (entry["id"], entry["path"]) == ("data", "data")
    assert entry["artifact_type"] == "directory"
    assert entry["digest_format"] == "runevidence-directory-v1"
    assert len(entry["sha256"]) == 64 and entry["size_bytes"] == 10
    assert "members" not in entry

    code, out, err = run_cli(["validate", str(root), "--directory-artifacts"])
    assert (code, err) == (0, "")
    assert "OK: 0 errors, 0 warnings" in out


def test_add_directory_kind_and_run(ext4, monkeypatch):
    root = make_exp(ext4, with_run=True)
    monkeypatch.chdir(root)

    code, out, err = add_dir(root, "data", "--kind", "dataset", "--run", "run-1")

    assert (code, err) == (0, "")
    entry = raw_manifest(root)["artifacts"][0]
    assert (entry["kind"], entry["run_id"]) == ("dataset", "run-1")


def test_add_directory_defaults_to_no_run(ext4, monkeypatch):
    root = make_exp(ext4, with_run=True)
    monkeypatch.chdir(root)

    assert add_dir(root, "data")[0] == 0
    entry = raw_manifest(root)["artifacts"][0]
    assert entry["run_id"] is None


def test_add_directory_dry_run_writes_nothing(ext4, monkeypatch):
    root = make_exp(ext4)
    before = (root / MANIFEST).read_bytes()
    monkeypatch.chdir(root)

    code, out, err = add_dir(root, "data", "--dry-run")

    assert (code, err) == (0, "")
    assert "would record sha256" in out
    assert "3 members" in out
    assert "Dry run: nothing was written." in out
    assert (root / MANIFEST).read_bytes() == before
    assert backups(root) == [] and cells(root) == [] and temps(root) == []


def test_rehash_directory_changed_and_unchanged(ext4, monkeypatch):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    assert add_dir(root, "data")[0] == 0
    first = raw_manifest(root)["artifacts"][0]["sha256"]
    backup_count = len(backups(root))

    (root / "data" / "a.txt").write_bytes(b"changed\n")

    code, out, err = run_cli(["rehash", str(root), "data", "--directory"])
    assert (code, err) == (0, "")
    assert "sha256 " in out and " -> " in out
    assert "Re-recorded 1 directory artifact" in out
    second = raw_manifest(root)["artifacts"][0]["sha256"]
    assert second != first
    assert len(backups(root)) == backup_count + 1
    assert run_cli(["validate", str(root), "--directory-artifacts"])[0] == 0

    code, out, err = run_cli(["rehash", str(root), "data", "--directory"])
    assert (code, err) == (0, "")
    assert "unchanged (sha256" in out
    assert "Wrote " not in out
    assert len(backups(root)) == backup_count + 1
    assert cells(root) == [] and temps(root) == []


def test_rehash_directory_id_prefix(ext4, monkeypatch):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    assert add_dir(root, "data")[0] == 0
    (root / "data" / "a.txt").write_bytes(b"changed\n")

    code, out, err = run_cli(["rehash", str(root), "id:data", "--directory"])
    assert (code, err) == (0, ""), out


def test_rehash_directory_dry_run_changed_and_unchanged(ext4, monkeypatch):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    assert add_dir(root, "data")[0] == 0
    before = (root / MANIFEST).read_bytes()
    backups_before = backups(root)

    (root / "data" / "a.txt").write_bytes(b"changed\n")
    code, out, err = run_cli(["rehash", str(root), "data", "--directory", "--dry-run"])
    assert (code, err) == (0, "")
    assert "would change sha256" in out
    assert "Dry run: nothing was written." in out
    assert (root / MANIFEST).read_bytes() == before
    assert backups(root) == backups_before

    (root / "data" / "a.txt").write_bytes(b"hello\n")
    code, out, err = run_cli(["rehash", str(root), "data", "--directory", "--dry-run"])
    assert (code, err) == (0, "")
    assert "unchanged (sha256" in out


def test_rehash_directory_not_a_directory_and_not_found(ext4, monkeypatch):
    root = make_exp(ext4, file_artifact=True)
    monkeypatch.chdir(root)
    before = (root / MANIFEST).read_bytes()

    code, out, err = run_cli(["rehash", str(root), "notes", "--directory"])
    assert (code, err) == (2, "")
    assert "NOT_A_DIRECTORY_ARTIFACT" in out
    assert "Nothing was written" in out
    assert (root / MANIFEST).read_bytes() == before
    assert backups(root) == []

    code, out, err = run_cli(["rehash", str(root), "missing", "--directory"])
    assert (code, err) == (2, "")
    assert "NOT_FOUND" in out
    assert (root / MANIFEST).read_bytes() == before


def test_add_directory_duplicate_id_is_refused_without_a_backup(ext4, monkeypatch):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    assert add_dir(root, "data")[0] == 0
    backups_before = backups(root)
    before = (root / MANIFEST).read_bytes()

    code, out, err = add_dir(root, "data")

    assert (code, err) == (2, "")
    assert "DUPLICATE_ID" in out
    assert (root / MANIFEST).read_bytes() == before
    assert backups(root) == backups_before
    assert cells(root) == []


def test_rehash_directory_volatile_refused(ext4, monkeypatch):
    root = make_exp(ext4, directory_artifact=True, volatile_directory=True)
    monkeypatch.chdir(root)
    before = (root / MANIFEST).read_bytes()

    code, out, err = run_cli(["rehash", str(root), "data", "--directory"])

    assert (code, err) == (2, "")
    assert "VOLATILE_UNSUPPORTED" in out
    assert (root / MANIFEST).read_bytes() == before
    assert backups(root) == [] and cells(root) == []


# ---------------------------------------------------------------------------
# Grammar and DIR base (no lock or scan)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["add", "PATH", "data", "--directory"],
        ["add", "PATH", "data", "data", "--directory", "--id", "data"],
        ["add", "PATH", "data", "--directory", "--id", "one", "--id", "two"],
        ["add", "PATH", "data", "--directory", "--id", "data", "--volatile"],
        ["add", "PATH", "data", "--directory", "--id", "data", "--run", "r", "--no-run"],
    ],
)
def test_add_directory_shape_errors_do_not_lock_or_scan(
    tmp_path, monkeypatch, argv
):
    root = make_exp(tmp_path)
    before = (root / MANIFEST).read_bytes()
    argv = [str(root) if value == "PATH" else value for value in argv]

    def no_lock(*args, **kwargs):
        raise AssertionError("a shape error attempted admission")

    def no_scan(*args, **kwargs):
        raise AssertionError("a shape error attempted a scan")

    monkeypatch.setattr(dp, "manifest_admission_lock", no_lock)
    monkeypatch.setattr(ds, "scan_directory", no_scan)

    code, out, err = run_cli(argv)
    assert (code, err) == (2, "")
    assert out.startswith("ERROR")
    assert (root / MANIFEST).read_bytes() == before
    assert backups(root) == []


def test_add_directory_shape_error_wins_over_a_held_lock(ext4, monkeypatch):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    with dp.manifest_admission_lock(root):
        code, out, err = run_cli(["add", str(root), "data", "--directory"])
    assert (code, err) == (2, "")
    assert "exactly one folder" in out
    assert "BUSY" not in out


@pytest.mark.parametrize(
    "flag",
    ["--json", "--force", "--sha256", "--size-bytes", "--metadata", "--backend"],
)
def test_add_directory_refuses_unsupported_flags(ext4, monkeypatch, flag):
    root = make_exp(ext4)
    before = (root / MANIFEST).read_bytes()
    monkeypatch.chdir(root)

    code, out, err = add_dir(root, "data", flag, "value")

    assert code == 2
    assert (root / MANIFEST).read_bytes() == before
    assert backups(root) == []


def test_rehash_directory_shape_errors(ext4, monkeypatch):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    assert add_dir(root, "data")[0] == 0
    before = (root / MANIFEST).read_bytes()

    for argv in (
        ["rehash", str(root), "--all", "--directory"],
        ["rehash", str(root), "--directory"],
        ["rehash", str(root), "data", "other", "--directory"],
        ["rehash", str(root), "path:data", "--directory"],
        ["rehash", str(root), "run:run-1", "--directory"],
        ["rehash", str(root), "data*", "--directory"],
        ["rehash", str(root), "data", "--directory", "--volatile"],
        ["rehash", str(root), "data", "--directory", "--no-volatile"],
    ):
        code, out, err = run_cli(argv)
        assert (code, err) == (2, ""), argv
        assert out.startswith("ERROR"), argv
        assert (root / MANIFEST).read_bytes() == before
    assert backups(root) == [MANIFEST + ".bak"]


@pytest.mark.parametrize(
    "directory",
    ["../outside", "OUTSIDE_ABS", "ALIAS"],
)
def test_add_directory_outside_root_is_refused(ext4, monkeypatch, directory):
    root = make_exp(ext4)
    outside = ext4 / "outside"
    outside.mkdir()
    alias = ext4 / "alias"
    alias.symlink_to(root, target_is_directory=True)
    if directory == "OUTSIDE_ABS":
        directory = str(outside)
    elif directory == "ALIAS":
        directory = str(alias / "data")
    monkeypatch.chdir(root)
    before = (root / MANIFEST).read_bytes()

    code, out, err = add_dir(root, directory)

    assert (code, err) == (2, "")
    assert "OUTSIDE_ROOT" in out
    assert (root / MANIFEST).read_bytes() == before
    assert backups(root) == []


def test_add_directory_cwd_pins_the_relative_base(ext4, monkeypatch):
    root = make_exp(ext4)

    monkeypatch.chdir(root)
    code, out, err = add_dir(root, "data", "--dry-run")
    assert (code, err) == (0, ""), out

    monkeypatch.chdir(ext4)
    code, out, err = add_dir(root, "data", "--dry-run")
    assert (code, err) == (2, "")
    assert "OUTSIDE_ROOT" in out


def test_add_directory_symlink_dir_is_refused(ext4, monkeypatch):
    root = make_exp(ext4)
    (root / "linked-data").symlink_to(root / "data", target_is_directory=True)
    monkeypatch.chdir(root)
    before = (root / MANIFEST).read_bytes()

    code, out, err = add_dir(root, "linked-data")

    assert (code, err) == (2, "")
    assert "RECHECK_FAILED" in out
    assert (root / MANIFEST).read_bytes() == before
    assert backups(root) == []


def test_add_directory_manifest_root_is_refused(ext4, monkeypatch):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    code, out, err = add_dir(root, ".")
    assert (code, err) == (2, "")
    assert "INVALID_PATH" in out


# ---------------------------------------------------------------------------
# Platform / filesystem
# ---------------------------------------------------------------------------


def test_tmpfs_is_refused_before_scanning(tmp_path, monkeypatch):
    shm = Path("/dev/shm")
    try:
        fstype = subprocess.run(
            ["stat", "-f", "-c", "%T", str(shm)],
            capture_output=True, text=True, check=False,
        ).stdout.strip()
    except OSError:
        fstype = ""
    if not shm.is_dir() or fstype != "tmpfs":
        pytest.skip("no tmpfs at /dev/shm: tmpfs refusal evidence missing")
    base = Path(tempfile.mkdtemp(dir=shm))
    try:
        root = make_exp(base)
        monkeypatch.chdir(root)
        before = (root / MANIFEST).read_bytes()
        for extra in ((), ("--dry-run",)):
            code, out, err = add_dir(root, str(root / "data"), *extra)
            assert (code, err) == (2, "")
            assert "UNSUPPORTED_FILESYSTEM" in out
            assert (root / MANIFEST).read_bytes() == before
        # The filesystem capability refusal precedes the manifest readability
        # pre-check, so an unreadable manifest on tmpfs is still refused as an
        # unsupported filesystem.
        os.chmod(root / MANIFEST, 0o000)
        try:
            code, out, err = add_dir(root, str(root / "data"))
        finally:
            os.chmod(root / MANIFEST, 0o644)
        assert (code, err) == (2, "")
        assert "UNSUPPORTED_FILESYSTEM" in out
        assert (root / MANIFEST).read_bytes() == before
    finally:
        import shutil

        shutil.rmtree(base, ignore_errors=True)


def test_non_linux_is_refused(ext4, monkeypatch):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    before = (root / MANIFEST).read_bytes()
    monkeypatch.setattr(sys, "platform", "win32")

    for extra in ((), ("--dry-run",)):
        code, out, err = add_dir(root, str(root / "data"), *extra)
        assert (code, err) == (2, "")
        assert "UNSUPPORTED_PLATFORM" in out
        assert (root / MANIFEST).read_bytes() == before


# ---------------------------------------------------------------------------
# Admission lock and concurrency
# ---------------------------------------------------------------------------


HOLDER = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {src!r})
    from runevidence.directory_publication import manifest_admission_lock
    with manifest_admission_lock(sys.argv[1]):
        sys.stdout.write("locked\\n")
        sys.stdout.flush()
        sys.stdin.read()
    """
)


def start_holder(exp: Path) -> subprocess.Popen:
    code = HOLDER.format(src=str(SRC))
    proc = subprocess.Popen(
        [sys.executable, "-B", "-c", code, str(exp)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    line = proc.stdout.readline().strip()
    if line != "locked":
        proc.kill()
        _, err = proc.communicate(timeout=30)
        raise AssertionError(f"lock holder did not start: {line!r} {err!r}")
    return proc


def stop_holder(proc: subprocess.Popen) -> None:
    try:
        proc.stdin.close()
    except OSError:
        pass
    proc.wait(timeout=30)


def test_busy_from_another_process_is_fixed_and_writes_nothing(ext4, monkeypatch):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    before = (root / MANIFEST).read_bytes()
    holder = start_holder(root)
    try:
        code, out, err = add_dir(root, "data")
    finally:
        stop_holder(holder)

    assert (code, err) == (2, "")
    assert out == BUSY_TEXT + "\n"
    assert (root / MANIFEST).read_bytes() == before
    assert backups(root) == []
    assert add_dir(root, "data")[0] == 0
    with dp.manifest_admission_lock(root):
        pass


def test_directory_and_file_writers_share_one_admission_lock(ext4, monkeypatch):
    root = make_exp(ext4, file_artifact=True)
    monkeypatch.chdir(root)
    before = (root / MANIFEST).read_bytes()

    # A held S4-2a writer lock makes the directory command BUSY.
    with dp.builtin_writer_admission_lock(root):
        code, out, err = add_dir(root, "data")
        assert (code, err) == (2, "")
        assert out == BUSY_TEXT + "\n"

    # A held directory admission lock makes the plain file writer BUSY.
    with dp.manifest_admission_lock(root):
        code, out, err = run_cli(["add", str(root), "notes.txt", "--id", "notes"])
        assert (code, err) == (2, "")
        assert out == BUSY_TEXT + "\n"

    assert (root / MANIFEST).read_bytes() == before
    assert add_dir(root, "data")[0] == 0


def test_busy_refuses_and_dry_run_ignores_the_lock(ext4, monkeypatch):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    before = (root / MANIFEST).read_bytes()

    with dp.manifest_admission_lock(root):
        code, out, err = add_dir(root, "data")
        assert (code, err) == (2, "")
        assert out == BUSY_TEXT + "\n"
        assert (root / MANIFEST).read_bytes() == before
        assert backups(root) == []

        code, out, err = add_dir(root, "data", "--dry-run")
        assert (code, err) == (0, "")
        assert "Dry run: nothing was written." in out
        assert (root / MANIFEST).read_bytes() == before

    assert add_dir(root, "data")[0] == 0


def test_rehash_dry_run_succeeds_while_a_writer_holds_the_lock(ext4, monkeypatch):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    assert add_dir(root, "data")[0] == 0
    before = (root / MANIFEST).read_bytes()

    (root / "data" / "a.txt").write_bytes(b"changed\n")
    with dp.manifest_admission_lock(root):
        code, out, err = run_cli(
            ["rehash", str(root), "data", "--directory", "--dry-run"]
        )

    assert (code, err) == (0, "")
    assert "would change sha256" in out
    assert (root / MANIFEST).read_bytes() == before
    assert cells(root) == [] and temps(root) == []


def test_dry_run_takes_no_lock_and_no_fsync(ext4, monkeypatch):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    before = (root / MANIFEST).read_bytes()

    def no_fsync(fd):
        raise AssertionError("a dry run called fsync")

    monkeypatch.setattr(os, "fsync", no_fsync)
    with dp.manifest_admission_lock(root):
        code, out, err = add_dir(root, "data", "--dry-run")

    assert (code, err) == (0, "")
    assert "would record sha256" in out
    assert (root / MANIFEST).read_bytes() == before
    assert backups(root) == [] and cells(root) == [] and temps(root) == []


def test_dry_run_does_not_require_a_writable_manifest(ext4, monkeypatch):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    manifest = root / MANIFEST
    original_mode = manifest.stat().st_mode & 0o777
    os.chmod(manifest, 0o444)
    try:
        code, out, err = add_dir(root, "data", "--dry-run")
    finally:
        os.chmod(manifest, original_mode)
    assert (code, err) == (0, ""), out
    assert "would record sha256" in out


def test_two_process_directory_add_never_duplicates_or_loses(ext4, monkeypatch):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    argv = [
        sys.executable, "-m", "runevidence", "add", str(root),
        str(root / "data"), "--directory", "--id", "data",
    ]
    procs = [
        subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env, cwd=str(root),
        )
        for _ in range(2)
    ]
    results = [proc.communicate(timeout=180) for proc in procs]
    codes = [proc.returncode for proc in procs]

    assert codes.count(0) == 1, (codes, results)
    loser = results[codes.index(2)][0]
    assert "BUSY" in loser or "DUPLICATE_ID" in loser or "PATH_COLLISION" in loser
    raw = raw_manifest(root)
    assert [entry["id"] for entry in raw["artifacts"]] == ["data"]
    assert run_cli(["validate", str(root), "--directory-artifacts"])[0] == 0


def test_validate_and_inspect_take_no_lock(ext4, monkeypatch):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    assert add_dir(root, "data")[0] == 0

    with dp.manifest_admission_lock(root):
        code, out, err = run_cli(["validate", str(root), "--directory-artifacts"])
        assert (code, err) == (0, "")
        code, out, err = run_cli(["inspect", str(root), "--directory-artifacts"])
        assert code == 0, out


# ---------------------------------------------------------------------------
# Sanitizer / phases / durability
# ---------------------------------------------------------------------------


def test_scanner_canaries_never_reach_public_output(ext4, monkeypatch):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    before = (root / MANIFEST).read_bytes()
    canaries = (
        "CANARY-ABS-ROOT", "CANARY-MEMBER.txt", "CANARY-STRERROR",
        "CANARY-INODE", "CANARY-DEVICE", "CANARY-MOUNT", "CANARY-DIGEST",
    )

    def refuse(path, limits=None):
        raise ds.DirectoryScanError(
            "unsafe",
            " ".join(canaries) + " /abs/root-canary member-canary.txt "
            "inode 123 dev 456 mount 789 digest " + "0" * 64,
        )

    monkeypatch.setattr(ds, "scan_directory", refuse)
    code, out, err = add_dir(root, "data")

    assert (code, err) == (2, "")
    assert "RECHECK_FAILED" in out
    combined = out + err
    for canary in canaries:
        assert canary not in combined
    assert "/abs/root-canary" not in combined
    assert "member-canary" not in combined
    assert (root / MANIFEST).read_bytes() == before


@pytest.mark.parametrize(
    ("attribute", "expected"),
    [
        ("_capture_metadata", INTERNAL_BEFORE),
        ("_boundary_final_guard", INTERNAL_CELL),
        ("_replace_manifest", INTERNAL_REPLACE),
    ],
)
def test_internal_error_phases_are_fixed_and_sanitized(
    ext4, monkeypatch, attribute, expected
):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    canaries = ("CANARY-INTERNAL-ROOT", "CANARY-INTERNAL-MEMBER", "CANARY-TRACE")

    def explode(*args, **kwargs):
        raise RuntimeError(" ".join(canaries))

    monkeypatch.setattr(dp, attribute, explode)
    code, out, err = add_dir(root, "data")

    assert (code, err) == (2, "")
    assert out.strip() == expected
    for canary in canaries:
        assert canary not in out + err
    assert "Traceback" not in out + err
    if attribute == "_capture_metadata":
        assert backups(root) == []
    else:
        # Phase 2/3 must not claim nothing was written once a backup exists.
        assert backups(root) == [MANIFEST + ".bak"]


def test_post_staging_failure_is_phase_two_and_keeps_manifest(ext4, monkeypatch):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    before = (root / MANIFEST).read_bytes()

    def explode(*args, **kwargs):
        raise RuntimeError("CANARY")

    monkeypatch.setattr(dp, "_boundary_staged_metadata", explode)
    code, out, err = add_dir(root, "data")

    assert (code, err) == (2, "")
    assert out.strip() == INTERNAL_CELL
    assert (root / MANIFEST).read_bytes() == before
    assert backups(root) == []


def test_durability_unknown_writes_and_exits_zero(ext4, monkeypatch):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    real_fsync = dp._fsync

    def fail_parent(fd, *, role):
        if role == "replace-parent":
            raise OSError(errno.EIO, "CANARY-DURABILITY")
        return real_fsync(fd, role=role)

    monkeypatch.setattr(dp, "_fsync", fail_parent)
    code, out, err = add_dir(root, "data")

    assert (code, err) == (0, "")
    assert "WARNING (DURABILITY_UNKNOWN)" in out
    assert "CANARY-DURABILITY" not in out + err
    assert len(raw_manifest(root)["artifacts"]) == 1


def test_backup_durability_failure_keeps_manifest(ext4, monkeypatch):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    before = (root / MANIFEST).read_bytes()
    real_fsync = dp._fsync

    def fail_backup(fd, *, role):
        if role == "backup-parent":
            raise OSError(errno.EIO, "CANARY-BACKUP")
        return real_fsync(fd, role=role)

    monkeypatch.setattr(dp, "_fsync", fail_backup)
    code, out, err = add_dir(root, "data")

    assert (code, err) == (2, "")
    assert "BACKUP_DURABILITY_FAILED" in out
    assert "the manifest was not changed" in out
    assert (root / MANIFEST).read_bytes() == before
    assert backups(root) == [MANIFEST + ".bak"]


# ---------------------------------------------------------------------------
# No artifacts / inspect
# ---------------------------------------------------------------------------


def test_success_leaves_only_the_manifest_and_backup(ext4, monkeypatch):
    root = make_exp(ext4)
    monkeypatch.chdir(root)

    assert add_dir(root, "data")[0] == 0

    assert names(root) == ["data", MANIFEST, MANIFEST + ".bak"]
    assert cells(root) == [] and temps(root) == []
    raw = raw_manifest(root)
    assert [entry["id"] for entry in raw["artifacts"]] == ["data"]
    assert raw["artifacts"][0]["artifact_type"] == "directory"


def _untracked_lines(output: str) -> list[str]:
    return sorted(line for line in output.splitlines() if "untracked-file" in line)


def test_inspect_untracked_findings_are_unchanged_without_the_flag(ext4, monkeypatch):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    before_code, before_out, before_err = run_cli(["inspect", str(root)])
    before = _untracked_lines(before_out)
    assert before, before_out

    assert add_dir(root, "data")[0] == 0

    after_code, after_out, after_err = run_cli(["inspect", str(root)])
    # The directory record is still not-a-file without the flag, so the exit
    # code may differ; its untracked-file findings must not.
    assert _untracked_lines(after_out) == before
    assert dp.CELL_PREFIX not in after_out


def test_inspect_directory_artifacts_covers_the_recorded_tree(ext4, monkeypatch):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    assert add_dir(root, "data")[0] == 0

    code, out, err = run_cli(["inspect", str(root), "--directory-artifacts"])

    assert (code, err) == (0, ""), out
    assert "untracked-file" not in out
    assert dp.CELL_PREFIX not in out


# ---------------------------------------------------------------------------
# Output-boundary and preparation/sanitizer regressions
# ---------------------------------------------------------------------------


def test_rehash_huge_recorded_size_never_claims_nothing_was_written(ext4, monkeypatch):
    """A hand-edited huge size must not raise while printing after a write."""
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    assert add_dir(root, "data")[0] == 0

    document = raw_manifest(root)
    record = next(item for item in document["artifacts"] if item["id"] == "data")
    record["size_bytes"] = 10**400
    (root / MANIFEST).write_bytes(dump_raw(document).encode("utf-8"))
    (root / "data" / "a.txt").write_bytes(b"hello again\n")

    code, out, err = run_cli(["rehash", str(root), "data", "--directory"])

    assert (code, err) == (0, ""), (out, err)
    assert "INTERNAL_ERROR" not in out
    assert "Nothing was written" not in out
    assert "Re-recorded 1 directory artifact" in out
    assert f"Wrote {root / MANIFEST}" in out
    assert (root / (MANIFEST + ".bak")).exists()


def test_add_overlong_directory_component_is_sanitized(ext4, monkeypatch):
    """ENAMETOOLONG during preparation is a fixed refusal, not a traceback."""
    root = make_exp(ext4)
    monkeypatch.chdir(root)

    code, out, err = add_dir(root, "a" * 256)

    assert code == 2, (out, err)
    assert out == (
        "ERROR (INVALID_PATH): the directory path is not an accepted v1 path. "
        "Nothing was written.\n"
    )
    assert err == ""
    assert "Traceback" not in out + err
    assert "File name too long" not in out + err
    assert cells(root) == [] and backups(root) == []


def test_output_failure_after_publication_uses_the_at_replace_phase(
    ext4, monkeypatch
):
    """A rendering bug after the replace must never say nothing was written."""
    root = make_exp(ext4)
    monkeypatch.chdir(root)

    def broken(*args, **kwargs):
        raise RuntimeError("render canary /abs/root member-name")

    monkeypatch.setattr(cli, "_print_directory_result", broken)
    code, out, err = add_dir(root, "data")

    assert (code, err) == (2, "")
    assert out == INTERNAL_REPLACE + "\n"
    assert "render canary" not in out
    assert "Nothing was written" not in out
    # The publication itself completed before rendering failed.
    assert (root / (MANIFEST + ".bak")).exists()
    assert any(
        item.get("path") == "data"
        for item in raw_manifest(root)["artifacts"]
    )


def test_overlong_manifest_path_is_sanitized_for_add_and_rehash(ext4, monkeypatch):
    """A hostile manifest path never reaches the shared load reporter."""
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    hostile = str(root / ("p" * 256))

    for argv in (
        ["add", hostile, "data", "--directory", "--id", "x"],
        ["add", hostile, "data", "--directory", "--id", "x", "--dry-run"],
        ["rehash", hostile, "data", "--directory"],
        ["rehash", hostile, "data", "--directory", "--dry-run"],
    ):
        code, out, err = run_cli(argv)
        assert code == 2, (argv, out, err)
        assert err == ""
        assert "Traceback" not in out + err
        assert "File name too long" not in out + err
        assert "[Errno" not in out + err
        assert "p" * 40 not in out
        assert out.startswith("ERROR (")
        assert "Nothing was written." in out


def test_late_render_failure_leaves_no_partial_success_output(ext4, monkeypatch):
    """A failure while formatting the rehash line emits only the phase-3 line."""
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    assert add_dir(root, "data")[0] == 0
    (root / "data" / "a.txt").write_bytes(b"changed\n")

    calls = {"count": 0}
    real_short = cli._short

    def fail_on_second(value):
        calls["count"] += 1
        if calls["count"] >= 2:
            raise RuntimeError("late render canary")
        return real_short(value)

    monkeypatch.setattr(cli, "_short", fail_on_second)
    code, out, err = run_cli(["rehash", str(root), "data", "--directory"])

    assert (code, err) == (2, "")
    assert out == INTERNAL_REPLACE + "\n"
    assert "Re-recorded" not in out
    assert "late render canary" not in out
    # The publication itself completed before the rendering failure.
    assert (root / (MANIFEST + ".bak")).exists()


# ---------------------------------------------------------------------------
# Dry-run load failures stay inside the public sanitizer
# ---------------------------------------------------------------------------


def _dry_run_load_cases(ext4: Path):
    missing = ext4 / "missing-exp"
    empty = ext4 / "empty-exp"
    empty.mkdir()
    (empty / "data").mkdir()
    (empty / "data" / "a.txt").write_bytes(b"x")
    (ext4 / "unreadable-exp").mkdir()
    unreadable = make_exp(ext4 / "unreadable-exp")
    os.chmod(unreadable / MANIFEST, 0o000)
    (ext4 / "invalid-exp").mkdir()
    invalid = make_exp(ext4 / "invalid-exp")
    (invalid / MANIFEST).write_bytes(b"{not json")
    return [
        (missing, missing / "data"),
        (empty, empty / "data"),
        (unreadable, unreadable / "data"),
        (invalid, invalid / "data"),
    ]


@pytest.mark.parametrize("writer", ["add", "rehash"])
def test_dry_run_load_failures_use_fixed_sanitized_lines(
    ext4, monkeypatch, writer
):
    cases = _dry_run_load_cases(ext4)
    monkeypatch.chdir(ext4)
    for path, directory in cases:
        argv = (
            ["add", str(path), str(directory), "--directory", "--id", "x", "--dry-run"]
            if writer == "add"
            else ["rehash", str(path), "data", "--directory", "--dry-run"]
        )
        code, out, err = run_cli(argv)
        assert code == 2, (writer, path, out, err)
        assert err == ""
        assert out.startswith("ERROR (")
        assert "Nothing was written." in out
        assert "[Errno" not in out
        assert "Traceback" not in out
        assert str(ext4) not in out
        assert "No manifest at" not in out
        assert "hint:" not in out
    for path, directory in cases:
        if path.name == "unreadable-exp":
            os.chmod(path / MANIFEST, 0o644)


def test_dry_run_missing_and_invalid_manifest_reasons(ext4, monkeypatch):
    empty = ext4 / "empty-exp"
    empty.mkdir()
    (empty / "data").mkdir()
    (empty / "data" / "a.txt").write_bytes(b"x")
    (ext4 / "invalid-exp").mkdir()
    invalid = make_exp(ext4 / "invalid-exp")
    (invalid / MANIFEST).write_bytes(b"{not json")
    monkeypatch.chdir(ext4)

    code, out, err = run_cli(
        ["add", str(empty), str(empty / "data"), "--directory", "--id", "x", "--dry-run"]
    )
    assert (code, err) == (2, "")
    assert "ERROR (NOT_FOUND)" in out

    code, out, err = run_cli(
        ["add", str(invalid), str(invalid / "data"), "--directory", "--id", "x", "--dry-run"]
    )
    assert (code, err) == (2, "")
    assert "ERROR (INVALID_MANIFEST)" in out


def test_rehash_preparation_wrapper_is_sanitized(ext4, monkeypatch):
    root = make_exp(ext4, directory_artifact=False)
    monkeypatch.chdir(root)

    def broken(args):
        raise OSError(errno.ENAMETOOLONG, "File name too long", "/abs/canary")

    monkeypatch.setattr(cli, "_directory_prepare_rehash", broken)
    code, out, err = run_cli(["rehash", str(root), "data", "--directory"])

    assert (code, err) == (2, "")
    assert out == (
        "ERROR (INVALID_PATH): the directory path is not an accepted v1 path. "
        "Nothing was written.\n"
    )
    assert "canary" not in out


def test_filesystem_preflight_maps_hostile_paths_to_invalid_path(ext4, monkeypatch):
    def broken(parts, policy, table):
        raise ValueError("embedded null byte")

    monkeypatch.setattr(ds, "_preflight_mounts", broken)
    assert cli._directory_fs_refusal([ext4]) == "invalid-path"


# ---------------------------------------------------------------------------
# Truthful reasons for an unreadable or dangling manifest; schema 0.2
# ---------------------------------------------------------------------------


PERMISSION_DENIED_TEXT = (
    "ERROR (PERMISSION_DENIED): the manifest or its folder is not writable "
    "by this user. Nothing was written."
)


@pytest.mark.parametrize("writer", ["add", "rehash"])
@pytest.mark.parametrize("dry", [False, True])
def test_unreadable_manifest_reports_permission_denied(
    ext4, monkeypatch, writer, dry
):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    before = (root / MANIFEST).read_bytes()
    os.chmod(root / MANIFEST, 0o000)
    try:
        argv = (
            ["add", str(root), str(root / "data"), "--directory", "--id", "x"]
            if writer == "add"
            else ["rehash", str(root), "data", "--directory"]
        )
        if dry:
            argv.append("--dry-run")
        code, out, err = run_cli(argv)
    finally:
        os.chmod(root / MANIFEST, 0o644)

    assert (code, err) == (2, "")
    assert out == PERMISSION_DENIED_TEXT + "\n"
    assert (root / MANIFEST).read_bytes() == before
    assert not (root / (MANIFEST + ".bak")).exists()


@pytest.mark.parametrize("writer", ["add", "rehash"])
@pytest.mark.parametrize("dry", [False, True])
@pytest.mark.parametrize("target_parent", ["present", "missing"])
def test_dangling_manifest_alias_reports_not_found(
    ext4, monkeypatch, writer, dry, target_parent
):
    root = make_exp(ext4)
    monkeypatch.chdir(root)
    if target_parent == "present":
        target = root / "missing-target.json"
    else:
        target = ext4 / "missing-parent" / "target.json"
    (root / MANIFEST).unlink()
    (root / MANIFEST).symlink_to(target)
    try:
        argv = (
            ["add", str(root), str(root / "data"), "--directory", "--id", "x"]
            if writer == "add"
            else ["rehash", str(root), "data", "--directory"]
        )
        if dry:
            argv.append("--dry-run")
        code, out, err = run_cli(argv)
        assert (code, err) == (2, "")
        assert out == "ERROR (NOT_FOUND): no such manifest or artifact. Nothing was written.\n"
        assert not target.exists()
    finally:
        (root / MANIFEST).unlink()


def test_directory_authoring_works_on_a_schema_0_2_manifest(ext4, monkeypatch):
    root = make_exp(ext4, with_run=True)
    monkeypatch.chdir(root)
    code, out, err = run_cli(["migrate", str(root), "--to", "0.2"])
    assert (code, err) == (0, ""), out
    assert raw_manifest(root)["schema_version"] == "0.2"

    code, out, err = run_cli(
        ["add", str(root), str(root / "data"), "--directory", "--id", "data",
         "--kind", "dataset", "--run", "run-1"]
    )
    assert (code, err) == (0, ""), out
    document = raw_manifest(root)
    assert document["schema_version"] == "0.2"
    record = next(item for item in document["artifacts"] if item["id"] == "data")
    assert record["artifact_type"] == "directory"
    assert record["kind"] == "dataset"
    assert record["run_id"] == "run-1"

    code, out, err = run_cli(["rehash", str(root), "data", "--directory"])
    assert (code, err) == (0, ""), out
    assert "unchanged" in out
    assert raw_manifest(root)["schema_version"] == "0.2"
    assert run_cli(["validate", str(root), "--directory-artifacts"])[0] == 0


def test_manifest_precheck_does_not_block_on_a_fifo(tmp_path):
    """A FIFO swapped in before the pre-check must refuse, not wait."""
    fifo = tmp_path / MANIFEST
    os.mkfifo(fifo)
    code = textwrap.dedent(
        """
        import sys
        sys.path.insert(0, sys.argv[1])
        from runevidence import cli
        cli._directory_manifest_usable(sys.argv[2])
        """
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, str(SRC), str(fifo)],
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "INVALID_PATH" in result.stdout
    assert result.stderr == ""


def test_dry_run_manifest_fifo_does_not_hang(tmp_path):
    """The dry-run loader must not wait on a FIFO swapped in as the manifest."""
    fifo = tmp_path / MANIFEST
    os.mkfifo(fifo)
    script = textwrap.dedent(
        """
        import sys
        sys.path.insert(0, sys.argv[1])
        from runevidence import cli
        cli._directory_load_dry_run(sys.argv[2])
        """
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(SRC), str(fifo)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert "INVALID_PATH" in result.stdout
    assert "Traceback" not in result.stdout + result.stderr
    assert result.stderr == ""
