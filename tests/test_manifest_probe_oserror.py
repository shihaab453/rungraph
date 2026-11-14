"""OSError from the preliminary manifest path checks becomes a load finding."""
import errno
import json
import shutil
from pathlib import Path

import pytest

from runevidence.checks import Report
from runevidence.cli import main
from runevidence.findings import Code, Severity
from runevidence.manifest import ManifestLoadError, load_manifest
from runevidence.models import MANIFEST_FILENAME
from runevidence.report import to_json

REPO = Path(__file__).resolve().parents[1]
MARKERS = ("private_parent_872", "private_filename_872")
CONTENT = b'{"private": "content"}'


@pytest.fixture
def target(tmp_path):
    parent = tmp_path / MARKERS[0]
    parent.mkdir()
    path = parent / f"{MARKERS[1]}.json"
    path.write_bytes(CONTENT)
    return path


def inject(monkeypatch, method, victim, error):
    """Make Path.<method> raise only for the synthetic target; count hits."""
    original = getattr(Path, method)
    hits = []

    def probe(self):
        if self == victim:
            hits.append(self)
            raise error
        return original(self)

    monkeypatch.setattr(Path, method, probe)
    return hits


def victim_for(method, target):
    # is_dir sees the argument as given; is_file sees the resolved path.
    return target if method == "is_dir" else target.resolve()


def denial(target, cls=PermissionError, code=errno.EACCES):
    return cls(code, f"synthetic denial {MARKERS[0]} {MARKERS[1]}", str(target))


def load_error(target) -> ManifestLoadError:
    with pytest.raises(ManifestLoadError) as info:
        load_manifest(target)
    return info.value


def run(command, target, capsys, *options):
    code = main([command, str(target), *options])
    return code, capsys.readouterr().out


@pytest.mark.parametrize("method", ["is_dir", "is_file"])
@pytest.mark.parametrize("error", [
    pytest.param(lambda t: denial(t), id="permission"),
    pytest.param(lambda t: denial(t, OSError, errno.EIO), id="plain-oserror"),
    pytest.param(lambda t: denial(t, TimeoutError, errno.ETIMEDOUT), id="timeout"),
])
def test_probe_oserror_becomes_unreadable_finding(method, error, target, monkeypatch):
    raised = error(target)
    hits = inject(monkeypatch, method, victim_for(method, target), raised)
    exc = load_error(target)
    assert hits, "fault must be raised by the real loader path check"
    assert exc.__cause__ is raised
    [finding] = exc.findings
    assert finding.code is Code.manifest_unreadable
    assert finding.severity is Severity.error
    assert finding.message == f"Could not read manifest as UTF-8 JSON: {raised}"
    assert finding.hint is None
    data = json.loads(to_json(Report(manifest_path=target, findings=exc.findings)))
    assert data["findings"][0]["code"] == "manifest-unreadable"
    assert data["counts"]["error"] == 1
    monkeypatch.undo()
    assert target.read_bytes() == CONTENT


def test_is_file_failure_reports_resolved_path(target, monkeypatch):
    inject(monkeypatch, "is_file", target.resolve(), denial(target))
    assert load_error(target).findings[0].path == str(target.resolve())


@pytest.mark.parametrize("method", ["is_dir", "is_file"])
@pytest.mark.parametrize("error", [RuntimeError, TypeError, ValueError, KeyboardInterrupt])
def test_unrelated_exceptions_are_not_converted(method, error, target, monkeypatch):
    inject(monkeypatch, method, victim_for(method, target), error("synthetic programming error"))
    with pytest.raises(error, match="synthetic programming error"):
        load_manifest(target)
    monkeypatch.undo()
    assert target.read_bytes() == CONTENT


def test_missing_manifest_keeps_not_found(tmp_path):
    missing = tmp_path / "absent.json"
    [finding] = load_error(missing).findings
    assert finding.code is Code.manifest_not_found
    assert finding.message == f"No manifest at {missing.resolve()}"
    assert finding.hint == f"Create one with: runevidence init {missing.resolve().parent}"
    assert not missing.exists()


