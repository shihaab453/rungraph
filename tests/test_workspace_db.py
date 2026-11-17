"""Derived SQLite export for workspaces."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from runevidence.rungraph_cli import main


def test_workspace_db_export(tmp_path: Path, capsys):
    root = Path("examples/workspace-study")
    output = tmp_path / "workspace.sqlite"
    assert main(["db-export", str(root), "--output", str(output), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["format"] == "rungraph-sqlite-export-v1"
    assert data["counts"]["members"] == 2
    connection = sqlite3.connect(output)
    try:
        assert connection.execute("select count(*) from members").fetchone()[0] == 2
        assert connection.execute("select count(*) from claims").fetchone()[0] == 1
    finally:
        connection.close()


def test_workspace_db_export_refuses_overwrite(tmp_path: Path):
    output = tmp_path / "workspace.sqlite"
    output.write_bytes(b"keep")
    assert main(["db-export", "examples/workspace-study", "--output", str(output)]) == 2
    assert output.read_bytes() == b"keep"
