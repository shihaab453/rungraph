"""D9 read-only CI policy gate over the D8 recorded claim-review queue."""
from __future__ import annotations

import json
import random

import pytest

from runevidence.cli import main
from runevidence.manifest import dump_manifest
from runevidence.models import (
    Artifact, EvidenceReference, Manifest, Project, Relationship,
    RelationshipType as T, Result, Run,
)
from runevidence.run_review_diff import compare_claim_review
from runevidence.run_review_gate import ReviewGateError, evaluate_review_gate, format_review_gate


def doc(*, runs=(), arts=(), results=(), claims=(), rels=(), project=None):
    return Manifest(project=project or Project(id="p", name="test"), runs=list(runs),
                    artifacts=list(arts), results=list(results), evidence=list(claims),
                    relationships=list(rels))


def claim(ident="c", *, support=(), contra=(), text="statement"):
    return EvidenceReference(id=ident, claim=text, supports=list(support), contradicts=list(contra))


def gate(a, b, policy="new-flags"):
    return evaluate_review_gate(a, b, before_path="before.json", after_path="after.json", policy=policy)


def ids(result):
    return result["gate"]["failing_claim_ids"]


def test_unchanged_snapshot_passes_with_existing_review_flags():
    a = doc(claims=[claim()])
    g = gate(a, a.model_copy(deep=True))
    assert g["gate"]["passed"] is True and g["gate"]["exit_code"] == 0
    assert g["gate"]["failing_claims"] == 0
    assert g["review"]["summary"]["candidates_total"] == 0


def test_loss_of_support_triggers_default_gate():
    a = doc(runs=[Run(id="r")], claims=[claim(support=["r"])])
    b = doc(runs=a.runs, claims=[claim()])
    g = gate(a, b)
    assert ids(g) == ["c"]
    assert g["gate"]["new_flag_claims"] == 1
    assert g["gate"]["exit_code"] == 1


def test_new_flagged_claim_triggers_default_gate():
    g = gate(doc(), doc(claims=[claim()]))
    assert ids(g) == ["c"] and g["gate"]["review_candidates"] == 1


def test_new_clean_claim_does_not_trigger_default_gate():
    a = doc(runs=[Run(id="r")])
    b = doc(runs=a.runs, claims=[claim(support=["r"])])
    g = gate(a, b)
    assert ids(g) == [] and g["gate"]["review_candidates"] == 1


def test_removed_flagged_claim_is_not_a_cleared_flag_or_default_gate():
    g = gate(doc(claims=[claim()]), doc())
    assert g["gate"]["passed"] and g["gate"]["removed_claims"] == 1
    assert g["review"]["claims"][0]["cleared_structural_flags"] == []


def test_removed_claim_triggers_removals_policy():
    g = gate(doc(claims=[claim()]), doc(), "new-flags-or-removals")
    assert ids(g) == ["c"] and g["gate"]["exit_code"] == 1


def test_new_flags_or_removals_is_union_without_duplicate_ids():
    a = doc(runs=[Run(id="r")], claims=[claim("deleted"), claim("lost", support=["r"])])
    b = doc(runs=a.runs, claims=[claim("lost"), claim("added")])
    g = gate(a, b, "new-flags-or-removals")
    assert ids(g) == ["added", "deleted", "lost"]
    assert g["gate"]["failing_claims"] == 3


def test_clearing_existing_flag_is_not_regression():
    a = doc(runs=[Run(id="r")], claims=[claim()])
    b = doc(runs=a.runs, claims=[claim(support=["r"])])
    g = gate(a, b)
    assert g["gate"]["passed"] and g["gate"]["review_candidates"] == 1
    assert g["review"]["claims"][0]["cleared_structural_flags"] == ["no_resolved_support"]


def test_any_review_gate_triggers_on_claim_text_edit():
    a = doc(runs=[Run(id="r")], claims=[claim(text="old", support=["r"])])
    b = doc(runs=a.runs, claims=[claim(text="new", support=["r"])])
    assert ids(gate(a, b)) == []
    g = gate(a, b, "any-review")
    assert ids(g) == ["c"]


