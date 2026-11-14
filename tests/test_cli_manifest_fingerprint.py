"""D11: deterministic semantic fingerprint of schema-loaded recorded manifests."""
from __future__ import annotations

import hashlib
import json
import random

import pytest

from runevidence.cli import main
from runevidence.manifest import dump_manifest
from runevidence.models import (
    Artifact, EvidenceReference, Manifest, Project, Relationship,
    RelationshipType as T, Result, Run,
)
from runevidence.run_fingerprint import FingerprintError, manifest_fingerprint, format_fingerprint
from runevidence.run_impact_diff import compare_claim_impact


def m(*, project=None, runs=(), artifacts=(), results=(), claims=(), rels=()):
    return Manifest(project=project or Project(id="p", name="test"), runs=list(runs),
                    artifacts=list(artifacts), results=list(results),
                    evidence=list(claims), relationships=list(rels))


def fp(manifest, path="a/runevidence.json"):
    return manifest_fingerprint(manifest, manifest_path=path)


def digest(manifest):
    return fp(manifest)["fingerprint"]["digest"]


def make():
    return m(runs=[Run(id="r", params={"seed": 7})],
             artifacts=[Artifact(id="a", path="artifact.bin", run_id="r", sha256="a" * 64)],
             results=[Result(id="v", run_id="r", name="accuracy", value=0.9)],
             claims=[EvidenceReference(id="c", claim="supported", supports=["r", "v"])],
             rels=[Relationship(source="r", target="r", type=T.derived_from)])


def test_same_snapshot_stable_and_correct_sha_hex():
    x = fp(make())
    assert x == fp(make())
    assert len(x["fingerprint"]["digest"]) == 64
    int(x["fingerprint"]["digest"], 16)
    assert x["fingerprint"]["algorithm"] == "sha256"
    assert x["fingerprint"]["semantic_format"] == "recorded-manifest-v1"
    assert x["fingerprint"]["objects"] == 5  # project, run, artifact, result, claim
    assert x["fingerprint"]["relationships"] == 1


def test_path_is_metadata_only_not_fingerprint_input():
    a = make()
    x = fp(a, "one.json")
    y = fp(a, "somewhere/else.json")
    assert x["fingerprint"] == y["fingerprint"]
    assert x["manifest"] != y["manifest"]


def test_object_array_order_and_json_dict_key_order_are_ignored():
    r0 = Run(id="a", params={"x": 1, "y": {"b": 2, "a": 3}})
    r1 = Run(id="b")
    first = m(runs=[r0, r1])
    second = m(runs=[r1, Run(id="a", params={"y": {"a": 3, "b": 2}, "x": 1})])
    assert digest(first) == digest(second)


def test_duplicate_relationships_and_reordering_are_irrelevant():
    r_none = Relationship(source="a", target="b", type=T.derived_from, note=None)
    r_text = Relationship(source="a", target="b", type=T.derived_from, note="because")
    common = dict(runs=[Run(id="a"), Run(id="b")])
    assert digest(m(**common, rels=[r_none, r_text, r_none])) == digest(
        m(**common, rels=[r_text, r_none]))


def test_none_empty_string_nonempty_notes_are_distinct():
    common = dict(runs=[Run(id="a"), Run(id="b")])
    def snapshot(notes):
        return m(**common, rels=[Relationship(source="a", target="b", type=T.derived_from, note=x)
                                 for x in notes])
    assert len({digest(snapshot([v])) for v in (None, "", "note")}) == 3
    assert fp(snapshot([None, "", "note"]))["fingerprint"]["relationships"] == 3


def test_claim_inline_support_order_and_duplicate_shorthand_ignored():
    common = dict(runs=[Run(id="a"), Run(id="b")])
    a = m(**common, claims=[EvidenceReference(id="c", claim="text", supports=["a", "b", "a"])])
    b = m(**common, claims=[EvidenceReference(id="c", claim="text", supports=["b", "a"])])
    assert digest(a) == digest(b)


def test_support_and_contradiction_are_not_collapsed():
    common = dict(runs=[Run(id="a")])
    a = m(**common, claims=[EvidenceReference(id="c", claim="text", supports=["a"])])
    b = m(**common, claims=[EvidenceReference(id="c", claim="text", contradicts=["a"])])
    assert digest(a) != digest(b)


def test_direct_claim_text_and_source_changes_change_fingerprint():
    common = dict(runs=[Run(id="r")])
    fingerprints = [digest(m(**common, claims=[EvidenceReference(id="c", claim=t, source=source)]))
                    for t, source in (("good", None), ("bad", None), ("good", "url"))]
    assert len(set(fingerprints)) == 3


def test_valid_metadata_changes_not_ignored():
    a = m(runs=[Run(id="r", metadata={"stage": 1})])
    b = m(runs=[Run(id="r", metadata={"stage": 2})])
    assert digest(a) != digest(b)


def test_project_only_edit_changes_fingerprint():
    a = m(project=Project(id="p", name="Old"))
    b = m(project=Project(id="p", name="New"))
    assert digest(a) != digest(b)
    assert fp(a)["fingerprint"]["objects"] == 1


def test_distinguishes_recorded_type_and_kind():
    # Run and artifact can share *an ID across snapshots*, not within one.
    a = m(runs=[Run(id="x")])
    b = m(artifacts=[Artifact(id="x", path="x")])
    assert digest(a) != digest(b)


