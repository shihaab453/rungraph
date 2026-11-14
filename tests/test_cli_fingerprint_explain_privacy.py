"""D14R1: safe-by-default CLI presentation without changing fingerprint v1 bytes."""
from __future__ import annotations

import json

import pytest

from runevidence.cli import main
from runevidence.manifest import dump_manifest
from runevidence.models import Manifest, Project, Run, Relationship, RelationshipType
from runevidence.run_fingerprint_explain import (
    explain_fingerprint_difference, format_fingerprint_explanation,
    redact_fingerprint_explanation,
)


SECRET = "private-token-ALPHA-123!"


def document(note=None):
    return Manifest(
        project=Project(id="study", name="demo"),
        runs=[Run(id="one"), Run(id="two")],
        relationships=[Relationship(source="one", target="two",
                                    type=RelationshipType.derived_from, note=note)],
    )


def run(tmp_path, capsys, old=None, new=None, *flags):
    p = tmp_path / "before.json"
    q = tmp_path / "after.json"
    p.write_text(dump_manifest(old if old is not None else document()), encoding="utf-8")
    q.write_text(dump_manifest(new if new is not None else document(SECRET)), encoding="utf-8")
    status = main(["fingerprint-compare", str(p), str(q), *flags])
    out = capsys.readouterr()
    return status, out.out, out.err


def test_default_explain_json_redacts_actual_note_contents(tmp_path, capsys):
    status, shown, _ = run(tmp_path, capsys, None, None, "--explain", "--json")
    assert status == 1
    parsed = json.loads(shown)
    assert parsed["comparison"]["matched"] is False
    assert parsed["explanation"]["notes_redacted"] is True
    assert SECRET not in shown
    assert len(parsed["explanation"]["relationships"]) == 2
    row = next(r for r in parsed["explanation"]["relationships"] if r["change"] == "added")
    assert row["note"] == "<redacted>" and row["note_redacted"] is True
    assert parsed["explanation"]["summary"]["relationship_changes"] == 2


def test_default_explain_text_redacts_note_contents_and_discloses_policy(tmp_path, capsys):
    status, shown, _ = run(tmp_path, capsys, None, None, "--explain")
    assert status == 1
    assert SECRET not in shown
    assert "<redacted>" in shown
    assert "notes redacted" in shown
    assert "DIFFERENT (exit 1)" in shown


def test_explicit_show_notes_json_reveals_exact_recorded_value(tmp_path, capsys):
    status, shown, _ = run(tmp_path, capsys, None, None, "--explain", "--json", "--show-notes")
    assert status == 1
    parsed = json.loads(shown)
    assert parsed["explanation"]["notes_redacted"] is False
    assert any(r["note"] == SECRET for r in parsed["explanation"]["relationships"])


def test_explicit_show_notes_terminal_controls_escaped(tmp_path, capsys):
    secret = "note\x1b[31m\r\nINJECT"
    status, shown, _ = run(tmp_path, capsys, document(), document(secret), "--explain", "--show-notes")
    assert status == 1
    assert "\\u001b" in shown and "\\r" in shown and "\\nINJECT" in shown
    assert "\x1b" not in shown and "\nINJECT" not in shown


@pytest.mark.parametrize("as_json", [True, False])
def test_show_notes_requires_explain_and_rejects_without_manifest_read(tmp_path, capsys, monkeypatch, as_json):
    import runevidence.cli as cli
    def refuse(*args, **kwargs):
        raise AssertionError("incorrectly tried manifest I/O")
    monkeypatch.setattr(cli, "load_manifest", refuse)
    args = ["fingerprint-compare", str(tmp_path / "none"), str(tmp_path / "none2"), "--show-notes"]
    if as_json:
        args.append("--json")
    assert main(args) == 2
    out = capsys.readouterr().out
    if as_json:
        obj = json.loads(out)
        assert obj["exit_code"] == 2
        assert "requires --explain" in obj["error"]
    else:
        assert "requires --explain" in out


def test_none_empty_and_nonempty_notes_are_distinguished_without_exposing_content():
    before = document(None)
    after = document("")
    report = redact_fingerprint_explanation(explain_fingerprint_difference(before, after))
    assert report["notes_redacted"] is True
    assert {r["note"] for r in report["relationships"]} == {None, ""}
    assert all(r["note_redacted"] is False for r in report["relationships"])
    another = redact_fingerprint_explanation(explain_fingerprint_difference(after, document(SECRET)))
    assert any(r["note"] == "<redacted>" and r["note_redacted"] for r in another["relationships"])
    assert SECRET not in json.dumps(another)


def test_two_distinct_nonempty_notes_still_counted_as_changes():
    out = redact_fingerprint_explanation(explain_fingerprint_difference(document(SECRET), document("other SECRET")))
    assert out["summary"]["relationship_changes"] == 2
    assert len(out["relationships"]) == 2
    assert all(r["note"] == "<redacted>" for r in out["relationships"])


def test_output_without_explain_is_identical_regardless_of_other_new_option_absence(tmp_path, capsys):
    status, shown, _ = run(tmp_path, capsys, document(), document(SECRET), "--json")
    assert status == 1
    parsed = json.loads(shown)
    assert set(parsed) == {"before_manifest", "after_manifest", "before_fingerprint", "after_fingerprint", "comparison"}
    assert SECRET not in shown


def test_row_shortening_keeps_each_presented_row_valid_json():
    evil = "\u1234" * 120 + "\x1b[0m" + "X" * 2500
    report = explain_fingerprint_difference(document(), document(evil))
    lines = format_fingerprint_explanation(report).splitlines()
    assert "row shortened" in "\n".join(lines)
    assert len("\n".join(lines)) <= 8192
    for row in lines[1:]:
        if row.startswith("{"):
            value = json.loads(row)
            assert isinstance(value, dict)
            if value.get("row_shortened"):
                assert value["message"] == "row shortened; use --json for the complete row"


def test_whole_cli_output_note_is_not_present_in_unrelated_fields(tmp_path, capsys):
    status, shown, _ = run(tmp_path, capsys, document(SECRET), document("other private"), "--explain", "--json")
    assert status == 1
    assert SECRET not in shown and "other private" not in shown
    parsed = json.loads(shown)
    assert len(parsed["before_fingerprint"]["digest"]) == len(parsed["after_fingerprint"]["digest"]) == 64


def test_null_vs_empty_change_does_not_get_hidden_by_redaction(tmp_path, capsys):
    status, shown, _ = run(tmp_path, capsys, document(None), document(""), "--explain", "--json")
    assert status == 1
    rows = json.loads(shown)["explanation"]["relationships"]
    assert len(rows) == 2
    assert {r["note"] for r in rows} == {None, ""}
    assert {r["change"] for r in rows} == {"removed", "added"}
