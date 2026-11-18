"""Round-trip import from a derived SQLite manifest cache."""

from __future__ import annotations

import json
from pathlib import Path

from runevidence.cli import main
from runevidence.manifest import load_manifest


def test_db_import_round_trips_and_refuses_overwrite(tmp_path: Path, capsys):
    source = Path("examples/generic-experiment")
    database = tmp_path / "cache.sqlite"
    target = tmp_path / "imported.json"
    assert main(["db-export", str(source), "--output", str(database)]) == 0
    capsys.readouterr()
    assert main(["db-import", "--db", str(database), "--output", str(target), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["counts"]["runs"] == 2
    loaded = load_manifest(target)
    assert len(loaded.manifest.runs) == 2
    assert len(loaded.manifest.results) == 2
    before = target.read_bytes()
    assert main(["db-import", "--db", str(database), "--output", str(target)]) == 2
    assert target.read_bytes() == before


def test_db_import_refuses_a_corrupt_or_non_sqlite_file(tmp_path: Path, capsys):
    database = tmp_path / "bad.sqlite"
    database.write_bytes(b"not a sqlite database")
    target = tmp_path / "out.json"
    assert main(["db-import", "--db", str(database), "--output", str(target)]) == 2
    out = capsys.readouterr().out
    assert "ERROR:" in out
    assert "Traceback" not in out
    assert not target.exists()

    assert main(["db-import", "--db", str(database), "--output", str(target), "--json"]) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False
    assert data["exit_code"] == 2


def test_db_import_reports_missing_meta_without_a_traceback(tmp_path: Path, capsys):
    import sqlite3

    source = Path("examples/generic-experiment")
    database = tmp_path / "cache.sqlite"
    target = tmp_path / "out.json"
    assert main(["db-export", str(source), "--output", str(database)]) == 0
    capsys.readouterr()
    connection = sqlite3.connect(database)
    try:
        connection.execute("delete from meta where key='schema_version'")
        connection.commit()
    finally:
        connection.close()
    assert main(["db-import", "--db", str(database), "--output", str(target)]) == 2
    out = capsys.readouterr().out
    assert "schema_version" in out
    assert "Traceback" not in out
    assert not target.exists()
