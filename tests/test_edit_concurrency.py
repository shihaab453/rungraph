"""Deterministic interleavings for edits of one manifest snapshot."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

import runevidence.cli as cli
import runevidence.manifest as manifest_io
from runevidence.edit import plan_rehash, select_artifacts
from runevidence.hashing import sha256_file
from runevidence.manifest import load_manifest

MANIFEST = "runevidence.json"


def read_doc(root):
    return json.loads((root / MANIFEST).read_text(encoding="utf-8"))


def write_doc(root, doc):
    path = root / MANIFEST
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path.read_bytes()


def prepare_edit(root, command):
    if command == "rehash":
        (root / "REPORT.md").write_bytes(b"intended report revision\n")
        return ["rehash", str(root), "report"]
    new_file = root / "new.txt"
    new_file.write_bytes(b"new evidence\n")
    return ["add", str(root), str(new_file)]


def assert_rejected(root, expected, capsys):
    out = capsys.readouterr().out
    assert "manifest changed" in out.lower()
    assert "Nothing was written." in out
    assert "Wrote " not in out
    assert (root / MANIFEST).read_bytes() == expected
    assert not list(root.glob(".runevidence.json.*.tmp"))
    load_manifest(root)  # The competing writer's valid document is intact.


@pytest.mark.parametrize("command", ["rehash", "add"])
def test_edit_rejects_reorder_after_validated_load(experiment, monkeypatch, capsys, command):
    args = prepare_edit(experiment, command)
    original_load = cli._load_or_report
    competing_bytes = None

    def load_then_reorder(*args, **kwargs):
        nonlocal competing_bytes
        loaded = original_load(*args, **kwargs)
        doc = read_doc(experiment)
        doc["artifacts"].reverse()
        competing_bytes = write_doc(experiment, doc)
        return loaded

    monkeypatch.setattr(cli, "_load_or_report", load_then_reorder)
    assert cli.main(args) == 2
    assert_rejected(experiment, competing_bytes, capsys)


def test_rehash_rejects_manifest_reorder_after_selection(experiment, monkeypatch, capsys):
    args = prepare_edit(experiment, "rehash")
    original_select = cli.select_artifacts
    competing_bytes = None

    def select_then_reorder(*args, **kwargs):
        nonlocal competing_bytes
        indexes = original_select(*args, **kwargs)
        doc = read_doc(experiment)
        doc["artifacts"].reverse()
        competing_bytes = write_doc(experiment, doc)
        return indexes

    monkeypatch.setattr(cli, "select_artifacts", select_then_reorder)
    assert cli.main(args) == 2
    assert_rejected(experiment, competing_bytes, capsys)


@pytest.mark.parametrize("command", ["rehash", "add"])
@pytest.mark.parametrize("existing_note", [False, True], ids=["metadata-added", "metadata-changed"])
def test_edit_rejects_competing_metadata_after_planning(
    experiment, monkeypatch, capsys, command, existing_note
):
    if existing_note:
        doc = read_doc(experiment)
        doc["project"]["metadata"] = {"note": "original"}
        write_doc(experiment, doc)
    args = prepare_edit(experiment, command)
    planner_name = "plan_rehash" if command == "rehash" else "plan_add"
    original_plan = getattr(cli, planner_name)
    competing_bytes = None

    def plan_then_edit(*args, **kwargs):
        nonlocal competing_bytes
        plan = original_plan(*args, **kwargs)
        doc = read_doc(experiment)
        doc["project"]["metadata"] = {"note": "competing writer"}
        competing_bytes = write_doc(experiment, doc)
        return plan

    monkeypatch.setattr(cli, planner_name, plan_then_edit)
    assert cli.main(args) == 2
    assert_rejected(experiment, competing_bytes, capsys)


@pytest.mark.parametrize("command", ["rehash", "add"])
def test_edit_checks_for_changes_after_staging(experiment, monkeypatch, capsys, command):
    args = prepare_edit(experiment, command)
    original_fsync = manifest_io.os.fsync
    competing_bytes = None

    def sync_then_edit(fd):
        nonlocal competing_bytes
        original_fsync(fd)
        doc = read_doc(experiment)
        doc["project"]["metadata"] = {"note": "changed during staging"}
        competing_bytes = write_doc(experiment, doc)

    monkeypatch.setattr(manifest_io.os, "fsync", sync_then_edit)
    assert cli.main(args) == 2
    assert_rejected(experiment, competing_bytes, capsys)


@pytest.mark.parametrize("replacement", [b"{incomplete", None], ids=["invalid-json", "removed"])
def test_rehash_rejects_unreadable_competing_manifest(experiment, monkeypatch, capsys, replacement):
    args = prepare_edit(experiment, "rehash")
    original_plan = cli.plan_rehash
    path = experiment / MANIFEST

    def plan_then_replace(*args, **kwargs):
        plan = original_plan(*args, **kwargs)
        if replacement is None:
            path.unlink()
        else:
            path.write_bytes(replacement)
        return plan

    monkeypatch.setattr(cli, "plan_rehash", plan_then_replace)
    assert cli.main(args) == 2
    assert "Nothing was written." in capsys.readouterr().out
    assert (path.read_bytes() if path.exists() else None) == replacement
    assert not list(experiment.glob(".runevidence.json.*.tmp"))


def test_edit_rejects_byte_changes_even_if_json_is_identical(experiment, monkeypatch, capsys):
    args = prepare_edit(experiment, "rehash")
    original_plan = cli.plan_rehash
    path = experiment / MANIFEST
    competing_bytes = path.read_bytes().replace(b"\n", b"\r\n")

    def plan_then_reformat(*args, **kwargs):
        plan = original_plan(*args, **kwargs)
        path.write_bytes(competing_bytes)
        return plan

    monkeypatch.setattr(cli, "plan_rehash", plan_then_reformat)
    assert cli.main(args) == 2
    assert_rejected(experiment, competing_bytes, capsys)


def test_rehash_binds_artifact_edits_to_id_after_raw_reorder(experiment):
    loaded = load_manifest(experiment)
    indexes = select_artifacts(loaded, ["report"])
    raw = read_doc(experiment)
    raw["artifacts"].reverse()
    before = deepcopy(raw)
    (experiment / "REPORT.md").write_bytes(b"revised report\n")

    plan_rehash(loaded, raw, indexes)

    report = next(a for a in raw["artifacts"] if a["id"] == "report")
    assert report["sha256"] == sha256_file(experiment / "REPORT.md")
    for old, new in zip(before["artifacts"], raw["artifacts"]):
        if old["id"] != "report":
            assert new == old


def test_rehash_binds_result_edits_to_id_after_raw_reorder(experiment):
    loaded = load_manifest(experiment)
    indexes = select_artifacts(loaded, ["tuned-metrics"])
    raw = read_doc(experiment)
    raw["results"].reverse()
    before = deepcopy(raw)
    metrics = experiment / "runs" / "tuned" / "metrics.json"
    metrics.write_bytes(b'{"eval": {"accuracy": 0.75, "f1": 0.7}}\n')

    plan_rehash(loaded, raw, indexes)

    result = next(r for r in raw["results"] if r["id"] == "tuned-accuracy")
    assert result["value"] == 0.75
    for old, new in zip(before["results"], raw["results"]):
        if old["id"] != "tuned-accuracy":
            assert new == old
