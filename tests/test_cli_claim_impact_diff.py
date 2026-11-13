"""D5: compare read-only manifest snapshots and review affected recorded claims."""
import json
from pathlib import Path
import pytest
from runevidence.models import Manifest, Project, Run, Result, Artifact, EvidenceReference, Relationship, RelationshipType as T
from runevidence.run_impact_diff import ImpactDiffError, compare_claim_impact, format_impact_diff
from runevidence.cli import main
from runevidence.manifest import dump_manifest


def m(*, project=None, runs=(), results=(), artifacts=(), claims=(), rels=()):
    return Manifest(project=project or Project(id="p", name="demo"), runs=list(runs),
                    results=list(results), artifacts=list(artifacts),
                    evidence=list(claims), relationships=list(rels))


def compare(before, after):
    return compare_claim_impact(before, after, before_path="before/runevidence.json", after_path="after/runevidence.json")


def got_by_id(result):
    return {c["id"]: c for c in result["claims"]}


def roots(row, snapshot="after", stance="supports"):
    return row[snapshot][stance] if row[snapshot] else []


def sample(value=.8, *, art_hash=None, relations=()):
    return m(runs=[Run(id="r")],
             results=[Result(id="score", run_id="r", name="accuracy", value=value)],
             artifacts=[Artifact(id="a", path="file.dat", run_id="r", sha256=art_hash)],
             claims=[EvidenceReference(id="c", claim="good", supports=["score"])], rels=relations)


def test_same_snapshot_has_no_changes_no_review_claims():
    a = sample(0.9)
    x = compare(a, a.model_copy(deep=True))
    assert x["changes"] == {"objects": [], "relationships": []}
    assert x["claims"] == []
    assert x["summary"]["claims_for_review"] == 0


def test_changed_result_value_surfaces_claim_with_both_snapshot_paths():
    x = compare(sample(.8), sample(.9))
    assert x["summary"]["object_changes"] == 1
    assert x["changes"]["objects"] == [{"id":"score","change":"modified","before_kind":"result","after_kind":"result","changed_fields":["value"]}]
    row = got_by_id(x)["c"]
    assert roots(row, "before")[0]["path"] == ["score"]
    assert roots(row)[0]["path"] == ["score"]
    assert row["direct_change"] is None


def test_changed_artifact_digest_flows_through_run_to_result():
    a0=Artifact(id="data",path="input",sha256="a"*64)
    a1=Artifact(id="data",path="input",sha256="b"*64)
    common=dict(runs=[Run(id="r")],results=[Result(id="s",name="score",run_id="r")],
                claims=[EvidenceReference(id="c",claim="claim",supports=["s"])],
                rels=[Relationship(source="data",target="r",type=T.input_to)])
    x=compare(m(artifacts=[a0],**common),m(artifacts=[a1],**common))
    assert roots(got_by_id(x)["c"])[0]["path"] == ["s","r","data"]


def test_new_input_relation_changes_run_dependency_without_object_change():
    c=dict(runs=[Run(id="r")],artifacts=[Artifact(id="a",path="input")],
           claims=[EvidenceReference(id="c",claim="run",supports=["r"])])
    x=compare(m(**c),m(**c,rels=[Relationship(source="a",target="r",type=T.input_to)]))
    assert x["summary"]["object_changes"] == 0
    assert x["summary"]["relationship_changes"] == 1
    row=got_by_id(x)["c"]
    assert roots(row,"before") == []
    assert roots(row)[0]["changed_id"] == "r"


def test_removed_input_relation_reports_prior_claim_not_after():
    c=dict(runs=[Run(id="r")],artifacts=[Artifact(id="a",path="input")],
           claims=[EvidenceReference(id="c",claim="run",supports=["r"])])
    rel=Relationship(source="a",target="r",type=T.input_to)
    x=compare(m(**c,rels=[rel]),m(**c))
    row=got_by_id(x)["c"]
    assert roots(row,"before")[0]["changed_id"] == "r"
    assert roots(row) == []


def test_changed_dependency_relation_note_marks_source_as_changed():
    common=dict(runs=[Run(id="child"),Run(id="parent")],
                claims=[EvidenceReference(id="c",claim="x",supports=["child"])])
    a=Relationship(source="child",target="parent",type=T.derived_from,note="old")
    b=Relationship(source="child",target="parent",type=T.derived_from,note="new")
    x=compare(m(**common,rels=[a]),m(**common,rels=[b]))
    assert x["summary"]["relationship_changes"] == 2
    assert roots(got_by_id(x)["c"])[0]["path"] == ["child"]


