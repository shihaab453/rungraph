"""D7: compare structural coverage across recorded manifests, read-only."""
import json
import random

import pytest

from runevidence.cli import main
from runevidence.manifest import dump_manifest
from runevidence.models import Artifact, EvidenceReference, Manifest, Project, Relationship, RelationshipType as T, Result, Run
from runevidence.run_coverage import claim_coverage
from runevidence.run_coverage_diff import CoverageDiffError, compare_claim_coverage, format_coverage_diff


def doc(*, runs=(), artifacts=(), results=(), claims=(), rels=(), project=None):
    return Manifest(project=project or Project(id="p", name="project"), runs=list(runs),
                    artifacts=list(artifacts), results=list(results),
                    evidence=list(claims), relationships=list(rels))


def diff(before, after, **kwargs):
    return compare_claim_coverage(before, after, before_path="old/runevidence.json",
                                  after_path="new/runevidence.json", **kwargs)


def rows(report):
    return {r["id"]: r for r in report["claims"]}


def c(*, ident="c", support=(), contra=(), text="claim"):
    return EvidenceReference(id=ident, claim=text, supports=list(support), contradicts=list(contra))


def test_unchanged_manifest_is_empty_even_with_preexisting_flags():
    m = doc(claims=[c()])
    result = diff(m, m.model_copy(deep=True))
    assert result["claims"] == []
    assert result["summary"]["changed_claims"] == 0
    assert result["summary"]["before_claims_for_review"] == 1
    assert result["summary"]["after_claims_for_review"] == 1


def test_new_unflagged_claim_is_added_not_a_regression():
    earlier = doc(runs=[Run(id="r")])
    later = doc(runs=earlier.runs, claims=[c(support=["r"])])
    report = diff(earlier, later)
    assert rows(report)["c"]["status"] == "added"
    assert rows(report)["c"]["new_flags"] == []
    assert report["summary"]["added_claims"] == 1
    assert diff(earlier, later, only_regressions=True)["claims"] == []


def test_new_flagged_claim_is_added_and_a_regression_candidate():
    report = diff(doc(), doc(claims=[c()]), only_regressions=True)
    assert rows(report)["c"]["new_flags"] == ["no_resolved_support"]
    assert report["summary"]["new_claims_with_flags"] == 1
    assert report["summary"]["surviving_claims_with_new_flags"] == 0
    assert report["summary"]["introduced_flag_counts"]["no_resolved_support"] == 1


def test_removed_flagged_claim_never_counts_as_flags_cleared():
    report = diff(doc(claims=[c()]), doc())
    assert rows(report)["c"]["status"] == "removed"
    assert rows(report)["c"]["new_flags"] == rows(report)["c"]["cleared_flags"] == []
    assert report["summary"]["removed_claims"] == 1
    assert not any(report["summary"]["cleared_flag_counts"].values())
    assert diff(doc(claims=[c()]), doc(), only_regressions=True)["claims"] == []


def test_losing_support_introduces_flag():
    common = dict(runs=[Run(id="r")])
    old = doc(**common, claims=[c(support=["r"])])
    new = doc(**common, claims=[c()])
    row = rows(diff(old, new))["c"]
    assert row["new_flags"] == ["no_resolved_support"]
    assert row["cleared_flags"] == []
    assert row["status"] == "flags_changed"


def test_gaining_support_clears_flag():
    common = dict(runs=[Run(id="r")])
    old = doc(**common, claims=[c()])
    new = doc(**common, claims=[c(support=["r"])])
    row = rows(diff(old, new))["c"]
    assert row["cleared_flags"] == ["no_resolved_support"]
    assert row["new_flags"] == []
    assert diff(old, new, only_regressions=True)["claims"] == []


def test_added_contradiction_and_dual_stance_both_flagged():
    common = dict(runs=[Run(id="r")])
    old = doc(**common, claims=[c(support=["r"])])
    new = doc(**common, claims=[c(support=["r"], contra=["r"])])
    row = rows(diff(old, new))["c"]
    assert row["new_flags"] == ["contradictions_recorded", "dual_stance"]
    assert row["cleared_flags"] == []
    assert row["after"]["dual_stance_target_ids"] == ["r"]


def test_unsupported_and_unresolved_direct_targets_introduced():
    common = dict(runs=[Run(id="r")])
    old = doc(**common, claims=[c(support=["r"])])
    new = doc(**common, claims=[c(support=["r", "missing", "p"])])
    row = rows(diff(old, new))["c"]
    assert row["new_flags"] == ["unresolved_direct_reference", "unsupported_direct_kind"]
    assert row["after"]["unresolved_direct_root_ids"] == ["missing"]


