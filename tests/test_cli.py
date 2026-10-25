"""CLI behaviour: exit codes, text and JSON output, init, schema, adapters."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from runevidence.cli import main

MANIFEST = "runevidence.json"


def read_doc(root: Path) -> dict:
    return json.loads((root / MANIFEST).read_text(encoding="utf-8"))


def write_doc(root: Path, doc: dict) -> None:
    (root / MANIFEST).write_text(
        json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n"
    )


def make_warning_only(experiment: Path) -> None:
    """A manifest whose only finding is the no-digest warning."""
    doc = read_doc(experiment)
    doc["artifacts"][0]["sha256"] = None
    write_doc(experiment, doc)


def build_folder(root: Path) -> Path:
    (root / "data").mkdir(parents=True)
    (root / "data" / "train.csv").write_bytes(b"text,label\nhello,1\n")
    (root / "runs" / "train").mkdir(parents=True)
    (root / "runs" / "train" / "metrics.json").write_bytes(b'{"accuracy": 1.0}\n')
    return root


def test_validate_clean_experiment_exits_zero(experiment, capsys):
    exit_code = main(["validate", str(experiment)])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "OK:" in out


def test_validate_json_has_the_machine_readable_keys(experiment, capsys):
    exit_code = main(["validate", str(experiment), "--json"])
    out = capsys.readouterr().out

    data = json.loads(out)
    assert exit_code == 0
    assert data["ok"] is True
    assert data["exit_code"] == 0
    assert set(data["counts"]) == {"error", "warning", "info"}
    assert data["findings"] == []
    assert data["manifest"].endswith(MANIFEST)


def test_strict_turns_a_warning_into_exit_one(experiment, capsys):
    make_warning_only(experiment)

    assert main(["validate", str(experiment)]) == 0
    capsys.readouterr()
    strict_code = main(["validate", str(experiment), "--strict"])
    out = capsys.readouterr().out

    assert strict_code == 1
    assert "no-digest" in out
    assert "FAIL:" in out


def test_inspect_prints_the_project_and_untracked_files(experiment, capsys):
    (experiment / "scratch.txt").write_text("scratch\n", encoding="utf-8")

    exit_code = main(["inspect", str(experiment)])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "Project:" in out
    assert "untracked-file" in out
    assert "scratch.txt" in out


def test_validate_missing_manifest_exits_two_in_text_and_json(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()

    assert main(["validate", str(empty)]) == 2
    text_out = capsys.readouterr().out
    assert "manifest-not-found" in text_out
    assert "INVALID" in text_out

    assert main(["validate", str(empty), "--json"]) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["exit_code"] == 2
    assert data["ok"] is False
    assert data["findings"][0]["code"] == "manifest-not-found"


def test_inspect_missing_manifest_exits_two_in_text_and_json(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()

    assert main(["inspect", str(empty)]) == 2
    assert "INVALID" in capsys.readouterr().out

    assert main(["inspect", str(empty), "--json"]) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["exit_code"] == 2


def test_init_writes_a_manifest_that_validates(tmp_path, capsys):
    folder = build_folder(tmp_path / "proj")

    exit_code = main(["init", str(folder)])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert (folder / MANIFEST).is_file()
    assert "Wrote" in out
    assert main(["validate", str(folder)]) == 0


def test_init_refuses_to_overwrite_without_force(tmp_path, capsys):
    folder = build_folder(tmp_path / "proj")
    assert main(["init", str(folder)]) == 0
    capsys.readouterr()

    assert main(["init", str(folder)]) == 2
    assert "already exists" in capsys.readouterr().out

    assert main(["init", str(folder), "--force"]) == 0


def test_init_with_unknown_adapter_exits_two(tmp_path, capsys):
    folder = build_folder(tmp_path / "proj")

    assert main(["init", str(folder), "--adapter", "nope"]) == 2
    assert "Unknown adapter" in capsys.readouterr().out
    assert not (folder / MANIFEST).exists()


def test_schema_prints_json_with_schema_key(capsys):
    exit_code = main(["schema"])
    out = capsys.readouterr().out

    data = json.loads(out)
    assert exit_code == 0
    assert "$schema" in data
    assert data["title"] == "Manifest"


def test_adapters_lists_the_builtin_generic_adapter(capsys):
    exit_code = main(["adapters"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "generic" in out


def test_unknown_subcommand_exits_two(capsys):
    assert main(["bogus"]) == 2
    capsys.readouterr()


def test_module_entry_point_reports_the_version(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "runevidence", "--version"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )

    assert result.returncode == 0
    assert "runevidence" in result.stdout
