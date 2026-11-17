"""Markdown output for the single-manifest inspect route."""

from __future__ import annotations

import json
from pathlib import Path

from runevidence.cli import main

REPO = Path(__file__).resolve().parents[1]


def test_inspect_markdown_renders_the_example(capsys):
    assert main(["inspect", str(REPO / "examples" / "generic-experiment"), "--markdown"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Tiny sentiment classifier (tiny-sentiment)")
    for section in ("## Runs", "## Artifacts", "## Results", "## Claims", "## Findings"):
        assert section in out
    assert "| `run-baseline` |" in out
    assert "| ERROR" not in out


def test_inspect_markdown_escapes_claim_text(tmp_path: Path, capsys):
    document = {
        "schema_version": "0.1",
        "project": {"id": "p", "name": "Project"},
        "runs": [{"id": "r"}],
        "artifacts": [],
        "evidence": [
            {
                "id": "claim",
                "claim": "bad | `[link](https://x) <script>\x1b[31m",
                "supports": [],
                "contradicts": [],
            }
        ],
    }
    (tmp_path / "runevidence.json").write_text(json.dumps(document), encoding="utf-8")

    assert main(["inspect", str(tmp_path), "--markdown", "--strict"]) == 1
    out = capsys.readouterr().out
    assert "bad \\|" in out
    assert "\\<script\\>" in out
    assert "<script>" not in out
    assert "\x1b" not in out
