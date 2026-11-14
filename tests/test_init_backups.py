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
import sys
from pathlib import Path

import pytest

from runevidence import cli
from runevidence.checks import run_checks
from runevidence.cli import main
from runevidence.manifest import load_manifest

MANIFEST = "runevidence.json"
BAK = MANIFEST + ".bak"

WARNING_TEXT = (
    "WARNING: the existing manifest declares schema 0.2. Regeneration writes schema 0.1\n"
    "and does not carry over first-class conditions, dataset_id or metric_definition.\n"
    "The backup keeps the previous manifest."
)
WARNING_PREFIX = "WARNING: the existing manifest declares schema 0.2."
ZERO_TWO_CANARIES = (
    "canary-project-id",
    "CANARY-PROJECT-VALUE",
    "CANARY-PROJECT-DESCRIPTION",
    "CANARY-TAG",
    "CANARY-METADATA-VALUE",
    "canary-run-id",
    "CANARY-RUN-NAME",
    "CANARY-RUN-METADATA",
    "CANARY-SPLIT",
    "CANARY-REVISION",
    "CANARY-ENV-VALUE",
    "canary-artifact-id",
    "canary-data.csv",
    "ab" * 32,
    "CANARY-DATASET-ID",
    "CANARY-ARTIFACT-METADATA",
    "canary-result-id",
    "CANARY-METRIC-NAME",
    "CANARY-DEFINITION-ID",
    "CANARY-VERSION",
    "canary-evidence-id",
    "CANARY-CLAIM",
)


def write_schema_0_2(root: Path, *, empty: bool = False) -> bytes:
    """Replace the generated manifest with a valid 0.2 document."""
    if empty:
        doc = {
            "schema_version": "0.2",
            "project": {"id": "canary-project-id", "name": "CANARY-PROJECT-VALUE"},
        }
    else:
        doc = {
            "schema_version": "0.2",
            "project": {
                "id": "canary-project-id",
                "name": "CANARY-PROJECT-VALUE",
                "description": "CANARY-PROJECT-DESCRIPTION",
                "tags": ["CANARY-TAG"],
                "metadata": {"canary": "CANARY-METADATA-VALUE"},
            },
            "runs": [
                {
                    "id": "canary-run-id",
                    "name": "CANARY-RUN-NAME",
                    "params": {"canary_param": 987654.321},
                    "metadata": {"canary_run": "CANARY-RUN-METADATA"},
                    "conditions": {
                        "evaluation_split": "CANARY-SPLIT",
                        "source_revision": "CANARY-REVISION",
                        "environment": {"canary_env": "CANARY-ENV-VALUE"},
                    },
                }
            ],
            "artifacts": [
                {
                    "id": "canary-artifact-id",
                    "path": "canary-data.csv",
                    "sha256": "ab" * 32,
                    "size_bytes": 424242,
                    "dataset_id": "CANARY-DATASET-ID",
                    "metadata": {"canary_art": "CANARY-ARTIFACT-METADATA"},
                }
            ],
            "results": [
                {
                    "id": "canary-result-id",
                    "run_id": "canary-run-id",
                    "name": "CANARY-METRIC-NAME",
                    "value": 987654.321,
                    "metric_definition": {
                        "definition_id": "CANARY-DEFINITION-ID",
                        "higher_is_better": True,
                        "version": "CANARY-VERSION",
                    },
                }
            ],
            "evidence": [{"id": "canary-evidence-id", "claim": "CANARY-CLAIM"}],
        }
    path = root / MANIFEST
    path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path.read_bytes()


