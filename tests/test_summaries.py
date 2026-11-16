"""Bounded, no-follow reads for metric summaries."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from runevidence.adapters import Adapter
from runevidence.models import Artifact, Manifest, Project, Result, Run
from runevidence.summaries import read_bounded, summary_claimants


def _manifest() -> tuple[Manifest, Result]:
    result = Result(
        id="m",
        run_id="r",
        name="acc",
        value=0.5,
        artifact_id="f",
        metadata={"step": 1, "timestamp_ms": 1},
    )
    manifest = Manifest(
        project=Project(id="p", name="p", metadata={"mlflow": {}}),
        runs=[Run(id="r", metadata={"mlflow": {}})],
        artifacts=[
            Artifact(
                id="f",
                path="run/metrics/acc",
                sha256="0" * 64,
                size_bytes=8,
                kind="metrics",
                run_id="r",
            )
        ],
        results=[result],
    )
    return manifest, result


class _Claiming(Adapter):
    name = "other"

    def discover(self, root):
        raise NotImplementedError

    def extract(self, root, discovery, *, project_id, project_name):
        raise NotImplementedError

    def claims_summary(self, manifest, result):
        return True


class _Broken(_Claiming):
    def claims_summary(self, manifest, result):
        raise RuntimeError("boom")


def test_an_unrelated_installed_adapter_is_not_asked(monkeypatch):
    manifest, result = _manifest()
    monkeypatch.setattr("runevidence.adapters.available_adapters", lambda: {"other": _Claiming})
    assert summary_claimants(manifest, result) == ([], "absent")


def test_an_eligible_adapter_claims_and_a_raising_one_is_broken(monkeypatch):
    manifest, result = _manifest()
    monkeypatch.setattr("runevidence.adapters.available_adapters", lambda: {"mlflow": _Claiming})
    claimants, reason = summary_claimants(manifest, result)
    assert reason == "ok" and len(claimants) == 1
    monkeypatch.setattr("runevidence.adapters.available_adapters", lambda: {"mlflow": _Broken})
    assert summary_claimants(manifest, result) == ([], "broken")



def test_reads_a_regular_file_exactly(tmp_path: Path):
    (tmp_path / "metrics").write_bytes(b"1 0.5 1\n")
    data, state = read_bounded(tmp_path, "metrics")
    assert state == "ok"
    assert data == b"1 0.5 1\n"


def test_reads_a_nested_regular_file(tmp_path: Path):
    (tmp_path / "run" / "metrics").mkdir(parents=True)
    (tmp_path / "run" / "metrics" / "acc").write_bytes(b"1 0.5 1\n")
    assert read_bounded(tmp_path, "run/metrics/acc") == (b"1 0.5 1\n", "ok")


def test_refuses_over_the_cap(tmp_path: Path):
    (tmp_path / "metrics").write_bytes(b"x" * 9)
    assert read_bounded(tmp_path, "metrics", cap=8) == (None, "over-cap")


def test_accepts_exactly_the_cap(tmp_path: Path):
    (tmp_path / "metrics").write_bytes(b"x" * 8)
    assert read_bounded(tmp_path, "metrics", cap=8) == (b"x" * 8, "ok")


def test_missing_file(tmp_path: Path):
    assert read_bounded(tmp_path, "absent") == (None, "missing")


def test_directory_is_not_a_regular_file(tmp_path: Path):
    (tmp_path / "sub").mkdir()
    assert read_bounded(tmp_path, "sub") == (None, "not-regular")


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_symlinked_leaf_is_refused(tmp_path: Path):
    (tmp_path / "target").write_bytes(b"1 0.5 1\n")
    (tmp_path / "link").symlink_to(tmp_path / "target")
    data, state = read_bounded(tmp_path, "link")
    assert data is None
    assert state in ("not-regular", "unreadable")


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_symlinked_parent_is_refused(tmp_path: Path):
    (tmp_path / "real" / "metrics").mkdir(parents=True)
    (tmp_path / "real" / "metrics" / "acc").write_bytes(b"1 0.5 1\n")
    (tmp_path / "alias").symlink_to(tmp_path / "real")
    data, state = read_bounded(tmp_path, "alias/metrics/acc")
    assert data is None
    assert state in ("not-regular", "unreadable")
    # The real path still works.
    assert read_bounded(tmp_path, "real/metrics/acc") == (b"1 0.5 1\n", "ok")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFO on this platform")
def test_fifo_cannot_block_the_open(tmp_path: Path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    assert read_bounded(tmp_path, "fifo") == (None, "not-regular")


def test_fallback_also_refuses_a_symlinked_parent(tmp_path: Path, monkeypatch):
    import runevidence.summaries as summaries

    (tmp_path / "real" / "metrics").mkdir(parents=True)
    (tmp_path / "real" / "metrics" / "acc").write_bytes(b"1 0.5 1\n")
    (tmp_path / "alias").symlink_to(tmp_path / "real")
    monkeypatch.setattr(summaries, "_DIR_FD", False)

    data, state = summaries.read_bounded(tmp_path, "alias/metrics/acc")
    assert data is None
    assert state in ("not-regular", "unreadable")
    assert summaries.read_bounded(tmp_path, "real/metrics/acc") == (b"1 0.5 1\n", "ok")
    (tmp_path / "sub").mkdir()
    assert summaries.read_bounded(tmp_path, "sub") == (None, "not-regular")
