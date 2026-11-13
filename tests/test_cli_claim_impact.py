"""D4 reverse recorded dependency impact: real models, CLI, read-only semantics."""
import json

import pytest

from runevidence.cli import main
from runevidence.manifest import dump_manifest
from runevidence.models import Artifact, EvidenceReference, Manifest, Project, Relationship, RelationshipType as T, Result, Run
from runevidence.run_impact import ImpactError, claim_impact, format_claim_impact


def doc(*, runs=(), artifacts=(), results=(), claims=(), rels=()):
    return Manifest(project=Project(id="project", name="Demo"), runs=list(runs),
                    artifacts=list(artifacts), results=list(results),
                    evidence=list(claims), relationships=list(rels))


def impact(m, ident):
    return claim_impact(m, ident, manifest_path="synthetic/runevidence.json")


def by_id(result):
    return {row["id"]: row for row in result["claims"]}


def test_input_artifact_impacts_downstream_run_result_and_claim():
    m = doc(runs=[Run(id="train"), Run(id="prep")],
            artifacts=[Artifact(id="dataset", path="data", run_id="prep"),
                       Artifact(id="metrics", path="metrics.json", run_id="train")],
            results=[Result(id="acc", run_id="train", name="acc", artifact_id="metrics", value=.9)],
            claims=[EvidenceReference(id="c", claim="accurate", supports=["acc"])],
            rels=[Relationship(source="dataset", target="train", type=T.input_to)])
    result = impact(m, "dataset")
    assert result["summary"]["impacted_claims"] == 1
    entry = by_id(result)["c"]["supports"][0]
    assert entry["path"] == ["acc", "train", "dataset"]
    assert [step["type"] for step in entry["steps"]] == ["result_of", "input_to"]
    assert entry["steps"][-1]["manifest_direction"] == "reversed"


def test_metrics_artifact_affects_result_through_reported_in():
    m = doc(runs=[Run(id="r")], artifacts=[Artifact(id="metrics", path="m.json")],
            results=[Result(id="res", run_id="r", name="score", artifact_id="metrics")],
            claims=[EvidenceReference(id="c", claim="score", supports=["res"])])
    result = impact(m, "metrics")
    assert by_id(result)["c"]["supports"][0]["path"] == ["res", "metrics"]


def test_explicit_reported_in_alternative_source_affects_result():
    m = doc(runs=[Run(id="r")], artifacts=[Artifact(id="a", path="a"), Artifact(id="b", path="b")],
            results=[Result(id="x", run_id="r", name="x", artifact_id="a")],
            claims=[EvidenceReference(id="c", claim="x", supports=["x"])],
            rels=[Relationship(source="x", target="b", type=T.reported_in)])
    assert by_id(impact(m, "b"))["c"]["supports"][0]["path"] == ["x", "b"]


def test_producer_run_impacts_claim_on_artifact_it_produced():
    m = doc(runs=[Run(id="r")], artifacts=[Artifact(id="a", path="a", run_id="r")],
            claims=[EvidenceReference(id="c", claim="file", supports=["a"])])
    assert by_id(impact(m, "r"))["c"]["supports"][0]["path"] == ["a", "r"]


def test_dependency_direction_prevents_downstream_query_infecting_input_claim():
    m = doc(runs=[Run(id="consumer")], artifacts=[Artifact(id="a", path="a")],
            claims=[EvidenceReference(id="c", claim="input", supports=["a"])],
            rels=[Relationship(source="a", target="consumer", type=T.input_to)])
    assert impact(m, "consumer")["claims"] == []
    assert impact(m, "a")["summary"]["impacted_claims"] == 1


def test_run_does_not_assume_every_output_was_a_direct_claim_root():
    m = doc(runs=[Run(id="r")], artifacts=[Artifact(id="output", path="out", run_id="r"),
                                           Artifact(id="input", path="in")],
            claims=[EvidenceReference(id="c", claim="run", supports=["r"])],
            rels=[Relationship(source="input", target="r", type=T.input_to)])
    assert impact(m, "output")["summary"]["impacted_claims"] == 0
    assert by_id(impact(m, "input"))["c"]["supports"][0]["path"] == ["r", "input"]


