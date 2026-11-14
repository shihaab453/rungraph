"""D6 structural claim coverage queue: real manifests, CLI, provenance semantics."""
import json

import pytest

from runevidence.cli import main
from runevidence.manifest import dump_manifest
from runevidence.models import (
    Artifact, EvidenceReference, Manifest, Project, Relationship,
    RelationshipType as T, Result, Run,
)
from runevidence.run_coverage import CoverageError, claim_coverage, format_claim_coverage


def doc(*, runs=(), artifacts=(), results=(), claims=(), relationships=()):
    return Manifest(project=Project(id="p", name="Test"), runs=list(runs),
                    artifacts=list(artifacts), results=list(results),
                    evidence=list(claims), relationships=list(relationships))


def review(m, **kwargs):
    return claim_coverage(m, manifest_path="test/runevidence.json", **kwargs)


def by_id(data):
    return {row["id"]: row for row in data["claims"]}


def test_valid_support_is_visible_without_structural_flags():
    data = review(doc(runs=[Run(id="r")], claims=[EvidenceReference(id="c", claim="Good", supports=["r"])]))
    assert by_id(data)["c"]["resolved_support_roots"] == 1
    assert by_id(data)["c"]["review_flags"] == []
    assert data["summary"]["claims_without_flags"] == 1
    assert data["summary"]["claims_for_review"] == 0


def test_no_support_and_no_associations_are_not_hidden():
    data = review(doc(claims=[EvidenceReference(id="empty", claim="Untethered")]))
    assert by_id(data)["empty"]["review_flags"] == ["no_resolved_support"]
    assert data["summary"]["flag_counts"]["no_resolved_support"] == 1
    assert data["summary"]["total_claims"] == 1


def test_only_contradictions_get_two_distinct_review_flags():
    data = review(doc(runs=[Run(id="r")], claims=[EvidenceReference(id="c", claim="Rebutted", contradicts=["r"])]))
    row = by_id(data)["c"]
    assert row["resolved_contradiction_roots"] == 1
    assert row["review_flags"] == ["no_resolved_support", "contradictions_recorded"]
    assert "not proof of false claims" in data["note"]


def test_dual_stance_not_resolved_or_suppressed():
    data = review(doc(runs=[Run(id="r")], claims=[EvidenceReference(id="c", claim="Both", supports=["r"], contradicts=["r"])]))
    row = by_id(data)["c"]
    assert row["dual_stance_target_ids"] == ["r"]
    assert row["review_flags"] == ["contradictions_recorded", "dual_stance"]


def test_invalid_and_unresolved_direct_link_details():
    m = doc(runs=[Run(id="r")], claims=[EvidenceReference(id="c", claim="Uncertain", supports=["r", "gone", "p", "other"]),
                                          EvidenceReference(id="other", claim="Other")])
    row = by_id(review(m))["c"]
    assert row["resolved_support_roots"] == 1
    assert row["recorded_support_roots"] == 4
    assert row["unresolved_direct_root_ids"] == ["gone"]
    assert row["unsupported_direct_root_ids"] == ["other", "p"]
    assert row["review_flags"] == ["unresolved_direct_reference", "unsupported_direct_kind"]


def test_invalid_direct_only_is_not_valid_support():
    m = doc(claims=[EvidenceReference(id="c", claim="Invalid", supports=["ghost", "p", "ghost"])])
    row = by_id(review(m))["c"]
    assert row["resolved_support_roots"] == 0
    assert row["recorded_support_roots"] == 2
    assert row["review_flags"] == ["no_resolved_support", "unresolved_direct_reference", "unsupported_direct_kind"]
    assert row["unresolved_direct_root_ids"] == ["ghost"]


def test_missing_producer_and_wrong_kind_upstream_edges_flagged():
    m = doc(artifacts=[Artifact(id="artifact", path="m.bin", run_id="ghost")],
            runs=[Run(id="r")],
            claims=[EvidenceReference(id="c", claim="Artifacts", supports=["artifact"])],
            relationships=[Relationship(source="artifact", target="r", type=T.reported_in)])
    row = by_id(review(m))["c"]
    assert row["review_flags"] == ["unresolved_provenance", "invalid_provenance_kind"]
    assert row["unresolved_dependency_edges"] == 1
    assert row["invalid_dependency_kind_edges"] == 1