def test_upstream_unresolved_added_and_repaired():
    old = doc(runs=[Run(id="r")], claims=[c(support=["r"])])
    bad = doc(runs=old.runs, claims=old.evidence,
              rels=[Relationship(source="r", target="ghost", type=T.derived_from)])
    first = rows(diff(old, bad))["c"]
    assert first["new_flags"] == ["unresolved_provenance"]
    second = rows(diff(bad, old))["c"]
    assert second["cleared_flags"] == ["unresolved_provenance"]


def test_invalid_upstream_kind_and_cycle_flags():
    base = doc(runs=[Run(id="r"), Run(id="s")], artifacts=[Artifact(id="a", path="a")],
               claims=[c(support=["r"])])
    bad = doc(runs=base.runs, artifacts=base.artifacts, claims=base.evidence,
              rels=[Relationship(source="r", target="a", type=T.produced_by),
                    Relationship(source="r", target="s", type=T.derived_from),
                    Relationship(source="s", target="r", type=T.derived_from)])
    row = rows(diff(base, bad))["c"]
    assert row["new_flags"] == ["invalid_provenance_kind", "provenance_cycle"]


def test_changed_flags_can_be_added_and_cleared_together():
    old = doc(runs=[Run(id="r")], claims=[c(support=["missing"])])
    new = doc(runs=old.runs, claims=[c(support=["r"], contra=["r"])])
    row = rows(diff(old, new))["c"]
    assert row["new_flags"] == ["contradictions_recorded", "dual_stance"]
    assert row["cleared_flags"] == ["no_resolved_support", "unresolved_direct_reference"]
    assert diff(old, new, only_regressions=True)["summary"]["surviving_claims_with_cleared_flags"] == 1


def test_only_regressions_preserves_unfiltered_global_summary():
    old = doc(runs=[Run(id="r")], claims=[c(ident="improve"), c(ident="worsen", support=["r"])])
    new = doc(runs=old.runs, claims=[c(ident="improve", support=["r"]), c(ident="worsen")])
    full = diff(old, new)
    filtered = diff(old, new, only_regressions=True)
    assert [x["id"] for x in full["claims"]] == ["improve", "worsen"]
    assert [x["id"] for x in filtered["claims"]] == ["worsen"]
    assert full["summary"]["changed_claims"] == filtered["summary"]["changed_claims"] == 2
    assert filtered["summary"]["displayed_claims"] == 1


def test_reorder_and_duplicate_inline_and_typed_links_are_semantically_identical():
    runs = [Run(id="r"), Run(id="s")]
    old = doc(runs=runs, claims=[c(support=["r", "s", "r"])],
              rels=[Relationship(source="c", target="r", type=T.supports)])
    new = doc(runs=list(reversed(runs)), claims=[c(support=["s", "r"])],
              rels=[Relationship(source="c", target="r", type=T.supports)] * 2)
    assert diff(old, new)["claims"] == []


def test_text_source_and_parameter_changes_without_flag_changes_not_reported():
    old = doc(runs=[Run(id="r", params={"x": 1})], claims=[c(support=["r"], text="old")])
    new = doc(runs=[Run(id="r", params={"x": 2})], claims=[c(support=["r"], text="new")])
    assert diff(old, new)["claims"] == []
    assert "impact-diff" in diff(old, new)["note"]


def test_project_change_not_silently_construed_as_coverage_regression():
    old = doc(project=Project(id="p", name="old"), claims=[c()])
    new = doc(project=Project(id="p", name="new"), claims=[c()])
    assert diff(old, new)["claims"] == []


def test_claim_rename_is_removed_plus_added_with_no_inferred_matching():
    old = doc(claims=[c(ident="old")])
    new = doc(claims=[c(ident="new")])
    result = diff(old, new)
    assert [(r["id"], r["status"]) for r in result["claims"]] == [
        ("new", "added"), ("old", "removed")]
    assert result["summary"]["new_claims_with_flags"] == 1
    assert result["summary"]["removed_claims"] == 1


def test_duplicate_global_namespace_id_refused_in_either_snapshot():
    bad = doc(runs=[Run(id="same")], artifacts=[Artifact(id="same", path="a")])
    for before, after in ((bad, doc()), (doc(), bad)):
        with pytest.raises(CoverageDiffError, match="duplicate object ID"):
            diff(before, after)