def warning_block(out: str) -> str:
    start = out.index(WARNING_PREFIX)
    end = out.index("The backup keeps the previous manifest.", start)
    return out[start:end + len("The backup keeps the previous manifest.")]



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


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the S4-2a admission lock is Linux-only (no Windows parity claim)",
)
def test_force_init_refuses_a_busy_lock_before_backup_or_save(tmp_path, capsys):
    from runevidence import directory_publication as dp

    root = make_folder(tmp_path / "proj")
    current = hand_edit(root, "current")
    capsys.readouterr()

    with dp.builtin_writer_admission_lock(root):
        assert main(["init", str(root), "--force"]) == 2

    out = capsys.readouterr().out
    assert out == (
        "ERROR (BUSY): another RunEvidence writer is updating the manifest. "
        "Nothing was written.\n"
    )
    assert (root / MANIFEST).read_bytes() == current
    assert backups(root) == {}

    # The same invocation succeeds once the cooperating writer releases it.
    assert main(["init", str(root), "--force"]) == 0
    assert backups(root) == {BAK: current}


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


def test_forced_init_over_0_2_refuses_and_writes_nothing(tmp_path, capsys):
    root = make_folder(tmp_path / "proj")
    old_0_2 = write_schema_0_2(root)

    assert main(["init", str(root), "--force"]) == 2
    out = capsys.readouterr().out

    assert out.endswith(cli.INIT_0_2_REFUSAL + "\n")
    assert (root / MANIFEST).read_bytes() == old_0_2
    assert backups(root) == {}
    for canary in ZERO_TWO_CANARIES:
        assert canary not in out


def test_forced_init_over_0_3_refuses_and_writes_nothing(tmp_path, capsys):
    root = make_folder(tmp_path / "proj")
    path = root / MANIFEST
    doc = {
        "schema_version": "0.3",
        "project": {"id": "CANARY-PROJECT-ID", "name": "CANARY-PROJECT-NAME"},
        "artifacts": [
            {
                "id": "canary-artifact-id",
                "path": "canary-data.jsonl",
                "sha256": "ab" * 32,
                "size_bytes": 424242,
            }
        ],
        "results": [
            {
                "id": "canary-result-id",
                "run_id": "canary-run-id",
                "name": "CANARY-METRIC-NAME",
                "artifact_id": "canary-artifact-id",
                "locator": {"kind": "jsonl", "line": 7, "pointer": "/CANARY-POINTER"},
            }
        ],
    }
    path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    old_0_3 = path.read_bytes()

    assert main(["init", str(root), "--force"]) == 2
    out = capsys.readouterr().out

    assert out.endswith(cli.INIT_0_3_REFUSAL + "\n")
    assert path.read_bytes() == old_0_3
    assert backups(root) == {}
    for canary in ("CANARY-PROJECT-ID", "CANARY-POINTER", "CANARY-METRIC-NAME"):
        assert canary not in out


