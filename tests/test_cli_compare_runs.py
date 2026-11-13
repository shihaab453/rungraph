"""Real CLI comparison tests with only disposable synthetic manifest files."""

import json
from pathlib import Path

from runevidence.cli import main
from runevidence.manifest import dump_manifest
from runevidence.models import Artifact, Manifest, Project, Result, Run


def root(tmp_path, *, runs=None, results=None, artifacts=None):
    directory = tmp_path / "project"
    directory.mkdir()
    manifest = Manifest(
        project=Project(id="demo", name="Demo"),
        runs=runs if runs is not None else [
            Run(id="before", name="baseline", params={"lr": 0.01, "seed": 1}),
            Run(id="after", name="target", params={"lr": 0.001, "seed": 1}),
        ],
        results=results if results is not None else [
            Result(id="a", run_id="before", name="accuracy", value=0.7),
            Result(id="b", run_id="after", name="accuracy", value=0.8),
        ],
        artifacts=artifacts or [],
    )
    (directory / "runevidence.json").write_text(dump_manifest(manifest), encoding="utf-8", newline="\n")
    return directory


def cmp_json(directory, capsys, *, base="before", target="after"):
    exit_code = main(["compare", str(directory), "--base", base, "--target", target, "--json"])
    captured = capsys.readouterr()
    assert captured.err == ""
    return exit_code, json.loads(captured.out)


def test_mixed_numeric_and_params_json(tmp_path, capsys):
    exit_code, doc = cmp_json(root(tmp_path), capsys)
    assert exit_code == 0
    assert doc["baseline"] == {"id": "before", "name": "baseline"}
    assert doc["target"] == {"id": "after", "name": "target"}
    assert doc["parameters"] == [
        {"name": "lr", "baseline": 0.01, "target": 0.001, "status": "changed"},
        {"name": "seed", "baseline": 1, "target": 1, "status": "same"},
    ]
    assert doc["results"][0]["delta"] == 0.8 - 0.7
    assert doc["summary"]["results_changed"] == 1
    assert doc["summary"]["parameters_changed"] == 1
    assert "not revalidated" in doc["note"]


def test_text_human_output_and_escaped_controls(tmp_path, capsys):
    directory = root(tmp_path, runs=[
        Run(id="before", params={"config": "old\nERROR spoof"}),
        Run(id="after", params={"config": "new"}),
    ])
    code = main(["compare", str(directory), "--base", "before", "--target", "after"])
    output = capsys.readouterr().out
    assert code == 0
    assert "Parameters (1)" in output and "Results (1)" in output
    assert "\\nERROR spoof" in output
    assert "\nERROR spoof" not in output
    assert "not validated" in output and "No artifact files were opened" in output


def test_missing_recorded_entries_are_not_conflated_with_none(tmp_path, capsys):
    directory = root(tmp_path, runs=[Run(id="before", params={"old": None}), Run(id="after", params={"new": None})],
                     results=[Result(id="x", run_id="before", name="old_metric"),
                              Result(id="y", run_id="after", name="new_metric")])
    code, doc = cmp_json(directory, capsys)
    assert code == 0
    assert doc["parameters"] == [
        {"name": "new", "target": None, "status": "target_only"},
        {"name": "old", "baseline": None, "status": "baseline_only"},
    ]
    assert {r["status"] for r in doc["results"]} == {"target_only", "baseline_only"}
    assert doc["summary"]["results_baseline_only"] == doc["summary"]["results_target_only"] == 1


def test_bool_and_strings_are_not_numeric_deltas(tmp_path, capsys):
    directory = root(tmp_path, results=[
        Result(id="b0", run_id="before", name="bool", value=True),
        Result(id="b1", run_id="after", name="bool", value=False),
        Result(id="s0", run_id="before", name="status", value="old"),
        Result(id="s1", run_id="after", name="status", value="new"),
    ])
    code, doc = cmp_json(directory, capsys)
    assert code == 0
    assert all("delta" not in r for r in doc["results"])
    assert all(r["status"] == "changed" for r in doc["results"])


def test_delta_is_absolute_not_percent(tmp_path, capsys):
    directory = root(tmp_path, results=[Result(id="a", run_id="before", name="loss", value=0),
                                        Result(id="b", run_id="after", name="loss", value=2)])
    code, doc = cmp_json(directory, capsys)
    assert code == 0
    assert doc["results"][0]["delta"] == 2
    assert "percent" not in str(doc)


def test_ambiguous_metric_refused(tmp_path, capsys):
    directory = root(tmp_path, results=[Result(id="a", run_id="before", name="loss", value=1),
                                        Result(id="b", run_id="before", name="loss", value=2)])
    code, doc = cmp_json(directory, capsys)
    assert code == 2 and not doc["ok"]
    assert "multiple results" in doc["error"]


def test_ambiguous_or_unknown_run_id_and_same_id_refused(tmp_path, capsys):
    directory = root(tmp_path)
    for base, target, error in (("missing", "after", "not found"), ("before", "before", "different")):
        code, doc = cmp_json(directory, capsys, base=base, target=target)
        assert code == doc["exit_code"] == 2
        assert error in doc["error"]
    directory2 = tmp_path / "dupe"
    directory2.mkdir()
    doc2 = Manifest(project=Project(id="demo", name="demo"), runs=[Run(id="before"), Run(id="before"), Run(id="after")])
    (directory2 / "runevidence.json").write_text(dump_manifest(doc2), encoding="utf-8")
    code, doc = cmp_json(directory2, capsys)
    assert code == 2 and "ambiguous" in doc["error"]


def test_missing_manifest_uses_existing_exit_two(tmp_path, capsys):
    directory = tmp_path / "missing"
    directory.mkdir()
    code, doc = cmp_json(directory, capsys)
    assert code == 2
    assert doc["exit_code"] == 2
    assert doc["findings"][0]["code"] == "manifest-not-found"


def test_no_artifact_read_or_manifest_mutation(tmp_path, capsys):
    directory = root(tmp_path, artifacts=[Artifact(id="absent", path="does-not-exist.bin", sha256="a" * 64)])
    raw = (directory / "runevidence.json").read_bytes()
    code, doc = cmp_json(directory, capsys)
    assert code == 0 and doc["results"][0]["name"] == "accuracy"
    assert (directory / "runevidence.json").read_bytes() == raw
    assert [p.name for p in directory.iterdir()] == ["runevidence.json"]


def test_validate_inspect_original_json_contract_unchanged(tmp_path, capsys):
    directory = root(tmp_path)
    for command in ("validate", "inspect"):
        code = main([command, str(directory), "--json"])
        doc = json.loads(capsys.readouterr().out)
        assert code == 0
        assert "findings" in doc and "counts" in doc
        assert "baseline" not in doc and "target" not in doc
