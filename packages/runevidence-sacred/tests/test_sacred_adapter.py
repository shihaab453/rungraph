"""Sacred FileStorageObserver adapter tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("runevidence_sacred")

from runevidence.checks.files import _dir_fd_supported
from runevidence.cli import main

pytestmark = pytest.mark.skipif(
    not _dir_fd_supported(),
    reason="the Sacred adapter requires POSIX directory-descriptor support",
)


def write_run(root: Path, run_id: str = "1", *, values=(1.0, 0.5)) -> Path:
    folder = root / run_id
    folder.mkdir(parents=True)
    (folder / "run.json").write_text(
        json.dumps(
            {
                "experiment": {"name": "demo", "id": 7, "base_dir": "/ignored"},
                "command": "train",
                "host": {"hostname": "ignored"},
                "status": "COMPLETED",
                "start_time": "2026-11-17T10:00:00+00:00",
                "stop_time": "2026-11-17T10:01:00+00:00",
                "artifacts": ["model.txt"],
                "resources": [],
            }
        ),
        encoding="utf-8",
    )
    (folder / "config.json").write_text(
        json.dumps({"learning_rate": 0.1, "model": {"hidden": 8}}, indent=2),
        encoding="utf-8",
    )
    (folder / "metrics.json").write_text(
        json.dumps(
            {
                "loss": {
                    "values": list(values),
                    "steps": list(range(1, len(values) + 1)),
                    "timestamps": ["2026-11-17T10:00:00+00:00"] * len(values),
                },
                "accuracy": {
                    "values": [0.5, 0.9],
                    "steps": [1, 2],
                    "timestamps": [],
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (folder / "cout.txt").write_text("hello\n", encoding="utf-8")
    (folder / "model.txt").write_text("weights\n", encoding="utf-8")
    return folder


def test_extract_records_runs_params_results_and_artifacts(tmp_path: Path, capsys):
    write_run(tmp_path)
    assert main(["init", str(tmp_path), "--adapter", "sacred", "--name", "Study", "--id", "study"]) == 0
    manifest = json.loads((tmp_path / "runevidence.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "0.3"
    assert len(manifest["runs"]) == 1
    assert manifest["runs"][0]["name"] == "demo"
    assert manifest["runs"][0]["params"]["model"]["hidden"] == 8
    assert manifest["runs"][0]["metadata"]["sacred"]["status"] == "COMPLETED"
    assert len(manifest["results"]) == 4
    by_name = {item["name"]: item for item in manifest["results"]}
    assert by_name["loss @ 1"]["locator"] == "/loss/values/0"
    assert by_name["loss @ 1"]["value"] == 1.0
    assert by_name["accuracy @ 2"]["locator"] == "/accuracy/values/1"
    assert by_name["loss @ 1"]["artifact_id"] in {a["id"] for a in manifest["artifacts"]}
    capsys.readouterr()
    assert main(["validate", str(tmp_path), "--strict"]) == 0


def test_a_changed_metric_value_mismatches(tmp_path: Path, capsys):
    write_run(tmp_path)
    assert main(["init", str(tmp_path), "--adapter", "sacred", "--name", "Study", "--id", "study"]) == 0
    path = tmp_path / "1" / "metrics.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["loss"]["values"][0] = 2.0
    path.write_text(json.dumps(document), encoding="utf-8")
    capsys.readouterr()
    assert main(["validate", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "result-value-mismatch" in out or "hash-mismatch" in out


def test_malformed_metrics_still_records_artifacts(tmp_path: Path, capsys):
    folder = write_run(tmp_path)
    (folder / "metrics.json").write_text("{not json", encoding="utf-8")
    assert main(["init", str(tmp_path), "--adapter", "sacred", "--name", "Study", "--id", "study"]) == 0
    manifest = json.loads((tmp_path / "runevidence.json").read_text(encoding="utf-8"))
    assert manifest["results"] == []
    sacred = manifest["runs"][0]["metadata"]["sacred"]
    assert "1/metrics.json" in sacred["unreadable"]
    capsys.readouterr()
    assert main(["validate", str(tmp_path)]) == 0


def test_a_folder_without_a_run_is_refused(tmp_path: Path, capsys):
    (tmp_path / "metrics.json").write_text("{}", encoding="utf-8")
    assert main(["init", str(tmp_path), "--adapter", "sacred"]) == 2
    assert not (tmp_path / "runevidence.json").exists()


def test_non_scalar_metrics_are_skipped(tmp_path: Path):
    folder = write_run(tmp_path)
    (folder / "metrics.json").write_text(
        json.dumps(
            {
                "bad": {"values": [[1, 2], {"x": 1}, None], "steps": [1, 2, 3]},
                "good": {"values": [1], "steps": [1]},
            }
        ),
        encoding="utf-8",
    )
    assert main(["init", str(tmp_path), "--adapter", "sacred", "--name", "Study", "--id", "study"]) == 0
    manifest = json.loads((tmp_path / "runevidence.json").read_text(encoding="utf-8"))
    assert [item["name"] for item in manifest["results"]] == ["good @ 1"]


def test_nested_pointer_names_are_escaped(tmp_path: Path):
    folder = write_run(tmp_path)
    (folder / "metrics.json").write_text(
        json.dumps({"a/b~c": {"values": [3], "steps": [5]}}),
        encoding="utf-8",
    )
    assert main(["init", str(tmp_path), "--adapter", "sacred", "--name", "Study", "--id", "study"]) == 0
    manifest = json.loads((tmp_path / "runevidence.json").read_text(encoding="utf-8"))
    assert manifest["results"][0]["locator"] == "/a~1b~0c/values/0"
    assert main(["validate", str(tmp_path)]) == 0


def test_repeated_steps_get_distinct_result_names(tmp_path: Path, capsys):
    folder = write_run(tmp_path)
    (folder / "metrics.json").write_text(
        json.dumps({"loss": {"values": [1, 2], "steps": [1, 1]}}), encoding="utf-8"
    )
    assert main(["init", str(tmp_path), "--adapter", "sacred", "--name", "Study", "--id", "study"]) == 0
    manifest = json.loads((tmp_path / "runevidence.json").read_text(encoding="utf-8"))
    names = [item["name"] for item in manifest["results"]]
    assert names == ["loss @ 1", "loss @ 1 #2"]
    capsys.readouterr()
    assert main(["validate", str(tmp_path), "--strict"]) == 0


def test_strict_json_refusals_leave_the_artifact(tmp_path: Path, capsys):
    folder = write_run(tmp_path)
    for bad in (
        '{"loss":{"values":[1],"values":[2]}}',
        '{"loss":{"values":[1e-999]}}',
        '{"lost":1e999,"loss":{"values":[1]}}',
        '{"loss":{"values":[' + "9" * 513 + ']}}',
        '{"loss":\r{"values":[1]}}',
    ):
        (folder / "metrics.json").write_text(bad, encoding="utf-8")
        (tmp_path / "runevidence.json").unlink(missing_ok=True)
        assert main(["init", str(tmp_path), "--adapter", "sacred", "--name", "Study", "--id", "study"]) == 0
        manifest = json.loads((tmp_path / "runevidence.json").read_text(encoding="utf-8"))
        assert manifest["results"] == []
        assert "1/metrics.json" in manifest["runs"][0]["metadata"]["sacred"]["unreadable"]
        capsys.readouterr()
        assert main(["validate", str(tmp_path)]) == 0


def test_literal_nan_values_are_skipped_but_finite_values_remain(tmp_path: Path):
    folder = write_run(tmp_path)
    (folder / "metrics.json").write_text(
        '{"loss":{"values":[NaN, 1.0],"steps":[1,2]}}', encoding="utf-8"
    )
    assert main(["init", str(tmp_path), "--adapter", "sacred", "--name", "Study", "--id", "study"]) == 0
    manifest = json.loads((tmp_path / "runevidence.json").read_text(encoding="utf-8"))
    assert [item["value"] for item in manifest["results"]] == [1.0]
    assert main(["validate", str(tmp_path)]) == 0


def test_a_lone_surrogate_metric_name_is_skipped(tmp_path: Path):
    folder = write_run(tmp_path)
    (folder / "metrics.json").write_text(
        '{"\\ud800":{"values":[1]},"ok":{"values":[2]}}', encoding="utf-8"
    )
    assert main(["init", str(tmp_path), "--adapter", "sacred", "--name", "Study", "--id", "study"]) == 0
    manifest = json.loads((tmp_path / "runevidence.json").read_text(encoding="utf-8"))
    assert [item["name"] for item in manifest["results"]] == ["ok @ 0"]


def test_an_external_symlink_is_not_read(tmp_path: Path):
    import os

    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"secret": 1}), encoding="utf-8")
    folder = write_run(tmp_path / "root")
    (folder / "config.json").unlink()
    try:
        os.symlink(outside, folder / "config.json")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")
    assert main(["init", str(tmp_path / "root"), "--adapter", "sacred", "--name", "Study", "--id", "study"]) == 0
    manifest = json.loads((tmp_path / "root" / "runevidence.json").read_text(encoding="utf-8"))
    assert manifest["runs"][0]["params"] == {}
    assert "1/config.json" in manifest["runs"][0]["metadata"]["sacred"]["unreadable"]


def test_a_swap_after_discovery_cannot_read_outside(tmp_path: Path):
    import os

    from runevidence.adapters import AdapterError
    from runevidence_sacred import SacredAdapter

    root = tmp_path / "root"
    folder = write_run(root)
    adapter = SacredAdapter()
    discovery = adapter.discover(root)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    (folder / "metrics.json").unlink()
    try:
        os.symlink(outside, folder / "metrics.json")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")
    with pytest.raises(AdapterError):
        adapter.extract(root, discovery, project_id="p", project_name="p")


def test_an_unreadable_subdirectory_refuses(tmp_path: Path):
    import os

    if os.name != "posix":
        pytest.skip("permission bits are POSIX-only")
    folder = write_run(tmp_path)
    block = folder / "checkpoints"
    block.mkdir()
    (block / "model.ckpt").write_text("x", encoding="utf-8")
    block.chmod(0)
    try:
        assert main(["init", str(tmp_path), "--adapter", "sacred"]) == 2
        assert not (tmp_path / "runevidence.json").exists()
    finally:
        block.chmod(0o700)


def test_an_unreadable_file_refuses(tmp_path: Path):
    import os

    if os.name != "posix":
        pytest.skip("permission bits are POSIX-only")
    folder = write_run(tmp_path)
    secret = folder / "secret.bin"
    secret.write_bytes(b"x")
    secret.chmod(0)
    try:
        assert main(["init", str(tmp_path), "--adapter", "sacred"]) == 2
        assert not (tmp_path / "runevidence.json").exists()
    finally:
        secret.chmod(0o600)


def test_malformed_metrics_are_visible_in_inspect(tmp_path: Path, capsys):
    folder = write_run(tmp_path)
    (folder / "metrics.json").write_text("{not json", encoding="utf-8")
    assert main(["init", str(tmp_path), "--adapter", "sacred", "--name", "Study", "--id", "study"]) == 0
    capsys.readouterr()

    assert main(["inspect", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Inputs the adapter could not read" in out
    assert "sacred" in out and "metrics.json" in out

    assert main(["inspect", str(tmp_path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    entries = data["summary"]["adapter_unreadable"]
    assert any(
        item["adapter"] == "sacred" and item["path"] == "1/metrics.json"
        for item in entries
    )


def test_unsupported_protected_reads_refuse(tmp_path: Path, capsys, monkeypatch):
    write_run(tmp_path)
    monkeypatch.setattr("runevidence.checks.files._DIR_FD", False)
    assert main(["init", str(tmp_path), "--adapter", "sacred"]) == 2
    assert not (tmp_path / "runevidence.json").exists()
