"""Frozen-reader acceptance for actual writer output (Stage 3b+3c gate).

The frozen 0.1 reader is the committed ``schema/runevidence.schema.json``
artifact validated with a standards-compliant JSON Schema validator, not a
handcrafted document. These tests dump manifests with the current writer and
validate the emitted JSON against that committed artifact. They also prove the
committed 0.2 schema accepts actual 0.2 writer output.

Everything here is read-only: committed examples are only loaded, and the
synthetic manifest is written inside ``tmp_path``.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import jsonschema
import pytest

from runevidence.manifest import ManifestLoadError, dump_manifest, load_manifest

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples"
SCHEMA_0_1_PATH = REPO / "schema" / "runevidence.schema.json"
SCHEMA_0_2_PATH = REPO / "schema" / "runevidence-0.2.schema.json"

ZERO_TWO_RUN_KEY = "conditions"
ZERO_TWO_ARTIFACT_KEY = "dataset_id"
ZERO_TWO_RESULT_KEY = "metric_definition"


def _load_schema(path: Path) -> dict:
    document = json.loads(path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(document)
    return document


SCHEMA_0_1 = _load_schema(SCHEMA_0_1_PATH)
SCHEMA_0_2 = _load_schema(SCHEMA_0_2_PATH)
VALIDATOR_0_1 = jsonschema.Draft202012Validator(SCHEMA_0_1)
VALIDATOR_0_2 = jsonschema.Draft202012Validator(SCHEMA_0_2)


def _errors(validator: jsonschema.Draft202012Validator, document: dict) -> list:
    """Validation errors in a stable order, so failures are reproducible."""
    return sorted(
        validator.iter_errors(document),
        key=lambda error: (list(error.absolute_path), error.message),
    )


def _manifest_id(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def _discover_manifests(version: str) -> list[Path]:
    """Every committed example ``*.json`` that loads as a ``version`` manifest.

    Non-manifest JSON committed under ``examples/`` (adapter inputs, metrics,
    Hydra configs) fails ``load_manifest`` and is skipped.
    """
    found: list[Path] = []
    for path in sorted(EXAMPLES.rglob("*.json")):
        try:
            loaded = load_manifest(path)
        except ManifestLoadError:
            continue
        if loaded.manifest.schema_version == version:
            found.append(path)
    return found


COMMITTED_0_1 = _discover_manifests("0.1")
COMMITTED_0_2 = _discover_manifests("0.2")

EXPECTED_0_1 = {
    "examples/comparability-recorded/baseline.json",
    "examples/comparability-recorded/target-comparable.json",
    "examples/comparability-recorded/target-drifted.json",
    "examples/comparability-recorded/target-insufficient.json",
    "examples/dogfood-mlp-sweep/runevidence.json",
    "examples/dogfood-sklearn/runevidence.json",
    "examples/generic-experiment/runevidence.json",
    "examples/hydra-sweep/runevidence.json",
}
EXPECTED_0_2 = {
    "examples/comparability-recorded-0.2/baseline.json",
    "examples/comparability-recorded-0.2/target-comparable.json",
    "examples/comparability-recorded-0.2/target-drifted.json",
    "examples/comparability-recorded-0.2/target-insufficient.json",
}


def _assert_no_0_2_keys(document: dict) -> None:
    for run in document.get("runs", []):
        assert ZERO_TWO_RUN_KEY not in run
    for artifact in document.get("artifacts", []):
        assert ZERO_TWO_ARTIFACT_KEY not in artifact
    for result in document.get("results", []):
        assert ZERO_TWO_RESULT_KEY not in result


# --------------------------------------------------------- frozen 0.1 artifact


def test_frozen_0_1_schema_is_the_published_0_1_artifact():
    assert SCHEMA_0_1["$id"] == "urn:runevidence:manifest:0.1"
    assert (
        SCHEMA_0_1["$schema"]
        == "https://json-schema.org/draft/2020-12/schema"
    )
    assert SCHEMA_0_1["properties"]["schema_version"]["const"] == "0.1"
    # Omitted schema_version is valid 0.1.
    assert "schema_version" not in SCHEMA_0_1.get("required", [])


def test_discovery_covers_every_committed_example_manifest():
    found_0_1 = {_manifest_id(path) for path in COMMITTED_0_1}
    found_0_2 = {_manifest_id(path) for path in COMMITTED_0_2}
    assert EXPECTED_0_1 <= found_0_1
    assert EXPECTED_0_2 <= found_0_2
    assert not (found_0_1 & found_0_2)


@pytest.mark.parametrize("path", COMMITTED_0_1, ids=_manifest_id)
def test_frozen_0_1_reader_accepts_actual_0_1_writer_output(path: Path):
    loaded = load_manifest(path)
    assert loaded.manifest.schema_version == "0.1"

    document = json.loads(dump_manifest(loaded.manifest))

    assert document["schema_version"] == "0.1"
    assert _errors(VALIDATOR_0_1, document) == []
    _assert_no_0_2_keys(document)


# -------------------------------------------------------- synthetic 0.1 writer


def _synthetic_0_1_source(*, declare_version: bool) -> dict:
    """A representative 0.1 manifest using non-default fields.

    Exercises params, tags, metadata, results, both relationship kinds, a
    volatile artifact, a directory artifact and finite float result values.
    """
    document: dict = {
        "project": {
            "id": "synthetic-0-1",
            "name": "Synthetic writer compatibility fixture",
            "description": "Non-default 0.1 fields for the frozen reader.",
            "tags": ["synthetic", "compat"],
            "metadata": {"owner": "gate", "nested": {"levels": [1, 2, 3]}},
        },
        "runs": [
            {
                "id": "train-run",
                "name": "training",
                "started_at": "2026-11-11T00:00:00Z",
                "params": {"learning_rate": 0.01, "epochs": 3},
                "metadata": {"framework": "none"},
            },
            {
                "id": "eval-run",
                "name": "evaluation",
                "started_at": "2026-11-11T01:02:03Z",
                "params": {"batch_size": 16, "shuffle": False},
                "metadata": {"evaluation": {"split": "test"}},
            },
        ],
        "artifacts": [
            {
                "id": "raw-data",
                "path": "data/raw.csv",
                "sha256": "a" * 64,
                "size_bytes": 128,
                "kind": "dataset",
                "run_id": None,
                "metadata": {"dataset_id": "raw-v1"},
            },
            {
                "id": "features",
                "path": "data/features.csv",
                "sha256": "b" * 64,
                "size_bytes": 256,
                "kind": "dataset",
                "run_id": "train-run",
                "metadata": {},
            },
            {
                "id": "run-log",
                "path": "logs/train.log",
                "volatile": True,
                "kind": "log",
                "run_id": "train-run",
                "metadata": {},
            },
            {
                "id": "checkpoint-dir",
                "path": "checkpoints/final",
                "artifact_type": "directory",
                "digest_format": "runevidence-directory-v1",
                "kind": "checkpoint",
                "run_id": "train-run",
                "metadata": {},
            },
        ],
        "results": [
            {
                "id": "accuracy-result",
                "run_id": "eval-run",
                "name": "accuracy",
                "value": 0.875,
                "artifact_id": None,
                "locator": None,
                "metadata": {
                    "metric_definition": {"definition_id": "accuracy-top1"}
                },
            },
            {
                "id": "loss-result",
                "run_id": "eval-run",
                "name": "loss",
                "value": 1.5,
                "artifact_id": None,
                "locator": None,
                "metadata": {},
            },
        ],
        "evidence": [
            {
                "id": "claim-1",
                "claim": "Accuracy is recorded.",
                "supports": ["accuracy-result"],
                "contradicts": [],
                "source": "synthetic fixture",
                "metadata": {"reviewed": True},
            }
        ],
        "relationships": [
            {
                "source": "raw-data",
                "target": "train-run",
                "type": "input_to",
                "note": None,
            },
            {
                "source": "features",
                "target": "eval-run",
                "type": "input_to",
                "note": None,
            },
            {
                "source": "features",
                "target": "raw-data",
                "type": "derived_from",
                "note": "feature build",
            },
            {
                "source": "eval-run",
                "target": "train-run",
                "type": "derived_from",
                "note": None,
            },
        ],
    }
    if declare_version:
        document["schema_version"] = "0.1"
    return document


@pytest.mark.parametrize(
    "declare_version",
    [True, False],
    ids=["declared-0.1", "omitted-version"],
)
def test_synthetic_0_1_writer_output_validates_against_frozen_reader(
    tmp_path: Path, declare_version: bool
):
    source = _synthetic_0_1_source(declare_version=declare_version)
    if declare_version:
        assert source["schema_version"] == "0.1"
    else:
        assert "schema_version" not in source

    manifest_path = tmp_path / "runevidence.json"
    manifest_path.write_text(json.dumps(source, indent=2) + "\n", encoding="utf-8")

    loaded = load_manifest(manifest_path)
    assert loaded.manifest.schema_version == "0.1"

    document = json.loads(dump_manifest(loaded.manifest))

    # Omitted version is written back as explicit 0.1.
    assert document["schema_version"] == "0.1"
    assert _errors(VALIDATOR_0_1, document) == []
    _assert_no_0_2_keys(document)

    # Non-default fields survive the writer unchanged enough for the reader.
    assert document["project"]["tags"] == ["synthetic", "compat"]
    assert document["project"]["metadata"] == {
        "owner": "gate",
        "nested": {"levels": [1, 2, 3]},
    }
    assert [run["params"] for run in document["runs"]] == [
        {"learning_rate": 0.01, "epochs": 3},
        {"batch_size": 16, "shuffle": False},
    ]
    assert any(
        artifact["id"] == "run-log" and artifact["volatile"] is True
        for artifact in document["artifacts"]
    )
    assert any(
        artifact["id"] == "checkpoint-dir"
        and artifact["artifact_type"] == "directory"
        and artifact["digest_format"] == "runevidence-directory-v1"
        for artifact in document["artifacts"]
    )
    values = [result["value"] for result in document["results"]]
    assert values == [0.875, 1.5]
    assert all(math.isfinite(value) for value in values)
    assert {relationship["type"] for relationship in document["relationships"]} == {
        "input_to",
        "derived_from",
    }
    assert len(document["evidence"]) == 1


# ----------------------------------------------- committed 0.2 writer output


def test_committed_0_2_schema_is_a_distinct_artifact():
    assert SCHEMA_0_2["$id"] == "urn:runevidence:manifest:0.2"
    assert (
        SCHEMA_0_2["$schema"]
        == "https://json-schema.org/draft/2020-12/schema"
    )
    assert SCHEMA_0_2["properties"]["schema_version"]["const"] == "0.2"
    # 0.2 is explicit: the declaration is required.
    assert "schema_version" in SCHEMA_0_2.get("required", [])
    assert SCHEMA_0_2["$id"] != SCHEMA_0_1["$id"]


@pytest.mark.parametrize("path", COMMITTED_0_2, ids=_manifest_id)
def test_committed_0_2_schema_accepts_actual_0_2_writer_output(path: Path):
    loaded = load_manifest(path)
    assert loaded.manifest.schema_version == "0.2"

    document = json.loads(dump_manifest(loaded.manifest))

    assert document["schema_version"] == "0.2"
    assert _errors(VALIDATOR_0_2, document) == []
    # The output really does carry the 0.2-only surface.
    assert any(ZERO_TWO_RUN_KEY in run for run in document["runs"])
    assert any(
        ZERO_TWO_ARTIFACT_KEY in artifact for artifact in document["artifacts"]
    )
    assert any(
        ZERO_TWO_RESULT_KEY in result for result in document["results"]
    )


@pytest.mark.parametrize("path", COMMITTED_0_2, ids=_manifest_id)
def test_frozen_0_1_reader_rejects_actual_0_2_writer_output(path: Path):
    loaded = load_manifest(path)
    document = json.loads(dump_manifest(loaded.manifest))
    assert _errors(VALIDATOR_0_1, document) != []
