"""Derived SQLite export of a validated manifest."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from runevidence.cli import main


def test_export_builds_query_tables_without_touching_the_manifest(tmp_path: Path, capsys):
    manifest = Path("examples/generic-experiment")
    before = (manifest / "runevidence.json").read_bytes()
    output = tmp_path / "export.sqlite"
    assert main(["db-export", str(manifest), "--output", str(output), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["format"] == "runevidence-db-export-v1"
    assert data["counts"]["runs"] == 2
    assert (manifest / "runevidence.json").read_bytes() == before

    connection = sqlite3.connect(output)
    try:
        assert connection.execute("select value from meta where key='schema_version'").fetchone()[0] == "0.1"
        assert connection.execute("select count(*) from runs").fetchone()[0] == 2
        assert connection.execute("select count(*) from artifacts").fetchone()[0] == 9
        assert connection.execute("select count(*) from evidence_refs").fetchone()[0] >= 2
    finally:
        connection.close()


def test_export_refuses_to_replace_without_force(tmp_path: Path, capsys):
    output = tmp_path / "export.sqlite"
    output.write_bytes(b"keep")
    assert main(["db-export", "examples/generic-experiment", "--output", str(output)]) == 2
    assert output.read_bytes() == b"keep"
    assert main(["validate", "examples/generic-experiment"]) == 0
