"""Read-only queries over a derived SQLite cache."""

from __future__ import annotations

import json
from pathlib import Path

from runevidence.cli import main


def test_db_query_is_read_only_and_bounded(tmp_path: Path, capsys):
    database = tmp_path / "cache.sqlite"
    assert main(["db-export", "examples/generic-experiment", "--output", str(database)]) == 0
    capsys.readouterr()
    assert main(["db-query", "--db", str(database), "--sql", "select id from runs order by id"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["columns"] == ["id"]
    assert [row[0] for row in data["rows"]] == ["run-baseline", "run-tuned"]
    assert data["truncated"] is False

    assert main(["db-query", "--db", str(database), "--sql", "delete from runs"]) == 2
    assert "only SELECT/WITH" in json.loads(capsys.readouterr().out)["error"]
