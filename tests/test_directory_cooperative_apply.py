"""Saving a directory proposal under the cooperative writer lock (internal,
development only).

Every manifest and folder is a new synthetic one under tmp_path; no test reads
or writes a real experiment. Test names say what kind of evidence each is:

* ``test_native_*``      the native scanner and real lock files in a synthetic
                         folder; writers are threads or processes of this test
                         that all call the cooperative function
* ``test_injected_*``    a fault or a timing is injected with monkeypatch;
                         these show the wrapper's logic, not OS-level safety
* ``test_limitation_*``  shows a documented limit is real; passing means the
                         limit still exists, not that anything is protected
* the rest               refusals before any scan, and wiring checks

None of these shows protection against a writer that does not take the lock,
an atomic compare-and-swap, or preservation of a custom Windows ACL.
"""

from __future__ import annotations

import ast
import contextvars
import copy
import dataclasses
import hashlib
import json
import os
import subprocess
import sys
import textwrap
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

import runevidence
import runevidence.directory_cooperative_apply as module
from conftest import make_dir_link, native_test, remove_dir_link
from runevidence import directory_scanner
from runevidence.directory_apply import DirectoryApplyError, DirectoryApplyResult, apply_directory_proposal
from runevidence.directory_authoring import propose_add_directory, propose_rehash_directory
from runevidence.directory_cooperative_apply import (
    LOCK_SUFFIX,
    CooperativeApplyError,
    apply_directory_proposal_cooperatively,
    lock_path_for,
)
from runevidence.edit import EditError
from runevidence.manifest import LoadedManifest, dump_raw, load_manifest, load_raw
from runevidence.models import MANIFEST_FILENAME

VECTORS = json.loads(
    (Path(__file__).parent / "data" / "directory_digest_vectors.json").read_bytes().decode("utf-8")
)["vectors"]
NESTED = VECTORS[2]["layout"]  # data/empty/, data/n/, data/n/x.bin

native = native_test
windows = pytest.mark.skipif(os.name != "nt", reason="Windows file-sharing behaviour")

NOTES = b"lab notes\n"
TIMEOUT = 60  # seconds; only to stop a broken test from hanging, never part of the lock


def base_raw(*artifacts):
    return {
        "project": {"name": "Demo", "id": "demo"},
        "schema_version": "0.1",
        "runs": [{"id": "run", "name": "baseline"}],
        "artifacts": [{"path": "notes.txt", "kind": "doc", "id": "notes",
                       "sha256": hashlib.sha256(NOTES).hexdigest(), "size_bytes": len(NOTES)}, *artifacts],
        "results": [],
        "evidence": [],
    }


def build(root: Path, layout) -> None:
    root.mkdir()
    for entry in layout:
        target = root.joinpath(*entry["path"].split("/"))
        if entry["type"] == "D":
            target.mkdir()
        else:
            target.write_bytes(bytes.fromhex(entry["content_hex"]))


def write_manifest(root: Path, raw: dict) -> None:
    (root / MANIFEST_FILENAME).write_text(dump_raw(raw), encoding="utf-8", newline="\n")


def experiment(root: Path, raw=None) -> LoadedManifest:
    """A synthetic experiment: notes.txt, the folders data/ and more/, and a manifest."""
    root.mkdir(exist_ok=True)
    (root / "notes.txt").write_bytes(NOTES)
    build(root / "data", NESTED)
    build(root / "more", VECTORS[1]["layout"])
    write_manifest(root, base_raw() if raw is None else raw)
    return load_manifest(root)


def recorded(root: Path) -> LoadedManifest:
    """As ``experiment``, with data/ already recorded as the last artifact."""
    loaded = experiment(root)
    write_manifest(root, propose_add_directory(loaded, load_raw(loaded), path="data", artifact_id="data").raw)
    return load_manifest(root)


def add(loaded: LoadedManifest, name="data", **kwargs):
    return propose_add_directory(loaded, load_raw(loaded), path=name, artifact_id=name, **kwargs)


def rehash(loaded: LoadedManifest):
    return propose_rehash_directory(loaded, load_raw(loaded), artifact_id="data")


def state(proposal) -> str:
    return json.dumps(dict(vars(proposal)), ensure_ascii=False, default=str)


def snapshot(root: Path):
    """Every path under ``root`` with its bytes and modification time."""
    return sorted(
        (p.relative_to(root).as_posix(), p.read_bytes() if p.is_file() else None, p.lstat().st_mtime_ns)
        for p in root.rglob("*")
    )


def lock_of(root: Path) -> Path:
    return root / (MANIFEST_FILENAME + LOCK_SUFFIX)


def manifest_bytes(root: Path) -> bytes:
    return (root / MANIFEST_FILENAME).read_bytes()


def leftovers(root: Path) -> list[str]:
    """Lock files and save_raw staged copies in the manifest folder."""
    return sorted(p.name for p in root.iterdir() if p.name.startswith(MANIFEST_FILENAME + ".")
                  or p.name.startswith(f".{MANIFEST_FILENAME}."))


WRITERS = [
    "runevidence.directory_apply.save_raw", "runevidence.manifest.save_raw", "runevidence.manifest.save_manifest",
    "runevidence.manifest._write", "tempfile.NamedTemporaryFile", "tempfile.mkstemp", "os.replace", "os.rename",
    "os.remove", "os.unlink", "os.mkdir", "os.makedirs", "os.rmdir", "os.write",
]


