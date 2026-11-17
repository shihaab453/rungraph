"""End-to-end internal opt-in directory checking through run_checks.

Nothing here is reachable from a command: every directory case passes
``enable_directory_artifacts=True`` explicitly. All roots are synthetic.
"""

import hashlib
import json
from pathlib import Path

import pytest

from runevidence.checks import run_checks
from runevidence.checks.files import check_files
from runevidence.checks.results import check_results
from runevidence.directory_hashing import FILE, DIRECTORY, DirectoryDigest, DirectoryRecord
from conftest import native_test
from runevidence.directory_scanner import DirectoryScan, DirectoryScanError
from runevidence.findings import Code, Finding, Severity
from runevidence.manifest import LoadedManifest
from runevidence.models import Artifact, EvidenceReference, Manifest, Project, Result, Run

VECTORS = json.loads(
    (Path(__file__).parent / "data" / "directory_digest_vectors.json").read_bytes().decode("utf-8")
)["vectors"]

native = native_test


def directory(**changes):
    values = dict(id="data", path="data", artifact_type="directory",
                  digest_format="runevidence-directory-v1", sha256="a" * 64, size_bytes=4)
    values.update(changes)
    return Artifact(**values)


def loaded(root, artifacts=None, results=(), evidence=()):
    manifest = Manifest(
        project=Project(id="p", name="p"),
        runs=[Run(id="run")],
        artifacts=list(artifacts if artifacts is not None else [directory()]),
        results=list(results),
        evidence=list(evidence),
    )
    return LoadedManifest(manifest, root / "runevidence.json", b"")


def fake_scan(*records, digest="a" * 64, size=4):
    return DirectoryScan(tuple(records), DirectoryDigest(digest, size, len(records)))


def file_record(path, content=b"x"):
    return DirectoryRecord(FILE, path, hashlib.sha256(content).digest(), len(content))


@pytest.fixture
def scanner(monkeypatch):
    """Replace the native scanner; records every root it is asked to scan."""
    calls = []
    state = {"outcome": fake_scan()}

    def scan(root):
        calls.append(Path(root))
        if isinstance(state["outcome"], Exception):
            raise state["outcome"]
        return state["outcome"]

    monkeypatch.setattr("runevidence.directory_scanner.scan_directory", scan)
    state["calls"] = calls
    return state


def codes(findings):
    return [f.code for f in findings]


# --- root path profile, before any scan -------------------------------------

UNSAFE_ROOTS = [
    "../data", "data/..", "./data", "data/.", "/data", "//server/share/data", "C:/data", "C:data",
    "data\\child", "\\\\server\\share", "data//child", "data/", "data.", "data ", "da*ta", "da?ta",
    "da|ta", "da<ta", 'da"ta', "d\u00e4ta", "da\tta", "con", "CON.txt", "data/aux", "lpt1",
    ".git", "data/.venv", "__pycache__", "runevidence.json", "data/runevidence.json",
]


@pytest.mark.parametrize("path", UNSAFE_ROOTS)
def test_malformed_root_never_invokes_the_scanner(tmp_path, scanner, path):
    result = check_files(loaded(tmp_path, [directory(path=path)]), enable_directory_artifacts=True)
    assert scanner["calls"] == []
    assert codes(result.findings) == [Code.directory_unsafe]
    assert result.findings[0].severity is Severity.error
    assert result.broken == {"data"}
    assert not result.ok_paths and not result.readable_paths


@pytest.mark.parametrize("path", ["/".join(["d"] * 65), "x" * 1025])
def test_root_beyond_bounds_never_invokes_the_scanner(tmp_path, scanner, path):
    result = check_files(loaded(tmp_path, [directory(path=path)]), enable_directory_artifacts=True)
    assert scanner["calls"] == []
    assert codes(result.findings) == [Code.directory_limit_exceeded]
    assert result.broken == {"data"}


