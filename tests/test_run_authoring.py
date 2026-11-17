"""Run authoring through the run command's action flags."""

from __future__ import annotations

import json
from pathlib import Path

from runevidence.cli import main


def make_root(tmp_path: Path) -> Path:
    data = b"dataset\n"
    document = {
        "schema_version": "0.2",
        "project": {"id": "p", "name": "p"},
        "runs": [{"id": "existing"}],
        "artifacts": [],
        "results": [],
    }
    (tmp_path / "data.csv").write_bytes(data)
    (tmp_path / "runevidence.json").write_text(json.dumps(document), encoding="utf-8")
    return tmp_path


def entry(root: Path, payload: dict) -> Path:
    path = root / "run.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_add_edit_and_remove_a_run(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    payload = entry(
        root,
        {
            "id": "new",
            "name": "New",
            "params": {"lr": 0.1},
        },
    )
    assert main(["run", str(root), "--add", "--file", str(payload)]) == 0
    document = json.loads((root / "runevidence.json").read_text(encoding="utf-8"))
    assert [run["id"] for run in document["runs"]] == ["existing", "new"]
    assert document["runs"][1]["params"] == {"lr": 0.1}

    edit = entry(root, {"id": "new", "name": "Renamed", "params": {"lr": 0.2}})
    assert main(["run", str(root), "--edit", "--file", str(edit)]) == 0
    document = json.loads((root / "runevidence.json").read_text(encoding="utf-8"))
    assert document["runs"][1]["name"] == "Renamed"
    assert document["runs"][1]["params"] == {"lr": 0.2}

    assert main(["run", str(root), "--remove", "--id", "new"]) == 0
    document = json.loads((root / "runevidence.json").read_text(encoding="utf-8"))
    assert [run["id"] for run in document["runs"]] == ["existing"]


def test_remove_refuses_while_artifacts_or_results_reference_it(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    document = json.loads((root / "runevidence.json").read_text(encoding="utf-8"))
    document["artifacts"] = [
        {"id": "data", "path": "data.csv", "sha256": "0" * 64, "size_bytes": 8, "run_id": "existing"}
    ]
    (root / "runevidence.json").write_text(json.dumps(document), encoding="utf-8")
    before = (root / "runevidence.json").read_bytes()

    assert main(["run", str(root), "--remove", "--id", "existing"]) == 2
    assert "still used by" in capsys.readouterr().out
    assert (root / "runevidence.json").read_bytes() == before


def test_add_refuses_duplicate_and_read_detail_still_works(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    payload = entry(root, {"id": "existing"})
    assert main(["run", str(root), "--add", "--file", str(payload)]) == 2
    assert "already used" in capsys.readouterr().out

    assert main(["run", str(root), "--id", "existing", "--json"]) == 0
    detail = json.loads(capsys.readouterr().out)
    assert detail["run"]["id"] == "existing"


def test_condition_selectors_must_resolve_to_run_inputs(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    document = json.loads((root / "runevidence.json").read_text(encoding="utf-8"))
    document["runs"] = [{"id": "r"}]
    document["artifacts"] = [
        {"id": "data", "path": "data.csv", "sha256": "0" * 64, "size_bytes": 8, "kind": "dataset"},
        {"id": "ckpt", "path": "model.bin", "sha256": "1" * 64, "size_bytes": 1, "kind": "checkpoint"},
    ]
    document["relationships"] = [
        {"source": "data", "target": "r", "type": "input_to"},
        {"source": "ckpt", "target": "r", "type": "input_to"},
    ]
    (root / "runevidence.json").write_text(json.dumps(document), encoding="utf-8")
    payload = entry(
        root,
        {
            "id": "r",
            "conditions": {
                "evaluation_split": "test",
                "evaluation_dataset_artifact_id": "data",
                "model_checkpoint_artifact_id": "ckpt",
            },
        },
    )
    assert main(["run", str(root), "--edit", "--file", str(payload)]) == 0

    bad = entry(
        root,
        {
            "id": "r",
            "conditions": {
                "evaluation_split": "test",
                "evaluation_dataset_artifact_id": "absent",
            },
        },
    )
    before = (root / "runevidence.json").read_bytes()
    assert main(["run", str(root), "--edit", "--file", str(bad)]) == 2
    assert "condition selectors" in capsys.readouterr().out
    assert (root / "runevidence.json").read_bytes() == before
