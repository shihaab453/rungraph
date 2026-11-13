"""D3 claim provenance paths: actual manifest models, CLI dispatch and no artifact reads."""
import json
from pathlib import Path

import pytest

from runevidence.cli import main
from runevidence.manifest import dump_manifest
from runevidence.models import (
    Artifact, EvidenceReference, Manifest, Project, Relationship,
    RelationshipType as T, Result, Run,
)
from runevidence.run_lineage import LineageError, claim_lineage, format_claim_lineage


def doc(*, runs=(), artifacts=(), results=(), claims=(), rels=()):
    return Manifest(project=Project(id="project", name="demo"), runs=list(runs),
                    artifacts=list(artifacts), results=list(results),
                    evidence=list(claims), relationships=list(rels))


def trace(manifest, identifier="c"):
    return claim_lineage(manifest, identifier, manifest_path="synthetic/runevidence.json")


def links(data):
    return {(e["source"], e["dependency"], e["type"]): e for e in data["edges"]}


def test_full_upstream_chain_result_artifact_run_input_and_producer():
    manifest = doc(
        runs=[Run(id="train"), Run(id="prep")],
        artifacts=[Artifact(id="metrics", path="metrics.json", run_id="train"),
                   Artifact(id="dataset", path="dataset.csv", run_id="prep")],
        results=[Result(id="acc", name="accuracy", run_id="train", value=.9, artifact_id="metrics")],
        claims=[EvidenceReference(id="c", claim="Accuracy", supports=["acc"])],
        rels=[Relationship(source="dataset", target="train", type=T.input_to)],
    )
    data = trace(manifest)
    edges = links(data)
    assert {("acc", "metrics", "reported_in"), ("acc", "train", "result_of"),
            ("metrics", "train", "produced_by"), ("train", "dataset", "input_to"),
            ("dataset", "prep", "produced_by")} <= set(edges)
    assert edges[("train", "dataset", "input_to")]["manifest_direction"] == "reversed"
    assert data["summary"]["nodes"] == 5
    assert data["summary"]["cycle_back_edges"] == 0
    assert data["summary"]["unresolved_nodes"] == 0
    assert "not validated provenance" in data["note"]


def test_inline_and_relationship_sources_merged_with_sorted_notes():
    manifest = doc(
        runs=[Run(id="r")],
        results=[Result(id="res", run_id="r", name="score", value=1)],
        claims=[EvidenceReference(id="c", claim="ok", supports=["res", "res"])],
        rels=[Relationship(source="res", target="r", type=T.result_of, note="B"),
              Relationship(source="res", target="r", type=T.result_of, note="A"),
              Relationship(source="c", target="res", type=T.supports, note="root")],
    )
    data = trace(manifest)
    assert data["roots"]["supports"][0]["via"] == ["inline", "relationship"]
    edge = links(data)[("res", "r", "result_of")]
    assert edge["via"] == ["inline", "relationship"]
    assert edge["relationship_notes"] == ["A", "B"]
    assert len(data["edges"]) == 1


def test_result_preserves_multiple_sources_even_with_inline_source():
    manifest = doc(
        runs=[Run(id="r")],
        artifacts=[Artifact(id="a", path="a.json"), Artifact(id="b", path="b.json")],
        results=[Result(id="x", run_id="r", name="x", artifact_id="a")],
        claims=[EvidenceReference(id="c", claim="x", supports=["x"])],
        rels=[Relationship(source="x", target="a", type=T.reported_in),
              Relationship(source="x", target="b", type=T.reported_in)],
    )
    data = trace(manifest)
    assert links(data)[("x", "a", "reported_in")]["via"] == ["inline", "relationship"]
    assert links(data)[("x", "b", "reported_in")]["via"] == ["relationship"]
    assert data["summary"]["dependencies"] == 3


def test_direct_artifact_does_not_follow_downstream_consumers():
    manifest = doc(
        runs=[Run(id="consumer")],
        artifacts=[Artifact(id="data", path="data.bin")],
        claims=[EvidenceReference(id="c", claim="Data", supports=["data"])],
        rels=[Relationship(source="data", target="consumer", type=T.input_to)],
    )
    data = trace(manifest)
    assert data["summary"]["nodes"] == 1
    assert data["edges"] == []


def test_run_reaches_inputs_but_does_not_auto_cite_outputs():
    manifest = doc(
        runs=[Run(id="r")],
        artifacts=[Artifact(id="input", path="in"), Artifact(id="output", path="out", run_id="r")],
        claims=[EvidenceReference(id="c", claim="run", supports=["r"])],
        rels=[Relationship(source="input", target="r", type=T.input_to)],
    )
    data = trace(manifest)
    assert {n["id"] for n in data["nodes"]} == {"r", "input"}
    assert "produced artifact" in data["note"]


