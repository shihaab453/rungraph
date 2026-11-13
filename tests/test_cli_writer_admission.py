"""S4-2a evidence: built-in manifest writers take the folder admission lock.

The retrofit is cooperative and Linux-only. These tests exercise the real
``flock``: a second writer (same process or a separate one) sees ``busy`` and
refuses with exit 2 and fixed text, writes nothing, and never waits. A killed
holder releases the kernel lock. Dry runs take no lock and perform no fsync.
Sequential success behavior is pinned byte-for-byte.

No directory write, no new route and no new flag is claimed or exercised here.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

# ``directory_publication`` imports ``fcntl`` at module level. Windows cannot
# import it, so collection skips instead of erroring; the CLI's lazy import of
# that module already fails closed off POSIX.
pytest.importorskip("fcntl")

from runevidence import cli
from runevidence import directory_publication as dp
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

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the S4-2a admission lock is Linux-only (no Windows parity claim)",
)

HOLDER = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {src!r})
    from runevidence.directory_publication import builtin_writer_admission_lock
    with builtin_writer_admission_lock(sys.argv[1]):
        sys.stdout.write("locked\\n")
        sys.stdout.flush()
        sys.stdin.read()
    """
)


# --- fixtures and helpers ----------------------------------------------------


def make_exp(
    tmp_path: Path,
    *,
    name: str = "exp",
    version: str = "0.1",
    wrong_digest: bool = False,
    conditions: bool = False,
) -> Path:
    exp = tmp_path / name
    exp.mkdir(parents=True)
    notes = b"notes\n"
    (exp / "notes.txt").write_bytes(notes)
    (exp / "new.txt").write_bytes(b"new\n")
    digest = hashlib.sha256(b"older\n" if wrong_digest else notes).hexdigest()
    run: dict = {"id": "run-1", "name": "Run"}
    if conditions:
        run["conditions"] = {"environment": {"seed": 1}}
    raw: dict = {
        "project": {"id": "proj-1", "name": "Project"},
        "schema_version": version,
        "runs": [run],
        "artifacts": [
            {"id": "notes", "path": "notes.txt", "sha256": digest, "size_bytes": len(notes)}
        ],
        "results": [],
        "evidence": [],
    }
    if version == "0.2":
        raw["relationships"] = []
    (exp / MANIFEST).write_bytes(dump_raw(raw).encode("utf-8"))
    return exp


def run_cli(argv) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(argv)
    return code, out.getvalue()


def snapshot(folder: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted(folder.iterdir())
        if path.is_file()
    }


def names(folder: Path) -> list[str]:
    return sorted(path.name for path in folder.iterdir())


def cells(folder: Path) -> list[str]:
    return [name for name in names(folder) if name.startswith(dp.CELL_PREFIX)]


@contextlib.contextmanager
def hold(target):
    """Hold the exact lock the writers use, without the S4-1 fs policy gate."""
    with dp.builtin_writer_admission_lock(target) as locked:
        yield locked


@contextlib.contextmanager
def blocked_import(monkeypatch, module: str):
    """Make ``import module`` (absolute or package-relative) fail."""

    import builtins

    real_import = builtins.__import__

    def blocked(name, globals=None, locals=None, fromlist=(), level=0):
        if name == module and level in (0, 1):
            raise ImportError(f"injected {module} import failure")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked)
    try:
        yield
    finally:
        monkeypatch.setattr(builtins, "__import__", real_import)


def blocked_publication_import(monkeypatch):
    """Make the lazy ``from .directory_publication import ...`` fail."""
    return blocked_import(monkeypatch, "directory_publication")


def restore_snapshot(folder: Path, snapshot_data: dict[str, bytes]) -> None:
    """Return a folder to a prior snapshot (writers only touch top-level files)."""
    for path in list(folder.iterdir()):
        if path.is_file() and path.name not in snapshot_data:
            path.unlink()
    for name, data in snapshot_data.items():
        (folder / name).write_bytes(data)


