"""Public docs must keep up with the shipped schemas, flags and migrate targets."""

from __future__ import annotations

import re
from pathlib import Path

from runevidence.migrate import TARGET_VERSIONS

REPO = Path(__file__).resolve().parents[1]
README = (REPO / "README.md").read_text(encoding="utf-8")
GUIDE = (REPO / "docs" / "tensorboard-guide.md").read_text(encoding="utf-8")

PAGES = {
    "0.5": "schema-0.5-tensorboard.md",
    "0.6": "schema-0.6-tensorboard-strings.md",
    "0.7": "schema-0.7-tensorboard-histograms.md",
    "0.8": "schema-0.8-tensorboard-media.md",
    "0.9": "schema-0.9-tensorboard-tensors.md",
    "0.10": "schema-0.10-tf2-media.md",
    "0.11": "schema-0.11-tf2-histograms.md",
}


def test_every_released_schema_has_a_page_named_in_the_readme():
    for version, page in PAGES.items():
        assert (REPO / "docs" / page).exists(), f"missing page for schema {version}"
        assert page in README, f"{page} is not linked from README.md"
        assert page in GUIDE, f"{page} is not linked from the guide"
        assert f"Schema {version}" in README, f"schema {version} is not described in README.md"


def test_guide_names_every_init_include_flag():
    for flag in (
        "--include-strings",
        "--include-histograms",
        "--include-images",
        "--include-audio",
        "--include-tensors",
        "--include-tf2-media",
        "--include-tf2-histograms",
    ):
        assert flag in GUIDE, f"{flag} is missing from the TensorBoard guide"
        assert flag in README, f"{flag} is missing from README.md"


def test_readme_names_every_migrate_target():
    listed = "0.2\\|0.3\\|0.4\\|0.5\\|0.6\\|0.7\\|0.8\\|0.9\\|0.10\\|0.11"
    assert f"--to {listed}" in README, "the README migrate row is missing a target"
    assert TARGET_VERSIONS[-1] == "0.11"


def test_guide_locator_kinds_match_the_models():
    from runevidence.models import (
        TensorboardAudioLocator,
        TensorboardHistogramLocator,
        TensorboardImageLocator,
        TensorboardLocator,
        TensorboardStringLocator,
        TensorboardTensorLocator,
    )

    for model in (
        TensorboardLocator,
        TensorboardStringLocator,
        TensorboardHistogramLocator,
        TensorboardImageLocator,
        TensorboardAudioLocator,
        TensorboardTensorLocator,
    ):
        annotation = model.model_fields["kind"].annotation
        kind = annotation.__args__[0]
        assert kind in GUIDE, f"locator kind {kind} is missing from the guide"


def test_guide_lists_every_include_flag_in_the_table():
    table_rows = [line for line in GUIDE.splitlines() if line.startswith("| `")]
    for flag in (
        "--include-strings",
        "--include-histograms",
        "--include-images",
        "--include-audio",
        "--include-tensors",
        "--include-tf2-media",
        "--include-tf2-histograms",
    ):
        assert any(flag in row for row in table_rows), f"{flag} has no guide table row"

def test_workspace_guide_names_every_shipped_route():
    guide = (REPO / "docs" / "rungraph-workspace.md").read_text(encoding="utf-8")
    for route in (
        "discover",
        "init",
        "validate",
        "graph",
        "report",
        "impact",
        "compare",
        "matrix",
        "leaderboard",
        "patch",
        "member",
        "claim",
        "relationship",
    ):
        assert f"rungraph {route}" in guide, f"rungraph {route} is missing from the workspace guide"
    assert "--baseline" in guide
    assert "rungraph-workspace.md" in README, "the README does not link the workspace guide"


def test_readme_names_every_public_cli_route():
    import argparse

    from runevidence.cli import build_parser

    parser = build_parser()
    sub = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    for route in sub.choices:
        assert f"runevidence {route}" in README, f"runevidence {route} is missing from the README"


def test_readme_names_every_workspace_route():
    import argparse

    from runevidence.rungraph_cli import build_parser

    parser = build_parser()
    sub = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    for route in sub.choices:
        assert f"rungraph {route}" in README, f"rungraph {route} is missing from the README"


def test_every_public_route_flag_is_named_in_the_reference():
    import argparse

    from runevidence.cli import build_parser
    from runevidence.rungraph_cli import build_parser as build_workspace_parser

    workspace_guide = (REPO / "docs" / "rungraph-workspace.md").read_text(encoding="utf-8")
    for parser, document, prefix in (
        (build_parser(), README, "runevidence"),
        (build_workspace_parser(), workspace_guide, "rungraph"),
    ):
        sub = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
        for route, command in sub.choices.items():
            for action in command._actions:
                for option in action.option_strings:
                    if option in ("-h", "--help"):
                        continue
                    assert option in document, f"{prefix} {route}: {option} is not documented"