def test_oracle_random_small_snapshots_matches_independent_set_delta():
    rng = random.Random(92921)
    runs = [Run(id=f"r{i}") for i in range(6)]
    for _ in range(160):
        def manifest():
            claims = []
            for i in range(7):
                if rng.random() < .2:
                    continue
                supports = [r.id for r in runs if rng.random() < .3]
                contradicts = [r.id for r in runs if rng.random() < .2]
                if rng.random() < .1: supports.append("ghost")
                claims.append(c(ident=f"c{i}", support=supports, contra=contradicts))
            return doc(runs=runs, claims=claims)
        before, after = manifest(), manifest()
        b = {r["id"]: set(r["review_flags"]) for r in claim_coverage(before, manifest_path="b")["claims"]}
        a = {r["id"]: set(r["review_flags"]) for r in claim_coverage(after, manifest_path="a")["claims"]}
        report = diff(before, after)
        got = rows(report)
        for identifier in b.keys() | a.keys():
            if identifier not in b:
                expected_gain = a[identifier]
                expected_clear = set()
            elif identifier not in a:
                expected_gain = expected_clear = set()
            else:
                expected_gain = a[identifier] - b[identifier]
                expected_clear = b[identifier] - a[identifier]
            must_show = identifier not in b or identifier not in a or bool(expected_gain or expected_clear)
            assert (identifier in got) == must_show
            if identifier in got:
                assert set(got[identifier]["new_flags"]) == expected_gain
                assert set(got[identifier]["cleared_flags"]) == expected_clear


def test_hostile_multiline_large_text_escaped_terminal_bounded_json_complete():
    huge = "line\nFAIL\x1b[31m" + "Z" * 90000
    old = doc()
    new = doc(claims=[c(text=huge)])
    data = diff(old, new)
    printed = format_coverage_diff(data)
    assert len(printed) <= 16384
    assert "\x1b" not in printed and "\nFAIL" not in printed
    assert data["claims"][0]["after"]["text"] == huge
    assert json.loads(json.dumps(data, allow_nan=False))["claims"][0]["after"]["text"] == huge


def test_many_changed_claims_caps_terminal_and_retains_footer():
    old = doc(runs=[Run(id="r")],
              claims=[c(ident=f"c{i:03d}", support=["r"], text="x"*700) for i in range(95)])
    new = doc(runs=old.runs, claims=[c(ident=f"c{i:03d}", text="x"*700) for i in range(95)])
    data = diff(old, new)
    text = format_coverage_diff(data)
    assert len(data["claims"]) == 95 and len(text) <= 16384
    assert "use --json" in text
    assert text.endswith("Recorded structural review flags only. Run 'runevidence validate' separately.")


def test_cli_json_and_text_with_actual_scratch_files_are_read_only(tmp_path, capsys):
    old = doc(runs=[Run(id="r")], claims=[c(support=["r"])])
    new = doc(runs=old.runs, claims=[c()])
    p, q = tmp_path/"before.json", tmp_path/"after.json"
    p.write_text(dump_manifest(old), encoding="utf8")
    q.write_text(dump_manifest(new), encoding="utf8")
    original = (p.read_bytes(), q.read_bytes())
    assert main(["coverage-diff", str(p), str(q), "--json"]) == 0
    out = capsys.readouterr()
    assert out.err == ""
    data = json.loads(out.out)
    assert rows(data)["c"]["new_flags"] == ["no_resolved_support"]
    assert main(["coverage-diff", str(p), str(q)]) == 0
    printed = capsys.readouterr().out
    assert "Claims with review flags: 0 -> 1" in printed
    assert (p.read_bytes(), q.read_bytes()) == original
    assert sorted(x.name for x in tmp_path.iterdir()) == ["after.json", "before.json"]


def test_cli_only_regressions_and_bad_manifest_exit_two(tmp_path, capsys):
    p = tmp_path/"before.json";q=tmp_path/"after.json"
    p.write_text(dump_manifest(doc(claims=[c()])), encoding="utf8")
    q.write_text(dump_manifest(doc(runs=[Run(id="r")], claims=[c(support=["r"])])), encoding="utf8")
    assert main(["coverage-diff", str(p), str(q), "--only-regressions", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["claims"] == [] and data["summary"]["changed_claims"] == 1
    assert main(["coverage-diff", str(p), str(tmp_path/"missing"), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["exit_code"] == 2


def test_other_commands_remain_available(tmp_path, capsys):
    p = tmp_path/"runevidence.json"
    p.write_text(dump_manifest(doc(runs=[Run(id="r")], claims=[c(support=["r"])])), encoding="utf8")
    assert main(["coverage", str(p), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["total_claims"] == 1
    assert main(["impact-diff", str(p), str(p), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["claims_for_review"] == 0
