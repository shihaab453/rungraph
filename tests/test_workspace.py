"""Workspace document, members and read-only discovery."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from runevidence.workspace import (
    WORKSPACE_FILENAME,
    Discovery,
    WorkspaceError,
    discover,
    load_workspace,
)

MANIFEST = '{"schema_version": "0.1", "project": {"id": "p", "name": "p"}}'


def make_manifest(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "runevidence.json").write_text(MANIFEST, encoding="utf-8")


def write_workspace(root: Path, **overrides) -> Path:
    doc = {
        "format": "rungraph-workspace",
        "version": "0.1",
        "id": "study",
        "name": "Study",
        "members": [{"id": "a", "path": "a"}],
    }
    doc.update(overrides)
    path = root / WORKSPACE_FILENAME
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_a_minimal_workspace_loads(tmp_path: Path):
    make_manifest(tmp_path / "a")
    write_workspace(tmp_path)
    loaded = load_workspace(tmp_path)
    assert loaded.document.id == "study"
    assert loaded.members["a"].manifest is not None
    assert loaded.members["a"].error is None


def test_unknown_fields_are_refused(tmp_path: Path):
    make_manifest(tmp_path / "a")
    write_workspace(tmp_path, unexpected=True)
    with pytest.raises(WorkspaceError, match="invalid workspace"):
        load_workspace(tmp_path)


def test_duplicate_json_keys_are_refused(tmp_path: Path):
    make_manifest(tmp_path / "a")
    (tmp_path / WORKSPACE_FILENAME).write_text(
        '{"format":"rungraph-workspace","version":"0.1","id":"s","name":"S",'
        '"members":[{"id":"a","path":"a"}],"members":[]}',
        encoding="utf-8",
    )
    with pytest.raises(WorkspaceError, match="duplicate JSON key"):
        load_workspace(tmp_path)


def test_non_finite_numbers_are_refused(tmp_path: Path):
    make_manifest(tmp_path / "a")
    (tmp_path / WORKSPACE_FILENAME).write_text(
        '{"format":"rungraph-workspace","version":"0.1","id":"s","name":"S",'
        '"members":[{"id":"a","path":"a"}],"metadata":{"x":NaN}}',
        encoding="utf-8",
    )
    with pytest.raises(WorkspaceError, match="non-finite"):
        load_workspace(tmp_path)


def test_a_ref_has_exactly_one_form():
    from runevidence.workspace import Ref

    assert Ref(member="a", id="r1").text() == "a#r1"
    assert Ref(claim="c1").text() == "claim:c1"
    with pytest.raises(Exception):
        Ref()
    with pytest.raises(Exception):
        Ref(member="a", id="r1", claim="c1")


def test_duplicate_member_ids_are_refused(tmp_path: Path):
    make_manifest(tmp_path / "a")
    make_manifest(tmp_path / "b")
    write_workspace(
        tmp_path,
        members=[{"id": "a", "path": "a"}, {"id": "a", "path": "b"}],
    )
    with pytest.raises(WorkspaceError, match="member IDs must be unique"):
        load_workspace(tmp_path)


def test_duplicate_manifest_by_path_is_refused(tmp_path: Path):
    make_manifest(tmp_path / "a")
    write_workspace(
        tmp_path,
        members=[{"id": "a", "path": "a"}, {"id": "b", "path": "a"}],
    )
    with pytest.raises(WorkspaceError, match="same"):
        load_workspace(tmp_path)


@pytest.mark.skipif(not hasattr(os, "link"), reason="hardlinks unavailable")
def test_duplicate_manifest_by_hardlink_is_refused(tmp_path: Path):
    make_manifest(tmp_path / "a")
    make_manifest(tmp_path / "b")
    os.unlink(tmp_path / "b" / "runevidence.json")
    os.link(tmp_path / "a" / "runevidence.json", tmp_path / "b" / "runevidence.json")
    write_workspace(
        tmp_path,
        members=[{"id": "a", "path": "a"}, {"id": "b", "path": "b"}],
    )
    with pytest.raises(WorkspaceError, match="same manifest"):
        load_workspace(tmp_path)


def test_a_nested_member_is_refused(tmp_path: Path):
    make_manifest(tmp_path / "a")
    make_manifest(tmp_path / "a" / "b")
    write_workspace(
        tmp_path,
        members=[{"id": "a", "path": "a"}, {"id": "b", "path": "a/b"}],
    )
    with pytest.raises(WorkspaceError, match="nested"):
        load_workspace(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_a_symlinked_manifest_is_refused(tmp_path: Path):
    make_manifest(tmp_path / "a")
    real = tmp_path / "a" / "real.json"
    real.write_text(MANIFEST, encoding="utf-8")
    (tmp_path / "a" / "runevidence.json").unlink()
    (tmp_path / "a" / "runevidence.json").symlink_to(real)
    write_workspace(tmp_path)
    with pytest.raises(WorkspaceError, match="symlink"):
        load_workspace(tmp_path)


def test_discovery_refuses_an_empty_root(tmp_path: Path):
    result = discover(tmp_path)
    assert result.errors and result.exit_code == 2


def test_discovery_assigns_sorted_provisional_ids(tmp_path: Path):
    make_manifest(tmp_path / "b")
    make_manifest(tmp_path / "a")
    result = discover(tmp_path)
    assert [m.id for m in result.members] == ["prov-a", "prov-b"]
    assert all(not m.stable for m in result.members)


def test_discovery_uses_stable_ids_from_the_document(tmp_path: Path):
    make_manifest(tmp_path / "a")
    make_manifest(tmp_path / "b")
    write_workspace(
        tmp_path,
        members=[{"id": "alpha", "path": "a"}, {"id": "beta", "path": "b"}],
    )
    result = discover(tmp_path)
    assert {m.path: m.id for m in result.members} == {"a": "alpha", "b": "beta"}
    assert all(m.stable for m in result.members)


def test_discovery_keeps_a_declared_member_under_an_ignored_folder(tmp_path: Path):
    make_manifest(tmp_path / "build" / "exp")
    write_workspace(tmp_path, members=[{"id": "exp", "path": "build/exp"}])
    result = discover(tmp_path)
    assert [m.id for m in result.members] == ["exp"]


def test_discovery_reports_a_nested_manifest(tmp_path: Path):
    make_manifest(tmp_path / "a")
    make_manifest(tmp_path / "a" / "b")
    result = discover(tmp_path)
    assert any("nested manifest" in error for error in result.errors)
    assert result.exit_code == 2


@pytest.mark.skipif(not hasattr(os, "link"), reason="hardlinks unavailable")
def test_discovery_reports_a_duplicate_manifest(tmp_path: Path):
    make_manifest(tmp_path / "a")
    make_manifest(tmp_path / "b")
    os.unlink(tmp_path / "b" / "runevidence.json")
    os.link(tmp_path / "a" / "runevidence.json", tmp_path / "b" / "runevidence.json")
    result = discover(tmp_path)
    assert any("duplicate manifest" in error for error in result.errors)
    assert result.exit_code == 2


def test_a_member_at_the_workspace_root_is_allowed(tmp_path: Path):
    make_manifest(tmp_path)
    write_workspace(tmp_path, members=[{"id": "root", "path": "."}])
    loaded = load_workspace(tmp_path)
    assert loaded.members["root"].manifest is not None