@contextmanager
def no_writes(monkeypatch):
    """Every write path, and making a lock, fails loudly and is recorded."""
    used = []

    def forbidden(name):
        def call(*args, **kwargs):
            used.append(name)
            raise AssertionError(f"{name} called")
        return call

    with monkeypatch.context() as m:
        for name in WRITERS:
            m.setattr(name, forbidden(name))
        m.setattr(module._Lock, "create", classmethod(lambda cls, path: forbidden("lock")()))
        yield used


@pytest.fixture
def scans(monkeypatch):
    """The real scanner, with each call's thread recorded."""
    calls = []
    real = directory_scanner.scan_directory

    def scan(root, *args, **kwargs):
        calls.append((threading.get_ident(), Path(root)))
        return real(root, *args, **kwargs)

    monkeypatch.setattr("runevidence.directory_scanner.scan_directory", scan)
    return calls


@pytest.fixture
def applies(monkeypatch):
    """The existing apply, with each call's dry_run recorded."""
    calls = []
    real = module.apply_directory_proposal

    def spy(loaded, proposal, *, dry_run):
        calls.append(dry_run)
        return real(loaded, proposal, dry_run=dry_run)

    monkeypatch.setattr(module, "apply_directory_proposal", spy)
    return calls


@pytest.fixture
def locks(monkeypatch):
    """Every lock this module creates."""
    made = []
    real = module._Lock.create.__func__

    def create(cls, path):
        held = real(cls, path)
        made.append(held)
        return held

    monkeypatch.setattr(module._Lock, "create", classmethod(create))
    return made


def refused(error, reason, loaded, proposal, root, monkeypatch, *, dry_run=False):
    """The call must fail with ``reason``, touching no write path, no file, no
    lock and not the proposal."""
    before, proposal_before = snapshot(root), state(proposal)
    with no_writes(monkeypatch) as used, pytest.raises(error) as info:
        apply_directory_proposal_cooperatively(loaded, proposal, dry_run=dry_run)
    assert info.value.reason == reason, str(info.value)
    assert isinstance(info.value, EditError)
    assert used == [] and snapshot(root) == before and state(proposal) == proposal_before
    return info.value


def expected_after(loaded: LoadedManifest, proposal) -> bytes:
    raw = load_raw(loaded)
    if proposal.operation == "add":
        raw["artifacts"].append(copy.deepcopy(proposal.entry))
    else:
        raw["artifacts"][-1].update(sha256=proposal.new_sha256, size_bytes=proposal.new_size)
    return dump_raw(raw).encode("utf-8")


# --- one writer, native --------------------------------------------------------------


@native
@pytest.mark.parametrize("operation", ["add", "rehash"])
def test_native_changed_proposal_takes_the_lock_once_and_removes_it(tmp_path, applies, locks, operation):
    if operation == "add":
        loaded = experiment(tmp_path)
        proposal = add(loaded, run_id="run", kind="dataset")
    else:
        recorded(tmp_path)
        (tmp_path / "data" / "n" / "x.bin").write_bytes(b"\x00\x01\x02\xff\x10")
        loaded = load_manifest(tmp_path)
        proposal = rehash(loaded)
    assert proposal.changed
    others = [s for s in snapshot(tmp_path) if s[0] != MANIFEST_FILENAME]
    before = state(proposal)
    seen = []
    real = module.apply_directory_proposal

    def look(loaded_, proposal_, *, dry_run):
        seen.append((dry_run, lock_of(tmp_path).exists()))
        return real(loaded_, proposal_, dry_run=dry_run)

    with pytest.MonkeyPatch.context() as m:
        m.setattr(module, "apply_directory_proposal", look)
        result = apply_directory_proposal_cooperatively(loaded, proposal, dry_run=False)

    assert type(result) is DirectoryApplyResult and result.status == "written" and result.manifest_path == loaded.path
    assert seen == [(True, False), (False, True)]  # checked without the lock, saved holding it
    assert applies == [True, False]
    assert len(locks) == 1 and locks[0].path == lock_of(tmp_path)
    assert manifest_bytes(tmp_path) == expected_after(loaded, proposal) == result.text.encode("utf-8")
    assert leftovers(tmp_path) == []  # lock removed, no staged copy left
    assert [s for s in snapshot(tmp_path) if s[0] != MANIFEST_FILENAME] == others
    assert state(proposal) == before


@native
def test_native_lock_holds_a_token_and_is_open_only_during_the_save(tmp_path, monkeypatch, locks):
    loaded = experiment(tmp_path)
    seen = {}
    real = module.apply_directory_proposal

    def look(loaded_, proposal_, *, dry_run):
        if not dry_run:
            seen["content"] = lock_of(tmp_path).read_bytes()
        return real(loaded_, proposal_, dry_run=dry_run)

    monkeypatch.setattr(module, "apply_directory_proposal", look)
    apply_directory_proposal_cooperatively(loaded, add(loaded), dry_run=False)
    lines = seen["content"].decode("ascii").splitlines()
    assert lines[0] == "runevidence cooperative writer lock v1" and lines[2] == f"pid {os.getpid()}"
    assert lines[1].startswith("token ") and len(lines[1]) == len("token ") + 32
    assert seen["content"] == locks[0].content and not lock_of(tmp_path).exists()


# --- nothing written: no lock either --------------------------------------------------


