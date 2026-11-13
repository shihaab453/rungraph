"""Bounded adapter-to-report integration, with only disposable tmp_path inputs.

Exercises direct Hydra/MLflow discovery and extraction, then existing read-only
report formatters. No CLI, manifest saves, plugin registry calls or SQLite.
"""

import json
from pathlib import Path

from runevidence.checks import Report
from runevidence.report import format_overview, to_json
from runevidence_hydra.adapter import HydraAdapter
from runevidence_mlflow.adapter import MlflowAdapter


def _write(root: Path, relative: str, contents: str) -> Path:
    target = root.joinpath(*relative.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(contents, encoding="utf-8")
    return target


def _extract(adapter, root: Path):
    return adapter.extract(
        root, adapter.discover(root), project_id="synthetic", project_name="Synthetic"
    )


def _inspect_json(manifest):
    report = Report(manifest_path=Path("synthetic/runevidence.json"))
    return json.loads(to_json(report, manifest=manifest))


def test_hydra_unreadable_yaml_is_present_in_actual_report(tmp_path):
    root = tmp_path / "hydra-example"
    rel = "outputs/2026-11-04/01-02-03/.hydra/config.yaml"
    _write(root, rel, "optim: [broken\n")
    _write(root, "outputs/2026-11-04/01-02-03/.hydra/overrides.yaml", "[]\n")

    manifest = _extract(HydraAdapter(), root)
    assert len(manifest.runs) == 1
    run = manifest.runs[0]
    assert run.metadata["hydra"]["unreadable"] == [rel]
    reason = run.metadata["hydra"]["unreadable_reasons"][rel]
    assert "not valid YAML" in reason
    assert any(a.path == rel for a in manifest.artifacts)  # bad source still hashed
    assert manifest.results == []

    human = format_overview(manifest)
    assert "Inputs the adapter could not read (1):" in human
    assert f"{run.id} (hydra): {rel}" in human
    assert reason in human
    inspected = _inspect_json(manifest)
    assert inspected["summary"]["adapter_unreadable"] == [
        {"run": run.id, "adapter": "hydra", "path": rel, "reason": reason}
    ]
    assert inspected["exit_code"] == 0  # visibility only, not a finding


def test_mlflow_unreadable_metric_is_present_but_not_a_result(tmp_path):
    root = tmp_path / "mlflow-example"
    _write(root, "mlruns/0/meta.yaml", "experiment_id: '0'\nname: Demo\n")
    _write(root, "mlruns/0/abc123/meta.yaml", "experiment_id: '0'\nrun_id: abc123\nstatus: 3\n")
    bad = "mlruns/0/abc123/metrics/val_loss"
    good = "mlruns/0/abc123/metrics/train_loss"
    _write(root, bad, "this is invalid\n")
    _write(root, good, "1700000000000 0.25 1\n")

    manifest = _extract(MlflowAdapter(), root)
    assert len(manifest.runs) == 1
    run = manifest.runs[0]
    assert run.metadata["mlflow"]["unreadable"] == [bad]
    assert any(a.path == bad for a in manifest.artifacts)
    assert {r.name for r in manifest.results} == {"train_loss"}
    assert all(r.name != "val_loss" for r in manifest.results)

    human = format_overview(manifest)
    assert f"{run.id} (mlflow): {bad}" in human
    entries = _inspect_json(manifest)["summary"]["adapter_unreadable"]
    assert entries == [{"run": run.id, "adapter": "mlflow", "path": bad}]


def test_valid_hydra_inputs_leave_legacy_report_unchanged(tmp_path):
    root = tmp_path / "valid-hydra"
    _write(root, "outputs/2026-11-04/01-02-03/.hydra/config.yaml", "model: tiny\n")

    manifest = _extract(HydraAdapter(), root)
    assert "unreadable" not in manifest.runs[0].metadata.get("hydra", {})
    assert "Inputs the adapter could not read" not in format_overview(manifest)
    assert "adapter_unreadable" not in _inspect_json(manifest)["summary"]
    no_manifest = json.loads(to_json(Report(manifest_path=Path("synthetic/runevidence.json"))))
    assert "summary" not in no_manifest  # validate-style JSON unaffected


def test_valid_mlflow_metric_does_not_create_unreadable_entry(tmp_path):
    root = tmp_path / "valid-mlflow"
    _write(root, "mlruns/0/meta.yaml", "experiment_id: '0'\nname: Demo\n")
    _write(root, "mlruns/0/abc123/meta.yaml", "experiment_id: '0'\nrun_id: abc123\n")
    _write(root, "mlruns/0/abc123/metrics/accuracy", "1700000000000 0.9 2\n")

    manifest = _extract(MlflowAdapter(), root)
    assert {r.name for r in manifest.results} == {"accuracy"}
    assert "unreadable" not in manifest.runs[0].metadata["mlflow"]
    assert "Inputs the adapter could not read" not in format_overview(manifest)
    assert "adapter_unreadable" not in _inspect_json(manifest)["summary"]
