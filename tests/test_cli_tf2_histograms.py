"""CLI/checker integration for schema-0.11 TF2 histograms."""

from __future__ import annotations

import json
from pathlib import Path

from runevidence.cli import main
from tensorboard_builder import event, records, shape, summary, tensor, value


def write_event(root: Path, payloads: list[bytes]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "events.out.tfevents.test").write_bytes(records(*payloads))


def hist_payload(tag: bytes, buckets, *, step: int = 0) -> bytes:
    flat = [item for bucket in buckets for item in bucket]
    return event(
        step=step,
        summary_bytes=summary(
            value(
                tag=tag,
                tensor_bytes=tensor(
                    dtype=2,
                    shape_bytes=shape([len(buckets), 3]),
                    double_values=flat,
                ),
            )
        ),
    )


def read_manifest(root: Path) -> dict:
    return json.loads((root / "runevidence.json").read_text(encoding="utf-8"))


def test_init_and_validate_a_tf2_histogram(tmp_path: Path, capsys):
    write_event(tmp_path, [hist_payload(b"t", [(0.0, 1.0, 2.0)])])
    assert main(
        ["init", str(tmp_path), "--adapter", "tensorboard", "--name", "h", "--id", "h",
         "--include-tf2-histograms"]
    ) == 0
    manifest = read_manifest(tmp_path)
    assert manifest["schema_version"] == "0.11"
    assert manifest["results"][0]["value"]["buckets"][0]["count"] == 2.0
    assert manifest["results"][0]["locator"]["kind"] == "tensorboard-tf2-histogram"
    capsys.readouterr()
    assert main(["validate", str(tmp_path)]) == 0


def test_a_changed_bucket_mismatches(tmp_path: Path, capsys):
    write_event(tmp_path, [hist_payload(b"t", [(0.0, 1.0, 2.0)])])
    assert main(
        ["init", str(tmp_path), "--adapter", "tensorboard", "--name", "h", "--id", "h",
         "--include-tf2-histograms"]
    ) == 0
    manifest = read_manifest(tmp_path)
    manifest["results"][0]["value"]["buckets"][0]["count"] = 3.0
    (tmp_path / "runevidence.json").write_text(json.dumps(manifest), encoding="utf-8")
    capsys.readouterr()
    assert main(["validate", str(tmp_path)]) == 1
    assert "does not match the recorded value" in capsys.readouterr().out


def test_an_out_of_range_occurrence_is_missing(tmp_path: Path, capsys):
    write_event(tmp_path, [hist_payload(b"t", [(0.0, 1.0, 2.0)])])
    assert main(
        ["init", str(tmp_path), "--adapter", "tensorboard", "--name", "h", "--id", "h",
         "--include-tf2-histograms"]
    ) == 0
    manifest = read_manifest(tmp_path)
    manifest["results"][0]["locator"]["occurrence"] = 1
    (tmp_path / "runevidence.json").write_text(json.dumps(manifest), encoding="utf-8")
    capsys.readouterr()
    assert main(["validate", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "was not found" in out
    assert "tensorboard-tf2-histogram:step=0,occurrence=1" in out


def test_a_malformed_claim_reports_unsupported_encoding(tmp_path: Path, capsys):
    write_event(tmp_path, [hist_payload(b"bad", [(0.0, 1.0, -1.0)])])
    assert main(
        ["init", str(tmp_path), "--adapter", "tensorboard", "--name", "h", "--id", "h",
         "--include-tf2-histograms"]
    ) == 0
    manifest = read_manifest(tmp_path)
    assert manifest["results"] == []
    manifest["results"] = [
        {
            "id": "manual",
            "run_id": manifest["runs"][0]["id"],
            "name": "manual",
            "value": {"kind": "tf2-histogram", "buckets": []},
            "artifact_id": manifest["artifacts"][0]["id"],
            "locator": {
                "kind": "tensorboard-tf2-histogram",
                "tag": "bad",
                "step": 0,
                "occurrence": 0,
            },
        }
    ]
    (tmp_path / "runevidence.json").write_text(json.dumps(manifest), encoding="utf-8")
    capsys.readouterr()
    assert main(["validate", str(tmp_path)]) == 1
    assert "unsupported tf2 histogram encoding" in capsys.readouterr().out


def test_other_tensor_reports_unsupported_value(tmp_path: Path, capsys):
    other = event(
        step=0,
        summary_bytes=summary(
            value(
                tag=b"other",
                tensor_bytes=tensor(
                    dtype=1, shape_bytes=shape([4]), float_values=[1.0, 2.0, 3.0, 4.0]
                ),
            )
        ),
    )
    write_event(tmp_path, [other])
    assert main(
        ["init", str(tmp_path), "--adapter", "tensorboard", "--name", "h", "--id", "h",
         "--include-tf2-histograms"]
    ) == 0
    manifest = read_manifest(tmp_path)
    manifest["results"] = [
        {
            "id": "manual",
            "run_id": manifest["runs"][0]["id"],
            "name": "manual",
            "value": {"kind": "tf2-histogram", "buckets": []},
            "artifact_id": manifest["artifacts"][0]["id"],
            "locator": {
                "kind": "tensorboard-tf2-histogram",
                "tag": "other",
                "step": 0,
                "occurrence": 0,
            },
        }
    ]
    (tmp_path / "runevidence.json").write_text(json.dumps(manifest), encoding="utf-8")
    capsys.readouterr()
    assert main(["validate", str(tmp_path)]) == 1
    assert "selects an unsupported value" in capsys.readouterr().out


def test_histogram_and_plain_tensor_occurrences_keep_gaps(tmp_path: Path):
    hist = hist_payload(b"t", [(0.0, 1.0, 2.0)])
    plain = event(
        step=0,
        summary_bytes=summary(
            value(
                tag=b"t",
                tensor_bytes=tensor(
                    dtype=1, shape_bytes=shape([4]), float_values=[1.0, 2.0, 3.0, 4.0]
                ),
            )
        ),
    )
    write_event(tmp_path, [hist, plain])
    assert main(
        ["init", str(tmp_path), "--adapter", "tensorboard", "--name", "h", "--id", "h",
         "--include-tf2-histograms", "--include-tensors"]
    ) == 0
    manifest = read_manifest(tmp_path)
    kinds = {result["locator"]["kind"]: result for result in manifest["results"]}
    assert kinds["tensorboard-tf2-histogram"]["locator"]["occurrence"] == 0
    assert kinds["tensorboard-tensor"]["locator"]["occurrence"] == 1
    assert main(["validate", str(tmp_path)]) == 0


def test_without_the_flag_a_histogram_shape_is_a_plain_tensor(tmp_path: Path):
    write_event(tmp_path, [hist_payload(b"t", [(0.0, 1.0, 2.0)])])
    assert main(
        ["init", str(tmp_path), "--adapter", "tensorboard", "--name", "h", "--id", "h",
         "--include-tensors"]
    ) == 0
    manifest = read_manifest(tmp_path)
    assert manifest["schema_version"] == "0.9"
    assert manifest["results"][0]["locator"]["kind"] == "tensorboard-tensor"