@native
@pytest.mark.parametrize("case", ["add-dry-run", "rehash-dry-run", "unchanged-write", "unchanged-dry-run"])
def test_native_dry_run_and_unchanged_never_touch_a_lock(tmp_path, monkeypatch, applies, case):
    if case == "add-dry-run":
        loaded = experiment(tmp_path)
        proposal = add(loaded)
    else:
        recorded(tmp_path)
        if case == "rehash-dry-run":
            (tmp_path / "data" / "n" / "x.bin").write_bytes(b"changed")
        loaded = load_manifest(tmp_path)
        proposal = rehash(loaded)
    dry_run = case.endswith("dry-run")
    before = snapshot(tmp_path)
    with no_writes(monkeypatch) as used:
        result = apply_directory_proposal_cooperatively(loaded, proposal, dry_run=dry_run)
    assert result.status == ("unchanged" if case.startswith("unchanged") else "dry-run")
    assert result == apply_directory_proposal(loaded, proposal, dry_run=True)
    assert applies == [True]  # always the full check; never a save
    assert used == [] and snapshot(tmp_path) == before and leftovers(tmp_path) == []

    # Still checked like a real save: a stale folder is refused, with no lock.
    (tmp_path / "data" / "late.txt").write_bytes(b"late")
    refused(DirectoryApplyError, "stale-tree", loaded, proposal, tmp_path, monkeypatch, dry_run=dry_run)


@native
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("forgery", ["add-claims-unchanged", "rehash-claims-unchanged", "entry-kind"])
def test_native_forged_or_corrupted_proposal_is_refused_without_a_lock(tmp_path, monkeypatch, dry_run, forgery):
    if forgery == "rehash-claims-unchanged":
        recorded(tmp_path)
        (tmp_path / "data" / "n" / "x.bin").write_bytes(b"changed")
        loaded = load_manifest(tmp_path)
        proposal = rehash(loaded)
        object.__setattr__(proposal, "changed", False)
    else:
        loaded = experiment(tmp_path)
        proposal = add(loaded, kind="dataset")
        if forgery == "add-claims-unchanged":
            object.__setattr__(proposal, "changed", False)
        else:
            proposal.entry["kind"] = proposal.raw["artifacts"][-1]["kind"] = "other"
    refused(DirectoryApplyError, "proposal-mismatch", loaded, proposal, tmp_path, monkeypatch, dry_run=dry_run)


@native
@pytest.mark.parametrize("dry_run", [0, 1, None, "no"])
def test_native_dry_run_must_be_a_bool(tmp_path, monkeypatch, dry_run):
    loaded = experiment(tmp_path)
    refused(DirectoryApplyError, "invalid-request", loaded, add(loaded), tmp_path, monkeypatch, dry_run=dry_run)


@native
def test_native_loaded_must_be_a_loaded_manifest(tmp_path, monkeypatch, scans):
    loaded = experiment(tmp_path)
    proposal = add(loaded)
    scans.clear()
    err = refused(CooperativeApplyError, "invalid-request", None, proposal, tmp_path, monkeypatch)
    assert err.lock_path is None and scans == []


# --- two cooperating writers -------------------------------------------------------------


class Holder:
    """Writer A in a thread: blocks inside the save, holding the lock, until told to go."""

    def __init__(self, monkeypatch, loaded, proposal):
        self.admitted, self.go = threading.Event(), threading.Event()
        self.result = self.error = None
        real = module.apply_directory_proposal
        me = self

        def held(loaded_, proposal_, *, dry_run):
            if not dry_run and threading.current_thread() is me.thread:
                me.admitted.set()
                assert me.go.wait(TIMEOUT)
            return real(loaded_, proposal_, dry_run=dry_run)

        monkeypatch.setattr(module, "apply_directory_proposal", held)
        # ContextVars are per-thread: the worker must run in a copy of the
        # test's context so the private S2a qualification policy applies.
        self.context = contextvars.copy_context()
        self.thread = threading.Thread(target=self._run, args=(loaded, proposal), daemon=True)

    def _run(self, loaded, proposal):
        try:
            self.result = self.context.run(
                apply_directory_proposal_cooperatively, loaded, proposal, dry_run=False
            )
        except BaseException as exc:  # reported by finish()
            self.error = exc

    def start(self):
        self.thread.start()
        assert self.admitted.wait(TIMEOUT), self.error
        return self

    def finish(self):
        self.go.set()
        self.thread.join(TIMEOUT)
        assert not self.thread.is_alive()
        if self.error is not None:
            raise self.error
        return self.result


@native
def test_native_second_cooperating_writer_is_busy_then_stale(tmp_path, monkeypatch, scans):
    loaded = experiment(tmp_path)
    first, second = add(loaded, "data"), add(loaded, "more")
    a = Holder(monkeypatch, loaded, first).start()
    held_lock = lock_of(tmp_path).read_bytes()

    # B, while A holds the lock: busy, before any scan, and nothing written.
    me, already = threading.get_ident(), len(scans)  # the proposals were made by scanning
    with pytest.raises(CooperativeApplyError) as info:
        apply_directory_proposal_cooperatively(loaded, second, dry_run=False)
    assert info.value.reason == "busy" and info.value.lock_path == lock_of(tmp_path) and not info.value.lock_left
    assert [s for s in scans[already:] if s[0] == me] == []
    assert manifest_bytes(tmp_path) == loaded.source_bytes
    assert lock_of(tmp_path).read_bytes() == held_lock  # still A's

    assert a.finish().status == "written"
    after_a = manifest_bytes(tmp_path)
    assert after_a == expected_after(loaded, first) and not lock_of(tmp_path).exists()

    # B again with its proposal from the old bytes: stale, A's save kept.
    with pytest.raises(DirectoryApplyError) as info:
        apply_directory_proposal_cooperatively(loaded, second, dry_run=False)
    assert info.value.reason == "stale-source"
    assert manifest_bytes(tmp_path) == after_a and leftovers(tmp_path) == []

    # Made again from the current manifest, B's change is added after A's.
    reloaded = load_manifest(tmp_path)
    assert apply_directory_proposal_cooperatively(reloaded, add(reloaded, "more"), dry_run=False).written
    ids = [a["id"] for a in json.loads(manifest_bytes(tmp_path))["artifacts"]]
    assert ids == ["notes", "data", "more"] and leftovers(tmp_path) == []


