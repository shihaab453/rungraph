"""D11R1: fixed v1 fingerprint ABI vectors and schema/default/timezone contracts.

These immutable expected hashes are intentionally NOT calculated from the
production canonicalizer, D5 helpers, or a mutable fixture file at runtime.
A hash change requires an independently reviewed format-version decision.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re

import pytest

from runevidence.models import (
    Artifact, EvidenceReference, Manifest, Project, Relationship,
    RelationshipType as T, Result, Run,
)
from runevidence.run_fingerprint import manifest_fingerprint, format_fingerprint
from runevidence.run_impact_diff import compare_claim_impact


def manifest(*, project=None, runs=(), artifacts=(), results=(), evidence=(), relationships=()):
    return Manifest(
        project=project or Project(id="p", name="demo"),
        runs=list(runs), artifacts=list(artifacts), results=list(results),
        evidence=list(evidence), relationships=list(relationships),
    )


def fingerprint(record):
    return manifest_fingerprint(record, manifest_path="synthetic/runevidence.json")["fingerprint"]


def digest(record):
    return fingerprint(record)["digest"]


def rich_record():
    # The fixed digest covers project, run, artifact, result, claim, typed
    # relationships with None versus empty notes, nested typed parameters,
    # duplicate shorthand membership, and an offset-aware datetime.
    return manifest(
        project=Project(id="study", name="trial", tags=["baseline"],
                        metadata={"owner": "lab", "priority": 1}),
        runs=[Run(id="run", started_at=datetime(2026, 11, 4, 9, 30,
                         tzinfo=timezone(timedelta(hours=1))),
                  params={"lr": 0.125, "seed": 7, "active": True,
                          "nested": {"z": None, "a": [1, 2.5]}},
                  metadata={"note": "alpha"})],
        artifacts=[Artifact(id="file", path="trial.bin", run_id="run",
                            sha256="ab" * 32, size_bytes=12,
                            kind="checkpoint", metadata={"x": "y"})],
        results=[Result(id="loss", run_id="run", name="loss", value=0.875,
                        metadata={"units": "fraction"})],
        evidence=[EvidenceReference(id="claim", claim="consistent",
                                    source="README.md",
                                    supports=["loss", "file", "loss"],
                                    metadata={"tag": "ok"})],
        relationships=[
            Relationship(source="file", target="run", type=T.produced_by, note=None),
            Relationship(source="file", target="run", type=T.produced_by, note=""),
            Relationship(source="claim", target="loss", type=T.supports, note="reason"),
        ],
    )


# Immutable expected digests for the *deployed D11 v1* encoding, computed
# offline from the v1 canonical byte contract; do not regenerate on test runs.
# rich_record is anchored to the actual Windows source/model (SHA256 1f6fb49e...)
# only after a separately verified, independent live-schema canonical probe.
# The superseded 861756... value came from a detached synthetic model; it is
# preserved as diagnostic evidence, not a valid authoritative live golden.
GOLDEN_V1 = {
    "project_only": "677e5b2b9660bb3818cec00086364326835f5bf8a4d6c4025d55da11fc06cb47",
    "rich_record": "87f78f7d218f7b3126023b9875291a03d8d6f8ba0cb395bc8c3ec77b65d382b4",
    "datetime_utc": "0ddd9e06e87e66faad1650858a936eff4d0a36417bfeb87d4a17a69f023a228b",
    "datetime_plus_one": "a2f479dca0ce3850c9eac0a93ed2db6fee58e4f5a7f2c264d7c4653ba54887ed",
    "number_bool": "ff3162d1a9a4f2e6a37f28b56c8688f32ab0099055bfeecf15cbc46376c5eee4",
    "number_int": "d0e49fa42ca465ce1d76749784075cc65efb247ccdd37098f3aa151cfd14cbd2",
    "number_float": "cbce95ecc8ca1f606a96287cf8320a68132cda133375448c9f25bfdb28900248",
    "number_nan": "7ae6a0f21ac54d81b8a7375ca54c51a47a495accd5890a53527289072841532c",
    "number_posinf": "fee7785254ab89306a3648e48f828abfdf129e63597ab856a81a74c3763bd478",
    "number_neginf": "618a5e66f1825708fc35ace4fb84b87a38105fdc1abebcd325acd53592f7f9f2",
    "number_negative_zero": "6d4d3aa6a239caf66828f399cd55889455a67362c059883ef140784c60405259",
    "number_null": "e83c6cf022732b50226650897179af554eb0c08806f182c298447deb742c5b38",
}


def test_d11r1_golden_minimal_format_v1():
    value = fingerprint(manifest())
    assert value == {
        "algorithm": "sha256", "semantic_format": "recorded-manifest-v1",
        "digest": GOLDEN_V1["project_only"],
        "objects": 1, "relationships": 0,
    }


def test_d11r1_golden_rich_graph_v1():
    value = fingerprint(rich_record())
    assert value == {
        "algorithm": "sha256", "semantic_format": "recorded-manifest-v1",
        "digest": GOLDEN_V1["rich_record"],
        "objects": 5, "relationships": 3,
    }


def test_d11r1_rich_golden_preserves_noop_permutations():
    first = rich_record()
    second = rich_record().model_copy(deep=True)
    second.relationships.reverse()
    second.relationships.extend(second.relationships)
    second.evidence[0].supports.reverse()
    second.runs[0].params = {"nested": {"a": [1, 2.5], "z": None},
                              "active": True, "seed": 7, "lr": 0.125}
    assert digest(first) == digest(second) == GOLDEN_V1["rich_record"]
    comparison = compare_claim_impact(first, second, before_path="one", after_path="two")
    assert comparison["changes"] == {"objects": [], "relationships": []}


@pytest.mark.parametrize("value,name", [
    (True, "number_bool"), (1, "number_int"), (1.0, "number_float"),
    (float("nan"), "number_nan"), (float("inf"), "number_posinf"),
    (float("-inf"), "number_neginf"), (-0.0, "number_negative_zero"),
    (None, "number_null"),
])
def test_d11r1_golden_typed_numeric_value_v1(value, name):
    record = manifest(runs=[Run(id="run")],
                      results=[Result(id="metric", run_id="run", name="score", value=value)])
    assert digest(record) == GOLDEN_V1[name]


def test_d11r1_golden_utc_datetime():
    record = manifest(runs=[Run(id="run", started_at=datetime(2026, 11, 4, 8, 30,
                                                          tzinfo=timezone.utc))])
    assert digest(record) == GOLDEN_V1["datetime_utc"]


def test_d11r1_golden_offset_datetime():
    record = manifest(runs=[Run(id="run", started_at=datetime(2026, 11, 4, 9, 30,
                      tzinfo=timezone(timedelta(hours=1))))])
    assert digest(record) == GOLDEN_V1["datetime_plus_one"]


def test_d11r1_timezones_are_recorded_representation_not_normalized_instants():
    # These represent the same instant but different recorded clock/offset
    # encodings. D5 considers the object modified; D11 must mirror that rule.
    utc = manifest(runs=[Run(id="run", started_at="2026-11-04T08:30:00Z")])
    plus_one = manifest(runs=[Run(id="run", started_at="2026-11-04T09:30:00+01:00")])
    naive = manifest(runs=[Run(id="run", started_at="2026-11-04T08:30:00")])
    assert digest(utc) == GOLDEN_V1["datetime_utc"]
    assert digest(plus_one) == GOLDEN_V1["datetime_plus_one"]
    assert len({digest(utc), digest(plus_one), digest(naive)}) == 3
    assert compare_claim_impact(utc, plus_one, before_path="a", after_path="b")\
        ["summary"]["object_changes"] == 1


def test_d11r1_timezone_z_and_plus_zero_parse_equivalently():
    z = manifest(runs=[Run(id="run", started_at="2026-11-04T08:30:00Z")])
    offset = manifest(runs=[Run(id="run", started_at="2026-11-04T08:30:00+00:00")])
    assert digest(z) == digest(offset) == GOLDEN_V1["datetime_utc"]


def test_d11r1_schema_defaults_are_normalized_not_raw_key_presence():
    omitted = Manifest.model_validate({"project": {"id": "p", "name": "demo"},
                                       "runs": [{"id": "run"}]})
    explicit = Manifest.model_validate({
        "schema_version": "0.1", "project": {"id": "p", "name": "demo",
            "description": None, "tags": [], "metadata": {}},
        "runs": [{"id": "run", "name": None, "started_at": None,
                  "params": {}, "metadata": {}}],
        "artifacts": [], "results": [], "evidence": [], "relationships": [],
    })
    assert digest(omitted) == digest(explicit)
    assert compare_claim_impact(omitted, explicit, before_path="a", after_path="b")\
        ["changes"] == {"objects": [], "relationships": []}


def test_d11r1_uppercase_sha_is_normalized_by_schema():
    upper = manifest(artifacts=[Artifact(id="file", path="a", sha256="AB" * 32)])
    lower = manifest(artifacts=[Artifact(id="file", path="a", sha256="ab" * 32)])
    assert upper.artifacts[0].sha256 == lower.artifacts[0].sha256
    assert digest(upper) == digest(lower)


def test_d11r1_format_domain_separation_and_artifact_scope():
    value = fingerprint(manifest())
    assert re.fullmatch(r"[0-9a-f]{64}", value["digest"])
    assert value["semantic_format"] == "recorded-manifest-v1"
    # File bytes / path to the manifest are deliberately not part of the digest.
    first = manifest_fingerprint(manifest(), manifest_path="first.json")
    second = manifest_fingerprint(manifest(), manifest_path="elsewhere.json")
    assert first["fingerprint"] == second["fingerprint"]
    assert first["manifest"] != second["manifest"]
    assert "not a digest of the" in first["note"]
    assert "file-integrity" in first["note"]


def test_d11r1_terminal_does_not_leak_ansi_or_unbounded_path():
    path = "trace\nHELLO\x1b[31m" + "A" * 100_000
    record = manifest_fingerprint(manifest(), manifest_path=path)
    shown = format_fingerprint(record)
    assert "\x1b" not in shown and "\nHELLO" not in shown
    assert len(shown) <= 16384
    assert record["manifest"] == path