@pytest.mark.parametrize("path", ["data", "runs/a b/out-1_v.2", "/".join(["d"] * 64)])
def test_valid_root_is_scanned_under_the_manifest_folder(tmp_path, scanner, path):
    result = check_files(loaded(tmp_path, [directory(path=path)]), enable_directory_artifacts=True)
    assert scanner["calls"] == [tmp_path.absolute().joinpath(*path.split("/"))]
    assert not result.findings and not result.broken
    assert not result.ok_paths and not result.readable_paths


# --- scan outcomes -------------------------------------------------------------

@pytest.mark.parametrize("reason,code", [
    ("missing", Code.missing_directory), ("not-a-directory", Code.not_a_directory),
    ("unsafe", Code.directory_unsafe), ("invalid-root", Code.directory_unsafe),
    ("unreadable", Code.directory_unreadable), ("unstable", Code.directory_unstable),
    ("limit-exceeded", Code.directory_limit_exceeded), ("collision", Code.directory_path_collision),
    ("unsupported-platform", Code.directory_unsafe), ("unsupported-filesystem", Code.directory_unsafe),
])
def test_scan_failures_are_broken(tmp_path, scanner, reason, code):
    scanner["outcome"] = DirectoryScanError(reason, "synthetic failure")
    result = check_files(loaded(tmp_path), enable_directory_artifacts=True)
    assert codes(result.findings) == [code]
    assert result.findings[0].severity is Severity.error and reason in result.findings[0].message
    assert result.broken == {"data"} and not result.ok_paths and not result.readable_paths


@pytest.mark.parametrize("changes,scan,expected,broken", [
    ({}, fake_scan(), [], False),
    ({"size_bytes": None}, fake_scan(), [], False),
    ({}, fake_scan(size=5), [Code.directory_size_mismatch], True),
    ({"size_bytes": None}, fake_scan(digest="b" * 64), [Code.directory_hash_mismatch], True),
    ({}, fake_scan(digest="b" * 64, size=5), [Code.directory_hash_mismatch, Code.directory_size_mismatch], True),
    ({"sha256": None}, fake_scan(), [Code.directory_no_digest], False),
    ({"sha256": None}, fake_scan(size=5), [Code.directory_size_mismatch], True),
])
def test_digest_and_size_outcomes(tmp_path, scanner, changes, scan, expected, broken):
    scanner["outcome"] = scan
    result = check_files(loaded(tmp_path, [directory(**changes)]), enable_directory_artifacts=True)
    assert codes(result.findings) == expected
    assert result.broken == ({"data"} if broken else set())
    assert not result.ok_paths and not result.readable_paths


def test_volatile_directory_is_unverified_and_never_scanned(tmp_path, scanner):
    result = check_files(loaded(tmp_path, [directory(volatile=True)]), enable_directory_artifacts=True)
    assert scanner["calls"] == []
    assert codes(result.findings) == [Code.directory_unsafe]
    assert "unverified" in result.findings[0].message
    assert result.broken == {"data"} and not result.ok_paths and not result.readable_paths


# --- native scanner, synthetic folders -----------------------------------------

def build(root: Path, layout) -> None:
    root.mkdir()
    for entry in layout:
        target = root.joinpath(*entry["path"].split("/"))
        if entry["type"] == "D":
            target.mkdir()
        else:
            target.write_bytes(bytes.fromhex(entry["content_hex"]))


@native
@pytest.mark.parametrize("vector", VECTORS, ids=[v["name"] for v in VECTORS])
def test_native_canonical_vectors_match_end_to_end(tmp_path, vector):
    build(tmp_path / "data", vector["layout"])
    art = directory(sha256=vector["sha256"], size_bytes=vector["size_bytes"])
    report = run_checks(loaded(tmp_path, [art]), enable_directory_artifacts=True)
    assert report.findings == []
    changed = directory(sha256=vector["sha256"][::-1], size_bytes=vector["size_bytes"] + 1)
    report = run_checks(loaded(tmp_path, [changed]), enable_directory_artifacts=True)
    assert [f.code for f in report.findings] == [Code.directory_hash_mismatch, Code.directory_size_mismatch]