def test_claim_statement_edit_is_direct_even_without_associations():
    a=m(claims=[EvidenceReference(id="c",claim="old")])
    b=m(claims=[EvidenceReference(id="c",claim="new")])
    row=got_by_id(compare(a,b))["c"]
    assert row["direct_change"] == "modified" and row["before"]["text"] == "old"
    assert row["after"]["text"] == "new"


def test_claim_source_edit_is_direct():
    a=m(claims=[EvidenceReference(id="c",claim="same",source="a")])
    b=m(claims=[EvidenceReference(id="c",claim="same",source="b")])
    assert got_by_id(compare(a,b))["c"]["direct_change"] == "modified"


def test_inline_claim_support_replacement_reports_direct_change():
    common=dict(runs=[Run(id="r"),Run(id="s")])
    a=m(**common,claims=[EvidenceReference(id="c",claim="claim",supports=["r"])])
    b=m(**common,claims=[EvidenceReference(id="c",claim="claim",supports=["s"])])
    row=got_by_id(compare(a,b))["c"]
    assert row["direct_change"] == "modified"


def test_explicit_claim_association_change_reports_direct_even_without_other_change():
    common=dict(runs=[Run(id="r")],claims=[EvidenceReference(id="c",claim="same")])
    before=m(**common)
    after=m(**common,rels=[Relationship(source="c",target="r",type=T.supports)])
    row=got_by_id(compare(before,after))["c"]
    assert row["direct_change"] == "association_changed"


def test_invalid_explicit_claim_association_change_is_direct_not_propagated():
    common=dict(runs=[Run(id="r")],claims=[EvidenceReference(id="c",claim="same")])
    after=m(**common,rels=[Relationship(source="c",target="ghost",type=T.supports)])
    x=compare(m(**common),after)
    row=got_by_id(x)["c"]
    assert row["direct_change"] == "association_changed"
    assert x["changes"]["relationships"][0]["status"] == "unresolved"
    assert roots(row) == []


def test_changed_run_parameters_affects_cited_results():
    before=m(runs=[Run(id="r",params={"lr":0.1})],results=[Result(id="s",run_id="r",name="score")],
             claims=[EvidenceReference(id="c",claim="x",supports=["s"])])
    after=m(runs=[Run(id="r",params={"lr":0.2})],results=before.results,claims=before.evidence)
    change=compare(before,after)
    assert change["changes"]["objects"][0]["changed_fields"] == ["params"]
    assert roots(got_by_id(change)["c"])[0]["path"] == ["s","r"]


def test_removed_artifact_reports_old_claim():
    before=m(artifacts=[Artifact(id="a",path="a")],claims=[EvidenceReference(id="c",claim="x",supports=["a"])])
    after=m(claims=before.evidence)
    row=got_by_id(compare(before,after))["c"]
    assert roots(row,"before")[0]["changed_id"] == "a"
    assert roots(row) == []


def test_added_artifact_reports_new_claim():
    after=m(artifacts=[Artifact(id="a",path="a")],claims=[EvidenceReference(id="c",claim="x",supports=["a"])])
    before=m(claims=after.evidence)
    row=got_by_id(compare(before,after))["c"]
    assert roots(row,"before") == []
    assert roots(row)[0]["changed_id"] == "a"


def test_new_and_removed_claim_always_in_review():
    old=m(claims=[EvidenceReference(id="old",claim="old")])
    new=m(claims=[EvidenceReference(id="new",claim="new")])
    x=compare(old,new)
    assert got_by_id(x)["old"]["presence"] == "before_only"
    assert got_by_id(x)["new"]["presence"] == "after_only"
    assert x["summary"]["direct_claim_changes"] == 2


def test_retyped_identifier_does_not_silently_resolve_ambiguity():
    before=m(runs=[Run(id="x")],claims=[EvidenceReference(id="c",claim="x",supports=["x"])])
    after=m(artifacts=[Artifact(id="x",path="x")],claims=before.evidence)
    x=compare(before,after)
    assert x["changes"]["objects"][0]["change"] == "retyped"
    assert roots(got_by_id(x)["c"])[0]["path"] == ["x"]


