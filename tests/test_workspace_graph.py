"""Workspace graph, claim states and consumer projections."""

from __future__ import annotations

import json
from pathlib import Path

from runevidence.findings import Code, Severity
from runevidence.workspace import WORKSPACE_FILENAME, load_workspace
from runevidence.workspace_consumers import impact_data, report_data
from runevidence.workspace_graph import (
    build_graph,
    claim_states,
    claim_support_closure,
    run_member_checks,
)

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


def test_a_claim_citing_a_duplicated_member_id_is_unresolved_not_a_crash(tmp_path: Path):
    root = make_workspace(tmp_path)
    manifest_path = root / "a" / "runevidence.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["results"] = [{"id": "r", "run_id": "r", "name": "accuracy", "value": 0.9}]
    manifest["evidence"] = []
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    claims = [
        {
            "id": "w1",
            "claim": "an ambiguous member id",
            "supports": [{"member": "a", "id": "r"}],
            "contradicts": [],
        }
    ]
    (root / "rungraph.json").write_text(
        json.dumps(
            {
                "format": "rungraph-workspace",
                "version": "0.1",
                "id": "study",
                "name": "Study",
                "members": [{"id": "a", "path": "a"}],
                "claims": claims,
            }
        ),
        encoding="utf-8",
    )
    graph, result = states(root)
    workspace_claim = next(item for item in result if item.member is None)
    assert {"unresolved", "at-risk"} <= set(workspace_claim.labels)
    assert "unresolved:a#r" in graph.unresolved


