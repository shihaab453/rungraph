"""A recognized Path.resolve loop error becomes a manifest-unreadable finding.

Python before 3.13 raises RuntimeError("Symlink loop from %r" % e.filename)
from Path.resolve while handling an OSError with errno ELOOP or Windows error
1921 (for example a junction that points at itself). Only that exact shape,
at the resolve call, is contained. Every other RuntimeError propagates.
"""
import errno
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from runevidence.cli import main
from runevidence.findings import Code, Severity
from runevidence.manifest import ManifestLoadError, load_manifest
from runevidence.models import MANIFEST_FILENAME

REPO = Path(__file__).resolve().parents[1]
MARKERS = ("private_parent_ind9", "private_loop_ind9")
CONTENT = b'{"private": "content"}'
PROBES = ("is_dir", "resolve", "is_file", "read_bytes")
REDACTED_FINDING = {
    "code": "manifest-unreadable",
    "severity": "error",
    "message": "Could not read manifest as UTF-8 JSON.",
    "subject": None,
    "path": "<redacted>",
    "hint": None,
}
real_resolve_raises_loop = pytest.mark.skipif(
    sys.version_info >= (3, 13), reason="Path.resolve raises RuntimeError on loops only before 3.13")
windows_only = pytest.mark.skipif(os.name != "nt", reason="winerror is set only on Windows")


@pytest.fixture
def target(tmp_path):
    parent = tmp_path / MARKERS[0]
    parent.mkdir()
    path = parent / f"{MARKERS[1]}.json"
    path.write_bytes(CONTENT)
    return path


def snapshot(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): p.read_bytes()
            for p in folder.rglob("*") if p.is_file()}


def windows_loop_oserror(filename):
    return OSError(None, "The name of the file cannot be resolved by the system", filename,
                   1921)


def eloop_oserror(filename):
    return OSError(errno.ELOOP, "Too many levels of symbolic links", filename)


def cpython_loop(oserror: OSError) -> RuntimeError:
    """Build the error exactly as CPython 3.10-3.12 pathlib check_eloop does."""
    try:
        raise oserror
    except OSError as e:
        try:
            raise RuntimeError("Symlink loop from %r" % e.filename)
        except RuntimeError as loop:
            return loop


def count_probes(monkeypatch, victim_name, raise_at=None, error=None):
    """Count Path probes on the synthetic target; optionally fail one of them."""
    hits = {name: 0 for name in PROBES}

    def wrap(name):
        original = getattr(Path, name)

        def probe(self, *args, **kwargs):
            if self.name == victim_name:
                hits[name] += 1
                if name == raise_at:
                    raise error
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, name, probe)

    for name in PROBES:
        wrap(name)
    return hits