def test_project_change_is_recorded_but_not_assumed_to_affect_claims():
    before=m(project=Project(id="p",name="old"),claims=[EvidenceReference(id="c",claim="x")])
    after=m(project=Project(id="p",name="new"),claims=before.evidence)
    x=compare(before,after)
    assert x["summary"]["object_changes"] == 1
    assert x["summary"]["claims_for_review"] == 0


def test_duplicate_global_id_in_either_manifest_is_refused():
    dup=m(runs=[Run(id="collision")],artifacts=[Artifact(id="collision",path="a")])
    for old,new in [(dup,m()),(m(),dup)]:
        with pytest.raises(ImpactDiffError,match="duplicate object ID"):
            compare(old,new)


def test_stable_nan_values_not_reported_as_changed_and_nan_to_null_is_changed():
    a=m(runs=[Run(id="r")],results=[Result(id="x",run_id="r",name="x",value=float('nan'))])
    b=m(runs=a.runs,results=[Result(id="x",run_id="r",name="x",value=float('nan'))])
    assert compare(a,b)["summary"]["object_changes"] == 0
    b2=m(runs=a.runs,results=[Result(id="x",run_id="r",name="x",value=None)])
    assert compare(a,b2)["changes"]["objects"][0]["id"] == "x"


def test_order_of_relationships_and_duplicate_repeated_edges_is_semantically_irrelevant():
    rel=Relationship(source="a",target="b",type=T.derived_from,note="note")
    same=dict(runs=[Run(id="a"),Run(id="b")],claims=[EvidenceReference(id="c",claim="x",supports=["a"])])
    a=m(**same,rels=[rel,rel])
    b=m(**same,rels=[rel])
    assert compare(a,b)["summary"]["relationship_changes"] == 0


def test_shortest_path_diamond_is_deterministic_under_reordered_edges():
    rels=[Relationship(source="a",target="b",type=T.derived_from),
          Relationship(source="a",target="c",type=T.derived_from),
          Relationship(source="b",target="d",type=T.derived_from),
          Relationship(source="c",target="d",type=T.derived_from)]
    before=m(runs=[Run(id=s) for s in "abcd"],claims=[EvidenceReference(id="claim",claim="x",supports=["a"])],rels=rels)
    after=m(runs=[Run(id=s,name=("changed" if s=="d" else None)) for s in "abcd"],
            claims=before.evidence,rels=list(reversed(rels)))
    result=compare(before,after)
    assert roots(got_by_id(result)["claim"])[0]["path"] == ["a","b","d"]
    assert len(result["claims"]) == 1


def test_cycle_safe_and_preferred_nearest_changed_anchor():
    rels=[Relationship(source="a",target="b",type=T.derived_from),
          Relationship(source="b",target="a",type=T.derived_from),
          Relationship(source="b",target="c",type=T.derived_from)]
    before=m(runs=[Run(id=s) for s in "abc"],claims=[EvidenceReference(id="claim",claim="x",supports=["a"])],rels=rels)
    after=m(runs=[Run(id="a"),Run(id="b",name="near"),Run(id="c",name="far")],claims=before.evidence,rels=rels)
    row=got_by_id(compare(before,after))["claim"]
    assert roots(row)[0]["path"] == ["a","b"]


def test_invalid_provenance_edges_appear_as_changes_but_do_not_propagate():
    common=dict(runs=[Run(id="r")],artifacts=[Artifact(id="a",path="a")],
                claims=[EvidenceReference(id="c",claim="x",supports=["r"])])
    bad=Relationship(source="r",target="a",type=T.produced_by)
    result=compare(m(**common),m(**common,rels=[bad]))
    assert result["changes"]["relationships"][0]["status"] == "invalid_endpoint_kinds"
    assert result["claims"] == []


def test_large_hostile_claim_text_is_escaped_and_text_output_bounded():
    noisy="line\nFAIL\x1b[31m"+"X"*80000
    a=m(runs=[Run(id="r")],claims=[EvidenceReference(id="c",claim=noisy,supports=["r"])])
    b=m(runs=[Run(id="r",name="changed")],claims=a.evidence)
    result=compare(a,b)
    text=format_impact_diff(result)
    assert len(text)<=16384 and "\x1b" not in text and "\nFAIL" not in text
    assert result["claims"][0]["after"]["text"] == noisy
    assert json.loads(json.dumps(result,allow_nan=False))["claims"][0]["after"]["text"] == noisy