def test_a_run_input_makes_citing_claims_at_risk(tmp_path: Path):
    root = make_workspace(tmp_path)
    manifest_path = root / "a" / "runevidence.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"].append(
        {"id": "input", "path": "missing.csv", "sha256": "0" * 64, "size_bytes": 1}
    )
    manifest["relationships"] = [
        {"source": "input", "target": "r", "type": "input_to"}
    ]
    manifest["results"] = [
        {"id": "acc", "run_id": "r", "name": "accuracy", "value": 0.9}
    ]
    manifest["evidence"] = [
        {"id": "claim", "claim": "accuracy holds", "supports": ["acc"], "contradicts": []}
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    claims = [
        {
            "id": "w1",
            "claim": "workspace accuracy holds",
            "supports": [{"member": "a", "id": "acc"}],
            "contradicts": [],
        }
    ]
    (root / "rungraph.json").write_text(
        json.dumps(
            {
                "format": "rungraph-workspace",
                "version": "0.1",
                "id": "study",
                "name": "Study",
                "members": [{"id": "a", "path": "a"}],
                "claims": claims,
            }
        ),
        encoding="utf-8",
    )
    graph, result = states(root)
    member_claim = next(item for item in result if item.member == "a")
    workspace_claim = next(item for item in result if item.member is None)
    assert "at-risk" in member_claim.labels
    assert "at-risk" in workspace_claim.labels

    from runevidence.workspace import load_workspace
    from runevidence.workspace_consumers import claim_closure

    loaded = load_workspace(root)
    closure = claim_closure(loaded, graph, workspace_claim)
    assert "artifact:a#input" in closure
    assert "result:a#acc" in closure


def test_a_cited_run_with_no_dependencies_stays_in_the_closure(tmp_path: Path):
    root = make_workspace(tmp_path)
    manifest_path = root / "a" / "runevidence.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"] = []
    manifest["evidence"] = [
        {"id": "e", "claim": "the run exists", "supports": ["r"], "contradicts": []}
    ]
    manifest["relationships"] = [
        {"source": "r", "target": "absent", "type": "derived_from"}
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    graph, result = states(root)
    member_claim = next(item for item in result if item.member == "a")
    assert {"unresolved", "at-risk"} <= set(member_claim.labels)
    assert "run:a#r" in claim_support_closure(graph, "evidence:a#e", {"supports"})


def test_impact_from_a_run_input_names_the_citing_claims(tmp_path: Path):
    root = make_workspace(tmp_path)
    manifest_path = root / "a" / "runevidence.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"].append(
        {"id": "input", "path": "missing.csv", "sha256": "0" * 64, "size_bytes": 1}
    )
    manifest["relationships"] = [{"source": "input", "target": "r", "type": "input_to"}]
    manifest["results"] = [{"id": "acc", "run_id": "r", "name": "accuracy", "value": 0.9}]
    manifest["evidence"] = [
        {"id": "claim", "claim": "accuracy holds", "supports": ["acc"], "contradicts": []}
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    claims = [
        {
            "id": "w1",
            "claim": "workspace accuracy holds",
            "supports": [{"member": "a", "id": "acc"}],
            "contradicts": [],
        }
    ]
    (root / "rungraph.json").write_text(
        json.dumps(
            {
                "format": "rungraph-workspace",
                "version": "0.1",
                "id": "study",
                "name": "Study",
                "members": [{"id": "a", "path": "a"}],
                "claims": claims,
            }
        ),
        encoding="utf-8",
    )
    loaded = load_workspace(root)
    graph = build_graph(loaded)
    checks = run_member_checks(loaded)
    states = claim_states(loaded, graph, checks["findings"])
    data = impact_data(loaded, graph, states, "artifact:a#input")
    assert {item["id"] for item in data["claims"]} == {"claim", "w1"}


def test_the_closure_keeps_every_recorded_producer(tmp_path: Path):
    root = make_workspace(tmp_path)
    manifest_path = root / "a" / "runevidence.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["runs"] = [{"id": "r"}, {"id": "r2"}]
    manifest["artifacts"] = [
        {"id": "art", "path": "art.bin", "sha256": "0" * 64, "size_bytes": 1, "run_id": "r"},
        {"id": "input2", "path": "in2.bin", "sha256": "0" * 64, "size_bytes": 1},
    ]
    manifest["relationships"] = [
        {"source": "art", "target": "r2", "type": "produced_by"},
        {"source": "input2", "target": "r2", "type": "input_to"},
    ]
    manifest["evidence"] = [
        {"id": "e", "claim": "art holds", "supports": ["art"], "contradicts": []}
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    graph, result = states(root)
    member_claim = next(item for item in result if item.member == "a")
    assert "at-risk" in member_claim.labels
    closure = claim_support_closure(graph, "evidence:a#e", {"supports"})
    assert {"run:a#r", "run:a#r2", "artifact:a#input2"} <= closure


def test_a_member_removed_before_verification_fails_closed(tmp_path: Path):
    root = make_workspace(tmp_path)
    loaded = load_workspace(root)
    graph = build_graph(loaded)
    (root / "a" / "runevidence.json").unlink()

    checks = run_member_checks(loaded)

    assert checks["reports"] == {}
    codes = [finding.code for finding in checks["findings"]["a"]]
    assert Code.manifest_not_found in codes
    assert checks["findings"]["a"][-1].code is Code.claim_at_risk

    states = claim_states(loaded, graph, checks["findings"])
    member_claim = next(item for item in states if item.member == "a")
    assert "at-risk" in member_claim.labels
    assert "supported" not in member_claim.labels
    data = report_data(loaded, graph, states, checks)
    assert data["exit_code"] == 1
    assert data["members"][0]["counts"]["error"] >= 2


def test_a_workspace_claim_loses_support_when_its_member_cannot_be_checked(tmp_path: Path):
    claims = [
        {
            "id": "w1",
            "claim": "a's evidence holds",
            "supports": [{"member": "a", "id": "claim"}],
            "contradicts": [],
        }
    ]
    root = make_workspace(tmp_path, claims)
    loaded = load_workspace(root)
    graph = build_graph(loaded)
    (root / "a" / "runevidence.json").unlink()

    checks = run_member_checks(loaded)
    states = claim_states(loaded, graph, checks["findings"])

    workspace_claim = next(item for item in states if item.member is None)
    assert "at-risk" in workspace_claim.labels
    assert "supported" not in workspace_claim.labels


def test_an_unexpected_check_exception_is_an_error_finding(tmp_path: Path, monkeypatch):
    root = make_workspace(tmp_path)
    loaded = load_workspace(root)

    def boom(*args: object, **kwargs: object):
        raise RuntimeError("simulated verifier failure")

    monkeypatch.setattr("runevidence.workspace_graph.run_checks", boom)
    checks = run_member_checks(loaded)

    codes = [finding.code for finding in checks["findings"]["a"]]
    assert Code.member_check_failed in codes
    assert checks["findings"]["a"][0].severity is Severity.error


def test_a_long_workspace_claim_chain_is_evaluated_iteratively(tmp_path: Path):
    count = 1_100
    claims = [
        {
            "id": f"c{index}",
            "claim": f"claim {index}",
            "supports": (
                [{"claim": f"c{index + 1}"}]
                if index + 1 < count
                else [{"member": "a", "id": "claim"}]
            ),
            "contradicts": [],
        }
        for index in range(count)
    ]
    root = make_workspace(tmp_path, claims)

    _, result = states(root)

    workspace = [item for item in result if item.member is None]
    assert len(workspace) == count
    # The tail cites a member evidence claim whose artifact is missing, so
    # every link in the chain inherits at-risk without a RecursionError.
    assert all("at-risk" in item.labels for item in workspace)


def test_a_valid_manifest_replaced_after_load_cannot_pass(tmp_path: Path):
    root = make_workspace(tmp_path)
    loaded = load_workspace(root)
    graph = build_graph(loaded)
    replacement = {
        "schema_version": "0.1",
        "project": {"id": "p", "name": "p"},
    }
    (root / "a" / "runevidence.json").write_text(json.dumps(replacement), encoding="utf-8")

    checks = run_member_checks(loaded)

    assert checks["reports"] == {}
    assert checks["failed"] == {"a"}
    assert checks["findings"]["a"][0].severity is Severity.error
    states = claim_states(loaded, graph, checks["findings"], checks["failed"])
    member_claim = next(item for item in states if item.member == "a")
    assert "at-risk" in member_claim.labels
    assert "supported" not in member_claim.labels
    assert report_data(loaded, graph, states, checks)["exit_code"] == 1


def test_a_workspace_claim_citing_a_failed_member_result_is_at_risk(tmp_path: Path):
    root = make_workspace(tmp_path)
    manifest = dict(MANIFEST)
    manifest["results"] = [{"id": "score", "run_id": "r", "name": "score", "value": 1}]
    (root / "a" / "runevidence.json").write_text(json.dumps(manifest), encoding="utf-8")
    document = json.loads((root / WORKSPACE_FILENAME).read_text(encoding="utf-8"))
    document["claims"] = [
        {
            "id": "w1",
            "claim": "a's score is recorded",
            "supports": [{"member": "a", "id": "score"}],
            "contradicts": [],
        }
    ]
    (root / WORKSPACE_FILENAME).write_text(json.dumps(document), encoding="utf-8")
    loaded = load_workspace(root)
    graph = build_graph(loaded)
    (root / "a" / "runevidence.json").write_text("{not json", encoding="utf-8")

    checks = run_member_checks(loaded)
    states = claim_states(loaded, graph, checks["findings"], checks["failed"])

    workspace_claim = next(item for item in states if item.member is None)
    assert "at-risk" in workspace_claim.labels
    assert "supported" not in workspace_claim.labels


def test_a_workspace_id_matching_a_failed_member_does_not_taint_healthy_claims(
    tmp_path: Path,
):
    root = make_workspace(tmp_path)
    healthy = root / "b"
    healthy.mkdir()
    healthy_manifest = dict(MANIFEST)
    healthy_manifest["results"] = [
        {"id": "score", "run_id": "r", "name": "score", "value": 2}
    ]
    (healthy / "runevidence.json").write_text(
        json.dumps(healthy_manifest), encoding="utf-8"
    )
    document = json.loads((root / WORKSPACE_FILENAME).read_text(encoding="utf-8"))
    document["id"] = "a"
    document["members"] = [
        {"id": "a", "path": "a"},
        {"id": "b", "path": "b"},
    ]
    document["claims"] = [
        {
            "id": "clean",
            "claim": "b's score is recorded",
            "supports": [{"member": "b", "id": "score"}],
            "contradicts": [],
        },
        {
            "id": "nested",
            "claim": "clean still holds",
            "supports": [{"claim": "clean"}],
            "contradicts": [],
        },
    ]
    (root / WORKSPACE_FILENAME).write_text(json.dumps(document), encoding="utf-8")
    loaded = load_workspace(root)
    graph = build_graph(loaded)
    (root / "a" / "runevidence.json").write_text("{not json", encoding="utf-8")

    checks = run_member_checks(loaded)
    states = claim_states(loaded, graph, checks["findings"], checks["failed"])

    labels = {item.id: item.labels for item in states if item.member is None}
    assert checks["failed"] == {"a"}
    assert "supported" in labels["clean"]
    assert "supported" in labels["nested"]
    assert "at-risk" not in labels["nested"]