def start_holder(exp: Path) -> subprocess.Popen:
    code = HOLDER.format(src=str(SRC))
    proc = subprocess.Popen(
        [sys.executable, "-B", "-c", code, str(exp)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
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


def writer_argv(exp: Path, writer: str) -> list[str]:
    return {
        "init": ["init", str(exp), "--force"],
        "add": ["add", str(exp), str(exp / "new.txt")],
        "rehash": ["rehash", str(exp), "--all"],
        "migrate": ["migrate", str(exp), "--to", "0.2"],
        "conditions": ["conditions", str(exp), "--run", "run-1", "--clear", "environment"],
    }[writer]


def writer_exp(tmp_path: Path, writer: str) -> Path:
    if writer == "conditions":
        return make_exp(tmp_path, version="0.2", conditions=True)
    if writer == "rehash":
        return make_exp(tmp_path, wrong_digest=True)
    return make_exp(tmp_path)


WRITERS = ("init", "add", "rehash", "migrate", "conditions")


# --- contention --------------------------------------------------------------


@pytest.mark.parametrize("writer", WRITERS)
def test_two_process_contention_loser_refuses_and_winner_completes(tmp_path, writer):
    exp = writer_exp(tmp_path / writer, writer)
    argv = writer_argv(exp, writer)
    before = snapshot(exp)

    holder = start_holder(exp)
    try:
        code, out = run_cli(argv)
        assert (code, out) == (2, BUSY_TEXT + "\n")
        assert snapshot(exp) == before
        assert cells(exp) == []
        assert not any(name.endswith(".tmp") for name in names(exp))
    finally:
        stop_holder(holder)

    code, out = run_cli(argv)
    assert code == 0, out
    assert BUSY_TEXT not in out
    assert snapshot(exp) != before


@pytest.mark.parametrize("writer", WRITERS)
def test_same_process_self_busy_is_refused(tmp_path, writer):
    exp = writer_exp(tmp_path / writer, writer)
    argv = writer_argv(exp, writer)
    before = snapshot(exp)

    with hold(exp):
        assert run_cli(argv) == (2, BUSY_TEXT + "\n")
        assert snapshot(exp) == before
        assert cells(exp) == []

    code, out = run_cli(argv)
    assert code == 0, out


def test_s4_1_manifest_admission_lock_and_writers_contend(tmp_path):
    exp = make_exp(tmp_path)
    argv = ["add", str(exp), str(exp / "new.txt")]
    try:
        with dp.manifest_admission_lock(exp):
            assert run_cli(argv) == (2, BUSY_TEXT + "\n")
    except dp.DirectoryPublicationError as exc:
        if exc.reason in ("unsupported-platform", "unsupported-filesystem"):
            pytest.skip(f"S4-1 core cannot lock this host: {exc.reason}: draft evidence")
        raise
    assert run_cli(argv)[0] == 0


def test_sigkill_releases_the_kernel_lock(tmp_path):
    exp = make_exp(tmp_path)
    argv = ["add", str(exp), str(exp / "new.txt")]

    holder = start_holder(exp)
    try:
        assert run_cli(argv) == (2, BUSY_TEXT + "\n")
        holder.send_signal(signal.SIGKILL)
        holder.wait(timeout=30)
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=30)

    # No stale lock artifact remains, and the next cooperating writer wins.
    with hold(exp):
        pass
    code, out = run_cli(argv)
    assert code == 0, out
    assert "new.txt" in out


# --- fail-closed admission ---------------------------------------------------
#
# These are the regression-sensitive cases: the earlier implementation fell
# back to an unlocked write when the lock could not be taken. Each writer must
# now refuse with LOCK_UNAVAILABLE (exit 2) before loading or writing.


@pytest.mark.parametrize("writer", WRITERS)
def test_admission_import_failure_fails_closed(tmp_path, monkeypatch, writer):
    exp = writer_exp(tmp_path / writer, writer)
    before = snapshot(exp)

    with blocked_publication_import(monkeypatch):
        code, out = run_cli(writer_argv(exp, writer))

    assert (code, out) == (2, LOCK_UNAVAILABLE_TEXT + "\n")
    assert BUSY_TEXT not in out
    assert snapshot(exp) == before
    assert cells(exp) == []


def test_builtin_writer_helper_reports_unsupported_as_lock_failed(tmp_path, monkeypatch):
    exp = make_exp(tmp_path)
    monkeypatch.setattr(dp, "_writer_lock_supported", lambda: False)

    with pytest.raises(dp.DirectoryPublicationError) as info:
        with dp.builtin_writer_admission_lock(exp):
            pytest.fail("the lock was unexpectedly acquired")

    # A non-contention failure is "lock-failed", never "busy".
    assert info.value.reason == "lock-failed"


def test_cli_maps_non_busy_admission_failure_to_lock_unavailable(
    tmp_path, monkeypatch
):
    exp = make_exp(tmp_path)
    before = snapshot(exp)
    monkeypatch.setattr(cli, "_directory_flock_available", lambda: True)

    def unsupported(target):
        raise dp.DirectoryPublicationError("unsupported-platform")

    monkeypatch.setattr(dp, "_resolve_writer_folder", unsupported)

    # Misclassifying this as BUSY would raise the wrong exception here.
    with pytest.raises(cli._WriterLockUnavailable):
        with cli._writer_lock(exp):
            pass

    code, out = run_cli(["add", str(exp), str(exp / "new.txt")])

    assert (code, out) == (2, LOCK_UNAVAILABLE_TEXT + "\n")
    assert BUSY_TEXT not in out
    assert snapshot(exp) == before


@pytest.mark.parametrize("writer", ("add", "rehash", "migrate", "conditions"))
def test_lock_unavailable_precedes_manifest_load(tmp_path, monkeypatch, writer):
    exp = writer_exp(tmp_path / writer, writer)
    (exp / MANIFEST).write_bytes(b"{not a valid manifest\n")
    before = snapshot(exp)

    with blocked_publication_import(monkeypatch):
        code, out = run_cli(writer_argv(exp, writer))

    # The broken manifest is never reached: admission fails first.
    assert (code, out) == (2, LOCK_UNAVAILABLE_TEXT + "\n")
    assert snapshot(exp) == before


@pytest.mark.parametrize("error_number", (errno.EIO, errno.ENOLCK))
@pytest.mark.parametrize("writer", WRITERS)
def test_non_busy_flock_error_fails_closed(tmp_path, monkeypatch, writer, error_number):
    exp = writer_exp(tmp_path / writer, writer)
    before = snapshot(exp)

    def boom(fd, operation):
        raise OSError(error_number, "injected flock failure")

    monkeypatch.setattr(dp.fcntl, "flock", boom)

    code, out = run_cli(writer_argv(exp, writer))

    assert (code, out) == (2, LOCK_UNAVAILABLE_TEXT + "\n")
    assert BUSY_TEXT not in out
    assert snapshot(exp) == before
    assert cells(exp) == []


@pytest.mark.parametrize("writer", WRITERS)
def test_eacces_lock_open_fails_closed_even_when_contended(tmp_path, writer):
    if os.geteuid() == 0:
        pytest.skip("root bypasses the folder permission check")

    exp = writer_exp(tmp_path / writer, writer)
    before = snapshot(exp)
    argv = writer_argv(exp, writer)

    # A real second process holds the lock. The folder then loses read
    # permission (write+traverse remain), so opening the lock descriptor fails
    # with EACCES before flock. The writer must report LOCK_UNAVAILABLE, never
    # BUSY, and must not write.
    holder = start_holder(exp)
    try:
        os.chmod(exp, 0o300)
        try:
            # Prove this is the real EACCES lock-open case, not a synthetic one.
            try:
                probe = os.open(exp, os.O_RDONLY | os.O_DIRECTORY)
            except PermissionError:
                pass
            else:
                os.close(probe)
                pytest.fail("expected EACCES opening the folder without read permission")
            code, out = run_cli(argv)
        finally:
            os.chmod(exp, 0o700)

        assert (code, out) == (2, LOCK_UNAVAILABLE_TEXT + "\n")
        assert BUSY_TEXT not in out
        assert snapshot(exp) == before
        assert cells(exp) == []
    finally:
        stop_holder(holder)

    # The failed attempt left no lock behind for the next cooperating writer.
    with hold(exp):
        pass


@pytest.mark.parametrize(
    ("writer", "format_id"),
    [
        ("migrate", "runevidence-migrate-error-v1"),
        ("conditions", "runevidence-conditions-error-v1"),
    ],
)
def test_lock_unavailable_json_envelopes_are_closed_and_path_free(
    tmp_path, monkeypatch, writer, format_id
):
    exp = writer_exp(tmp_path / writer, writer)
    if writer == "migrate":
        argv = ["migrate", str(exp), "--to", "0.2", "--json"]
    else:
        argv = [
            "conditions", str(exp), "--run", "run-1", "--clear", "environment", "--json"
        ]
    before = snapshot(exp)

    with blocked_publication_import(monkeypatch):
        code, out = run_cli(argv)

    assert code == 2
    assert json.loads(out) == {
        "format": format_id,
        "ok": False,
        "exit_code": 2,
        "error": {"code": "LOCK_UNAVAILABLE"},
    }
    assert str(exp) not in out and str(exp.parent) not in out
    assert snapshot(exp) == before


@pytest.mark.parametrize("writer", ("add", "rehash"))
def test_missing_folder_keeps_the_legacy_manifest_not_found_error(tmp_path, writer):
    target = tmp_path / "missing" / "exp"
    argv = {
        "add": ["add", str(target), str(target / "x.txt")],
        "rehash": ["rehash", str(target), "--all"],
    }[writer]

    code, out = run_cli(argv)

    # L1: a missing folder is a path error, not a lock error. The pre-admission
    # shape check keeps the existing manifest-not-found report and init hint.
    assert code == 2
    assert "manifest-not-found" in out
    assert f"No manifest at {target}" in out
    assert f"runevidence init {target.parent}" in out
    assert "INVALID: manifest could not be checked" in out
    assert LOCK_UNAVAILABLE_TEXT not in out
    assert BUSY_TEXT not in out
    assert not target.exists()


def test_missing_folder_keeps_the_legacy_manifest_load_failed_error(tmp_path):
    target = tmp_path / "missing" / "exp"

    code, out = run_cli([
        "conditions", str(target), "--run", "run-1", "--clear", "environment"
    ])

    assert code == 2
    assert "MANIFEST_LOAD_FAILED" in out
    assert LOCK_UNAVAILABLE_TEXT not in out
    assert BUSY_TEXT not in out
    assert not target.exists()


def test_init_path_errors_are_reported_before_the_lock(tmp_path, monkeypatch):
    missing = tmp_path / "missing"
    a_file = tmp_path / "a-file"
    a_file.write_bytes(b"not a folder")

    # Even when the lock import would fail, init keeps its existing cheap path
    # error because the path type is validated before admission.
    with blocked_publication_import(monkeypatch):
        for target in (missing, a_file):
            code, out = run_cli(["init", str(target)])
            assert code == 2
            assert out == f"ERROR: {target.resolve()} is not a folder\n"


# --- symlink retarget races --------------------------------------------------
#
# A target like `link/runevidence.json` where `link` is a folder symlink must
# never let two cooperating writers lock different folders and lose a
# committed update. Admission re-resolves after flock and retries (bounded);
# the command then loads/saves the resolved path under that lock, never the
# alias.


def make_race_folder(tmp_path: Path, *, names=("A", "B")):
    """Two manifest folders plus a folder symlink initially pointing at A."""
    root = tmp_path / "root"
    root.mkdir()
    manifests = {}
    for name in names:
        folder = root / name
        folder.mkdir()
        (folder / "base.txt").write_bytes(b"base\n")
        raw = {
            "project": {"id": f"proj-{name}", "name": name},
            "schema_version": "0.1",
            "runs": [],
            "artifacts": [
                {
                    "id": "base",
                    "path": "base.txt",
                    "sha256": hashlib.sha256(b"base\n").hexdigest(),
                    "size_bytes": 5,
                }
            ],
            "results": [],
            "evidence": [],
        }
        (folder / MANIFEST).write_bytes(dump_raw(raw).encode("utf-8"))
        manifests[name] = folder
    for file_name in ("w1.txt", "w2.txt"):
        for folder in manifests.values():
            (folder / file_name).write_bytes(file_name.encode("ascii"))
    alias = root / "alias"
    alias.symlink_to("A", target_is_directory=True)
    return root, alias, manifests


def retarget_alias(alias: Path, destination: str) -> None:
    """Atomically replace the folder symlink (os.rename replaces on POSIX)."""
    candidate = alias.with_name(alias.name + ".new")
    try:
        candidate.unlink()
    except FileNotFoundError:
        pass
    candidate.symlink_to(destination, target_is_directory=True)
    os.rename(candidate, alias)


def manifest_paths(folder: Path) -> list[str]:
    raw = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    return [artifact["path"] for artifact in raw["artifacts"]]


def test_retarget_between_resolve_and_lock_retries_to_current_target(
    tmp_path, monkeypatch
):
    root, alias, folders = make_race_folder(tmp_path)
    calls: list[int] = []

    def boundary(target_path, parent):
        if not calls:
            calls.append(1)
            retarget_alias(alias, "B")

    monkeypatch.setattr(dp, "_boundary_before_writer_lock_open", boundary)
    before_a = (folders["A"] / MANIFEST).read_bytes()

    code, out = run_cli(["add", str(alias), str(alias / "w1.txt")])

    # The post-lock re-resolution must retry onto B, the current target.
    assert code == 0, out
    assert "w1.txt" in manifest_paths(folders["B"])
    assert manifest_paths(folders["A"]) == ["base.txt"]
    assert (folders["A"] / MANIFEST).read_bytes() == before_a
    assert alias.resolve() == folders["B"].resolve()
    assert str(folders["B"] / MANIFEST) in out


def test_alias_retarget_after_load_keeps_the_locked_target(tmp_path, monkeypatch):
    root, alias, folders = make_race_folder(tmp_path)
    real_load = cli.load_manifest
    retargeted: list[int] = []

    def load(path):
        loaded = real_load(path)
        if not retargeted:
            retargeted.append(1)
            retarget_alias(alias, "B")
        return loaded

    monkeypatch.setattr(cli, "load_manifest", load)
    before_b = (folders["B"] / MANIFEST).read_bytes()

    code, out = run_cli(["add", str(alias), str(folders["A"] / "w1.txt")])

    # Load and save use the resolved A path under A's lock; the later alias
    # retarget does not move the write to B.
    assert code == 0, out
    assert "w1.txt" in manifest_paths(folders["A"])
    assert (folders["B"] / MANIFEST).read_bytes() == before_b
    assert str(folders["A"] / MANIFEST) in out


def test_retarget_after_post_lock_check_before_load_keeps_locked_target(
    tmp_path, monkeypatch
):
    root, alias, folders = make_race_folder(tmp_path)
    real_load = cli.load_manifest
    retargeted: list[int] = []

    def load(path):
        # This runs after admission has checked the resolution and before the
        # real loader resolves its argument, so it catches yielding the alias.
        if not retargeted:
            retargeted.append(1)
            retarget_alias(alias, "B")
        return real_load(path)

    monkeypatch.setattr(cli, "load_manifest", load)
    before_b = (folders["B"] / MANIFEST).read_bytes()

    code, out = run_cli(["add", str(alias), str(folders["A"] / "w1.txt")])

    assert code == 0, out
    assert "w1.txt" in manifest_paths(folders["A"])
    assert (folders["B"] / MANIFEST).read_bytes() == before_b
    assert str(folders["A"] / MANIFEST) in out


def test_resolution_mismatch_releases_first_lock_before_retry(tmp_path, monkeypatch):
    root, alias, folders = make_race_folder(tmp_path)
    calls: list[int] = []

    def boundary(target_path, parent):
        if not calls:
            calls.append(1)
            retarget_alias(alias, "B")

    monkeypatch.setattr(dp, "_boundary_before_writer_lock_open", boundary)

    code, out = run_cli(["add", str(alias), str(alias / "w1.txt")])
    assert code == 0, out

    # A leaked first lock on A would make either acquisition BUSY here.
    with hold(folders["A"]):
        pass
    with hold(folders["B"]):
        pass


def test_persistent_resolution_churn_fails_closed_without_leaking_locks(
    tmp_path, monkeypatch
):
    root, alias, folders = make_race_folder(tmp_path)
    before_a = (folders["A"] / MANIFEST).read_bytes()
    before_b = (folders["B"] / MANIFEST).read_bytes()
    calls: list[int] = []

    def boundary(target_path, parent):
        calls.append(1)
        retarget_alias(alias, "B" if len(calls) % 2 else "A")

    monkeypatch.setattr(dp, "_boundary_before_writer_lock_open", boundary)

    code, out = run_cli(["add", str(alias), str(alias / "w1.txt")])

    assert (code, out) == (2, LOCK_UNAVAILABLE_TEXT + "\n")
    assert len(calls) == dp.WRITER_LOCK_ATTEMPTS
    assert (folders["A"] / MANIFEST).read_bytes() == before_a
    assert (folders["B"] / MANIFEST).read_bytes() == before_b
    with hold(folders["A"]):
        pass
    with hold(folders["B"]):
        pass


@pytest.mark.parametrize("writer", ("add", "rehash", "migrate", "conditions"))
def test_precheck_false_but_path_loadable_refuses_without_write(
    tmp_path, monkeypatch, writer
):
    if writer == "conditions":
        exp = make_exp(tmp_path / writer, version="0.2", conditions=True)
    else:
        exp = make_exp(tmp_path / writer)
    before = snapshot(exp)
    argv = {
        "add": ["add", str(exp), str(exp / "new.txt")],
        "rehash": ["rehash", str(exp), "--all"],
        "migrate": ["migrate", str(exp), "--to", "0.2"],
        "conditions": [
            "conditions", str(exp), "--run", "run-1", "--clear", "environment"
        ],
    }[writer]

    # Simulate the folder/manifest appearing between the cheap precheck and the
    # fallback load: the fallback must still never write unlocked.
    monkeypatch.setattr(cli, "_writer_target_folder_exists", lambda target: False)
    code, out = run_cli(argv)

    assert (code, out) == (2, LOCK_UNAVAILABLE_TEXT + "\n")
    assert BUSY_TEXT not in out
    assert snapshot(exp) == before


def test_init_force_over_symlinked_manifest_writes_the_root_entry(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "data.txt").write_bytes(b"data\n")
    other = tmp_path / "other"
    other.mkdir()
    other_raw = {
        "project": {"id": "other-project", "name": "Other"},
        "schema_version": "0.1",
        "runs": [],
        "artifacts": [],
        "results": [],
        "evidence": [],
    }
    (other / MANIFEST).write_bytes(dump_raw(other_raw).encode("utf-8"))
    (root / MANIFEST).symlink_to(Path("../other") / MANIFEST)
    other_before = (other / MANIFEST).read_bytes()

    code, out = run_cli(
        ["init", str(root), "--force", "--id", "root-project", "--name", "Root"]
    )

    assert code == 0, out
    root_manifest = root / MANIFEST
    assert not root_manifest.is_symlink()
    written = json.loads(root_manifest.read_text(encoding="utf-8"))
    assert written["project"]["id"] == "root-project"
    # The symlink target is never written, and its bytes are in root's backup.
    assert (other / MANIFEST).read_bytes() == other_before
    assert (root / (MANIFEST + ".bak")).read_bytes() == other_before
    assert not (other / (MANIFEST + ".bak")).exists()


def _other_manifest_bytes():
    return dump_raw(
        {
            "project": {"id": "other-project", "name": "Other"},
            "schema_version": "0.1",
            "runs": [],
            "artifacts": [],
            "results": [],
            "evidence": [],
        }
    ).encode("utf-8")


def test_init_force_refuses_a_directory_symlink_manifest_without_following_it(
    tmp_path,
):
    """A directory at <root>/runevidence.json is a legacy save failure.

    The writer must not apply the loader's directory rule here: doing so would
    lock and overwrite the manifest *inside* the linked folder.
    """
    root = tmp_path / "root"
    root.mkdir()
    (root / "data.txt").write_bytes(b"data\n")
    other = tmp_path / "other"
    other.mkdir()
    other_before = _other_manifest_bytes()
    (other / MANIFEST).write_bytes(other_before)
    (root / MANIFEST).symlink_to(other, target_is_directory=True)

    code, out = run_cli(
        ["init", str(root), "--force", "--id", "root-project", "--name", "Root"]
    )

    assert code == 2, out
    assert (other / MANIFEST).read_bytes() == other_before
    assert not (other / (MANIFEST + ".bak")).exists()
    assert (root / MANIFEST).is_symlink()


def test_init_force_refuses_a_real_directory_manifest(tmp_path):
    """A real directory named runevidence.json is refused, not descended into."""
    root = tmp_path / "root"
    root.mkdir()
    (root / "data.txt").write_bytes(b"data\n")
    inner = root / MANIFEST
    inner.mkdir()
    inner_before = _other_manifest_bytes()
    (inner / MANIFEST).write_bytes(inner_before)

    code, out = run_cli(
        ["init", str(root), "--force", "--id", "root-project", "--name", "Root"]
    )

    assert code == 2, out
    assert (inner / MANIFEST).read_bytes() == inner_before
    assert not (inner / (MANIFEST + ".bak")).exists()


# Real two-process regression: W1 is paused between resolution and flock; the
# alias retargets to B; W2 writes B. Both must serialize on B, or one must
# refuse BUSY. A successful writer's update may never disappear.
WORKER = r"""
import os, sys, time
from pathlib import Path

src = sys.argv[1]
sys.path.insert(0, src)
from runevidence import cli, directory_publication as dp

name, target, file_name, alias, retarget_to, barrier, state = sys.argv[2:9]
barrier = Path(barrier)
state = Path(state)
alias = Path(alias)


def wait_for(path, seconds=1.5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if Path(path).exists():
            return True
        time.sleep(0.01)
    return False


real_load = cli.load_manifest


def load(path):
    loaded = real_load(path)
    (barrier / ("loaded." + name)).write_text("1")
    wait_for(barrier / "loaded.w1")
    wait_for(barrier / "loaded.w2")
    return loaded


cli.load_manifest = load

real_save = cli.save_raw


def save(raw, path, *, expected_bytes):
    (barrier / ("saving." + name)).write_text("1")
    wait_for(barrier / "saving.w1")
    wait_for(barrier / "saving.w2")
    return real_save(raw, path, expected_bytes=expected_bytes)


cli.save_raw = save

real_replace = os.replace


def replace(source, destination, *args, **kwargs):
    (barrier / ("replace." + name)).write_text("1")
    wait_for(barrier / "replace.w1")
    wait_for(barrier / "replace.w2")
    return real_replace(source, destination, *args, **kwargs)


os.replace = replace

if retarget_to != "-":
    def boundary(target_path, parent):
        marker = state / "retargeted"
        try:
            fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return
        else:
            os.close(fd)
        candidate = Path(str(alias) + ".new")
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
        candidate.symlink_to(retarget_to, target_is_directory=True)
        os.rename(candidate, alias)

    dp._boundary_before_writer_lock_open = boundary

print("CODE", cli.main(["add", target, file_name]), flush=True)
"""


def test_two_process_retarget_race_does_not_lose_a_committed_update(
    tmp_path, monkeypatch
):
    root, alias, folders = make_race_folder(tmp_path)
    barrier = tmp_path / "barrier"
    state = tmp_path / "state"
    barrier.mkdir()
    state.mkdir()

    def start(name, retarget_to):
        args = [
            sys.executable, "-B", "-c", WORKER, str(SRC),
            name, str(alias), str(alias / f"{name}.txt"), str(alias),
            retarget_to, str(barrier), str(state),
        ]
        return subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

    w1 = start("w1", str(folders["B"]))
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if alias.resolve() == folders["B"].resolve():
                break
            time.sleep(0.01)
        else:
            pytest.fail("the injected retarget never happened")
        w2 = start("w2", "-")
        out1, err1 = w1.communicate(timeout=60)
        out2, err2 = w2.communicate(timeout=60)
    finally:
        if w1.poll() is None:
            w1.kill()
            w1.wait(timeout=30)

    codes = {}
    for name, out, err in (("w1", out1, err1), ("w2", out2, err2)):
        line = [ln for ln in out.splitlines() if ln.startswith("CODE ")]
        assert line and err == "", (name, out, err)
        codes[name] = int(line[-1].split()[1])

    assert set(codes.values()) <= {0, 2}
    assert 0 in codes.values()
    for name, code in codes.items():
        if code == 2:
            # The only accepted refusal is real contention.
            output = out1 if name == "w1" else out2
            assert BUSY_TEXT in output
            assert LOCK_UNAVAILABLE_TEXT not in output

    # Every writer that reported success is still present after both finish;
    # no committed update is lost.
    paths_b = manifest_paths(folders["B"])
    for name, code in codes.items():
        if code == 0:
            assert f"{name}.txt" in paths_b, (name, codes, paths_b)
    # The retargeted alias points at B and A was not written behind it.
    assert alias.resolve() == folders["B"].resolve()
    assert manifest_paths(folders["A"]) == ["base.txt"]


# --- stable nested-directory layout -----------------------------------------
#
# The loader appends runevidence.json at most once when an operand names a
# directory. A nested layout (exp/runevidence.json/ is itself a directory) is
# therefore not loadable through the outer operand: `add EXP` keeps its legacy
# refusal and must never write beside it. The inner operand still names the
# real manifest and locks its folder.


def make_nested_folder(tmp_path: Path):
    exp = tmp_path / "exp"
    inner = exp / MANIFEST
    inner.mkdir(parents=True)
    (inner / "base.txt").write_bytes(b"base\n")
    raw = {
        "project": {"id": "proj-nested", "name": "Nested"},
        "schema_version": "0.1",
        "runs": [],
        "artifacts": [
            {
                "id": "base",
                "path": "base.txt",
                "sha256": hashlib.sha256(b"base\n").hexdigest(),
                "size_bytes": 5,
            }
        ],
        "results": [],
        "evidence": [],
    }
    (inner / MANIFEST).write_bytes(dump_raw(raw).encode("utf-8"))
    for name in ("w1.txt", "w2.txt"):
        (inner / name).write_bytes(name.encode("ascii"))
    return exp, inner


def test_outer_nested_directory_operand_is_refused_like_the_loader(tmp_path):
    exp, inner = make_nested_folder(tmp_path)
    before = (inner / MANIFEST).read_bytes()

    with pytest.raises(dp.DirectoryPublicationError) as info:
        dp._resolve_writer_folder(exp)
    assert info.value.reason == "lock-failed"

    # The inner operand still resolves to the real file and its folder.
    assert dp._resolve_writer_folder(exp / MANIFEST) == (
        (inner / MANIFEST).resolve(),
        inner.resolve(),
    )

    code, out = run_cli(["add", str(exp), str(inner / "w1.txt")])
    assert code == 2, out
    assert LOCK_UNAVAILABLE_TEXT not in out
    assert BUSY_TEXT not in out
    # Legacy load refusal: nothing was written beside the directory.
    assert (inner / MANIFEST).read_bytes() == before
    assert (exp / MANIFEST).is_dir()

    code, out = run_cli(["add", str(exp / MANIFEST), str(inner / "w2.txt")])
    assert code == 0, out
    assert "w2.txt" in manifest_paths(inner)
    assert (exp / MANIFEST).is_dir()


def test_nested_directory_symlink_operand_refused_but_inner_operand_writes(
    tmp_path,
):
    exp = tmp_path / "exp"
    exp.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    (target / "base.txt").write_bytes(b"base\n")
    raw = {
        "project": {"id": "proj-target", "name": "Target"},
        "schema_version": "0.1",
        "runs": [],
        "artifacts": [],
        "results": [],
        "evidence": [],
    }
    (target / MANIFEST).write_bytes(dump_raw(raw).encode("utf-8"))
    (exp / MANIFEST).symlink_to(target, target_is_directory=True)
    before = (target / MANIFEST).read_bytes()

    with pytest.raises(dp.DirectoryPublicationError) as info:
        dp._resolve_writer_folder(exp)
    assert info.value.reason == "lock-failed"
    assert dp._resolve_writer_folder(exp / MANIFEST) == (
        (target / MANIFEST).resolve(),
        target.resolve(),
    )

    (target / "w1.txt").write_bytes(b"w1\n")
    code, out = run_cli(["add", str(exp), str(target / "w1.txt")])
    assert code == 2, out
    assert (target / MANIFEST).read_bytes() == before
    assert not (exp / "w1.txt").exists()

    code, out = run_cli(["add", str(exp / MANIFEST), str(target / "w1.txt")])
    assert code == 0, out
    assert "w1.txt" in manifest_paths(target)


def test_deep_directory_chain_is_refused_not_descended(tmp_path):
    exp = tmp_path / "exp"
    exp.mkdir()
    path = exp
    for _ in range(3):
        path = path / MANIFEST
        path.mkdir()

    with pytest.raises(dp.DirectoryPublicationError) as info:
        dp._resolve_writer_folder(exp)
    assert info.value.reason == "lock-failed"

    code, out = run_cli(["rehash", str(exp), "--all"])
    assert code == 2, out
    assert LOCK_UNAVAILABLE_TEXT not in out
    assert (exp / MANIFEST).is_dir()


def test_two_process_nested_layout_outer_refuses_and_inner_commits(tmp_path):
    exp, inner = make_nested_folder(tmp_path)
    barrier = tmp_path / "barrier"
    state = tmp_path / "state"
    barrier.mkdir()
    state.mkdir()

    def start(name, target, file_name):
        args = [
            sys.executable, "-B", "-c", WORKER, str(SRC),
            name, str(target), str(file_name), "-",
            "-", str(barrier), str(state),
        ]
        return subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )

    before = (inner / MANIFEST).read_bytes()
    w1 = start("w1", exp, inner / "w1.txt")
    try:
        w2 = start("w2", exp / MANIFEST, inner / "w2.txt")
        out1, err1 = w1.communicate(timeout=60)
        out2, err2 = w2.communicate(timeout=60)
    finally:
        if w1.poll() is None:
            w1.kill()
            w1.wait(timeout=30)

    codes = {}
    for name, out, err in (("w1", out1, err1), ("w2", out2, err2)):
        line = [ln for ln in out.splitlines() if ln.startswith("CODE ")]
        assert line and err == "", (name, out, err)
        codes[name] = int(line[-1].split()[1])

    # The outer operand cannot load this layout, so it refuses in every
    # interleaving; the inner operand commits. No update is lost.
    assert codes["w1"] == 2, (codes, out1)
    assert codes["w2"] == 0, (codes, out2)
    paths = manifest_paths(inner)
    assert "w1.txt" not in paths
    assert "w2.txt" in paths
    assert (exp / MANIFEST).is_dir()
    if before != (inner / MANIFEST).read_bytes():
        assert "w1.txt" not in paths


