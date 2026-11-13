"""D14: opt-in, deterministic recorded fingerprint change explanation."""
from __future__ import annotations

import json
import random
from types import SimpleNamespace

import pytest

from runevidence.cli import main
from runevidence.manifest import dump_manifest
from runevidence.models import (Artifact, EvidenceReference, Manifest, Project, Result,
                                 Run, Relationship, RelationshipType as T)
from runevidence.run_fingerprint_explain import (explain_fingerprint_difference,
                                                format_fingerprint_explanation)
from runevidence.run_fingerprint import manifest_fingerprint, FingerprintError
from runevidence.run_impact_diff import compare_claim_impact


def doc(*, title="demo", runs=(), artifacts=(), results=(), evidence=(), relationships=()):
    return Manifest(project=Project(id="p", name=title), runs=list(runs), artifacts=list(artifacts),
                    results=list(results), evidence=list(evidence), relationships=list(relationships))


def put(tmp_path, stem, m):
    path = tmp_path / (stem + ".json")
    path.write_text(dump_manifest(m), encoding="utf-8")
    return path


def command(left, right, capsys, *, explain=True, as_json=True):
    argv = ["fingerprint-compare", str(left), str(right)]
    if explain:
        argv.append("--explain")
    if as_json:
        argv.append("--json")
    code = main(argv)
    result = capsys.readouterr()
    return code, json.loads(result.out) if as_json else result.out


def fingerprint(m):
    return manifest_fingerprint(m, manifest_path="synth.json")["fingerprint"]["digest"]


def test_equal_explanation_empty_and_digest_unaffected(tmp_path, capsys):
    p, q = put(tmp_path, "a", doc(runs=[Run(id="r")])), put(tmp_path, "b", doc(runs=[Run(id="r")]))
    code, data = command(p, q, capsys)
    assert code == 0 and data["comparison"]["matched"]
    assert data["explanation"]["objects"] == data["explanation"]["relationships"] == []
    assert data["explanation"]["summary"]["object_changes"] == 0
    assert data["explanation"]["summary"]["relationship_changes"] == 0
    assert data["before_fingerprint"] == data["after_fingerprint"]


def test_no_explain_json_remains_exactly_original_shape(tmp_path, capsys):
    a = put(tmp_path, "a", doc()); b = put(tmp_path, "b", doc())
    code, data = command(a, b, capsys, explain=False)
    assert code == 0
    assert sorted(data) == ["after_fingerprint", "after_manifest", "before_fingerprint", "before_manifest", "comparison"]


def test_change_project_included_even_if_no_claims(tmp_path, capsys):
    a, b = put(tmp_path, "a", doc()), put(tmp_path, "b", doc(title="new"))
    code, data = command(a, b, capsys)
    assert code == 1
    assert data["explanation"]["objects"] == [{
        "id": "p", "change": "modified", "before_kind": "project", "after_kind": "project",
        "changed_fields": ["name"]}]


def test_add_remove_retype_and_modified_report():
    a = doc(runs=[Run(id="r0", params={"seed": 2}), Run(id="r1")],
            artifacts=[Artifact(id="x", path="x")])
    b = doc(runs=[Run(id="r0", params={"seed": 3}), Run(id="r2")],
            results=[Result(id="x", name="metric", run_id="r0", value=1)])
    report = explain_fingerprint_difference(a,b)
    assert [(r["id"],r["change"],r["changed_fields"]) for r in report["objects"]] == [
        ("r0", "modified", ["params"]), ("r1", "removed", []), ("r2", "added", []), ("x", "retyped", [])]
    assert report["summary"] == {"object_changes": 4, "relationship_changes": 0,
        "objects_added":1, "objects_removed":1, "objects_retyped":1, "objects_modified":1,
        "relationships_added":0, "relationships_removed":0}


