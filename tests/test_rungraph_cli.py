"""The read-only rungraph CLI."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import os

import pytest

from runevidence.rungraph_cli import main
from runevidence.workspace import load_workspace

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


def test_graph_dot_fails_on_unresolved_when_asked(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    assert main(["init", str(root), "--id", "study", "--name", "Study"]) == 0
    document = json.loads((root / "rungraph.json").read_text(encoding="utf-8"))
    document["claims"] = [
        {
            "id": "w1",
            "claim": "missing member",
            "supports": [{"member": "absent", "id": "x"}],
            "contradicts": [],
        }
    ]
    (root / "rungraph.json").write_text(json.dumps(document), encoding="utf-8")
    capsys.readouterr()
    assert main(["graph", str(root), "--format", "dot", "--fail-on-unresolved"]) == 1
    assert "digraph rungraph" in capsys.readouterr().out


@pytest.mark.skipif(os.name == "nt", reason="workspace locks are POSIX-only")
def test_init_holds_the_workspace_lock(tmp_path: Path, capsys):
    from runevidence.workspace import WorkspaceError, workspace_lock

    root = make_root(tmp_path)
    try:
        with workspace_lock(root):
            assert main(["init", str(root), "--id", "study", "--name", "Study", "--json"]) == 2
    except WorkspaceError:
        pytest.skip("this platform does not support workspace locks")
    data = json.loads(capsys.readouterr().out)
    assert data["error"]["code"] == "workspace"
    assert not (root / "rungraph.json").exists()


def test_a_failed_force_keeps_the_previous_document(tmp_path: Path, capsys, monkeypatch):
    root = make_root(tmp_path)
    assert main(["init", str(root), "--id", "study", "--name", "Study"]) == 0
    capsys.readouterr()
    before = (root / "rungraph.json").read_bytes()

    def fail(*args: object, **kwargs: object):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("runevidence.rungraph_cli.save_workspace", fail)
    assert main(["init", str(root), "--id", "study", "--name", "Study", "--force", "--json"]) == 2

    assert (root / "rungraph.json").read_bytes() == before
    assert (root / "rungraph.json.bak").read_bytes() == before
    data = json.loads(capsys.readouterr().out)
    assert data["error"]["code"] == "workspace"


def test_init_refuses_an_invalid_workspace_id(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    assert main(["init", str(root), "--id", "bad#id", "--name", "Study"]) == 2
    assert not (root / "rungraph.json").exists()
    data = json.loads(capsys.readouterr().out)
    assert data["error"]["code"] == "invalid"


def test_compare_and_matrix_serialize_histogram_values(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    histogram = {
        "schema_version": "0.11",
        "project": {"id": "p", "name": "p"},
        "runs": [{"id": "r"}],
        "results": [
            {
                "id": "hist",
                "run_id": "r",
                "name": "weights",
                "value": {
                    "kind": "tf2-histogram",
                    "buckets": [{"left": 0.0, "right": 1.0, "count": 2.0}],
                },
            }
        ],
    }
    (root / "a" / "runevidence.json").write_text(json.dumps(histogram), encoding="utf-8")
    assert main(["init", str(root), "--id", "study", "--name", "Study"]) == 0
    capsys.readouterr()

    assert main(["compare", str(root), "--metric", "weights", "--json"]) == 0
    compared = json.loads(capsys.readouterr().out)
    assert compared["rows"][0]["value"]["kind"] == "tf2-histogram"
    assert compared["non_numeric"] == 1

    assert main(["matrix", str(root), "--json"]) == 0
    matrix = json.loads(capsys.readouterr().out)
    cell = next(item for item in matrix["cells"] if item["metric"] == "weights")
    assert cell["state"] == "non-numeric"
    assert cell["value"]["buckets"][0]["count"] == 2.0


def test_compare_follows_the_declared_manifest_filename(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    custom = dict(MANIFEST)
    custom["results"] = [{"id": "acc", "run_id": "r", "name": "accuracy", "value": 0.25}]
    (root / "a" / "custom.json").write_text(json.dumps(custom), encoding="utf-8")
    document = {
        "format": "rungraph-workspace",
        "version": "0.1",
        "id": "study",
        "name": "Study",
        "members": [
            {"id": "a", "path": "a", "manifest": "custom.json"},
            {"id": "b", "path": "b"},
        ],
    }
    (root / "rungraph.json").write_text(json.dumps(document), encoding="utf-8")

    assert main(["compare", str(root), "--metric", "accuracy", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    values = {row["member"]: row["value"] for row in data["rows"]}
    assert values == {"a": 0.25, "b": 0.9}


def test_report_text_renders_policy_and_baseline(tmp_path: Path, capsys):
    root = make_root(tmp_path / "study")
    baseline = make_root(tmp_path / "baseline")
    assert main(["init", str(root), "--id", "study", "--name", "Study"]) == 0
    assert main(["init", str(baseline), "--id", "study", "--name", "Study"]) == 0
    capsys.readouterr()

    assert main(["report", str(root), "--baseline", str(baseline)]) == 1
    out = capsys.readouterr().out
    assert "Policy: directory artifacts disabled" in out
    assert f"Baseline ({baseline}):" in out
    assert "prov-a: unchanged" in out
    assert "prov-b: unchanged" in out

    assert main(["report", str(root), "--baseline", str(baseline), "--directory-artifacts"]) == 1
    out = capsys.readouterr().out
    assert "Policy: directory artifacts enabled" in out


def test_report_text_names_baseline_changes(tmp_path: Path, capsys):
    root = make_root(tmp_path / "study")
    baseline = make_root(tmp_path / "baseline")
    assert main(["init", str(root), "--id", "study", "--name", "Study"]) == 0
    assert main(["init", str(baseline), "--id", "study", "--name", "Study"]) == 0
    document = json.loads((baseline / "rungraph.json").read_text(encoding="utf-8"))
    document["members"] = document["members"][:1]
    (baseline / "rungraph.json").write_text(json.dumps(document), encoding="utf-8")
    capsys.readouterr()

    assert main(["report", str(root), "--baseline", str(baseline)]) == 1
    out = capsys.readouterr().out
    assert "prov-b: added" in out


def test_force_init_preserves_a_declared_manifest_filename(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    custom = {
        "schema_version": "0.1",
        "project": {"id": "p", "name": "p"},
        "runs": [{"id": "r"}],
        "results": [{"id": "m", "run_id": "r", "name": "score", "value": 7}],
    }
    (root / "a" / "custom.json").write_text(json.dumps(custom), encoding="utf-8")
    (root / "a" / "runevidence.json").write_text("{not json", encoding="utf-8")
    document = {
        "format": "rungraph-workspace",
        "version": "0.1",
        "id": "study",
        "name": "Study",
        "members": [
            {"id": "a", "path": "a", "manifest": "custom.json"},
            {"id": "b", "path": "b"},
        ],
    }
    (root / "rungraph.json").write_text(json.dumps(document), encoding="utf-8")
    capsys.readouterr()

    assert main(["init", str(root), "--force", "--id", "study", "--name", "Study"]) == 0

    regenerated = json.loads((root / "rungraph.json").read_text(encoding="utf-8"))
    member = next(item for item in regenerated["members"] if item["id"] == "a")
    assert member["manifest"] == "custom.json"
    loaded = load_workspace(root)
    assert loaded.members["a"].manifest.results[0].value == 7


def test_report_text_names_the_claim_and_finding_messages(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    assert main(["init", str(root), "--id", "study", "--name", "Study"]) == 0
    capsys.readouterr()

    assert main(["report", str(root)]) == 1
    out = capsys.readouterr().out
    assert "prov-a (a): 2 errors" in out
    assert "ERROR missing-file [data]" in out
    assert "claim [prov-a]" in out
    assert "data is intact" in out


def test_analysis_routes_offer_human_readable_text(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    assert main(["init", str(root), "--id", "study", "--name", "Study"]) == 0
    capsys.readouterr()

    assert main(["compare", str(root), "--metric", "accuracy", "--text"]) == 0
    out = capsys.readouterr().out
    assert "Metric: accuracy" in out and "Rows (2):" in out

    assert main(["matrix", str(root), "--text"]) == 0
    assert "Metrics: accuracy" in capsys.readouterr().out

    assert main(
        ["leaderboard", str(root), "--metric", "accuracy", "--direction", "max", "--text"]
    ) == 0
    out = capsys.readouterr().out
    assert "Ranking:" in out and "0.9" in out

    assert main(["stats", str(root), "--metric", "accuracy", "--text"]) == 0
    out = capsys.readouterr().out
    assert "2 rows, 2 numeric" in out