# --- post-lock validation cleanup --------------------------------------------


def test_keyboard_interrupt_during_post_lock_validation_releases_fd(tmp_path, monkeypatch):
    exp = make_exp(tmp_path)
    with dp.builtin_writer_admission_lock(exp):
        pass
    baseline = _open_fds()

    real_matches = dp._locked_resolution_matches

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(dp, "_locked_resolution_matches", interrupt)
    with pytest.raises(KeyboardInterrupt):
        with dp.builtin_writer_admission_lock(exp):
            pytest.fail("admission unexpectedly succeeded")

    monkeypatch.setattr(dp, "_locked_resolution_matches", real_matches)
    assert _open_fds() == baseline
    # A later admission in the same process must not see a leaked self-lock.
    with dp.builtin_writer_admission_lock(exp):
        pass
    assert run_cli(["add", str(exp), str(exp / "new.txt")])[0] == 0


# --- legacy fallback: no POSIX directory flock -------------------------------
#
# The admission guarantee is POSIX-flock-only. Where the capability probe says
# the host cannot take a directory flock, the writers must keep the exact
# pre-retrofit unlocked behavior: success, legacy output, no BUSY, no
# LOCK_UNAVAILABLE. This branch must never be reachable on a POSIX host that
# has fcntl.flock (asserted below).


