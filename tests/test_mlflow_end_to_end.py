"""End-to-end MLflow metric summary lifecycle (RE-10 / ML-1)."""

from __future__ import annotations

import json
from pathlib import Path

from runevidence.cli import main
from runevidence.models import MANIFEST_FILENAME


def make_store(root: Path) -> Path:
    store = root / "mlruns" / "0"
    (store / "run1" / "metrics").mkdir(parents=True)
    (store / "meta.yaml").write_text("experiment_id: '0'\nname: demo\n", encoding="utf-8")
    (store / "run1" / "meta.yaml").write_text(
        "run_id: run1\nexperiment_id: '0'\nstatus: '3'\nstart_time: 1700000000000\n",
        encoding="utf-8",
    )
    (store / "run1" / "metrics" / "accuracy").write_text(
        "1700000000000 0.5 1\n1700000001000 0.7 2\n", encoding="utf-8"
    )
    return root


def result_doc(root: Path) -> dict:
    return json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))["results"][0]


def append_row(root: Path, text: str) -> None:
    with (root / "mlruns/0/run1/metrics/accuracy").open("a", encoding="utf-8") as handle:
        handle.write(text)


def test_mlflow_summary_lifecycle(tmp_path, capsys):
    root = make_store(tmp_path)

    assert main(["init", str(root), "--adapter", "mlflow", "--name", "demo", "--id", "demo"]) == 0
    assert main(["validate", str(root), "--strict"]) == 0
    assert result_doc(root)["value"] == 0.7

    append_row(root, "1700000002000 0.9 3\n")
    assert main(["validate", str(root), "--strict"]) == 1

    assert main(["rehash", str(root), "--all"]) == 0
    after = result_doc(root)
    assert after["value"] == 0.9
    assert after["metadata"] == {"step": 3, "timestamp_ms": 1700000002000}
    assert main(["validate", str(root), "--strict"]) == 0

    # Repair: a hand-edited stale summary with an unchanged digest.
    doc = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    doc["results"][0]["value"] = 0.1
    doc["results"][0]["metadata"] = {"step": 0, "timestamp_ms": 0}
    (root / MANIFEST_FILENAME).write_text(json.dumps(doc), encoding="utf-8")
    assert main(["validate", str(root), "--strict"]) == 1
    assert main(["rehash", str(root), "--all"]) == 0
    assert main(["validate", str(root), "--strict"]) == 0

    # An unreadable history is visible, not silently dropped.
    append_row(root, "broken row\n")
    assert main(["rehash", str(root), "--all"]) == 2
    assert main(["validate", str(root)]) == 1  # hash mismatch
    capsys.readouterr()
