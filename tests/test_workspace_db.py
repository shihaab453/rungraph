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


def test_workspace_db_check_detects_drift(tmp_path: Path, capsys):
    import sqlite3
    output = tmp_path / "workspace.sqlite"
    assert main(["db-export", "examples/workspace-study", "--output", str(output)]) == 0
    capsys.readouterr()
    assert main(["db-check", "examples/workspace-study", "--db", str(output), "--json"]) == 0
    connection = sqlite3.connect(output)
    try:
        connection.execute("delete from members where rowid=1")
        connection.commit()
    finally:
        connection.close()
    assert main(["db-check", "examples/workspace-study", "--db", str(output), "--json"]) == 1


def test_workspace_db_import_round_trips(tmp_path: Path, capsys):
    output = tmp_path / "workspace.sqlite"
    target = tmp_path / "rungraph.json"
    assert main(["db-export", "examples/workspace-study", "--output", str(output)]) == 0
    capsys.readouterr()
    assert main(["db-import", "--db", str(output), "--output", str(target), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["counts"]["members"] == 2
    assert json.loads(target.read_text())["format"] == "rungraph-workspace"


def test_workspace_db_import_refuses_a_corrupt_database_without_traceback(tmp_path: Path, capsys):
    database = tmp_path / "bad.sqlite"
    database.write_bytes(b"not a sqlite database")
    target = tmp_path / "rungraph.json"
    assert main(["db-import", "--db", str(database), "--output", str(target)]) == 2
    out = capsys.readouterr().out
    assert "db-import" in out
    assert "Traceback" not in out
    assert not target.exists()
