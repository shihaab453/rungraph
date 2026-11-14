"""D10 single-snapshot structural coverage CI gate, valid schema and CLI."""
from __future__ import annotations

import json
import random

import pytest

from runevidence.cli import main
from runevidence.manifest import dump_manifest
from runevidence.models import (
    Artifact, EvidenceReference, Manifest, Project, Relationship,
    RelationshipType as T, Run,
)
from runevidence.run_coverage import _FLAGS, claim_coverage
from runevidence.run_coverage_gate import (
    CoverageGateError, evaluate_coverage_gate, format_coverage_gate,
)


def doc(*, runs=(), artifacts=(), claims=(), rels=()):
    return Manifest(project=Project(id="p", name="demo"), runs=list(runs),
                    artifacts=list(artifacts), evidence=list(claims),
                    relationships=list(rels))


def claim(ident="c", *, supports=(), contradicts=(), text="Claim"):
    return EvidenceReference(id=ident, claim=text, supports=list(supports), contradicts=list(contradicts))


def gate(m, flags=None):
    return evaluate_coverage_gate(m, manifest_path="synthetic/runevidence.json", selected_flags=flags)


def test_clean_claim_passes_default_policy():
    x = gate(doc(runs=[Run(id="r")], claims=[claim(supports=["r"])]))
    assert x["gate"]["exit_code"] == 0 and x["gate"]["passed"]
    assert x["gate"]["selected_flags"] == list(_FLAGS)
    assert x["coverage"]["summary"]["claims_without_flags"] == 1


def test_unattached_claim_triggers_default():
    x = gate(doc(claims=[claim()]))
    assert x["gate"]["failing_claim_ids"] == ["c"]
    assert x["gate"]["exit_code"] == 1
    assert x["gate"]["selected_flag_claim_counts"]["no_resolved_support"] == 1


def test_contradictions_alone_trigger_default():
    x = gate(doc(runs=[Run(id="r")], claims=[claim(supports=["r"], contradicts=["r"])]))
    assert x["gate"]["failing_claim_ids"] == ["c"]
    assert x["gate"]["selected_flag_claim_counts"]["dual_stance"] == 1


def test_specific_flag_ignores_other_existing_flags():
    x = gate(doc(claims=[claim()]), flags=["provenance_cycle"])
    assert x["gate"]["passed"] and x["gate"]["failing_claim_ids"] == []
    assert x["gate"]["claims_with_any_flags"] == 1
    assert x["coverage"]["claims"][0]["review_flags"] == ["no_resolved_support"]


def test_specific_flag_counts_any_matching_claim_once():
    x = gate(doc(runs=[Run(id="r")], claims=[claim(supports=["r"], contradicts=["r"])]),
             flags=["dual_stance", "contradictions_recorded"])
    assert x["gate"]["failing_claim_ids"] == ["c"]
    assert x["gate"]["failing_claims"] == 1
    assert sum(x["gate"]["selected_flag_claim_counts"].values()) == 2


def test_duplicate_flag_selectors_dedup_in_canonical_order():
    x = gate(doc(), flags=["provenance_cycle", "dual_stance", "dual_stance"])
    assert x["gate"]["selected_flags"] == ["dual_stance", "provenance_cycle"]


def test_absent_claims_are_valid_and_pass():
    x = gate(doc())
    assert x["gate"]["total_claims"] == 0 and x["gate"]["passed"]
    assert x["coverage"]["claims"] == []


def test_invalid_flag_rejected_before_processing():
    with pytest.raises(CoverageGateError, match="unknown coverage flag"):
        gate(doc(), flags=["not_a_flag"])


def test_explicit_empty_flag_list_refused_not_silent_pass():
    with pytest.raises(CoverageGateError, match="cannot be empty"):
        gate(doc(), flags=[])


def test_string_instead_of_sequence_refused():
    with pytest.raises(CoverageGateError, match="nonempty sequence"):
        gate(doc(), flags="no_resolved_support")


def test_nonstr_and_nonsesequence_flag_arguments_refused():
    for bad in (123, [None], [1], ["dual_stance", None]):
        with pytest.raises(CoverageGateError):
            gate(doc(), flags=bad)


def test_duplicate_namespace_ids_refused_even_without_claims():
    with pytest.raises(CoverageGateError, match="duplicate object ID"):
        gate(doc(runs=[Run(id="x")], artifacts=[Artifact(id="x", path="a")]))


def test_invalid_direct_support_flag_can_be_selected():
    x = gate(doc(claims=[claim(supports=["ghost"])]), flags=["unresolved_direct_reference"])
    assert x["gate"]["failing_claim_ids"] == ["c"]
    assert x["coverage"]["claims"][0]["review_flags"] == [
        "no_resolved_support", "unresolved_direct_reference"]


def test_provenance_cycle_selectable_without_labeling_science():
    x = gate(doc(runs=[Run(id="a"), Run(id="b")],
                 claims=[claim(supports=["a"])],
                 rels=[Relationship(source="a", target="b", type=T.derived_from),
                       Relationship(source="b", target="a", type=T.derived_from)]),
             flags=["provenance_cycle"])
    assert x["gate"]["failing_claim_ids"] == ["c"]
    assert "not a scientific truth judgement" in x["note"]


def test_relationship_only_support_is_resolved():
    x = gate(doc(runs=[Run(id="r")], claims=[claim()],
                 rels=[Relationship(source="c", target="r", type=T.supports)]))
    assert x["gate"]["passed"]


