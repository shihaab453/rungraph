"""D8: unified read-only recorded claim review triage across two manifests."""
import json
from pathlib import Path
import random

import pytest

from runevidence.cli import main
from runevidence.manifest import dump_manifest
from runevidence.models import (
    Manifest, Project, Run, Artifact, Result, EvidenceReference, Relationship,
    RelationshipType as T,
)
from runevidence.run_review_diff import ReviewDiffError, compare_claim_review, format_claim_review
from runevidence.run_impact_diff import compare_claim_impact
from runevidence.run_coverage_diff import compare_claim_coverage


def doc(*, runs=(), arts=(), results=(), claims=(), rels=()):
    return Manifest(project=Project(id="p", name="demo"), runs=list(runs),
                    artifacts=list(arts), results=list(results),
                    evidence=list(claims), relationships=list(rels))


def claim(ident="c", *, support=(), contra=(), text="claim"):
    return EvidenceReference(id=ident, claim=text, supports=list(support),
                             contradicts=list(contra))


def review(a, b, **kw):
    return compare_claim_review(a, b, before_path="before.json", after_path="after.json", **kw)


def lookup(report):
    return {row["id"]: row for row in report["claims"]}


def test_same_snapshot_clean_queue_even_if_existing_flags():
    x = doc(claims=[claim()])
    data = review(x, x.model_copy(deep=True))
    assert data["claims"] == [] and data["summary"]["candidates_total"] == 0
    assert data["summary"]["structural_coverage_diff"]["before_claims_for_review"] == 1
    assert data["summary"]["record_impact"]["claims_for_review"] == 0


def test_recorded_result_change_appears_without_any_new_structural_flag():
    old = doc(runs=[Run(id="r")], results=[Result(id="x", run_id="r", name="score", value=.2)],
              claims=[claim(support=["x"])])
    new = doc(runs=old.runs, results=[Result(id="x", run_id="r", name="score", value=.8)],
              claims=old.evidence)
    data = review(old, new)
    row = lookup(data)["c"]
    assert row["review_categories"] == ["dependency_impact"]
    assert row["record_impact"]["after"]["supports"][0]["path"] == ["x"]
    assert row["structural_transition"] is None
    assert data["recorded_changes"]["objects"][0]["changed_fields"] == ["value"]
    assert review(old, new, only_new_flags=True)["claims"] == []


def test_new_bad_support_generates_structural_regression_and_direct_change():
    old = doc(runs=[Run(id="r")], claims=[claim(support=["r"])])
    new = doc(runs=old.runs, claims=[claim(support=["ghost"])])
    row = lookup(review(old, new))["c"]
    assert row["review_categories"][:2] == ["new_structural_flags", "direct_claim_change"]
    assert row["introduced_structural_flags"] == ["no_resolved_support", "unresolved_direct_reference"]
    assert row["record_impact"]["direct_change"] == "modified"


def test_added_clean_claim_is_still_visible_but_not_a_new_flag():
    old = doc(runs=[Run(id="r")])
    new = doc(runs=old.runs, claims=[claim(support=["r"])])
    row = lookup(review(old, new))["c"]
    assert row["presence"] == "after_only"
    assert row["review_categories"] == ["direct_claim_change", "claim_added"]
    assert row["introduced_structural_flags"] == []
    assert review(old, new, only_new_flags=True)["claims"] == []


def test_added_flagged_claim_is_new_concern_in_filtered_view():
    row = lookup(review(doc(), doc(claims=[claim()]), only_new_flags=True))["c"]
    assert row["presence"] == "after_only"
    assert row["review_categories"] == ["new_structural_flags", "direct_claim_change", "claim_added"]
    assert row["structural_transition"]["status"] == "added"


def test_removed_flagged_claim_never_counted_as_improvement():
    row = lookup(review(doc(claims=[claim()]), doc()))["c"]
    assert row["presence"] == "before_only"
    assert "claim_removed" in row["review_categories"]
    assert "structural_flags_cleared" not in row["review_categories"]
    assert row["cleared_structural_flags"] == []
    assert review(doc(claims=[claim()]), doc(), only_new_flags=True)["claims"] == []


def test_structural_improvement_preserves_independent_record_change_reasons():
    old = doc(runs=[Run(id="r")], claims=[claim()])
    new = doc(runs=old.runs, claims=[claim(support=["r"])])
    row = lookup(review(old, new))["c"]
    assert row["cleared_structural_flags"] == ["no_resolved_support"]
    assert "direct_claim_change" in row["review_categories"]
    assert "structural_flags_cleared" in row["review_categories"]
    assert row["introduced_structural_flags"] == []