@native
def test_native_missing_and_not_a_directory(tmp_path):
    report = run_checks(loaded(tmp_path), enable_directory_artifacts=True)
    assert [f.code for f in report.findings] == [Code.missing_directory]
    (tmp_path / "data").write_bytes(b"not a folder")
    report = run_checks(loaded(tmp_path), enable_directory_artifacts=True)
    assert [f.code for f in report.findings] == [Code.not_a_directory]


@native
def test_native_untracked_coverage(tmp_path):
    build(tmp_path / "data", VECTORS[2]["layout"])
    (tmp_path / "stray.txt").write_bytes(b"s")
    art = directory(sha256=VECTORS[2]["sha256"], size_bytes=VECTORS[2]["size_bytes"])
    report = run_checks(loaded(tmp_path, [art]), include_untracked=True, enable_directory_artifacts=True)
    assert [(f.code, f.path) for f in report.findings] == [(Code.untracked_file, "stray.txt")]


# --- untracked-file coverage -----------------------------------------------------

def untracked(findings):
    return sorted(f.path for f in findings if f.code is Code.untracked_file)


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "data" / "sub").mkdir(parents=True)
    (tmp_path / "data" / "a.txt").write_bytes(b"a")
    (tmp_path / "data" / "sub" / "b.txt").write_bytes(b"b")
    (tmp_path / "data-extra.txt").write_bytes(b"e")
    (tmp_path / "stray.txt").write_bytes(b"s")
    return tmp_path


def test_untracked_coverage_comes_only_from_scan_records(tree, scanner):
    scanner["outcome"] = fake_scan(file_record("a.txt"), DirectoryRecord(DIRECTORY, "sub"), file_record("sub/b.txt"))
    result = check_files(loaded(tree), include_untracked=True, enable_directory_artifacts=True)
    assert untracked(result.findings) == ["data-extra.txt", "stray.txt"]
    # A member the scan did not record stays visible: no raw path prefix.
    scanner["outcome"] = fake_scan(file_record("a.txt"))
    result = check_files(loaded(tree), include_untracked=True, enable_directory_artifacts=True)
    assert untracked(result.findings) == ["data-extra.txt", "data/sub/b.txt", "stray.txt"]


def test_mismatched_but_complete_scan_still_covers_its_records(tree, scanner):
    scanner["outcome"] = fake_scan(file_record("a.txt"), DirectoryRecord(DIRECTORY, "sub"), file_record("sub/b.txt"),
                                   digest="b" * 64)
    result = check_files(loaded(tree), include_untracked=True, enable_directory_artifacts=True)
    assert Code.directory_hash_mismatch in codes(result.findings)
    assert untracked(result.findings) == ["data-extra.txt", "stray.txt"]


@pytest.mark.parametrize("reason", ["unstable", "unsafe", "unreadable", "unsupported-platform"])
def test_failed_scan_covers_nothing(tree, scanner, reason):
    scanner["outcome"] = DirectoryScanError(reason, "synthetic failure")
    result = check_files(loaded(tree), include_untracked=True, enable_directory_artifacts=True)
    assert untracked(result.findings) == ["data-extra.txt", "data/a.txt", "data/sub/b.txt", "stray.txt"]


def test_volatile_or_malformed_directory_covers_nothing(tree, scanner):
    scanner["outcome"] = fake_scan(file_record("a.txt"), DirectoryRecord(DIRECTORY, "sub"), file_record("sub/b.txt"))
    everything = ["data-extra.txt", "data/a.txt", "data/sub/b.txt", "stray.txt"]
    for art in (directory(volatile=True), directory(path="data/./")):
        result = check_files(loaded(tree, [art]), include_untracked=True, enable_directory_artifacts=True)
        assert untracked(result.findings) == everything
    assert scanner["calls"] == []


