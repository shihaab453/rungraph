"""Schema 0.4 models, dispatch, diagnostics, serialization and artifacts.

Phase 1 pins the schema admission surface only: ``csv-cell`` verification,
migration, CLI texts and display are later phases.
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
    MAX_CSV_CELL_HEADER,
    MAX_JSONL_LINE,
    Artifact,
    CsvCellLocator,
    JsonlLocator,
    Manifest,
    Manifest04,
    Result04,
    is_manifest,
    json_schema_0_3,
    json_schema_0_4,
)

REPO = Path(__file__).resolve().parents[1]
SCHEMA_0_3 = REPO / "schema" / "runevidence-0.3.schema.json"
SCHEMA_0_4 = REPO / "schema" / "runevidence-0.4.schema.json"
SCHEMA_0_3_SHA256 = "bd18dd053b6805b96b27cb8a9477dcbb9497d84ef6735c840c9aa2ebd0a544e8"


def doc(
    version: str = "0.4",
    locator=...,
    *,
    artifact_id="a",
    artifact_path="metrics.csv",
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


def csv_cell(**updates) -> dict:
    value = {"kind": "csv-cell", "row": 1, "column": 2, "header": "val_rmse"}
    value.update(updates)
    return value


def write_doc(tmp_path: Path, document: dict, name: str = "runevidence.json") -> Path:
    path = tmp_path / name
    # ensure_ascii keeps even a lone surrogate representable in the file.
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


# (a) The five pinned 0.4 diagnostics, asserted through both the model and the
# loader so the `issue_location` field is covered.
DIAGNOSTICS = (
    (
        "row-zero",
        csv_cell(row=0),
        "greater_than_equal",
        "/results/0/locator/row",
        "invalid_value",
    ),
    (
        "unknown-kind",
        csv_cell(kind="csvx"),
        "union_tag_invalid",
        "/results/0/locator/kind",
        "invalid_value",
    ),
    (
        "missing-kind",
        {"row": 1, "column": 2, "header": "val_rmse"},
        "union_tag_not_found",
        "/results/0/locator/kind",
        "missing_required",
    ),
    (
        "extra-key",
        csv_cell(extra=1),
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
)


@pytest.mark.parametrize(
    ("name", "locator", "error_type", "location", "category"),
    DIAGNOSTICS,
    ids=[case[0] for case in DIAGNOSTICS],
)
def test_0_4_locator_diagnostics(
    tmp_path, name, locator, error_type, location, category
):
    document = doc(locator=locator)

    with pytest.raises(ValidationError) as caught:
        Manifest04.model_validate(document)
    (error,) = caught.value.errors()
    assert error["type"] == error_type
    assert (
        _issue_location(
            error["loc"], root_model=Manifest04, error_type=error["type"]
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


def test_0_4_jsonl_diagnostic_tag_maps_before_branch_labels():
    document = doc(locator={"kind": "jsonl", "line": 0, "pointer": ""})
    with pytest.raises(ValidationError) as caught:
        Manifest04.model_validate(document)
    (error,) = caught.value.errors()
    assert error["type"] == "greater_than_equal"
    assert (
        _issue_location(error["loc"], root_model=Manifest04, error_type=error["type"])
        == "/results/0/locator/line"
    )


def test_0_4_walker_needs_the_version_root_for_locator_tags():
    # The default root is the 0.3 Manifest, whose locator union has no
    # csv-cell option; forwarding Manifest04 is what preserves the location.
    loc = ("results", 0, "locator", "csv-cell", "row")
    assert _issue_location(loc) is None
    assert _issue_location(loc, root_model=Manifest04) == "/results/0/locator/row"


# (b) The delivered 0.3 diagnostics stay seven findings for a csv-cell object.
def test_0_3_csv_cell_object_is_seven_findings():
    document = doc("0.3", csv_cell())
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
    assert [_issue_category(error["type"]) for error in errors] == [
        "invalid_type",
        "invalid_value",
        "missing_required",
        "missing_required",
        "invalid_value",
        "invalid_value",
        "invalid_value",
    ]


def test_0_3_csv_cell_findings_keep_the_result_index():
    document = doc("0.3")
    document["results"] = [
        {"id": "plain", "run_id": "run", "name": "plain", "value": 1},
        {
            "id": "csv",
            "run_id": "run",
            "name": "metric",
            "value": 1,
            "artifact_id": "a",
            "locator": csv_cell(),
        },
    ]
    with pytest.raises(ValidationError) as caught:
        Manifest.model_validate(document)
    errors = caught.value.errors()
    assert len(errors) == 7
    assert _issue_location(errors[0]["loc"]) == "/results/1/locator"
    assert _issue_location(errors[1]["loc"]) == "/results/1/locator/kind"
    assert [_issue_location(error["loc"]) for error in errors[2:4]] == [
        "/results/1/locator/line",
        "/results/1/locator/pointer",
    ]
    assert all(_issue_location(error["loc"]) is None for error in errors[4:])


def test_0_3_locator_instance_boundary_is_unchanged():
    csv_locator = CsvCellLocator(
        kind="csv-cell", row=1, column=2, header="val_rmse"
    )
    with pytest.raises(ValidationError):
        Manifest.model_validate(doc("0.3", csv_locator))

    jsonl = JsonlLocator(kind="jsonl", line=1, pointer="")
    manifest = Manifest.model_validate(doc("0.3", jsonl))
    assert manifest.results[0].locator is jsonl


def test_result04_accepts_both_locator_instances():
    csv_locator = CsvCellLocator(
        kind="csv-cell", row=1, column=2, header="val_rmse"
    )
    jsonl = JsonlLocator(kind="jsonl", line=1, pointer="/v")
    for locator in (csv_locator, jsonl):
        result = Result04.model_validate(
            {
                "id": "r",
                "run_id": "run",
                "name": "metric",
                "artifact_id": "a",
                "locator": locator,
            }
        )
        assert result.locator is locator


# (c) Every 0.4 locator form loads through the dispatcher.
def test_0_4_no_results_manifest_loads(tmp_path):
    for document in (
        {"schema_version": "0.4", "project": {"id": "p", "name": "P"}},
        {
            "schema_version": "0.4",
            "project": {"id": "p", "name": "P"},
            "results": [],
        },
    ):
        path = write_doc(tmp_path, document)
        loaded = load_manifest(path)
        assert isinstance(loaded.manifest, Manifest04)
        assert is_manifest(loaded.manifest)
        assert loaded.manifest.schema_version == "0.4"
        assert loaded.manifest.results == []


@pytest.mark.parametrize("locator", [..., None])
def test_0_4_locator_absent_or_null_loads(tmp_path, locator):
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
    ],
)
def test_0_4_locator_forms_load(
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
    else:
        assert found == expected_value


def test_validate_manifest_dispatches_only_on_a_0_4_source():
    assert type(validate_manifest(doc("0.4", csv_cell()))) is Manifest04
    assert type(validate_manifest(doc("0.3", "/v"))) is Manifest
    assert type(validate_manifest(doc("0.2", "/v"))) is Manifest


# (d) A lone surrogate is refused in `header` only.
def test_0_4_lone_surrogate_in_header_is_model_rejected(tmp_path):
    document = doc(locator=csv_cell(header="val_\ud800rmse"))

    with pytest.raises(ValidationError) as caught:
        Manifest04.model_validate(document)
    (error,) = caught.value.errors()
    assert error["type"] == "string_unicode"
    assert _issue_category(error["type"]) == "other_validation"
    assert _issue_location(
        error["loc"], root_model=Manifest04, error_type=error["type"]
    ) == "/results/0/locator/header"

    with pytest.raises(ValidationError) as caught:
        CsvCellLocator(
            kind="csv-cell", row=1, column=2, header="val_\ud800rmse"
        )
    (error,) = caught.value.errors()
    assert error["type"] == "string_unicode"
    assert _issue_category(error["type"]) == "other_validation"

    path = write_doc(tmp_path, document)
    with pytest.raises(ManifestLoadError) as load_error:
        load_manifest(path)
    (finding,) = load_error.value.findings
    assert finding.issue_category == "other_validation"
    assert finding.message == (
        "Manifest schema validation failed at /results/0/locator/header: "
        "other validation"
    )


def test_0_4_string_and_jsonl_pointers_may_contain_a_lone_surrogate(tmp_path):
    string_document = doc(locator="/\ud800")
    string_manifest = Manifest04.model_validate(string_document)
    assert string_manifest.results[0].locator == "/\ud800"
    loaded = load_manifest(write_doc(tmp_path, string_document, "string.json"))
    assert loaded.manifest.results[0].locator == "/\ud800"

    jsonl_document = doc(
        locator={"kind": "jsonl", "line": 1, "pointer": "/\ud800"}
    )
    jsonl_manifest = Manifest04.model_validate(jsonl_document)
    assert jsonl_manifest.results[0].locator.pointer == "/\ud800"
    loaded = load_manifest(write_doc(tmp_path, jsonl_document, "jsonl.json"))
    assert loaded.manifest.results[0].locator.pointer == "/\ud800"


# (e) Schema artifacts and the 0.4 shape.
def test_0_3_schema_bytes_are_unchanged():
    assert hashlib.sha256(SCHEMA_0_3.read_bytes()).hexdigest() == SCHEMA_0_3_SHA256
    assert (
        json.dumps(json_schema_0_3(), indent=2, ensure_ascii=False).encode() + b"\n"
        == SCHEMA_0_3.read_bytes()
    )


def test_0_4_schema_artifact_is_generated_and_pinned():
    generated = json.dumps(json_schema_0_4(), indent=2, ensure_ascii=False)
    assert generated.encode() + b"\n" == SCHEMA_0_4.read_bytes()
    assert hashlib.sha256(SCHEMA_0_4.read_bytes()).hexdigest() == (
        "973099fb21c3d67a8775c6c0ceb3d2baeb6416df464b2b980f7148f90c962078"
    )


def test_0_4_schema_shape_and_descriptions():
    schema = json_schema_0_4()
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == "urn:runevidence:manifest:0.4"
    assert schema["title"] == "Manifest04"
    assert schema["properties"]["schema_version"]["const"] == "0.4"
    assert schema["properties"]["schema_version"]["default"] == "0.4"
    defs = schema["$defs"]
    assert {"Result04", "JsonlLocator", "CsvCellLocator"} <= set(defs)

    locator = defs["Result04"]["properties"]["locator"]
    assert len(locator["oneOf"]) == 4
    assert locator["oneOf"][0]["type"] == "string"
    assert locator["oneOf"][0]["pattern"] == (
        r"^(?![\s\S]*[\r\n\u2028\u2029])(?:/(?:[^~]|~[01])*)?$"
    )
    assert locator["oneOf"][1] == {"$ref": "#/$defs/JsonlLocator"}
    assert locator["oneOf"][2] == {"$ref": "#/$defs/CsvCellLocator"}
    assert locator["oneOf"][3] == {"type": "null"}
    assert locator["default"] is None
    assert "anyOf" not in locator
    assert "In 0.4 any non-null locator requires artifact_id" in locator["description"]

    csv_schema = defs["CsvCellLocator"]
    assert csv_schema["additionalProperties"] is False
    assert csv_schema["properties"]["kind"]["const"] == "csv-cell"
    assert csv_schema["properties"]["row"]["minimum"] == 1
    assert csv_schema["properties"]["row"]["maximum"] == MAX_JSONL_LINE
    assert csv_schema["properties"]["column"]["minimum"] == 1
    assert csv_schema["properties"]["column"]["maximum"] == MAX_JSONL_LINE
    assert csv_schema["properties"]["header"]["minLength"] == 1
    assert csv_schema["properties"]["header"]["maxLength"] == MAX_CSV_CELL_HEADER
    assert defs["Result04"]["if"]["required"] == ["locator"]
    assert defs["Result04"]["then"]["required"] == ["artifact_id"]
    assert defs["Result04"]["then"]["properties"]["artifact_id"] == {
        "type": "string"
    }

    jsonl_schema = defs["JsonlLocator"]
    assert jsonl_schema["properties"]["pointer"]["pattern"] == (
        r"^(?![\s\S]*[\r\n\u2028\u2029])(?:/(?:[^~]|~[01])*)?$"
    )
    for description in (
        locator["description"],
        jsonl_schema["description"],
        jsonl_schema["properties"]["kind"]["description"],
    ):
        assert "0.3" not in description
        assert "0.4" in description
    assert "0.4 admits ``jsonl``; the locator union admits ``jsonl``/``csv-cell``" in (
        jsonl_schema["description"]
    )
    assert jsonl_schema["properties"]["kind"]["description"] == (
        "Locator kind; 0.4 admits jsonl; the locator union admits jsonl/csv-cell."
    )


def test_0_4_schema_accepts_and_rejects_the_locator_boundary():
    schema = jsonschema.Draft202012Validator(json_schema_0_4())
    assert list(schema.iter_errors(doc("0.4", csv_cell()))) == []
    assert list(schema.iter_errors(doc("0.4", "/eval/accuracy"))) == []
    assert list(schema.iter_errors(doc("0.4", None))) == []
    assert list(schema.iter_errors(doc("0.4", {"kind": "jsonl", "line": 1, "pointer": ""}))) == []

    assert list(schema.iter_errors(doc("0.4", csv_cell(row=0))))
    assert list(schema.iter_errors(doc("0.4", csv_cell(column=MAX_JSONL_LINE + 1))))
    assert list(schema.iter_errors(doc("0.4", csv_cell(kind="csvx"))))
    assert list(schema.iter_errors(doc("0.4", csv_cell(header=""))))
    assert list(schema.iter_errors(doc("0.4", csv_cell(extra=1))))
    assert list(schema.iter_errors(doc("0.4", csv_cell(), artifact_id=None)))
    assert list(schema.iter_errors(doc("0.4", "/a\n")))


# (f) Serialization preserves 0.2/0.3 fields and never wraps a string locator.
def test_dump_manifest_round_trips_0_4_with_a_string_locator(tmp_path):
    manifest = Manifest04.model_validate(doc(locator="/eval/accuracy"))
    text = dump_manifest(manifest)
    data = json.loads(text)
    assert data["schema_version"] == "0.4"
    assert data["results"][0]["locator"] == "/eval/accuracy"
    assert data["runs"][0]["conditions"]["evaluation_split"] == "test"
    assert data["artifacts"][0]["dataset_id"] == "toy-v1"
    assert data["results"][0]["metric_definition"]["definition_id"] == "accuracy"

    path = tmp_path / "runevidence.json"
    path.write_text(text, encoding="utf-8", newline="\n")
    loaded = load_manifest(path)
    assert isinstance(loaded.manifest, Manifest04)
    assert dump_manifest(loaded.manifest) == text


def test_dump_manifest_round_trips_a_0_4_no_results_manifest(tmp_path):
    manifest = Manifest04.model_validate(
        {"schema_version": "0.4", "project": {"id": "p", "name": "P"}}
    )
    text = dump_manifest(manifest)
    assert json.loads(text)["results"] == []

    path = tmp_path / "runevidence.json"
    path.write_text(text, encoding="utf-8", newline="\n")
    loaded = load_manifest(path)
    assert loaded.manifest.results == []
    assert dump_manifest(loaded.manifest) == text


def test_artifact_to_dict_accepts_0_4():
    artifact = Artifact(id="a", path="metrics.csv", dataset_id="toy-v1")
    data = artifact_to_dict(artifact, schema_version="0.4")
    assert data["dataset_id"] == "toy-v1"
