"""Read-only run-drilldown CLI tests using disposable synthetic manifests."""
import json
from pathlib import Path

import pytest

from runevidence.cli import main
from runevidence.manifest import dump_manifest
from runevidence.models import (
    Artifact, EvidenceReference, Manifest, Project, Relationship,
    RelationshipType as T, Result, Run,
)
from runevidence.run_details import RunDetailsError, format_run_details, run_details


def manifest(*, runs=None, artifacts=None, results=None, claims=None, relationships=None):
    return Manifest(project=Project(id="proj", name="Project"),
                    runs=runs if runs is not None else [Run(id="a", name="Alpha"), Run(id="b", name="Beta")],
                    artifacts=artifacts or [], results=results or [],
                    evidence=claims or [], relationships=relationships or [])


def root(tmp_path, doc):
    path = tmp_path / "synthetic"
    path.mkdir()
    (path / "runevidence.json").write_text(dump_manifest(doc), encoding="utf-8", newline="\n")
    return path


def call(path, capsys, *, run="a", as_json=True):
    argv = ["run", str(path), "--id", run] + (["--json"] if as_json else [])
    code = main(argv)
    captured = capsys.readouterr()
    assert captured.err == ""
    return code, json.loads(captured.out) if as_json else captured.out


def test_selected_run_only_direct_attributions(tmp_path, capsys):
    doc = manifest(
        runs=[Run(id="a", name="A", params={"lr": .01}), Run(id="b", name="B")],
        artifacts=[Artifact(id="fa", path="a.bin", run_id="a"), Artifact(id="fb", path="b.bin", run_id="b")],
        results=[Result(id="ra", name="acc", value=.9, run_id="a", artifact_id="fa"),
                 Result(id="rb", name="acc", value=.8, run_id="b")],
    )
    folder = root(tmp_path, doc)
    before = (folder / "runevidence.json").read_bytes()
    code, payload = call(folder, capsys)
    assert code == 0
    assert payload["run"]["id"] == "a" and payload["run"]["params"] == {"lr": .01}
    assert [x["id"] for x in payload["results"]] == ["ra"]
    assert [x["id"] for x in payload["produced_artifacts"]] == ["fa"]
    assert payload["summary"] == {"results": 1, "produced_artifacts": 1, "input_artifacts": 0, "related_claims": 0}
    assert (folder / "runevidence.json").read_bytes() == before
    assert [p.name for p in folder.iterdir()] == ["runevidence.json"]


def test_explicit_relationships_and_claim_links(tmp_path, capsys):
    doc = manifest(
        artifacts=[Artifact(id="model", path="weights.bin"), Artifact(id="data", path="input.csv")],
        results=[Result(id="score", run_id="a", name="f1", value=.83)],
        claims=[EvidenceReference(id="support", claim="Metric is strong", supports=["score"]),
                EvidenceReference(id="counter", claim="Limitations remain")],
        relationships=[
            Relationship(source="model", target="a", type=T.produced_by),
            Relationship(source="data", target="a", type=T.input_to),
            Relationship(source="counter", target="model", type=T.contradicts),
            Relationship(source="support", target="score", type=T.supports),
        ],
    )
    code, data = call(root(tmp_path, doc), capsys)
    assert code == 0
    assert [a["id"] for a in data["produced_artifacts"]] == ["model"]
    assert [a["id"] for a in data["input_artifacts"]] == ["data"]
    assert data["related_claims"] == [
        {"id": "support", "claim": "Metric is strong", "source": None, "supports": ["score"], "contradicts": []},
        {"id": "counter", "claim": "Limitations remain", "source": None, "supports": [], "contradicts": ["model"]},
    ]


def test_other_run_claims_not_attributed(tmp_path, capsys):
    doc = manifest(results=[Result(id="rb", run_id="b", name="acc", value=.8)],
                   claims=[EvidenceReference(id="ev", claim="Only b", supports=["b", "rb"])])
    code, output = call(root(tmp_path, doc), capsys)
    assert code == 0
    assert output["related_claims"] == []


def test_metadata_unreadable_and_text_escapes_controls(tmp_path, capsys):
    doc = manifest(runs=[Run(id="a", name="A\nERROR forged", metadata={"hydra": {
        "unreadable": ["file\nBAD", 123], "unreadable_reasons": {"file\nBAD": "reason\rBAD"}}})],
        results=[Result(id="r", run_id="a", name="metric\nBAD", value="v\nBAD")])
    folder = root(tmp_path, doc)
    code, text = call(folder, capsys, as_json=False)
    assert code == 0
    assert "A\\nERROR forged" in text
    assert "file\\nBAD" in text and "reason\\rBAD" in text
    assert "metric\\nBAD" in text and "v\\nBAD" in text
    assert "\nERROR forged" not in text and "\nBAD" not in text
    code, payload = call(folder, capsys)
    assert payload["unreadable_inputs"] == [{"adapter": "hydra", "path": "file\nBAD", "reason": "reason\rBAD"}]


def test_no_manifests_or_missing_id_refuses(tmp_path, capsys):
    folder = tmp_path / "missing"
    folder.mkdir()
    code, output = call(folder, capsys)
    assert code == 2 and output["exit_code"] == 2
    code, output = call(root(tmp_path, manifest()), capsys, run="unknown")
    assert code == 2 and "not found" in output["error"]


def test_duplicate_global_ids_refused():
    doc = manifest(results=[Result(id="a", run_id="a", name="m", value=1)])
    with pytest.raises(RunDetailsError, match="duplicate object ID"):
        run_details(doc, "a", manifest_path="synthetic")


def test_conflicting_attributions_refused():
    doc = manifest(artifacts=[Artifact(id="f", path="f.txt", run_id="b")],
                   relationships=[Relationship(source="f", target="a", type=T.produced_by)])
    with pytest.raises(RunDetailsError, match="conflicting recorded run attribution"):
        run_details(doc, "a", manifest_path="synthetic")


def test_text_output_bounded_json_full():
    doc = manifest(results=[Result(id=f"res{i}", run_id="a", name=f"m{i}", value=i) for i in range(22)])
    result = run_details(doc, "a", manifest_path="synthetic")
    text = format_run_details(result)
    assert "and 2 more (use --json)" in text
    assert len(result["results"]) == 22


def test_untracked_files_not_opened(tmp_path, capsys):
    doc = manifest(artifacts=[Artifact(id="file", path="unavailable.bin", run_id="a", sha256="a" * 64)])
    folder = root(tmp_path, doc)
    code, data = call(folder, capsys)
    assert code == 0 and data["produced_artifacts"][0]["path"] == "unavailable.bin"
    assert not (folder / "unavailable.bin").exists()


def test_other_commands_unchanged(tmp_path, capsys):
    folder = root(tmp_path, manifest(results=[Result(id="ra", run_id="a", name="acc", value=.8)]))
    code = main(["matrix", str(folder), "--json"])
    assert code == 0 and "summary" in json.loads(capsys.readouterr().out)
    code = main(["compare", str(folder), "--base", "a", "--target", "b", "--json"])
    assert code == 0 and "baseline" in json.loads(capsys.readouterr().out)
    code = main(["leaderboard", str(folder), "--metric", "acc", "--direction", "max", "--json"])
    assert code == 0 and "ranked" in json.loads(capsys.readouterr().out)