def test_explicit_reported_in_and_input_to_are_traced_without_reading_artifact():
    m = doc(runs=[Run(id="r")],
            artifacts=[Artifact(id="file", path="does-not-exist.bin", run_id="unresolved")],
            results=[Result(id="res", run_id="r", name="score")],
            claims=[EvidenceReference(id="c", claim="Performance", supports=["res"])],
            relationships=[Relationship(source="res", target="file", type=T.reported_in),
                           Relationship(source="file", target="r", type=T.input_to)])
    row = by_id(review(m))["c"]
    assert row["upstream_nodes"] >= 3
    assert "unresolved_provenance" in row["review_flags"]
    assert row["resolved_support_roots"] == 1


def test_cycle_and_diamond_shared_parent_not_confused():
    acyclic = doc(runs=[Run(id=i) for i in "abcd"],
                  claims=[EvidenceReference(id="claim", claim="DAG", supports=["a"])],
                  relationships=[Relationship(source="a", target="b", type=T.derived_from),
                                 Relationship(source="a", target="c", type=T.derived_from),
                                 Relationship(source="b", target="d", type=T.derived_from),
                                 Relationship(source="c", target="d", type=T.derived_from)])
    assert "provenance_cycle" not in by_id(review(acyclic))["claim"]["review_flags"]
    cyclic = doc(runs=[Run(id=i) for i in "abc"],
                 claims=[EvidenceReference(id="claim", claim="cycle", supports=["a"])],
                 relationships=[Relationship(source="a", target="b", type=T.derived_from),
                                Relationship(source="b", target="c", type=T.derived_from),
                                Relationship(source="c", target="a", type=T.derived_from)])
    row = by_id(review(cyclic))["claim"]
    assert row["review_flags"] == ["provenance_cycle"]
    assert row["cycles"] == [["a", "b", "c", "a"]]


def test_other_claims_and_unrelated_bad_edges_do_not_contaminate():
    m = doc(runs=[Run(id="a"), Run(id="b")],
            claims=[EvidenceReference(id="c1", claim="first", supports=["a"]),
                    EvidenceReference(id="c2", claim="second", supports=["b"])],
            relationships=[Relationship(source="b", target="missing", type=T.derived_from)])
    rows = by_id(review(m))
    assert rows["c1"]["review_flags"] == []
    assert rows["c2"]["review_flags"] == ["unresolved_provenance"]


def test_relationship_only_support_and_contradiction_respected():
    m = doc(runs=[Run(id="a"), Run(id="b")],
            claims=[EvidenceReference(id="c", claim="links")],
            relationships=[Relationship(source="c", target="a", type=T.supports),
                           Relationship(source="c", target="b", type=T.contradicts)])
    row = by_id(review(m))["c"]
    assert row["resolved_support_roots"] == row["resolved_contradiction_roots"] == 1
    assert row["review_flags"] == ["contradictions_recorded"]


def test_only_flagged_filters_rows_but_not_global_summary():
    m = doc(runs=[Run(id="r")], claims=[EvidenceReference(id="safe", claim="safe", supports=["r"]),
                                         EvidenceReference(id="review", claim="review")])
    full = review(m)
    filtered = review(m, only_flagged=True)
    assert full["summary"]["total_claims"] == filtered["summary"]["total_claims"] == 2
    assert full["summary"]["claims_for_review"] == filtered["summary"]["claims_for_review"] == 1
    assert filtered["summary"]["displayed_claims"] == 1
    assert [x["id"] for x in filtered["claims"]] == ["review"]


def test_deterministic_on_claim_and_relationship_reordering_and_duplicates():
    claims = [EvidenceReference(id="z", claim="Z", supports=["r", "r"]),
              EvidenceReference(id="a", claim="A", supports=["r"])]
    rels = [Relationship(source="z", target="r", type=T.supports, note="n"),
            Relationship(source="z", target="r", type=T.supports, note="n")]
    before = doc(runs=[Run(id="r")], claims=claims, relationships=rels)
    after = doc(runs=[Run(id="r")], claims=list(reversed(claims)), relationships=list(reversed(rels)))
    assert review(before) == review(after)


