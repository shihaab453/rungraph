"""D13: two-snapshot recorded semantic equality CI gate, not artifact integrity."""
from __future__ import annotations

import builtins
import io
import json
import os
from pathlib import Path
import socket
import urllib.request

import pytest

from runevidence.cli import main
from runevidence.manifest import dump_manifest
from runevidence.models import (
    Artifact, EvidenceReference, Manifest, Project, Relationship,
    RelationshipType, Result, Run,
)
from runevidence.run_fingerprint import manifest_fingerprint
from test_cli_fingerprint_golden import rich_record, GOLDEN_V1


def m(*, name="study", run_seed=5, results=(), relationships=(), artifacts=(), evidence=()):
    return Manifest(project=Project(id="p", name=name), runs=[Run(id="r", params={"seed": run_seed})],
                    results=list(results), relationships=list(relationships),
                    artifacts=list(artifacts), evidence=list(evidence))


def write(tmp_path: Path, stem: str, manifest: Manifest) -> Path:
    path = tmp_path / f"{stem}.json"
    path.write_text(dump_manifest(manifest), encoding="utf-8")
    return path


def command(before: Path, after: Path, capsys, *, text=False):
    args = ["fingerprint-compare", str(before), str(after)]
    if not text:
        args.append("--json")
    status = main(args)
    out = capsys.readouterr().out
    return status, out if text else json.loads(out)


def sha(m):
    return manifest_fingerprint(m, manifest_path="ignored.json")["fingerprint"]


def test_equal_snapshots_exit_zero_full_fingerprints_and_read_only(tmp_path, capsys):
    left, right = write(tmp_path, "before", rich_record()), write(tmp_path, "after", rich_record())
    baseline = {p.name: p.read_bytes() for p in (left, right)}
    code, obj = command(left, right, capsys)
    assert code == 0 and obj["comparison"]["matched"] is True
    assert obj["comparison"]["exit_code"] == 0
    assert obj["before_fingerprint"] == obj["after_fingerprint"] == sha(rich_record())
    assert obj["before_fingerprint"]["digest"] == GOLDEN_V1["rich_record"]
    assert obj["before_manifest"] == str(left) and obj["after_manifest"] == str(right)
    assert "artifact contents" in obj["comparison"]["note"]
    assert {p.name: p.read_bytes() for p in (left, right)} == baseline
    assert sorted(p.name for p in tmp_path.iterdir()) == ["after.json", "before.json"]


def test_different_recorded_assertions_exit_one_with_both_full_hashes(tmp_path, capsys):
    before = m(); after = m(run_seed=6)
    p, q = write(tmp_path, "one", before), write(tmp_path, "two", after)
    code, data = command(p, q, capsys)
    assert code == data["comparison"]["exit_code"] == 1
    assert data["comparison"]["matched"] is False
    assert data["before_fingerprint"]["digest"] == sha(before)["digest"]
    assert data["after_fingerprint"]["digest"] == sha(after)["digest"]
    assert data["before_fingerprint"]["digest"] != data["after_fingerprint"]["digest"]
    assert len(data["before_fingerprint"]["digest"]) == 64
    assert len(data["after_fingerprint"]["digest"]) == 64


def test_manifest_paths_are_metadata_only(tmp_path, capsys):
    p = write(tmp_path, "one", m())
    code, data = command(p, p, capsys)
    assert code == 0 and data["before_manifest"] == data["after_manifest"]
    assert data["comparison"]["matched"] is True


def test_order_and_duplicate_relationship_permutations_are_equal(tmp_path, capsys):
    rel = Relationship(source="r", target="r", type=RelationshipType.derived_from)
    a = m(relationships=[rel, rel], evidence=[EvidenceReference(id="c", claim="c", supports=["r", "r"])])
    b = m(relationships=[rel], evidence=[EvidenceReference(id="c", claim="c", supports=["r"])])
    p, q = write(tmp_path, "p", a), write(tmp_path, "q", b)
    code, data = command(p, q, capsys)
    assert code == 0 and data["comparison"]["matched"] is True
    assert data["before_fingerprint"]["relationships"] == 1


def test_nullable_relationship_notes_are_distinct(tmp_path, capsys):
    r1 = Relationship(source="r", target="r", type=RelationshipType.derived_from, note=None)
    r2 = Relationship(source="r", target="r", type=RelationshipType.derived_from, note="")
    code, data = command(write(tmp_path, "a", m(relationships=[r1])),
                         write(tmp_path, "b", m(relationships=[r2])), capsys)
    assert code == 1 and data["comparison"]["matched"] is False