@native
def test_native_writer_that_loses_the_exclusive_create_is_busy(tmp_path, monkeypatch, locks):
    # B passes the first lock check; A takes the lock while B is scanning.
    # B's exclusive create then fails and B gives up without writing.
    loaded = experiment(tmp_path)
    first, second = add(loaded, "data"), add(loaded, "more")
    a = Holder(monkeypatch, loaded, first)
    real = directory_scanner.scan_directory
    me = threading.get_ident()

    def scan(root, *args, **kwargs):
        if threading.get_ident() == me and not a.thread.is_alive() and not a.admitted.is_set():
            a.start()
        return real(root, *args, **kwargs)

    monkeypatch.setattr("runevidence.directory_scanner.scan_directory", scan)
    with pytest.raises(CooperativeApplyError) as info:
        apply_directory_proposal_cooperatively(loaded, second, dry_run=False)
    assert info.value.reason == "busy" and not info.value.lock_left
    assert len(locks) == 1  # only A's
    assert manifest_bytes(tmp_path) == loaded.source_bytes
    assert a.finish().written
    assert manifest_bytes(tmp_path) == expected_after(loaded, first) and leftovers(tmp_path) == []


CHILD = textwrap.dedent("""\
    import json, os, sys
    from pathlib import Path
    import runevidence.directory_cooperative_apply as module
    from runevidence.directory_authoring import propose_add_directory
    from runevidence.directory_scanner import CANDIDATE_FSTYPES, _qualification_for_tests
    from runevidence.manifest import load_manifest, load_raw

    root, name = Path(sys.argv[1]), sys.argv[2]
    real = module.apply_directory_proposal

    def held(loaded, proposal, *, dry_run):
        if not dry_run:
            print("admitted", os.getpid(), flush=True)
            if sys.stdin.readline().strip() != "go":
                raise SystemExit("not told to go")
        return real(loaded, proposal, dry_run=dry_run)

    module.apply_directory_proposal = held
    loaded = load_manifest(root)
    # In-process private seam; the child never inherits the parent ContextVar.
    with _qualification_for_tests(CANDIDATE_FSTYPES):
        proposal = propose_add_directory(loaded, load_raw(loaded), path=name, artifact_id=name)
        result = module.apply_directory_proposal_cooperatively(loaded, proposal, dry_run=False)
    print(json.dumps({"status": result.status, "pid": os.getpid()}), flush=True)
""")