def test_directory_artifact_naming_a_file_does_not_hide_it(tree, scanner):
    scanner["outcome"] = DirectoryScanError("not-a-directory", "is a file")
    result = check_files(loaded(tree, [directory(path="stray.txt")]), include_untracked=True,
                         enable_directory_artifacts=True)
    assert Code.not_a_directory in codes(result.findings)
    assert "stray.txt" in untracked(result.findings)


def test_child_file_artifact_keeps_legacy_checks(tree, scanner):
    scanner["outcome"] = fake_scan(file_record("a.txt"), DirectoryRecord(DIRECTORY, "sub"), file_record("sub/b.txt"))
    child = Artifact(id="child", path="data/a.txt", sha256="0" * 64)
    result = check_files(loaded(tree, [directory(), child]), include_untracked=True, enable_directory_artifacts=True)
    assert [f.code for f in result.findings if f.subject == "child"] == [Code.hash_mismatch]
    assert result.broken == {"child"}
    assert set(result.readable_paths) == {"child"} and not result.ok_paths
    assert untracked(result.findings) == ["data-extra.txt", "stray.txt"]


# --- results and claims ------------------------------------------------------------

def claim_on(*ids):
    return EvidenceReference(id="claim", claim="it works", supports=list(ids))


def test_opt_in_success_with_directory_backed_result(tmp_path, scanner):
    result = Result(id="acc", run_id="run", name="accuracy", value=0.9, artifact_id="data")
    report = run_checks(loaded(tmp_path, results=[result], evidence=[claim_on("acc")]),
                        enable_directory_artifacts=True)
    assert report.findings == []


def test_result_locator_on_directory_is_rejected_and_puts_claim_at_risk(tmp_path, scanner, monkeypatch):
    reads = []
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: reads.append(self) or "{}")
    result = Result(id="acc", run_id="run", name="accuracy", value=0.9, artifact_id="data", locator="/acc")
    report = run_checks(loaded(tmp_path, results=[result], evidence=[claim_on("acc")]),
                        enable_directory_artifacts=True)
    assert [f.code for f in report.findings] == [Code.directory_locator_unsupported, Code.claim_at_risk]
    locator = report.findings[0]
    assert locator.severity is Severity.error and locator.subject == "acc" and locator.path == "data"
    assert "acc" in report.findings[1].message
    assert reads == []


def test_broken_directory_puts_no_locator_result_claim_at_risk(tmp_path, scanner):
    scanner["outcome"] = fake_scan(digest="b" * 64)
    result = Result(id="acc", run_id="run", name="accuracy", value=0.9, artifact_id="data")
    report = run_checks(loaded(tmp_path, results=[result], evidence=[claim_on("acc")]),
                        enable_directory_artifacts=True)
    assert [f.code for f in report.findings] == [Code.directory_hash_mismatch, Code.claim_at_risk]
    assert "data" in report.findings[1].message


@pytest.mark.parametrize("outcome", [DirectoryScanError("unstable", "changed"), None])
def test_failed_or_volatile_directory_puts_direct_claim_at_risk(tmp_path, scanner, outcome):
    art = directory(volatile=outcome is None)
    if outcome is not None:
        scanner["outcome"] = outcome
    report = run_checks(loaded(tmp_path, [art], evidence=[claim_on("data")]), enable_directory_artifacts=True)
    assert [f.code for f in report.findings][-1] is Code.claim_at_risk
    assert all(f.severity is Severity.error for f in report.findings)


def test_default_check_results_ignores_directory_kind(tmp_path):
    result = Result(id="acc", run_id="run", name="accuracy", value=0.9, artifact_id="data", locator="/acc")
    manifest = loaded(tmp_path, results=[result]).manifest
    assert check_results(manifest, {}) == ([], set())
    findings, mismatched = check_results(manifest, {}, enable_directory_artifacts=True)
    assert [f.code for f in findings] == [Code.directory_locator_unsupported] and mismatched == {"acc"}


# --- defaults stay legacy ------------------------------------------------------------

