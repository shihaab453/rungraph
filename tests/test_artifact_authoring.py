"""Artifact metadata authoring without hand-editing the manifest."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from runevidence import authoring
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


def test_remove_artifact_untracks_without_deleting_the_file(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    source = root / "data.csv"
    before_source = source.read_bytes()
    before_manifest = (root / "runevidence.json").read_bytes()

    assert main(["artifact", "remove", str(root)]) == 2
    assert "needs a non-empty --id" in capsys.readouterr().out
    assert (root / "runevidence.json").read_bytes() == before_manifest

    assert main(["artifact", "remove", str(root), "--id", "data", "--dry-run"]) == 0
    assert (root / "runevidence.json").read_bytes() == before_manifest

    assert main(["artifact", "remove", str(root), "--id", "data"]) == 0
    manifest = json.loads((root / "runevidence.json").read_text(encoding="utf-8"))
    assert manifest["artifacts"] == []
    assert source.read_bytes() == before_source
    capsys.readouterr()
    assert main(["validate", str(root)]) == 0


def test_remove_artifact_refuses_a_result_reference(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    manifest = json.loads((root / "runevidence.json").read_text(encoding="utf-8"))
    manifest["results"] = [
        {"id": "metric", "run_id": "r", "name": "accuracy", "value": 1, "artifact_id": "data"}
    ]
    (root / "runevidence.json").write_text(json.dumps(manifest), encoding="utf-8")
    before = (root / "runevidence.json").read_bytes()

    assert main(["artifact", "remove", str(root), "--id", "data"]) == 2
    assert "result 'metric'" in capsys.readouterr().out
    assert (root / "runevidence.json").read_bytes() == before


def test_remove_artifact_refuses_claim_relationship_and_condition_references():
    def base() -> dict:
        return {
            "schema_version": "0.2",
            "project": {"id": "p", "name": "p"},
            "runs": [{"id": "r"}],
            "artifacts": [{"id": "data", "path": "data.csv", "sha256": "0" * 64, "size_bytes": 1}],
        }

    claim = base()
    claim["evidence"] = [{"id": "c", "claim": "c", "supports": ["data"]}]
    with pytest.raises(authoring.AuthoringError, match="claim 'c'"):
        authoring.remove_artifact(claim, "data")

    relationship = base()
    relationship["relationships"] = [
        {"source": "data", "target": "r", "type": "input_to"}
    ]
    with pytest.raises(authoring.AuthoringError, match="a relationship"):
        authoring.remove_artifact(relationship, "data")

    condition = base()
    condition["runs"][0]["conditions"] = {"evaluation_dataset_artifact_id": "data"}
    with pytest.raises(authoring.AuthoringError, match="condition 'evaluation_dataset_artifact_id'"):
        authoring.remove_artifact(condition, "data")


def _add_keep_and_results(root: Path, count: int, *, cited: bool = False) -> None:
    document = json.loads((root / "runevidence.json").read_text(encoding="utf-8"))
    (root / "keep.csv").write_bytes(b"keep\n")
    document["artifacts"].append(
        {
            "id": "keep",
            "path": "keep.csv",
            "sha256": hashlib.sha256(b"keep\n").hexdigest(),
            "size_bytes": 5,
        }
    )
    document["results"] = [
        {
            "id": f"r{index}",
            "run_id": "r",
            "name": f"metric-{index}",
            "value": index,
            "artifact_id": "data",
        }
        for index in range(count)
    ]
    document["results"].append(
        {"id": "kept-result", "run_id": "r", "name": "keep", "value": 1, "artifact_id": "keep"}
    )
    if cited:
        document["evidence"] = [
            {"id": "claim", "claim": "one result", "supports": ["r0"], "contradicts": []}
        ]
    (root / "runevidence.json").write_text(json.dumps(document), encoding="utf-8")


def test_remove_artifact_with_many_results_in_one_edit(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    _add_keep_and_results(root, 2_278)
    assert main(["artifact", "remove", str(root), "--id", "data", "--with-results"]) == 0
    document = json.loads((root / "runevidence.json").read_text(encoding="utf-8"))
    assert [item["id"] for item in document["artifacts"]] == ["keep"]
    assert [item["id"] for item in document["results"]] == ["kept-result"]
    capsys.readouterr()
    assert main(["validate", str(root), "--strict"]) == 0


def test_remove_artifact_with_results_refuses_a_cited_result(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    _add_keep_and_results(root, 2, cited=True)
    before = (root / "runevidence.json").read_bytes()
    assert main(["artifact", "remove", str(root), "--id", "data", "--with-results"]) == 2
    out = capsys.readouterr().out
    assert "still cited" in out
    assert "claim 'claim'" in out
    assert (root / "runevidence.json").read_bytes() == before


def test_remove_artifact_with_results_refuses_artifact_relationship(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    _add_keep_and_results(root, 2)
    document = json.loads((root / "runevidence.json").read_text(encoding="utf-8"))
    document["relationships"] = [
        {"source": "data", "target": "r", "type": "input_to"}
    ]
    (root / "runevidence.json").write_text(json.dumps(document), encoding="utf-8")
    before = (root / "runevidence.json").read_bytes()
    assert main(["artifact", "remove", str(root), "--id", "data", "--with-results"]) == 2
    assert "a relationship" in capsys.readouterr().out
    assert (root / "runevidence.json").read_bytes() == before


def test_with_results_is_remove_only_and_dry_run_is_clean(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    _add_keep_and_results(root, 2)
    entry = write_entry(root, {"id": "data", "kind": "dataset"})
    assert main(["artifact", "edit", str(root), "--file", str(entry), "--with-results"]) == 2
    assert "--with-results is only valid" in capsys.readouterr().out

    before = (root / "runevidence.json").read_bytes()
    assert main(["artifact", "remove", str(root), "--id", "data", "--with-results", "--dry-run"]) == 0
    assert (root / "runevidence.json").read_bytes() == before