@pytest.mark.parametrize("writer", WRITERS)
def test_no_directory_flock_keeps_legacy_unlocked_success_output(
    tmp_path, monkeypatch, writer
):
    exp = writer_exp(tmp_path / writer, writer)
    before = snapshot(exp)
    argv = writer_argv(exp, writer)

    monkeypatch.setattr(cli, "_directory_flock_available", lambda: False)
    # The fallback must not need the publication module at all.
    with blocked_publication_import(monkeypatch):
        fallback_code, fallback_out = run_cli(argv)

    assert fallback_code == 0, fallback_out
    assert BUSY_TEXT not in fallback_out
    assert LOCK_UNAVAILABLE_TEXT not in fallback_out
    assert snapshot(exp) != before

    # Same folder, restored bytes, real lock: output must be identical.
    restore_snapshot(exp, before)
    monkeypatch.setattr(cli, "_directory_flock_available", lambda: True)
    locked_code, locked_out = run_cli(argv)

    assert locked_code == 0, locked_out
    assert fallback_out == locked_out


@pytest.mark.parametrize("writer", WRITERS)
def test_no_directory_flock_never_reports_busy_or_waits(tmp_path, monkeypatch, writer):
    exp = writer_exp(tmp_path / writer, writer)
    argv = writer_argv(exp, writer)

    holder = start_holder(exp)
    try:
        monkeypatch.setattr(cli, "_directory_flock_available", lambda: False)
        code, out = run_cli(argv)
    finally:
        stop_holder(holder)

    # An unparticipating writer is not blocked by a POSIX lock it does not use.
    assert code == 0, out
    assert BUSY_TEXT not in out
    assert LOCK_UNAVAILABLE_TEXT not in out