def test_only_selected_flag_does_not_suppress_full_coverage():
    m = doc(runs=[Run(id="r")], claims=[claim("a"), claim("b", supports=["r"], contradicts=["r"])])
    x = gate(m, flags=["no_resolved_support"])
    assert x["gate"]["failing_claim_ids"] == ["a"]
    assert x["gate"]["claims_with_any_flags"] == 2
    assert x["coverage"] == claim_coverage(m, manifest_path="synthetic/runevidence.json")


def test_sorted_claim_ids_not_dependent_on_manifest_order():
    claims = [claim("z"), claim("a"), claim("m")]
    assert gate(doc(claims=claims))["gate"]["failing_claim_ids"] == ["a", "m", "z"]
    assert gate(doc(claims=claims)) == gate(doc(claims=list(reversed(claims))))


def test_hostile_valid_claim_text_path_escapes_and_caps():
    hostile = "hello\nBAD\r\x1b[31m" + "X"*100000
    m = doc(claims=[claim("c", text=hostile)])
    x = evaluate_coverage_gate(m, manifest_path="bad\nPATH\x1b[31m" + "Y"*100000)
    out = format_coverage_gate(x)
    assert "\x1b" not in out and "\nBAD" not in out and "\nPATH" not in out
    assert len(out) <= 16384 and x["coverage"]["claims"][0]["text"] == hostile
    assert out.endswith("Recorded structural review policy only. Run 'runevidence validate' separately.")


def test_very_long_but_schema_valid_claim_id_is_bounded_in_terminal():
    giant_id = "C"*100000
    x = gate(doc(claims=[claim(giant_id)]))
    out = format_coverage_gate(x)
    assert len(out) <= 16384 and giant_id not in out
    assert x["gate"]["failing_claim_ids"] == [giant_id]


def test_many_claims_have_bounded_terminal_and_complete_json():
    x = gate(doc(claims=[claim(f"c{i:04d}") for i in range(140)]))
    assert x["gate"]["failing_claims"] == 140
    assert len(format_coverage_gate(x)) <= 16384
    assert len(json.loads(json.dumps(x))["gate"]["failing_claim_ids"]) == 140


def test_cli_missing_file_yields_two_json(tmp_path, capsys):
    assert main(["coverage-gate", str(tmp_path / "missing"), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["exit_code"] == 2


def test_cli_bad_schema_yields_two_json(tmp_path, capsys):
    mf = tmp_path / "bad.json"
    mf.write_text('{"hello": "not a manifest"}', encoding="utf8")
    assert main(["coverage-gate", str(mf), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["exit_code"] == 2


def test_cli_exit_codes_0_and_1_and_readonly(tmp_path, capsys):
    mf = tmp_path / "runevidence.json"
    m = doc(runs=[Run(id="r")], claims=[claim(supports=["r"])])
    mf.write_text(dump_manifest(m), encoding="utf8")
    original = mf.read_bytes()
    assert main(["coverage-gate", str(mf), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["gate"]["passed"]
    mf.write_text(dump_manifest(doc(claims=[claim()])), encoding="utf8")
    changed = mf.read_bytes()
    assert main(["coverage-gate", str(mf), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["gate"]["failing_claim_ids"] == ["c"]
    assert changed != original and mf.read_bytes() == changed
    assert sorted(p.name for p in tmp_path.iterdir()) == ["runevidence.json"]


def test_cli_repeatable_flags_and_text_presentation(tmp_path, capsys):
    mf = tmp_path / "runevidence.json"
    mf.write_text(dump_manifest(doc(claims=[claim()])), encoding="utf8")
    assert main(["coverage-gate", str(mf), "--flag", "provenance_cycle", "--json"]) == 0
    x = json.loads(capsys.readouterr().out)
    assert x["gate"]["selected_flags"] == ["provenance_cycle"]
    assert main(["coverage-gate", str(mf), "--flag", "no_resolved_support",
                 "--flag", "provenance_cycle"]) == 1
    text = capsys.readouterr().out
    assert "Coverage gate: TRIGGERED (exit 1)" in text


def test_cli_legacy_review_gate_still_runs(tmp_path, capsys):
    mf = tmp_path / "runevidence.json"
    mf.write_text(dump_manifest(doc(runs=[Run(id="r")], claims=[claim(supports=["r"])])), encoding="utf8")
    assert main(["review-gate", str(mf), str(mf), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["gate"]["passed"]


def test_cli_does_not_open_artifact_files(tmp_path, capsys):
    mf = tmp_path / "runevidence.json"
    mf.write_text(dump_manifest(doc(artifacts=[Artifact(id="a", path="not-there.bin")],
                                    claims=[claim(supports=["a"])])), encoding="utf8")
    original = mf.read_bytes()
    assert main(["coverage-gate", str(mf), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["gate"]["passed"]
    assert mf.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["runevidence.json"]


def test_randomized_flag_selection_matches_independent_set_oracle():
    rng = random.Random(20261104)
    runs = [Run(id=f"r{i}") for i in range(5)]
    for _ in range(180):
        claims = []
        for i in range(10):
            supports = [r.id for r in runs if rng.random() < .27]
            contras = [r.id for r in runs if rng.random() < .18]
            if rng.random() < .15:
                supports.append("ghost")
            claims.append(claim(f"c{i}", supports=supports, contradicts=contras))
        m = doc(runs=runs, claims=claims)
        selection = rng.sample(list(_FLAGS), rng.randrange(1, len(_FLAGS)+1))
        expected_data = claim_coverage(m, manifest_path="synthetic/runevidence.json")
        expected = sorted(row["id"] for row in expected_data["claims"]
                          if set(row["review_flags"]) & set(selection))
        actual = gate(m, selection)
        assert actual["gate"]["failing_claim_ids"] == expected
        assert actual["gate"]["exit_code"] == int(bool(expected))
        assert actual["coverage"] == expected_data

