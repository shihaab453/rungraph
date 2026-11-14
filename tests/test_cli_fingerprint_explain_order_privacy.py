"""D14R2: a redacted explanation must not encode private note ordering.

Keep the private raw Python explanation ABI and fingerprint-v1 hashing unchanged.
These tests exercise the presentation boundary, including CLI text and JSON.
"""
from __future__ import annotations

from copy import deepcopy
import json
import random

import pytest

from runevidence.models import Manifest, Project, Run, Relationship, RelationshipType
from runevidence.run_fingerprint import manifest_fingerprint
from runevidence.run_fingerprint_explain import (
    explain_fingerprint_difference, redact_fingerprint_explanation,
    format_fingerprint_explanation,
)


def doc(notes=(), *, source="a", target="b"):
    return Manifest(
        project=Project(id="p", name="demo"),
        runs=[Run(id="a"), Run(id="b")],
        relationships=[Relationship(source=source, target=target,
                                    type=RelationshipType.derived_from, note=note)
                       for note in notes],
    )


def display(old, new):
    return redact_fingerprint_explanation(explain_fingerprint_difference(old, new))


def rows(report):
    return report["relationships"]


@pytest.mark.parametrize("old,new", [
    ("ALPHA-secret", "ZULU-secret"),
    ("ZULU-secret", "ALPHA-secret"),
    ("a", "b"),
    ("b", "a"),
])
def test_redacted_added_removed_order_does_not_depend_on_secret_lexical_rank(old, new):
    report = display(doc([old]), doc([new]))
    assert report["summary"]["relationship_changes"] == 2
    assert [(r["change"], r["note"], r["note_redacted"]) for r in rows(report)] == [
        ("added", "<redacted>", True), ("removed", "<redacted>", True),
    ]
    shown = json.dumps(report, sort_keys=True)
    assert all(item["note"] not in (old, new) for item in rows(report))


def test_multiple_secret_notes_have_identical_redacted_output_across_permutations():
    a = display(doc(["a", "c", "e"]), doc(["b", "d", "f"]))
    b = display(doc(["b", "d", "f"]), doc(["a", "c", "e"]))
    assert a == b
    assert [r["change"] for r in rows(a)] == ["added"] * 3 + ["removed"] * 3
    assert all(r["note"] == "<redacted>" for r in rows(a))


def test_none_empty_nonempty_note_categories_keep_public_distinction():
    # A null-to-empty edit and a private-note edit share one type/endpoints.
    report = display(doc([None, "v-private"]), doc(["", "w-private"]))
    assert [(r["note"], r["change"]) for r in rows(report)] == [
        (None, "removed"), ("", "added"),
        ("<redacted>", "added"), ("<redacted>", "removed"),
    ]
    assert report["summary"]["relationship_changes"] == 4


def test_presentation_is_copy_and_explicit_show_notes_keeps_original_order():
    original = explain_fingerprint_difference(doc(["z"]), doc(["a"]))
    saved = deepcopy(original)
    assert [r["change"] for r in rows(original)] == ["added", "removed"]
    safe = redact_fingerprint_explanation(original)
    raw = redact_fingerprint_explanation(original, show_notes=True)
    assert original == saved
    assert raw["relationships"] == original["relationships"]
    assert raw["notes_redacted"] is False
    assert safe["notes_redacted"] is True
    assert safe["relationships"] is not original["relationships"]
    assert [r["note"] for r in rows(safe)] == ["<redacted>", "<redacted>"]


def test_randomized_private_note_substitutions_never_change_public_order():
    rng = random.Random(20261108)
    for _ in range(200):
        values = [f"{rng.randrange(10**12):012d}-{i}" for i in range(5)]
        left = doc(values[:2]); right = doc(values[2:4])
        a = display(left, right)
        b = display(right, left)
        # Count and exposed metadata are the same; raw contents differ.
        assert a == b
        assert all(row["note_redacted"] is True for row in rows(a))
        assert [row["change"] for row in rows(a)] == ["added"] * 2 + ["removed"] * 2


def test_terminal_text_does_not_restore_hidden_note_order_or_values():
    first = format_fingerprint_explanation(display(doc(["a"]), doc(["z"])))
    second = format_fingerprint_explanation(display(doc(["z"]), doc(["a"])))
    assert first == second
    assert "<redacted>" in first
    assert "Relationship notes redacted" in first
    assert "\"note\": \"a\"" not in first and "\"note\": \"z\"" not in first
    for line in first.splitlines():
        if line.startswith("{"):
            assert isinstance(json.loads(line), dict)


def test_public_relation_coordinates_sort_before_note_category():
    # Identity fields are deliberately public; note ordering must not drive ties.
    a = doc(["z"], source="b", target="a")
    b = doc(["a"], source="a", target="b")
    report = display(a, b)
    assert [(r["source"], r["target"], r["change"]) for r in rows(report)] == [
        ("a", "b", "added"), ("b", "a", "removed"),
    ]


def test_fingerprints_not_changed_by_rendering_a_redacted_copy():
    a, b = doc(["alpha-secret"]), doc(["zulu-secret"])
    before = (manifest_fingerprint(a, manifest_path="a")["fingerprint"]["digest"],
              manifest_fingerprint(b, manifest_path="b")["fingerprint"]["digest"])
    _ = display(a, b)
    after = (manifest_fingerprint(a, manifest_path="a")["fingerprint"]["digest"],
             manifest_fingerprint(b, manifest_path="b")["fingerprint"]["digest"])
    assert before == after and before[0] != before[1]


def test_cli_json_default_redaction_order_is_independent_of_lexical_rank(tmp_path, capsys):
    from runevidence.cli import main
    from runevidence.manifest import dump_manifest
    def call(old, new):
        p = tmp_path / "left.json"; q = tmp_path / "right.json"
        p.write_text(dump_manifest(doc([old])), encoding="utf-8")
        q.write_text(dump_manifest(doc([new])), encoding="utf-8")
        assert main(["fingerprint-compare", str(p), str(q), "--explain", "--json"]) == 1
        value = json.loads(capsys.readouterr().out)
        return value["explanation"]
    assert call("a-private", "z-private") == call("z-private", "a-private")


def test_cli_text_default_redaction_order_is_independent_of_lexical_rank(tmp_path, capsys):
    from runevidence.cli import main
    from runevidence.manifest import dump_manifest
    def call(old, new):
        p = tmp_path / "left.json"; q = tmp_path / "right.json"
        p.write_text(dump_manifest(doc([old])), encoding="utf-8")
        q.write_text(dump_manifest(doc([new])), encoding="utf-8")
        assert main(["fingerprint-compare", str(p), str(q), "--explain"]) == 1
        text = capsys.readouterr().out
        return text[text.index("Recorded change explanation:"):]
    assert call("a-private", "z-private") == call("z-private", "a-private")