def test_capability_probe_is_false_without_fcntl(monkeypatch):
    with blocked_import(monkeypatch, "fcntl"):
        assert cli._directory_flock_available() is False


def test_linux_with_fcntl_never_uses_the_fallback(tmp_path, monkeypatch):
    import fcntl

    assert os.name == "posix" and hasattr(os, "O_DIRECTORY")
    assert hasattr(fcntl, "flock")
    assert cli._directory_flock_available() is True

    exp = make_exp(tmp_path)
    before = snapshot(exp)

    with blocked_publication_import(monkeypatch):
        # The fallback decision does not depend on the publication import:
        # a real flock host stays in the fail-closed branch.
        assert cli._directory_flock_available() is True
        code, out = run_cli(["add", str(exp), str(exp / "new.txt")])

    assert (code, out) == (2, LOCK_UNAVAILABLE_TEXT + "\n")
    assert snapshot(exp) == before


# --- dry runs ----------------------------------------------------------------


def dry_run_argv(exp: Path, writer: str) -> list[str]:
    return {
        "add": ["add", str(exp), str(exp / "new.txt"), "--dry-run"],
        "rehash": ["rehash", str(exp), "--all", "--dry-run"],
        "migrate": ["migrate", str(exp), "--to", "0.2", "--dry-run"],
        "conditions": [
            "conditions", str(exp), "--run", "run-1", "--clear", "environment", "--dry-run"
        ],
    }[writer]


