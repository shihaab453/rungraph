"""Read-only metric matrix CLI contract with disposable, synthetic manifests."""

import json
from pathlib import Path

from runevidence.cli import main
from runevidence.manifest import dump_manifest
from runevidence.models import Artifact, Manifest, Project, Result, Run
from runevidence.run_matrix import MatrixError, format_metric_matrix, metric_matrix


def _manifest(*, runs=None, results=None, artifacts=None):
    return Manifest(
        project=Project(id="demo", name="Demo"),
        runs=(runs if runs is not None else [Run(id="b", name="Beta"), Run(id="a", name="Alpha")]),
        results=(results if results is not None else [
            Result(id="r1", run_id="a", name="accuracy", value=0.8),
            Result(id="r2", run_id="b", name="loss", value=0.2),
            Result(id="r3", run_id="b", name="accuracy", value=0.9),
        ]),
        artifacts=artifacts or [],
    )


def _root(tmp_path, manifest):
    root = tmp_path / "experiment"
    root.mkdir()
    f = root / "runevidence.json"
    f.write_text(dump_manifest(manifest), encoding="utf-8", newline="\n")
    return root, f


def _call(root, capsys, *extra):
    code = main(["matrix", str(root), *extra, "--json"])
    cap = capsys.readouterr()
    assert cap.err == ""
    return code, json.loads(cap.out)


def test_default_matrix_auto_discovers_and_sorts_runs_and_metrics(tmp_path, capsys):
    root, _ = _root(tmp_path, _manifest())
    code, doc = _call(root, capsys)
    assert code == 0
    assert doc["metrics"] == ["accuracy", "loss"]
    assert [r["run_id"] for r in doc["runs"]] == ["a", "b"]
    assert doc["runs"][0]["values"] == [
        {"metric": "accuracy", "status": "recorded", "value": 0.8},
        {"metric": "loss", "status": "missing"},
    ]
    assert doc["summary"] == {"runs": 2, "metrics": 2, "missing_cells": 1, "nonfinite_cells": 0}
    assert "not verified" in doc["note"]


def test_explicit_run_and_metric_order_and_absent_metric(tmp_path, capsys):
    root, _ = _root(tmp_path, _manifest())
    code, doc = _call(root, capsys, "--run", "b", "--run", "a", "--metric", "loss", "--metric", "not_collected")
    assert code == 0
    assert [r["run_id"] for r in doc["runs"]] == ["b", "a"]
    assert doc["metrics"] == ["loss", "not_collected"]
    assert doc["runs"][0]["values"][0]["value"] == 0.2
    assert doc["summary"]["missing_cells"] == 3


def test_unknown_and_duplicate_selection_refused(tmp_path, capsys):
    root, _ = _root(tmp_path, _manifest())
    for extra, phrase in (
        (("--run", "absent"), "not found"),
        (("--run", "a", "--run", "a"), "duplicate run selector"),
        (("--metric", "accuracy", "--metric", "accuracy"), "duplicate metric selector"),
    ):
        code, doc = _call(root, capsys, *extra)
        assert code == 2 and doc["exit_code"] == 2 and doc["ok"] is False
        assert phrase in doc["error"]


def test_duplicate_run_and_duplicate_selected_metric_are_refused(tmp_path, capsys):
    root, _ = _root(tmp_path, _manifest(runs=[Run(id="a"), Run(id="a")], results=[]))
    code, doc = _call(root, capsys)
    assert code == 2 and "duplicate run ID" in doc["error"]
    other = _manifest(runs=[Run(id="a")], results=[
        Result(id="one", run_id="a", name="loss", value=1),
        Result(id="two", run_id="a", name="loss", value=2),
    ])
    data_root = tmp_path / "duplicate"
    data_root.mkdir()
    (data_root / "runevidence.json").write_text(dump_manifest(other), encoding="utf-8")
    code, doc = _call(data_root, capsys)
    assert code == 2 and "multiple results" in doc["error"]


def test_recorded_null_and_boolean_not_conflated_with_missing(tmp_path, capsys):
    manifest = _manifest(runs=[Run(id="a"), Run(id="b")], results=[
        Result(id="one", run_id="a", name="nullable", value=None),
        Result(id="two", run_id="b", name="nullable", value=True),
    ])
    root, _ = _root(tmp_path, manifest)
    code, doc = _call(root, capsys)
    assert code == 0
    assert doc["runs"][0]["values"] == [{"metric": "nullable", "status": "recorded", "value": None}]
    assert doc["runs"][1]["values"] == [{"metric": "nullable", "status": "recorded", "value": True}]


def test_in_memory_nonfinite_values_are_explicit_not_invalid_json():
    manifest = _manifest(runs=[Run(id="a"), Run(id="b")], results=[
        Result(id="one", run_id="a", name="score", value=float("nan")),
        Result(id="two", run_id="b", name="score", value=float("inf")),
    ])
    doc = metric_matrix(manifest, manifest_path="detached")
    assert doc["summary"]["nonfinite_cells"] == 2
    assert all(r["values"] == [{"metric": "score", "status": "nonfinite"}] for r in doc["runs"])
    json.dumps(doc, allow_nan=False)


def test_text_escapes_untrusted_metric_run_name_and_string_value(tmp_path, capsys):
    manifest = _manifest(runs=[Run(id="a", name="A\nERROR fake")], results=[
        Result(id="r1", run_id="a", name="x\tmetric", value="val\nFAIL fake"),
    ])
    root, _ = _root(tmp_path, manifest)
    code = main(["matrix", str(root)])
    text = capsys.readouterr().out
    assert code == 0
    assert "A\\nERROR fake" in text and "x\\tmetric" in text and "val\\nFAIL fake" in text
    assert "\nERROR fake" not in text and "\nFAIL fake" not in text
    assert "Recorded values only" in text


def test_empty_inputs_and_nonexistent_manifest(tmp_path, capsys):
    root, _ = _root(tmp_path, _manifest(runs=[], results=[]))
    code, doc = _call(root, capsys)
    assert code == 0 and doc["runs"] == [] and doc["metrics"] == []
    code, doc = _call(tmp_path / "missing", capsys)
    assert code == 2 and doc["exit_code"] == 2


def test_no_artifact_access_and_manifest_unchanged(tmp_path, capsys):
    manifest = _manifest(artifacts=[Artifact(id="ghost", path="not-present.bin", sha256="a" * 64)])
    root, path = _root(tmp_path, manifest)
    before = path.read_bytes()
    code, doc = _call(root, capsys)
    assert code == 0 and doc["summary"]["runs"] == 2
    assert path.read_bytes() == before
    assert set(p.name for p in root.iterdir()) == {"runevidence.json"}


def test_existing_compare_leaderboard_and_inspect_still_dispatch(tmp_path, capsys):
    root, _ = _root(tmp_path, _manifest())
    cases = [
        ["compare", str(root), "--base", "a", "--target", "b", "--json"],
        ["leaderboard", str(root), "--metric", "accuracy", "--direction", "max", "--json"],
        ["inspect", str(root), "--json"],
    ]
    for args in cases:
        result = main(args)
        doc = json.loads(capsys.readouterr().out)
        assert result == 0 and isinstance(doc, dict)
