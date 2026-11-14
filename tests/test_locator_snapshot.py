"""Protected snapshot: one read, descriptor identity, containment and caps."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from runevidence.checks import run_checks
from runevidence.checks import files as file_checks
from runevidence.manifest import dump_manifest, load_manifest
from runevidence.models import Manifest


def _manifest(root: Path, *, data: bytes, sha256=None, locators=None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    target = root / "metrics.jsonl"
    target.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest() if sha256 is None else sha256
    doc = {
        "schema_version": "0.3",
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "name": "Run"}],
        "artifacts": [
            {
                "id": "a",
                "path": "metrics.jsonl",
                "sha256": digest,
                "size_bytes": len(data),
            }
        ],
        "results": [
            {
                "id": f"res{index}",
                "run_id": "run",
                "name": "val",
                "value": 0.9,
                "artifact_id": "a",
                "locator": locator,
            }
            for index, locator in enumerate(locators or [{"kind": "jsonl", "line": 1, "pointer": "/val"}])
        ],
        "evidence": [],
    }
    (root / "runevidence.json").write_text(dump_manifest(Manifest.model_validate(doc)))
    return target


def _codes(report):
    return [finding.code.value for finding in report.findings]


@pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX-only evidence: the shared protected open is the dir_fd walk",
)
def test_two_locators_share_one_protected_open(tmp_path, monkeypatch):
    root = tmp_path / "once"
    target = _manifest(
        root,
        data=b'{"val": 0.9}\n',
        locators=[
            {"kind": "jsonl", "line": 1, "pointer": "/val"},
            {"kind": "jsonl", "line": 1, "pointer": ""},
        ],
    )
    # The second locator targets the line object, so it is unsupported, but the
    # file is still read only once for both.
    opened = []
    real_open = file_checks.os.open

    def counting_open(path, flags, *args, **kwargs):
        # The protected walk opens the leaf by name relative to its verified
        # parent descriptor, not by the resolved absolute path.
        if os.fspath(path) == "metrics.jsonl" and flags & getattr(os, "O_NONBLOCK", 0):
            opened.append(flags)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(file_checks.os, "open", counting_open)
    monkeypatch.setattr(
        file_checks,
        "sha256_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("streaming hash used")),
    )
    report = run_checks(load_manifest(root))
    assert len(opened) == 1
    assert "locator-unsupported-value" in _codes(report)
    flags = opened[0]
    assert flags & getattr(os, "O_NOFOLLOW", 0)
    assert flags & getattr(os, "O_NONBLOCK", 0)
    assert flags & getattr(os, "O_NOCTTY", 0)
    assert flags & getattr(os, "O_CLOEXEC", 0) or not hasattr(os, "O_CLOEXEC")


def test_descriptor_identity_change_is_artifact_changed(tmp_path, monkeypatch):
    root = tmp_path / "identity"
    _manifest(root, data=b'{"val": 0.9}\n')
    real_fstat = os.fstat

    def changed_fstat(fd):
        stat = real_fstat(fd)
        return os.stat_result(
            (stat.st_mode, stat.st_ino + 1, stat.st_dev, stat.st_nlink,
             stat.st_uid, stat.st_gid, stat.st_size, stat.st_atime,
             stat.st_mtime, stat.st_ctime)
        )

    monkeypatch.setattr(file_checks.os, "fstat", changed_fstat)
    report = run_checks(load_manifest(root))
    assert "artifact-changed" in _codes(report)


@pytest.mark.skipif(
    not hasattr(os, "mkfifo"),
    reason="POSIX-only evidence: FIFOs require os.mkfifo",
)
def test_fifo_never_blocks_and_is_not_a_regular_snapshot(tmp_path):
    root = tmp_path / "fifo"
    _manifest(root, data=b"")
    fifo = root / "fifo.jsonl"
    os.mkfifo(fifo)
    read = file_checks._read_snapshot(fifo, root)
    assert read.state == "changed"


def test_symlink_outside_root_is_refused_before_snapshot(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(b'{"val": 0.9}\n')
    _manifest(root, data=b'{"val": 0.9}\n')
    (root / "metrics.jsonl").unlink()
    (root / "metrics.jsonl").symlink_to(outside)
    report = run_checks(load_manifest(root))
    codes = _codes(report)
    assert codes == ["path-escapes-root"]


def test_symlink_inside_root_is_permitted(tmp_path):
    root = tmp_path / "root-inside"
    target = _manifest(root, data=b'{"val": 0.9}\n')
    link = root / "alias.jsonl"
    link.symlink_to(target.name)
    doc = json.loads((root / "runevidence.json").read_text())
    doc["artifacts"][0]["path"] = "alias.jsonl"
    (root / "runevidence.json").write_text(dump_manifest(Manifest.model_validate(doc)))
    report = run_checks(load_manifest(root))
    assert report.findings == []


@pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX-only evidence: the shared protected open is the dir_fd walk",
)
def test_over_cap_still_hashes_and_each_locator_is_unreadable(tmp_path, monkeypatch):
    root = tmp_path / "over"
    data = b"x" * (60 * 1024 * 1024)
    _manifest(root, data=data)
    real_open = file_checks.os.open
    opened = []

    def counting_open(path, flags, *args, **kwargs):
        if os.fspath(path) == "metrics.jsonl" and flags & getattr(os, "O_NONBLOCK", 0):
            opened.append(flags)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(file_checks.os, "open", counting_open)
    report = run_checks(load_manifest(root))
    assert len(opened) == 1
    assert "hash-mismatch" not in _codes(report)
    assert _codes(report).count("locator-unreadable") == 1

    doc = json.loads((root / "runevidence.json").read_text())
    doc["artifacts"][0]["sha256"] = "0" * 64
    (root / "runevidence.json").write_text(dump_manifest(Manifest.model_validate(doc)))
    report = run_checks(load_manifest(root))
    assert "hash-mismatch" in _codes(report)
    assert "locator-unreadable" in _codes(report)


def test_non_targeted_artifact_keeps_streaming_hash(tmp_path, monkeypatch):
    root = tmp_path / "legacy"
    data = b'{"val": 0.9}\n'
    _manifest(root, data=data)
    doc = json.loads((root / "runevidence.json").read_text())
    doc["results"][0].pop("locator")
    (root / "runevidence.json").write_text(dump_manifest(Manifest.model_validate(doc)))
    called = []
    real = file_checks.sha256_file
    monkeypatch.setattr(file_checks, "sha256_file", lambda path: called.append(path) or real(path))
    run_checks(load_manifest(root))
    assert called