@pytest.mark.parametrize("command", ["validate", "inspect"])
def test_directory_without_manifest_keeps_not_found(command, tmp_path, capsys):
    [finding] = load_error(tmp_path).findings
    assert finding.code is Code.manifest_not_found
    assert finding.path == str((tmp_path / MANIFEST_FILENAME).resolve())
    code, out = run(command, tmp_path, capsys, "--json")
    assert code == 2
    assert json.loads(out)["findings"][0]["code"] == "manifest-not-found"


def test_valid_manifest_loads_from_folder_or_file(tmp_path):
    folder = tmp_path / "experiment"
    shutil.copytree(REPO / "examples" / "generic-experiment", folder)
    manifest_file = folder / MANIFEST_FILENAME
    before = manifest_file.read_bytes()
    by_folder, by_file = load_manifest(folder), load_manifest(manifest_file)
    assert by_folder.path == by_file.path == manifest_file.resolve()
    assert by_folder.source_bytes == by_file.source_bytes == before
    assert manifest_file.read_bytes() == before


def test_malformed_json_keeps_unreadable(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"{invalid")
    [finding] = load_error(bad).findings
    assert finding.code is Code.manifest_unreadable
    assert finding.message.startswith("Could not read manifest as UTF-8 JSON: Expecting property name")
    assert bad.read_bytes() == b"{invalid"


@pytest.mark.parametrize("command", ["validate", "inspect"])
@pytest.mark.parametrize("method", ["is_dir", "is_file"])
def test_redacted_cli_contains_probe_failure(command, method, target, capsys, monkeypatch):
    inject(monkeypatch, method, victim_for(method, target), denial(target))
    plain_code, plain_out = run(command, target, capsys, "--json")
    code, out = run(command, target, capsys, "--json", "--redact-manifest-path")
    assert plain_code == code == 2
    assert all(marker in plain_out for marker in MARKERS)
    report = json.loads(out)
    assert not any(marker in out for marker in MARKERS)
    assert report["manifest"] == "<redacted>"
    assert report["exit_code"] == 2
    assert report["findings"] == [{
        "code": "manifest-unreadable",
        "severity": "error",
        "message": "Could not read manifest as UTF-8 JSON.",
        "subject": None,
        "path": "<redacted>",
        "hint": None,
    }]
    monkeypatch.undo()
    assert target.read_bytes() == CONTENT


@pytest.mark.parametrize("command", ["validate", "inspect"])
@pytest.mark.parametrize("method", ["is_dir", "is_file"])
def test_default_output_for_contained_probe_failure(command, method, target, capsys, monkeypatch):
    raised = denial(target)
    inject(monkeypatch, method, victim_for(method, target), raised)
    code, out = run(command, target, capsys)
    assert code == 2
    assert out == (f"ERROR manifest-unreadable: Could not read manifest as UTF-8 JSON: {raised}\n"
                   "INVALID: manifest could not be checked (exit 2)\n")
    code, out = run(command, target, capsys, "--json")
    report = json.loads(out)
    assert code == report["exit_code"] == 2
    assert report["manifest"] == str(target)
    expected_path = target if method == "is_dir" else target.resolve()
    assert report["findings"] == [{
        "code": "manifest-unreadable",
        "severity": "error",
        "message": f"Could not read manifest as UTF-8 JSON: {raised}",
        "subject": None,
        "path": str(expected_path),
        "hint": None,
    }]


@pytest.mark.parametrize("command", ["validate", "inspect"])
def test_default_output_for_previously_handled_failures(command, tmp_path, capsys):
    missing = tmp_path / "absent.json"
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"{invalid")
    code, out = run(command, missing, capsys)
    resolved = missing.resolve()
    assert code == 2
    assert out == (f"ERROR manifest-not-found: No manifest at {resolved}\n"
                   f"      hint: Create one with: runevidence init {resolved.parent}\n"
                   "INVALID: manifest could not be checked (exit 2)\n")
    code, out = run(command, bad, capsys, "--json")
    assert code == 2
    assert json.loads(out)["findings"] == [{
        "code": "manifest-unreadable",
        "severity": "error",
        "message": "Could not read manifest as UTF-8 JSON: "
                   "Expecting property name enclosed in double quotes: line 1 column 2 (char 1)",
        "subject": None,
        "path": str(bad.resolve()),
        "hint": None,
    }]
    assert bad.read_bytes() == b"{invalid"