def test_any_review_gate_triggers_on_result_change_without_flags():
    a = doc(runs=[Run(id="r")], results=[Result(id="x", run_id="r", name="loss", value=.5)],
            claims=[claim(support=["x"])])
    b = doc(runs=a.runs, results=[Result(id="x", run_id="r", name="loss", value=.4)],
            claims=a.evidence)
    assert ids(gate(a, b)) == []
    assert ids(gate(a, b, "any-review")) == ["c"]


def test_any_review_ignores_project_only_change():
    a = doc(claims=[claim()])
    b = doc(project=Project(id="p", name="changed"), claims=a.evidence)
    assert ids(gate(a, b, "any-review")) == []


def test_nullable_note_relation_change_can_trigger_any_review():
    a = doc(runs=[Run(id="a"), Run(id="b")], claims=[claim(support=["a"])],
            rels=[Relationship(source="a", target="b", type=T.derived_from, note=None)])
    b = doc(runs=a.runs, claims=a.evidence,
            rels=[Relationship(source="a", target="b", type=T.derived_from, note="note")])
    assert ids(gate(a, b, "any-review")) == ["c"]
    assert ids(gate(a, b)) == []


def test_mixed_new_and_cleared_flags_still_triggers():
    a = doc(runs=[Run(id="r")], claims=[claim(support=["ghost"])])
    b = doc(runs=a.runs, claims=[claim(support=["r"], contra=["r"])])
    g = gate(a, b)
    assert ids(g) == ["c"]
    assert g["review"]["claims"][0]["cleared_structural_flags"]


def test_bad_global_namespace_rejected_with_dedicated_gate_error():
    bad = doc(runs=[Run(id="dup")], arts=[Artifact(id="dup", path="a")])
    with pytest.raises(ReviewGateError, match="duplicate object ID"):
        gate(bad, doc())


def test_unknown_direct_api_policy_is_explicit_invalid_request():
    with pytest.raises(ReviewGateError, match="unknown review-gate policy"):
        gate(doc(), doc(), "incorrect")


def test_cli_bad_policy_parse_returns_exit_two(capsys):
    assert main(["review-gate", "before.json", "after.json", "--policy", "invalid"]) == 2
    capsys.readouterr()


def test_cli_missing_manifest_json_returns_exit_two(tmp_path, capsys):
    p = tmp_path / "before.json"
    p.write_text(dump_manifest(doc()), encoding="utf8")
    assert main(["review-gate", str(p), str(tmp_path / "ghost.json"), "--json"]) == 2
    d = json.loads(capsys.readouterr().out)
    assert d["exit_code"] == 2