def test_deep_chain_iterative_bounded_no_recursion_error():
    count=1400
    runs=[Run(id=f"r{i}") for i in range(count)]
    rels=[Relationship(source=f"r{i}",target=f"r{i+1}",type=T.derived_from) for i in range(count-1)]
    claim=EvidenceReference(id="c",claim="x",supports=["r0"])
    a=m(runs=runs,claims=[claim],rels=rels)
    new=[Run(id=f"r{i}",name="changed" if i==count-1 else None) for i in range(count)]
    b=m(runs=new,claims=[claim],rels=rels)
    result=compare(a,b)
    assert len(roots(got_by_id(result)["c"])[0]["path"])==count
    assert len(format_impact_diff(result))<=16384


def test_cli_integrates_two_actual_synthetic_files_without_changes(tmp_path,capsys):
    a=sample(.8);b=sample(.9)
    p=tmp_path/"before.json";q=tmp_path/"after.json"
    p.write_text(dump_manifest(a),encoding="utf8")
    q.write_text(dump_manifest(b),encoding="utf8")
    orig1=p.read_bytes();orig2=q.read_bytes()
    assert main(["impact-diff",str(p),str(q),"--json"])==0
    out=capsys.readouterr()
    data=json.loads(out.out)
    assert out.err=="" and data["summary"]["claims_for_review"]==1
    assert main(["impact-diff",str(p),str(q)])==0
    out=capsys.readouterr().out
    assert "Claims needing review: 1" in out
    assert p.read_bytes()==orig1 and q.read_bytes()==orig2
    assert sorted(x.name for x in tmp_path.iterdir())==["after.json","before.json"]


def test_cli_missing_file_exits_two_json(tmp_path,capsys):
    p=tmp_path/"before.json";p.write_text(dump_manifest(sample()),encoding="utf8")
    assert main(["impact-diff",str(p),str(tmp_path/"missing.json"),"--json"])==2
    out=json.loads(capsys.readouterr().out)
    assert out["exit_code"]==2


def test_existing_impact_cli_remains_available(tmp_path,capsys):
    p=tmp_path/"runevidence.json";p.write_text(dump_manifest(sample()),encoding="utf8")
    assert main(["impact",str(p),"--id","score","--json"])==0
    assert json.loads(capsys.readouterr().out)["summary"]["impacted_claims"]==1


def test_claim_inline_support_order_and_repetition_are_semantically_unchanged():
    before=m(runs=[Run(id="r"), Run(id="s")],
             claims=[EvidenceReference(id="c",claim="same",supports=["r","s","r"])])
    after=m(runs=before.runs,
            claims=[EvidenceReference(id="c",claim="same",supports=["s","r"])])
    result=compare(before,after)
    assert result["changes"]["objects"] == []
    assert result["summary"]["claims_for_review"] == 0

# D5R1: genuine nullable-note ordering regression. The Pydantic schema permits
# both None and str for Relationship.note. The original unkeyed tuple sort
# raises TypeError when keys otherwise agree, before an impact report is made.

def test_d5r1_simultaneous_added_none_and_text_notes_are_sorted_and_traced():
    common = dict(runs=[Run(id="a"), Run(id="b")],
                  claims=[EvidenceReference(id="claim", claim="affected", supports=["a"])])
    before = m(**common)
    after = m(**common, rels=[Relationship(source="a", target="b", type=T.derived_from, note="why"),
                              Relationship(source="a", target="b", type=T.derived_from, note=None)])
    data = compare(before, after)
    rows = data["changes"]["relationships"]
    assert [(r["change"], r["note"], r["status"]) for r in rows] == [
        ("added", None, "recorded"), ("added", "why", "recorded")]
    assert data["summary"]["relationship_changes"] == 2
    assert roots(got_by_id(data)["claim"])[0]["path"] == ["a"]


def test_d5r1_simultaneous_removed_none_and_text_notes_are_sorted_and_traced():
    common = dict(runs=[Run(id="a"), Run(id="b")],
                  claims=[EvidenceReference(id="claim", claim="affected", supports=["a"])])
    before = m(**common, rels=[Relationship(source="a", target="b", type=T.derived_from, note=None),
                               Relationship(source="a", target="b", type=T.derived_from, note="why")])
    data = compare(before, m(**common))
    assert [(r["change"], r["note"]) for r in data["changes"]["relationships"]] == [
        ("removed", None), ("removed", "why")]
    assert roots(got_by_id(data)["claim"], snapshot="before")[0]["path"] == ["a"]
    assert roots(got_by_id(data)["claim"], snapshot="after") == []