def test_typed_evidence_memberships_dedupe_order_and_relationships():
    edge = Relationship(source="c", target="r", type=T.supports)
    a = doc(runs=[Run(id="r")], evidence=[EvidenceReference(id="c", claim="yes", supports=["r", "r"])],
            relationships=[edge,edge])
    b = doc(runs=[Run(id="r")], evidence=[EvidenceReference(id="c", claim="yes", supports=["r"])],
            relationships=[edge])
    report = explain_fingerprint_difference(a,b)
    assert report["objects"] == report["relationships"] == []
    assert fingerprint(a) == fingerprint(b)


def test_relationships_nullable_note_and_deterministic_order():
    base = doc(runs=[Run(id="a"),Run(id="b")], relationships=[
        Relationship(source="a",target="b",type=T.derived_from,note=None)])
    updated = doc(runs=[Run(id="b"),Run(id="a")], relationships=[
        Relationship(source="a",target="b",type=T.derived_from,note=""),
        Relationship(source="a",target="b",type=T.derived_from,note=""),
        Relationship(source="b",target="a",type=T.input_to,note="x")])
    out = explain_fingerprint_difference(base,updated)
    assert out["summary"]["relationship_changes"] == 3
    assert out["summary"]["relationships_added"] == 2
    assert out["summary"]["relationships_removed"] == 1
    assert out["relationships"] == [
        {"change":"removed","type":"derived_from","source":"a","target":"b","note":None},
        {"change":"added","type":"derived_from","source":"a","target":"b","note":""},
        {"change":"added","type":"input_to","source":"b","target":"a","note":"x"}]


def test_field_names_dont_expose_metadata_values():
    a = doc(runs=[Run(id="r", metadata={"secret": "password_a"})])
    b = doc(runs=[Run(id="r", metadata={"secret": "password_b"})])
    report = explain_fingerprint_difference(a,b)
    assert report["objects"][0]["changed_fields"] == ["metadata"]
    assert "password_" not in json.dumps(report)


def test_missing_first_does_not_attempt_to_load_second(tmp_path, capsys, monkeypatch):
    import runevidence.cli as cli
    original = cli.load_manifest
    before = tmp_path / "missing.json"
    after = put(tmp_path,"valid",doc())
    calls=[]
    def wrapped(path):
        calls.append(str(path))
        if str(path)==str(after):
            raise AssertionError("second manifest must not be loaded after failure on first")
        return original(path)
    with monkeypatch.context() as mp:
        mp.setattr(cli,"load_manifest",wrapped)
        code, data = command(before,after,capsys)
    assert code == 2 and data["exit_code"] == 2 and calls == [str(before)]


def test_missing_second_reports_two(tmp_path,capsys):
    code,data=command(put(tmp_path,"a",doc()),tmp_path/"missing.json",capsys)
    assert code==2 and data["exit_code"]==2


def test_duplicate_global_id_in_one_manifest_reports_two(tmp_path,capsys):
    invalid=doc(runs=[Run(id="p")]);valid=doc()
    code,data=command(put(tmp_path,"a",invalid),put(tmp_path,"b",valid),capsys)
    assert code==2 and "duplicate object ID" in data["error"]


def test_schema_invalid_reports_two(tmp_path,capsys):
    a=tmp_path/"a.json";a.write_text('{"project":42}')
    code,data=command(a,put(tmp_path,"b",doc()),capsys)
    assert code==2 and data["exit_code"]==2


def test_no_extra_io_using_loader_spy(tmp_path,capsys,monkeypatch):
    import runevidence.cli as cli
    a=doc(artifacts=[Artifact(id="a", path="nonexistent-dir", artifact_type="directory",
                                  digest_format="runevidence-directory-v1")])
    def load(path):return SimpleNamespace(path=path,manifest=a)
    def deny(*args,**kw): raise AssertionError("artifact I/O forbidden")
    with monkeypatch.context() as mp:
        # On Linux argparse's gettext lookup stats locale files while
        # build_parser() runs. LANGUAGE=C stops gettext before os.path.exists.
        mp.setenv("LANGUAGE","C")
        mp.setattr(cli,"load_manifest",load)
        from pathlib import Path
        import builtins,os,socket,urllib.request
        for obj,name in ((builtins,"open"),(Path,"open"),(Path,"stat"),(os,"stat"),
                         (os,"scandir"),(os,"listdir"),(socket.socket,"connect"),
                         (urllib.request,"urlopen")):
            mp.setattr(obj,name,deny)
        code,data=command(tmp_path/"a",tmp_path/"b",capsys)
    assert code==0 and data["explanation"]["summary"]["object_changes"]==0


