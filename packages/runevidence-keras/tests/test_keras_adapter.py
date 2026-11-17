"""Keras history CSV adapter tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("runevidence_keras")

from runevidence.cli import main


def write_history(folder: Path, text: str, name: str = "history.csv") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8", newline="")
    return folder / name


def test_extract_records_numeric_cells(tmp_path: Path, capsys):
    write_history(
        tmp_path,
        "epoch,loss,accuracy,val_loss\n0,1.0,0.5,1.2\n1,0.5,0.8,0.9\n2,0.25,0.9,0.8\n",
    )
    assert main(["init", str(tmp_path), "--adapter", "keras", "--name", "Study", "--id", "study"]) == 0
    manifest = json.loads((tmp_path / "runevidence.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "0.4"
    assert len(manifest["results"]) == 9
    first = next(item for item in manifest["results"] if item["name"] == "loss @ 0")
    assert first["locator"] == {"kind": "csv-cell", "row": 1, "column": 2, "header": "loss"}
    assert first["value"] == 1.0
    assert first["artifact_id"] in {item["id"] for item in manifest["artifacts"]}
    capsys.readouterr()
    assert main(["validate", str(tmp_path), "--strict"]) == 0


def test_named_epoch_column_and_non_numeric_cells(tmp_path: Path, capsys):
    write_history(
        tmp_path,
        "epoch,loss,note\n3,0.4,ok\n4,n/a,skip\n",
    )
    assert main(["init", str(tmp_path), "--adapter", "keras", "--name", "Study", "--id", "study"]) == 0
    manifest = json.loads((tmp_path / "runevidence.json").read_text(encoding="utf-8"))
    assert [item["name"] for item in manifest["results"]] == ["loss @ 3"]
    capsys.readouterr()
    assert main(["validate", str(tmp_path), "--strict"]) == 0


def test_history_csv_wins_over_other_csvs(tmp_path: Path):
    write_history(tmp_path, "epoch,loss\n1,0.5\n", name="other.csv")
    write_history(tmp_path, "epoch,loss\n2,0.4\n")
    assert main(["init", str(tmp_path), "--adapter", "keras", "--name", "Study", "--id", "study"]) == 0
    manifest = json.loads((tmp_path / "runevidence.json").read_text(encoding="utf-8"))
    assert manifest["results"][0]["name"] == "loss @ 2"


def test_multiple_csvs_without_history_refuse(tmp_path: Path, capsys):
    write_history(tmp_path, "epoch,loss\n1,0.5\n", name="a.csv")
    write_history(tmp_path, "epoch,loss\n1,0.5\n", name="b.csv")
    assert main(["init", str(tmp_path), "--adapter", "keras"]) == 2
    assert not (tmp_path / "runevidence.json").exists()


def test_a_malformed_history_refuses(tmp_path: Path, capsys):
    write_history(tmp_path, "epoch,loss\r1,0.5\n")
    assert main(["init", str(tmp_path), "--adapter", "keras"]) == 2
    assert not (tmp_path / "runevidence.json").exists()


def test_no_history_refuses(tmp_path: Path):
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert main(["init", str(tmp_path), "--adapter", "keras"]) == 2


def test_external_history_symlink_is_refused(tmp_path: Path):
    import os

    outside = tmp_path / "outside.csv"
    outside.write_text("epoch,loss\n1,0.5\n", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    try:
        os.symlink(outside, root / "history.csv")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")
    assert main(["init", str(root), "--adapter", "keras"]) == 2
    assert not (root / "runevidence.json").exists()


def test_an_unreadable_artifact_is_an_adapter_error(tmp_path: Path):
    import os

    if os.name != "posix":
        pytest.skip("permission bits are POSIX-only")
    write_history(tmp_path, "epoch,loss\n1,0.5\n")
    secret = tmp_path / "secret.bin"
    secret.write_bytes(b"x")
    secret.chmod(0)
    try:
        assert main(["init", str(tmp_path), "--adapter", "keras"]) == 2
        assert not (tmp_path / "runevidence.json").exists()
    finally:
        secret.chmod(0o600)


def test_a_file_deleted_after_discovery_is_an_adapter_error(tmp_path: Path):
    from runevidence.adapters import AdapterError
    from runevidence_keras import KerasAdapter

    write_history(tmp_path, "epoch,loss\n1,0.5\n")
    (tmp_path / "gone.bin").write_bytes(b"x")
    adapter = KerasAdapter()
    discovery = adapter.discover(tmp_path)
    (tmp_path / "gone.bin").unlink()
    with pytest.raises(AdapterError):
        adapter.extract(tmp_path, discovery, project_id="p", project_name="p")
