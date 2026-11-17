"""Weights & Biases summary-folder adapter tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("runevidence_wandb")

from runevidence.adapters import AdapterError, available_adapters
from runevidence.cli import main
from runevidence_wandb import WandbAdapter

pytestmark = pytest.mark.skipif(
    not __import__("runevidence.checks.files", fromlist=["_dir_fd_supported"])._dir_fd_supported(),
    reason="the W&B adapter requires POSIX directory-descriptor support",
)


def write_run(root: Path, name: str = "offline-run-1", *, root_summary: bool = False) -> Path:
    folder = root / "wandb" / name
    files = folder / "files"
    files.mkdir(parents=True)
    (files / "wandb-summary.json").write_text(
        json.dumps({"loss": 0.5, "acc": 0.9, "_runtime": 12, "label": "x"}),
        encoding="utf-8",
    )
    (files / "config.yaml").write_text("lr: 0.1\nmodel:\n  hidden: 8\n", encoding="utf-8")
    (files / "wandb-metadata.json").write_text(
        json.dumps(
            {
                "run_id": "abc",
                "display_name": "Demo",
                "started_at": "2026-11-17T10:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    (files / "output.log").write_text("hello\n", encoding="utf-8")
    if root_summary:
        (folder / "wandb-summary.json").write_text(
            json.dumps({"loss": 0.4}), encoding="utf-8"
        )
    return folder


def test_extract_records_run_params_metrics_and_artifacts(tmp_path: Path):
    root = tmp_path / "runs"
    write_run(root)
    adapter = WandbAdapter()
    discovery = adapter.discover(root)
    assert discovery.details["runs"] == ["wandb/offline-run-1"]
    manifest = adapter.extract(root, discovery, project_id="w", project_name="W")
    assert manifest.schema_version == "0.3"
    run = manifest.runs[0]
    assert run.name == "Demo"
    assert run.params == {"lr": 0.1, "model": {"hidden": 8}}
    assert {result.name: result.value for result in manifest.results} == {
        "acc": 0.9,
        "loss": 0.5,
    }
    assert all(result.locator.startswith("/") for result in manifest.results)
    assert len(manifest.artifacts) == 4
    assert all(artifact.run_id == run.id for artifact in manifest.artifacts)


def test_root_summary_wins_and_no_duplicate_run(tmp_path: Path):
    root = tmp_path / "runs"
    write_run(root, root_summary=True)
    adapter = WandbAdapter()
    discovery = adapter.discover(root)
    assert discovery.details["runs"] == ["wandb/offline-run-1"]
    manifest = adapter.extract(root, discovery, project_id="w", project_name="W")
    results = {result.name: result.value for result in manifest.results}
    assert results["loss"] == 0.4


def test_a_folder_without_a_summary_is_refused(tmp_path: Path):
    root = tmp_path / "runs"
    (root / "nothing").mkdir(parents=True)
    with pytest.raises(AdapterError, match="no W&B run folders"):
        WandbAdapter().discover(root)


def test_cli_ingests_when_the_adapter_is_installed(tmp_path: Path, capsys):
    if "wandb" not in available_adapters():
        pytest.skip("install packages/runevidence-wandb to run the CLI test")
    write_run(tmp_path)
    assert main(["init", str(tmp_path), "--adapter", "wandb", "--name", "W", "--id", "w"]) == 0
    manifest = json.loads((tmp_path / "runevidence.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "0.3"
    assert [result["name"] for result in manifest["results"]] == ["acc", "loss"]