def test_text_escapes_note_and_bounded_for_hostile_identifiers():
    a=doc(runs=[Run(id="r")],relationships=[Relationship(source="r",target="r",type=T.derived_from,note=None)])
    b=doc(runs=[Run(id="r")],relationships=[Relationship(source="r",target="r",type=T.derived_from,
                                 note="\x1b[31m\nINJECT"+"x"*200_000)])
    report=explain_fingerprint_difference(a,b)
    shown=format_fingerprint_explanation(report)
    assert len(shown)<=8192
    assert "\\u001b" in shown and "\x1b" not in shown and "\nINJECT" not in shown
    assert "row shortened" in shown


def test_text_truncates_many_rows_and_json_is_complete():
    a=doc(runs=[Run(id=f"r{i}") for i in range(120)])
    b=doc(runs=[Run(id=f"r{i}",name="updated") for i in range(120)])
    report=explain_fingerprint_difference(a,b)
    assert report["summary"]["object_changes"]==120
    assert len(report["objects"])==120
    shown=format_fingerprint_explanation(report)
    assert len(shown)<=8192 and "additional change row" in shown


def test_explain_text_cli_contract(tmp_path,capsys):
    a=put(tmp_path,"a",doc()); b=put(tmp_path,"b",doc(title="v2"))
    code,shown=command(a,b,capsys,as_json=False)
    assert code==1 and "DIFFERENT (exit 1)" in shown
    assert "Recorded change explanation: 1 object(s), 0 typed relationship(s)" in shown


def test_existing_text_without_explain_unchanged(tmp_path,capsys):
    a=put(tmp_path,"a",doc());b=put(tmp_path,"b",doc())
    code,shown=command(a,b,capsys,explain=False,as_json=False)
    assert code==0 and "Recorded change explanation" not in shown


def test_v1_format_refuses_unknown_optional_nondefault_field():
    from pydantic import create_model
    Future=create_model("FutureRunD14",__base__=Run,new_field=(str,""))
    a=doc(runs=[Run(id="r")])
    b=doc(runs=[Future(id="r",new_field="meaning")])
    with pytest.raises(FingerprintError,match="version bump"):
        explain_fingerprint_difference(a,b)


def test_randomized_v1_structural_explanation_agrees_with_d5_impact_and_hash():
    rng=random.Random(20261104)
    for _ in range(225):
        n=rng.randrange(1,7)
        before=doc(runs=[Run(id=f"r{i}",params={"epoch":i,"ok":bool(i&1)}) for i in range(n)],
                   evidence=[EvidenceReference(id="c",claim="claim",supports=["r0","r0"])])
        after=before.model_copy(deep=True)
        rng.shuffle(after.runs)
        if rng.random()<.55:
            after.runs[0].params["epoch"]+=1
        if rng.random()<.15:
            after.evidence[0].claim="different claim"
        if rng.random()<.25:
            after.project.name="newname"
        if rng.random()<.2:
            after.relationships.append(Relationship(source="r0",target="r0",type=T.derived_from,note=None))
        explanation=explain_fingerprint_difference(before,after)
        d5=compare_claim_impact(before,after,before_path="a",after_path="b")
        assert explanation["summary"]["object_changes"]==d5["summary"]["object_changes"]
        assert explanation["summary"]["relationship_changes"]==d5["summary"]["relationship_changes"]
        assert {o["id"] for o in explanation["objects"]}=={o["id"] for o in d5["changes"]["objects"]}
        changed=bool(explanation["objects"] or explanation["relationships"])
        assert changed==(fingerprint(before)!=fingerprint(after))
