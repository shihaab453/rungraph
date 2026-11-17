"""Schema artifact freeze and runtime accept/reject vectors (S6 slice 1).

The byte pins detect any change to the generated schemas; the runtime vectors detect a
changed interpretation that a JSON Schema file alone would not catch. Changing either
requires a reviewed schema version decision, never an in-place edit.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from runevidence.manifest import ManifestLoadError, load_manifest
from runevidence.run_comparability import compare_paths

REPO = Path(__file__).resolve().parents[1]
VECTORS = Path(__file__).resolve().parent / "data" / "schema-vectors"

SCHEMA_PINS = {
    "schema/runevidence.schema.json": (
        "cc0727d4693216d2580ae8b2343661ad9ab64f15129ffa492a270c8697644ab2"
    ),
    "schema/runevidence-0.2.schema.json": (
        "67658ebfd9b55ee1a9d2b659facdd5e7dc22b9ee507e2892a67b6733f9280d46"
    ),
    "schema/runevidence-0.3.schema.json": (
        "bd18dd053b6805b96b27cb8a9477dcbb9497d84ef6735c840c9aa2ebd0a544e8"
    ),
    "schema/runevidence-0.4.schema.json": (
        "973099fb21c3d67a8775c6c0ceb3d2baeb6416df464b2b980f7148f90c962078"
    ),
    "schema/runevidence-0.5.schema.json": (
        "af1c085eb50ab3d8111404c23503628c3af2433bfb5b0a956d75bee6f00935b4"
    ),
    "schema/runevidence-0.6.schema.json": (
        "ce8d0137b1d49f28d7ae33910e6cd7c7ab25a14075d7bc7639854ad691216b26"
    ),
    "schema/runevidence-0.7.schema.json": (
        "642bc55e133db09b66dde8b61722a03181094b3e760fc91c23a2ea575b10aa76"
    ),
    "schema/runevidence-0.8.schema.json": (
        "ca29c87f61439ca3295e60794d10bdf9cac008b7bcfb0f43baee5388f18a22d0"
    ),
    "schema/runevidence-0.9.schema.json": (
        "22baff4b0f73db32ff3ebf7f8f2829b30c132cf3b646e5c7707a88fcc5ffa50c"
    ),
    "schema/runevidence-0.10.schema.json": (
        "bd8760ca6ce119326a0b0b06e7c616d8b0fa027285f67422454934fadfba1cdf"
    ),
    "schema/runevidence-0.11.schema.json": (
        "1dce887130d09ef13465274793a0c8e70ccd017db6ea10d9d3b6ad71e111ce00"
    ),
}

REJECT_VECTORS = (
    "reject-0.1-conditions-only.json",
    "reject-0.1-dataset-id-only.json",
    "reject-0.1-dataset-id-null-only.json",
    "reject-0.1-metric-definition-only.json",
    "reject-0.2-blank-split.json",
    "reject-0.2-whitespace-split.json",
    "reject-0.2-conditions-null.json",
    "reject-0.2-dataset-id-null.json",
    "reject-0.2-unknown-condition.json",
    "reject-unknown-version.json",
)


def test_schema_artifacts_are_byte_frozen():
    for relative, digest in SCHEMA_PINS.items():
        assert hashlib.sha256((REPO / relative).read_bytes()).hexdigest() == digest, (
            f"{relative} changed; schema changes need a new reviewed version, not an edit"
        )


def test_schema_files_stay_lf_on_checkout():
    text = (REPO / ".gitattributes").read_text(encoding="utf-8")
    assert "schema/** -text" in text


def test_schema_description_matches_the_delivered_directory_surface():
    for relative in SCHEMA_PINS:
        document = json.loads((REPO / relative).read_text(encoding="utf-8"))
        description = document["$defs"]["Artifact"]["properties"]["artifact_type"][
            "description"
        ]
        assert "development-only" not in description
        assert "--directory-artifacts" in description
        assert "add --directory or rehash --directory" in description


def test_runtime_accepts_permitted_explicit_nulls():
    loaded = load_manifest(VECTORS / "accept-0.2-permitted-nulls.json")
    conditions = loaded.manifest.runs[0].conditions
    assert conditions.evaluation_split is None
    assert conditions.evaluation_protocol is None
    assert conditions.source_revision is None
    assert conditions.environment is None
    assert loaded.manifest.results[0].metric_definition is None


def test_runtime_accepts_a_complete_0_2_manifest():
    loaded = load_manifest(VECTORS / "accept-0.2-all-fields.json")
    assert loaded.manifest.schema_version == "0.2"
    conditions = loaded.manifest.runs[0].conditions
    assert conditions.evaluation_split == "test"
    assert conditions.evaluation_protocol.protocol_id == "proto"


@pytest.mark.parametrize("name", REJECT_VECTORS)
def test_runtime_rejects_the_frozen_invalid_vectors(name):
    with pytest.raises(ManifestLoadError):
        load_manifest(VECTORS / name)


def test_selector_ownership_is_rejected_by_the_comparison_layer(tmp_path):
    """A selector must name an artifact consumed by the selected run."""
    import json

    base = json.loads((VECTORS / "accept-0.2-all-fields.json").read_text())

    def side(name: str, run_id: str, result_id: str):
        document = json.loads(json.dumps(base))
        document["runs"][0]["id"] = run_id
        document["results"][0]["id"] = result_id
        document["results"][0]["run_id"] = run_id
        # Only the dataset is consumed; the checkpoint selector is dangling.
        document["relationships"] = [
            {"source": "data", "target": run_id, "type": "input_to"}
        ]
        path = tmp_path / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    baseline = side("baseline.json", "run-b", "acc-b")
    target = side("target.json", "run-t", "acc-t")
    outcome = compare_paths(
        baseline, target, baseline_result_id="acc-b", target_result_id="acc-t"
    )

    assert outcome.exit_code == 2
    document = outcome.document
    assert document["format"] == "runevidence-comparability-error-v1"
    assert document["error"]["code"] == "MAPPING_FAILED"
    assert document["error"]["mapping_code"] == "DANGLING_REFERENCE"
