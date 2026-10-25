"""Local-file checks: existence, digests, path hygiene and untracked files."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

import runevidence.checks.files as file_checks
import runevidence.edit as edit_module
from conftest import is_junction, make_dir_link, remove_dir_link
from runevidence.checks import run_checks
from runevidence.cli import main
from runevidence.findings import Code, Severity
from runevidence.manifest import load_manifest

MANIFEST = "runevidence.json"


def read_doc(root: Path) -> dict:
    return json.loads((root / MANIFEST).read_text(encoding="utf-8"))


def write_doc(root: Path, doc: dict) -> None:
    (root / MANIFEST).write_text(
        json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def check(root: Path, **kwargs):
    return run_checks(load_manifest(root), **kwargs)


def codes(report) -> list[str]:
    return [f.code.value for f in report.findings]


def only(report, code: Code):
    matches = [f for f in report.findings if f.code is code]
    assert len(matches) == 1, f"expected exactly one {code.value}, got {codes(report)}"
    return matches[0]


def test_missing_file_is_an_error(experiment):
    (experiment / "data" / "test.csv").unlink()

    finding = only(check(experiment), Code.missing_file)

    assert finding.severity is Severity.error
    assert finding.subject == "test-data"
    assert "'data/test.csv' does not exist" in finding.message


def test_path_pointing_at_a_directory_is_not_a_file(experiment):
    doc = read_doc(experiment)
    doc["artifacts"][0]["path"] = "data"
    write_doc(experiment, doc)

    finding = only(check(experiment), Code.not_a_file)

    assert finding.severity is Severity.error
    assert finding.subject == "train-data"
    assert "not a regular file" in finding.message
    assert "Directory artifacts are not supported" in finding.hint


def test_hash_mismatch_mentions_size_change_when_size_differs(experiment):
    path = experiment / "data" / "train.csv"
    path.write_bytes(path.read_bytes() + b"extra,rows\n")

    report = check(experiment)
    finding = only(report, Code.hash_mismatch)

    assert finding.severity is Severity.error
    assert finding.subject == "train-data"
    assert "changed since it was recorded" in finding.message
    assert "Size also changed" in finding.message
    assert Code.size_mismatch not in {f.code for f in report.findings}


def test_size_mismatch_is_reported_when_there_is_no_digest(experiment):
    doc = read_doc(experiment)
    doc["artifacts"][0]["sha256"] = None
    doc["artifacts"][0]["size_bytes"] = 999
    write_doc(experiment, doc)

    report = check(experiment)
    finding = only(report, Code.size_mismatch)

    assert finding.severity is Severity.error
    assert finding.subject == "train-data"
    assert "999 recorded" in finding.message
    assert Code.hash_mismatch not in {f.code for f in report.findings}


def test_no_digest_warning_includes_the_actual_digest(experiment):
    target = experiment / "data" / "train.csv"
    doc = read_doc(experiment)
    doc["artifacts"][0]["sha256"] = None
    write_doc(experiment, doc)

    report = check(experiment)
    finding = only(report, Code.no_digest)

    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    assert finding.severity is Severity.warning
    assert finding.subject == "train-data"
    assert digest in finding.hint
    assert codes(report) == ["no-digest"]
    assert report.exit_code() == 0


def test_backslash_path_warns_but_the_file_is_still_found(experiment):
    doc = read_doc(experiment)
    doc["artifacts"][0]["path"] = "data\\train.csv"
    write_doc(experiment, doc)

    report = check(experiment)
    finding = only(report, Code.non_posix_path)

    assert finding.severity is Severity.warning
    assert finding.subject == "train-data"
    assert "data/train.csv" in finding.hint
    assert Code.missing_file not in {f.code for f in report.findings}
    assert codes(report) == ["non-posix-path"]
    assert report.exit_code() == 0


def test_path_escaping_the_root_is_an_error(experiment):
    (experiment.parent / "outside.txt").write_bytes(b"outside the experiment\n")
    doc = read_doc(experiment)
    doc["artifacts"][0]["path"] = "../outside.txt"
    doc["artifacts"][0]["sha256"] = None
    doc["artifacts"][0]["size_bytes"] = None
    write_doc(experiment, doc)

    report = check(experiment)
    finding = only(report, Code.path_escapes_root)

    assert finding.severity is Severity.error
    assert finding.subject == "train-data"
    assert "outside the experiment folder" in finding.message
    assert report.exit_code() == 1


def test_path_case_mismatch_warns_only_on_case_insensitive_filesystems(experiment):
    if not (experiment / "DATA").exists():
        pytest.skip("filesystem is case sensitive, so wrong case cannot resolve")

    doc = read_doc(experiment)
    doc["artifacts"][0]["path"] = "DATA/train.csv"
    write_doc(experiment, doc)

    report = check(experiment)
    finding = only(report, Code.path_case_mismatch)

    assert finding.severity is Severity.warning
    assert finding.subject == "train-data"
    assert "data/train.csv" in finding.hint
    assert codes(report) == ["path-case-mismatch"]
    assert report.exit_code() == 0


def test_missing_file_hint_names_the_same_file_elsewhere(experiment):
    source = experiment / "data" / "train.csv"
    moved = experiment / "kept" / "train.csv"
    moved.parent.mkdir()
    source.replace(moved)

    finding = only(check(experiment), Code.missing_file)

    assert finding.subject == "train-data"
    assert finding.hint is not None
    assert "kept/train.csv" in finding.hint


def test_untracked_files_are_reported_only_when_requested(experiment):
    (experiment / "scratch.txt").write_text("scratch\n", encoding="utf-8")

    assert check(experiment).findings == []

    report = check(experiment, include_untracked=True)
    finding = only(report, Code.untracked_file)
    assert finding.severity is Severity.info
    assert finding.path == "scratch.txt"


def test_info_findings_never_change_the_exit_code(experiment):
    (experiment / "scratch.txt").write_text("scratch\n", encoding="utf-8")

    report = check(experiment, include_untracked=True)

    assert report.errors == 0
    assert report.warnings == 0
    assert report.exit_code() == 0
    assert report.exit_code(strict=True) == 0


def _forbid_reading(outside: Path, monkeypatch) -> None:
    """Fail if any check reads bytes at or under ``outside``."""
    outside_real = os.path.normcase(os.path.realpath(outside))

    def is_outside(path: object) -> bool:
        real = os.path.normcase(os.path.realpath(str(path)))
        return real == outside_real or real.startswith(outside_real + os.sep)

    real_hash = file_checks.sha256_file

    def hashing(path):
        assert not is_outside(path), f"hashed an outside file: {path}"
        return real_hash(path)

    real_read_text, real_read_bytes = Path.read_text, Path.read_bytes

    def read_text(self, *args, **kwargs):
        assert not is_outside(self), f"read an outside file: {self}"
        return real_read_text(self, *args, **kwargs)

    def read_bytes(self, *args, **kwargs):
        assert not is_outside(self), f"read an outside file: {self}"
        return real_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(file_checks, "sha256_file", hashing)
    monkeypatch.setattr(edit_module, "sha256_file", hashing)
    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)


def test_artifact_through_linked_directory_outside_is_rejected_without_reads(
    experiment, tmp_path, monkeypatch
):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.json").write_text('{"eval": {"accuracy": 1.0}}\n', encoding="utf-8")
    link = experiment / "runs" / "escaped"
    if not make_dir_link(link, outside):
        pytest.skip("this platform cannot create a directory link")
    try:
        if os.name == "nt":
            # A real junction, created without symlink privileges.
            assert is_junction(link)
        doc = read_doc(experiment)
        entry = next(a for a in doc["artifacts"] if a["id"] == "tuned-metrics")
        entry["path"] = "runs/escaped/secret.json"
        entry["sha256"] = None
        entry["size_bytes"] = None
        write_doc(experiment, doc)
        _forbid_reading(outside, monkeypatch)

        report = check(experiment)
        finding = only(report, Code.path_escapes_root)

        assert finding.severity is Severity.error
        assert finding.subject == "tuned-metrics"
        assert "outside the experiment folder" in finding.message
        # The rejected artifact is broken, so claims resting on it are at risk.
        assert "path-escapes-root" in codes(report)
        assert "claim-at-risk" in codes(report)
        assert report.exit_code() == 1
    finally:
        remove_dir_link(link)


def test_absolute_artifact_outside_the_root_is_rejected_without_reads(
    experiment, tmp_path, monkeypatch
):
    outside = tmp_path / "outside.json"
    outside.write_text('{"eval": {"accuracy": 1.0}}\n', encoding="utf-8")
    doc = read_doc(experiment)
    entry = next(a for a in doc["artifacts"] if a["id"] == "tuned-metrics")
    entry["path"] = str(outside)
    entry["sha256"] = None
    entry["size_bytes"] = None
    write_doc(experiment, doc)
    _forbid_reading(outside, monkeypatch)

    report = check(experiment)
    finding = only(report, Code.path_escapes_root)

    assert finding.severity is Severity.error
    assert finding.subject == "tuned-metrics"
    assert "Absolute path resolves outside" in finding.message
    assert Code.absolute_path in {f.code for f in report.findings}
    assert "result-value-mismatch" not in codes(report)
    assert "result-unverifiable" not in codes(report)
    assert report.exit_code() == 1


def test_physical_resolution_error_fails_closed(experiment, monkeypatch):
    loaded = load_manifest(experiment)
    target = experiment / "data" / "train.csv"
    target_key = os.path.normcase(os.path.abspath(target))
    real_resolve = Path.resolve

    def flaky_resolve(self, *args, **kwargs):
        if os.path.normcase(os.path.abspath(self)) == target_key:
            raise OSError("injected resolution failure")
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", flaky_resolve)
    report = run_checks(loaded)
    finding = only(report, Code.path_escapes_root)

    assert finding.severity is Severity.error
    assert finding.subject == "train-data"
    assert "outside the experiment folder" in finding.message
    # An unresolved path must not be accepted through its lexical spelling.
    assert "missing-file" not in codes(report)
    assert report.exit_code() == 1


def test_linked_directory_inside_the_root_still_validates(experiment):
    link = experiment / "alias-data"
    if not make_dir_link(link, experiment / "data"):
        pytest.skip("this platform cannot create a directory link")
    try:
        doc = read_doc(experiment)
        next(a for a in doc["artifacts"] if a["id"] == "train-data")["path"] = "alias-data/train.csv"
        write_doc(experiment, doc)

        assert check(experiment).findings == []
    finally:
        remove_dir_link(link)


def test_link_to_a_file_inside_the_root_still_validates(experiment):
    link = experiment / "alias-report.md"
    try:
        os.symlink(experiment / "REPORT.md", link)
    except OSError:
        pytest.skip("this platform cannot create file symlinks without extra privileges")
    doc = read_doc(experiment)
    next(a for a in doc["artifacts"] if a["id"] == "report")["path"] = "alias-report.md"
    write_doc(experiment, doc)

    assert check(experiment).findings == []


def test_untracked_scan_and_hints_do_not_enter_an_outside_link(experiment, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"outside bytes\n")
    link = experiment / "linked"
    if not make_dir_link(link, outside):
        pytest.skip("this platform cannot create a directory link")
    try:
        report = check(experiment, include_untracked=True)

        assert report.findings == []
        assert not any(f.path and f.path.startswith("linked/") for f in report.findings)
    finally:
        remove_dir_link(link)


@pytest.mark.skipif(os.name != "posix", reason="file symlinks need privileges on Windows")
def test_dangling_symlink_is_reported_without_raising(experiment):
    os.symlink(experiment / "no-such-target.txt", experiment / "dangling.txt")
    doc = read_doc(experiment)
    entry = next(a for a in doc["artifacts"] if a["id"] == "train-data")
    entry["path"] = "dangling.txt"
    entry["sha256"] = None
    entry["size_bytes"] = None
    write_doc(experiment, doc)

    finding = only(check(experiment), Code.missing_file)

    assert finding.subject == "train-data"
    assert "does not exist" in finding.message


def test_nested_link_escape_is_rejected_without_reads(experiment, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.json").write_text('{"eval": {"accuracy": 1.0}}\n', encoding="utf-8")
    nested = experiment / "nested"
    nested.mkdir()
    link = nested / "escape"
    if not make_dir_link(link, outside):
        pytest.skip("this platform cannot create a directory link")
    try:
        doc = read_doc(experiment)
        entry = next(a for a in doc["artifacts"] if a["id"] == "tuned-metrics")
        entry["path"] = "nested/escape/secret.json"
        entry["sha256"] = None
        entry["size_bytes"] = None
        write_doc(experiment, doc)
        _forbid_reading(outside, monkeypatch)

        report = check(experiment)
        finding = only(report, Code.path_escapes_root)

        assert finding.subject == "tuned-metrics"
        assert "path-escapes-root" in codes(report)
        assert "claim-at-risk" in codes(report)

        untracked = check(experiment, include_untracked=True)
        assert not any(
            f.code is Code.untracked_file and f.path and f.path.startswith("nested/escape/")
            for f in untracked.findings
        )
    finally:
        remove_dir_link(link)


def test_folder_add_prunes_an_outside_link_without_reads(experiment, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"outside bytes\n")
    folder = experiment / "newdir"
    folder.mkdir()
    (folder / "safe.txt").write_bytes(b"safe\n")
    link = folder / "escape"
    if not make_dir_link(link, outside):
        pytest.skip("this platform cannot create a directory link")
    try:
        _forbid_reading(outside, monkeypatch)
        monkeypatch.chdir(experiment)

        assert main(["add", ".", "newdir"]) == 0
        paths = {a["path"] for a in read_doc(experiment)["artifacts"]}
        assert "newdir/safe.txt" in paths
        assert not any(p.startswith("newdir/escape/") for p in paths)
    finally:
        remove_dir_link(link)


def test_folder_add_follows_an_in_root_link_and_stops_cycles(experiment, monkeypatch):
    folder = experiment / "newdir"
    (folder / "real").mkdir(parents=True)
    (folder / "real" / "safe.txt").write_bytes(b"safe\n")
    link = folder / "alias"
    if not make_dir_link(link, folder / "real"):
        pytest.skip("this platform cannot create a directory link")
    cycle = folder / "real" / "loop"
    try:
        if not make_dir_link(cycle, folder):
            pytest.skip("this platform cannot create a directory link")
        monkeypatch.chdir(experiment)

        assert main(["add", ".", "newdir"]) == 0
        paths = {a["path"] for a in read_doc(experiment)["artifacts"]}

        assert "newdir/real/safe.txt" in paths
        assert "newdir/alias/safe.txt" in paths
    finally:
        if cycle.exists():
            remove_dir_link(cycle)
        remove_dir_link(link)


def test_rehash_refuses_an_outside_artifact_without_reads(experiment, tmp_path, monkeypatch):
    outside = tmp_path / "outside.json"
    outside.write_text('{"eval": {"accuracy": 1.0}}\n', encoding="utf-8")
    doc = read_doc(experiment)
    next(a for a in doc["artifacts"] if a["id"] == "tuned-metrics")["path"] = str(outside)
    write_doc(experiment, doc)
    _forbid_reading(outside, monkeypatch)

    assert main(["rehash", str(experiment), "tuned-metrics"]) == 2