def test_empty_manifest_no_claims_is_supported():
    data = review(doc())
    assert data["claims"] == []
    assert data["summary"]["total_claims"] == 0
    assert "Recorded claims: 0" in format_claim_coverage(data)


def test_duplicate_ids_rejected_even_without_claims():
    m = doc(runs=[Run(id="same")], artifacts=[Artifact(id="same", path="a")])
    with pytest.raises(CoverageError, match="duplicate object ID"):
        review(m)


def test_huge_hostile_text_is_escaped_and_capped_and_json_complete():
    hostile = "line\nFAIL\r\x1b[31m" + "X" * 100000
    m = doc(runs=[Run(id="r")], claims=[EvidenceReference(id="c", claim=hostile, supports=["r"])])
    data = review(m)
    terminal = format_claim_coverage(data)
    assert len(terminal) <= 16384
    assert "\x1b" not in terminal and "\nFAIL" not in terminal
    assert "chars omitted" in terminal
    assert data["claims"][0]["text"] == hostile
    assert json.loads(json.dumps(data, allow_nan=False))["claims"][0]["text"] == hostile


def test_many_claims_text_truncation_is_clear_and_footer_retained():
    m = doc(claims=[EvidenceReference(id=f"c{i:04d}", claim="long" * 200) for i in range(110)])
    data = review(m)
    terminal = format_claim_coverage(data)
    assert data["summary"]["total_claims"] == 110
    assert len(terminal) <= 16384
    assert "use --json" in terminal
    assert terminal.endswith("Recorded assertions only. Run 'runevidence validate' separately before trusting evidence.")


def test_deep_cycle_safe_chain_uses_iterative_graph_traversal():
    n = 1150
    m = doc(runs=[Run(id=f"r{i}") for i in range(n)],
            claims=[EvidenceReference(id="c", claim="deep", supports=["r0"])],
            relationships=[Relationship(source=f"r{i}", target=f"r{i+1}", type=T.derived_from)
                           for i in range(n-1)])
    row = by_id(review(m))["c"]
    assert row["upstream_nodes"] == n
    assert row["review_flags"] == []


def test_cli_json_and_text_read_only_without_artifact_access(tmp_path, capsys):
    m = doc(runs=[Run(id="r")], artifacts=[Artifact(id="a", path="absent.bin", run_id="r")],
            claims=[EvidenceReference(id="c", claim="Has file", supports=["a"])])
    folder = tmp_path / "experiment"
    folder.mkdir()
    mf = folder / "runevidence.json"
    mf.write_text(dump_manifest(m), encoding="utf8")
    before = mf.read_bytes()
    assert main(["coverage", str(folder), "--json"]) == 0
    capture = capsys.readouterr()
    data = json.loads(capture.out)
    assert capture.err == ""
    assert data["claims"][0]["review_flags"] == []
    assert main(["coverage", str(folder), "--only-flagged", "--json"]) == 0
    filtered = json.loads(capsys.readouterr().out)
    assert filtered["claims"] == [] and filtered["summary"]["total_claims"] == 1
    assert main(["coverage", str(folder)]) == 0
    assert "Recorded claims:" in capsys.readouterr().out
    assert mf.read_bytes() == before
    assert [p.name for p in folder.iterdir()] == ["runevidence.json"]


def test_cli_error_missing_manifest_and_global_id_collision(tmp_path, capsys):
    assert main(["coverage", str(tmp_path / "missing"), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["exit_code"] == 2
    m = doc(runs=[Run(id="r")], artifacts=[Artifact(id="r", path="a")])
    path = tmp_path / "runevidence.json"
    path.write_text(dump_manifest(m), encoding="utf8")
    assert main(["coverage", str(path), "--json"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False and "duplicate object ID" in result["error"]


def test_existing_impact_diff_and_lineage_commands_preserved(tmp_path, capsys):
    m = doc(runs=[Run(id="r")], claims=[EvidenceReference(id="c", claim="X", supports=["r"])])
    path = tmp_path / "runevidence.json"
    path.write_text(dump_manifest(m), encoding="utf8")
    assert main(["lineage", str(path), "--id", "c", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["nodes"] == 1
    assert main(["impact-diff", str(path), str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["claims_for_review"] == 0
