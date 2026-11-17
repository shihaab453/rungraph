"""Schema 0.11 migration and condition coverage."""

from __future__ import annotations

import json
from pathlib import Path

from runevidence.cli import main


def write(folder: Path, version: str = "0.10") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": version,
        "project": {"id": "p", "name": "p"},
        "runs": [{"id": "run"}],
        "artifacts": [
            {"id": "data", "path": "data.json", "sha256": "ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356", "size_bytes": 3}
        ],
        "results": [
            {"id": "acc", "run_id": "run", "name": "accuracy", "value": 0.9}
        ],
    }
    (folder / "data.json").write_text("{}\n", encoding="utf-8")
    path = folder / "runevidence.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_migrate_0_10_to_0_11_is_version_only(tmp_path: Path):
    path = write(tmp_path)
    assert main(["migrate", str(tmp_path), "--to", "0.11"]) == 0
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema_version"] == "0.11"
    assert document["results"][0]["value"] == 0.9
    assert main(["validate", str(tmp_path)]) == 0


def test_downgrade_from_0_11_is_refused(tmp_path: Path):
    path = write(tmp_path, "0.11")
    before = path.read_bytes()
    assert main(["migrate", str(tmp_path), "--to", "0.10"]) == 2
    assert path.read_bytes() == before


def test_conditions_accept_0_11(tmp_path: Path):
    path = write(tmp_path, "0.11")
    patch = tmp_path / "patch.json"
    patch.write_text(json.dumps({"evaluation_split": "test"}), encoding="utf-8")
    assert main(
        ["conditions", str(tmp_path), "--run", "run", "--patch", str(patch)]
    ) == 0
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["runs"][0]["conditions"]["evaluation_split"] == "test"


def test_init_force_refuses_a_declared_0_11(tmp_path: Path):
    write(tmp_path, "0.11")
    assert main(["init", str(tmp_path), "--force"]) == 2
    document = json.loads((tmp_path / "runevidence.json").read_text(encoding="utf-8"))
    assert document["schema_version"] == "0.11"