def test_d5r1_simultaneous_mixed_added_removed_notes_deterministic():
    base = dict(runs=[Run(id="a"), Run(id="b")],
                claims=[EvidenceReference(id="claim", claim="affected", supports=["a"])])
    before_rel = [Relationship(source="a", target="b", type=T.derived_from, note=None),
                  Relationship(source="a", target="b", type=T.derived_from, note="before")]
    after_rel = [Relationship(source="a", target="b", type=T.derived_from, note="after"),
                 Relationship(source="a", target="b", type=T.derived_from, note="")]
    result = compare(m(**base, rels=before_rel), m(**base, rels=after_rel))
    shuffled = compare(m(**base, rels=list(reversed(before_rel))),
                       m(**base, rels=list(reversed(after_rel))))
    assert result == shuffled
    assert [(r["change"], r["note"]) for r in result["changes"]["relationships"]] == [
        ("removed", None), ("added", ""), ("added", "after"), ("removed", "before")]
    assert result["summary"]["relationship_changes"] == 4
    assert result["summary"]["claims_for_review"] == 1


def test_d5r1_null_empty_and_text_notes_are_three_distinct_edge_assertions():
    common = dict(runs=[Run(id="a"), Run(id="b")])
    before = m(**common)
    after = m(**common, rels=[Relationship(source="a", target="b", type=T.derived_from, note=""),
                              Relationship(source="a", target="b", type=T.derived_from, note="x"),
                              Relationship(source="a", target="b", type=T.derived_from, note=None)])
    rows = compare(before, after)["changes"]["relationships"]
    assert [r["note"] for r in rows] == [None, "", "x"]


def test_d5r1_nullable_claim_association_notes_report_direct_claim_change():
    common = dict(runs=[Run(id="r")], claims=[EvidenceReference(id="claim", claim="same")])
    before = m(**common)
    after = m(**common, rels=[Relationship(source="claim", target="r", type=T.supports, note=None),
                              Relationship(source="claim", target="r", type=T.supports, note="source")])
    result = compare(before, after)
    assert [r["note"] for r in result["changes"]["relationships"]] == [None, "source"]
    row = got_by_id(result)["claim"]
    assert row["direct_change"] == "association_changed"
    assert roots(row, "before") == []
    assert roots(row, "after") == []  # association edit is direct, not an object mutation


def test_d5r1_reordered_duplicated_nullable_notes_are_semantically_equal():
    common = dict(runs=[Run(id="a"), Run(id="b")])
    rels = [Relationship(source="a", target="b", type=T.derived_from, note=None),
            Relationship(source="a", target="b", type=T.derived_from, note="text")]
    result = compare(m(**common, rels=rels),
                     m(**common, rels=[rels[1], rels[0], rels[0], rels[1]]))
    assert result["changes"] == {"objects": [], "relationships": []}
    assert result["summary"]["claims_for_review"] == 0


def test_d5r1_cli_mixed_nullable_notes_json_and_terminal_are_read_only(tmp_path, capsys):
    common = dict(runs=[Run(id="a"), Run(id="b")],
                  claims=[EvidenceReference(id="claim", claim="x", supports=["a"])])
    before = m(**common)
    after = m(**common, rels=[Relationship(source="a", target="b", type=T.derived_from, note="note\nESC\x1b[31m"),
                              Relationship(source="a", target="b", type=T.derived_from, note=None)])
    old = tmp_path / "before.json"
    new = tmp_path / "after.json"
    old.write_text(dump_manifest(before), encoding="utf8")
    new.write_text(dump_manifest(after), encoding="utf8")
    original = (old.read_bytes(), new.read_bytes())
    assert main(["impact-diff", str(old), str(new), "--json"]) == 0
    output = capsys.readouterr()
    data = json.loads(output.out)
    assert output.err == ""
    assert [r["note"] for r in data["changes"]["relationships"]] == [None, "note\nESC\x1b[31m"]
    assert data["summary"]["claims_for_review"] == 1
    assert main(["impact-diff", str(old), str(new)]) == 0
    terminal = capsys.readouterr().out
    assert len(terminal) <= 16384 and "Claims needing review: 1" in terminal
    assert "\x1b" not in terminal
    assert (old.read_bytes(), new.read_bytes()) == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["after.json", "before.json"]
