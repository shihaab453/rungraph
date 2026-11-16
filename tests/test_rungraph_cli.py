"""The read-only rungraph CLI."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from runevidence.rungraph_cli import main

MANIFEST = {
    "schema_version": "0.1",
    "project": {"id": "p", "name": "p"},
    "runs": [{"id": "r"}],
    "artifacts": [
        {"id": "data", "path": "data.csv", "sha256": "0" * 64, "size_bytes": 1}
    ],
    "results": [{"id": "acc", "run_id": "r", "name": "accuracy", "value": 0.9}],
    "evidence": [
        {"id": "claim", "claim": "data is intact", "supports": ["data"], "contradicts": []}
    ],
}


def make_root(root: Path) -> Path:
    for name in ("a", "b"):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "runevidence.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    return root


def test_discover_lists_members(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    assert main(["discover", str(root), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [m["id"] for m in data["members"]] == ["prov-a", "prov-b"]
    assert data["exit_code"] == 0


def test_init_writes_a_workspace(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    assert main(["init", str(root), "--id", "study", "--name", "Study"]) == 0
    doc = json.loads((root / "rungraph.json").read_text(encoding="utf-8"))
    assert doc["format"] == "rungraph-workspace"
    assert [m["id"] for m in doc["members"]] == ["prov-a", "prov-b"]


def test_validate_reports_evidence_errors(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    main(["init", str(root), "--id", "study", "--name", "Study"])
    capsys.readouterr()
    assert main(["validate", str(root)]) == 1
    out = capsys.readouterr().out
    assert "prov-a" in out and "2 errors" in out
    assert "at-risk" in out


def test_impact_names_the_affected_claim(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    main(["init", str(root), "--id", "study", "--name", "Study"])
    capsys.readouterr()
    assert main(["impact", str(root), "--from", "artifact:prov-a#data"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [claim["id"] for claim in data["claims"]] == ["claim"]


def test_compare_and_matrix_cover_both_members(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    main(["init", str(root), "--id", "study", "--name", "Study"])
    capsys.readouterr()
    assert main(["compare", str(root), "--metric", "accuracy"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert len(data["rows"]) == 2
    assert data["rows"][0]["value"] == 0.9
    assert main(["matrix", str(root)]) == 0
    matrix = json.loads(capsys.readouterr().out)
    assert len(matrix["cells"]) == 2


def test_leaderboard_ranks_numeric_rows(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    main(["init", str(root), "--id", "study", "--name", "Study"])
    capsys.readouterr()
    assert main(["leaderboard", str(root), "--metric", "accuracy", "--direction", "max"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [row["rank"] for row in data["rows"]] == [1, 1]


def test_discover_check_runs_member_checks(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    assert main(["discover", str(root), "--check", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert all(member["check"]["errors"] == 2 for member in data["members"])
    assert data["exit_code"] == 1


def test_a_strict_warning_fails_and_sets_the_json_exit_code(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    data_bytes = b"x"
    digest = hashlib.sha256(data_bytes).hexdigest()
    for name in ("a", "b"):
        folder = root / name
        (folder / "data.csv").write_bytes(data_bytes)
        manifest_path = folder / "runevidence.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts"][0]["sha256"] = digest
        manifest["artifacts"][0]["size_bytes"] = len(data_bytes)
        if name == "a":
            manifest["evidence"].append(
                {"id": "uncited", "claim": "no support", "supports": [], "contradicts": []}
            )
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert main(["init", str(root), "--id", "study", "--name", "Study"]) == 0
    capsys.readouterr()

    assert main(["validate", str(root), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["exit_code"] == 0

    assert main(["validate", str(root), "--strict", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["exit_code"] == 1

    assert main(["report", str(root), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["exit_code"] == 0

    assert main(["report", str(root), "--strict", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["exit_code"] == 1
