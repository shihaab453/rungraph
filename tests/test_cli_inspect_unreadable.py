"""Read-only CLI inspect regression using synthetic, Temp-contained manifests.

No CLI writer command, production manifest, SQLite, or plugin discovery.  Only
fixture files under pytest's tmp_path are created by the tests.
"""

import json
from pathlib import Path

from runevidence.cli import main
from runevidence.manifest import dump_manifest
from runevidence.models import MANIFEST_FILENAME
from runevidence_hydra.adapter import HydraAdapter
from runevidence_mlflow.adapter import MlflowAdapter


def _write(root: Path, relative: str, text: str) -> None:
    dest = root.joinpath(*relative.split("/"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")


def _synthetic_manifest(root: Path, adapter) -> Path:
    # Deliberately serialize a fabricated adapter output directly to tmp_path.
    # Do not use the CLI init/save functions or open a real project manifest.
    manifest = adapter.extract(
        root, adapter.discover(root), project_id="synthetic", project_name="Synthetic"
    )
    path = root / MANIFEST_FILENAME
    path.write_text(dump_manifest(manifest), encoding="utf-8", newline="\n")
    return path


def _hydra_root(tmp_path: Path, *, corrupt: bool) -> tuple[Path, str]:
    root = tmp_path / "hydra-demo"
    path = "outputs/2026-11-04/01-02-03/.hydra/config.yaml"
    _write(root, path, "optim: [broken\n" if corrupt else "optim: adam\n")
    _write(root, "outputs/2026-11-04/01-02-03/.hydra/overrides.yaml", "[]\n")
    _synthetic_manifest(root, HydraAdapter())
    return root, path


def _mlflow_root(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "mlflow-demo"
    _write(root, "mlruns/0/meta.yaml", "experiment_id: '0'\nname: Demo\n")
    _write(root, "mlruns/0/abc123/meta.yaml", "experiment_id: '0'\nrun_id: abc123\nstatus: 3\n")
    path = "mlruns/0/abc123/metrics/val_loss"
    _write(root, path, "malformed metric\n")
    _write(root, "mlruns/0/abc123/metrics/train_loss", "1700000000000 0.25 1\n")
    _synthetic_manifest(root, MlflowAdapter())
    return root, path


def _run(args: list[str], capsys) -> tuple[int, str]:
    result = main(args)
    captured = capsys.readouterr()
    assert not captured.err
    assert result in (0, 1), "no malformed synthetic manifest or invalid CLI invocation"
    return result, captured.out


def test_hydra_cli_inspect_text_shows_unreadable_without_writes(tmp_path, capsys):
    root, unreadable_path = _hydra_root(tmp_path, corrupt=True)
    manifest_path = root / MANIFEST_FILENAME
    original = manifest_path.read_bytes()

    result, text = _run(["inspect", str(root)], capsys)
    assert "Inputs the adapter could not read (1):" in text
    assert f"(hydra): {unreadable_path}" in text
    assert "not valid YAML" in text
    assert "Findings:" in text
    assert f"(exit {result})" in text
    assert manifest_path.read_bytes() == original


def test_hydra_cli_inspect_json_contains_unreadable_metadata(tmp_path, capsys):
    root, path = _hydra_root(tmp_path, corrupt=True)
    result, output = _run(["inspect", str(root), "--json"], capsys)
    data = json.loads(output)
    entries = data["summary"]["adapter_unreadable"]
    assert len(entries) == 1
    assert entries[0]["adapter"] == "hydra"
    assert entries[0]["path"] == path
    assert "not valid YAML" in entries[0]["reason"]
    assert data["exit_code"] == result
    assert data["ok"] == (result == 0)


def test_mlflow_cli_inspect_json_surfaces_skipped_metric(tmp_path, capsys):
    root, path = _mlflow_root(tmp_path)
    result, output = _run(["inspect", "--json", str(root)], capsys)
    data = json.loads(output)
    entries = data["summary"]["adapter_unreadable"]
    assert len(entries) == 1
    assert entries[0]["adapter"] == "mlflow"
    assert entries[0]["path"] == path
    assert isinstance(entries[0]["run"], str) and entries[0]["run"]
    assert set(entries[0]) == {"run", "adapter", "path", "reason"}
    assert entries[0]["reason"] == "malformed"
    assert data["summary"]["results"] == 1
    assert data["exit_code"] == result


def test_validate_json_remains_findings_only(tmp_path, capsys):
    root, _ = _hydra_root(tmp_path, corrupt=True)
    result, output = _run(["validate", "--json", str(root)], capsys)
    data = json.loads(output)
    assert "summary" not in data
    assert "adapter_unreadable" not in data
    assert data["exit_code"] == result


def test_clean_hydra_cli_inspect_has_no_new_section_or_key(tmp_path, capsys):
    root, _ = _hydra_root(tmp_path, corrupt=False)
    _, text = _run(["inspect", str(root)], capsys)
    assert "Inputs the adapter could not read" not in text
    _, json_out = _run(["inspect", "--json", str(root)], capsys)
    assert "adapter_unreadable" not in json.loads(json_out)["summary"]
