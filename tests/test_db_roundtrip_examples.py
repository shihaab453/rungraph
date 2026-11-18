"""Round-trip every committed example through the derived SQLite cache."""

from __future__ import annotations

import math
from pathlib import Path

from runevidence.cli import main as rv
from runevidence.manifest import load_manifest
from runevidence.rungraph_cli import main as rg
from runevidence.workspace import load_workspace

REPO = Path(__file__).resolve().parents[1]


def _normalize(value):
    if isinstance(value, float) and math.isnan(value):
        return "<nan>"
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def _manifest_signature(path: Path) -> dict:
    return _normalize(load_manifest(path).manifest.model_dump(mode="json"))


def test_every_manifest_example_round_trips(tmp_path: Path):
    examples = sorted(REPO.glob("examples/*/runevidence.json"))
    assert examples
    for index, manifest_path in enumerate(examples):
        if "workspace-study" in manifest_path.parts:
            continue
        database = tmp_path / f"{index}.sqlite"
        imported = tmp_path / f"{index}.json"
        assert rv(["db-export", str(manifest_path.parent), "--output", str(database), "--force"]) == 0
        assert rv(["db-import", "--db", str(database), "--output", str(imported), "--force"]) == 0
        assert _manifest_signature(manifest_path) == _manifest_signature(imported)


def test_every_workspace_example_round_trips(tmp_path: Path):
    examples = sorted(REPO.glob("examples/*/rungraph.json"))
    assert examples
    import shutil
    for index, document in enumerate(examples):
        copy = tmp_path / f"ws-{index}"
        shutil.copytree(document.parent, copy)
        database = tmp_path / f"ws-{index}.sqlite"
        imported = copy / "rungraph.json"
        assert rg(["db-export", str(copy), "--output", str(database), "--force"]) == 0
        assert rg(["db-import", "--db", str(database), "--output", str(imported), "--force"]) == 0
        before = load_workspace(document)
        after = load_workspace(imported)
        assert before.document.model_dump(mode="json") == after.document.model_dump(mode="json")
