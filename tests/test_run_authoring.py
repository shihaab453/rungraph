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
            "conditions": {
                "evaluation_split": "test",
                "evaluation_protocol": {"protocol_id": "p", "version": 1},
                "evaluation_dataset_artifact_id": "missing",
                "model_checkpoint_artifact_id": "missing",
                "source_revision": "rev",
                "environment": {"python": "3.12"},
            },
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
