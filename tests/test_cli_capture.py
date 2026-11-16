"""Source/environment capture."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from runevidence.cli import main


def git(folder: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(folder), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def make_repo(root: Path, version: str = "0.2") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "data.txt").write_text("evidence\n", encoding="utf-8")
    document = {
        "schema_version": version,
        "project": {"id": "p", "name": "p"},
        "runs": [{"id": "run"}],
    }
    (root / "runevidence.json").write_text(json.dumps(document), encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")
    return root


def run_capture(root: Path, capsys, *extra: str) -> tuple[int, str]:
    code = main(["capture", str(root), "--run", "run", "--json", *extra])
    return code, capsys.readouterr().out


def test_capture_records_head_and_environment(tmp_path: Path, capsys):
    root = make_repo(tmp_path)
    head = git(root, "rev-parse", "HEAD")
    code, out = run_capture(root, capsys)
    assert code == 0
    data = json.loads(out)
    assert data["source_revision"] == head
    assert data["environment"]["git_dirty"] is False
    document = json.loads((root / "runevidence.json").read_text(encoding="utf-8"))
    conditions = document["runs"][0]["conditions"]
    assert conditions["source_revision"] == head
    assert conditions["environment"]["git_dirty"] is False


def test_capture_marks_a_dirty_tree(tmp_path: Path, capsys):
    root = make_repo(tmp_path)
    (root / "data.txt").write_text("changed\n", encoding="utf-8")
    code, out = run_capture(root, capsys)
    assert code == 0
    assert json.loads(out)["environment"]["git_dirty"] is True


def test_capture_refuses_schema_0_1(tmp_path: Path, capsys):
    root = make_repo(tmp_path, version="0.1")
    code, out = run_capture(root, capsys)
    assert code == 2
    assert "schema 0.2" in out
    assert json.loads((root / "runevidence.json").read_text())["runs"][0].get("conditions") is None


def test_capture_refuses_a_non_git_folder(tmp_path: Path, capsys):
    root = tmp_path / "plain"
    root.mkdir()
    (root / "runevidence.json").write_text(
        json.dumps({"schema_version": "0.2", "project": {"id": "p", "name": "p"}, "runs": [{"id": "run"}]}),
        encoding="utf-8",
    )
    code, out = run_capture(root, capsys)
    assert code == 2
    assert "not a git repository" in out
    assert "conditions" not in json.loads((root / "runevidence.json").read_text())["runs"][0]


def test_capture_dry_run_writes_nothing(tmp_path: Path, capsys):
    root = make_repo(tmp_path)
    before = (root / "runevidence.json").read_bytes()
    code, out = run_capture(root, capsys, "--dry-run")
    assert code == 0
    assert (root / "runevidence.json").read_bytes() == before