def test_two_stances_distinct_roots_both_reported():
    m = doc(runs=[Run(id="r")],
            results=[Result(id="a", run_id="r", name="a"), Result(id="b", run_id="r", name="b")],
            claims=[EvidenceReference(id="c", claim="both", supports=["a"], contradicts=["b"])])
    result = impact(m, "r")
    assert result["summary"]["both_stances_claims"] == 1
    row = by_id(result)["c"]
    assert [p["root_id"] for p in row["supports"]] == ["a"]
    assert [p["root_id"] for p in row["contradicts"]] == ["b"]


def test_same_root_multiple_spelling_and_duplicate_notes_deduplicated():
    m = doc(runs=[Run(id="r")], claims=[EvidenceReference(id="c", claim="same", supports=["r", "r"])],
            rels=[Relationship(source="c", target="r", type=T.supports, note="x"),
                  Relationship(source="c", target="r", type=T.supports, note="x")])
    row = by_id(impact(m, "r"))["c"]
    assert len(row["supports"]) == 1 and row["supports"][0]["path"] == ["r"]


def test_relationship_only_claim_link_is_discovered():
    m = doc(runs=[Run(id="r")], claims=[EvidenceReference(id="c", claim="typed")],
            rels=[Relationship(source="c", target="r", type=T.contradicts)])
    row = by_id(impact(m, "r"))["c"]
    assert not row["supports"] and row["contradicts"][0]["root_id"] == "r"


def test_invalid_and_dangling_dependency_edges_excluded_not_followed():
    m = doc(runs=[Run(id="r")], artifacts=[Artifact(id="a", path="a")],
            claims=[EvidenceReference(id="c", claim="claim", supports=["r"])],
            rels=[Relationship(source="r", target="a", type=T.produced_by),
                  Relationship(source="r", target="missing", type=T.derived_from)])
    result = impact(m, "a")
    assert result["claims"] == []
    assert result["summary"]["ignored_invalid_kind_links"] == 1
    assert result["summary"]["ignored_unresolved_links"] == 1


def test_missing_or_invalid_direct_claim_targets_do_not_propagate():
    m = doc(runs=[Run(id="r")], claims=[EvidenceReference(id="c", claim="x", supports=["project", "absent", "r"])])
    result = impact(m, "r")
    assert by_id(result)["c"]["supports"][0]["root_id"] == "r"
    assert len(by_id(result)["c"]["supports"]) == 1


def test_cycles_terminate_with_one_shortest_path():
    m = doc(runs=[Run(id="a"), Run(id="b"), Run(id="c")],
            claims=[EvidenceReference(id="claim", claim="x", supports=["a"])],
            rels=[Relationship(source="a", target="b", type=T.derived_from),
                  Relationship(source="b", target="a", type=T.derived_from),
                  Relationship(source="b", target="c", type=T.derived_from)])
    result = impact(m, "c")
    assert by_id(result)["claim"]["supports"][0]["path"] == ["a", "b", "c"]


def test_diamond_chooses_stable_shortest_path_and_real_graph():
    rels = [Relationship(source="a", target="b", type=T.derived_from),
            Relationship(source="a", target="c", type=T.derived_from),
            Relationship(source="b", target="d", type=T.derived_from),
            Relationship(source="c", target="d", type=T.derived_from)]
    common = dict(runs=[Run(id=s) for s in "abcd"],
                  claims=[EvidenceReference(id="claim", claim="diamond", supports=["a"])])
    a = impact(doc(**common, rels=rels), "d")
    b = impact(doc(**common, rels=list(reversed(rels))), "d")
    assert a == b
    assert by_id(a)["claim"]["supports"][0]["path"] == ["a", "b", "d"]
    assert a["summary"]["reachable_objects"] == 4


