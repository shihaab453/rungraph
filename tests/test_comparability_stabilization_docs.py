"""The README/guide split schema stability from the experimental verdict CLI."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_readme_splits_schema_stability_from_the_verdict_cli():
    text = (REPO / "README.md").read_text(encoding="utf-8")

    assert "Schema 0.2 is stable" in text
    assert "verdict CLI" in text and "remains experimental" in text
    assert "read-only readiness" in text
    assert "Schema 0.2 (experimental)" not in text
    assert "remain evolving" not in text


def test_guide_marks_the_schema_stable_and_preflight_read_only():
    text = (REPO / "docs" / "comparability-schema-0.2.md").read_text(encoding="utf-8")

    assert "schema 0.2 is stable" in text
    assert "comparison verdict" in text and "remains experimental" in text
    assert "Readiness preflight (read-only)" in text
