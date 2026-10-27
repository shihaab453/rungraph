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
from runevidence.models import ID_PATTERN, MANIFEST_FILENAME, Manifest


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


def test_run_ids_are_allocated_deterministically_across_slug_collisions(tmp_path):
    root = tmp_path / "proj"
    for name in ("a b", "a-b", "a-b-2"):
        write(root, f"runs/{name}/metrics.json", f"{name}\n".encode())

    manifest = extract(root)

    assert [run.id for run in manifest.runs] == ["run-a-b", "run-a-b-2", "run-a-b-2-2"]
    by_path = {artifact.path: artifact for artifact in manifest.artifacts}
    assert by_path["runs/a b/metrics.json"].run_id == "run-a-b"
    assert by_path["runs/a-b/metrics.json"].run_id == "run-a-b-2"
    assert by_path["runs/a-b-2/metrics.json"].run_id == "run-a-b-2-2"


def test_project_run_and_artifact_ids_share_one_namespace(tmp_path):
    root = tmp_path / "proj"
    write(root, "run-a-b", b"artifact with a run-like path\n")
    write(root, "runs/a b/metrics.json")

    adapter = GenericAdapter()
    manifest = adapter.extract(
        root, adapter.discover(root), project_id="run-a-b", project_name="Demo"
    )

    assert manifest.project.id == "run-a-b"
    assert [run.id for run in manifest.runs] == ["run-a-b-2"]
    by_path = {artifact.path: artifact for artifact in manifest.artifacts}
    assert by_path["run-a-b"].id == "run-a-b-3"
    assert by_path["runs/a b/metrics.json"].run_id == "run-a-b-2"


def test_supplied_run_details_are_sorted_for_stable_allocation(tmp_path):
    root = tmp_path / "proj"
    write(root, "runs/a/metrics.json")
    write(root, "runs/b/metrics.json")
    files = ["runs/a/metrics.json", "runs/b/metrics.json"]
    forward = Discovery(root=root, files=files, details={"runs": ["a", "b"]})
    reverse = Discovery(root=root, files=files, details={"runs": ["b", "a"]})

    first = GenericAdapter().extract(root, forward, project_id="demo", project_name="Demo")
    second = GenericAdapter().extract(root, reverse, project_id="demo", project_name="Demo")

    assert first == second
    assert [run.id for run in first.runs] == ["run-a", "run-b"]
    by_path = {artifact.path: artifact for artifact in first.artifacts}
    assert by_path["runs/a/metrics.json"].run_id == "run-a"
    assert by_path["runs/b/metrics.json"].run_id == "run-b"


def test_existing_run_and_artifact_ids_stay_stable(tmp_path):
    root = tmp_path / "proj"
    write(root, "notes.txt")
    write(root, "runs/_baseline/metrics.json")
    write(root, "runs/train/metrics.json")

    manifest = extract(root)

    assert [run.id for run in manifest.runs] == ["run-_baseline", "run-train"]
    by_path = {artifact.path: artifact for artifact in manifest.artifacts}
    assert by_path["notes.txt"].id == "notes.txt"
    assert by_path["runs/_baseline/metrics.json"].id == "runs-_baseline-metrics.json"
    assert by_path["runs/train/metrics.json"].id == "runs-train-metrics.json"
    assert by_path["runs/_baseline/metrics.json"].run_id == "run-_baseline"
    assert by_path["runs/train/metrics.json"].run_id == "run-train"


def test_inconsistent_discovery_refuses_to_drop_run_ownership(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    write(root, "runs/train/metrics.json")
    discovery = Discovery(root=root, files=["runs/train/metrics.json"], details={"runs": []})

    def unexpected_hash(path):
        pytest.fail(f"Inconsistent discovery must be rejected before hashing {path}")

    monkeypatch.setattr(generic_adapter, "sha256_file", unexpected_hash)
    with pytest.raises(AdapterError, match="Discovery omitted run folder 'train'"):
        GenericAdapter().extract(root, discovery, project_id="demo", project_name="Demo")


def test_unprefixed_artifact_ids_start_with_an_alphanumeric(tmp_path):
    root = tmp_path / "proj"
    write(root, "_summary.json")
    write(root, "__init__.py")
    write(root, "_pkg/__init__.py")
    write(root, "item-_summary.json")

    manifest = extract(root)

    by_path = {artifact.path: artifact for artifact in manifest.artifacts}
    assert by_path["_summary.json"].id == "item-_summary.json"
    assert by_path["__init__.py"].id == "item-__init__.py"
    assert by_path["_pkg/__init__.py"].id == "item-_pkg-__init__.py"
    # The generated ID collides with this real filename; suffix it in a
    # stable order rather than emitting a duplicate.
    assert by_path["item-_summary.json"].id == "item-_summary.json-2"
    assert all(re.fullmatch(ID_PATTERN, artifact.id) for artifact in manifest.artifacts)


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
