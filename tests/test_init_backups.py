"""init --force keeps every earlier manifest (F-03).

A single runevidence.json.bak used to be overwritten on every forced init,
so a second run replaced the only copy of a hand-edited manifest with a
generated one. Backups are now generations that are never overwritten or
deleted, and a failed write leaves the existing files as they were.
"""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from runevidence import cli
from runevidence.checks import run_checks
from runevidence.cli import main
from runevidence.manifest import load_manifest

MANIFEST = "runevidence.json"
BAK = MANIFEST + ".bak"


def make_folder(root: Path) -> Path:
    (root / "runs" / "train").mkdir(parents=True)
    (root / "runs" / "train" / "metrics.json").write_bytes(b'{"accuracy": 1.0}\n')
    (root / "data.csv").write_bytes(b"x,y\n1,2\n")
    assert main(["init", str(root)]) == 0
    return root


def hand_edit(root: Path, description: str) -> bytes:
    """Simulate hand-written work that a fresh scan cannot reproduce."""
    path = root / MANIFEST
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["project"]["description"] = description
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path.read_bytes()


def backups(root: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(root.iterdir()) if p.name.startswith(BAK)}


def test_three_forced_inits_keep_three_recoverable_generations(tmp_path, capsys):
    root = make_folder(tmp_path / "proj")
    first = hand_edit(root, "first hand edit")
    assert main(["init", str(root), "--force"]) == 0
    second = hand_edit(root, "second hand edit")
    assert main(["init", str(root), "--force"]) == 0
    third = hand_edit(root, "third hand edit")
    assert main(["init", str(root), "--force"]) == 0
    out = capsys.readouterr().out

    # Before the fix the second run overwrote the .bak holding "first".
    assert backups(root) == {BAK: first, BAK + ".2": second, BAK + ".3": third}
    assert f"Saved the previous manifest to {root / (BAK + '.3')}" in out
    assert json.loads((root / MANIFEST).read_text(encoding="utf-8"))["project"]["description"] is None


def test_backups_are_never_tracked_or_reported_as_untracked(tmp_path, capsys):
    root = make_folder(tmp_path / "proj")
    for n in range(3):
        hand_edit(root, f"edit {n}")
        assert main(["init", str(root), "--force"]) == 0
    assert len(backups(root)) == 3
    (root / "nested").mkdir()
    (root / "nested" / (BAK + ".2")).write_bytes(b"ordinary content\n")
    assert main(["init", str(root), "--force"]) == 0
    capsys.readouterr()

    paths = [a.path for a in load_manifest(root).manifest.artifacts]
    assert not any(p.startswith(BAK) for p in paths)
    assert "nested/" + BAK + ".2" in paths
    report = run_checks(load_manifest(root), include_untracked=True)
    assert report.findings == []
    assert main(["add", str(root), str(root / (BAK + ".2"))]) == 2
    assert "RunEvidence's own file" in capsys.readouterr().out


def test_an_unchanged_manifest_reuses_the_newest_backup(tmp_path, capsys):
    root = make_folder(tmp_path / "proj")
    edited = hand_edit(root, "hand edit")
    assert main(["init", str(root), "--force"]) == 0
    generated = (root / MANIFEST).read_bytes()
    assert main(["init", str(root), "--force"]) == 0
    capsys.readouterr()
    assert backups(root) == {BAK: edited, BAK + ".2": generated}

    # The manifest now equals the newest backup, so no duplicate is written.
    assert main(["init", str(root), "--force"]) == 0
    assert f"already saved in {root / (BAK + '.2')}" in capsys.readouterr().out
    assert backups(root) == {BAK: edited, BAK + ".2": generated}


def test_numbering_continues_after_the_highest_generation(tmp_path, capsys):
    # Gaps left by deleted backups are not refilled, so a higher number is
    # always a newer backup.
    root = make_folder(tmp_path / "proj")
    (root / BAK).write_bytes(b"oldest\n")
    (root / (BAK + ".5")).write_bytes(b"newer\n")
    current = hand_edit(root, "current")

    assert main(["init", str(root), "--force"]) == 0
    capsys.readouterr()

    assert backups(root) == {BAK: b"oldest\n", BAK + ".5": b"newer\n", BAK + ".6": current}


def test_failed_backup_write_changes_nothing(tmp_path, capsys, monkeypatch):
    root = make_folder(tmp_path / "proj")
    first = hand_edit(root, "first")
    assert main(["init", str(root), "--force"]) == 0
    current = hand_edit(root, "current")
    capsys.readouterr()

    def disk_full(fd):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "fsync", disk_full)
    assert main(["init", str(root), "--force"]) == 2
    out = capsys.readouterr().out

    assert "could not back up the existing manifest" in out
    assert "Nothing was written" in out
    assert (root / MANIFEST).read_bytes() == current
    # The partly written generation is removed; the earlier one is untouched.
    assert backups(root) == {BAK: first}


def test_failed_manifest_replace_keeps_the_old_manifest_and_its_backup(tmp_path, capsys, monkeypatch):
    root = make_folder(tmp_path / "proj")
    current = hand_edit(root, "current")
    capsys.readouterr()

    def locked(src, dst):
        raise PermissionError(errno.EACCES, "file is locked", str(dst))

    monkeypatch.setattr(os, "replace", locked)
    assert main(["init", str(root), "--force"]) == 2
    out = capsys.readouterr().out

    assert f"could not write {root / MANIFEST}" in out
    assert "was not replaced" in out
    assert (root / MANIFEST).read_bytes() == current
    assert backups(root) == {BAK: current}
    assert not [p.name for p in root.iterdir() if p.name.endswith(".tmp")]


def test_a_backup_name_taken_meanwhile_is_never_overwritten(tmp_path, capsys, monkeypatch):
    # Even if another process creates the chosen name between choosing it and
    # writing it, the existing file wins and init stops.
    root = make_folder(tmp_path / "proj")
    (root / BAK).write_bytes(b"someone else's backup\n")
    current = hand_edit(root, "current")
    monkeypatch.setattr(cli, "_newest_backup", lambda folder: (0, None))
    capsys.readouterr()

    assert main(["init", str(root), "--force"]) == 2

    assert "could not back up the existing manifest" in capsys.readouterr().out
    assert (root / BAK).read_bytes() == b"someone else's backup\n"
    assert (root / MANIFEST).read_bytes() == current


@pytest.mark.parametrize(
    ("name", "generation"),
    [
        (BAK, 1),
        (BAK + ".2", 2),
        (BAK + ".17", 17),
        (BAK + ".0", None),
        (BAK + ".02", None),
        (BAK + ".x", None),
        (BAK + "2", None),
        (MANIFEST, None),
        ("other.json.bak", None),
    ],
)
def test_backup_generation_names(name, generation):
    from runevidence.models import backup_generation, is_own_file

    assert backup_generation(name) == generation
    assert is_own_file(name) == (generation is not None or name == MANIFEST)


def test_a_failed_first_init_leaves_no_partial_manifest(tmp_path, capsys, monkeypatch):
    from runevidence import manifest as manifest_module

    root = tmp_path / "proj"
    (root / "runs" / "train").mkdir(parents=True)
    (root / "runs" / "train" / "metrics.json").write_bytes(b"{}")

    def partial_write(path, text):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text[:10])
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(manifest_module, "_write", partial_write)
    assert main(["init", str(root)]) == 2

    assert f"could not write {root / MANIFEST}" in capsys.readouterr().out
    assert not (root / MANIFEST).exists()
    assert backups(root) == {}
