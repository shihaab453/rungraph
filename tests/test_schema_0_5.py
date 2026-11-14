"""Schema 0.5 models, dispatch, diagnostics, serialization and artifacts.

Phase 1 pins the schema admission surface only: the TensorBoard event reader,
checker integration, adapter, migration and CLI texts are later phases.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from runevidence.manifest import (
    ManifestLoadError,
    _issue_category,
    _issue_location,
    artifact_to_dict,
    dump_manifest,
    load_manifest,
    validate_manifest,
)
from runevidence.models import (
    MAX_TENSORBOARD_TAG,
    Artifact,
    CsvCellLocator,
    JsonlLocator,
    Manifest,
    Manifest04,
    Manifest05,
    Result04,
    Result05,
    TensorboardLocator,
    is_manifest,
    json_schema_0_3,
    json_schema_0_4,
    json_schema_0_5,
    locator_to_json,
)

REPO = Path(__file__).resolve().parents[1]
SCHEMA_0_3 = REPO / "schema" / "runevidence-0.3.schema.json"
SCHEMA_0_4 = REPO / "schema" / "runevidence-0.4.schema.json"
SCHEMA_0_5 = REPO / "schema" / "runevidence-0.5.schema.json"
SCHEMA_0_3_SHA256 = "bd18dd053b6805b96b27cb8a9477dcbb9497d84ef6735c840c9aa2ebd0a544e8"
SCHEMA_0_4_SHA256 = "973099fb21c3d67a8775c6c0ceb3d2baeb6416df464b2b980f7148f90c962078"
SCHEMA_0_5_SHA256 = "af1c085eb50ab3d8111404c23503628c3af2433bfb5b0a956d75bee6f00935b4"


def doc(
    version: str = "0.5",
    locator=...,
    *,
    artifact_id="a",
    artifact_path="events.tfevents",
) -> dict:
    document = {
        "schema_version": version,
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "conditions": {"evaluation_split": "test"}}],
        "artifacts": [
            {
                "id": "a",
                "path": artifact_path,
                "sha256": "a" * 64,
                "size_bytes": 1,
                "kind": "dataset",
                "dataset_id": "toy-v1",
            }
        ],
        "results": [
            {
                "id": "r",
                "run_id": "run",
                "name": "accuracy",
                "value": 1,
                "artifact_id": artifact_id,
                "metric_definition": {"definition_id": "accuracy"},
            }
        ],
        "evidence": [],
        "relationships": [],
    }
    if locator is not ...:
        document["results"][0]["locator"] = locator
    return document


def tb(**updates) -> dict:
    value = {"kind": "tensorboard", "tag": "loss/val", "step": 40, "occurrence": 0}
    value.update(updates)
    return value


def write_doc(tmp_path: Path, document: dict, name: str = "runevidence.json") -> Path:
    path = tmp_path / name
    # ensure_ascii keeps even a lone surrogate representable in the file.
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


# The RFC §4.3 diagnostic table, asserted through both the model and the
# loader so the `issue_location` and `issue_category` fields are covered.
DIAGNOSTICS = (
    (
        "missing-tag",
        {"kind": "tensorboard", "step": 40, "occurrence": 0},
        "missing",
        "/results/0/locator/tag",
        "missing_required",
    ),
    (
        "missing-step",
        {"kind": "tensorboard", "tag": "loss/val", "occurrence": 0},
        "missing",
        "/results/0/locator/step",
        "missing_required",
    ),
    (
        "missing-occurrence",
        {"kind": "tensorboard", "tag": "loss/val", "step": 40},
        "missing",
        "/results/0/locator/occurrence",
        "missing_required",
    ),
    (
        "tag-4096-code-points-over-4096-bytes",
        tb(tag="\u00e9" * 4096),
        "value_error",
        "/results/0/locator/tag",
        "invalid_value",
    ),
    (
        "step-float",
        tb(step=1.0),
        "int_type",
        "/results/0/locator/step",
        "invalid_type",
    ),
    (
        "step-2**63",
        tb(step=2**63),
        "less_than_equal",
        "/results/0/locator/step",
        "invalid_value",
    ),
    (
        "step--2**63-1",
        tb(step=-(2**63) - 1),
        "greater_than_equal",
        "/results/0/locator/step",
        "invalid_value",
    ),
    (
        "occurrence--1",
        tb(occurrence=-1),
        "greater_than_equal",
        "/results/0/locator/occurrence",
        "invalid_value",
    ),
    (
        "occurrence-2**31",
        tb(occurrence=2**31),
        "less_than_equal",
        "/results/0/locator/occurrence",
        "invalid_value",
    ),
    (
        "empty-tag",
        tb(tag=""),
        "string_too_short",
        "/results/0/locator/tag",
        "invalid_value",
    ),
    (
        "tag-over-4096-code-points",
        tb(tag="x" * 4097),
        "string_too_long",
        "/results/0/locator/tag",
        "invalid_value",
    ),
    (
        "unknown-kind",
        tb(kind="tensorboarx"),
        "union_tag_invalid",
        "/results/0/locator/kind",
        "invalid_value",
    ),
    (
        "missing-kind",
        {"tag": "loss/val", "step": 40, "occurrence": 0},
        "union_tag_not_found",
        "/results/0/locator/kind",
        "missing_required",
    ),
    (
        "extra-key",
        tb(extra=1),
        "extra_forbidden",
        None,
        "invalid_value",
    ),
    (
        "scalar",
        5,
        "value_error",
        "/results/0/locator",
        "invalid_value",
    ),
    (
        "lone-surrogate",
        tb(tag="loss/\ud800val"),
        "string_unicode",
        "/results/0/locator/tag",
        "other_validation",
    ),
)


@pytest.mark.parametrize(
    ("name", "locator", "error_type", "location", "category"),
    DIAGNOSTICS,
    ids=[case[0] for case in DIAGNOSTICS],
)
def test_0_5_locator_diagnostics(
    tmp_path, name, locator, error_type, location, category
):
    document = doc(locator=locator)

    with pytest.raises(ValidationError) as caught:
        Manifest05.model_validate(document)
    (error,) = caught.value.errors()
    assert error["type"] == error_type
    assert (
        _issue_location(
            error["loc"], root_model=Manifest05, error_type=error["type"]
        )
        == location
    )
    assert _issue_category(error["type"]) == category

    path = write_doc(tmp_path, document, f"{name}.json")
    with pytest.raises(ManifestLoadError) as load_error:
        load_manifest(path)
    (finding,) = load_error.value.findings
    assert finding.code == "schema-invalid"
    assert finding.issue_location == location
    assert finding.issue_category == category
    where = f" at {location}" if location else ""
    assert finding.message == (
        f"Manifest schema validation failed{where}: {category.replace('_', ' ')}"
    )


def test_issue_category_pins_the_new_error_types():
    assert _issue_category("union_tag_invalid") == "invalid_value"
    assert _issue_category("union_tag_not_found") == "missing_required"
    assert _issue_category("string_unicode") == "other_validation"


def test_0_5_tag_boundaries_are_accepted():
    for tag in ("x" * MAX_TENSORBOARD_TAG, "\u00e9" * (MAX_TENSORBOARD_TAG // 2)):
        locator = TensorboardLocator(
            kind="tensorboard", tag=tag, step=0, occurrence=0
        )
        assert locator.tag == tag

    result = Result05.model_validate(
        {
            "id": "r",
            "run_id": "run",
            "name": "accuracy",
            "artifact_id": "a",
            "locator": TensorboardLocator(
                kind="tensorboard",
                tag="loss/val",
                step=-(2**63),
                occurrence=2**31 - 1,
            ),
        }
    )
    assert result.locator.step == -(2**63)
    assert result.locator.occurrence == 2**31 - 1


def test_0_5_lone_surrogate_is_rejected_on_a_hand_built_locator():
    with pytest.raises(ValidationError) as caught:
        TensorboardLocator(
            kind="tensorboard", tag="loss/\ud800val", step=0, occurrence=0
        )
    (error,) = caught.value.errors()
    assert error["type"] == "string_unicode"
    assert _issue_category(error["type"]) == "other_validation"


def test_0_5_walker_needs_the_version_root_for_tensorboard_tags():
    # The default root is the 0.3 Manifest, whose locator union has no
    # tensorboard option; forwarding Manifest05 is what preserves the location.
    loc = ("results", 0, "locator", "tensorboard", "tag")
    assert _issue_location(loc) is None
    assert _issue_location(loc, root_model=Manifest05) == "/results/0/locator/tag"


# The delivered 0.3/0.4 diagnostics stay on their own paths.
def test_0_3_csv_cell_object_is_still_seven_findings():
    document = doc(
        "0.3",
        {"kind": "csv-cell", "row": 1, "column": 2, "header": "val_rmse"},
        artifact_path="metrics.csv",
    )
    with pytest.raises(ValidationError) as caught:
        Manifest.model_validate(document)
    errors = caught.value.errors()

    assert len(errors) == 7
    assert [
        (error["type"], _issue_location(error["loc"], error_type=error["type"]))
        for error in errors
    ] == [
        ("string_type", "/results/0/locator"),
        ("literal_error", "/results/0/locator/kind"),
        ("missing", "/results/0/locator/line"),
        ("missing", "/results/0/locator/pointer"),
        ("extra_forbidden", None),
        ("extra_forbidden", None),
        ("extra_forbidden", None),
    ]


def test_0_4_csv_cell_diagnostic_is_unchanged(tmp_path):
    document = doc(
        "0.4",
        {"kind": "csv-cell", "row": 0, "column": 2, "header": "val_rmse"},
        artifact_path="metrics.csv",
    )
    with pytest.raises(ValidationError) as caught:
        Manifest04.model_validate(document)
    (error,) = caught.value.errors()
    assert error["type"] == "greater_than_equal"
    assert (
        _issue_location(
            error["loc"], root_model=Manifest04, error_type=error["type"]
        )
        == "/results/0/locator/row"
    )
    assert _issue_category(error["type"]) == "invalid_value"

    with pytest.raises(ManifestLoadError) as load_error:
        load_manifest(write_doc(tmp_path, document))
    (finding,) = load_error.value.findings
    assert finding.issue_location == "/results/0/locator/row"
    assert finding.issue_category == "invalid_value"


# Every 0.5 locator form loads through the dispatcher.
def test_0_5_no_results_manifest_loads(tmp_path):
    for document in (
        {"schema_version": "0.5", "project": {"id": "p", "name": "P"}},
        {
            "schema_version": "0.5",
            "project": {"id": "p", "name": "P"},
            "results": [],
        },
    ):
        path = write_doc(tmp_path, document)
        loaded = load_manifest(path)
        assert isinstance(loaded.manifest, Manifest05)
        assert is_manifest(loaded.manifest)
        assert loaded.manifest.schema_version == "0.5"
        assert loaded.manifest.results == []


@pytest.mark.parametrize("locator", [..., None])
def test_0_5_locator_absent_or_null_loads(tmp_path, locator):
    document = doc(locator=locator)
    path = write_doc(tmp_path, document)
    loaded = load_manifest(path)
    assert loaded.manifest.results[0].locator is None


@pytest.mark.parametrize(
    ("locator", "expected_type", "expected_value"),
    [
        ("/eval/accuracy", str, "/eval/accuracy"),
        ("", str, ""),
        (
            {"kind": "jsonl", "line": 41, "pointer": "/val"},
            JsonlLocator,
            ("jsonl", 41, "/val"),
        ),
        (
            {"kind": "csv-cell", "row": 41, "column": 4, "header": "val_rmse"},
            CsvCellLocator,
            ("csv-cell", 41, 4, "val_rmse"),
        ),
        (
            {"kind": "tensorboard", "tag": "loss/val", "step": 40, "occurrence": 0},
            TensorboardLocator,
            ("tensorboard", "loss/val", 40, 0),
        ),
    ],
)
def test_0_5_locator_forms_load(
    tmp_path, locator, expected_type, expected_value
):
    document = doc(locator=locator)
    path = write_doc(tmp_path, document)
    loaded = load_manifest(path)
    found = loaded.manifest.results[0].locator
    assert type(found) is expected_type
    if expected_type is CsvCellLocator:
        assert (found.kind, found.row, found.column, found.header) == expected_value
    elif expected_type is JsonlLocator:
        assert (found.kind, found.line, found.pointer) == expected_value
    elif expected_type is TensorboardLocator:
        assert (
            found.kind,
            found.tag,
            found.step,
            found.occurrence,
        ) == expected_value
    else:
        assert found == expected_value


def test_validate_manifest_dispatches_only_on_an_explicit_0_5_source():
    assert type(validate_manifest(doc("0.5", tb()))) is Manifest05
    assert type(validate_manifest(doc("0.4", "/v"))) is Manifest04
    assert type(validate_manifest(doc("0.3", "/v"))) is Manifest

    # The base Manifest stays 0.1-0.3: omitting schema_version does not make
    # a tensorboard locator valid.
    document = doc("0.5", tb())
    document.pop("schema_version")
    with pytest.raises(ValidationError):
        validate_manifest(document)


def test_result05_accepts_a_constructed_tensorboard_locator():
    locator = TensorboardLocator(
        kind="tensorboard", tag="loss/val", step=40, occurrence=0
    )
    result = Result05.model_validate(
        {
            "id": "r",
            "run_id": "run",
            "name": "metric",
            "artifact_id": "a",
            "locator": locator,
        }
    )
    assert result.locator is locator


def test_0_3_and_0_4_reject_a_constructed_tensorboard_locator():
    locator = TensorboardLocator(
        kind="tensorboard", tag="loss/val", step=40, occurrence=0
    )
    with pytest.raises(ValidationError):
        Manifest.model_validate(doc("0.3", locator))
    with pytest.raises(ValidationError):
        Result04.model_validate(
            {
                "id": "r",
                "run_id": "run",
                "name": "metric",
                "artifact_id": "a",
                "locator": locator,
            }
        )


def test_0_3_locator_instance_boundary_is_unchanged():
    jsonl = JsonlLocator(kind="jsonl", line=1, pointer="")
    manifest = Manifest.model_validate(doc("0.3", jsonl))
    assert manifest.results[0].locator is jsonl

    csv_locator = CsvCellLocator(
        kind="csv-cell", row=1, column=2, header="val_rmse"
    )
    with pytest.raises(ValidationError):
        Manifest.model_validate(doc("0.3", csv_locator))


@pytest.mark.parametrize(
    "locator",
    [
        "/eval/accuracy",
        {"kind": "jsonl", "line": 1, "pointer": ""},
        {"kind": "csv-cell", "row": 1, "column": 2, "header": "val_rmse"},
        tb(),
    ],
    ids=["string", "jsonl", "csv-cell", "tensorboard"],
)
def test_0_5_non_null_locator_requires_artifact_id(locator):
    for missing in ("absent", "null"):
        document = doc(locator=locator, artifact_path="metrics.csv")
        if missing == "absent":
            document["results"][0].pop("artifact_id")
        else:
            document["results"][0]["artifact_id"] = None
        with pytest.raises(ValidationError) as caught:
            Manifest05.model_validate(document)
        assert "requires artifact_id" in str(caught.value)


# Serialization preserves 0.2+ fields and writes every tensorboard field.
def test_dump_manifest_round_trips_0_5_with_a_tensorboard_locator(tmp_path):
    manifest = Manifest05.model_validate(doc(locator=tb()))
    locator = manifest.results[0].locator
    assert locator_to_json(locator) == {
        "kind": "tensorboard",
        "tag": "loss/val",
        "step": 40,
        "occurrence": 0,
    }

    text = dump_manifest(manifest)
    data = json.loads(text)
    assert data["schema_version"] == "0.5"
    assert data["results"][0]["locator"] == {
        "kind": "tensorboard",
        "tag": "loss/val",
        "step": 40,
        "occurrence": 0,
    }
    assert data["runs"][0]["conditions"]["evaluation_split"] == "test"
    assert data["artifacts"][0]["dataset_id"] == "toy-v1"
    assert data["results"][0]["metric_definition"]["definition_id"] == "accuracy"

    path = tmp_path / "runevidence.json"
    path.write_text(text, encoding="utf-8", newline="\n")
    loaded = load_manifest(path)
    assert isinstance(loaded.manifest, Manifest05)
    assert dump_manifest(loaded.manifest) == text


def test_dump_manifest_round_trips_a_0_4_to_0_5_version_bump(tmp_path):
    raw = doc(
        "0.4",
        {"kind": "csv-cell", "row": 1, "column": 2, "header": "val_rmse"},
        artifact_path="metrics.csv",
    )
    original = Manifest04.model_validate(raw)
    assert isinstance(original, Manifest04)

    raw["schema_version"] = "0.5"
    bumped = validate_manifest(raw)
    assert type(bumped) is Manifest05
    assert isinstance(bumped.results[0].locator, CsvCellLocator)

    text = dump_manifest(bumped)
    data = json.loads(text)
    assert data["schema_version"] == "0.5"
    assert data["runs"][0]["conditions"]["evaluation_split"] == "test"
    assert data["artifacts"][0]["dataset_id"] == "toy-v1"
    assert data["results"][0]["metric_definition"]["definition_id"] == "accuracy"
    assert data["results"][0]["locator"] == {
        "kind": "csv-cell",
        "row": 1,
        "column": 2,
        "header": "val_rmse",
    }

    path = tmp_path / "runevidence.json"
    path.write_text(text, encoding="utf-8", newline="\n")
    loaded = load_manifest(path)
    assert type(loaded.manifest) is Manifest05
    assert dump_manifest(loaded.manifest) == text


def test_dump_manifest_round_trips_a_0_5_no_results_manifest(tmp_path):
    manifest = Manifest05.model_validate(
        {"schema_version": "0.5", "project": {"id": "p", "name": "P"}}
    )
    text = dump_manifest(manifest)
    assert json.loads(text)["results"] == []

    path = tmp_path / "runevidence.json"
    path.write_text(text, encoding="utf-8", newline="\n")
    loaded = load_manifest(path)
    assert loaded.manifest.results == []
    assert dump_manifest(loaded.manifest) == text


def test_artifact_to_dict_accepts_0_5():
    labelled = Artifact(id="a", path="events.tfevents", dataset_id="toy-v1")
    data = artifact_to_dict(labelled, schema_version="0.5")
    assert data["dataset_id"] == "toy-v1"

    plain = Artifact(id="a", path="events.tfevents")
    assert "dataset_id" not in artifact_to_dict(plain, schema_version="0.5")


# Schema artifacts and the 0.5 shape.
def test_0_3_and_0_4_schema_bytes_are_unchanged():
    assert hashlib.sha256(SCHEMA_0_3.read_bytes()).hexdigest() == SCHEMA_0_3_SHA256
    assert hashlib.sha256(SCHEMA_0_4.read_bytes()).hexdigest() == SCHEMA_0_4_SHA256
    assert (
        json.dumps(json_schema_0_3(), indent=2, ensure_ascii=False).encode() + b"\n"
        == SCHEMA_0_3.read_bytes()
    )
    assert (
        json.dumps(json_schema_0_4(), indent=2, ensure_ascii=False).encode() + b"\n"
        == SCHEMA_0_4.read_bytes()
    )


def test_0_5_schema_artifact_is_generated_and_pinned():
    generated = json.dumps(json_schema_0_5(), indent=2, ensure_ascii=False)
    assert generated.encode() + b"\n" == SCHEMA_0_5.read_bytes()
    assert hashlib.sha256(SCHEMA_0_5.read_bytes()).hexdigest() == SCHEMA_0_5_SHA256


def _descriptions(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                yield path, value
            yield from _descriptions(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _descriptions(value, f"{path}/{index}")


def test_0_5_schema_shape_and_descriptions():
    schema = json_schema_0_5()
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == "urn:runevidence:manifest:0.5"
    assert schema["title"] == "Manifest05"
    assert schema["properties"]["schema_version"]["const"] == "0.5"
    assert schema["properties"]["schema_version"]["default"] == "0.5"
    assert "schema_version" in schema["required"]
    defs = schema["$defs"]
    assert {"Result05", "JsonlLocator", "CsvCellLocator", "TensorboardLocator"} <= set(defs)

    locator = defs["Result05"]["properties"]["locator"]
    assert len(locator["oneOf"]) == 5
    assert locator["oneOf"][0]["type"] == "string"
    assert locator["oneOf"][0]["pattern"] == (
        r"^(?![\s\S]*[\r\n\u2028\u2029])(?:/(?:[^~]|~[01])*)?$"
    )
    assert locator["oneOf"][1] == {"$ref": "#/$defs/JsonlLocator"}
    assert locator["oneOf"][2] == {"$ref": "#/$defs/CsvCellLocator"}
    assert locator["oneOf"][3] == {"$ref": "#/$defs/TensorboardLocator"}
    assert locator["oneOf"][4] == {"type": "null"}
    assert locator["default"] is None
    assert "anyOf" not in locator
    assert "In 0.5 any non-null locator requires artifact_id" in locator["description"]

    tensorboard = defs["TensorboardLocator"]
    assert tensorboard["additionalProperties"] is False
    assert tensorboard["properties"]["kind"]["const"] == "tensorboard"
    assert tensorboard["properties"]["tag"]["minLength"] == 1
    assert tensorboard["properties"]["tag"]["maxLength"] == MAX_TENSORBOARD_TAG
    assert tensorboard["properties"]["step"]["type"] == "integer"
    assert tensorboard["properties"]["step"]["minimum"] == -(2**63)
    assert tensorboard["properties"]["step"]["maximum"] == 2**63 - 1
    assert tensorboard["properties"]["occurrence"]["type"] == "integer"
    assert tensorboard["properties"]["occurrence"]["minimum"] == 0
    assert tensorboard["properties"]["occurrence"]["maximum"] == 2**31 - 1
    assert set(tensorboard["required"]) == {
        "kind",
        "tag",
        "step",
        "occurrence",
    }

    assert defs["Result05"]["if"]["required"] == ["locator"]
    assert defs["Result05"]["then"]["required"] == ["artifact_id"]
    assert defs["Result05"]["then"]["properties"]["artifact_id"] == {
        "type": "string"
    }

    for path, description in _descriptions(schema):
        assert "0.3" not in description, path
        assert "0.4" not in description, path
    assert "0.5" in defs["JsonlLocator"]["description"]
    assert "0.5" in defs["CsvCellLocator"]["description"]
    assert "0.5" in defs["TensorboardLocator"]["description"]


def test_0_5_schema_accepts_and_rejects_the_locator_boundary():
    validator = jsonschema.Draft202012Validator(json_schema_0_5())
    assert list(validator.iter_errors(doc("0.5", tb()))) == []
    assert list(validator.iter_errors(doc("0.5", "/eval/accuracy"))) == []
    assert list(validator.iter_errors(doc("0.5", None))) == []
    assert list(validator.iter_errors(doc("0.5", {"kind": "jsonl", "line": 1, "pointer": ""}))) == []
    assert list(
        validator.iter_errors(
            doc(
                "0.5",
                {"kind": "csv-cell", "row": 1, "column": 2, "header": "val_rmse"},
                artifact_path="metrics.csv",
            )
        )
    ) == []

    assert list(validator.iter_errors(doc("0.5", tb(step=2**63))))
    assert list(validator.iter_errors(doc("0.5", tb(step=-(2**63) - 1))))
    assert list(validator.iter_errors(doc("0.5", tb(occurrence=-1))))
    assert list(validator.iter_errors(doc("0.5", tb(occurrence=2**31))))
    assert list(validator.iter_errors(doc("0.5", tb(tag=""))))
    assert list(validator.iter_errors(doc("0.5", tb(tag="x" * 4097))))
    assert list(validator.iter_errors(doc("0.5", tb(kind="tensorboarx"))))
    assert list(validator.iter_errors(doc("0.5", tb(extra=1))))
    assert list(validator.iter_errors(doc("0.5", tb(), artifact_id=None)))

    # Documented schema/runtime difference: draft 2020-12 accepts 1.0 for an
    # integer and counts maxLength in code points; the model rejects both.
    assert list(validator.iter_errors(doc("0.5", tb(step=1.0)))) == []
    assert list(validator.iter_errors(doc("0.5", tb(occurrence=1.0)))) == []
    assert list(
        validator.iter_errors(doc("0.5", tb(tag="\u00e9" * MAX_TENSORBOARD_TAG)))
    ) == []

    with pytest.raises(ValidationError):
        Manifest05.model_validate(doc("0.5", tb(step=1.0)))
    with pytest.raises(ValidationError):
        Manifest05.model_validate(doc("0.5", tb(tag="\u00e9" * MAX_TENSORBOARD_TAG)))
