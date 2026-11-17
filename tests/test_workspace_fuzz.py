"""Deterministic random-input robustness for the workspace layer.

The generators are seeded and bounded; these are regression tests for
"refuses cleanly", not a property-based proof or a coverage claim.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from runevidence.manifest import ManifestLoadError, load_manifest
from runevidence.workspace import (
    WORKSPACE_FILENAME,
    WorkspaceError,
    apply_patch,
    load_workspace,
)

VALUES = (
    None,
    True,
    False,
    0,
    1,
    -1,
    1.5,
    "",
    "x",
    [],
    {},
    [1],
    {"a": 1},
)


def random_value(rng: random.Random, depth: int = 0):
    if depth > 4 or rng.random() < 0.5:
        return rng.choice(VALUES)
    if rng.random() < 0.5:
        return [random_value(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    return {f"k{i}": random_value(rng, depth + 1) for i in range(rng.randint(0, 4))}


def valid_member(root: Path) -> None:
    member = root / "a"
    member.mkdir(parents=True)
    (member / "data.csv").write_bytes(b"x")
    (member / "runevidence.json").write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "project": {"id": "p", "name": "p"},
                "runs": [{"id": "r"}],
                "artifacts": [
                    {"id": "data", "path": "data.csv", "sha256": "0" * 64, "size_bytes": 1}
                ],
                "results": [{"id": "acc", "run_id": "r", "name": "accuracy", "value": 0.9}],
                "evidence": [],
            }
        ),
        encoding="utf-8",
    )


def test_random_workspace_documents_refuse_cleanly(tmp_path: Path):
    rng = random.Random(20261117)
    for index in range(150):
        root = tmp_path / f"doc-{index}"
        root.mkdir()
        try:
            document = random_value(rng)
            (root / WORKSPACE_FILENAME).write_text(json.dumps(document), encoding="utf-8")
        except (TypeError, ValueError):
            continue
        try:
            load_workspace(root)
        except WorkspaceError:
            continue


def test_random_patch_operations_refuse_cleanly(tmp_path: Path):
    rng = random.Random(20261118)
    root = tmp_path / "study"
    valid_member(root)
    (root / WORKSPACE_FILENAME).write_text(
        json.dumps(
            {
                "format": "rungraph-workspace",
                "version": "0.1",
                "id": "study",
                "name": "Study",
                "members": [{"id": "a", "path": "a"}],
                "claims": [],
                "relationships": [],
            }
        ),
        encoding="utf-8",
    )
    loaded = load_workspace(root)
    digest = hashlib.sha256((root / WORKSPACE_FILENAME).read_bytes()).hexdigest()
    operations = (
        "set-workspace",
        "add-member",
        "remove-member",
        "move-member",
        "rename-member",
        "add-claim",
        "edit-claim",
        "remove-claim",
        "add-relationship",
        "remove-relationship",
        "unknown",
        None,
    )
    payload = (None, True, 0, 1.5, "", "x", [], {}, [1], {"id": "x"}, {"member": "a", "id": "acc"})
    for _ in range(150):
        operation = {"op": rng.choice(operations)}
        for key in ("id", "path", "new_id", "member", "claim", "relationship"):
            if rng.random() < 0.5:
                operation[key] = rng.choice(payload)
        patch = {
            "format": "rungraph-patch",
            "version": "0.1",
            "operations": [operation],
        }
        try:
            apply_patch(loaded, patch, expected_sha256=digest, dry_run=True)
        except WorkspaceError:
            continue


def test_random_manifest_documents_refuse_cleanly(tmp_path: Path):
    rng = random.Random(20261119)
    for index in range(150):
        root = tmp_path / f"manifest-{index}"
        root.mkdir()
        try:
            document = random_value(rng)
            (root / "runevidence.json").write_text(json.dumps(document), encoding="utf-8")
        except (TypeError, ValueError):
            continue
        try:
            load_manifest(root)
        except ManifestLoadError:
            continue
