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
