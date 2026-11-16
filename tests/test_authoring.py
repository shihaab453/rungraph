"""Manifest authoring for results, claims/evidence and relationships."""

from __future__ import annotations

import json
from pathlib import Path

from runevidence.cli import main

MANIFEST = {
    "schema_version": "0.1",
    "project": {"id": "p", "name": "p"},
    "runs": [{"id": "run"}],
    "artifacts": [
        {"id": "data", "path": "data.csv", "sha256": "0" * 64, "size_bytes": 1}
    ],
}


def make_manifest(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "runevidence.json"
    path.write_text(json.dumps(MANIFEST), encoding="utf-8")
    (root / "data.csv").write_bytes(b"x")
    return path


def write_json(path: Path, data: dict) -> str:
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_result_add_edit_remove(tmp_path: Path, capsys):
    path = make_manifest(tmp_path)
    result = {"id": "acc", "run_id": "run", "name": "accuracy", "value": 0.9}
    assert main(["result", "add", str(path), "--file", write_json(tmp_path / "r.json", result)]) == 0
    doc = json.loads(path.read_text())
    assert doc["results"][0]["id"] == "acc"

    result["value"] = 0.95
    assert main(["result", "edit", str(path), "--file", write_json(tmp_path / "r.json", result)]) == 0
    assert json.loads(path.read_text())["results"][0]["value"] == 0.95

    assert main(["result", "remove", str(path), "--id", "acc"]) == 0
    assert json.loads(path.read_text())["results"] == []


def test_evidence_add_requires_resolving_targets(tmp_path: Path, capsys):
    path = make_manifest(tmp_path)
    claim = {"id": "c1", "claim": "data is intact", "supports": ["data"], "contradicts": []}
    assert main(["evidence", "add", str(path), "--file", write_json(tmp_path / "c.json", claim)]) == 0
    assert json.loads(path.read_text())["evidence"][0]["id"] == "c1"

    broken = {"id": "c2", "claim": "missing", "supports": ["absent"], "contradicts": []}
    before = path.read_bytes()
    assert main(["evidence", "add", str(path), "--file", write_json(tmp_path / "b.json", broken)]) == 2
    assert path.read_bytes() == before


def test_relationship_add_and_remove(tmp_path: Path, capsys):
    path = make_manifest(tmp_path)
    relationship = {"source": "data", "target": "run", "type": "input_to"}
    assert main(["relationship", "add", str(path), "--file", write_json(tmp_path / "rel.json", relationship)]) == 0
    assert json.loads(path.read_text())["relationships"][0]["type"] == "input_to"
    assert main(["relationship", "remove", str(path), "--index", "0"]) == 0
    assert json.loads(path.read_text())["relationships"] == []


def test_duplicate_id_is_refused(tmp_path: Path, capsys):
    path = make_manifest(tmp_path)
    result = {"id": "run", "run_id": "run", "name": "collision", "value": 1.0}
    before = path.read_bytes()
    assert main(["result", "add", str(path), "--file", write_json(tmp_path / "r.json", result)]) == 2
    assert path.read_bytes() == before


def test_dry_run_writes_nothing(tmp_path: Path, capsys):
    path = make_manifest(tmp_path)
    before = path.read_bytes()
    result = {"id": "acc", "run_id": "run", "name": "accuracy", "value": 0.9}
    assert main(["result", "add", str(path), "--file", write_json(tmp_path / "r.json", result), "--dry-run"]) == 0
    assert path.read_bytes() == before


def test_a_malformed_file_is_refused_without_a_traceback(tmp_path: Path, capsys):
    path = make_manifest(tmp_path)
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    before = path.read_bytes()
    for argv in (
        ["result", "add", str(path), "--file", str(broken)],
        ["evidence", "add", str(path), "--file", str(broken)],
        ["relationship", "add", str(path), "--file", str(broken)],
    ):
        assert main(argv) == 2
        captured = capsys.readouterr()
        assert "cannot read" in captured.out
        assert "Traceback" not in captured.err
        assert path.read_bytes() == before


def test_edit_needs_an_id_in_the_file(tmp_path: Path, capsys):
    path = make_manifest(tmp_path)
    before = path.read_bytes()
    entry = {"run_id": "run", "name": "accuracy", "value": 0.9}
    assert main(["result", "edit", str(path), "--file", write_json(tmp_path / "r.json", entry)]) == 2
    assert "non-empty 'id'" in capsys.readouterr().out
    assert path.read_bytes() == before


def test_invalid_utf8_is_refused_without_a_traceback(tmp_path: Path, capsys):
    path = make_manifest(tmp_path)
    broken = tmp_path / "broken.json"
    broken.write_bytes(b"\xff")
    before = path.read_bytes()
    assert main(["result", "add", str(path), "--file", str(broken)]) == 2
    captured = capsys.readouterr()
    assert "cannot read" in captured.out
    assert "Traceback" not in captured.err
    assert path.read_bytes() == before


def test_a_non_string_result_id_is_refused(tmp_path: Path, capsys):
    path = make_manifest(tmp_path)
    before = path.read_bytes()
    entry = {"id": [], "run_id": "run", "name": "accuracy", "value": 0.9}
    assert main(["result", "add", str(path), "--file", write_json(tmp_path / "r.json", entry)]) == 2
    assert "non-empty string 'id'" in capsys.readouterr().out
    assert path.read_bytes() == before


def test_removing_a_cited_result_is_refused(tmp_path: Path, capsys):
    path = make_manifest(tmp_path)
    result = {"id": "acc", "run_id": "run", "name": "accuracy", "value": 0.9}
    assert main(["result", "add", str(path), "--file", write_json(tmp_path / "r.json", result)]) == 0
    claim = {"id": "c1", "claim": "accuracy holds", "supports": ["acc"], "contradicts": []}
    assert main(["evidence", "add", str(path), "--file", write_json(tmp_path / "c.json", claim)]) == 0
    before = path.read_bytes()
    assert main(["result", "remove", str(path), "--id", "acc"]) == 2
    assert "still cited" in capsys.readouterr().out
    assert path.read_bytes() == before

    assert main(["evidence", "remove", str(path), "--id", "c1"]) == 0
    assert main(["result", "remove", str(path), "--id", "acc"]) == 0
