import pytest

from conftest import native_test
from runevidence.checks.files import check_files
from runevidence.directory_hashing import DirectoryDigest
from runevidence.directory_scanner import DirectoryScan, DirectoryScanError
from runevidence.findings import Code
from runevidence.manifest import LoadedManifest
from runevidence.models import Artifact, Manifest, Project


def loaded(root, **changes):
    values = dict(id="data", path="data", artifact_type="directory",
                  digest_format="runevidence-directory-v1", sha256="a" * 64, size_bytes=4)
    values.update(changes)
    return LoadedManifest(Manifest(project=Project(id="p", name="p"), artifacts=[Artifact(**values)]),
                          root / "manifest.json", b"")


def test_default_remains_legacy_and_opt_in_never_exposes_directory_path(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    assert check_files(loaded(tmp_path)).findings[0].code == Code.not_a_file
    monkeypatch.setattr("runevidence.directory_scanner.scan_directory",
                        lambda root: DirectoryScan((), DirectoryDigest("a" * 64, 4, 1)))
    result = check_files(loaded(tmp_path), enable_directory_artifacts=True)
    assert not result.findings and not result.broken
    assert not result.ok_paths and not result.readable_paths


@pytest.mark.parametrize("reason,code", [("missing", Code.missing_directory),
    ("not-a-directory", Code.not_a_directory), ("unsafe", Code.directory_unsafe),
    ("unreadable", Code.directory_unreadable), ("unstable", Code.directory_unstable),
    ("unsupported-platform", Code.directory_unsafe)])
def test_failures_are_broken(tmp_path, monkeypatch, reason, code):
    def fail(root):
        raise DirectoryScanError(reason, "failure")
    monkeypatch.setattr("runevidence.directory_scanner.scan_directory", fail)
    result = check_files(loaded(tmp_path), enable_directory_artifacts=True)
    assert result.findings[0].code == code and result.broken == {"data"}


@pytest.mark.parametrize("path", ["../data", "/data", "C:/data", "data\\child", "data//child", "./data"])
def test_rejects_unsafe_lexical_paths(tmp_path, path):
    result = check_files(loaded(tmp_path, path=path), enable_directory_artifacts=True)
    assert result.findings[0].code == Code.directory_unsafe and "data" in result.broken


@pytest.mark.parametrize("path", ["con", "data.", "data ", ".git", "däta", "da:ta"])
def test_name_profile_rejects_root_before_scanning(tmp_path, monkeypatch, path):
    monkeypatch.setattr("runevidence.directory_scanner.scan_directory", lambda root: pytest.fail("scanned"))
    result = check_files(loaded(tmp_path, path=path), enable_directory_artifacts=True)
    assert [f.code for f in result.findings] == [Code.directory_unsafe] and result.broken == {"data"}


def test_mismatched_size_missing_digest_and_volatile(tmp_path, monkeypatch):
    monkeypatch.setattr("runevidence.directory_scanner.scan_directory",
                        lambda root: DirectoryScan((), DirectoryDigest("a" * 64, 5, 1)))
    result = check_files(loaded(tmp_path), enable_directory_artifacts=True)
    assert result.findings[0].code == Code.directory_size_mismatch and result.broken
    result = check_files(loaded(tmp_path, sha256=None, size_bytes=5), enable_directory_artifacts=True)
    assert result.findings[0].code == Code.directory_no_digest and not result.broken
    result = check_files(loaded(tmp_path, volatile=True), enable_directory_artifacts=True)
    assert result.broken and not result.ok_paths


@native_test
def test_native_directory_scan_when_supported(tmp_path):
    from runevidence.directory_scanner import scan_directory
    root = tmp_path / "data"
    root.mkdir()
    (root / "example.txt").write_bytes(b"hello")
    digest = scan_directory(root).digest
    result = check_files(loaded(tmp_path, sha256=digest.sha256, size_bytes=digest.size_bytes),
                         enable_directory_artifacts=True)
    assert not result.findings and not result.broken and not result.readable_paths