def test_derived_from_run_and_artifact_dependencies():
    manifest = doc(
        runs=[Run(id="r2"), Run(id="r1")],
        artifacts=[Artifact(id="b", path="b"), Artifact(id="a", path="a")],
        claims=[EvidenceReference(id="c", claim="x", supports=["r2", "b"])],
        rels=[Relationship(source="r2", target="r1", type=T.derived_from),
              Relationship(source="b", target="a", type=T.derived_from)],
    )
    edges = links(trace(manifest))
    assert ("r2", "r1", "derived_from") in edges
    assert ("b", "a", "derived_from") in edges


def test_dangling_and_wrong_kind_dependencies_are_flagged_and_not_expanded():
    manifest = doc(
        runs=[Run(id="r")],
        artifacts=[Artifact(id="a", path="a", run_id="missing_run")],
        claims=[EvidenceReference(id="c", claim="x", supports=["a"])],
        rels=[Relationship(source="a", target="project", type=T.produced_by),
              Relationship(source="a", target="r", type=T.reported_in)],
    )
    data = trace(manifest)
    edges = links(data)
    assert edges[("a", "missing_run", "produced_by")]["status"] == "unresolved"
    assert edges[("a", "project", "produced_by")]["status"] == "invalid_endpoint_kinds"
    assert edges[("a", "r", "reported_in")]["status"] == "invalid_endpoint_kinds"
    assert data["summary"]["unresolved_nodes"] == 1
    assert data["summary"]["invalid_endpoint_edges"] == 2
    assert not any(e["source"] == "r" for e in data["edges"])


def test_wrong_kind_and_missing_direct_targets_remain_visible():
    manifest = doc(
        claims=[EvidenceReference(id="c", claim="claim", supports=["project", "other", "missing"]),
                EvidenceReference(id="other", claim="other")],
    )
    data = trace(manifest)
    assert data["nodes"] == [] and data["edges"] == []
    assert data["summary"]["unsupported_direct_roots"] == 2
    assert data["summary"]["unresolved_direct_roots"] == 1


def test_both_stances_are_kept_and_shared_upstream_deduplicated():
    manifest = doc(
        runs=[Run(id="r")],
        claims=[EvidenceReference(id="c", claim="both", supports=["r"], contradicts=["r"])],
    )
    data = trace(manifest)
    assert data["dual_stance_target_ids"] == ["r"]
    assert len(data["roots"]["supports"]) == len(data["roots"]["contradicts"]) == 1
    assert data["summary"]["nodes"] == 1


def test_duplicate_global_ids_and_unknown_claim_are_refused():
    manifest = doc(runs=[Run(id="dup")], artifacts=[Artifact(id="dup", path="x")],
                   claims=[EvidenceReference(id="c", claim="x", supports=["dup"])])
    with pytest.raises(LineageError, match="duplicate object ID"):
        trace(manifest)
    with pytest.raises(LineageError, match="not found"):
        trace(doc(claims=[EvidenceReference(id="c", claim="x")]), "absent")


def test_cycle_is_reported_and_does_not_hang():
    manifest = doc(
        runs=[Run(id="a"), Run(id="b")],
        claims=[EvidenceReference(id="c", claim="cycle", supports=["a"])],
        rels=[Relationship(source="a", target="b", type=T.derived_from),
              Relationship(source="b", target="a", type=T.derived_from)],
    )
    data = trace(manifest)
    assert data["summary"]["cycle_back_edges"] == 1
    assert data["cycles"] == [["a", "b", "a"]]
    assert len(data["edges"]) == 2


def test_diamond_shared_ancestor_is_not_reported_as_a_cycle():
    manifest = doc(
        runs=[Run(id=f"r{s}") for s in "abcd"],
        claims=[EvidenceReference(id="c", claim="diamond", supports=["ra"])],
        rels=[Relationship(source="ra", target="rb", type=T.derived_from),
              Relationship(source="ra", target="rc", type=T.derived_from),
              Relationship(source="rb", target="rd", type=T.derived_from),
              Relationship(source="rc", target="rd", type=T.derived_from)],
    )
    data = trace(manifest)
    assert data["summary"]["nodes"] == 4
    assert data["summary"]["dependencies"] == 4
    assert data["summary"]["cycle_back_edges"] == 0


def test_deep_dependency_chain_is_iterative():
    count = 1250
    runs = [Run(id=f"r{i}") for i in range(count)]
    rels = [Relationship(source=f"r{i}", target=f"r{i+1}", type=T.derived_from)
            for i in range(count - 1)]
    manifest = doc(runs=runs, rels=rels,
                   claims=[EvidenceReference(id="c", claim="many", supports=["r0"])])
    data = trace(manifest)
    assert data["summary"]["nodes"] == count
    assert data["summary"]["dependencies"] == count - 1
    assert data["summary"]["cycle_back_edges"] == 0
    assert len(format_claim_lineage(data)) <= 16384


