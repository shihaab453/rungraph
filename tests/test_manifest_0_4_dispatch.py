"""Schema 0.4 dispatcher wiring at every direct validation/identity site.

Each site must accept a valid 0.4 manifest without an internal failure, while
0.1/0.2/0.3 keep their delivered behavior. Rehash refuses any csv-cell because
structured-locator refresh is not implemented.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from runevidence import _experimental_staged_reader as reader
from runevidence import _experimental_versioned_manifest as stage
from runevidence import directory_authoring
from runevidence import migrate as migrate_module
from runevidence.directory_apply import _pristine
from runevidence.directory_audit import audit_recorded_directories
from runevidence.directory_batch import review_directory_changes
from runevidence.edit import LocatorRefreshUnsupported, plan_add, plan_rehash
from runevidence.manifest import dump_raw, load_manifest, load_raw, validate_manifest
from runevidence.models import Manifest04

MANIFEST = "runevidence.json"


def doc(*, artifact=None, results=None) -> dict:
    return {
        "schema_version": "0.4",
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "name": "Run"}],
        "artifacts": [] if artifact is None else [artifact],
        "results": [] if results is None else results,
        "evidence": [],
    }


def write(root: Path, document: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / MANIFEST
    path.write_bytes(dump_raw(document).encode("utf-8"))
    return path


def test_validate_manifest_dispatches_0_4():
    manifest = validate_manifest(doc())

    assert type(manifest) is Manifest04
    assert manifest.schema_version == "0.4"


def test_migrate_validate_candidate_accepts_0_4():
    migrate_module._validate_candidate(doc(), "0.4")


def test_plan_rehash_self_validation_accepts_0_4(tmp_path):
    root = tmp_path / "exp"
    data = b'{"v": 1}\n'
    digest = hashlib.sha256(data).hexdigest()
    write(root, doc(artifact={
        "id": "a", "path": "data.json", "sha256": digest, "size_bytes": len(data),
    }, results=[{
        "id": "res", "run_id": "run", "name": "v", "value": 1, "artifact_id": "a",
        "locator": "/v",
    }]))
    (root / "data.json").write_bytes(data)
    loaded = load_manifest(root)
    raw = load_raw(loaded)

    plan = plan_rehash(loaded, raw, [0])

    assert plan.files[0].id == "a"
    loaded_after = validate_manifest(raw)
    assert type(loaded_after) is Manifest04
    assert raw["schema_version"] == "0.4"
    assert raw["results"][0]["locator"] == "/v"


def test_plan_rehash_refuses_any_csv_cell(tmp_path):
    root = tmp_path / "exp"
    data = b"val\n1\n"
    digest = hashlib.sha256(data).hexdigest()
    write(root, doc(artifact={
        "id": "a", "path": "metrics.csv", "sha256": digest, "size_bytes": len(data),
    }, results=[{
        "id": "res", "run_id": "run", "name": "val", "value": 1, "artifact_id": "a",
        "locator": {"kind": "csv-cell", "row": 2, "column": 1, "header": "val"},
    }]))
    (root / "metrics.csv").write_bytes(data)
    loaded = load_manifest(root)

    with pytest.raises(LocatorRefreshUnsupported) as info:
        plan_rehash(loaded, load_raw(loaded), [0])

    assert info.value.code == "LOCATOR_REFRESH_UNSUPPORTED"


def test_plan_add_self_validation_accepts_0_4(tmp_path):
    root = tmp_path / "exp"
    write(root, doc())
    (root / "new.txt").write_bytes(b"new bytes\n")
    loaded = load_manifest(root)
    raw = load_raw(loaded)

    plan = plan_add(loaded, raw, [Path("new.txt")], cwd=root)

    assert len(plan.added) == 1
    assert type(validate_manifest(raw)) is Manifest04


def test_directory_authoring_validation_accepts_0_4():
    proposed, manifest = directory_authoring._validated_copy(doc())
    assert type(manifest) is Manifest04
    assert proposed == doc()

    proposed["artifacts"] = [{
        "id": "d",
        "path": "data",
        "artifact_type": "directory",
        "digest_format": "runevidence-directory-v1",
        "sha256": "a" * 64,
        "size_bytes": 1,
    }]
    entry = proposed["artifacts"][0]
    digest = SimpleNamespace(sha256="a" * 64, size_bytes=1, members=1)
    proposal = directory_authoring._finish(
        "add", proposed, entry, None, None, digest, True, (), (), (),
        add_kind=None, add_run_id=None,
    )
    assert proposal.raw["schema_version"] == "0.4"


def test_directory_apply_pristine_accepts_0_4(tmp_path):
    root = tmp_path / "exp"
    write(root, doc(artifact={
        "id": "a", "path": "data.json", "sha256": "a" * 64, "size_bytes": 8,
    }))
    loaded = load_manifest(root)

    pristine = _pristine(loaded)

    assert pristine["schema_version"] == "0.4"


def test_directory_audit_self_validation_accepts_0_4(tmp_path):
    root = tmp_path / "exp"
    write(root, doc())
    loaded = load_manifest(root)

    report = audit_recorded_directories(loaded)

    assert report.state == "no-directory-records"
    assert report.total == 0


def test_directory_batch_self_validation_accepts_0_4(tmp_path):
    root = tmp_path / "exp"
    write(root, doc())
    loaded = load_manifest(root)

    report = review_directory_changes(loaded, ())

    assert report.total == 0


def test_experimental_readers_accept_0_4(tmp_path):
    root = tmp_path / "exp"
    path = write(root, doc())

    model = stage._model(path.read_bytes())
    assert type(model) is Manifest04

    readback = stage.StagedReadback(path.read_bytes(), model, 1, 1, "a" * 64)
    view = reader._view(readback, path.resolve())
    assert type(view.manifest) is Manifest04
    assert view.manifest.schema_version == "0.4"