def real_loop_stat(monkeypatch, victim_name, make_error):
    """Fail only the OS-level stat of the target, as a self-referencing
    junction or symlink does, and let the real Path.resolve build its error."""
    original = Path.stat
    seen = []

    def stat(self, *, follow_symlinks=True):
        if self.name == victim_name:
            seen.append(self)
            raise make_error(str(self))
        return original(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", stat)
    return seen


def load_error(target) -> ManifestLoadError:
    with pytest.raises(ManifestLoadError) as info:
        load_manifest(target)
    return info.value


def run(command, target, capsys, *options):
    code = main([command, str(target), *options])
    return code, capsys.readouterr().out


def assert_loop_finding(exc, target, loop):
    assert exc.__cause__ is loop
    [finding] = exc.findings
    assert finding.code is Code.manifest_unreadable
    assert finding.severity is Severity.error
    assert finding.message == f"Could not read manifest as UTF-8 JSON: {loop}"
    assert finding.path == str(target)
    assert finding.hint is None


LOOP_ERRORS = [
    pytest.param(windows_loop_oserror, id="windows-1921", marks=windows_only),
    pytest.param(eloop_oserror, id="eloop"),
]


# The real CPython resolve path, with only the OS stat call faked.

@real_resolve_raises_loop
@pytest.mark.parametrize("make_error", LOOP_ERRORS)
def test_real_resolve_loop_is_contained(make_error, tmp_path, monkeypatch):
    parent = tmp_path / MARKERS[0]
    parent.mkdir()
    (parent / "keep.json").write_bytes(CONTENT)
    loop_target = parent / MARKERS[1]
    seen = real_loop_stat(monkeypatch, MARKERS[1], make_error)
    exc = load_error(loop_target)
    loop = exc.__cause__
    assert type(loop) is RuntimeError
    assert loop.__cause__ is None
    assert isinstance(loop.__context__, OSError)
    assert loop.args == ("Symlink loop from %r" % loop.__context__.filename,)
    assert_loop_finding(exc, loop_target, loop)
    # One stat from is_dir, one from resolve: no extra probes.
    assert len(seen) == 2
    monkeypatch.undo()
    assert snapshot(parent) == {"keep.json": CONTENT}


@real_resolve_raises_loop
@pytest.mark.parametrize("command", ["validate", "inspect"])
@pytest.mark.parametrize("make_error", LOOP_ERRORS)
def test_real_resolve_loop_redacted_cli(command, make_error, tmp_path, capsys, monkeypatch):
    parent = tmp_path / MARKERS[0]
    parent.mkdir()
    loop_target = parent / MARKERS[1]
    real_loop_stat(monkeypatch, MARKERS[1], make_error)
    plain_code, plain_out = run(command, loop_target, capsys, "--json")
    code, out = run(command, loop_target, capsys, "--json", "--redact-manifest-path")
    assert plain_code == code == 2
    assert json.loads(plain_out)["findings"][0]["code"] == "manifest-unreadable"
    assert not any(marker in out for marker in MARKERS)
    report = json.loads(out)
    assert report["manifest"] == "<redacted>"
    assert report["exit_code"] == 2
    assert report["findings"] == [REDACTED_FINDING]


# Realistically chained loop errors raised at the resolve boundary.

@pytest.mark.parametrize("make_error", LOOP_ERRORS)
def test_chained_loop_at_resolve_is_contained(make_error, target, monkeypatch):
    loop = cpython_loop(make_error(str(target)))
    hits = count_probes(monkeypatch, target.name, "resolve", loop)
    exc = load_error(target)
    assert_loop_finding(exc, target, loop)
    assert hits == {"is_dir": 1, "resolve": 1, "is_file": 0, "read_bytes": 0}
    monkeypatch.undo()
    assert snapshot(target.parent) == {target.name: CONTENT}


def test_loop_from_folder_reports_given_path(tmp_path, monkeypatch):
    folder = tmp_path / MARKERS[0]
    folder.mkdir()
    loop = cpython_loop(eloop_oserror(str(folder / MANIFEST_FILENAME)))
    count_probes(monkeypatch, MANIFEST_FILENAME, "resolve", loop)
    exc = load_error(folder)
    assert_loop_finding(exc, folder, loop)
    monkeypatch.undo()
    assert list(folder.iterdir()) == []


@pytest.mark.parametrize("command", ["validate", "inspect"])
@pytest.mark.parametrize("make_error", LOOP_ERRORS)
def test_chained_loop_redacted_cli(command, make_error, target, capsys, monkeypatch):
    loop = cpython_loop(make_error(str(target)))
    count_probes(monkeypatch, target.name, "resolve", loop)
    plain_code, plain_out = run(command, target, capsys, "--json")
    code, out = run(command, target, capsys, "--json", "--redact-manifest-path")
    assert plain_code == code == 2
    assert all(marker in plain_out for marker in MARKERS)
    assert not any(marker in out for marker in MARKERS)
    report = json.loads(out)
    assert report["manifest"] == "<redacted>"
    assert report["exit_code"] == 2
    assert report["findings"] == [REDACTED_FINDING]
    monkeypatch.undo()
    assert target.read_bytes() == CONTENT


@pytest.mark.parametrize("command", ["validate", "inspect"])
def test_chained_loop_default_output(command, target, capsys, monkeypatch):
    loop = cpython_loop(eloop_oserror(str(target)))
    count_probes(monkeypatch, target.name, "resolve", loop)
    code, out = run(command, target, capsys)
    assert code == 2
    assert out == (f"ERROR manifest-unreadable: Could not read manifest as UTF-8 JSON: {loop}\n"
                   "INVALID: manifest could not be checked (exit 2)\n")
    code, out = run(command, target, capsys, "--json")
    report = json.loads(out)
    assert code == report["exit_code"] == 2
    assert report["manifest"] == str(target)
    assert report["findings"] == [{
        "code": "manifest-unreadable",
        "severity": "error",
        "message": f"Could not read manifest as UTF-8 JSON: {loop}",
        "subject": None,
        "path": str(target),
        "hint": None,
    }]


# Misleading or unrelated RuntimeErrors are never converted.

class CustomRuntimeError(RuntimeError):
    pass


def _with_context(error, context):
    try:
        raise context
    except BaseException:
        try:
            raise error
        except BaseException as raised:
            return raised


def _explicit_cause(name):
    os_error = eloop_oserror(name)
    try:
        raise RuntimeError("Symlink loop from %r" % name) from os_error
    except RuntimeError as raised:
        return raised


def _suppressed_context(name):
    try:
        raise eloop_oserror(name)
    except OSError:
        try:
            raise RuntimeError("Symlink loop from %r" % name) from None
        except RuntimeError as raised:
            return raised


MISLEADING = [
    pytest.param(lambda n: RuntimeError("synthetic programming error"), id="unrelated"),
    pytest.param(lambda n: RuntimeError("Symlink loop from %r" % n), id="no-context"),
    pytest.param(lambda n: _with_context(RuntimeError("Symlink loop from %r" % n),
                                         OSError(errno.ENOENT, "missing", n)), id="enoent-context"),
    pytest.param(lambda n: _with_context(RuntimeError("Symlink loop from %r" % n),
                                         PermissionError(errno.EACCES, "denied", n)),
                 id="eacces-context"),
    pytest.param(lambda n: _with_context(RuntimeError("Symlink loop from %r" % n),
                                         OSError(None, "cannot access", n, 1920)),
                 id="winerror-1920-context"),
    pytest.param(lambda n: _with_context(RuntimeError("Symlink loop from %r" % n),
                                         ValueError(errno.ELOOP, "not an OSError", n)),
                 id="non-oserror-context"),
    pytest.param(lambda n: _with_context(RuntimeError("Symlink loop from %r" % (n + "x")),
                                         eloop_oserror(n)), id="filename-mismatch"),
    pytest.param(lambda n: _with_context(RuntimeError("Symlink loop from %r!" % n),
                                         eloop_oserror(n)), id="message-suffix"),
    pytest.param(lambda n: _with_context(RuntimeError("symlink loop from %r" % n),
                                         eloop_oserror(n)), id="message-case"),
    pytest.param(lambda n: _with_context(RuntimeError("Symlink loop from %r" % n, "extra"),
                                         eloop_oserror(n)), id="extra-args"),
    pytest.param(lambda n: _with_context(RuntimeError("synthetic programming error"),
                                         eloop_oserror(n)), id="loop-context-other-message"),
    pytest.param(lambda n: _with_context(CustomRuntimeError("Symlink loop from %r" % n),
                                         eloop_oserror(n)), id="subclass"),
    pytest.param(lambda n: _with_context(RecursionError("Symlink loop from %r" % n),
                                         eloop_oserror(n)), id="recursion-error"),
    pytest.param(lambda n: _with_context(NotImplementedError("Symlink loop from %r" % n),
                                         eloop_oserror(n)), id="not-implemented"),
    pytest.param(_explicit_cause, id="explicit-cause"),
    pytest.param(_suppressed_context, id="suppressed-context"),
]


@pytest.mark.parametrize("make", MISLEADING)
def test_misleading_runtime_error_from_resolve_propagates(make, target, monkeypatch):
    raised = make(str(target))
    hits = count_probes(monkeypatch, target.name, "resolve", raised)
    with pytest.raises(BaseException) as info:
        load_manifest(target)
    assert info.value is raised
    assert hits["is_file"] == hits["read_bytes"] == 0
    monkeypatch.undo()
    assert target.read_bytes() == CONTENT


@pytest.mark.parametrize("command", ["validate", "inspect"])
def test_misleading_runtime_error_propagates_through_cli(command, target, capsys, monkeypatch):
    raised = RuntimeError("Symlink loop from %r" % str(target))
    count_probes(monkeypatch, target.name, "resolve", raised)
    with pytest.raises(RuntimeError) as info:
        main([command, str(target), "--json", "--redact-manifest-path"])
    assert info.value is raised
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("probe", ["is_dir", "is_file", "read_bytes"])
@pytest.mark.parametrize("make_error", LOOP_ERRORS)
def test_loop_shaped_error_outside_resolve_propagates(probe, make_error, target, monkeypatch):
    loop = cpython_loop(make_error(str(target)))
    count_probes(monkeypatch, target.name, probe, loop)
    with pytest.raises(RuntimeError) as info:
        load_manifest(target)
    assert info.value is loop
    monkeypatch.undo()
    assert target.read_bytes() == CONTENT


# Existing OSError handling at every probe is unchanged.

@pytest.mark.parametrize("probe", PROBES)
@pytest.mark.parametrize("make_error", [
    pytest.param(lambda n: PermissionError(errno.EACCES, "synthetic denial", n), id="permission"),
    pytest.param(lambda n: OSError(errno.EIO, "synthetic io", n), id="plain-oserror"),
    pytest.param(eloop_oserror, id="raw-eloop-oserror"),
])
def test_oserror_at_each_probe_stays_unreadable(probe, make_error, target, monkeypatch):
    raised = make_error(str(target))
    hits = count_probes(monkeypatch, target.name, probe, raised)
    exc = load_error(target)
    assert exc.__cause__ is raised
    [finding] = exc.findings
    assert finding.code is Code.manifest_unreadable
    assert finding.message == f"Could not read manifest as UTF-8 JSON: {raised}"
    assert finding.hint is None
    expected_path = target if probe in ("is_dir", "resolve") else target.resolve()
    assert finding.path == str(expected_path)
    assert hits[probe] == 1
    later = PROBES[PROBES.index(probe) + 1:]
    assert all(hits[name] == 0 for name in later)
    monkeypatch.undo()
    assert target.read_bytes() == CONTENT


# Ordinary loads keep their existing results.

def test_missing_manifest_keeps_not_found(tmp_path):
    missing = tmp_path / "absent.json"
    [finding] = load_error(missing).findings
    assert finding.code is Code.manifest_not_found
    assert finding.message == f"No manifest at {missing.resolve()}"
    assert not missing.exists()


def test_directory_without_manifest_keeps_not_found(tmp_path):
    [finding] = load_error(tmp_path).findings
    assert finding.code is Code.manifest_not_found
    assert finding.path == str((tmp_path / MANIFEST_FILENAME).resolve())
    assert list(tmp_path.iterdir()) == []


def test_valid_manifest_loads_with_no_extra_probes(tmp_path, monkeypatch):
    folder = tmp_path / "experiment"
    shutil.copytree(REPO / "examples" / "generic-experiment", folder)
    manifest_file = folder / MANIFEST_FILENAME
    before = snapshot(folder)
    hits = count_probes(monkeypatch, MANIFEST_FILENAME)
    by_folder = load_manifest(folder)
    assert hits == {"is_dir": 0, "resolve": 1, "is_file": 1, "read_bytes": 1}
    by_file = load_manifest(manifest_file)
    assert by_folder.path == by_file.path == manifest_file.resolve()
    assert by_folder.source_bytes == by_file.source_bytes == before[MANIFEST_FILENAME]
    monkeypatch.undo()
    assert snapshot(folder) == before


def test_malformed_json_keeps_unreadable(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"{invalid")
    [finding] = load_error(bad).findings
    assert finding.code is Code.manifest_unreadable
    assert finding.message.startswith("Could not read manifest as UTF-8 JSON: Expecting property name")
    assert bad.read_bytes() == b"{invalid"
