"""Accepting intended changes: volatile files (G2), rehash (G1), add (G5)."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from conftest import DEEP_JSON_PARSES
from runevidence.adapters import Summary
from runevidence.checks import run_checks
from runevidence.cli import main
from runevidence.edit import EditError, plan_add
from runevidence.manifest import load_manifest, load_raw
from runevidence.models import Result

MANIFEST = "runevidence.json"


def read_doc(root: Path) -> dict:
    return json.loads((root / MANIFEST).read_text(encoding="utf-8"))


def write_doc(root: Path, doc: dict) -> None:
    (root / MANIFEST).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")


def artifact(doc: dict, art_id: str) -> dict:
    return next(a for a in doc["artifacts"] if a["id"] == art_id)


def codes(root: Path) -> list[str]:
    return [f.code.value for f in run_checks(load_manifest(root)).findings]


def retrain_tuned(root: Path, accuracy: float = 0.75) -> None:
    """A deliberate rerun of the tuned run: new weights, new metrics."""
    (root / "runs" / "tuned" / "model.bin").write_bytes(b"retrained weights\n")
    metrics = {"eval": {"split": "test", "accuracy": accuracy, "f1": 0.7}}
    text = json.dumps(metrics, indent=2) + "\n"
    (root / "runs" / "tuned" / "metrics.json").write_bytes(text.encode())


# --- volatile ----------------------------------------------------------------


def make_volatile_log(root: Path) -> None:
    (root / "runs" / "tuned" / "train.log").write_text("12:00 epoch 1\n", encoding="utf-8", newline="\n")
    doc = read_doc(root)
    doc["artifacts"].append(
        {"id": "tuned-log", "path": "runs/tuned/train.log", "volatile": True, "run_id": "run-tuned"}
    )
    doc["evidence"][0]["supports"].append("tuned-log")
    write_doc(root, doc)


def test_volatile_file_may_change_without_any_finding(experiment):
    make_volatile_log(experiment)
    (experiment / "runs" / "tuned" / "train.log").write_text("13:30 epoch 1\nmore\n", encoding="utf-8", newline="\n")

    assert codes(experiment) == []  # no hash-mismatch, no no-digest, no claim-at-risk


def test_volatile_file_must_still_exist(experiment):
    make_volatile_log(experiment)
    (experiment / "runs" / "tuned" / "train.log").unlink()

    found = codes(experiment)
    assert "missing-file" in found
    assert "claim-at-risk" in found


def test_volatile_ignores_a_stale_recorded_digest(experiment):
    doc = read_doc(experiment)
    artifact(doc, "report")["volatile"] = True
    write_doc(experiment, doc)
    (experiment / "REPORT.md").write_text("rewritten\n", encoding="utf-8", newline="\n")

    assert codes(experiment) == []


def test_volatile_metrics_still_check_recorded_result_values(experiment, capsys):
    doc = read_doc(experiment)
    artifact(doc, "tuned-metrics")["volatile"] = True
    write_doc(experiment, doc)
    retrain_tuned(experiment)

    found = codes(experiment)
    assert "result-value-mismatch" in found
    assert "claim-at-risk" in found
    assert main(["rehash", str(experiment), "tuned-metrics"]) == 0
    assert "source is volatile, so its value was left as is" in capsys.readouterr().out
    assert read_doc(experiment)["results"][1]["value"] == 1.0


def test_manifests_without_the_field_still_load_and_are_written_without_it(experiment, tmp_path, capsys):
    assert "volatile" not in json.dumps(read_doc(experiment))
    assert codes(experiment) == []
    main(["init", str(experiment), "--force"])
    assert '"volatile"' not in (experiment / MANIFEST).read_text(encoding="utf-8")


def test_init_volatile_glob_marks_matching_files(tmp_path, capsys):
    (tmp_path / "runs" / "a").mkdir(parents=True)
    (tmp_path / "runs" / "a" / "train.log").write_text("t0\n", encoding="utf-8", newline="\n")
    (tmp_path / "runs" / "a" / "metrics.json").write_text("{}\n", encoding="utf-8", newline="\n")

    assert main(["init", str(tmp_path), "--volatile", "*.log", "--volatile", "nothing/*"]) == 0
    out = capsys.readouterr().out
    doc = read_doc(tmp_path)

    log = artifact(doc, "runs-a-train.log")
    assert log["volatile"] is True and log["sha256"] is None and log["size_bytes"] is None
    assert list(log)[:5] == ["id", "path", "sha256", "size_bytes", "volatile"]
    assert "volatile" not in artifact(doc, "runs-a-metrics.json")
    assert "1 volatile" in out
    assert "'nothing/*' matches no file" in out
    (tmp_path / "runs" / "a" / "train.log").write_text("t1, longer\n", encoding="utf-8", newline="\n")
    assert main(["validate", str(tmp_path), "--strict"]) == 0


def test_init_force_warns_when_regeneration_drops_volatile_flags(tmp_path, capsys):
    (tmp_path / "train.log").write_bytes(b"log")
    assert main(["init", str(tmp_path), "--volatile", "*.log"]) == 0
    before = (tmp_path / MANIFEST).read_bytes()
    capsys.readouterr()

    assert main(["init", str(tmp_path), "--force"]) == 0

    assert "volatile flag on 1 artifact" in capsys.readouterr().out
    assert (tmp_path / (MANIFEST + ".bak")).read_bytes() == before


# --- rehash ------------------------------------------------------------------


def test_rehash_run_accepts_a_retrain_and_updates_the_result_value(experiment, capsys):
    retrain_tuned(experiment)
    assert main(["validate", str(experiment)]) == 1
    capsys.readouterr()

    assert main(["rehash", str(experiment), "run-tuned"]) == 0
    out = capsys.readouterr().out

    assert "tuned-model: sha256" in out
    assert "tuned-metrics: sha256" in out
    assert "result tuned-accuracy (test_accuracy): 1.0 -> 0.75" in out
    assert "Re-recorded 2 files and 1 result value (1 already up to date)" in out
    # Both claims cite tuned-accuracy, so both were just re-vouched for.
    assert "claim-tuned-perfect, claim-tuning-helps" in out
    assert artifact(read_doc(experiment), "tuned-model")["size_bytes"] == len(b"retrained weights\n")
    assert main(["validate", str(experiment), "--strict"]) == 0


def test_rehash_reminds_about_a_claim_supported_only_by_a_relationship(experiment, capsys):
    doc = read_doc(experiment)
    doc["evidence"][0]["supports"] = []
    doc["relationships"].append(
        {
            "source": "claim-tuned-perfect",
            "target": "tuned-metrics",
            "type": "supports",
            "note": None,
        }
    )
    write_doc(experiment, doc)
    metrics = experiment / "runs" / "tuned" / "metrics.json"
    metrics.write_bytes(metrics.read_bytes() + b"\n")

    assert main(["rehash", str(experiment), "tuned-metrics", "--dry-run"]) == 0
    out = capsys.readouterr().out

    assert "Re-read these claims; they rest on what changed: claim-tuned-perfect" in out


@pytest.mark.parametrize("bad_target", [None, "ghost-result"])
def test_rehash_does_not_remind_for_a_claim_without_valid_support(
    experiment, capsys, bad_target
):
    doc = read_doc(experiment)
    doc["evidence"][0]["supports"] = []
    if bad_target is not None:
        doc["relationships"].append(
            {
                "source": "claim-tuned-perfect",
                "target": bad_target,
                "type": "supports",
                "note": None,
            }
        )
    write_doc(experiment, doc)
    metrics = experiment / "runs" / "tuned" / "metrics.json"
    metrics.write_bytes(metrics.read_bytes() + b"\n")

    assert main(["rehash", str(experiment), "tuned-metrics", "--dry-run"]) == 0
    out = capsys.readouterr().out

    assert "claim-tuned-perfect" not in out


def test_rehash_changes_only_the_values_it_re_records(experiment, capsys):
    doc = read_doc(experiment)
    del artifact(doc, "tuned-model")["kind"]  # hand-written: a field left out
    doc["project"]["metadata"] = {"note": "kept exactly"}
    write_doc(experiment, doc)
    before = read_doc(experiment)
    retrain_tuned(experiment)

    main(["rehash", str(experiment), "tuned-model", "tuned-metrics"])
    after = read_doc(experiment)

    for key in ("sha256", "size_bytes"):
        before_model, after_model = artifact(before, "tuned-model"), artifact(after, "tuned-model")
        assert before_model[key] != after_model[key]
        after_model[key] = before_model[key]
        artifact(after, "tuned-metrics")[key] = artifact(before, "tuned-metrics")[key]
    after["results"][1]["value"] = before["results"][1]["value"]
    assert after == before
    assert "kind" not in artifact(read_doc(experiment), "tuned-model")


def test_rehash_accepts_paths_globs_and_paths_relative_to_the_current_folder(experiment, monkeypatch, capsys):
    retrain_tuned(experiment)
    monkeypatch.chdir(experiment / "runs")

    # As written in the manifest (backslashes allowed), and as seen from here.
    assert main(["rehash", str(experiment), "runs\\tuned\\model.bin", "tuned/metrics.json", "--dry-run"]) == 0
    assert "Would re-record 2 files" in capsys.readouterr().out
    assert main(["rehash", str(experiment), "runs/*/m*", "--dry-run"]) == 0
    assert "Would re-record 2 files and 1 result value (2 already up to date)" in capsys.readouterr().out


def test_rehash_folder_target_selects_every_file_inside(experiment, monkeypatch, capsys):
    retrain_tuned(experiment)

    assert main(["rehash", str(experiment), "runs/tuned/", "--dry-run"]) == 0
    assert "Would re-record 2 files and 1 result value (1 already up to date)" in capsys.readouterr().out
    monkeypatch.chdir(experiment / "runs" / "tuned")
    assert main(["rehash", str(experiment), ".", "--dry-run"]) == 0
    assert "(1 already up to date)" in capsys.readouterr().out


def test_rehash_does_not_overwrite_a_wrong_value_when_the_file_did_not_change(experiment, capsys):
    doc = read_doc(experiment)
    doc["results"][0]["value"] = 0.55  # typo: the file says 0.5
    write_doc(experiment, doc)

    assert main(["rehash", str(experiment), "--all"]) == 0

    assert read_doc(experiment)["results"][0]["value"] == 0.55
    assert "result-value-mismatch" in codes(experiment)


HUGE = 10**400  # far beyond float range


def huge_values_are_valid() -> bool:
    """Whether the installed Pydantic keeps an int beyond float range as a
    result value. Supported versions need not agree, so the rehash tests
    below accept either answer but insist on a controlled outcome."""
    try:
        Result.model_validate({"id": "r", "run_id": "run", "name": "n", "value": HUGE})
    except ValidationError:
        return False
    return True


def test_rehash_dry_run_with_a_huge_metrics_value_writes_nothing(experiment, capsys):
    retrain_tuned(experiment, accuracy=HUGE)
    before = (experiment / MANIFEST).read_bytes()

    exit_code = main(["rehash", str(experiment), "run-tuned", "--dry-run"])
    out = capsys.readouterr().out

    assert (experiment / MANIFEST).read_bytes() == before
    if huge_values_are_valid():
        assert exit_code == 0
        assert f"result tuned-accuracy (test_accuracy): 1.0 -> {HUGE}" in out
        assert "Dry run: nothing was written." in out
    else:
        assert exit_code == 2
        assert "Nothing was written." in out


def test_rehash_with_a_huge_metrics_value_records_it_or_refuses_cleanly(experiment, capsys):
    retrain_tuned(experiment, accuracy=HUGE)
    before = (experiment / MANIFEST).read_bytes()

    exit_code = main(["rehash", str(experiment), "run-tuned"])
    out = capsys.readouterr().out

    if huge_values_are_valid():
        assert exit_code == 0
        assert read_doc(experiment)["results"][1]["value"] == HUGE
        assert main(["validate", str(experiment), "--strict"]) == 0
    else:
        assert exit_code == 2
        assert "Nothing was written." in out
        assert (experiment / MANIFEST).read_bytes() == before


def test_rehash_dry_run_writes_nothing(experiment, capsys):
    retrain_tuned(experiment)
    before = (experiment / MANIFEST).read_bytes()

    assert main(["rehash", str(experiment), "--all", "--dry-run"]) == 0
    out = capsys.readouterr().out

    assert "Would re-record 2 files" in out
    assert "Dry run: nothing was written." in out
    assert (experiment / MANIFEST).read_bytes() == before


def test_rehash_all_with_nothing_changed_leaves_the_file_alone(experiment, capsys):
    before = (experiment / MANIFEST).read_bytes()

    assert main(["rehash", str(experiment), "--all"]) == 0

    assert "Re-recorded 0 files (9 already up to date)" in capsys.readouterr().out
    assert (experiment / MANIFEST).read_bytes() == before


def test_rehash_refuses_missing_files_and_writes_nothing(experiment, capsys):
    retrain_tuned(experiment)
    (experiment / "data" / "test.csv").unlink()
    before = (experiment / MANIFEST).read_bytes()

    assert main(["rehash", str(experiment), "--all"]) == 2
    out = capsys.readouterr().out

    assert "'test-data' (data/test.csv): does not exist" in out
    assert "Nothing was written." in out
    assert (experiment / MANIFEST).read_bytes() == before


def _drive_shaped_manifest(root: Path, data: bytes) -> None:
    """A manifest whose artifact path is the literal `C:/x.bin` inside root."""
    (root / "C:").mkdir(parents=True)
    (root / "C:" / "x.bin").write_bytes(data)
    write_doc(
        root,
        {
            "schema_version": "0.2",
            "project": {"id": "p", "name": "P"},
            "runs": [{"id": "run", "name": "Run"}],
            "artifacts": [
                {
                    "id": "a",
                    "path": "C:/x.bin",
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size_bytes": len(data),
                }
            ],
            "results": [],
            "relationships": [],
        },
    )


@pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX-only evidence: 'C:/x.bin' is a literal relative path only on POSIX",
)
def test_rehash_reads_a_drive_shaped_artifact_from_any_cwd(
    tmp_path, monkeypatch, capsys
):
    """`C:/x` is root-joined, not `Path("C:/x")` relative to the process cwd."""
    root = tmp_path / "root"
    root.mkdir()
    _drive_shaped_manifest(root, b"old\n")
    (root / "C:" / "x.bin").write_bytes(b"new bytes\n")
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    monkeypatch.chdir(decoy)

    assert main(["rehash", str(root), "a"]) == 0
    capsys.readouterr()

    entry = artifact(read_doc(root), "a")
    assert entry["sha256"] == hashlib.sha256(b"new bytes\n").hexdigest()
    assert entry["size_bytes"] == len(b"new bytes\n")


@pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX-only evidence: 'C:/x.bin' is a literal relative path only on POSIX",
)
def test_add_sees_a_tracked_drive_shaped_artifact_from_any_cwd(tmp_path, monkeypatch):
    """The tracked-file key is root-joined, so the same file is not added twice."""
    root = tmp_path / "root"
    root.mkdir()
    _drive_shaped_manifest(root, b"old\n")
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    monkeypatch.chdir(decoy)

    loaded = load_manifest(root)
    with pytest.raises(EditError, match="already tracked"):
        plan_add(loaded, load_raw(loaded), [root / "C:" / "x.bin"])


def test_rehash_needs_targets_or_all_and_known_targets(experiment, capsys):
    assert main(["rehash", str(experiment)]) == 2
    assert "pass --all" in capsys.readouterr().out
    assert main(["rehash", str(experiment), "report", "--all"]) == 2
    assert main(["rehash", str(experiment), "--all", "--volatile"]) == 2
    assert main(["rehash", str(experiment), "report", "no-such-thing"]) == 2
    assert "No artifact matches 'no-such-thing'" in capsys.readouterr().out


def test_rehash_volatile_marks_and_no_volatile_restores_the_digest(experiment, capsys):
    (experiment / "REPORT.md").write_text("edited by hand\n", encoding="utf-8", newline="\n")

    assert main(["rehash", str(experiment), "report", "--volatile"]) == 0
    entry = artifact(read_doc(experiment), "report")
    assert entry["volatile"] is True and entry["sha256"] is None and entry["size_bytes"] is None
    assert list(entry)[:5] == ["id", "path", "sha256", "size_bytes", "volatile"]
    assert "now volatile" in capsys.readouterr().out
    assert codes(experiment) == []

    assert main(["rehash", str(experiment), "REPORT.md", "--no-volatile"]) == 0
    entry = artifact(read_doc(experiment), "report")
    assert "volatile" not in entry
    assert entry["size_bytes"] == len(b"edited by hand\n")
    (experiment / "REPORT.md").write_text("edited again\n", encoding="utf-8", newline="\n")
    assert "hash-mismatch" in codes(experiment)


def test_rehash_leaves_a_value_alone_when_the_locator_is_gone(experiment, capsys):
    (experiment / "runs" / "tuned" / "metrics.json").write_bytes(b'{"renamed": {"acc": 0.5}}\n')

    assert main(["rehash", str(experiment), "tuned-metrics"]) == 0
    out = capsys.readouterr().out

    assert "NOTE tuned-accuracy: /eval/accuracy is not in runs/tuned/metrics.json any more" in out
    assert read_doc(experiment)["results"][1]["value"] == 1.0
    assert "result-unverifiable" in codes(experiment)


@pytest.mark.parametrize(
    "token", ["\u00b2", "01", "9" * 5000], ids=["superscript-two", "zero-padded", "5000-digits"]
)
def test_rehash_leaves_a_value_alone_when_a_list_index_is_malformed(experiment, capsys, token):
    locator = "/eval/scores/" + token
    doc = read_doc(experiment)
    doc["results"][1]["locator"] = locator
    write_doc(experiment, doc)
    new_metrics = b'{"eval": {"scores": [0.25, 0.5]}}\n'
    (experiment / "runs" / "tuned" / "metrics.json").write_bytes(new_metrics)

    assert main(["rehash", str(experiment), "tuned-metrics"]) == 0
    out = capsys.readouterr().out

    assert f"NOTE tuned-accuracy: {locator} is not in runs/tuned/metrics.json any more; value left as is" in out
    after = read_doc(experiment)
    assert after["results"][1]["value"] == 1.0
    assert artifact(after, "tuned-metrics")["size_bytes"] == len(new_metrics)
    assert codes(experiment) == ["result-unverifiable"]


def test_hash_mismatch_hint_names_the_rehash_command(experiment):
    retrain_tuned(experiment)
    report = run_checks(load_manifest(experiment))
    hint = next(f.hint for f in report.findings if f.code.value == "hash-mismatch")
    assert "runevidence rehash <folder> tuned-" in hint


# --- RE-10: verified MLflow metric summaries --------------------------------

METRIC_LINE = b"1700000000000 0.5 3\n"


def make_mlflow(root: Path, names: tuple[str, ...] = ("acc",)) -> Path:
    """A minimal folder shaped like the MLflow adapter's output: one run with
    MLflow provenance, and per metric a file backing a locator-less summary."""
    artifacts, results = [], []
    for i, name in enumerate(names):
        rel = f"mlruns/1/abc/metrics/{name}"
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(METRIC_LINE)
        artifacts.append({
            "id": f"metric-file-{i}", "path": rel, "sha256": hashlib.sha256(METRIC_LINE).hexdigest(),
            "size_bytes": len(METRIC_LINE), "kind": "metrics", "run_id": "run-a",
        })
        results.append({
            "id": f"metric-{i}", "run_id": "run-a", "name": name, "value": 0.5,
            "artifact_id": f"metric-file-{i}", "metadata": {"step": 3, "timestamp_ms": 1700000000000},
        })
    doc = {
        "schema_version": "0.1",
        "project": {"id": "mlflow-project", "name": "mlflow project"},
        "runs": [
            {"id": "run-a", "metadata": {"mlflow": {"experiment_id": "1", "run_id": "abc"}}},
            {"id": "run-b", "metadata": {"mlflow": {"experiment_id": "1", "run_id": "def"}}},
        ],
        "artifacts": artifacts,
        "results": results,
    }
    write_doc(root, doc)
    return root


def log_another_step(root: Path, name: str = "acc") -> None:
    with (root / "mlruns" / "1" / "abc" / "metrics" / name).open("ab") as f:
        f.write(b"1700000001000 0.75 4\n")


def rehash_out(capsys, root: Path, *args: str) -> tuple[int, str, bytes]:
    before = (root / MANIFEST).read_bytes()
    code = main(["rehash", str(root), *args])
    return code, capsys.readouterr().out, before


def test_rehash_refreshes_an_mlflow_summary_from_accepted_bytes(tmp_path, capsys):
    root = make_mlflow(tmp_path)
    log_another_step(root)

    code, out, _ = rehash_out(capsys, root, "metric-file-0")

    assert code == 0
    assert "result metric-0" in out and "0.5 -> 0.75" in out
    after = read_doc(root)
    assert after["results"][0]["value"] == 0.75
    assert after["results"][0]["metadata"] == {"step": 4, "timestamp_ms": 1700000001000}
    assert codes(root) == []


def test_rehash_refreshes_when_the_first_digest_is_recorded(tmp_path, capsys):
    root = make_mlflow(tmp_path)
    doc = read_doc(root)
    doc["artifacts"][0].update(sha256=None, size_bytes=None)
    write_doc(root, doc)

    code, _, _ = rehash_out(capsys, root, "metric-file-0")

    assert code == 0
    after = read_doc(root)
    assert after["artifacts"][0]["sha256"] == hashlib.sha256(METRIC_LINE).hexdigest()
    assert after["results"][0]["value"] == 0.5


def test_rehash_repairs_a_stale_summary_with_an_unchanged_digest(tmp_path, capsys):
    root = make_mlflow(tmp_path)
    doc = read_doc(root)
    doc["results"][0].update(value=0.1, metadata={"step": 1, "timestamp_ms": 1})
    write_doc(root, doc)

    code, _, _ = rehash_out(capsys, root, "metric-file-0")

    assert code == 0
    after = read_doc(root)
    assert after["results"][0]["value"] == 0.5
    assert after["results"][0]["metadata"] == {"step": 3, "timestamp_ms": 1700000000000}
    assert codes(root) == []


def test_rehash_leaves_a_volatile_summary_and_notes_it(tmp_path, capsys):
    root = make_mlflow(tmp_path)

    code, out, _ = rehash_out(capsys, root, "metric-file-0", "--volatile")
    assert code == 0 and "volatile" in out.lower()
    assert read_doc(root)["results"][0]["value"] == 0.5

    log_another_step(root)
    code, out, _ = rehash_out(capsys, root, "metric-file-0")
    assert code == 0 and "volatile" in out.lower()
    assert read_doc(root)["results"][0]["value"] == 0.5


def test_rehash_no_volatile_refreshes_and_records_the_digest(tmp_path, capsys):
    root = make_mlflow(tmp_path)
    assert main(["rehash", str(root), "metric-file-0", "--volatile"]) == 0
    capsys.readouterr()
    log_another_step(root)
    data = METRIC_LINE + b"1700000001000 0.75 4\n"

    code, out, _ = rehash_out(capsys, root, "metric-file-0", "--no-volatile")

    assert code == 0 and "0.5 -> 0.75" in out
    after = read_doc(root)
    assert after["artifacts"][0]["sha256"] == hashlib.sha256(data).hexdigest()
    assert after["results"][0]["value"] == 0.75


def test_rehash_does_not_rewrite_an_unchanged_summary(tmp_path, capsys):
    root = make_mlflow(tmp_path)
    before = (root / MANIFEST).read_bytes()

    code, out, _ = rehash_out(capsys, root, "metric-file-0")

    assert code == 0
    assert "0.5 -> 0.5" not in out
    assert (root / MANIFEST).read_bytes() == before


def test_rehash_dry_run_matches_the_write_and_changes_nothing(tmp_path, capsys):
    root = make_mlflow(tmp_path)
    log_another_step(root)
    before = (root / MANIFEST).read_bytes()

    code, dry, _ = rehash_out(capsys, root, "metric-file-0", "--dry-run")
    assert code == 0
    assert (root / MANIFEST).read_bytes() == before
    assert "0.5 -> 0.75" in dry

    code, written, _ = rehash_out(capsys, root, "metric-file-0")
    assert code == 0 and "0.5 -> 0.75" in written


def test_rehash_refreshes_only_selected_summaries(tmp_path, capsys):
    root = make_mlflow(tmp_path, names=("acc", "loss"))
    log_another_step(root, "acc")
    log_another_step(root, "loss")

    code, _, _ = rehash_out(capsys, root, "metric-file-0")

    assert code == 0
    after = read_doc(root)
    assert after["results"][0]["value"] == 0.75
    assert after["results"][1]["value"] == 0.5


def test_rehash_refreshes_a_nested_metric_name(tmp_path, capsys):
    root = make_mlflow(tmp_path, names=("a/b",))
    log_another_step(root, "a/b")

    code, out, _ = rehash_out(capsys, root, "metric-file-0")

    assert code == 0 and "0.5 -> 0.75" in out


def test_rehash_refuses_an_unreadable_metric_history(tmp_path, capsys):
    root = make_mlflow(tmp_path)
    (root / "mlruns/1/abc/metrics/acc").write_bytes(b"not a metric row\n")
    before = (root / MANIFEST).read_bytes()

    code = main(["rehash", str(root), "metric-file-0"])
    out = capsys.readouterr().out

    assert code == 2
    assert "Nothing was written" in out
    assert (root / MANIFEST).read_bytes() == before


def test_rehash_notes_when_no_adapter_can_refresh(tmp_path, capsys, monkeypatch):
    root = make_mlflow(tmp_path)
    log_another_step(root)
    monkeypatch.setattr("runevidence.summaries.summary_claimants", lambda *a, **k: ([], "absent"))

    code, out, _ = rehash_out(capsys, root, "metric-file-0")

    assert code == 0
    assert "validate will report it as unverified" in out
    assert read_doc(root)["results"][0]["value"] == 0.5


def test_rehash_refreshes_a_0_2_summary(tmp_path, capsys):
    root = make_mlflow(tmp_path)
    doc = read_doc(root)
    doc["schema_version"] = "0.2"
    write_doc(root, doc)
    log_another_step(root)

    code, out, _ = rehash_out(capsys, root, "metric-file-0")

    assert code == 0
    assert "0.5 -> 0.75" in out
    assert read_doc(root)["results"][0]["value"] == 0.75


def test_rehash_does_not_refresh_a_0_3_summary(tmp_path, capsys):
    root = make_mlflow(tmp_path)
    doc = read_doc(root)
    doc["schema_version"] = "0.3"
    write_doc(root, doc)
    log_another_step(root)

    code, out, _ = rehash_out(capsys, root, "metric-file-0")

    assert code == 0
    assert "was not re-read" in out
    assert "0.5 ->" not in out
    assert read_doc(root)["results"][0]["value"] == 0.5


def test_rehash_does_not_note_an_unchanged_0_3_summary(tmp_path, capsys):
    root = make_mlflow(tmp_path)
    doc = read_doc(root)
    doc["schema_version"] = "0.3"
    write_doc(root, doc)

    code, out, _ = rehash_out(capsys, root, "metric-file-0")

    assert code == 0
    assert "was not re-read" not in out


class _RaisingOwner:
    def read_summary(self, data):
        raise ZeroDivisionError


class _MalformedOwner:
    def __init__(self, summary):
        self.summary = summary

    def read_summary(self, data):
        return self.summary


class _ExplodingValue:
    @property
    def value(self):
        raise RuntimeError("boom")

    step = 3
    timestamp = 1


@pytest.mark.parametrize(
    "owner",
    [
        _RaisingOwner(),
        _MalformedOwner(Summary("0.9", 3, 1)),
        _MalformedOwner(Summary(float("nan"), 3, 1)),
        _MalformedOwner(Summary(10**400, 3, 1)),
        _MalformedOwner(Summary(0.9, True, 1)),
        _MalformedOwner(_ExplodingValue()),
        _MalformedOwner({"value": 0.9, "step": 3, "timestamp": 1}),
    ],
    ids=["raises", "string-value", "nan", "huge-int", "bool-step", "raising-property", "dict"],
)
def test_rehash_refuses_a_broken_adapter_and_writes_nothing(tmp_path, capsys, monkeypatch, owner):
    root = make_mlflow(tmp_path)
    log_another_step(root)
    monkeypatch.setattr("runevidence.summaries.summary_claimants", lambda *a, **k: ([owner], "ok"))
    before = (root / MANIFEST).read_bytes()

    code = main(["rehash", str(root), "metric-file-0"])
    out = capsys.readouterr().out

    assert code == 2
    assert "Nothing was written" in out
    assert (root / MANIFEST).read_bytes() == before


def test_rehash_refuses_an_ambiguous_owner(tmp_path, capsys, monkeypatch):
    root = make_mlflow(tmp_path)
    log_another_step(root)
    monkeypatch.setattr(
        "runevidence.summaries.summary_claimants",
        lambda *a, **k: ([object(), object()], "ambiguous"),
    )
    before = (root / MANIFEST).read_bytes()

    code = main(["rehash", str(root), "metric-file-0"])
    out = capsys.readouterr().out

    assert code == 2
    assert "more than one installed adapter" in out
    assert (root / MANIFEST).read_bytes() == before


def test_rehash_refuses_a_broken_claim_hook(tmp_path, capsys, monkeypatch):
    root = make_mlflow(tmp_path)
    log_another_step(root)
    monkeypatch.setattr("runevidence.summaries.summary_claimants", lambda *a, **k: ([], "broken"))
    before = (root / MANIFEST).read_bytes()

    code = main(["rehash", str(root), "metric-file-0"])
    out = capsys.readouterr().out

    assert code == 2
    assert "Nothing was written" in out
    assert (root / MANIFEST).read_bytes() == before


def test_rehash_refuses_a_digest_race(tmp_path, capsys, monkeypatch):
    root = make_mlflow(tmp_path)
    log_another_step(root)
    monkeypatch.setattr(
        "runevidence.edit.read_bounded",
        lambda *a, **k: (b"1 0.5 3\n", "ok"),
    )
    before = (root / MANIFEST).read_bytes()

    code = main(["rehash", str(root), "metric-file-0"])
    out = capsys.readouterr().out

    assert code == 2
    assert "changed while the manifest was being updated" in out
    assert (root / MANIFEST).read_bytes() == before


def _no_mlflow_run(doc: dict) -> None:
    doc["runs"][0]["metadata"] = {}


def _mlflow_marker_not_a_dict(doc: dict) -> None:
    doc["runs"][0]["metadata"] = {"mlflow": True}


def _no_step(doc: dict) -> None:
    del doc["results"][0]["metadata"]["step"]


def _no_timestamp(doc: dict) -> None:
    del doc["results"][0]["metadata"]["timestamp_ms"]


def _not_a_metrics_file(doc: dict) -> None:
    doc["artifacts"][0]["kind"] = "log"


def _file_of_another_run(doc: dict) -> None:
    doc["artifacts"][0]["run_id"] = "run-b"


def _name_is_only_a_substring(doc: dict) -> None:
    doc["results"][0]["name"] = "cc"


def _name_is_only_the_nested_tail(doc: dict) -> None:
    doc["results"][0]["name"] = "b"


def _has_a_locator(doc: dict) -> None:
    doc["results"][0]["locator"] = "/acc"


@pytest.mark.parametrize(
    "weaken",
    [
        _no_mlflow_run,
        _mlflow_marker_not_a_dict,
        _no_step,
        _no_timestamp,
        _not_a_metrics_file,
        _file_of_another_run,
        _name_is_only_a_substring,
        _name_is_only_the_nested_tail,
        _has_a_locator,
    ],
)
def test_rehash_does_not_touch_a_summary_without_full_mlflow_provenance(tmp_path, capsys, weaken):
    root = make_mlflow(tmp_path, names=("a/b",) if weaken is _name_is_only_the_nested_tail else ("acc",))
    doc = read_doc(root)
    weaken(doc)
    write_doc(root, doc)
    log_another_step(root, doc["artifacts"][0]["path"].split("/metrics/")[1])

    code, _, _ = rehash_out(capsys, root, "metric-file-0", "--dry-run")

    assert code == 0
    assert read_doc(root)["results"][0]["value"] == 0.5


def test_volatile_locator_note_is_kept_alongside_summary_handling(experiment, capsys):
    assert main(["rehash", str(experiment), "tuned-metrics", "--volatile"]) == 0
    out = capsys.readouterr().out

    assert "NOTE tuned-accuracy: source is volatile, so its value was left as is" in out
    assert "MLflow" not in out


# --- add ---------------------------------------------------------------------


def test_add_tracks_one_new_file(experiment, monkeypatch, capsys):
    (experiment / "NOTES.md").write_text("notes\n", encoding="utf-8", newline="\n")
    monkeypatch.chdir(experiment)

    assert main(["add", ".", "NOTES.md", "--kind", "notes", "--id", "notes"]) == 0
    out = capsys.readouterr().out

    entry = artifact(read_doc(experiment), "notes")
    assert entry["path"] == "NOTES.md"
    assert entry["kind"] == "notes"
    assert entry["run_id"] is None  # root files belong to no run here
    assert entry["size_bytes"] == len(b"notes\n")
    assert "volatile" not in entry
    assert "Added 1 artifact" in out
    assert main(["inspect", ".", "--strict"]) == 0
    assert "untracked-file" not in capsys.readouterr().out


def test_add_dry_run_preserves_manifest_and_experiment_files(experiment, monkeypatch, capsys):
    new_file = experiment / "new.txt"
    new_file.write_bytes(b"new evidence")
    monkeypatch.chdir(experiment)
    before = {p.relative_to(experiment): p.read_bytes() for p in experiment.rglob("*") if p.is_file()}

    assert main(["add", ".", "new.txt", "--dry-run"]) == 0

    assert "Would add 1 artifact" in capsys.readouterr().out
    after = {p.relative_to(experiment): p.read_bytes() for p in experiment.rglob("*") if p.is_file()}
    assert after == before


def test_failed_manifest_replacement_preserves_original_and_removes_staging_file(experiment, monkeypatch, capsys):
    import runevidence.manifest as manifest_io

    (experiment / "new.txt").write_bytes(b"new evidence")
    monkeypatch.chdir(experiment)
    before = (experiment / MANIFEST).read_bytes()

    def refuse_replace(source, destination):
        raise PermissionError("replacement refused")

    monkeypatch.setattr(manifest_io.os, "replace", refuse_replace)
    assert main(["add", ".", "new.txt"]) == 2
    out = capsys.readouterr().out
    assert "Nothing was written" in out
    assert "Added 1 artifact" not in out
    assert (experiment / MANIFEST).read_bytes() == before
    assert not list(experiment.glob(".runevidence.json.*.tmp"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission modes")
def test_edit_preserves_shared_manifest_permissions(experiment, monkeypatch):
    manifest = experiment / MANIFEST
    manifest.chmod(0o640)
    (experiment / "new.txt").write_bytes(b"new evidence")
    monkeypatch.chdir(experiment)

    assert main(["add", ".", "new.txt"]) == 0

    assert stat.S_IMODE(manifest.stat().st_mode) == 0o640


def test_add_infers_the_run_from_neighbouring_files_and_can_be_overridden(experiment, monkeypatch, capsys):
    (experiment / "runs" / "tuned" / "plots").mkdir()
    (experiment / "runs" / "tuned" / "plots" / "roc.png").write_bytes(b"png")
    (experiment / "runs" / "tuned" / "notes.txt").write_bytes(b"n")
    monkeypatch.chdir(experiment)

    assert main(["add", ".", "runs/tuned/plots/roc.png"]) == 0
    assert "run run-tuned (from neighbouring files" in capsys.readouterr().out
    assert artifact(read_doc(experiment), "runs-tuned-plots-roc.png")["run_id"] == "run-tuned"

    assert main(["add", ".", "runs/tuned/notes.txt", "--no-run"]) == 0
    assert artifact(read_doc(experiment), "runs-tuned-notes.txt")["run_id"] is None


def test_add_folder_adds_only_untracked_files(experiment, monkeypatch, capsys):
    (experiment / "runs" / "tuned" / "train.log").write_text("log\n", encoding="utf-8", newline="\n")
    monkeypatch.chdir(experiment)

    assert main(["add", ".", "runs", "--volatile", "--run", "run-tuned"]) == 0
    out = capsys.readouterr().out

    assert "Added 1 artifact" in out
    assert "(6 files inside already tracked, left as is)" in out
    entry = artifact(read_doc(experiment), "runs-tuned-train.log")
    assert entry["volatile"] is True and entry["sha256"] is None
    assert main(["add", ".", "runs"]) == 0
    assert "Nothing new to add." in capsys.readouterr().out


def test_add_refusals_write_nothing(experiment, monkeypatch, capsys, tmp_path):
    (experiment / "a.txt").write_bytes(b"a")
    (experiment / "b.txt").write_bytes(b"b")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"x")
    monkeypatch.chdir(experiment)
    before = (experiment / MANIFEST).read_bytes()

    refusals = {
        "already tracked as 'report'": ["REPORT.md"],
        "does not exist": ["nope.txt"],
        "outside the experiment folder": [str(outside)],
        "RunEvidence's own file": [MANIFEST],
        "No run 'run-nope'": ["a.txt", "--run", "run-nope"],
        "ID 'report' is already used": ["a.txt", "--id", "report"],
        "String should match pattern": ["a.txt", "--id", "invalid id"],
        "--id needs exactly one file": ["a.txt", "b.txt", "--id", "pair"],
        "--run or --no-run": ["a.txt", "--run", "run-tuned", "--no-run"],
    }
    for message, extra in refusals.items():
        assert main(["add", ".", *extra]) == 2, message
        assert message in capsys.readouterr().out
    assert (experiment / MANIFEST).read_bytes() == before


def test_add_generated_ids_do_not_collide(experiment, monkeypatch, capsys):
    (experiment / "report").write_bytes(b"a file literally named report")
    monkeypatch.chdir(experiment)

    assert main(["add", ".", "report"]) == 0
    ids = [a["id"] for a in read_doc(experiment)["artifacts"]]
    assert "report-2" in ids
    assert len(ids) == len(set(ids))


def test_rehash_deeply_nested_metrics_does_not_crash(experiment, capsys):
    """Re-reading a locator from deep JSON is a note, not RecursionError."""
    deep = "[" * 20000 + "0" + "]" * 20000
    (experiment / "runs" / "tuned" / "metrics.json").write_text(deep, encoding="utf-8")

    code = main(["rehash", str(experiment), "runs/tuned/metrics.json"])
    out = capsys.readouterr().out

    assert code == 0, out
    document = read_doc(experiment)
    result = next(r for r in document["results"] if r["id"] == "tuned-accuracy")
    assert result["value"] == 1.0
    if DEEP_JSON_PARSES:
        # 3.14 parses the deep document, so the pointer is simply not there.
        assert "could not parse" not in out
        assert "/eval/accuracy is not in runs/tuned/metrics.json any more" in out
    else:
        assert "could not parse" in out
    assert "Traceback" not in out


def test_add_on_a_0_2_manifest_preserves_first_class_fields(experiment, monkeypatch):
    """Absorption at the CLI: a file add keeps conditions, definitions and labels."""
    assert main(["migrate", str(experiment), "--to", "0.2"]) == 0
    doc = read_doc(experiment)
    doc["runs"][0]["conditions"] = {
        "evaluation_split": "test",
        "source_revision": "rev-1",
    }
    doc["results"][0]["metric_definition"] = {"definition_id": "accuracy"}
    doc["artifacts"][0]["dataset_id"] = "dataset-x"
    write_doc(experiment, doc)
    (experiment / "NEW.md").write_text("new\n", encoding="utf-8", newline="\n")
    monkeypatch.chdir(experiment)

    assert main(["add", ".", "NEW.md", "--id", "new"]) == 0

    after = read_doc(experiment)
    assert artifact(after, "new")["path"] == "NEW.md"
    assert after["schema_version"] == "0.2"
    assert after["runs"][0]["conditions"] == {
        "evaluation_split": "test",
        "source_revision": "rev-1",
    }
    assert after["results"][0]["metric_definition"] == {"definition_id": "accuracy"}
    assert artifact(after, doc["artifacts"][0]["id"])["dataset_id"] == "dataset-x"


def test_rehash_on_a_0_2_manifest_preserves_first_class_fields(experiment, monkeypatch):
    """Absorption at the CLI: a real rehash keeps conditions, definitions and labels."""
    assert main(["migrate", str(experiment), "--to", "0.2"]) == 0
    doc = read_doc(experiment)
    doc["runs"][0]["conditions"] = {
        "evaluation_split": "test",
        "source_revision": "rev-1",
    }
    doc["results"][0]["metric_definition"] = {"definition_id": "accuracy"}
    doc["artifacts"][0]["dataset_id"] = "dataset-x"
    write_doc(experiment, doc)
    retrain_tuned(experiment)
    monkeypatch.chdir(experiment)

    assert main(["rehash", ".", "runs/tuned/metrics.json"]) == 0

    after = read_doc(experiment)
    assert after["schema_version"] == "0.2"
    assert next(r for r in after["results"] if r["id"] == "tuned-accuracy")["value"] == 0.75
    assert after["runs"][0]["conditions"] == {
        "evaluation_split": "test",
        "source_revision": "rev-1",
    }
    assert after["results"][0]["metric_definition"] == {"definition_id": "accuracy"}
    assert artifact(after, doc["artifacts"][0]["id"])["dataset_id"] == "dataset-x"