def test_nan_stability_and_infinity_and_null_distinction():
    common = dict(runs=[Run(id="r")])
    def snap(v):
        return m(**common, results=[Result(id="n", name="n", run_id="r", value=v)])
    assert digest(snap(float("nan"))) == digest(snap(float("nan")))
    assert len({digest(snap(x)) for x in (float("nan"), float("inf"), float("-inf"), None)}) == 4
    assert len(set(digest(snap(x)) for x in (1.0, 1, True))) == 3


def test_zero_sign_uses_existing_d5_typed_float_semantics():
    common = dict(runs=[Run(id="r")])
    def snap(v):
        return m(**common, results=[Result(id="n", name="n", run_id="r", value=v)])
    assert digest(snap(0.0)) != digest(snap(-0.0))


def test_missing_dependency_does_not_block_recorded_fingerprint():
    a = m(claims=[EvidenceReference(id="c", claim="not yet wired", supports=["missing"])])
    assert len(digest(a)) == 64


def test_duplicate_global_namespace_is_refused_even_without_claims():
    dup = m(runs=[Run(id="x")], artifacts=[Artifact(id="x", path="x")])
    with pytest.raises(FingerprintError, match="duplicate object ID"):
        fp(dup)


def test_d5_equivalence_randomized_object_and_relationship_permutations():
    rng = random.Random(11042026)
    for _ in range(240):
        n = rng.randrange(1, 9)
        runs = [Run(id=f"r{i}", params={"i": i}) for i in range(n)]
        edges = [Relationship(source=f"r{rng.randrange(n)}", target=f"r{rng.randrange(n)}",
                              type=T.derived_from, note=rng.choice((None, "", "why")))
                 for _ in range(rng.randrange(0, 14))]
        claims = [EvidenceReference(id="c", claim="same", supports=[f"r{rng.randrange(n)}"
                   for _ in range(rng.randrange(0, 8))])]
        a = m(runs=runs, claims=claims, rels=edges)
        duplicated = list(edges)
        if edges:
            duplicated.append(rng.choice(edges))
        rng.shuffle(duplicated)
        b = m(runs=list(reversed(runs)), claims=[EvidenceReference(id="c", claim="same",
                  supports=list(reversed(list(set(claims[0].supports)))) )], rels=duplicated)
        # Relationship and stance order/repetition are D5-semantic no-ops.
        assert digest(a) == digest(b)
        d5 = compare_claim_impact(a, b, before_path="old", after_path="new")
        assert d5["changes"] == {"objects": [], "relationships": []}


def test_recorded_change_matches_d5_object_delta():
    a = make()
    b = a.model_copy(deep=True)
    b.runs[0].params["seed"] = 11
    assert digest(a) != digest(b)
    assert compare_claim_impact(a, b, before_path="old", after_path="new")["summary"]["object_changes"] == 1


def test_text_renderer_escapes_path_and_does_not_expose_other_fields():
    scary = "data\nWARNING\x1b[31m" + "X" * 100_000
    x = fp(make(), scary)
    out = format_fingerprint(x)
    assert "\nWARNING" not in out and "\x1b" not in out
    assert len(out) <= 16384 and "chars omitted" in out
    assert x["manifest"] == scary


def test_cli_valid_json_same_after_whitespace_reorder_and_read_only(tmp_path, capsys):
    snapshot = make()
    p, q = tmp_path / "a.json", tmp_path / "b.json"
    original = dump_manifest(snapshot)
    p.write_text(original, encoding="utf8")
    q.write_text(json.dumps(json.loads(original), sort_keys=True, indent=4), encoding="utf8")
    p_bytes, q_bytes = p.read_bytes(), q.read_bytes()
    assert main(["fingerprint", str(p), "--json"]) == 0
    x = json.loads(capsys.readouterr().out)
    assert main(["fingerprint", str(q), "--json"]) == 0
    y = json.loads(capsys.readouterr().out)
    assert x["fingerprint"] == y["fingerprint"]
    assert p.read_bytes() == p_bytes and q.read_bytes() == q_bytes
    assert sorted(r.name for r in tmp_path.iterdir()) == ["a.json", "b.json"]


def test_cli_missing_manifest_returns_exit_two_json(tmp_path, capsys):
    assert main(["fingerprint", str(tmp_path / "absent.json"), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["exit_code"] == 2


def test_cli_duplicate_id_error_is_json_and_exit_two(tmp_path, capsys):
    p = tmp_path / "runevidence.json"
    p.write_text(dump_manifest(m(runs=[Run(id="same")], artifacts=[Artifact(id="same", path="x")])), encoding="utf8")
    assert main(["fingerprint", str(p), "--json"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "duplicate object ID" in out["error"]


def test_cli_existing_coverage_gate_still_works(tmp_path, capsys):
    p = tmp_path / "runevidence.json"
    p.write_text(dump_manifest(m(claims=[EvidenceReference(id="c", claim="needs support")])), encoding="utf8")
    assert main(["coverage-gate", str(p), "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["gate"]["failing_claims"] == 1


def test_digest_reproducibility_over_subprocess_equivalent_byte_sequences():
    x = make()
    data = fp(x)
    assert data["fingerprint"]["digest"] == digest(x)
    assert json.loads(json.dumps(data, allow_nan=False))["fingerprint"]["algorithm"] == "sha256"
    assert "artifact" in data["note"]
