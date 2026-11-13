"""D12: read-only SHA-256 pin check of recorded manifest fingerprints in CI."""
from __future__ import annotations

import builtins
import io
import json
import os
from pathlib import Path
import re
import socket
import urllib.request

import pytest

from runevidence.cli import main
from runevidence.manifest import dump_manifest
from runevidence.models import Artifact, EvidenceReference, Manifest, Project, Relationship, RelationshipType, Result, Run
from runevidence.run_fingerprint import manifest_fingerprint
from test_cli_fingerprint_golden import rich_record, GOLDEN_V1


def _doc():
    return Manifest(project=Project(id="project", name="demo"),
                    runs=[Run(id="r", params={"seed": 4})],
                    evidence=[EvidenceReference(id="claim", claim="ok", supports=["r"])])


def _file(tmp_path, manifest=None):
    p = tmp_path / "runevidence.json"
    p.write_text(dump_manifest(manifest or _doc()), encoding="utf8")
    return p


def _digest(m):
    return manifest_fingerprint(m, manifest_path="local.json")["fingerprint"]["digest"]


def test_existing_fingerprint_json_is_identical_without_expect(tmp_path, capsys):
    m = _doc(); p = _file(tmp_path, m)
    original = manifest_fingerprint(m, manifest_path=str(p))
    assert main(["fingerprint", str(p), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == original
    assert "verification" not in original


def test_existing_fingerprint_text_unchanged_without_expect(tmp_path, capsys):
    p = _file(tmp_path)
    assert main(["fingerprint", str(p)]) == 0
    output = capsys.readouterr().out
    assert "Fingerprint check:" not in output
    assert "Recorded fingerprint (sha256):" in output


def test_expected_correct_json_exit_zero_full_digest_and_readonly(tmp_path, capsys):
    p = _file(tmp_path); before = p.read_bytes(); expected = _digest(_doc())
    assert main(["fingerprint", str(p), "--expect", expected, "--json"]) == 0
    obj = json.loads(capsys.readouterr().out)
    assert obj["verification"] == {
        "expected_digest": expected, "actual_digest": expected,
        "matched": True, "exit_code": 0,
        "note": "Recorded manifest meaning only; not artifact integrity or scientific validation.",
    }
    assert obj["fingerprint"]["digest"] == expected
    assert p.read_bytes() == before
    assert sorted(x.name for x in tmp_path.iterdir()) == ["runevidence.json"]


def test_expected_uppercase_accepted_and_canonicalized(tmp_path, capsys):
    p = _file(tmp_path); d = _digest(_doc())
    assert main(["fingerprint", str(p), "--json", "--expect", d.upper()]) == 0
    assert json.loads(capsys.readouterr().out)["verification"]["expected_digest"] == d


def test_expected_wrong_json_exit_one_preserves_both_values(tmp_path, capsys):
    p = _file(tmp_path); before = p.read_bytes(); real = _digest(_doc())
    wrong = "0" * 64 if real != "0" * 64 else "f" * 64
    assert main(["fingerprint", str(p), "--json", "--expect", wrong]) == 1
    obj = json.loads(capsys.readouterr().out)
    assert obj["verification"]["matched"] is False
    assert obj["verification"]["exit_code"] == 1
    assert obj["verification"]["expected_digest"] == wrong
    assert obj["verification"]["actual_digest"] == real
    assert obj["fingerprint"]["digest"] == real
    assert p.read_bytes() == before


def test_expected_mismatch_text_exit_one(tmp_path, capsys):
    p = _file(tmp_path)
    assert main(["fingerprint", str(p), "--expect", "e" * 64]) == 1
    output = capsys.readouterr().out
    assert "Fingerprint check: MISMATCH (exit 1)" in output
    assert "Expected recorded SHA-256: " + "e" * 64 in output


def test_expected_match_text_exit_zero(tmp_path, capsys):
    p = _file(tmp_path); d = _digest(_doc())
    assert main(["fingerprint", str(p), "--expect", d]) == 0
    assert "Fingerprint check: MATCH (exit 0)" in capsys.readouterr().out


@pytest.mark.parametrize("bad", [
    "", " ", "0" * 63, "0" * 65, "0" * 63 + "g", "0x" + "0" * 64,
    "sha256:" + "a" * 64, "a" * 64 + "\n", "\u00e9" * 64,
    "-" * 64, "0" * 31 + " " + "0" * 32,
])
def test_invalid_expected_returns_two_json_without_opening_manifest(tmp_path, capsys, bad, monkeypatch):
    path = tmp_path / "missing.json"
    def should_not_load(*a, **kw):
        raise AssertionError("malformed pin must be rejected before manifest load")
    import runevidence.cli as cli
    with monkeypatch.context() as mp:
        mp.setattr(cli, "load_manifest", should_not_load)
        assert main(["fingerprint", str(path), "--expect=" + bad, "--json"]) == 2
    obj = json.loads(capsys.readouterr().out)
    assert obj == {"ok": False, "exit_code": 2,
                   "error": "--expect requires exactly 64 hexadecimal SHA-256 characters"}


def test_missing_manifest_with_valid_expected_returns_two_json(tmp_path, capsys):
    assert main(["fingerprint", str(tmp_path / "absent.json"), "--expect", "a" * 64, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["exit_code"] == 2


def test_schema_invalid_with_valid_expected_returns_two(tmp_path, capsys):
    p = tmp_path / "runevidence.json"
    p.write_text('{"project": "invalid"}', encoding="utf8")
    assert main(["fingerprint", str(p), "--json", "--expect", "a" * 64]) == 2
    assert json.loads(capsys.readouterr().out)["exit_code"] == 2


def test_known_golden_with_expected_is_stable_across_reload(tmp_path, capsys):
    p = _file(tmp_path, rich_record())
    d = GOLDEN_V1["rich_record"]
    assert main(["fingerprint", str(p), "--json", "--expect", d]) == 0
    assert json.loads(capsys.readouterr().out)["verification"]["actual_digest"] == d


def test_semantic_change_between_runs_triggers_mismatch(tmp_path, capsys):
    original = _doc(); p = _file(tmp_path, original); digest = _digest(original)
    updated = _doc(); updated.runs[0].params["seed"] = 5
    p.write_text(dump_manifest(updated), encoding="utf8")
    current = p.read_bytes()
    assert main(["fingerprint", str(p), "--expect", digest, "--json"]) == 1
    obj = json.loads(capsys.readouterr().out)
    assert obj["verification"]["actual_digest"] == _digest(updated)
    assert p.read_bytes() == current


def test_invalid_expected_text_reports_safe_error(tmp_path, capsys):
    p = _file(tmp_path)
    assert main(["fingerprint", str(p), "--expect", "\x1b[31mbad"]) == 2
    output = capsys.readouterr().out
    assert "\x1b" not in output
    assert "ERROR: --expect requires exactly 64 hexadecimal" in output


def test_legacy_coverage_gate_works_with_new_fingerprint_option(tmp_path, capsys):
    p = _file(tmp_path)
    assert main(["coverage-gate", str(p), "--json"]) in (0, 1)
    assert "gate" in json.loads(capsys.readouterr().out)


def test_byte_invariant_when_json_whitespace_changes(tmp_path, capsys):
    p = _file(tmp_path); d = _digest(_doc())
    raw = json.loads(p.read_text())
    p.write_text(json.dumps(raw, sort_keys=True, indent=4), encoding="utf8")
    assert main(["fingerprint", str(p), "--expect", d, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["verification"]["matched"]


def test_explicit_default_only_addition_preserves_expected(tmp_path, capsys):
    p = _file(tmp_path)
    d = _digest(_doc())
    raw = json.loads(p.read_text());raw["runs"][0]["name"] = None
    p.write_text(json.dumps(raw), encoding="utf8")
    assert main(["fingerprint", str(p), "--json", "--expect", d]) == 0
    assert json.loads(capsys.readouterr().out)["verification"]["matched"]


def test_no_artifact_network_or_extra_fs_checks_for_direct_fingerprint(monkeypatch, tmp_path):
    # Explicit instrumentation, still not a security audit of all OS interfaces.
    m = Manifest(project=Project(id="p", name="demo"),
                 artifacts=[Artifact(id="a", path="nonexistent-directory", artifact_type="directory",
                                     digest_format="runevidence-directory-v1")],
                 evidence=[EvidenceReference(id="c", claim="x", source="https://invalid.example", supports=["a"])])
    prior = _digest(m)
    def stop(*args, **kwargs):
        raise AssertionError("unexpected file or network I/O during canonicalization")
    with monkeypatch.context() as mp:
        for obj, name in ((builtins, "open"), (io, "open"), (os, "open"), (os, "stat"),
                          (os, "scandir"), (os, "listdir"), (os.path, "exists"),
                          (os.path, "isfile"), (Path, "open"), (Path, "stat"),
                          (Path, "exists"), (Path, "iterdir"), (Path, "glob"),
                          (Path, "rglob"), (Path, "read_bytes"), (Path, "read_text"),
                          (urllib.request, "urlopen"), (socket.socket, "connect"),
                          (socket.socket, "connect_ex"), (socket, "create_connection")):
            mp.setattr(obj, name, stop)
        assert _digest(m) == prior


def test_full_digest_unchanged_for_all_model_kinds_when_allow_extras_empty():
    from pydantic import ConfigDict
    kinds = (
        (Project, dict(id="p", name="demo"), "project"),
        (Run, dict(id="r"), "runs"),
        (Artifact, dict(id="a", path="file"), "artifacts"),
        (Result, dict(id="x", name="metric", run_id="r", value=1), "results"),
        (EvidenceReference, dict(id="c", claim="c"), "evidence"),
        (Relationship, dict(source="r", target="r", type=RelationshipType.derived_from), "relationships"),
    )
    for klass, fields, kind in kinds:
        class Expanded(klass):
            model_config = ConfigDict(extra="allow")
        a = _doc(); b = _doc()
        if kind == "project":
            a.project = klass(**fields); b.project = Expanded(**fields)
        else:
            setattr(a, kind, [klass(**fields)]); setattr(b, kind, [Expanded(**fields)])
        assert _digest(a) == _digest(b), kind