def test_deep_chain_iterative_avoids_recursion_error():
    n = 1400
    m = doc(runs=[Run(id=f"r{i}") for i in range(n)],
            claims=[EvidenceReference(id="claim", claim="long", supports=["r0"])],
            rels=[Relationship(source=f"r{i}", target=f"r{i+1}", type=T.derived_from)
                  for i in range(n-1)])
    result = impact(m, f"r{n-1}")
    assert len(by_id(result)["claim"]["supports"][0]["path"]) == n
    assert len(format_claim_impact(result)) <= 16384


def test_unknown_target_wrong_kind_and_duplicate_ids_refused():
    m = doc(runs=[Run(id="r")], claims=[EvidenceReference(id="c", claim="x")])
    for ident in ("unknown", "c", "project"):
        with pytest.raises(ImpactError, match="not found"):
            impact(m, ident)
    dup = doc(runs=[Run(id="r")], artifacts=[Artifact(id="r", path="a")],
              claims=[EvidenceReference(id="c", claim="x")])
    with pytest.raises(ImpactError, match="duplicate object ID"):
        impact(dup, "r")


def test_large_hostile_text_is_escaped_and_bounded_json_complete():
    huge = "unsafe\nFAIL\r\x1b[31m" + "Z" * 100000
    m = doc(runs=[Run(id="r")], claims=[EvidenceReference(id="c", claim=huge, supports=["r"])])
    result = impact(m, "r")
    terminal = format_claim_impact(result)
    assert len(terminal) <= 16384
    assert "\x1b" not in terminal and "\nFAIL" not in terminal
    assert result["claims"][0]["text"] == huge
    assert json.loads(json.dumps(result))["claims"][0]["text"] == huge


def test_nonfinite_query_result_sanitized_and_pointer_exact():
    m = doc(runs=[Run(id="r")], results=[Result(id="x", run_id="r", name="n", value=float("inf"))],
            claims=[EvidenceReference(id="c", claim="x", supports=["x"])])
    result = impact(m, "x")
    assert result["target"]["details"]["value"] is None
    assert result["nonfinite_recorded_values"] == [{"path": "/target/details/value", "kind": "Infinity"}]
    json.dumps(result, allow_nan=False)


def test_cli_json_and_text_read_only_even_with_missing_artifact(tmp_path, capsys):
    m = doc(runs=[Run(id="r")], artifacts=[Artifact(id="a", path="ghost.bin", run_id="r")],
            claims=[EvidenceReference(id="c", claim="x", supports=["a"])])
    folder = tmp_path / "experiment"
    folder.mkdir()
    manifest = folder / "runevidence.json"
    manifest.write_text(dump_manifest(m), encoding="utf-8")
    before = manifest.read_bytes()
    assert main(["impact", str(folder), "--id", "r", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["summary"]["impacted_claims"] == 1
    assert main(["impact", str(folder), "--id", "r"]) == 0
    assert "potentially affected" in capsys.readouterr().out.lower()
    assert manifest.read_bytes() == before
    assert sorted(p.name for p in folder.iterdir()) == ["runevidence.json"]


def test_cli_bad_id_or_missing_manifest_exit_two(tmp_path, capsys):
    folder = tmp_path / "experiment"
    folder.mkdir()
    (folder / "runevidence.json").write_text(dump_manifest(doc(runs=[Run(id="r")])), encoding="utf-8")
    assert main(["impact", str(folder), "--id", "missing", "--json"]) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and data["exit_code"] == 2
    assert main(["impact", str(tmp_path / "not-there"), "--id", "r", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["exit_code"] == 2


def test_other_claim_and_unrelated_edges_not_leaked():
    m = doc(runs=[Run(id="r1"), Run(id="r2")],
            claims=[EvidenceReference(id="c1", claim="one", supports=["r1"]),
                    EvidenceReference(id="c2", claim="two", supports=["r2"])],
            rels=[Relationship(source="r2", target="r1", type=T.derived_from)])
    assert [c["id"] for c in impact(m, "r2")["claims"]] == ["c2"]
    assert [c["id"] for c in impact(m, "r1")["claims"]] == ["c1", "c2"]