def test_whitespace_and_schema_default_are_ignored(tmp_path, capsys):
    p = write(tmp_path, "a", m()); q = tmp_path / "b.json"
    raw = json.loads(p.read_text(encoding="utf-8"))
    raw["runs"][0]["name"] = None
    q.write_text(json.dumps(raw, sort_keys=True, indent=5), encoding="utf-8")
    code, data = command(p, q, capsys)
    assert code == 0 and data["comparison"]["matched"]


@pytest.mark.parametrize("value", [True, 1, 1.0, -0.0, float("nan"), float("inf"), None])
def test_scalar_change_to_another_type_refuses_semantic_equality(tmp_path, capsys, value):
    baseline = m(results=[Result(id="metric", run_id="r", name="accuracy", value="baseline")])
    candidate = m(results=[Result(id="metric", run_id="r", name="accuracy", value=value)])
    code, data = command(write(tmp_path, "a", baseline), write(tmp_path, "b", candidate), capsys)
    assert code == 1 and not data["comparison"]["matched"]


def test_project_only_change_changes_digest(tmp_path, capsys):
    code, data = command(write(tmp_path, "p", m(name="old")),
                         write(tmp_path, "q", m(name="new")), capsys)
    assert code == 1 and not data["comparison"]["matched"]


def test_artifact_digest_edit_changes_recorded_hash_not_file_access(tmp_path, capsys):
    a = m(artifacts=[Artifact(id="a", path="not-here", sha256="a" * 64)])
    b = m(artifacts=[Artifact(id="a", path="not-here", sha256="b" * 64)])
    code, data = command(write(tmp_path, "a", a), write(tmp_path, "b", b), capsys)
    assert code == 1 and not data["comparison"]["matched"]


def test_missing_first_snapshot_returns_two_without_opening_second(tmp_path, capsys, monkeypatch):
    absent = tmp_path / "absent.json"
    other = write(tmp_path, "exists", m())
    code, data = command(absent, other, capsys)
    assert code == 2 and data["exit_code"] == 2


def test_missing_second_snapshot_returns_two(tmp_path, capsys):
    first = write(tmp_path, "exists", m())
    code, data = command(first, tmp_path / "absent.json", capsys)
    assert code == 2 and data["exit_code"] == 2


def test_schema_invalid_snapshot_returns_two(tmp_path, capsys):
    p = write(tmp_path, "valid", m())
    q = tmp_path / "invalid.json";q.write_text('{"project": 17}', encoding="utf-8")
    code, data = command(p, q, capsys)
    assert code == 2 and data["exit_code"] == 2


def test_ambiguous_global_id_in_before_returns_two(tmp_path, capsys):
    broken = m(artifacts=[Artifact(id="r", path="a")])
    code, data = command(write(tmp_path, "bad", broken), write(tmp_path, "good", m()), capsys)
    assert code == 2 and data["exit_code"] == 2
    assert data["ok"] is False and "duplicate object ID" in data["error"]


def test_ambiguous_global_id_in_after_returns_two(tmp_path, capsys):
    broken = m(evidence=[EvidenceReference(id="p", claim="duplicate project ID")])
    code, data = command(write(tmp_path, "good", m()), write(tmp_path, "bad", broken), capsys)
    assert code == 2 and data["exit_code"] == 2
    assert data["ok"] is False and "duplicate object ID" in data["error"]


def test_text_reports_match_and_mismatch(tmp_path, capsys):
    p, q, r = write(tmp_path, "a", m()), write(tmp_path, "b", m()), write(tmp_path, "c", m(run_seed=9))
    code, result = command(p, q, capsys, text=True)
    assert code == 0 and "MATCH (exit 0)" in result
    assert "Recorded fingerprint (sha256):" in result
    code, result = command(p, r, capsys, text=True)
    assert code == 1 and "DIFFERENT (exit 1)" in result


def test_cli_requires_both_manifest_args(capsys):
    assert main(["fingerprint-compare", "one.json"]) == 2
    assert "required" in capsys.readouterr().err


