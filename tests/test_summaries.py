"""Bounded, no-follow reads for metric summaries."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from runevidence.summaries import read_bounded


def test_reads_a_regular_file_exactly(tmp_path: Path):
    path = tmp_path / "metrics"
    path.write_bytes(b"1 0.5 1\n")
    data, state = read_bounded(path)
    assert state == "ok"
    assert data == b"1 0.5 1\n"


def test_refuses_over_the_cap(tmp_path: Path):
    path = tmp_path / "metrics"
    path.write_bytes(b"x" * 9)
    assert read_bounded(path, cap=8) == (None, "over-cap")


def test_accepts_exactly_the_cap(tmp_path: Path):
    path = tmp_path / "metrics"
    path.write_bytes(b"x" * 8)
    assert read_bounded(path, cap=8) == (b"x" * 8, "ok")


def test_missing_file(tmp_path: Path):
    assert read_bounded(tmp_path / "absent") == (None, "missing")


def test_directory_is_not_a_regular_file(tmp_path: Path):
    assert read_bounded(tmp_path) == (None, "not-regular")


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_symlink_is_refused(tmp_path: Path):
    target = tmp_path / "target"
    target.write_bytes(b"1 0.5 1\n")
    link = tmp_path / "link"
    link.symlink_to(target)
    data, state = read_bounded(link)
    assert data is None
    assert state in ("not-regular", "unreadable")


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFO on this platform")
def test_fifo_cannot_block_the_open(tmp_path: Path):
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    assert read_bounded(fifo) == (None, "not-regular")
