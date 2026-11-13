"""One-shot leaderboard contract tests using synthetic serialized manifests."""
import json
from pathlib import Path

from runevidence.cli import main
from runevidence.manifest import dump_manifest
from runevidence.models import Artifact, Manifest, Project, Result, Run


def _root(tmp_path, *, runs, results, artifacts=None):
    folder = tmp_path / "experiment"
    folder.mkdir()
    manifest = Manifest(
        project=Project(id="demo", name="Demo"),
        runs=runs, results=results, artifacts=artifacts or [],
    )
    path = folder / "runevidence.json"
    path.write_text(dump_manifest(manifest), encoding="utf-8", newline="\n")
    return folder, path


def _call(path, capsys, *, metric="score", direction="max", top=10, text=False):
    argv = ["leaderboard", str(path), "--metric", metric, "--direction", direction, "--top", str(top)]
    if not text:
        argv.append("--json")
    code = main(argv)
    captured = capsys.readouterr()
    assert not captured.err
    return code, captured.out if text else json.loads(captured.out)


def _data(tmp_path):
    return _root(
        tmp_path,
        runs=[Run(id=i, name="Run " + i) for i in ("a", "b", "c", "d", "e")],
        results=[
            Result(id="ra", run_id="a", name="score", value=0.7),
            Result(id="rb", run_id="b", name="score", value=0.9),
            Result(id="rc", run_id="c", name="score", value=0.9),
            Result(id="rd", run_id="d", name="score", value="not a number"),
        ],
    )


def test_max_direction_deterministic_ties_and_missing_exclusions(tmp_path, capsys):
    folder, _ = _data(tmp_path)
    code, doc = _call(folder, capsys)
    assert code == 0
    assert [(r["run_id"], r["rank"]) for r in doc["ranked"]] == [("b", 1), ("c", 1), ("a", 3)]
    assert [x["run_id"] for x in doc["missing"]] == ["e"]
    assert [x["run_id"] for x in doc["excluded"]] == ["d"]
    assert doc["summary"] == {
        "runs_total": 5, "eligible": 3, "shown": 3,
        "omitted_from_ranking": 0, "missing": 1, "excluded": 1,
    }
    assert "Recorded manifest values only" in doc["note"]


def test_min_direction_and_top_cap_report_omitted(tmp_path, capsys):
    folder, _ = _data(tmp_path)
    code, doc = _call(folder, capsys, direction="min", top=2)
    assert code == 0
    assert [(r["run_id"], r["rank"]) for r in doc["ranked"]] == [("a", 1), ("b", 2)]
    assert doc["summary"]["omitted_from_ranking"] == 1


def test_text_format_missing_and_nonnumeric_and_no_control_injection(tmp_path, capsys):
    folder, _ = _root(
        tmp_path,
        runs=[Run(id="a", name="foo\nERROR forged"), Run(id="b"), Run(id="c")],
        results=[Result(id="r1", run_id="a", name="metric\tvalue", value=1),
                 Result(id="r2", run_id="b", name="metric\tvalue", value=None)],
    )
    code, text = _call(folder, capsys, metric="metric\tvalue", text=True)
    assert code == 0
    assert "foo\\nERROR forged" in text
    assert "metric\\tvalue" in text
    assert "\nERROR forged" not in text
    assert "Missing metric: 1" in text
    assert "Excluded nonnumeric: 1" in text


def test_numeric_sort_does_not_float_round_large_integers(tmp_path, capsys):
    folder, _ = _root(tmp_path, runs=[Run(id="a"), Run(id="b"), Run(id="c")], results=[
        Result(id="aa", run_id="a", name="m", value=2**53+1),
        Result(id="bb", run_id="b", name="m", value=float(2**53)),
        Result(id="cc", run_id="c", name="m", value=2**53),
    ])
    code, doc = _call(folder, capsys, metric="m")
    assert code == 0
    assert [(r["run_id"], r["rank"]) for r in doc["ranked"]] == [("a", 1), ("b", 2), ("c", 2)]
    assert type(doc["ranked"][0]["value"]) is int
    assert type(doc["ranked"][1]["value"]) is float


def test_duplicate_selected_metric_refused(tmp_path, capsys):
    folder, _ = _root(tmp_path, runs=[Run(id="a")], results=[
        Result(id="aa", run_id="a", name="m", value=1),
        Result(id="bb", run_id="a", name="m", value=2),
    ])
    code, doc = _call(folder, capsys, metric="m")
    assert code == 2 and doc["exit_code"] == 2
    assert "multiple results" in doc["error"]


def test_duplicate_run_id_refused(tmp_path, capsys):
    folder, _ = _root(tmp_path, runs=[Run(id="a"), Run(id="a")], results=[])
    code, doc = _call(folder, capsys)
    assert code == 2 and "duplicate run ID" in doc["error"]


def test_no_numeric_values_is_accurately_empty(tmp_path, capsys):
    folder, _ = _root(tmp_path, runs=[Run(id="a"), Run(id="b")], results=[
        Result(id="aa", run_id="a", name="score", value=True),
    ])
    code, doc = _call(folder, capsys)
    assert code == 0
    assert doc["ranked"] == [] and doc["summary"]["eligible"] == 0
    assert doc["summary"]["excluded"] == doc["summary"]["missing"] == 1


def test_bad_arguments_and_nonexistent_manifest(tmp_path, capsys):
    folder, _ = _data(tmp_path)
    for args in ("0", "1001", "-1"):
        code, doc = _call(folder, capsys, top=args)
        assert code == 2 and doc["exit_code"] == 2
    code, doc = _call(tmp_path / "absent", capsys)
    assert code == 2 and doc["exit_code"] == 2


def test_does_not_access_absent_artifacts_or_mutate_manifest(tmp_path, capsys):
    folder, path = _root(tmp_path, runs=[Run(id="a")], results=[
        Result(id="aaa", run_id="a", name="score", value=1.0),
    ], artifacts=[Artifact(id="absent", path="missing-checkpoint.bin", sha256="a"*64)])
    before = path.read_bytes()
    code, doc = _call(folder, capsys)
    assert code == 0 and doc["ranked"][0]["value"] == 1.0
    assert path.read_bytes() == before
    assert {p.name for p in folder.iterdir()} == {"runevidence.json"}


def test_compare_and_inspect_commands_remain_unchanged(tmp_path, capsys):
    folder, _ = _data(tmp_path)
    code = main(["compare", str(folder), "--base", "a", "--target", "b", "--json"])
    assert code == 0 and json.loads(capsys.readouterr().out)["summary"]["results_changed"] == 1
    code = main(["inspect", str(folder), "--json"])
    assert code == 0 and "summary" in json.loads(capsys.readouterr().out)