def test_simultaneous_new_and_cleared_flags_is_not_lost():
    old = doc(runs=[Run(id="r")], claims=[claim(support=["lost"])])
    new = doc(runs=old.runs, claims=[claim(support=["r"], contra=["r"])])
    row = lookup(review(old, new))["c"]
    assert row["introduced_structural_flags"] == ["contradictions_recorded", "dual_stance"]
    assert row["cleared_structural_flags"] == ["no_resolved_support", "unresolved_direct_reference"]
    assert "structural_flags_cleared" in row["review_categories"]


def test_relationship_only_note_change_surfaces_dependency_impact():
    old = doc(runs=[Run(id="a"), Run(id="b")], claims=[claim(support=["a"])],
              rels=[Relationship(source="a", target="b", type=T.derived_from, note=None)])
    new = doc(runs=old.runs, claims=old.evidence,
              rels=[Relationship(source="a", target="b", type=T.derived_from, note="new")])
    data = review(old, new)
    assert data["summary"]["record_impact"]["relationship_changes"] == 2
    assert data["summary"]["category_counts"]["dependency_impact"] == 1
    assert [r["note"] for r in data["recorded_changes"]["relationships"]] == [None, "new"]


def test_invalid_provenance_edges_produce_flags_without_false_dependency_impact():
    old = doc(runs=[Run(id="r")], arts=[Artifact(id="a", path="a")],
              claims=[claim(support=["r"])])
    new = doc(runs=old.runs, arts=old.artifacts, claims=old.evidence,
              rels=[Relationship(source="r", target="a", type=T.produced_by)])
    row = lookup(review(old, new))["c"]
    assert row["introduced_structural_flags"] == ["invalid_provenance_kind"]
    assert row["record_impact"] is None  # invalid-kind links do not propagate


def test_cycle_and_claim_impact_safely_combine():
    old = doc(runs=[Run(id="a"), Run(id="b")], claims=[claim(support=["a"])])
    new = doc(runs=old.runs, claims=old.evidence,
              rels=[Relationship(source="a", target="b", type=T.derived_from),
                    Relationship(source="b", target="a", type=T.derived_from)])
    row = lookup(review(old, new))["c"]
    assert row["introduced_structural_flags"] == ["provenance_cycle"]
    assert "dependency_impact" in row["review_categories"]


def test_deterministic_sorted_tiers_and_manifest_order_independence():
    runs = [Run(id="r"), Run(id="s")]
    old = doc(runs=runs, claims=[claim("b", support=["r"]), claim("a", support=["s"])])
    new = doc(runs=list(reversed(runs)),
              claims=[claim("a", support=["s"], contra=["s"]), claim("b", support=["r"], text="changed")])
    data = review(old, new)
    assert [row["id"] for row in data["claims"]] == ["a", "b"]
    rev = review(doc(runs=list(reversed(runs)), claims=list(reversed(old.evidence))), new)
    assert data == rev


def test_focus_only_new_flags_filters_rows_not_global_counts():
    old = doc(runs=[Run(id="r")], claims=[claim("a", support=["r"]), claim("b")])
    new = doc(runs=[Run(id="r", name="updated")],
              claims=[claim("a"), claim("b", support=["r"])])
    full = review(old, new)
    filt = review(old, new, only_new_flags=True)
    assert set(lookup(full)) == {"a", "b"}
    assert set(lookup(filt)) == {"a"}
    assert filt["summary"]["category_counts"] == full["summary"]["category_counts"]
    assert filt["summary"]["candidates_total"] == full["summary"]["candidates_total"]
    assert filt["summary"]["displayed_claims"] == 1


def test_project_only_change_is_not_falsely_a_claim_review():
    before = doc(claims=[claim()])
    after = before.model_copy(deep=True)
    after.project.name = "changed"
    assert review(before, after)["claims"] == []


def test_id_retype_and_ambiguous_duplicate_id_behaviour():
    old = doc(runs=[Run(id="x")], claims=[claim(support=["x"])])
    new = doc(arts=[Artifact(id="x", path="x")], claims=old.evidence)
    assert "c" in lookup(review(old, new))
    bad = doc(runs=[Run(id="same")], arts=[Artifact(id="same", path="x")])
    for a, b in ((bad, doc()), (doc(), bad)):
        with pytest.raises(ReviewDiffError, match="duplicate object ID"):
            review(a, b)