def test_d12_existing_pin_command_retains_behavior(tmp_path, capsys):
    p = write(tmp_path, "x", m())
    digest = sha(m())["digest"]
    assert main(["fingerprint", str(p), "--expect", digest, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["verification"]["matched"]


def test_legacy_coverage_gate_exact_exit_one_when_claim_unsupported(tmp_path, capsys):
    doc = m(evidence=[EvidenceReference(id="c", claim="unsupported")])
    p = write(tmp_path, "x", doc)
    code = main(["coverage-gate", str(p), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert code == 1 and data["gate"]["exit_code"] == 1
    assert data["gate"]["failing_claims"] == 1


def test_format_text_escapes_long_paths_and_control_characters(tmp_path, capsys):
    # Uses direct monkeypatch because actual filesystem filenames cannot contain NUL.
    from runevidence import cli
    from types import SimpleNamespace
    p = write(tmp_path, "ordinary", m())
    original = cli.load_manifest
    def fake(path):
        data = original(p)
        return SimpleNamespace(path=Path("record\nINJECT\x1b[31m" + "x" * 10000), manifest=data.manifest)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cli, "load_manifest", fake)
        code, out = command(p, p, capsys, text=True)
    assert code == 0 and "\\nINJECT" in out and "\nINJECT" not in out and "\x1b" not in out
    assert len(out) < 20000


def test_text_and_json_report_do_not_make_scientific_proof_claim(tmp_path, capsys):
    p = write(tmp_path, "p", m())
    _, data = command(p, p, capsys)
    assert "not scientific validation" in data["comparison"]["note"]
    _, shown = command(p, p, capsys, text=True)
    assert "not scientific validation" in shown


def test_no_extra_artifact_io_during_fingerprint_calculation(monkeypatch):
    import runevidence.cli as cli
    document = m(artifacts=[Artifact(id="a", path="missing-directory", artifact_type="directory",
                                     digest_format="runevidence-directory-v1")],
                 evidence=[EvidenceReference(id="c", claim="x", source="https://invalid.example", supports=["a"])])
    from types import SimpleNamespace
    # load_manifest is the permitted manifest reader. Trap extra filesystem/network
    # work only once loading is bypassed with an already validated object.
    def no_load(path): return SimpleNamespace(path=Path(str(path)), manifest=document)
    def deny(*args, **kwargs): raise AssertionError("unexpected external I/O")
    with monkeypatch.context() as mp:
        # On Linux argparse's gettext lookup stats locale files while
        # build_parser() runs. LANGUAGE=C stops gettext before os.path.exists.
        mp.setenv("LANGUAGE", "C")
        mp.setattr(cli, "load_manifest", no_load)
        for obj, name in ((builtins,"open"),(io,"open"),(os,"stat"),(os,"scandir"),
                          (os,"listdir"),(Path,"open"),(Path,"read_bytes"),
                          (urllib.request,"urlopen"),(socket.socket,"connect")):
            mp.setattr(obj,name,deny)
        assert main(["fingerprint-compare", "before", "after", "--json"]) == 0


def test_artifact_io_trap_still_catches_a_stat(monkeypatch, tmp_path):
    """Negative control: the LANGUAGE=C fix must not weaken the I/O trap."""
    import runevidence.cli as cli
    document = m(artifacts=[Artifact(id="a", path="missing-directory", artifact_type="directory",
                                     digest_format="runevidence-directory-v1")])
    from types import SimpleNamespace
    def no_load(path): return SimpleNamespace(path=Path(str(path)), manifest=document)
    def deny(*args, **kwargs): raise AssertionError("unexpected external I/O")
    real_fingerprint = cli.manifest_fingerprint
    def stat_fingerprint(*args, **kwargs):
        os.stat(tmp_path / "artifact")
        return real_fingerprint(*args, **kwargs)
    with monkeypatch.context() as mp:
        mp.setenv("LANGUAGE", "C")
        mp.setattr(cli, "load_manifest", no_load)
        mp.setattr(cli, "manifest_fingerprint", stat_fingerprint)
        for obj, name in ((builtins,"open"),(io,"open"),(os,"stat"),(os,"scandir"),
                          (os,"listdir"),(Path,"open"),(Path,"read_bytes"),
                          (urllib.request,"urlopen"),(socket.socket,"connect")):
            mp.setattr(obj,name,deny)
        with pytest.raises(AssertionError, match="unexpected external I/O"):
            main(["fingerprint-compare", "before", "after", "--json"])
