"""Adapters: discovery rules, extraction, id uniqueness and the registry."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path

import pytest

import runevidence.adapters.generic as generic_adapter
from conftest import is_junction, make_dir_link, remove_dir_link
from runevidence.adapters import BUILTIN, AdapterError, get_adapter
from runevidence.adapters.base import Adapter, Discovery
from runevidence.adapters.generic import GenericAdapter
from runevidence.cli import main
from runevidence.models import MANIFEST_FILENAME, Manifest


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


def test_discover_prunes_linked_directories_outside_and_reads_no_outside_bytes(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    write(root, "notes.txt", b"safe\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"outside bytes\n")
    link = root / "linked"
    if not make_dir_link(link, outside):
        pytest.skip("this platform cannot create a directory link")
    try:
        if os.name == "nt":
            assert is_junction(link)
        discovery = GenericAdapter().discover(root)
        assert discovery.files == ["notes.txt"]

        hashed: list[Path] = []
        real_hash = generic_adapter.sha256_file

        def recording_hash(path):
            hashed.append(Path(path))
            return real_hash(path)

        monkeypatch.setattr(generic_adapter, "sha256_file", recording_hash)
        manifest = GenericAdapter().extract(root, discovery, project_id="demo", project_name="Demo")

        assert [artifact.path for artifact in manifest.artifacts] == ["notes.txt"]
        assert hashed == [root / "notes.txt"]
    finally:
        remove_dir_link(link)


def test_discover_keeps_a_file_link_inside_the_root(tmp_path):
    root = tmp_path / "proj"
    write(root, "notes.txt", b"safe\n")
    try:
        os.symlink(root / "notes.txt", root / "alias.txt")
    except OSError:
        pytest.skip("this platform cannot create file symlinks without extra privileges")

    discovery = GenericAdapter().discover(root)

    assert discovery.files == ["alias.txt", "notes.txt"]


def test_discover_follows_an_in_root_directory_link_and_stops_cycles(tmp_path):
    root = tmp_path / "proj"
    write(root, "data/inner.txt", b"inner\n")
    link = root / "alias"
    if not make_dir_link(link, root / "data"):
        pytest.skip("this platform cannot create a directory link")
    cycle = root / "data" / "loop"
    try:
        if not make_dir_link(cycle, root):
            pytest.skip("this platform cannot create a directory link")
        if os.name == "nt":
            assert is_junction(link)
            assert is_junction(cycle)

        discovery = GenericAdapter().discover(root)

        # The safe in-root alias is traversed, and its link back to the root
        # is skipped instead of recursing forever.
        assert discovery.files == ["alias/inner.txt", "data/inner.txt"]
    finally:
        if cycle.exists():
            remove_dir_link(cycle)
        remove_dir_link(link)


def test_extract_refuses_an_entry_switched_to_an_outside_link_after_discovery(
    tmp_path, monkeypatch
):
    root = tmp_path / "proj"
    write(root, "data/inner.txt", b"inside bytes\n")
    discovery = GenericAdapter().discover(root)
    assert discovery.files == ["data/inner.txt"]

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "inner.txt").write_bytes(b"outside bytes\n")
    shutil.rmtree(root / "data")
    if not make_dir_link(root / "data", outside):
        pytest.skip("this platform cannot create a directory link")
    try:
        hashed: list[Path] = []
        real_hash = generic_adapter.sha256_file

        def recording_hash(path):
            hashed.append(Path(path))
            return real_hash(path)

        monkeypatch.setattr(generic_adapter, "sha256_file", recording_hash)
        manifest = GenericAdapter().extract(
            root, discovery, project_id="demo", project_name="Demo"
        )

        assert manifest.artifacts == []
        assert hashed == []
    finally:
        remove_dir_link(root / "data")


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


class _RefusingAdapter(Adapter):
    name = "refusing"

    def discover(self, root: Path) -> Discovery:
        return Discovery(root=root)

    def extract(self, root: Path, discovery: Discovery, *, project_id: str, project_name: str) -> Manifest:
        raise AdapterError("expected a tool folder here")


def test_init_reports_adapter_error_exits_two_and_writes_nothing(tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(BUILTIN, _RefusingAdapter.name, _RefusingAdapter)
    write(tmp_path, "data.csv")

    assert main(["init", str(tmp_path), "--adapter", "refusing"]) == 2
    out = capsys.readouterr().out
    assert "adapter 'refusing' cannot read" in out
    assert "expected a tool folder here" in out
    assert not (tmp_path / MANIFEST_FILENAME).exists()


def test_core_never_imports_tool_libraries():
    """Tool support lives in separate adapter packages (e.g.
    packages/runevidence-hydra), so core stays dependency-light."""
    pattern = re.compile(r"^\s*(import|from)\s+(yaml|hydra|omegaconf|mlflow|wandb|tensorboard)\b", re.MULTILINE)
    src = Path(__file__).resolve().parents[1] / "src" / "runevidence"
    offenders = [p.name for p in src.rglob("*.py") if pattern.search(p.read_text(encoding="utf-8"))]
    assert offenders == []
