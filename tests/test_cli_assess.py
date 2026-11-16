"""Evidence-aware comparison (conditions plus artifact/value integrity)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from runevidence.cli import main


def _artifact(rel: str, data: bytes, kind: str, **extra) -> dict:
    return {
        "id": extra.pop("id", rel.split(".")[0]),
        "path": rel,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "kind": kind,
        **extra,
    }


def make_member(root: Path, tag: str, split: str = "test") -> Path:
    folder = root / tag
    folder.mkdir(parents=True)
    data = b"dataset\n"
    checkpoint = b"model\n"
    (folder / "data.csv").write_bytes(data)
    (folder / "model.bin").write_bytes(checkpoint)
    run_id = f"run-{tag}"
    document = {
        "schema_version": "0.2",
        "project": {"id": "p", "name": "p"},
        "runs": [
            {
                "id": run_id,
                "conditions": {
                    "evaluation_split": split,
                    "evaluation_protocol": {"protocol_id": "protocol-1", "version": 1},
                    "evaluation_dataset_artifact_id": "data",
                    "model_checkpoint_artifact_id": "checkpoint",
                    "source_revision": "rev-1",
                    "environment": {"python": "3.12"},
                },
            }
        ],
        "artifacts": [
            _artifact("data.csv", data, "dataset", id="data", dataset_id="ds-shared"),
            _artifact("model.bin", checkpoint, "checkpoint", id="checkpoint"),
        ],
        "results": [
            {
                "id": f"acc-{tag}",
                "run_id": run_id,
                "name": "accuracy",
                "value": 0.9,
                "metric_definition": {"definition_id": "acc-v1"},
            }
        ],
        "relationships": [
            {"source": "data", "target": run_id, "type": "input_to"},
            {"source": "checkpoint", "target": run_id, "type": "input_to"},
        ],
    }
    (folder / "runevidence.json").write_text(json.dumps(document), encoding="utf-8")
    return folder


def run_assess(baseline: Path, target: Path, capsys, *extra: str) -> tuple[int, str]:
    code = main(
        [
            "assess",
            str(baseline),
            str(target),
            "--baseline-result",
            "acc-base",
            "--target-result",
            "acc-target",
            *extra,
        ]
    )
    return code, capsys.readouterr().out


def test_clean_and_comparable_is_permitted(tmp_path: Path, capsys):
    baseline = make_member(tmp_path, "base")
    target = make_member(tmp_path, "target")
    code, out = run_assess(baseline, target, capsys)
    assert code == 0
    assert "Recorded conditions: comparable" in out
    assert "Assessment: PERMITTED" in out


def test_changed_evidence_is_not_permitted(tmp_path: Path, capsys):
    baseline = make_member(tmp_path, "base")
    target = make_member(tmp_path, "target")
    (target / "data.csv").write_bytes(b"changed\n")
    code, out = run_assess(baseline, target, capsys)
    assert code == 1
    assert "Target evidence: 1 errors" in out
    assert "Assessment: NOT PERMITTED" in out


def test_differing_conditions_are_not_permitted(tmp_path: Path, capsys):
    baseline = make_member(tmp_path, "base", split="test")
    target = make_member(tmp_path, "target", split="validation")
    code, out = run_assess(baseline, target, capsys)
    assert code == 1
    assert "Recorded conditions: not-comparable" in out
    assert "Assessment: NOT PERMITTED" in out


def test_json_envelope(tmp_path: Path, capsys):
    baseline = make_member(tmp_path, "base")
    target = make_member(tmp_path, "target")
    code, out = run_assess(baseline, target, capsys, "--json")
    data = json.loads(out)
    assert code == 0
    assert data["format"] == "runevidence-assess-v1"
    assert data["conditions"] == "comparable"
    assert data["permitted"] is True
    assert data["evidence"]["baseline"]["intact"] is True
