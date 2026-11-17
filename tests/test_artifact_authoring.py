"""Artifact metadata authoring without hand-editing the manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from runevidence.cli import main


def make_root(root: Path, schema: str = "0.2") -> Path:
    data = b"dataset\n"
    (root / "data.csv").write_bytes(data)
    document = {
        "schema_version": schema,
        "project": {"id": "p", "name": "p"},
        "runs": [{"id": "r"}],
        "artifacts": [
            {
                "id": "data",
                "path": "data.csv",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
        ],
    }
    (root / "runevidence.json").write_text(json.dumps(document), encoding="utf-8")
    return root


def write_entry(root: Path, entry: dict) -> Path:
    path = root / "entry.json"
    path.write_text(json.dumps(entry), encoding="utf-8")
    return path


def test_edit_artifact_metadata_and_dataset_id(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    entry = write_entry(
        root,
        {
            "id": "data",
            "kind": "dataset",
            "dataset_id": "ds-shared",
            "run_id": "r",
            "metadata": {"source": "split-v1"},
        },
    )
    assert main(["artifact", "edit", str(root), "--file", str(entry)]) == 0
    manifest = json.loads((root / "runevidence.json").read_text(encoding="utf-8"))
    artifact = manifest["artifacts"][0]
    assert artifact["kind"] == "dataset"
    assert artifact["dataset_id"] == "ds-shared"
    assert artifact["run_id"] == "r"
    assert artifact["metadata"] == {"source": "split-v1"}
    capsys.readouterr()
    assert main(["validate", str(root)]) == 0


def test_edit_is_a_merge_and_dry_run_writes_nothing(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    entry = write_entry(root, {"id": "data", "kind": "dataset"})
    before = (root / "runevidence.json").read_bytes()
    assert main(
        ["artifact", "edit", str(root), "--file", str(entry), "--dry-run"]
    ) == 0
    assert (root / "runevidence.json").read_bytes() == before

    assert main(["artifact", "edit", str(root), "--file", str(entry)]) == 0
    manifest = json.loads((root / "runevidence.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"][0]["sha256"] == hashlib.sha256(b"dataset\n").hexdigest()
    assert manifest["artifacts"][0]["size_bytes"] == 8


def test_identity_and_byte_fields_are_refused(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    for field in ("path", "sha256", "size_bytes", "artifact_type"):
        entry = write_entry(root, {"id": "data", field: "x"})
        before = (root / "runevidence.json").read_bytes()
        assert main(["artifact", "edit", str(root), "--file", str(entry)]) == 2
        out = capsys.readouterr().out
        assert "cannot be edited here" in out
        assert (root / "runevidence.json").read_bytes() == before


def test_unknown_field_and_dataset_id_on_0_1_are_refused(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    entry = write_entry(root, {"id": "data", "unexpected": 1})
    assert main(["artifact", "edit", str(root), "--file", str(entry)]) == 2
    assert "unknown artifact field" in capsys.readouterr().out

    old = tmp_path / "old"
    old.mkdir()
    old = make_root(old, schema="0.1")
    entry = write_entry(old, {"id": "data", "dataset_id": "ds"})
    assert main(["artifact", "edit", str(old), "--file", str(entry)]) == 2
