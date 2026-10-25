"""RE-02: rehash selector ambiguity fails closed before anything is written.

A bare target is read independently as an artifact ID, a run ID, an exact
path, a folder prefix and a glob. When those readings name different artifact
sets the edit must be refused, and the manifest must stay byte-identical.
Explicit prefixes (``id:``, ``run:``, ``path:``, ``folder:``, ``glob:``)
restrict the reading to one kind. Routine unambiguous selectors keep working.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from runevidence.cli import main
from runevidence.edit import EditError, select_artifacts
from runevidence.manifest import load_manifest

MANIFEST = "runevidence.json"
METRICS = b'{"eval": {"accuracy": 0.5}}\n'

# rel path -> (artifact id, file bytes, run id)
BASE_FILES = {
    "REPORT.md": ("report", b"report body\n", None),
    "README.md": ("README.md", b"readme\n", None),
    "NOTES.md": ("path:report", b"notes\n", None),
    "data/train.csv": ("train-data", b"x,y\n1,2\n", None),
    "logs/app.log": ("app-log", b"app\n", None),
    "logs/db.log": ("db-log", b"db\n", None),
    "runs/a/train.log": ("a-log", b"a log\n", "run-a"),
    "runs/a/metrics.json": ("a-metrics", METRICS, "run-a"),
    "runs/b/train.log": ("b-log", b"b log\n", "run-b"),
}

EXTRA_FILES = {
    # ID "report" also reads as the folder prefix report/.
    "folder": {"report/other.txt": ("report-other", b"other report\n", None)},
    # ID "report" also reads as the exact path of a file literally named report.
    "path": {"report": ("report-file", b"a file literally named report\n", None)},
    # Artifact ID "run-b" also reads as run ID run-b.
    "run-clash": {"notes/run-b.txt": ("run-b", b"clash\n", None)},
    # Same relative path in two locations, reachable from different folders.
    "location": {
        "configs/run.json": ("cfg-top", b'{"top": true}\n', None),
        "nested/configs/run.json": ("cfg-nested", b'{"nested": true}\n', None),
    },
}


def read_doc(root: Path) -> dict:
    return json.loads((root / MANIFEST).read_text(encoding="utf-8"))


def write_doc(root: Path, doc: dict) -> None:
    (root / MANIFEST).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def artifact(doc: dict, art_id: str) -> dict:
    return next(a for a in doc["artifacts"] if a["id"] == art_id)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(root: Path, *extras: str) -> Path:
    """A small synthetic experiment; extras add one ambiguity per case."""
    files = dict(BASE_FILES)
    for extra in extras:
        files.update(EXTRA_FILES[extra])
    root.mkdir(parents=True, exist_ok=True)
    artifacts = []
    for rel, (art_id, data, run_id) in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        artifacts.append(
            {
                "id": art_id,
                "path": rel,
                "sha256": digest(data),
                "size_bytes": len(data),
                "run_id": run_id,
            }
        )
    doc = {
        "schema_version": "0.1",
        "project": {"id": "selector-lab", "name": "Selector test lab"},
        "runs": [{"id": "run-a", "name": "a"}, {"id": "run-b", "name": "b"}],
        "artifacts": artifacts,
        "results": [
            {
                "id": "a-accuracy",
                "run_id": "run-a",
                "name": "test_accuracy",
                "value": 0.5,
                "artifact_id": "a-metrics",
                "locator": "/eval/accuracy",
            }
        ],
    }
    write_doc(root, doc)
    return root


def rerun(root: Path, rel: str) -> bytes:
    """Change a tracked file without changing any measured value."""
    path = root / rel
    original = path.read_bytes()
    if path.suffix.lower() == ".json":
        data = json.dumps({**json.loads(original), "rerun": True}).encode()
    else:
        data = b"rerun " + original
    path.write_bytes(data)
    return data


def selected_ids(root: Path, targets: list[str], cwd: Path | None = None) -> set[str]:
    loaded = load_manifest(root)
    indexes = select_artifacts(loaded, targets, cwd=cwd if cwd is not None else root)
    return {loaded.manifest.artifacts[i].id for i in indexes}


def changed_ids(out: str) -> list[str]:
    return [m.group(1) for line in out.splitlines() if (m := re.match(r"^  (\S+): sha256 ", line))]


# --- ambiguity is refused ----------------------------------------------------


def test_bare_id_vs_folder_prefix_fails_closed(tmp_path):
    root = build(tmp_path, "folder")

    with pytest.raises(EditError):
        selected_ids(root, ["report"])


def test_bare_id_vs_exact_path_fails_closed(tmp_path):
    root = build(tmp_path, "path")

    with pytest.raises(EditError):
        selected_ids(root, ["report"])


def test_bare_run_id_vs_artifact_id_fails_closed(tmp_path):
    root = build(tmp_path, "run-clash")

    with pytest.raises(EditError):
        selected_ids(root, ["run-b"])


def test_identical_sets_from_id_and_path_are_allowed(tmp_path):
    root = build(tmp_path)

    # The same artifact is both its ID and its manifest path.
    assert selected_ids(root, ["README.md"]) == {"README.md"}


@pytest.mark.parametrize("extra", ["folder", "path"])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("targets", [["report"], ["a-log", "report"]])
def test_ambiguous_selector_writes_nothing(tmp_path, capsys, extra, dry_run, targets):
    root = build(tmp_path, extra)
    rerun(root, "REPORT.md")
    rerun(root, "report/other.txt" if extra == "folder" else "report")
    before = (root / MANIFEST).read_bytes()

    argv = ["rehash", str(root), *targets] + (["--dry-run"] if dry_run else [])

    assert main(argv) == 2
    out = capsys.readouterr().out
    assert "Ambiguous target 'report'" in out
    assert "id:ID" in out and "folder:PATH" in out and "path:PATH" in out
    assert "report-other" in out if extra == "folder" else "report-file" in out
    assert "Nothing was written." in out
    assert (root / MANIFEST).read_bytes() == before


# --- explicit prefixes resolve ambiguity -------------------------------------


@pytest.mark.parametrize("extra", ["folder", "path"])
def test_id_prefix_changes_only_the_report_artifact(tmp_path, capsys, extra):
    root = build(tmp_path, extra)
    other_rel = "report/other.txt" if extra == "folder" else "report"
    new_report = rerun(root, "REPORT.md")
    rerun(root, other_rel)
    before = read_doc(root)
    before_bytes = (root / MANIFEST).read_bytes()

    assert main(["rehash", str(root), "id:report", "--dry-run"]) == 0
    dry_out = capsys.readouterr().out
    assert changed_ids(dry_out) == ["report"]
    assert (root / MANIFEST).read_bytes() == before_bytes

    assert main(["rehash", str(root), "id:report"]) == 0
    actual_out = capsys.readouterr().out
    assert changed_ids(actual_out) == changed_ids(dry_out)

    after = read_doc(root)
    expected = copy.deepcopy(before)
    entry = artifact(expected, "report")
    entry["sha256"] = digest(new_report)
    entry["size_bytes"] = len(new_report)
    assert after == expected


def test_run_prefix_and_id_prefix_split_a_run_id_clash(tmp_path):
    root = build(tmp_path, "run-clash")

    assert selected_ids(root, ["run:run-b"]) == {"b-log"}
    assert selected_ids(root, ["id:run-b"]) == {"run-b"}


def test_prefix_in_artifact_id_is_quoted_with_id_prefix(tmp_path):
    root = build(tmp_path)

    assert selected_ids(root, ["id:path:report"]) == {"path:report"}
    # A bare path: prefix is read as a path prefix, not as the artifact ID.
    with pytest.raises(EditError):
        selected_ids(root, ["path:report"])


# --- routine selectors stay compatible ---------------------------------------


@pytest.mark.parametrize(
    "target, expected",
    [
        ("a-log", {"a-log"}),
        ("run-a", {"a-log", "a-metrics"}),
        ("runs/b/train.log", {"b-log"}),
        ("logs", {"app-log", "db-log"}),
        ("logs/", {"app-log", "db-log"}),
        ("logs/*.log", {"app-log", "db-log"}),
        ("id:a-log", {"a-log"}),
        ("run:run-a", {"a-log", "a-metrics"}),
        ("path:runs/b/train.log", {"b-log"}),
        ("folder:logs/", {"app-log", "db-log"}),
        ("glob:logs/*.log", {"app-log", "db-log"}),
    ],
)
def test_bare_and_explicit_selectors_pick_their_artifacts(tmp_path, target, expected):
    root = build(tmp_path)

    assert selected_ids(root, [target]) == expected


def test_bare_and_explicit_paths_resolve_from_the_current_folder(tmp_path):
    root = build(tmp_path)
    cwd = root / "runs" / "b"

    assert selected_ids(root, ["train.log"], cwd=cwd) == {"b-log"}
    assert selected_ids(root, ["path:train.log"], cwd=cwd) == {"b-log"}


# --- manifest and cwd locations may not disagree -----------------------------


@pytest.mark.parametrize("target", ["configs/run.json", "path:configs/run.json"])
def test_bare_and_explicit_paths_reject_differing_location_sets(tmp_path, target):
    root = build(tmp_path, "location")

    assert selected_ids(root, [target]) == {"cfg-top"}
    with pytest.raises(EditError):
        selected_ids(root, [target], cwd=root / "nested")


def test_folder_prefix_rejects_differing_location_sets(tmp_path):
    root = build(tmp_path, "location")

    assert selected_ids(root, ["folder:configs/"], cwd=root) == {"cfg-top"}
    with pytest.raises(EditError):
        selected_ids(root, ["folder:configs/"], cwd=root / "nested")


def test_absolute_path_prefix_resolves_one_location(tmp_path):
    root = build(tmp_path, "location")
    absolute = (root / "nested" / "configs" / "run.json").resolve()

    assert selected_ids(root, [f"path:{absolute}"], cwd=root / "nested") == {"cfg-nested"}


def test_literal_path_vs_glob_requires_explicit_selection(tmp_path):
    root = build(tmp_path)
    literal = root / "[R]EPORT.md"
    data = b"literal brackets\n"
    literal.write_bytes(data)
    doc = read_doc(root)
    doc["artifacts"].append({
        "id": "literal-report", "path": literal.name,
        "sha256": digest(data), "size_bytes": len(data),
    })
    write_doc(root, doc)

    with pytest.raises(EditError, match="Ambiguous target"):
        selected_ids(root, [literal.name])
    assert selected_ids(root, [f"path:{literal.name}"]) == {"literal-report"}
    assert selected_ids(root, [f"glob:{literal.name}"]) == {"report"}


@pytest.mark.parametrize("target", ["id:", "run:", "path:", "folder:", "glob:"])
def test_empty_explicit_selector_is_refused(tmp_path, target):
    root = build(tmp_path)

    with pytest.raises(EditError, match="No artifact matches"):
        selected_ids(root, [target])


# --- dry run selects exactly what the real run selects -----------------------


@pytest.mark.parametrize(
    "targets, expected",
    [
        (["run-a"], {"a-log", "a-metrics"}),
        (["folder:logs/"], {"app-log", "db-log"}),
        (["path:runs/a/train.log"], {"a-log"}),
    ],
)
def test_dry_run_selects_the_same_files_as_the_real_run(tmp_path, capsys, targets, expected):
    root = build(tmp_path)
    doc = read_doc(root)
    for rel in (artifact(doc, art_id)["path"] for art_id in expected):
        rerun(root, rel)
    before = (root / MANIFEST).read_bytes()

    assert main(["rehash", str(root), *targets, "--dry-run"]) == 0
    dry = changed_ids(capsys.readouterr().out)
    assert set(dry) == expected
    assert (root / MANIFEST).read_bytes() == before

    assert main(["rehash", str(root), *targets]) == 0
    actual = changed_ids(capsys.readouterr().out)
    assert set(actual) == set(dry) == expected

    after = read_doc(root)
    for art_id in expected:
        entry = artifact(after, art_id)
        assert entry["sha256"] == digest((root / entry["path"]).read_bytes())
