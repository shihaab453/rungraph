"""Checks between a derived SQLite cache and its manifest."""

from __future__ import annotations

import json
from pathlib import Path

from runevidence.cli import main


def test_db_check_matches_after_export_and_detects_drift(tmp_path: Path, capsys):
    manifest = Path("examples/generic-experiment")
    database = tmp_path / "cache.sqlite"
    assert main(["db-export", str(manifest), "--output", str(database)]) == 0
    capsys.readouterr()
    assert main(["db-check", str(manifest), "--db", str(database), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    (tmp_path / "copy").mkdir()
    for name in ("runevidence.json",):
        (tmp_path / "copy" / name).write_bytes((manifest / name).read_bytes())
    document = json.loads((tmp_path / "copy" / "runevidence.json").read_text())
    document["results"] = document["results"][:1]
    (tmp_path / "copy" / "runevidence.json").write_text(json.dumps(document))
    assert main(["db-check", str(tmp_path / "copy"), "--db", str(database), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["mismatches"][0]["table"] == "meta"


def test_db_check_detects_relationship_and_reference_drift(tmp_path: Path, capsys):
    import sqlite3
    manifest = Path("examples/generic-experiment")
    database = tmp_path / "cache.sqlite"
    assert main(["db-export", str(manifest), "--output", str(database)]) == 0
    capsys.readouterr()
    connection = sqlite3.connect(database)
    try:
        connection.execute("delete from relationships where source='run-tuned'")
        connection.commit()
    finally:
        connection.close()
    assert main(["db-check", str(manifest), "--db", str(database), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert any(item["table"] == "relationships" for item in data["mismatches"])