def test_cli_bad_schema_json_returns_exit_two(tmp_path, capsys):
    p, q = tmp_path / "before.json", tmp_path / "after.json"
    p.write_text(dump_manifest(doc()), encoding="utf8")
    q.write_text('{"not": "a manifest"}', encoding="utf8")
    assert main(["review-gate", str(p), str(q), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["exit_code"] == 2


def test_cli_pass_and_trigger_exit_codes_with_complete_json(tmp_path, capsys):
    a = doc(runs=[Run(id="r")], claims=[claim(support=["r"])])
    b = doc(runs=a.runs, claims=[claim()])
    p, q = tmp_path / "before.json", tmp_path / "after.json"
    p.write_text(dump_manifest(a), encoding="utf8")
    q.write_text(dump_manifest(b), encoding="utf8")
    original = (p.read_bytes(), q.read_bytes())
    assert main(["review-gate", str(p), str(p), "--json"]) == 0
    passed = json.loads(capsys.readouterr().out)
    assert passed["gate"]["passed"] is True
    assert main(["review-gate", str(p), str(q), "--json"]) == 1
    triggered = json.loads(capsys.readouterr().out)
    assert triggered["gate"]["exit_code"] == 1
    assert triggered["review"]["summary"]["candidates_total"] == 1
    assert (p.read_bytes(), q.read_bytes()) == original
    assert sorted(x.name for x in tmp_path.iterdir()) == ["after.json", "before.json"]


def test_cli_terminal_trigger_text_safe(tmp_path, capsys):
    p, q = tmp_path / "a.json", tmp_path / "b.json"
    a = doc(runs=[Run(id="r")], claims=[claim(support=["r"])])
    b = doc(runs=a.runs, claims=[claim()])
    p.write_text(dump_manifest(a), encoding="utf8")
    q.write_text(dump_manifest(b), encoding="utf8")
    assert main(["review-gate", str(p), str(q)]) == 1
    out = capsys.readouterr().out
    assert "Gate: TRIGGERED (exit 1)" in out and "Review policy:" in out
    assert "Recorded review policy only" in out


def test_cli_one_changed_result_is_not_a_default_policy_failure(tmp_path, capsys):
    a = doc(runs=[Run(id="r")], results=[Result(id="x", name="x", run_id="r", value=1)],
            claims=[claim(support=["x"])])
    b = doc(runs=a.runs, results=[Result(id="x", name="x", run_id="r", value=2)],
            claims=a.evidence)
    p, q = tmp_path / "a.json", tmp_path / "b.json"
    p.write_text(dump_manifest(a), encoding="utf8")
    q.write_text(dump_manifest(b), encoding="utf8")
    assert main(["review-gate", str(p), str(q), "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["gate"]["review_candidates"] == 1
    assert main(["review-gate", str(p), str(q), "--policy", "any-review", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["gate"]["failing_claim_ids"] == ["c"]


def test_randomized_policies_match_independent_d8_union_oracle():
    rng = random.Random(6289)
    runs = [Run(id=f"r{i}") for i in range(5)]
    for _ in range(160):
        def snapshot():
            evidence = []
            for i in range(8):
                if rng.random() < .23:
                    continue
                supports = [r.id for r in runs if rng.random() < .33]
                contra = [r.id for r in runs if rng.random() < .1]
                if rng.random() < .16:
                    supports.append("ghost")
                evidence.append(claim(f"c{i}", support=supports, contra=contra,
                                      text=str(rng.randrange(3))))
            return doc(runs=runs, claims=evidence)
        a, b = snapshot(), snapshot()
        reference = compare_claim_review(a, b, before_path="before.json", after_path="after.json")
        expected = {
            "new-flags": sorted(x["id"] for x in reference["claims"] if x["introduced_structural_flags"]),
            "new-flags-or-removals": sorted(x["id"] for x in reference["claims"]
                                           if x["introduced_structural_flags"] or x["presence"] == "before_only"),
            "any-review": [x["id"] for x in reference["claims"]],
        }
        for policy, selection in expected.items():
            actual = gate(a, b, policy)
            assert set(ids(actual)) == set(selection)
            assert actual["gate"]["exit_code"] == int(bool(selection))
            assert actual["gate"]["failing_claims"] == len(selection)
            assert actual["review"] == reference


def test_text_renderer_escape_and_overall_bound():
    huge = "bad\nUNSAFE\x1b[31m" + "A" * 100000
    # ID_PATTERN rejects control characters, so skip validation to model a
    # direct API caller and still exercise renderer escaping of claim IDs.
    a = doc(claims=[claim().model_copy(update={"id": huge})])
    b = doc()
    g = gate(a, b, "new-flags-or-removals")
    rendered = format_review_gate(g)
    assert "\x1b" not in rendered and "\nUNSAFE" not in rendered
    assert len(rendered) <= 16384
    assert rendered.endswith("Recorded review policy only. Run 'runevidence validate' separately.")
    assert g["gate"]["failing_claim_ids"] == [huge]
    json.dumps(g, allow_nan=False)


def test_large_review_preview_caps_ids_and_keeps_json_complete():
    claims = [claim(ident=f"c{i:03d}", text="a" * 300) for i in range(150)]
    g = gate(doc(claims=claims), doc(), "new-flags-or-removals")
    assert g["gate"]["failing_claims"] == 150
    assert len(format_review_gate(g)) <= 16384
    assert len(g["review"]["claims"]) == 150


def test_gate_policy_aliases_do_not_change_review_evidence():
    a = doc(runs=[Run(id="r")], claims=[claim(support=["r"])])
    b = doc(runs=a.runs, claims=[claim()])
    results = [gate(a, b, x) for x in ("new-flags", "new-flags-or-removals", "any-review")]
    assert all(x["review"] == results[0]["review"] for x in results)
    assert all(x["gate"]["failing_claims"] == 1 for x in results)