def test_nonfinite_result_is_json_safe_while_source_engine_preserves_semantics():
    old = doc(runs=[Run(id="r")], results=[Result(id="x", run_id="r", name="x", value=float("nan"))],
              claims=[claim(support=["x"])])
    new = doc(runs=old.runs, results=[Result(id="x", run_id="r", name="x", value=float("inf"))],
              claims=old.evidence)
    result = review(old, new)
    json.dumps(result, allow_nan=False)
    assert result["recorded_changes"]["objects"][0]["changed_fields"] == ["value"]
    assert result["summary"]["record_impact"]["claims_for_review"] == 1


def test_long_malicious_claim_text_is_escaped_and_bounded():
    huge = "line\nERROR\x1b[31m" + "Z" * 85000
    old = doc(runs=[Run(id="r")], claims=[claim(text=huge, support=["r"])])
    new = doc(runs=[Run(id="r", name="changed")], claims=old.evidence)
    result = review(old, new)
    text = format_claim_review(result)
    assert len(text) <= 16384 and "\x1b" not in text and "\nERROR" not in text
    assert result["claims"][0]["record_impact"]["after"]["text"] == huge
    assert text.endswith("Recorded review suggestions only. Run 'runevidence validate' separately.")


def test_many_claims_output_caps_without_mutating_json():
    runs = [Run(id="r")]
    old = doc(runs=runs, claims=[claim(f"c{i:03d}", support=["r"], text="Z"*1000) for i in range(80)])
    new = doc(runs=[Run(id="r", name="changed")], claims=old.evidence)
    report = review(old, new)
    text = format_claim_review(report)
    assert len(report["claims"]) == 80
    assert len(text) <= 16384 and "use --json" in text


def test_cli_readonly_combines_record_and_structural_changes(tmp_path, capsys):
    old = doc(runs=[Run(id="r")], claims=[claim(support=["r"])])
    new = doc(runs=old.runs, claims=[claim()])
    p, q = tmp_path / "before.json", tmp_path / "after.json"
    p.write_text(dump_manifest(old), encoding="utf8")
    q.write_text(dump_manifest(new), encoding="utf8")
    originally = (p.read_bytes(), q.read_bytes())
    assert main(["review-diff", str(p), str(q), "--json"]) == 0
    output = capsys.readouterr()
    data = json.loads(output.out)
    assert output.err == ""
    assert data["summary"]["candidates_total"] == 1
    assert lookup(data)["c"]["introduced_structural_flags"] == ["no_resolved_support"]
    assert main(["review-diff", str(p), str(q), "--only-new-flags"]) == 0
    text = capsys.readouterr().out
    assert "Claims suggested for review: 1; displayed: 1" in text
    assert (p.read_bytes(), q.read_bytes()) == originally
    assert [x.name for x in sorted(tmp_path.iterdir())] == ["after.json", "before.json"]


def test_cli_bad_manifest_exits_two_and_existing_commands_preserved(tmp_path, capsys):
    p = tmp_path / "valid.json"
    p.write_text(dump_manifest(doc(claims=[claim()])), encoding="utf8")
    assert main(["review-diff", str(p), str(tmp_path / "missing"), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["exit_code"] == 2
    assert main(["coverage-diff", str(p), str(p), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["changed_claims"] == 0
    assert main(["impact-diff", str(p), str(p), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["claims_for_review"] == 0


def test_random_independent_union_oracle_small_manifest_snapshots():
    rng = random.Random(123741)
    run_ids = [f"r{i}" for i in range(5)]
    for _ in range(80):
        def make():
            runs = [Run(id=ident, name=("modified" if rng.random() < .25 else None)) for ident in run_ids]
            claims = []
            for i in range(6):
                if rng.random() < .16:
                    continue
                supports = [ident for ident in run_ids if rng.random() < .2]
                contradicts = [ident for ident in run_ids if rng.random() < .14]
                claims.append(claim(f"c{i}", support=supports, contra=contradicts))
            return doc(runs=runs, claims=claims)
        a, b = make(), make()
        d5 = compare_claim_impact(a, b, before_path="before.json", after_path="after.json")
        d7 = compare_claim_coverage(a, b, before_path="before.json", after_path="after.json")
        unified = review(a, b)
        d5_ids = {row["id"] for row in d5["claims"]}
        d7_ids = {row["id"] for row in d7["claims"]}
        assert set(lookup(unified)) == d5_ids | d7_ids
        assert len(unified["claims"]) == len(d5_ids | d7_ids)
        focused = review(a, b, only_new_flags=True)
        new_ids = {row["id"] for row in d7["claims"] if row["new_flags"]}
        assert set(lookup(focused)) == new_ids