@pytest.mark.parametrize("writer", ("add", "rehash", "migrate", "conditions"))
def test_dry_run_succeeds_under_a_held_writer_lock_without_fsync(
    tmp_path, monkeypatch, writer
):
    exp = writer_exp(tmp_path / writer, writer)
    before = snapshot(exp)

    def no_fsync(fd):
        raise AssertionError("a dry run called fsync")

    monkeypatch.setattr(os, "fsync", no_fsync)
    with hold(exp):
        code, out = run_cli(dry_run_argv(exp, writer))

    assert code == 0, out
    assert "Dry run: nothing was written." in out
    assert snapshot(exp) == before
    assert cells(exp) == []
    assert not any(name.endswith(".tmp") for name in names(exp))


@pytest.mark.parametrize("writer", ("add", "rehash", "migrate", "conditions"))
def test_import_failure_does_not_block_a_dry_run(tmp_path, monkeypatch, writer):
    exp = writer_exp(tmp_path / writer, writer)
    before = snapshot(exp)

    # A dry run never imports or acquires the lock, so it is unaffected by an
    # unavailable admission lock.
    with blocked_publication_import(monkeypatch):
        code, out = run_cli(dry_run_argv(exp, writer))

    assert code == 0, out
    assert "Dry run: nothing was written." in out
    assert snapshot(exp) == before


