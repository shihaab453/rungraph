"""Schema 0.3 model, serialization, schema generation and fingerprint refusal.

Slice A only: locator shape and migration gate. JSONL/JSON verification is not
implemented; a 0.3 locator is loadable but not yet checkable.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from runevidence import cli
from runevidence.manifest import (
    _issue_location,
    dump_manifest,
    dump_raw,
    load_manifest,
)
from runevidence.models import (
    MAX_JSONL_LINE,
    Manifest,
    json_schema,
    json_schema_0_2,
    json_schema_0_3,
)
from runevidence.run_fingerprint import FingerprintError, manifest_fingerprint

REPO = Path(__file__).resolve().parents[1]


def doc(
    version: str = "0.3",
    locator=...,
    *,
    artifact_id="a",
    artifact_path="data.jsonl",
    extra_result=None,
) -> dict:
    document = {
        "schema_version": version,
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run"}],
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
                "metric_definition": {
                    "definition_id": "accuracy",
                    "higher_is_better": True,
                },
            }
        ],
        "evidence": [],
        "relationships": [],
    }
    if locator is not ...:
        document["results"][0]["locator"] = locator
    if extra_result:
        document["results"].append(extra_result)
    return document


def object_locator(**updates) -> dict:
    value = {"kind": "jsonl", "line": 1, "pointer": ""}
    value.update(updates)
    return value


def validator():
    schema = json_schema_0_3()
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def test_0_3_string_and_object_locators_load():
    string = Manifest.model_validate(doc(locator="/eval/accuracy"))
    assert string.results[0].locator == "/eval/accuracy"

    obj = Manifest.model_validate(doc(locator=object_locator(line=41, pointer="/val")))
    assert obj.results[0].locator.kind == "jsonl"
    assert obj.results[0].locator.line == 41
    assert obj.results[0].locator.pointer == "/val"

    assert Manifest.model_validate(doc(locator="")).results[0].locator == ""
    assert Manifest.model_validate(doc(locator=None)).results[0].locator is None
    assert Manifest.model_validate(doc()).results[0].locator is None


@pytest.mark.parametrize(
    "pointer",
    ["", "/", "/eval/accuracy", "/a~0b", "/a~1b", "//"],
)
def test_0_3_pointer_grammar_accepts(pointer):
    assert Manifest.model_validate(doc(locator=pointer)).results[0].locator == pointer
    obj = Manifest.model_validate(doc(locator=object_locator(pointer=pointer)))
    assert obj.results[0].locator.pointer == pointer


@pytest.mark.parametrize(
    "pointer",
    [
        "jsonl:/x", "eval", "~0", "/a~2", "/a~", "/a~x", " /x",
        "\n", "/a\n", "/a\r", "/a\u2028", "/a\u2029",
    ],
)
def test_0_3_pointer_grammar_rejects(pointer):
    with pytest.raises(ValidationError):
        Manifest.model_validate(doc(locator=pointer))
    with pytest.raises(ValidationError):
        Manifest.model_validate(doc(locator=object_locator(pointer=pointer)))


def test_0_3_any_non_null_locator_requires_artifact_id():
    with pytest.raises(ValidationError):
        Manifest.model_validate(doc(locator="/x", artifact_id=None))
    with pytest.raises(ValidationError):
        Manifest.model_validate(doc(locator=object_locator(), artifact_id=None))
    Manifest.model_validate(doc(locator=None, artifact_id=None))


def test_0_3_line_bounds_and_strict_int():
    for line in (1, 2, MAX_JSONL_LINE):
        Manifest.model_validate(doc(locator=object_locator(line=line)))
    for line in (0, -1, 2**63, True, False, 1.0, "1", None):
        with pytest.raises(ValidationError):
            Manifest.model_validate(doc(locator=object_locator(line=line)))


@pytest.mark.parametrize(
    "locator",
    [
        object_locator(kind="csv"),
        object_locator(kind="jsonl", extra=1),
        {"kind": "jsonl", "line": 1},
        {"line": 1, "pointer": ""},
    ],
)
def test_0_3_object_shape_is_closed(locator):
    with pytest.raises(ValidationError):
        Manifest.model_validate(doc(locator=locator))


@pytest.mark.parametrize("version", ["0.1", "0.2"])
def test_object_locator_is_rejected_before_0_3(version):
    with pytest.raises(ValidationError):
        Manifest.model_validate(doc(version, object_locator()))


def test_0_3_keeps_all_0_2_fields_and_round_trips(tmp_path):
    document = doc(locator=object_locator(line=7, pointer="/v"))
    document["runs"][0]["conditions"] = {"evaluation_split": "test"}
    manifest = Manifest.model_validate(document)
    text = dump_manifest(manifest)
    path = tmp_path / "runevidence.json"
    path.write_text(text, encoding="utf-8", newline="\n")

    loaded = load_manifest(path)
    assert loaded.manifest.schema_version == "0.3"
    assert loaded.manifest.runs[0].conditions.evaluation_split == "test"
    assert loaded.manifest.artifacts[0].dataset_id == "toy-v1"
    assert loaded.manifest.results[0].metric_definition.definition_id == "accuracy"
    assert loaded.manifest.results[0].locator.kind == "jsonl"
    assert dump_manifest(loaded.manifest) == text


def test_0_3_string_only_is_semantically_identical_to_0_2():
    two = json.loads(dump_manifest(Manifest.model_validate(doc("0.2", "/v"))))
    three = json.loads(dump_manifest(Manifest.model_validate(doc("0.3", "/v"))))
    assert two.pop("schema_version") == "0.2"
    assert three.pop("schema_version") == "0.3"
    assert two == three


def test_0_1_and_0_2_schemas_remain_byte_identical():
    assert (
        json.dumps(json_schema(), indent=2, ensure_ascii=False).encode() + b"\n"
        == (REPO / "schema" / "runevidence.schema.json").read_bytes()
    )
    assert (
        json.dumps(json_schema_0_2(), indent=2, ensure_ascii=False).encode() + b"\n"
        == (REPO / "schema" / "runevidence-0.2.schema.json").read_bytes()
    )


def test_0_3_schema_is_generated_and_pinned():
    text = json.dumps(json_schema_0_3(), indent=2, ensure_ascii=False) + "\n"
    path = REPO / "schema" / "runevidence-0.3.schema.json"
    assert text.encode("utf-8") == path.read_bytes()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        "bd18dd053b6805b96b27cb8a9477dcbb9497d84ef6735c840c9aa2ebd0a544e8"
    )


def test_0_3_schema_shape():
    schema = json_schema_0_3()
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"] == "urn:runevidence:manifest:0.3"
    assert schema["properties"]["schema_version"]["const"] == "0.3"
    result = schema["$defs"]["Result"]
    locator = result["properties"]["locator"]
    assert len(locator["oneOf"]) == 3
    assert locator["oneOf"][0]["type"] == "string"
    assert locator["oneOf"][0]["pattern"]
    assert locator["oneOf"][1] == {"$ref": "#/$defs/JsonlLocator"}
    assert locator["oneOf"][2] == {"type": "null"}
    jsonl = schema["$defs"]["JsonlLocator"]
    assert jsonl["additionalProperties"] is False
    assert jsonl["properties"]["kind"]["const"] == "jsonl"
    assert jsonl["properties"]["line"]["minimum"] == 1
    assert jsonl["properties"]["line"]["maximum"] == MAX_JSONL_LINE
    assert jsonl["properties"]["pointer"]["pattern"] == (
        r"^(?![\s\S]*[\r\n\u2028\u2029])(?:/(?:[^~]|~[01])*)?$"
    )
    assert result["if"]["required"] == ["locator"]
    assert result["then"]["required"] == ["artifact_id"]
    assert result["then"]["properties"]["artifact_id"] == {"type": "string"}


def test_0_3_schema_accepts_and_rejects_the_runtime_boundary():
    schema = validator()
    assert list(schema.iter_errors(doc(locator=object_locator()))) == []
    assert list(schema.iter_errors(doc(locator="/v"))) == []
    assert list(schema.iter_errors(doc(locator=""))) == []
    assert list(schema.iter_errors(doc(locator=None))) == []

    assert list(schema.iter_errors(doc(locator="/x", artifact_id=None)))
    assert list(schema.iter_errors(doc(locator=object_locator(), artifact_id=None)))
    assert list(schema.iter_errors(doc(locator="jsonl:/x")))
    assert list(schema.iter_errors(doc(locator="/x~2")))
    assert list(schema.iter_errors(doc(locator="\n")))
    assert list(schema.iter_errors(doc(locator="/x\n")))
    assert list(schema.iter_errors(doc(locator="/x\r")))
    assert list(schema.iter_errors(doc(locator="/x\u2028")))
    assert list(schema.iter_errors(doc(locator=object_locator(pointer="/x\n"))))
    assert list(schema.iter_errors(doc(locator=object_locator(pointer="/x\u2029"))))
    assert list(schema.iter_errors(doc(locator=object_locator(line=0))))
    assert list(schema.iter_errors(doc(locator=object_locator(line=True))))
    assert list(schema.iter_errors(doc(locator=object_locator(kind="csv"))))
    assert list(schema.iter_errors(doc(locator=object_locator(extra=1))))

    # Draft 2020-12 accepts 1.0 as an integer; StrictInt does not. Documented.
    assert list(schema.iter_errors(doc(locator=object_locator(line=1.0)))) == []
    with pytest.raises(ValidationError):
        Manifest.model_validate(doc(locator=object_locator(line=1.0)))


def test_0_2_schema_rejects_object_locator():
    schema = jsonschema.Draft202012Validator(json_schema_0_2())
    assert list(schema.iter_errors(doc("0.2", object_locator())))
    assert list(schema.iter_errors(doc("0.2", "/v"))) == []


def test_fingerprint_v1_refuses_0_3(tmp_path):
    path = tmp_path / "runevidence.json"
    path.write_text(dump_manifest(Manifest.model_validate(doc(locator="/v"))))
    with pytest.raises(FingerprintError):
        manifest_fingerprint(load_manifest(path).manifest, manifest_path=str(path))

    code = cli.main(["fingerprint", str(path), "--json"])
    assert code == 2


def test_0_3_object_locator_diagnostic_keeps_the_nested_field():
    with pytest.raises(ValidationError) as caught:
        Manifest.model_validate(doc(locator=object_locator(pointer="/x~2")))
    error = next(err for err in caught.value.errors() if err["loc"][-1] == "pointer")
    assert _issue_location(error["loc"]) == "/results/0/locator/pointer"

    with pytest.raises(ValidationError) as caught:
        Manifest.model_validate(doc(locator=object_locator(kind="csv")))
    error = next(err for err in caught.value.errors() if err["loc"][-1] == "kind")
    assert _issue_location(error["loc"]) == "/results/0/locator/kind"