def test_forced_init_over_0_4_refuses_and_writes_nothing(tmp_path, capsys):
    root = make_folder(tmp_path / "proj")
    path = root / MANIFEST
    doc = {
        "schema_version": "0.4",
        "project": {"id": "CANARY-PROJECT-ID", "name": "CANARY-PROJECT-NAME"},
        "artifacts": [
            {
                "id": "canary-artifact-id",
                "path": "canary-data.csv",
                "sha256": "ab" * 32,
                "size_bytes": 424242,
            }
        ],
        "results": [
            {
                "id": "canary-result-id",
                "run_id": "canary-run-id",
                "name": "CANARY-METRIC-NAME",
                "artifact_id": "canary-artifact-id",
                "locator": {
                    "kind": "csv-cell",
                    "row": 41,
                    "column": 4,
                    "header": "CANARY-HEADER",
                },
            }
        ],
    }
    path.write_text(
        json.dumps(doc, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    old_0_4 = path.read_bytes()

    assert main(["init", str(root), "--force"]) == 2
    out = capsys.readouterr().out

    assert out.endswith(cli.INIT_0_4_REFUSAL + "\n")
    assert path.read_bytes() == old_0_4
    assert backups(root) == {}
    for canary in ("CANARY-PROJECT-ID", "CANARY-HEADER", "CANARY-METRIC-NAME"):
        assert canary not in out


def test_init_0_4_refusal_is_the_exact_rfc_text():
    assert cli.INIT_0_4_REFUSAL == (
        "ERROR: the existing manifest declares schema 0.4. init --force would regenerate it as "
        "schema 0.1 and cannot carry over first-class values or locators. Nothing was written. "
        "Move the manifest aside, run init, then runevidence migrate --to 0.4 and reapply the "
        "first-class values and locators."
    )


def test_forced_init_over_empty_0_2_refuses_the_downgrade(tmp_path, capsys):
    root = make_folder(tmp_path / "proj")
    old_0_2 = write_schema_0_2(root, empty=True)

    assert main(["init", str(root), "--force"]) == 2
    out = capsys.readouterr().out

    assert out.endswith(cli.INIT_0_2_REFUSAL + "\n")
    assert (root / MANIFEST).read_bytes() == old_0_2
    assert backups(root) == {}


def test_forced_init_refuses_an_invalid_0_2_declaration(tmp_path, capsys):
    root = make_folder(tmp_path / "proj")
    invalid = b'{"schema_version": "0.2", "project": {}}\n'
    (root / MANIFEST).write_bytes(invalid)

    assert main(["init", str(root), "--force"]) == 2
    out = capsys.readouterr().out

    assert out.endswith(cli.INIT_0_2_REFUSAL + "\n")
    assert (root / MANIFEST).read_bytes() == invalid
    assert backups(root) == {}


def test_0_2_refusal_happens_before_backup(tmp_path, capsys, monkeypatch):
    root = make_folder(tmp_path / "proj")
    old_0_2 = write_schema_0_2(root)

    def forbidden(*args, **kwargs):
        raise AssertionError("backup must not run for a declared 0.2 refusal")

    monkeypatch.setattr(cli, "_backup_existing", forbidden)
    assert main(["init", str(root), "--force"]) == 2
    assert (root / MANIFEST).read_bytes() == old_0_2
    assert backups(root) == {}


def test_forced_init_over_0_1_prints_no_0_2_warning(tmp_path, capsys):
    root = make_folder(tmp_path / "proj")
    hand_edit(root, "hand edit")

    assert main(["init", str(root), "--force"]) == 0
    out = capsys.readouterr().out

    assert "declares schema 0.2" not in out
    assert WARNING_PREFIX not in out
    assert json.loads((root / MANIFEST).read_text(encoding="utf-8"))["schema_version"] == "0.1"


def test_unparseable_old_manifest_is_backed_up_and_regenerated(tmp_path, capsys):
    root = make_folder(tmp_path / "proj")
    unreadable = b"{not a manifest\n"
    (root / MANIFEST).write_bytes(unreadable)

    assert main(["init", str(root), "--force"]) == 0
    out = capsys.readouterr().out

    assert backups(root) == {BAK: unreadable}
    assert WARNING_PREFIX not in out
    assert json.loads((root / MANIFEST).read_text(encoding="utf-8"))["schema_version"] == "0.1"


def test_backup_failure_over_0_1_keeps_manifest(tmp_path, capsys, monkeypatch):
    root = make_folder(tmp_path / "proj")
    current = hand_edit(root, "hand edit")

    def disk_full(fd):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "fsync", disk_full)
    assert main(["init", str(root), "--force"]) == 2
    out = capsys.readouterr().out

    assert "could not back up the existing manifest" in out
    assert (root / MANIFEST).read_bytes() == current
    assert backups(root) == {}


def symlink_or_skip(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symbolic links unavailable: {exc.__class__.__name__}")


def hardlink_or_skip(link: Path, target: Path) -> None:
    try:
        os.link(target, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"hard links unavailable: {exc.__class__.__name__}")


def test_symlinked_backup_is_not_reused_and_old_bytes_survive(tmp_path, capsys):
    root = make_folder(tmp_path / "proj")
    old = hand_edit(root, "hand edit")
    symlink_or_skip(root / BAK, Path(MANIFEST))
    assert (root / BAK).is_symlink()

    assert main(["init", str(root), "--force"]) == 0
    out = capsys.readouterr().out

    assert (root / BAK).is_symlink()
    assert os.readlink(root / BAK) == MANIFEST
    assert (root / (BAK + ".2")).read_bytes() == old
    assert backups(root)[BAK + ".2"] == old
    assert WARNING_PREFIX not in out


def test_hardlinked_backup_alias_is_not_reused_and_old_bytes_survive(
    tmp_path, capsys
):
    root = make_folder(tmp_path / "proj")
    old = hand_edit(root, "hand edit")
    hardlink_or_skip(root / BAK, root / MANIFEST)
    assert os.path.samefile(root / BAK, root / MANIFEST)

    assert main(["init", str(root), "--force"]) == 0
    out = capsys.readouterr().out

    assert (root / (BAK + ".2")).read_bytes() == old
    assert backups(root)[BAK + ".2"] == old
    assert WARNING_PREFIX not in out


def test_directory_backup_name_falls_through_to_the_next_generation(tmp_path, capsys):
    root = make_folder(tmp_path / "proj")
    old = hand_edit(root, "hand edit")
    (root / BAK).mkdir()

    assert main(["init", str(root), "--force"]) == 0
    out = capsys.readouterr().out

    assert (root / BAK).is_dir()
    assert (root / (BAK + ".2")).read_bytes() == old
    assert WARNING_PREFIX not in out


def test_regular_byte_identical_backup_is_still_reused(tmp_path, capsys):
    root = make_folder(tmp_path / "proj")
    old = hand_edit(root, "hand edit")
    (root / BAK).write_bytes(old)

    assert main(["init", str(root), "--force"]) == 0
    out = capsys.readouterr().out

    assert f"The previous manifest is already saved in {root / BAK}" in out
    assert backups(root) == {BAK: old}
    assert not (root / (BAK + ".2")).exists()


def test_regular_backup_with_different_bytes_creates_the_next_generation(
    tmp_path, capsys
):
    root = make_folder(tmp_path / "proj")
    old = hand_edit(root, "hand edit")
    (root / BAK).write_bytes(b"an older, different backup\n")

    assert main(["init", str(root), "--force"]) == 0
    out = capsys.readouterr().out

    assert (root / BAK).read_bytes() == b"an older, different backup\n"
    assert (root / (BAK + ".2")).read_bytes() == old
    assert backups(root)[BAK + ".2"] == old


def test_symlinked_backup_to_identical_other_file_is_not_reused(tmp_path, capsys):
    root = make_folder(tmp_path / "proj")
    old = hand_edit(root, "hand edit")
    other = root / "identical-copy.json"
    other.write_bytes(old)
    symlink_or_skip(root / BAK, Path(other.name))
    assert (root / BAK).is_symlink()

    assert main(["init", str(root), "--force"]) == 0
    out = capsys.readouterr().out

    assert (root / BAK).is_symlink()
    assert os.readlink(root / BAK) == other.name
    assert other.read_bytes() == old
    assert (root / (BAK + ".2")).read_bytes() == old
    assert backups(root)[BAK + ".2"] == old


def test_identity_unavailable_never_reuses_a_backup(tmp_path, capsys, monkeypatch):
    root = make_folder(tmp_path / "proj")
    old = hand_edit(root, "hand edit")
    (root / BAK).write_bytes(old)
    monkeypatch.setattr(cli, "_same_file", lambda *args, **kwargs: None)

    assert main(["init", str(root), "--force"]) == 0
    out = capsys.readouterr().out

    assert "already saved in" not in out
    assert (root / BAK).read_bytes() == old
    assert (root / (BAK + ".2")).read_bytes() == old
    assert backups(root) == {BAK: old, BAK + ".2": old}
