"""Regressions for bugs found by dogfooding on a real scikit-learn run
(examples/dogfood-sklearn, 2026-10-25)."""

import json
import shutil
from pathlib import Path

import pytest

from runevidence.checks import run_checks
from runevidence.cli import main
from runevidence.manifest import ManifestLoadError, load_manifest

from conftest import REPO

DOGFOOD = REPO / "examples" / "dogfood-sklearn"
MANIFEST = "runevidence.json"


@pytest.fixture
def dogfood(tmp_path: Path) -> Path:
    dest = tmp_path / "dogfood"
    shutil.copytree(DOGFOOD, dest)
    return dest


def edit(root: Path, fn) -> None:
    path = root / MANIFEST
    doc = json.loads(path.read_text(encoding="utf-8"))
    fn(doc)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def test_committed_dogfood_example_is_clean():
    report = run_checks(load_manifest(DOGFOOD), include_untracked=True)
    assert report.findings == []


def test_uppercase_digest_from_get_filehash_is_accepted(dogfood):
    def upper(doc):
        doc["artifacts"][0]["sha256"] = doc["artifacts"][0]["sha256"].upper()

    edit(dogfood, upper)
    loaded = load_manifest(dogfood)
    assert loaded.manifest.artifacts[0].sha256.islower()
    assert run_checks(loaded).findings == []


def test_schema_error_names_the_entry_by_id(dogfood):
    def bad(doc):
        doc["artifacts"][2]["sha256"] = "not-a-digest"

    edit(dogfood, bad)
    with pytest.raises(ManifestLoadError) as exc:
        load_manifest(dogfood)
    art_id = json.loads((dogfood / MANIFEST).read_text(encoding="utf-8"))["artifacts"][2]["id"]
    assert f"artifacts[2] (id {art_id!r}).sha256" in exc.value.findings[0].message


def test_same_name_file_from_another_run_is_not_suggested_as_moved(dogfood):
    # Every run has a predictions.csv. Deleting one must not point at the
    # other run's (different) file.
    (dogfood / "runs" / "forest" / "predictions.csv").unlink()
    [missing] = [f for f in run_checks(load_manifest(dogfood)).findings if f.code.value == "missing-file"]
    assert missing.hint is None


def test_identical_file_elsewhere_is_suggested_as_moved(dogfood):
    src = dogfood / "runs" / "forest" / "predictions.csv"
    (dogfood / "archive").mkdir()
    src.rename(dogfood / "archive" / "predictions.csv")
    [missing] = [f for f in run_checks(load_manifest(dogfood)).findings if f.code.value == "missing-file"]
    assert "identical file" in missing.hint
    assert "archive/predictions.csv" in missing.hint


def test_init_force_backs_up_hand_written_annotations(dogfood, capsys):
    before = (dogfood / MANIFEST).read_bytes()
    assert main(["init", str(dogfood), "--force"]) == 0
    out = capsys.readouterr().out
    assert (dogfood / "runevidence.json.bak").read_bytes() == before
    assert "4 results, 2 claims, 6 relationships" in out

    # The backup is neither tracked by a later init nor reported as untracked.
    assert main(["init", str(dogfood), "--force"]) == 0
    paths = [a["path"] for a in json.loads((dogfood / MANIFEST).read_text(encoding="utf-8"))["artifacts"]]
    assert "runevidence.json.bak" not in paths
    report = run_checks(load_manifest(dogfood), include_untracked=True)
    assert all(f.path != "runevidence.json.bak" for f in report.findings)


def test_init_project_id_is_lowercase(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    assert main(["init", str(tmp_path), "--name", "My Big Experiment"]) == 0
    doc = json.loads((tmp_path / MANIFEST).read_text(encoding="utf-8"))
    assert doc["project"]["id"] == "my-big-experiment"
