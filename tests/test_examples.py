"""Committed examples stay clean."""

from __future__ import annotations

from pathlib import Path

import pytest

from runevidence.cli import main
from runevidence.rungraph_cli import main as rungraph_main

REPO = Path(__file__).resolve().parents[1]


def test_tensorboard_scalars_example_is_clean(capsys):
    pytest.importorskip("runevidence_tensorboard")
    assert main(["validate", str(REPO / "examples/tensorboard-scalars"), "--strict"]) == 0
    assert "0 errors, 0 warnings" in capsys.readouterr().out


def test_workspace_study_example_is_clean(capsys):
    root = REPO / "examples/workspace-study"
    assert rungraph_main(["validate", str(root), "--strict"]) == 0
    assert "OK: exit 0" in capsys.readouterr().out
    assert rungraph_main(["report", str(root), "--json"]) == 0


def test_sacred_file_store_example_is_clean(capsys):
    root = REPO / "examples/sacred-file-store/runs"
    assert main(["validate", str(root), "--strict"]) == 0
    assert "0 errors, 0 warnings" in capsys.readouterr().out


def test_keras_history_example_is_clean(capsys):
    root = REPO / "examples/keras-history"
    assert main(["validate", str(root), "--strict"]) == 0
    assert "0 errors, 0 warnings" in capsys.readouterr().out


def test_lightning_logs_example_is_clean(capsys):
    root = REPO / "examples/lightning-logs"
    assert main(["validate", str(root), "--strict"]) == 0
    assert "0 errors, 0 warnings" in capsys.readouterr().out
