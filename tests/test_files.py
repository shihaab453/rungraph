"""Local-file checks: existence, digests, path hygiene and untracked files."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from runevidence.checks import run_checks
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
