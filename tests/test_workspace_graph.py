"""Workspace graph, claim states and consumer projections."""

from __future__ import annotations

import json
from pathlib import Path

from runevidence.workspace import WORKSPACE_FILENAME, load_workspace
from runevidence.workspace_graph import build_graph, claim_states, run_member_checks

MANIFEST = {
    "schema_version": "0.1",
    "project": {"id": "p", "name": "p"},
    "runs": [{"id": "r"}],
    "artifacts": [
        {"id": "data", "path": "data.csv", "sha256": "0" * 64, "size_bytes": 1}
    ],
    "evidence": [
        {
            "id": "claim",
            "claim": "the data is intact",
            "supports": ["data"],
            "contradicts": [],
        }
    ],
}


def make_workspace(root: Path, claims: list[dict] | None = None) -> Path:
    member = root / "a"
    member.mkdir(parents=True)
    (member / "runevidence.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    doc = {
        "format": "rungraph-workspace",
        "version": "0.1",
        "id": "study",
        "name": "Study",
        "members": [{"id": "a", "path": "a"}],
        "claims": claims or [],
    }
    (root / WORKSPACE_FILENAME).write_text(json.dumps(doc), encoding="utf-8")
    return root


def states(root: Path):
    loaded = load_workspace(root)
    graph = build_graph(loaded)
    checks = run_member_checks(loaded)
    return graph, claim_states(loaded, graph, checks["findings"])


def test_graph_has_member_objects_and_edges(tmp_path: Path):
    graph, _ = states(make_workspace(tmp_path))

    assert "member:a" in graph.nodes
    assert "artifact:a#data" in graph.nodes
    assert "evidence:a#claim" in graph.nodes
    assert any(edge.relation == "supports" for edge in graph.edges)
    assert any(edge.relation == "contains" for edge in graph.edges)


def test_a_missing_artifact_makes_the_member_claim_at_risk(tmp_path: Path):
    _, result = states(make_workspace(tmp_path))
    member_claim = next(item for item in result if item.member == "a")
    assert "at-risk" in member_claim.labels
    assert "supported" not in member_claim.labels


def test_a_workspace_claim_inherits_member_at_risk(tmp_path: Path):
    claims = [
        {
            "id": "w1",
            "claim": "a's evidence holds",
            "supports": [{"member": "a", "id": "claim"}],
            "contradicts": [],
        }
    ]
    _, result = states(make_workspace(tmp_path, claims))
    workspace_claim = next(item for item in result if item.member is None)
    assert "at-risk" in workspace_claim.labels


def test_a_context_citation_does_not_count_as_support(tmp_path: Path):
    claims = [
        {
            "id": "w1",
            "claim": "the run exists",
            "supports": [{"member": "a", "id": "r"}],
            "contradicts": [],
        }
    ]
    _, result = states(make_workspace(tmp_path, claims))
    workspace_claim = next(item for item in result if item.member is None)
    assert "uncited" in workspace_claim.labels


def test_a_workspace_claim_with_an_unresolved_reference_is_flagged(tmp_path: Path):
    claims = [
        {
            "id": "w1",
            "claim": "a's missing result holds",
            "supports": [{"member": "a", "id": "absent"}],
            "contradicts": [],
        }
    ]
    _, result = states(make_workspace(tmp_path, claims))
    workspace_claim = next(item for item in result if item.member is None)
    assert {"unresolved", "at-risk"} <= set(workspace_claim.labels)
    assert "supported" not in workspace_claim.labels


def test_claim_cycles_are_reported(tmp_path: Path):
    claims = [
        {"id": "c1", "claim": "one", "supports": [{"claim": "c2"}], "contradicts": []},
        {"id": "c2", "claim": "two", "supports": [{"claim": "c1"}], "contradicts": []},
    ]
    graph, _ = states(make_workspace(tmp_path, claims))
    assert graph.cycles


def test_a_member_claim_with_an_unresolved_support_ref_is_at_risk(tmp_path: Path):
    root = make_workspace(tmp_path)
    manifest_path = root / "a" / "runevidence.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["evidence"][0]["supports"] = ["absent"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _, result = states(root)
    member_claim = next(item for item in result if item.member == "a")
    assert {"unresolved", "at-risk"} <= set(member_claim.labels)
    assert "supported" not in member_claim.labels


def test_a_member_claim_with_a_dangling_contradiction_stays_unresolved(tmp_path: Path):
    root = make_workspace(tmp_path)
    manifest_path = root / "a" / "runevidence.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["evidence"][0]["supports"] = ["r"]
    manifest["evidence"][0]["contradicts"] = ["absent"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _, result = states(root)
    member_claim = next(item for item in result if item.member == "a")
    assert "unresolved" in member_claim.labels
    assert "at-risk" not in member_claim.labels
    assert "supported" not in member_claim.labels


def test_a_member_claim_with_a_relationship_to_a_missing_id_is_at_risk(tmp_path: Path):
    root = make_workspace(tmp_path)
    manifest_path = root / "a" / "runevidence.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["relationships"] = [
        {"source": "claim", "target": "absent", "type": "supports"}
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    graph, result = states(root)
    member_claim = next(item for item in result if item.member == "a")
    assert {"unresolved", "at-risk"} <= set(member_claim.labels)
    assert "unresolved:a#absent" in graph.unresolved


def test_an_unknown_member_citation_is_a_named_unresolved_node(tmp_path: Path):
    claims = [
        {
            "id": "w1",
            "claim": "a missing member still counts",
            "supports": [{"member": "absent", "id": "acc"}],
            "contradicts": [],
        }
    ]
    graph, result = states(make_workspace(tmp_path, claims))
    workspace_claim = next(item for item in result if item.member is None)
    assert {"unresolved", "at-risk"} <= set(workspace_claim.labels)
    assert "unresolved:absent#acc" in graph.unresolved


def test_an_unknown_workspace_claim_citation_is_named_not_a_crash(tmp_path: Path):
    claims = [
        {
            "id": "w1",
            "claim": "a missing claim still counts",
            "supports": [{"claim": "absent"}],
            "contradicts": [],
        }
    ]
    graph, result = states(make_workspace(tmp_path, claims))
    workspace_claim = next(item for item in result if item.member is None)
    assert {"unresolved", "at-risk"} <= set(workspace_claim.labels)
    assert "claim:study#absent" in graph.unresolved


def test_a_declared_claim_citation_is_not_unresolved(tmp_path: Path):
    claims = [
        {"id": "w1", "claim": "one", "supports": [], "contradicts": []},
        {"id": "w2", "claim": "two", "supports": [{"claim": "w1"}], "contradicts": []},
    ]
    graph, result = states(make_workspace(tmp_path, claims))
    assert graph.unresolved == []
    second = next(item for item in result if item.id == "w2")
    assert "unresolved" not in second.labels