def test_default_run_checks_is_legacy_for_directory_records(tmp_path, scanner):
    (tmp_path / "data").mkdir()
    result = Result(id="acc", run_id="run", name="accuracy", value=0.9, artifact_id="data", locator="/acc")
    target = loaded(tmp_path, results=[result], evidence=[claim_on("acc")])
    report = run_checks(target)
    assert [f.code for f in report.findings] == [Code.not_a_file, Code.claim_at_risk]
    assert run_checks(target, enable_directory_artifacts=False).findings == report.findings
    assert scanner["calls"] == []


def test_file_only_manifest_is_unchanged_by_the_switch(tmp_path):
    (tmp_path / "metrics.json").write_text('{"acc": 0.8}', encoding="utf-8")
    (tmp_path / "stray.txt").write_bytes(b"s")
    arts = [Artifact(id="metrics", path="metrics.json"), Artifact(id="gone", path="gone.txt")]
    results = [Result(id="acc", run_id="run", name="accuracy", value=0.9, artifact_id="metrics", locator="/acc")]
    target = loaded(tmp_path, arts, results, [claim_on("acc", "gone")])
    default = run_checks(target, include_untracked=True)
    assert run_checks(target, include_untracked=True, enable_directory_artifacts=True).findings == default.findings
    assert {f.code for f in default.findings} == {
        Code.missing_file, Code.no_digest, Code.result_value_mismatch, Code.claim_at_risk, Code.untracked_file,
    }


# --- finding code order ----------------------------------------------------------------

LEGACY_CODES = [
    "manifest-not-found", "manifest-unreadable", "schema-invalid", "missing-file", "not-a-file",
    "hash-mismatch", "size-mismatch", "no-digest", "absolute-path", "non-posix-path", "path-escapes-root",
    "path-case-mismatch", "untracked-file", "result-value-mismatch", "result-unverifiable", "duplicate-id",
    "dangling-reference", "invalid-relationship", "conflicting-provenance", "conflicting-evidence",
    "derivation-cycle", "unsupported-claim", "claim-at-risk",
]
DIRECTORY_CODES = [
    "missing-directory", "not-a-directory", "directory-hash-mismatch", "directory-size-mismatch",
    "directory-no-digest", "directory-unsafe", "directory-unreadable", "directory-unstable",
    "directory-limit-exceeded", "directory-path-collision",
]
CONDITION_CODES = [
    "condition-reference-missing", "condition-reference-wrong-kind",
    "condition-reference-unsupported", "condition-reference-not-consumed",
]


def test_existing_codes_keep_spelling_and_relative_order():
    order = [c.value for c in Code]
    assert [c for c in order if c in LEGACY_CODES] == LEGACY_CODES
    assert [c for c in order if c in DIRECTORY_CODES] == DIRECTORY_CODES
    assert [c for c in order if c in CONDITION_CODES] == CONDITION_CODES
    assert set(order) == (
        set(LEGACY_CODES) | set(DIRECTORY_CODES) | set(CONDITION_CODES)
        | {
            "member-check-failed",
            "directory-locator-unsupported", "locator-verifier-unavailable",
            "locator-kind-mismatch", "locator-mismatch", "locator-unreadable",
            "locator-unsupported-value", "artifact-changed", "locator-no-digest",
            "locator-unverified", "result-unverified", "claim-unverified",
        }
    )


def test_directory_root_causes_sort_before_consequences():
    order = [c.value for c in Code]
    for code in DIRECTORY_CODES:
        assert order.index("untracked-file") < order.index(code) < order.index("result-value-mismatch")
    assert order.index("result-unverifiable") < order.index("directory-locator-unsupported") < order.index("duplicate-id")
    found = [Finding(code=c, severity=Severity.error, message="m") for c in
             (Code.claim_at_risk, Code.directory_locator_unsupported, Code.dangling_reference, Code.directory_unsafe)]
    assert [f.code for f in sorted(found, key=Finding.sort_key)] == [
        Code.directory_unsafe, Code.directory_locator_unsupported, Code.dangling_reference, Code.claim_at_risk,
    ]
