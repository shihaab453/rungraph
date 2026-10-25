"""Accepting intended changes: volatile files (G2), rehash (G1), add (G5)."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from runevidence.checks import run_checks
from runevidence.cli import main
from runevidence.manifest import load_manifest

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


def test_hash_mismatch_hint_names_the_rehash_command(experiment):
    retrain_tuned(experiment)
    report = run_checks(load_manifest(experiment))
    hint = next(f.hint for f in report.findings if f.code.value == "hash-mismatch")
    assert "runevidence rehash <folder> tuned-" in hint


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
