"""Public read-only ``--directory-artifacts`` flag on validate and inspect.

Every manifest is synthetic and written under ``tmp_path``. Scanner failures
are injected with monkeypatch where a real host filesystem would make the
case hard to produce; the real ext4 and tmpfs cases skip on hosts where the
release policy refuses or qualifies the root differently.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from runevidence.cli import main
from runevidence.directory_scanner import DirectoryScanError, scan_directory

MANIFEST = "runevidence.json"
FINDING_FIELDS = {"code", "severity", "message", "subject", "path", "hint"}
REPORT_FIELDS = {"manifest", "ok", "exit_code", "counts", "findings"}

# Distinctive values that must never reach public output.
SECRET_ROOT = "/home/private-scan-root-8a1f"
SECRET_MEMBER = "member_secret_9f3c.txt"
SECRET_STRERROR = "[Errno 13] Permission denied"
SECRET_IDS = "inode 424242 device 777:3 mount id 36"
SECRET_DIGEST = "9f3c" * 16
CANARIES = (SECRET_ROOT, SECRET_MEMBER, SECRET_STRERROR, SECRET_IDS, SECRET_DIGEST)


def write_directory_manifest(
    root: Path,
    *,
    sha256: str | None = "a" * 64,
    size_bytes: int | None = 4,
    path: str = "data",
    artifact_id: str = "data",
    volatile: bool = False,
) -> Path:
    artifact = {
        "id": artifact_id,
        "path": path,
        "artifact_type": "directory",
        "digest_format": "runevidence-directory-v1",
        "sha256": sha256,
        "size_bytes": size_bytes,
    }
    if volatile:
        artifact["volatile"] = True
    document = {
        "schema_version": "0.1",
        "project": {"id": "p", "name": "p"},
        "runs": [{"id": "run"}],
        "artifacts": [artifact],
    }
    manifest = root / MANIFEST
    manifest.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest


def build_tree(root: Path) -> Path:
    data = root / "data"
    (data / "sub").mkdir(parents=True)
    (data / "a.txt").write_bytes(b"a")
    (data / "sub" / "b.txt").write_bytes(b"b")
    return data


@pytest.fixture
def fake_scanner(monkeypatch):
    state: dict[str, object] = {}

    def scan(root):
        state["root"] = Path(root)
        outcome = state.get("outcome", DirectoryScanError("missing", "not there"))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr("runevidence.directory_scanner.scan_directory", scan)
    return state


def json_doc(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def assert_no_canary(text: str) -> None:
    for canary in CANARIES:
        assert canary not in text


# --- default behavior is unchanged --------------------------------------------


@pytest.mark.parametrize("command", ["validate", "inspect"])
def test_directory_is_not_a_file_without_the_flag(command, tmp_path, capsys):
    build_tree(tmp_path)
    write_directory_manifest(tmp_path)

    assert main([command, str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "not-a-file" in out
    assert "--directory-artifacts" not in out

    assert main([command, str(tmp_path), "--json"]) == 1
    data = json_doc(capsys)
    codes = [f["code"] for f in data["findings"]]
    assert "not-a-file" in codes
    assert not [code for code in codes if code.startswith("directory-")]
    expected = REPORT_FIELDS | ({"summary"} if command == "inspect" else set())
    assert set(data) == expected


def test_file_only_manifest_is_byte_identical_with_or_without_flag(tmp_path, capsys):
    (tmp_path / "metrics.json").write_bytes(b'{"acc": 0.8}\n')
    (tmp_path / MANIFEST).write_text(json.dumps({
        "schema_version": "0.1",
        "project": {"id": "p", "name": "p"},
        "runs": [{"id": "run"}],
        "artifacts": [{"id": "metrics", "path": "metrics.json",
                       "sha256": "0" * 64, "size_bytes": 1}],
    }, indent=2) + "\n", encoding="utf-8", newline="\n")

    for command in ("validate", "inspect"):
        default = main([command, str(tmp_path), "--json"])
        default_out = capsys.readouterr().out
        flagged = main([command, str(tmp_path), "--json", "--directory-artifacts"])
        flagged_out = capsys.readouterr().out
        assert default == flagged
        assert default_out == flagged_out


# --- match, mismatch, incomplete ------------------------------------------------


def test_matching_directory_validates_with_the_flag(tmp_path, fake_scanner, capsys):
    from runevidence.directory_hashing import FILE, DIRECTORY, DirectoryDigest, DirectoryRecord
    from runevidence.directory_scanner import DirectoryScan

    write_directory_manifest(tmp_path)
    fake_scanner["outcome"] = DirectoryScan(
        (DirectoryRecord(FILE, "a.txt", b"\x00" * 32, 1),
         DirectoryRecord(DIRECTORY, "sub")),
        DirectoryDigest("a" * 64, 4, 2),
    )

    for command in ("validate", "inspect"):
        assert main([command, str(tmp_path), "--directory-artifacts"]) == 0
        out = capsys.readouterr().out
        assert "directory-" not in out
        assert fake_scanner["root"] == tmp_path.absolute() / "data"


def test_digest_and_size_mismatch_are_existing_codes(tmp_path, fake_scanner, capsys):
    from runevidence.directory_hashing import DirectoryDigest
    from runevidence.directory_scanner import DirectoryScan

    write_directory_manifest(tmp_path)
    fake_scanner["outcome"] = DirectoryScan((), DirectoryDigest("b" * 64, 5, 0))

    assert main(["validate", str(tmp_path), "--directory-artifacts", "--json"]) == 1
    data = json_doc(capsys)
    assert [f["code"] for f in data["findings"]] == [
        "directory-hash-mismatch", "directory-size-mismatch",
    ]
    assert [f["severity"] for f in data["findings"]] == ["error", "error"]
    assert all(set(f) == FINDING_FIELDS for f in data["findings"])
    assert not any("b" * 64 in f["message"] for f in data["findings"])


def test_incomplete_directory_warns_and_strict_fails(tmp_path, fake_scanner, capsys):
    from runevidence.directory_hashing import DirectoryDigest
    from runevidence.directory_scanner import DirectoryScan

    write_directory_manifest(tmp_path, sha256=None)
    fake_scanner["outcome"] = DirectoryScan((), DirectoryDigest("a" * 64, 4, 0))

    assert main(["validate", str(tmp_path), "--directory-artifacts"]) == 0
    out = capsys.readouterr().out
    assert "directory-no-digest" in out

    assert main(["validate", str(tmp_path), "--directory-artifacts", "--strict"]) == 1
    capsys.readouterr()

    assert main(["inspect", str(tmp_path), "--directory-artifacts", "--json"]) == 0
    assert [f["code"] for f in json_doc(capsys)["findings"]] == ["directory-no-digest"]


# --- scan errors, sanitized -----------------------------------------------------


def _leaky_error(reason: str) -> DirectoryScanError:
    return DirectoryScanError(
        reason,
        f"scan of {SECRET_ROOT} failed at {SECRET_MEMBER}: {SECRET_STRERROR}; "
        f"{SECRET_IDS}; digest {SECRET_DIGEST}",
        SECRET_MEMBER,
    )


@pytest.mark.parametrize("reason,code", [
    ("missing", "missing-directory"),
    ("not-a-directory", "not-a-directory"),
    ("unsafe", "directory-unsafe"),
    ("invalid-root", "directory-unsafe"),
    ("unreadable", "directory-unreadable"),
    ("unstable", "directory-unstable"),
    ("limit-exceeded", "directory-limit-exceeded"),
    ("collision", "directory-path-collision"),
    ("unsupported-platform", "directory-unsafe"),
    ("unsupported-filesystem", "directory-unsafe"),
])
def test_scan_error_classes_are_sanitized_in_text_and_json(
    reason, code, tmp_path, fake_scanner, capsys
):
    write_directory_manifest(tmp_path)
    fake_scanner["outcome"] = _leaky_error(reason)

    for command in ("validate", "inspect"):
        assert main([command, str(tmp_path), "--directory-artifacts"]) == 1
        out = capsys.readouterr().out
        assert code in out
        assert "data" in out
        assert_no_canary(out)

        assert main([command, str(tmp_path), "--directory-artifacts", "--json"]) == 1
        raw = capsys.readouterr().out
        assert json.loads(raw)["exit_code"] == 1
        assert [f["code"] for f in json.loads(raw)["findings"]] == [code]
        assert_no_canary(raw)


def test_malformed_recorded_path_is_sanitized(tmp_path, capsys):
    # The recorded relative artifact path may be echoed, but the detailed
    # encoder message (which names internal member pieces) may not.
    write_directory_manifest(tmp_path, path="data/.venv")

    assert main(["validate", str(tmp_path), "--directory-artifacts", "--json"]) == 1
    raw = capsys.readouterr().out
    data = json.loads(raw)
    assert [f["code"] for f in data["findings"]] == ["directory-unsafe"]
    assert "protected-name" in data["findings"][0]["message"]
    assert "names protected content" not in raw
    assert_no_canary(raw)


def test_volatile_directory_is_never_scanned(tmp_path, fake_scanner, capsys):
    write_directory_manifest(tmp_path, volatile=True)

    assert main(["validate", str(tmp_path), "--directory-artifacts"]) == 1
    out = capsys.readouterr().out
    assert "directory-unsafe" in out
    assert "unverified" in out
    assert fake_scanner.get("root") is None


# --- inspect untracked coverage -------------------------------------------------


def test_inspect_untracked_coverage_with_and_without_the_flag(tmp_path, fake_scanner, capsys):
    from runevidence.directory_hashing import (
        DIRECTORY,
        FILE,
        DirectoryDigest,
        DirectoryRecord,
    )
    from runevidence.directory_scanner import DirectoryScan

    build_tree(tmp_path)
    write_directory_manifest(tmp_path, sha256="a" * 64, size_bytes=2)

    def untracked(document):
        return sorted(
            f["path"] for f in document["findings"] if f["code"] == "untracked-file"
        )

    # Without the flag the directory record is not a file, so it covers nothing.
    assert main(["inspect", str(tmp_path), "--json"]) == 1
    no_flag = json_doc(capsys)
    assert "not-a-file" in [f["code"] for f in no_flag["findings"]]
    expected = ["data/a.txt", "data/sub/b.txt"]
    assert untracked(no_flag) == expected

    # With the flag but a failed scan, coverage is still empty, so the same
    # pre-existing untracked findings name the same relative member paths.
    fake_scanner["outcome"] = _leaky_error("unreadable")
    assert main(["inspect", str(tmp_path), "--directory-artifacts", "--json"]) == 1
    failed = json_doc(capsys)
    assert "directory-unreadable" in [f["code"] for f in failed["findings"]]
    assert untracked(failed) == expected
    for finding in failed["findings"]:
        if finding["code"] == "untracked-file":
            assert not Path(finding["path"]).is_absolute()
            assert str(tmp_path) not in finding["path"]
            assert str(tmp_path) not in finding["message"]

    # A successful scan covers exactly the files it recorded.
    fake_scanner["outcome"] = DirectoryScan(
        (
            DirectoryRecord(FILE, "a.txt", b"\x00" * 32, 1),
            DirectoryRecord(DIRECTORY, "sub"),
            DirectoryRecord(FILE, "sub/b.txt", b"\x01" * 32, 1),
        ),
        DirectoryDigest("a" * 64, 2, 3),
    )
    assert main(["inspect", str(tmp_path), "--directory-artifacts", "--json"]) == 0
    covered = json_doc(capsys)
    assert covered["findings"] == []


# --- output modes ---------------------------------------------------------------


def test_summary_only_keeps_counts_and_no_details(tmp_path, fake_scanner, capsys):
    write_directory_manifest(tmp_path)
    fake_scanner["outcome"] = _leaky_error("unreadable")

    assert main(["validate", str(tmp_path), "--directory-artifacts", "--json",
                 "--summary-only"]) == 1
    raw = capsys.readouterr().out
    data = json.loads(raw)
    assert data["summary_version"] == 1
    assert data["by_code"] == {"directory-unreadable": 1}
    assert data["by_severity"] == {"error": 1}
    assert data["exit_code"] == 1
    assert set(data) == {"summary_version", "total_findings", "by_severity",
                         "by_code", "exit_code"}
    assert_no_canary(raw)


def test_redacted_manifest_path_survives_a_scan_error(tmp_path, fake_scanner, capsys):
    write_directory_manifest(tmp_path)
    fake_scanner["outcome"] = _leaky_error("unreadable")
    manifest = tmp_path / MANIFEST
    before = manifest.read_bytes()

    assert main(["validate", str(tmp_path), "--directory-artifacts", "--json",
                 "--redact-manifest-path"]) == 1
    raw = capsys.readouterr().out
    data = json.loads(raw)
    assert data["manifest"] == "<redacted>"
    assert data["findings"][0]["path"] == "data"
    assert data["findings"][0]["subject"] == "data"
    assert_no_canary(raw)
    assert manifest.read_bytes() == before


def test_flag_is_grammar_only_for_validate_and_inspect():
    import argparse

    from runevidence.cli import build_parser

    parser = build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    for name, route in sub.choices.items():
        options = {option for action in route._actions for option in action.option_strings}
        if name in ("validate", "inspect"):
            assert "--directory-artifacts" in options
        else:
            assert "--directory-artifacts" not in options


@pytest.mark.parametrize("command", ["validate", "inspect"])
def test_help_describes_the_flag_as_experimental_read_only(command, capsys):
    assert main([command, "--help"]) == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "--directory-artifacts" in help_text
    assert "experimental read-only directory-artifact checking" in help_text


# --- real ext4 scan (read-only) -------------------------------------------------


def _release_scan(root: Path):
    try:
        return scan_directory(root)
    except DirectoryScanError as exc:
        if exc.reason in ("unsupported-filesystem", "unsupported-platform"):
            pytest.skip(f"release policy refuses this host: {exc.reason}")
        raise


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="POSIX ext4 case")
def test_real_ext4_scan_matches_and_does_not_write(tmp_path, capsys):
    data = build_tree(tmp_path)
    digest = _release_scan(data)
    write_directory_manifest(tmp_path, sha256=digest.digest.sha256,
                             size_bytes=digest.digest.size_bytes)
    manifest = tmp_path / MANIFEST
    before_manifest = manifest.read_bytes()
    before_tree = sorted(
        (p.relative_to(tmp_path).as_posix(), p.read_bytes() if p.is_file() else None)
        for p in tmp_path.rglob("*")
    )
    mtimes = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")}

    assert main(["validate", str(tmp_path), "--directory-artifacts"]) == 0
    out = capsys.readouterr().out
    assert "OK:" in out

    assert main(["inspect", str(tmp_path), "--directory-artifacts", "--json"]) == 0
    data_doc = json_doc(capsys)
    assert data_doc["findings"] == []
    assert data_doc["ok"] is True

    assert manifest.read_bytes() == before_manifest
    assert sorted(
        (p.relative_to(tmp_path).as_posix(), p.read_bytes() if p.is_file() else None)
        for p in tmp_path.rglob("*")
    ) == before_tree
    assert {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")} == mtimes


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="POSIX ext4 case")
def test_real_ext4_digest_mismatch_is_sanitized(tmp_path, capsys):
    data = tmp_path / "data"
    (data / "nested").mkdir(parents=True)
    (data / "alpha.dat").write_bytes(b"a")
    (data / "nested" / "beta.dat").write_bytes(b"b")
    digest = _release_scan(data)
    assert digest.digest.sha256 != "0" * 64
    write_directory_manifest(
        tmp_path,
        sha256="0" * 64,
        size_bytes=digest.digest.size_bytes + 1,
    )
    scan_root = data.absolute()
    member_names = ("alpha.dat", "beta.dat", "nested")

    assert main(["validate", str(tmp_path), "--directory-artifacts", "--json"]) == 1
    raw = capsys.readouterr().out
    document = json.loads(raw)
    assert [f["code"] for f in document["findings"]] == [
        "directory-hash-mismatch", "directory-size-mismatch",
    ]
    assert all(set(f) == FINDING_FIELDS for f in document["findings"])
    assert str(scan_root) not in raw
    assert str(scan_root.resolve()) not in raw
    assert digest.digest.sha256 not in raw
    assert "[Errno " not in raw
    for name in member_names:
        assert name not in json.dumps(document["findings"], sort_keys=True)
    # The pre-existing manifest path is still present; output is not path-free.
    assert document["manifest"] == str(tmp_path / MANIFEST)
    assert_no_canary(raw)

    assert main(["inspect", str(tmp_path), "--directory-artifacts", "--json"]) == 1
    raw = capsys.readouterr().out
    document = json.loads(raw)
    assert [f["code"] for f in document["findings"]] == [
        "directory-hash-mismatch", "directory-size-mismatch",
    ]
    assert str(scan_root) not in raw
    assert digest.digest.sha256 not in raw
    assert "[Errno " not in raw
    for name in member_names:
        assert name not in json.dumps(document["findings"], sort_keys=True)
    assert_no_canary(raw)


def test_non_ext4_root_reports_unsupported_filesystem(tmp_path, capsys):
    shared = Path("/dev/shm")
    if not sys.platform.startswith("linux") or not os.path.isdir(shared):
        pytest.skip("no Linux tmpfs to scan")
    try:
        scan_directory(shared)
    except DirectoryScanError as exc:
        if exc.reason != "unsupported-filesystem":
            pytest.skip(f"/dev/shm is not a refused tmpfs here: {exc.reason}")
    else:
        pytest.skip("/dev/shm is release-qualified on this host")

    root = shared / f"runevidence_cli_dir_{os.getpid()}"
    root.mkdir()
    try:
        (root / "data").mkdir()
        (root / "data" / "a.txt").write_bytes(b"a")
        write_directory_manifest(root)
        assert main(["validate", str(root), "--directory-artifacts", "--json"]) == 1
        raw = capsys.readouterr().out
        data = json.loads(raw)
        assert [f["code"] for f in data["findings"]] == ["directory-unsafe"]
        assert "unsupported-filesystem" in data["findings"][0]["message"]
        assert_no_canary(raw)
    finally:
        (root / "data" / "a.txt").unlink(missing_ok=True)
        (root / "data").rmdir()
        (root / MANIFEST).unlink(missing_ok=True)
        root.rmdir()


def test_real_tmpfs_refusal_does_not_echo_the_absolute_scan_root(capsys):
    shared = Path("/dev/shm")
    if not sys.platform.startswith("linux") or not os.path.isdir(shared):
        pytest.skip("no Linux tmpfs to scan")
    try:
        scan_directory(shared)
    except DirectoryScanError as exc:
        if exc.reason != "unsupported-filesystem":
            pytest.skip(f"/dev/shm is not a refused tmpfs here: {exc.reason}")
    else:
        pytest.skip("/dev/shm is release-qualified on this host")

    root = shared / f"runevidence_cli_tmpfs_{os.getpid()}"
    root.mkdir()
    try:
        data = root / "data"
        data.mkdir()
        (data / "a.txt").write_bytes(b"a")
        write_directory_manifest(root)
        scan_root = data.absolute()
        for command in ("validate", "inspect"):
            assert main([command, str(root), "--directory-artifacts", "--json"]) == 1
            raw = capsys.readouterr().out
            document = json.loads(raw)
            directory_findings = [
                f for f in document["findings"] if f["code"].startswith("directory-")
            ]
            assert [f["code"] for f in directory_findings] == ["directory-unsafe"]
            assert "unsupported-filesystem" in directory_findings[0]["message"]
            assert str(scan_root) not in raw
            assert str(scan_root.resolve()) not in raw
            # The manifest path is pre-existing output and stays visible.
            assert str(root) in raw
            assert_no_canary(raw)
    finally:
        (root / "data" / "a.txt").unlink(missing_ok=True)
        (root / "data").rmdir()
        (root / MANIFEST).unlink(missing_ok=True)
        root.rmdir()
