"""PyTorch Lightning CSVLogger adapter tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("runevidence_lightning")

from runevidence.checks.files import _dir_fd_supported
from runevidence.cli import main

pytestmark = pytest.mark.skipif(
    not _dir_fd_supported(),
    reason="the Lightning adapter requires POSIX directory-descriptor support",
)


def write_version(root: Path, version: str, text: str) -> Path:
    folder = root / "lightning_logs" / f"version_{version}"
    folder.mkdir(parents=True)
    (folder / "metrics.csv").write_text(text, encoding="utf-8", newline="")
    return folder


def test_extract_records_versions_and_sparse_cells(tmp_path: Path, capsys):
    write_version(
        tmp_path,
        "0",
        "epoch,step,train_loss,val_loss\n0,0,1.0,\n0,1,,0.8\n1,2,0.5,\n",
    )
    write_version(tmp_path, "1", "epoch,step,train_loss\n0,0,0.9\n")
    assert main(["init", str(tmp_path), "--adapter", "lightning", "--name", "Study", "--id", "study"]) == 0
    manifest = json.loads((tmp_path / "runevidence.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "0.4"
    assert len(manifest["runs"]) == 2
    names = sorted(item["name"] for item in manifest["results"])
    assert names == ["train_loss @ 0", "train_loss @ 0", "train_loss @ 2", "val_loss @ 1"]
    capsys.readouterr()
    assert main(["validate", str(tmp_path), "--strict"]) == 0


def test_changed_metrics_mismatch(tmp_path: Path, capsys):
    folder = write_version(tmp_path, "0", "epoch,step,loss\n0,0,1.0\n")
    assert main(["init", str(tmp_path), "--adapter", "lightning", "--name", "Study", "--id", "study"]) == 0
    (folder / "metrics.csv").write_text("epoch,step,loss\n0,0,2.0\n", encoding="utf-8", newline="")
    capsys.readouterr()
    assert main(["validate", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "result-value-mismatch" in out or "hash-mismatch" in out


def test_no_metrics_refuses(tmp_path: Path):
    (tmp_path / "lightning_logs").mkdir()
    assert main(["init", str(tmp_path), "--adapter", "lightning"]) == 2


def test_malformed_metrics_refuses(tmp_path: Path):
    write_version(tmp_path, "0", "epoch,step,loss\r0,0,1.0\n")
    assert main(["init", str(tmp_path), "--adapter", "lightning"]) == 2
    assert not (tmp_path / "runevidence.json").exists()


def test_external_metrics_symlink_refused(tmp_path: Path):
    import os

    outside = tmp_path / "outside.csv"
    outside.write_text("epoch,step,loss\n0,0,1.0\n", encoding="utf-8")
    folder = tmp_path / "logs" / "lightning_logs" / "version_0"
    folder.mkdir(parents=True)
    try:
        os.symlink(outside, folder / "metrics.csv")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")
    assert main(["init", str(tmp_path / "logs"), "--adapter", "lightning"]) == 2
    assert not (tmp_path / "logs" / "runevidence.json").exists()


def test_refuses_without_protected_reads(tmp_path: Path, monkeypatch):
    from runevidence.adapters import AdapterError
    from runevidence_lightning import LightningAdapter

    write_version(tmp_path, "0", "epoch,step,loss\n0,0,1.0\n")
    monkeypatch.setattr("runevidence.checks.files._DIR_FD", False)
    with pytest.raises(AdapterError):
        LightningAdapter().extract(
            tmp_path, LightningAdapter().discover(tmp_path), project_id="p", project_name="p"
        )
