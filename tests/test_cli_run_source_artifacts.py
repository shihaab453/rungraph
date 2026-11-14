"""D1R1: direct and typed result-source links survive run drilldown."""

import json

from runevidence.models import (
    Artifact, EvidenceReference, Manifest, Project, Relationship,
    RelationshipType as T, Result, Run,
)
from runevidence.run_details import format_run_details, run_details


def _doc(*, results, artifacts=(), claims=(), relationships=()):
    return Manifest(
        project=Project(id="project", name="Demo"),
        runs=[Run(id="a"), Run(id="b")],
        results=list(results), artifacts=list(artifacts),
        evidence=list(claims), relationships=list(relationships),
    )


def _selected(manifest):
    return run_details(manifest, "a", manifest_path="synthetic")


def test_explicit_reported_in_unowned_artifact_and_linked_claim_visible():
    """Reproduces D1 omission: recorded file and its claim were invisible."""
    data = _selected(_doc(
        results=[Result(id="metric", run_id="a", name="accuracy", value=0.83)],
        artifacts=[Artifact(id="metrics_file", path="metrics.json")],
        claims=[EvidenceReference(id="proof", claim="Source metrics", supports=["metrics_file"])],
        relationships=[Relationship(source="metric", target="metrics_file", type=T.reported_in)],
    ))
    assert data["produced_artifacts"] == [] and data["input_artifacts"] == []
    assert data["results"][0]["recorded_source_artifact_ids"] == ["metrics_file"]
    assert [a["id"] for a in data["result_source_artifacts"]] == ["metrics_file"]
    assert data["related_claims"][0]["supports"] == ["metrics_file"]
    text = format_run_details(data)
    assert 'Recorded result source artifacts (1):' in text
    assert '"metrics.json"' in text
    json.dumps(data, allow_nan=False)


def test_direct_artifact_id_source_without_run_ownership_is_visible():
    data = _selected(_doc(
        results=[Result(id="metric", run_id="a", name="accuracy", value=0.7, artifact_id="file")],
        artifacts=[Artifact(id="file", path="scores.json")],
        claims=[EvidenceReference(id="attestation", claim="Numbers sourced", supports=["file"])],
    ))
    assert data["results"][0]["artifact_id"] == "file"  # old key remains
    assert data["results"][0]["recorded_source_artifact_ids"] == ["file"]
    assert [a["id"] for a in data["result_source_artifacts"]] == ["file"]
    assert data["related_claims"][0]["supports"] == ["file"]
    assert data["summary"] == {"results": 1, "produced_artifacts": 0, "input_artifacts": 0, "related_claims": 1}


def test_direct_and_explicit_duplicates_are_deduplicated_and_sorted():
    data = _selected(_doc(
        results=[Result(id="metric", run_id="a", name="score", artifact_id="z")],
        artifacts=[Artifact(id="z", path="z.json"), Artifact(id="a_file", path="a.json")],
        relationships=[
            Relationship(source="metric", target="z", type=T.reported_in),
            Relationship(source="metric", target="a_file", type=T.reported_in),
            Relationship(source="metric", target="a_file", type=T.reported_in),
        ],
    ))
    assert data["results"][0]["recorded_source_artifact_ids"] == ["a_file", "z"]
    # Top-level artifact objects follow manifest order, not set order.
    assert [a["id"] for a in data["result_source_artifacts"]] == ["z", "a_file"]


def test_other_runs_reported_in_links_do_not_leak_into_selected_run():
    data = _selected(_doc(
        results=[Result(id="metric_a", run_id="a", name="acc"),
                 Result(id="metric_b", run_id="b", name="acc")],
        artifacts=[Artifact(id="file_b", path="b.json")],
        claims=[EvidenceReference(id="b_claim", claim="Only b", supports=["file_b"])],
        relationships=[Relationship(source="metric_b", target="file_b", type=T.reported_in)],
    ))
    assert "result_source_artifacts" not in data
    assert "recorded_source_artifact_ids" not in data["results"][0]
    assert data["related_claims"] == []


def test_dangling_source_id_stays_visible_but_not_claimed_as_real_artifact():
    data = _selected(_doc(
        results=[Result(id="metric", run_id="a", name="acc", artifact_id="missing_file")],
    ))
    assert data["results"][0]["recorded_source_artifact_ids"] == ["missing_file"]
    assert "result_source_artifacts" not in data
    assert "missing_file" in format_run_details(data)
    assert "not been checked" in data["note"]