# --- lock coverage: order of acquisition -------------------------------------


def assert_cannot_take_lock_again(exp: Path) -> None:
    with pytest.raises(cli._WriterBusy):
        with cli._writer_lock(exp):
            pytest.fail("the writer lock was not already held")


def test_init_force_holds_the_lock_across_backup_and_save(tmp_path, monkeypatch):
    exp = make_exp(tmp_path)
    seen: list[str] = []
    real_backup = cli._backup_existing
    real_save = cli.save_manifest

    def backup(out, new):
        assert_cannot_take_lock_again(exp)
        seen.append("backup")
        return real_backup(out, new)

    def save(manifest, path):
        assert_cannot_take_lock_again(exp)
        seen.append("save")
        return real_save(manifest, path)

    monkeypatch.setattr(cli, "_backup_existing", backup)
    monkeypatch.setattr(cli, "save_manifest", save)

    code, out = run_cli(["init", str(exp), "--force"])

    assert code == 0, out
    assert seen == ["backup", "save"]


@pytest.mark.parametrize("writer", ("add", "rehash"))
def test_edit_lock_is_held_before_load_and_through_save(tmp_path, monkeypatch, writer):
    exp = writer_exp(tmp_path / writer, writer)
    seen: list[str] = []
    real_load = cli.load_manifest
    real_save = cli.save_raw

    def load(path):
        assert_cannot_take_lock_again(exp)
        seen.append("load")
        return real_load(path)

    def save(raw, path, *, expected_bytes):
        assert_cannot_take_lock_again(exp)
        seen.append("save")
        return real_save(raw, path, expected_bytes=expected_bytes)

    monkeypatch.setattr(cli, "load_manifest", load)
    monkeypatch.setattr(cli, "save_raw", save)

    code, out = run_cli(writer_argv(exp, writer))

    assert code == 0, out
    assert seen == ["load", "save"]


@pytest.mark.parametrize("writer", ("migrate", "conditions"))
def test_migrate_conditions_lock_before_the_core_runs(tmp_path, monkeypatch, writer):
    exp = writer_exp(tmp_path / writer, writer)
    seen: list[str] = []

    def probe(real):
        def wrapper(*args, **kwargs):
            assert_cannot_take_lock_again(exp)
            seen.append("core")
            return real(*args, **kwargs)

        return wrapper

    if writer == "migrate":
        real = cli.migrate_manifest
        monkeypatch.setattr(cli, "migrate_manifest", probe(real))
    else:
        real = cli.author_conditions
        monkeypatch.setattr(cli, "author_conditions", probe(real))

    code, out = run_cli(writer_argv(exp, writer))

    assert code == 0, out
    assert seen == ["core"]