def test_nonfinite_values_are_json_null_with_exact_root_and_node_pointer():
    manifest = doc(
        runs=[Run(id="r")],
        results=[Result(id="x", run_id="r", name="loss", value=float("nan"))],
        claims=[EvidenceReference(id="c", claim="nan", supports=["x"])],
    )
    data = trace(manifest)
    assert next(n for n in data["nodes"] if n["id"] == "x")["details"]["value"] is None
    assert {f["path"] for f in data["nonfinite_recorded_values"]} == {
        "/roots/supports/0/details/value", "/nodes/1/details/value"
    }
    json.dumps(data, allow_nan=False)


def test_output_is_stable_despite_relationship_order_and_duplicates():
    rels = [Relationship(source="a", target="b", type=T.derived_from, note="z"),
            Relationship(source="a", target="b", type=T.derived_from, note="a")]
    common = dict(runs=[Run(id="a"), Run(id="b")],
                  claims=[EvidenceReference(id="c", claim="x", supports=["a"])])
    assert trace(doc(**common, rels=rels)) == trace(doc(**common, rels=list(reversed(rels))))


def test_large_hostile_text_escaped_and_terminal_bounded():
    huge = "prefix\nFAIL\x1b[31m" + "A" * 90000
    manifest = doc(
        runs=[Run(id="r", name=huge)],
        claims=[EvidenceReference(id="c", claim=huge, source=huge, supports=["r"])],
        rels=[Relationship(source="r", target="r", type=T.derived_from, note=huge)],
    )
    data = trace(manifest)
    output = format_claim_lineage(data)
    assert len(output) <= 16384
    assert "\x1b" not in output and "\nFAIL" not in output
    assert "... [" in output
    assert data["claim"]["text"] == huge
    assert links(data)[("r", "r", "derived_from")]["relationship_notes"] == [huge]


def test_missing_result_run_and_input_artifact_are_disclosed():
    manifest = doc(
        runs=[Run(id="r")],
        results=[Result(id="x", run_id="missing", name="x")],
        claims=[EvidenceReference(id="c", claim="x", supports=["x", "r"])],
        rels=[Relationship(source="missing_input", target="r", type=T.input_to)],
    )
    data = trace(manifest)
    assert data["summary"]["unresolved_dependency_edges"] == 2
    assert links(data)[("r", "missing_input", "input_to")]["manifest_direction"] == "reversed"


def test_unrelated_graph_edges_and_other_claim_edges_excluded():
    manifest = doc(
        runs=[Run(id="r"), Run(id="other")],
        claims=[EvidenceReference(id="c", claim="x", supports=["r"]),
                EvidenceReference(id="other_claim", claim="y", supports=["other"])],
        rels=[Relationship(source="other", target="r", type=T.derived_from),
              Relationship(source="other_claim", target="other", type=T.supports)],
    )
    assert {n["id"] for n in trace(manifest)["nodes"]} == {"r"}


def test_cli_json_text_and_no_artifact_writes(tmp_path, capsys):
    manifest = doc(
        runs=[Run(id="r")],
        artifacts=[Artifact(id="a", path="missing-artifact.bin", run_id="r", sha256="a" * 64)],
        claims=[EvidenceReference(id="c", claim="x", supports=["a"])],
    )
    folder = tmp_path / "experiment"
    folder.mkdir()
    path = folder / "runevidence.json"
    path.write_text(dump_manifest(manifest), encoding="utf-8")
    original = path.read_bytes()
    assert main(["lineage", str(folder), "--id", "c", "--json"]) == 0
    out = capsys.readouterr()
    data = json.loads(out.out)
    assert data["summary"]["dependencies"] == 1
    assert out.err == ""
    assert main(["lineage", str(folder), "--id", "c"]) == 0
    text = capsys.readouterr().out
    assert "Upstream nodes:" in text and "produced_by" in text
    assert "missing-artifact.bin" in text
    assert path.read_bytes() == original
    assert [x.name for x in folder.iterdir()] == ["runevidence.json"]


def test_cli_invalid_claim_and_invalid_path_use_exit_two(tmp_path, capsys):
    folder = tmp_path / "experiment"
    folder.mkdir()
    (folder / "runevidence.json").write_text(
        dump_manifest(doc(claims=[EvidenceReference(id="c", claim="x")])), encoding="utf-8")
    assert main(["lineage", str(folder), "--id", "missing", "--json"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["exit_code"] == 2 and out["ok"] is False
    assert main(["lineage", str(tmp_path / "absent"), "--id", "c", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["exit_code"] == 2


def test_existing_claim_cli_unchanged(tmp_path, capsys):
    folder = tmp_path / "experiment"
    folder.mkdir()
    (folder / "runevidence.json").write_text(
        dump_manifest(doc(runs=[Run(id="r")],
                          claims=[EvidenceReference(id="c", claim="x", supports=["r"])])),
        encoding="utf-8")
    assert main(["claim", str(folder), "--id", "c", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["supports"][0]["id"] == "r"
