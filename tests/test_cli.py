"""CLI behaviour: exit codes, text and JSON output, init, schema, adapters."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from runevidence.adapters import BUILTIN
from runevidence.adapters.base import Adapter, Discovery
from runevidence.cli import main
from runevidence.models import Manifest, Project

MANIFEST = "runevidence.json"
BACKUP = MANIFEST + ".bak"


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


def oversized_int_token() -> str:
    """One digit more than this Python will turn into an int, built as text:
    json.dumps refuses to write such a number at all."""
    limit = getattr(sys, "get_int_max_str_digits", lambda: 0)()
    if not limit:
        pytest.skip("this Python has no integer string conversion limit")
    return "9" * (limit + 1)


def test_manifest_number_beyond_the_int_digit_limit_is_unreadable(experiment, capsys):
    doc = read_doc(experiment)
    doc["results"][1]["value"] = "__oversized__"
    text = json.dumps(doc, indent=2).replace('"__oversized__"', oversized_int_token())
    (experiment / MANIFEST).write_text(text + "\n", encoding="utf-8", newline="\n")

    for command in ("validate", "inspect"):
        assert main([command, str(experiment)]) == 2
        out = capsys.readouterr().out
        assert "manifest-unreadable" in out
        assert "INVALID" in out

        assert main([command, str(experiment), "--json"]) == 2
        data = json.loads(capsys.readouterr().out)
        assert data["exit_code"] == 2
        assert [f["code"] for f in data["findings"]] == ["manifest-unreadable"]


def test_metrics_number_beyond_the_int_digit_limit_is_unverifiable(experiment, capsys):
    metrics = ('{"eval": {"accuracy": ' + oversized_int_token() + "}}\n").encode()
    (experiment / "runs" / "tuned" / "metrics.json").write_bytes(metrics)
    doc = read_doc(experiment)
    art = next(a for a in doc["artifacts"] if a["id"] == "tuned-metrics")
    art["sha256"] = hashlib.sha256(metrics).hexdigest()
    art["size_bytes"] = len(metrics)
    write_doc(experiment, doc)

    for command in ("validate", "inspect"):
        assert main([command, str(experiment)]) == 0
        assert "result-unverifiable" in capsys.readouterr().out

        assert main([command, str(experiment), "--json"]) == 0
        data = json.loads(capsys.readouterr().out)
        assert [f["code"] for f in data["findings"]] == ["result-unverifiable"]

        assert main([command, str(experiment), "--json", "--strict"]) == 1
        assert json.loads(capsys.readouterr().out)["exit_code"] == 1


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


def test_init_with_invalid_project_id_reports_and_writes_nothing(tmp_path, capsys):
    folder = build_folder(tmp_path / "proj")
    assert main(["init", str(folder)]) == 0
    capsys.readouterr()
    original = (folder / MANIFEST).read_bytes()

    assert main(["init", str(folder), "--id", "_bad", "--force"]) == 2
    captured = capsys.readouterr()

    assert "produced an invalid manifest" in captured.out
    assert "Traceback" not in captured.out + captured.err
    assert (folder / MANIFEST).read_bytes() == original
    assert not (folder / BACKUP).exists()


class _InvalidManifestAdapter(Adapter):
    name = "invalid-manifest"

    def discover(self, root: Path) -> Discovery:
        return Discovery(root=root)

    def extract(self, root: Path, discovery: Discovery, *, project_id: str, project_name: str) -> Manifest:
        return Manifest(project=Project(id="_bad", name=project_name))


def test_init_adapter_validation_error_reports_and_writes_nothing(tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(BUILTIN, _InvalidManifestAdapter.name, _InvalidManifestAdapter)
    folder = build_folder(tmp_path / "proj")

    assert main(["init", str(folder), "--adapter", "invalid-manifest"]) == 2
    captured = capsys.readouterr()

    assert "produced an invalid manifest" in captured.out
    assert "Traceback" not in captured.out + captured.err
    assert not (folder / MANIFEST).exists()
    assert not (folder / BACKUP).exists()


def test_init_generic_with_root_summary_files_then_validate(tmp_path, capsys):
    folder = build_folder(tmp_path / "proj")
    (folder / "_summary.json").write_text("{}\n", encoding="utf-8")
    (folder / "__init__.py").write_text("\n", encoding="utf-8")

    assert main(["init", str(folder)]) == 0
    capsys.readouterr()

    assert main(["validate", str(folder), "--strict"]) == 0
    ids = {artifact["id"] for artifact in read_doc(folder)["artifacts"]}
    assert "item-_summary.json" in ids
    assert "item-__init__.py" in ids


def test_init_derives_a_valid_project_id_from_a_leading_underscore_name(tmp_path, capsys):
    folder = build_folder(tmp_path / "_proj")

    assert main(["init", str(folder)]) == 0
    capsys.readouterr()

    assert read_doc(folder)["project"]["id"] == "item-_proj"
    assert main(["validate", str(folder)]) == 0


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


def test_help_says_a_clean_result_is_consistency_not_truth(capsys):
    assert main(["--help"]) == 0
    out = " ".join(capsys.readouterr().out.split())  # undo argparse line wrapping

    assert (
        "Exit codes: 0 ok, 1 evidence problems found, 2 could not check (bad manifest or arguments). "
        "A clean result means the folder matches its manifest; it does not show that results "
        "are scientifically true, valid or fresh."
    ) in out


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