@native
def test_native_cooperating_process_holds_the_lock_against_this_one(tmp_path, scans):
    root = tmp_path / "exp"
    loaded = experiment(root)
    mine = add(loaded, "more")
    script = tmp_path / "child_writer.py"
    script.write_text(CHILD, encoding="utf-8")
    src = Path(runevidence.__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(src), os.environ.get("PYTHONPATH", "")]),
           "PYTHONDONTWRITEBYTECODE": "1"}
    child = subprocess.Popen([sys.executable, "-B", str(script), str(root), "data"], cwd=tmp_path, env=env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    watchdog = threading.Timer(TIMEOUT, child.kill)
    watchdog.start()
    try:
        line = child.stdout.readline().split()
        assert line[:1] == ["admitted"], child.stderr.read()
        child_pid = int(line[1])  # the venv launcher may run the interpreter as a further process
        lock_text = lock_of(root).read_text(encoding="ascii")
        assert f"pid {child_pid}\n" in lock_text and child_pid != os.getpid()

        scans.clear()
        with pytest.raises(CooperativeApplyError) as info:
            apply_directory_proposal_cooperatively(loaded, mine, dry_run=False)
        assert info.value.reason == "busy" and scans == []
        assert manifest_bytes(root) == loaded.source_bytes

        out, err = child.communicate("go\n", timeout=TIMEOUT)
    finally:
        watchdog.cancel()
        if child.poll() is None:
            child.kill()
            child.communicate()
    assert child.returncode == 0, err
    assert json.loads(out) == {"status": "written", "pid": child_pid}
    after_child = manifest_bytes(root)
    assert [a["id"] for a in json.loads(after_child)["artifacts"]] == ["notes", "data"]
    assert leftovers(root) == []

    with pytest.raises(DirectoryApplyError) as info:
        apply_directory_proposal_cooperatively(loaded, mine, dry_run=False)
    assert info.value.reason == "stale-source" and manifest_bytes(root) == after_child


# --- an existing lock is never taken over ---------------------------------------------------


def _stale_file(lock: Path):
    lock.write_bytes(b"runevidence cooperative writer lock v1\ntoken " + b"0" * 32 + b"\npid 1\n")
    return "busy"


def _empty_file(lock: Path):
    lock.write_bytes(b"")
    return "busy"


def _folder(lock: Path):
    lock.mkdir()
    (lock / "inside").write_bytes(b"keep")
    return "invalid-lock-target"


def _junction(lock: Path):
    target = lock.parent.parent / "elsewhere"
    target.mkdir()
    (target / "keep.txt").write_bytes(b"keep")
    if not make_dir_link(lock, target):
        pytest.skip("cannot create a directory link here")
    return "invalid-lock-target"


def _dangling_junction(lock: Path):
    target = lock.parent.parent / "gone"
    target.mkdir()
    if not make_dir_link(lock, target):
        pytest.skip("cannot create a directory link here")
    target.rmdir()
    if os.name == "nt":
        # A dangling junction is a reparse point whose target no longer exists.
        # pathlib's rglob raises in scandir for it before Python 3.13, so the
        # no-write snapshot helper cannot enumerate this folder on 3.10/3.12.
        # The "junction" case still covers the refusal on Windows.
        try:
            snapshot(lock.parent)
        except OSError:
            pytest.skip("dangling junction cannot be enumerated by pathlib on this Python")
    return "invalid-lock-target"


EXISTING = {"stale-token": _stale_file, "empty": _empty_file, "folder": _folder,
            "junction": _junction, "dangling-junction": _dangling_junction}


@native
@pytest.mark.parametrize("name", list(EXISTING))
def test_native_existing_lock_is_never_overwritten_removed_or_waited_for(tmp_path, monkeypatch, scans, name):
    root = tmp_path / "exp"
    loaded = experiment(root)
    proposal = add(loaded)
    reason = EXISTING[name](lock_of(root))
    whole = snapshot(tmp_path)
    scans.clear()

    with no_writes(monkeypatch) as used, pytest.raises(CooperativeApplyError) as info:
        apply_directory_proposal_cooperatively(loaded, proposal, dry_run=False)
    assert info.value.reason == reason and info.value.lock_path == lock_of(root) and not info.value.lock_left
    assert used == [] and scans == [] and snapshot(tmp_path) == whole
    assert os.path.lexists(lock_of(root))

    # A dry run does not take part in locking and leaves the lock alone too.
    assert apply_directory_proposal_cooperatively(loaded, proposal, dry_run=True).status == "dry-run"
    assert snapshot(tmp_path) == whole
    if name.endswith("junction"):
        remove_dir_link(lock_of(root))


# --- the manifest path must name the plain file ----------------------------------------------


def _relative(root: Path, loaded: LoadedManifest):
    return LoadedManifest(loaded.manifest, Path(os.path.relpath(loaded.path)), loaded.source_bytes)


def _dotdot(root: Path, loaded: LoadedManifest):
    return LoadedManifest(loaded.manifest, root / "data" / ".." / MANIFEST_FILENAME, loaded.source_bytes)


def _through_junction(root: Path, loaded: LoadedManifest):
    if not make_dir_link(root.parent / "alias", root):
        pytest.skip("cannot create a directory link here")
    return LoadedManifest(loaded.manifest, root.parent / "alias" / MANIFEST_FILENAME, loaded.source_bytes)


def _other_case(root: Path, loaded: LoadedManifest):
    if os.name != "nt":
        pytest.skip("case-insensitive spelling is a Windows case")
    return LoadedManifest(loaded.manifest, loaded.path.with_name(MANIFEST_FILENAME.upper()), loaded.source_bytes)


def _hard_linked(root: Path, loaded: LoadedManifest):
    os.link(loaded.path, root.parent / "second-name.json")
    return loaded


PATHS = {"relative": _relative, "dotdot": _dotdot, "through-junction": _through_junction,
         "other-case": _other_case, "hard-linked": _hard_linked}


@native
@pytest.mark.parametrize("name", list(PATHS))
def test_native_manifest_path_must_be_the_plain_resolved_file(tmp_path, monkeypatch, scans, name):
    root = tmp_path / "exp"
    loaded = experiment(root)
    proposal = add(loaded)
    monkeypatch.chdir(tmp_path)
    spelled = PATHS[name](root, loaded)
    whole = snapshot(tmp_path)
    scans.clear()
    with no_writes(monkeypatch) as used, pytest.raises(CooperativeApplyError) as info:
        apply_directory_proposal_cooperatively(spelled, proposal, dry_run=False)
    assert info.value.reason == "invalid-lock-target", str(info.value)
    assert used == [] and scans == [] and snapshot(tmp_path) == whole
    if name == "through-junction":
        remove_dir_link(tmp_path / "alias")


@native
def test_native_missing_manifest_is_stale_before_any_lock(tmp_path, monkeypatch, scans):
    loaded = experiment(tmp_path)
    proposal = add(loaded)
    (tmp_path / MANIFEST_FILENAME).unlink()
    scans.clear()
    refused(CooperativeApplyError, "stale-source", loaded, proposal, tmp_path, monkeypatch)
    assert scans == []


# --- the manifest changes ---------------------------------------------------------------------


@native
@pytest.mark.parametrize("change", ["edited", "reformatted"])
def test_native_manifest_changed_before_admission_is_stale(tmp_path, monkeypatch, change):
    loaded = experiment(tmp_path)
    proposal = add(loaded)
    if change == "edited":
        write_manifest(tmp_path, {**base_raw(), "project": {"name": "Edited", "id": "demo"}})
    else:
        (tmp_path / MANIFEST_FILENAME).write_text(json.dumps(base_raw()), encoding="utf-8", newline="\n")
    refused(DirectoryApplyError, "stale-source", loaded, proposal, tmp_path, monkeypatch)


@native
def test_injected_manifest_changed_after_the_check_is_stale_under_the_lock(tmp_path, monkeypatch, locks):
    loaded = experiment(tmp_path)
    proposal = add(loaded)
    other = {**base_raw(), "project": {"name": "Another writer", "id": "demo"}}
    real = module.apply_directory_proposal
    calls = []

    def check_then_other_writer(loaded_, proposal_, *, dry_run):
        calls.append(dry_run)
        out = real(loaded_, proposal_, dry_run=dry_run)
        write_manifest(tmp_path, other)  # after the check, before the lock
        return out

    monkeypatch.setattr(module, "apply_directory_proposal", check_then_other_writer)
    with pytest.raises(CooperativeApplyError) as info:
        apply_directory_proposal_cooperatively(loaded, proposal, dry_run=False)
    assert info.value.reason == "stale-source" and info.value.result is None
    assert calls == [True] and len(locks) == 1  # locked, then refused without calling save
    assert manifest_bytes(tmp_path) == dump_raw(other).encode("utf-8") and leftovers(tmp_path) == []


@native
def test_injected_manifest_changed_during_the_guarded_save_is_refused(tmp_path, monkeypatch, locks):
    loaded = experiment(tmp_path)
    proposal = add(loaded)
    other = {**base_raw(), "project": {"name": "Another writer", "id": "demo"}}
    real = directory_scanner.scan_directory
    count = []

    def scan(root, *args, **kwargs):
        out = real(root, *args, **kwargs)
        count.append(root)
        if len(count) == 2:  # the scan under the lock
            write_manifest(tmp_path, other)
        return out

    monkeypatch.setattr("runevidence.directory_scanner.scan_directory", scan)
    with pytest.raises(DirectoryApplyError) as info:
        apply_directory_proposal_cooperatively(loaded, proposal, dry_run=False)
    assert info.value.reason == "stale-source" and len(locks) == 1
    assert manifest_bytes(tmp_path) == dump_raw(other).encode("utf-8") and leftovers(tmp_path) == []


@native
def test_limitation_writer_ignoring_the_lock_in_the_save_gap_is_overwritten(tmp_path, monkeypatch):
    # save_raw compares bytes, then calls os.replace. A writer that does not
    # take the lock and writes in between is lost: this is not compare-and-swap.
    loaded = experiment(tmp_path)
    proposal = add(loaded)
    other = dump_raw({**base_raw(), "project": {"name": "Lost writer", "id": "demo"}}).encode("utf-8")
    real = os.replace

    def gap_then_replace(src, dst, *args, **kwargs):
        if Path(dst) == loaded.path:
            loaded.path.write_bytes(other)
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr("os.replace", gap_then_replace)
    result = apply_directory_proposal_cooperatively(loaded, proposal, dry_run=False)
    assert result.written
    assert manifest_bytes(tmp_path) == expected_after(loaded, proposal) != other  # the other write is gone
    assert leftovers(tmp_path) == []


# --- faults: no false success, the lock handled explicitly ---------------------------------------


@native
@pytest.mark.parametrize("fault", ["os.replace", "tempfile.NamedTemporaryFile"])
def test_injected_failed_save_releases_the_lock_and_keeps_the_manifest(tmp_path, monkeypatch, locks, fault):
    loaded = experiment(tmp_path)
    proposal = add(loaded)
    before = snapshot(tmp_path)

    def fail(*args, **kwargs):
        raise PermissionError(13, "injected failure")

    with monkeypatch.context() as m, pytest.raises(DirectoryApplyError) as info:
        m.setattr(fault, fail)
        apply_directory_proposal_cooperatively(loaded, proposal, dry_run=False)
    assert info.value.reason == "write-failed" and "injected failure" in str(info.value)
    assert len(locks) == 1 and snapshot(tmp_path) == before and leftovers(tmp_path) == []


@native
def test_injected_scan_crash_under_the_lock_propagates_and_releases(tmp_path, monkeypatch, locks):
    loaded = experiment(tmp_path)
    proposal = add(loaded)
    before = snapshot(tmp_path)
    real = directory_scanner.scan_directory
    count = []

    def scan(root, *args, **kwargs):
        count.append(root)
        if len(count) == 2:
            raise RuntimeError("injected crash")
        return real(root, *args, **kwargs)

    monkeypatch.setattr("runevidence.directory_scanner.scan_directory", scan)
    with pytest.raises(RuntimeError, match="injected crash"):
        apply_directory_proposal_cooperatively(loaded, proposal, dry_run=False)
    assert len(locks) == 1 and snapshot(tmp_path) == before and leftovers(tmp_path) == []


@native
def test_injected_lock_create_failure_is_lock_failed(tmp_path, monkeypatch, applies):
    loaded = experiment(tmp_path)
    proposal = add(loaded)
    before = snapshot(tmp_path)
    real = os.open

    def fail(path, flags, *args, **kwargs):
        if Path(path) == lock_of(tmp_path):
            raise PermissionError(13, "injected failure")
        return real(path, flags, *args, **kwargs)

    monkeypatch.setattr("os.open", fail)
    with pytest.raises(CooperativeApplyError) as info:
        apply_directory_proposal_cooperatively(loaded, proposal, dry_run=False)
    assert info.value.reason == "lock-failed" and not info.value.lock_left and info.value.result is None
    assert applies == [True] and snapshot(tmp_path) == before


@native
def test_injected_lock_write_failure_removes_only_its_own_lock(tmp_path, monkeypatch, applies):
    loaded = experiment(tmp_path)
    proposal = add(loaded)
    before = snapshot(tmp_path)

    real = os.write

    def fail(fd, data):
        if bytes(data).startswith(b"runevidence cooperative writer lock"):
            raise OSError(28, "injected: no space")
        return real(fd, data)

    monkeypatch.setattr("os.write", fail)
    with pytest.raises(CooperativeApplyError) as info:
        apply_directory_proposal_cooperatively(loaded, proposal, dry_run=False)
    assert info.value.reason == "lock-failed" and not info.value.lock_left
    assert applies == [True] and snapshot(tmp_path) == before


def tamper(lock: Path) -> bytes:
    """Overwrite the lock's token in place through a second handle: the same
    file, different contents (a real write, timed by the test)."""
    data = lock.read_bytes()
    with open(lock, "r+b") as fh:
        fh.seek(len(data) - 3)
        fh.write(b"999")
    return lock.read_bytes()


@native
def test_native_lock_changed_before_the_save_is_left_and_nothing_written(tmp_path, monkeypatch, applies):
    loaded = experiment(tmp_path)
    proposal = add(loaded)
    real = module._Lock.create.__func__
    seen = {}

    def create_then_tamper(cls, path):
        held = real(cls, path)
        seen["bytes"] = tamper(path)
        return held

    monkeypatch.setattr(module._Lock, "create", classmethod(create_then_tamper))
    with pytest.raises(CooperativeApplyError) as info:
        apply_directory_proposal_cooperatively(loaded, proposal, dry_run=False)
    assert info.value.reason == "lock-ownership-lost" and info.value.lock_left and info.value.result is None
    assert applies == [True]  # never saved
    assert manifest_bytes(tmp_path) == loaded.source_bytes
    assert lock_of(tmp_path).read_bytes() == seen["bytes"]  # left for a person; not removed


@native
def test_native_lock_changed_during_the_save_is_left_and_the_save_reported(tmp_path, monkeypatch):
    loaded = experiment(tmp_path)
    proposal = add(loaded)
    real = directory_scanner.scan_directory
    count, seen = [], {}

    def scan(root, *args, **kwargs):
        count.append(root)
        if len(count) == 2:
            seen["bytes"] = tamper(lock_of(tmp_path))
        return real(root, *args, **kwargs)

    monkeypatch.setattr("runevidence.directory_scanner.scan_directory", scan)
    with pytest.raises(CooperativeApplyError) as info:
        apply_directory_proposal_cooperatively(loaded, proposal, dry_run=False)
    err = info.value
    assert err.reason == "lock-ownership-lost" and err.lock_left
    assert err.result is not None and err.result.written  # the manifest WAS saved; not a silent success
    assert manifest_bytes(tmp_path) == expected_after(loaded, proposal) == err.result.text.encode("utf-8")
    assert lock_of(tmp_path).read_bytes() == seen["bytes"]


@native
def test_injected_lock_replaced_by_same_token_file_is_not_removed(tmp_path, monkeypatch, locks):
    # After this writer closes its lock and before it checks it, the file is
    # replaced by another one holding the same bytes. Only the identity
    # differs; the other file must not be removed.
    loaded = experiment(tmp_path)
    proposal = add(loaded)
    real_close = os.close
    replaced = {}

    def close_then_replace(fd):
        real_close(fd)
        if locks and fd == locks[0].fd and not replaced:
            spare = tmp_path / "spare"
            spare.write_bytes(locks[0].content)
            os.replace(spare, lock_of(tmp_path))
            replaced["identity"] = (os.lstat(lock_of(tmp_path)).st_dev, os.lstat(lock_of(tmp_path)).st_ino)

    monkeypatch.setattr("os.close", close_then_replace)
    with pytest.raises(CooperativeApplyError) as info:
        apply_directory_proposal_cooperatively(loaded, proposal, dry_run=False)
    assert info.value.reason == "lock-ownership-lost" and info.value.lock_left and info.value.result.written
    assert replaced["identity"] != locks[0].identity
    assert lock_of(tmp_path).read_bytes() == locks[0].content  # the replacement is still there


@native
def test_injected_lock_remove_failure_is_reported_with_the_saved_result(tmp_path, monkeypatch, locks):
    loaded = experiment(tmp_path)
    proposal = add(loaded)
    real = os.unlink

    def fail(path, *args, **kwargs):
        if Path(path) == lock_of(tmp_path):
            raise PermissionError(13, "injected failure")
        return real(path, *args, **kwargs)

    monkeypatch.setattr("os.unlink", fail)
    with pytest.raises(CooperativeApplyError) as info:
        apply_directory_proposal_cooperatively(loaded, proposal, dry_run=False)
    err = info.value
    assert err.reason == "lock-cleanup-failed" and err.lock_left and err.result.written and err.apply_error is None
    assert manifest_bytes(tmp_path) == expected_after(loaded, proposal)
    assert lock_of(tmp_path).read_bytes() == locks[0].content  # residual lock: left for a person


@native
def test_injected_remove_failure_after_a_crash_keeps_the_crash(tmp_path, monkeypatch, locks):
    loaded = experiment(tmp_path)
    proposal = add(loaded)
    real_scan, real_unlink = directory_scanner.scan_directory, os.unlink
    count = []

    def scan(root, *args, **kwargs):
        count.append(root)
        if len(count) == 2:
            raise RuntimeError("injected crash")
        return real_scan(root, *args, **kwargs)

    def fail(path, *args, **kwargs):
        if Path(path) == lock_of(tmp_path):
            raise PermissionError(13, "injected failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr("runevidence.directory_scanner.scan_directory", scan)
    monkeypatch.setattr("os.unlink", fail)
    with pytest.raises(CooperativeApplyError) as info:
        apply_directory_proposal_cooperatively(loaded, proposal, dry_run=False)
    err = info.value
    assert err.reason == "lock-cleanup-failed" and err.lock_left and err.result is None
    assert isinstance(err.apply_error, RuntimeError) and err.__cause__ is err.apply_error
    assert manifest_bytes(tmp_path) == loaded.source_bytes and lock_of(tmp_path).exists()


@native
@pytest.mark.parametrize("change", [{"path": "."}, {"status": "written"}, {"manifest_path": Path("elsewhere")}])
def test_injected_unusable_check_result_is_refused_without_a_lock(tmp_path, monkeypatch, change):
    loaded = experiment(tmp_path)
    proposal = add(loaded)
    real = module.apply_directory_proposal

    def altered(loaded_, proposal_, *, dry_run):
        return dataclasses.replace(real(loaded_, proposal_, dry_run=dry_run), **change)

    monkeypatch.setattr(module, "apply_directory_proposal", altered)
    reason = "invalid-lock-target" if "path" in change else "unexpected-outcome"
    refused(CooperativeApplyError, reason, loaded, proposal, tmp_path, monkeypatch)


@native
@windows
def test_native_open_lock_refuses_ordinary_delete_and_replace_on_windows(tmp_path, monkeypatch):
    # Observed Windows behaviour while the lock is held; not relied on and
    # not protection: the contents can still be overwritten (see tamper).
    loaded = experiment(tmp_path)
    proposal = add(loaded)
    real = module.apply_directory_proposal
    seen = []

    def try_to_remove(loaded_, proposal_, *, dry_run):
        if not dry_run:
            spare = tmp_path / "spare"
            spare.write_bytes(b"x")
            for op in (lambda: os.unlink(lock_of(tmp_path)), lambda: os.replace(spare, lock_of(tmp_path))):
                with pytest.raises(PermissionError):
                    op()
                seen.append(True)
            spare.unlink()
        return real(loaded_, proposal_, dry_run=dry_run)

    monkeypatch.setattr(module, "apply_directory_proposal", try_to_remove)
    assert apply_directory_proposal_cooperatively(loaded, proposal, dry_run=False).written
    assert seen == [True, True] and leftovers(tmp_path) == []


# --- not wired into anything ------------------------------------------------------------------


def test_no_command_or_other_module_uses_the_cooperative_module():
    src = Path(runevidence.__file__).parent
    users = [
        p.relative_to(src).as_posix() for p in src.rglob("*.py")
        if p.name != "directory_cooperative_apply.py" and "directory_cooperative_apply" in p.read_text(encoding="utf-8")
    ]
    assert users == []


def test_public_paths_do_not_import_the_cooperative_module(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    paths = [repo / "src", repo / "packages" / "runevidence-hydra" / "src", repo / "packages" / "runevidence-mlflow" / "src"]
    exp = tmp_path / "exp"
    experiment(exp)
    code = textwrap.dedent(f"""\
        import contextlib, io, sys
        sys.path[:0] = {[str(p) for p in paths]!r}
        import runevidence, runevidence.cli, runevidence.checks, runevidence.edit, runevidence.manifest
        import runevidence.adapters, runevidence_hydra, runevidence_mlflow
        from runevidence.checks import run_checks
        from runevidence.manifest import load_manifest
        run_checks(load_manifest({str(exp)!r}))
        with contextlib.redirect_stdout(io.StringIO()):
            codes = [runevidence.cli.main(["validate", {str(exp)!r}]), runevidence.cli.main(["inspect", {str(exp)!r}])]
        print(codes, "runevidence.directory_cooperative_apply" in sys.modules, "runevidence.directory_apply" in sys.modules)
        assert runevidence.__file__.startswith({str(repo / "src")!r}), runevidence.__file__
    """)
    proc = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, cwd=tmp_path,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=TIMEOUT)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split()[-2:] == ["False", "False"], proc.stdout


def test_cooperative_module_imports_only_what_it_needs():
    source = (Path(runevidence.__file__).parent / "directory_cooperative_apply.py").read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported |= {f"{'.' * node.level}{node.module}.{a.name}" for a in node.names}
    assert imported == {
        "__future__.annotations", "os", "stat", "uuid", "pathlib.Path", "pathlib.PurePosixPath", "typing.Optional",
        ".directory_apply.DirectoryApplyResult", ".directory_apply.apply_directory_proposal",
        ".edit.EditError", ".manifest.LoadedManifest",
    }


def test_lock_path_is_a_sibling_named_after_the_manifest(tmp_path):
    manifest = tmp_path / MANIFEST_FILENAME
    assert lock_path_for(manifest) == tmp_path / f"{MANIFEST_FILENAME}.cooperative-writer.lock"
    assert lock_path_for(tmp_path / "other.json").name == "other.json.cooperative-writer.lock"
