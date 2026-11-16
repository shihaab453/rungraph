"""Workspace authoring and baseline diffs."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from runevidence.rungraph_cli import main


def make_root(root: Path) -> Path:
    manifest = {
        "schema_version": "0.1",
        "project": {"id": "p", "name": "p"},
        "runs": [{"id": "r"}],
        "artifacts": [
            {"id": "data", "path": "data.csv", "sha256": "0" * 64, "size_bytes": 1}
        ],
        "results": [{"id": "acc", "run_id": "r", "name": "accuracy", "value": 0.9}],
        "evidence": [
            {"id": "claim", "claim": "data is intact", "supports": ["data"], "contradicts": []}
        ],
    }
    for name in ("a", "b"):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "runevidence.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def init(root: Path) -> None:
    assert main(["init", str(root), "--id", "study", "--name", "Study"]) == 0


def sha(root: Path) -> str:
    return hashlib.sha256((root / "rungraph.json").read_bytes()).hexdigest()


def doc(root: Path) -> dict:
    return json.loads((root / "rungraph.json").read_text(encoding="utf-8"))


def test_member_add_and_remove(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    init(root)
    capsys.readouterr()
    (root / "c").mkdir()
    (root / "c" / "runevidence.json").write_text(
        (root / "a" / "runevidence.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    assert main(["member", "add", str(root), "--id", "c", "--path", "c", "--expected-sha256", sha(root)]) == 0
    assert [m["id"] for m in doc(root)["members"]] == ["prov-a", "prov-b", "c"]
    assert main(["member", "remove", str(root), "--id", "c", "--expected-sha256", sha(root)]) == 0
    assert [m["id"] for m in doc(root)["members"]] == ["prov-a", "prov-b"]


def test_claim_add_and_edit(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    init(root)
    capsys.readouterr()
    claim = {
        "id": "w1",
        "claim": "a's data is intact",
        "supports": [{"member": "prov-a", "id": "data"}],
        "contradicts": [],
    }
    claim_file = tmp_path / "claim.json"
    claim_file.write_text(json.dumps(claim), encoding="utf-8")
    assert main(["claim", "add", str(root), "--file", str(claim_file), "--expected-sha256", sha(root)]) == 0
    assert doc(root)["claims"][0]["id"] == "w1"
    claim["claim"] = "edited"
    claim_file.write_text(json.dumps(claim), encoding="utf-8")
    assert main(["claim", "edit", str(root), "--file", str(claim_file), "--expected-sha256", sha(root)]) == 0
    assert doc(root)["claims"][0]["claim"] == "edited"


def test_a_stale_sha_writes_nothing(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    init(root)
    capsys.readouterr()
    before = (root / "rungraph.json").read_bytes()
    assert main(["member", "remove", str(root), "--id", "prov-b", "--expected-sha256", "0" * 64, "--json"]) == 2
    assert (root / "rungraph.json").read_bytes() == before


def test_dry_run_writes_nothing(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    init(root)
    capsys.readouterr()
    before = (root / "rungraph.json").read_bytes()
    assert main(["member", "remove", str(root), "--id", "prov-b", "--expected-sha256", sha(root), "--dry-run"]) == 0
    assert (root / "rungraph.json").read_bytes() == before


def test_patch_applies_several_operations(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    init(root)
    capsys.readouterr()
    patch = {
        "format": "rungraph-patch",
        "version": "0.1",
        "operations": [
            {"op": "set-workspace", "name": "Renamed"},
            {"op": "add-claim", "claim": {"id": "w1", "claim": "x", "supports": [], "contradicts": []}},
        ],
    }
    path = tmp_path / "patch.json"
    path.write_text(json.dumps(patch), encoding="utf-8")
    assert main(["patch", str(root), "--file", str(path), "--expected-sha256", sha(root)]) == 0
    updated = doc(root)
    assert updated["name"] == "Renamed"
    assert updated["claims"][0]["id"] == "w1"


def test_report_baseline_lists_changed_objects(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    init(root)
    capsys.readouterr()
    baseline = tmp_path / "baseline"
    shutil.copytree(root, baseline)
    manifest = json.loads((root / "a" / "runevidence.json").read_text(encoding="utf-8"))
    manifest["results"][0]["value"] = 0.8
    (root / "a" / "runevidence.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert main(["report", str(root), "--baseline", str(baseline), "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    member = next(item for item in data["baseline"]["members"] if item["id"] == "prov-a")
    assert ["result", "acc"] in member["changed"]


def test_a_malformed_claim_file_is_refused_without_a_traceback(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    init(root)
    capsys.readouterr()
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    before = (root / "rungraph.json").read_bytes()
    assert main(["claim", "add", str(root), "--file", str(broken)]) == 2
    captured = capsys.readouterr()
    assert "rungraph-error" in captured.out
    assert "Traceback" not in captured.err
    assert (root / "rungraph.json").read_bytes() == before


def test_a_claim_file_needs_an_id(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    init(root)
    capsys.readouterr()
    claim_file = tmp_path / "claim.json"
    claim_file.write_text(json.dumps({"claim": "no id"}), encoding="utf-8")
    before = (root / "rungraph.json").read_bytes()
    assert main(["claim", "add", str(root), "--file", str(claim_file)]) == 2
    assert "non-empty 'id'" in capsys.readouterr().out
    assert (root / "rungraph.json").read_bytes() == before


def test_a_patch_must_declare_its_envelope(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    init(root)
    capsys.readouterr()
    patch_file = tmp_path / "patch.json"
    patch_file.write_text(
        json.dumps({"format": "other", "version": "99", "operations": [{"op": "set-workspace", "name": "x"}]}),
        encoding="utf-8",
    )
    before = (root / "rungraph.json").read_bytes()
    assert main(["patch", str(root), "--file", str(patch_file), "--expected-sha256", sha(root)]) == 2
    assert "rungraph-patch 0.1" in capsys.readouterr().out
    assert (root / "rungraph.json").read_bytes() == before


def test_a_patch_operation_needs_its_payload(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    init(root)
    capsys.readouterr()
    patch_file = tmp_path / "patch.json"
    patch_file.write_text(
        json.dumps({"format": "rungraph-patch", "version": "0.1", "operations": [{"op": "add-member"}]}),
        encoding="utf-8",
    )
    before = (root / "rungraph.json").read_bytes()
    assert main(["patch", str(root), "--file", str(patch_file), "--expected-sha256", sha(root)]) == 2
    captured = capsys.readouterr()
    assert "needs 'member'" in captured.out
    assert "Traceback" not in captured.err
    assert (root / "rungraph.json").read_bytes() == before


def test_invalid_utf8_in_an_authoring_file_is_refused(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    init(root)
    capsys.readouterr()
    broken = tmp_path / "broken.json"
    broken.write_bytes(b"\xff")
    before = (root / "rungraph.json").read_bytes()
    assert main(["claim", "add", str(root), "--file", str(broken)]) == 2
    captured = capsys.readouterr()
    assert "rungraph-error" in captured.out
    assert "Traceback" not in captured.err
    assert (root / "rungraph.json").read_bytes() == before


def test_a_claim_into_an_ambiguous_member_id_is_refused(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    manifest_path = root / "a" / "runevidence.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["results"] = [
        {"id": "r", "run_id": "r", "name": "accuracy", "value": 0.9},
        {"id": "ok", "run_id": "r", "name": "other", "value": 0.8},
    ]
    manifest["evidence"] = []
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    init(root)
    capsys.readouterr()
    claim = {
        "id": "w1",
        "claim": "ambiguous",
        "supports": [{"member": "prov-a", "id": "ok"}],
        "contradicts": [],
    }
    claim_file = tmp_path / "claim.json"
    claim_file.write_text(json.dumps(claim), encoding="utf-8")
    before = (root / "rungraph.json").read_bytes()
    assert main(["claim", "add", str(root), "--file", str(claim_file), "--expected-sha256", sha(root)]) == 2
    assert "duplicate IDs" in capsys.readouterr().out
    assert (root / "rungraph.json").read_bytes() == before


def test_relationship_endpoints_into_an_ambiguous_member_are_refused(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    manifest_path = root / "a" / "runevidence.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["results"] = [
        {"id": "r", "run_id": "r", "name": "accuracy", "value": 0.9},
        {"id": "ok", "run_id": "r", "name": "other", "value": 0.8},
    ]
    manifest["evidence"] = []
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    init(root)
    capsys.readouterr()
    before = (root / "rungraph.json").read_bytes()
    for source, target in (
        ({"member": "prov-a", "id": "data"}, {"member": "prov-b", "id": "data"}),
        ({"member": "prov-b", "id": "data"}, {"member": "prov-a", "id": "data"}),
    ):
        relationship = {
            "id": "rel1",
            "type": "derived_from",
            "source": source,
            "target": target,
        }
        relationship_file = tmp_path / "rel.json"
        relationship_file.write_text(json.dumps(relationship), encoding="utf-8")
        assert main(
            ["relationship", "add", str(root), "--file", str(relationship_file), "--expected-sha256", sha(root)]
        ) == 2
        assert "duplicate IDs" in capsys.readouterr().out
        assert (root / "rungraph.json").read_bytes() == before


def test_a_whitespace_only_workspace_claim_is_refused(tmp_path: Path, capsys):
    root = make_root(tmp_path)
    init(root)
    capsys.readouterr()
    claim = {"id": "w1", "claim": "   ", "supports": [], "contradicts": []}
    claim_file = tmp_path / "claim.json"
    claim_file.write_text(json.dumps(claim), encoding="utf-8")
    before = (root / "rungraph.json").read_bytes()
    assert main(["claim", "add", str(root), "--file", str(claim_file), "--expected-sha256", sha(root)]) == 2
    assert "claim text must be non-blank" in capsys.readouterr().out
    assert (root / "rungraph.json").read_bytes() == before