def test_conditions_busy_refuses_before_reading_the_patch(tmp_path):
    exp = make_exp(tmp_path, version="0.2", conditions=True)
    missing_patch = exp / "no-such-patch.json"

    with hold(exp):
        code, out = run_cli([
            "conditions", str(exp), "--run", "run-1", "--patch", str(missing_patch)
        ])

    assert (code, out) == (2, BUSY_TEXT + "\n")


# --- migrate/conditions busy envelopes ---------------------------------------


def test_migrate_busy_json_envelope_is_closed_and_path_free(tmp_path):
    exp = make_exp(tmp_path)
    before = snapshot(exp)
    canary = "CANARY-BUSY-PATH-" + exp.name

    with hold(exp):
        code, out = run_cli(["migrate", str(exp), "--to", "0.2", "--json"])

    assert code == 2
    assert json.loads(out) == {
        "format": "runevidence-migrate-error-v1",
        "ok": False,
        "exit_code": 2,
        "error": {"code": "BUSY"},
    }
    assert str(exp) not in out
    assert canary not in out
    assert snapshot(exp) == before


def test_conditions_busy_json_envelope_is_closed_and_path_free(tmp_path):
    exp = make_exp(tmp_path, version="0.2", conditions=True)
    before = snapshot(exp)

    with hold(exp):
        code, out = run_cli([
            "conditions", str(exp), "--run", "run-1", "--clear", "environment", "--json"
        ])

    assert code == 2
    assert json.loads(out) == {
        "format": "runevidence-conditions-error-v1",
        "ok": False,
        "exit_code": 2,
        "error": {"code": "BUSY"},
    }
    assert str(exp) not in out
    assert "environment" not in out
    assert snapshot(exp) == before


def test_migrate_and_conditions_busy_text_is_the_same_fixed_line(tmp_path):
    migrate_exp = make_exp(tmp_path / "m")
    conditions_exp = make_exp(tmp_path / "c", version="0.2", conditions=True)

    with hold(migrate_exp):
        assert run_cli(["migrate", str(migrate_exp), "--to", "0.2"]) == (
            2, BUSY_TEXT + "\n"
        )
    with hold(conditions_exp):
        assert run_cli([
            "conditions", str(conditions_exp), "--run", "run-1", "--clear", "environment"
        ]) == (2, BUSY_TEXT + "\n")


def test_busy_text_matches_the_s4_1_core_message():
    assert BUSY_TEXT == str(dp.DirectoryPublicationError("busy"))
    assert LOCK_UNAVAILABLE_TEXT != BUSY_TEXT
    assert "LOCK_UNAVAILABLE" in LOCK_UNAVAILABLE_TEXT


# --- sequential success behavior and resources --------------------------------


def test_uncontended_success_output_is_unchanged(tmp_path):
    exp = make_exp(tmp_path)
    digest = hashlib.sha256(b"new\n").hexdigest()

    code, out = run_cli(["add", str(exp), str(exp / "new.txt")])
    assert (code, out) == (
        0,
        f"  new.txt: new.txt  sha256 {digest[:12]}...\n"
        "Added 1 artifact\n"
        f"Wrote {exp / MANIFEST}\n"
        f"Next: runevidence validate {exp}\n",
    )

    (exp / "notes.txt").write_bytes(b"changed\n")
    new_digest = hashlib.sha256(b"changed\n").hexdigest()
    old_digest = hashlib.sha256(b"notes\n").hexdigest()
    code, out = run_cli(["rehash", str(exp), "--all"])
    assert (code, out) == (
        0,
        f"  notes: sha256 {old_digest[:12]}... -> {new_digest[:12]}..., 6 -> 8 bytes\n"
        "Re-recorded 1 file (1 already up to date)\n"
        f"Wrote {exp / MANIFEST}\n"
        f"Next: runevidence validate {exp}\n",
    )


def _open_fds() -> int:
    return len(os.listdir("/proc/self/fd"))


def test_writers_leak_no_descriptors_locks_or_staging_cells(tmp_path):
    edit = make_exp(tmp_path / "edit")
    init = make_exp(tmp_path / "init")
    migrate = make_exp(tmp_path / "migrate")
    conditions = make_exp(tmp_path / "conditions", version="0.2", conditions=True)

    # Warm up: the first real write imports the S4-1 lock helper.
    assert run_cli(["add", str(edit), str(edit / "new.txt")])[0] == 0
    baseline_fds = _open_fds()

    for _ in range(2):
        assert run_cli(["init", str(init), "--force"])[0] == 0
        assert run_cli(["rehash", str(edit), "--all"])[0] == 0
        assert run_cli(["migrate", str(migrate), "--to", "0.2"])[0] == 0
        assert run_cli([
            "conditions", str(conditions), "--run", "run-1", "--clear", "environment"
        ])[0] == 0
        assert _open_fds() == baseline_fds

    for folder in (edit, init, migrate, conditions):
        assert cells(folder) == []
        # The lock is free in this process after every command.
        with hold(folder):
            pass
        raw = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
        assert all(a.get("artifact_type") != "directory" for a in raw["artifacts"])


def test_read_only_and_out_of_scope_writers_do_not_take_the_lock(tmp_path):
    exp = make_exp(tmp_path)
    before = snapshot(exp)
    schema_out = tmp_path / "schema.json"

    with hold(exp):
        assert run_cli(["validate", str(exp)])[0] == 0
        code, out = run_cli(["schema", "-o", str(schema_out)])

    assert code == 0, out
    assert schema_out.is_file()
    assert json.loads(schema_out.read_text(encoding="utf-8"))["title"]
    assert snapshot(exp) == before


def test_directory_authoring_flag_is_only_on_add_and_rehash():
    # S4-2b authorized the public --directory forms on add/rehash only. No
    # route was added and the top-level help does not advertise subcommand
    # options.
    parser = cli.build_parser()
    sub = next(
        action
        for action in parser._actions
        if action.__class__.__name__ == "_SubParsersAction"
    )
    assert len(sub.choices) == 26
    assert "--directory" not in parser.format_help()
    for route in ("init", "migrate", "conditions", "add", "rehash", "validate", "inspect"):
        options = {
            option
            for action in sub.choices[route]._actions
            for option in action.option_strings
        }
        assert ("--directory" in options) is (route in ("add", "rehash"))
