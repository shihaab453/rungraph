"""Adapters: discovery rules, extraction, id uniqueness and the registry."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from runevidence.adapters import get_adapter
from runevidence.adapters.base import Adapter, Discovery
from runevidence.adapters.generic import GenericAdapter
from runevidence.models import Manifest


def write(root: Path, rel: str, data: bytes = b"x\n") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def extract(root: Path) -> Manifest:
    adapter = GenericAdapter()
    discovery = adapter.discover(root)
    return adapter.extract(root, discovery, project_id="demo", project_name="Demo")


def test_discover_skips_the_manifest_and_ignored_directories(tmp_path):
    root = tmp_path / "proj"
    write(root, "notes.txt")
    write(root, "runevidence.json", b"{}\n")
    write(root, ".git/HEAD", b"ref: refs/heads/main\n")
    write(root, "__pycache__/mod.cpython-312.pyc")
    write(root, ".venv/pyvenv.cfg")
    write(root, "runs/train/metrics.json")

    discovery = GenericAdapter().discover(root)

    assert discovery.files == ["notes.txt", "runs/train/metrics.json"]
    assert discovery.root == root


def test_runs_are_only_immediate_subfolders_of_runs(tmp_path):
    root = tmp_path / "proj"
    write(root, "runs/x.txt")
    write(root, "runs/train/metrics.json")

    discovery = GenericAdapter().discover(root)

    assert discovery.details["runs"] == ["train"]
    assert "runs/x.txt" in discovery.files


def test_extract_hashes_files_and_assigns_run_ids(tmp_path):
    root = tmp_path / "proj"
    write(root, "notes.txt", b"hello\n")
    write(root, "runs/train/metrics.json", b'{"accuracy": 1.0}\n')

    manifest = extract(root)

    assert isinstance(manifest, Manifest)
    assert Manifest.model_validate(manifest.model_dump()) == manifest
    assert [run.id for run in manifest.runs] == ["run-train"]

    by_path = {artifact.path: artifact for artifact in manifest.artifacts}
    assert set(by_path) == {"notes.txt", "runs/train/metrics.json"}
    for rel, artifact in by_path.items():
        target = root / rel
        assert artifact.sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
        assert artifact.size_bytes == target.stat().st_size
    assert by_path["notes.txt"].run_id is None
    assert by_path["runs/train/metrics.json"].run_id == "run-train"


def test_ids_stay_unique_when_two_paths_slug_the_same(tmp_path):
    root = tmp_path / "proj"
    write(root, "a b.txt", b"space\n")
    write(root, "a-b.txt", b"dash\n")

    manifest = extract(root)

    ids = [artifact.id for artifact in manifest.artifacts]
    assert len(ids) == len(set(ids)) == 2
    assert set(ids) == {"a-b.txt", "a-b.txt-2"}
    assert {artifact.path for artifact in manifest.artifacts} == {"a b.txt", "a-b.txt"}


def test_unknown_adapter_raises_key_error():
    with pytest.raises(KeyError, match="Unknown adapter"):
        get_adapter("nope")


class _IncompleteAdapter(Adapter):
    name = "incomplete"

    def discover(self, root: Path) -> Discovery:
        return Discovery(root=root)


def test_adapter_subclass_without_extract_cannot_be_instantiated():
    with pytest.raises(TypeError, match="extract"):
        _IncompleteAdapter()