def test_untrusted_recorded_file_path_does_not_forge_report_lines():
    data = _selected(_doc(
        results=[Result(id="metric", run_id="a", name="acc")],
        artifacts=[Artifact(id="file", path="folder/metrics\nERROR spoof.json")],
        relationships=[Relationship(source="metric", target="file", type=T.reported_in)],
    ))
    rendered = format_run_details(data)
    assert "metrics\\nERROR spoof" in rendered
    assert "\nERROR spoof" not in rendered


def test_no_source_link_preserves_all_legacy_json_keys_and_text_sections():
    data = _selected(_doc(results=[Result(id="metric", run_id="a", name="acc")]))
    assert "recorded_source_artifact_ids" not in data["results"][0]
    assert "result_source_artifacts" not in data
    assert "Recorded result source artifacts" not in format_run_details(data)
    assert data["summary"] == {"results": 1, "produced_artifacts": 0, "input_artifacts": 0, "related_claims": 0}


def test_large_source_artifact_display_bounded_json_complete():
    artifacts = [Artifact(id=f"f{i}", path=f"f{i}.json") for i in range(25)]
    edges = [Relationship(source="metric", target=a.id, type=T.reported_in) for a in artifacts]
    data = _selected(_doc(results=[Result(id="metric", run_id="a", name="acc")],
                          artifacts=artifacts, relationships=edges))
    assert len(data["result_source_artifacts"]) == 25
    assert len(data["results"][0]["recorded_source_artifact_ids"]) == 25
    text = format_run_details(data)
    assert "Recorded result source artifacts (25):" in text
    assert "and 5 more (use --json)" in text
    assert "and 5 more in --json" in text  # per-result source ID list is also bounded


def test_nonfinite_metric_visible_without_invalid_json_or_null_conflation():
    data = _selected(_doc(results=[
        Result(id="nan", run_id="a", name="loss", value=float("nan")),
        Result(id="inf", run_id="a", name="overflow", value=float("inf")),
        Result(id="neg", run_id="a", name="neg", value=float("-inf")),
        Result(id="null", run_id="a", name="unset", value=None),
    ]))
    assert [r["value"] for r in data["results"]] == [None, None, None, None]
    assert data["nonfinite_recorded_values"] == [
        {"path": "/results/0/value", "kind": "NaN"},
        {"path": "/results/1/value", "kind": "Infinity"},
        {"path": "/results/2/value", "kind": "-Infinity"},
    ]
    assert json.loads(json.dumps(data, allow_nan=False))["results"][3]["value"] is None
    text = format_run_details(data)
    assert "Nonfinite recorded values (3):" in text
    assert "NaN (JSON null)" in text


def test_nested_nonfinite_params_use_escaped_json_pointers():
    doc = Manifest(
        project=Project(id="project", name="Demo"),
        runs=[Run(id="a", params={"a/b~c": [{"lr": float("inf")}, float("nan")]})],
    )
    data = _selected(doc)
    assert data["run"]["params"]["a/b~c"] == [{"lr": None}, None]
    assert data["nonfinite_recorded_values"] == [
        {"path": "/run/params/a~1b~0c/0/lr", "kind": "Infinity"},
        {"path": "/run/params/a~1b~0c/1", "kind": "NaN"},
    ]
    assert "Nonfinite recorded values (2):" in format_run_details(data)


def test_finite_and_recorded_null_legacy_output_unchanged():
    data = _selected(_doc(results=[Result(id="finite", run_id="a", name="acc", value=0.92),
                                   Result(id="null", run_id="a", name="no value", value=None)]))
    assert "nonfinite_recorded_values" not in data
    assert data["results"][0]["value"] == 0.92
    assert data["results"][1]["value"] is None


def test_cli_handles_serialized_nonfinite_recorded_metric(tmp_path, capsys):
    # Mirrors JSON persisted by the current manifest serializer. The recorded
    # NaN token must not cause a JSON serializer exception in `run --json`.
    from runevidence.cli import main
    from runevidence.manifest import dump_manifest
    root = tmp_path / "synthetic"
    root.mkdir()
    doc = _doc(results=[Result(id="loss", run_id="a", name="loss", value=float("nan"))])
    original = dump_manifest(doc).encode("utf-8")
    path = root / "runevidence.json"
    path.write_bytes(original)
    rc = main(["run", str(root), "--id", "a", "--json"])
    streams = capsys.readouterr()
    assert rc == 0 and not streams.err
    payload = json.loads(streams.out)
    assert payload["results"][0]["value"] is None
    assert payload["nonfinite_recorded_values"] == [{"path": "/results/0/value", "kind": "NaN"}]
    assert path.read_bytes() == original
