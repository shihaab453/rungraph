"""Schema 0.2 model, presence, serialization and edit coverage (Stage 3b1).

These tests add separate 0.2 evidence. No committed 0.1 fixture is rewritten.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from runevidence.edit import plan_add, plan_rehash
from runevidence.manifest import (
    ManifestLoadError,
    artifact_to_dict,
    dump_manifest,
    dump_raw,
    load_manifest,
)
from runevidence.models import (
    Artifact,
    EvaluationProtocol,
    EvidenceReference,
    Manifest,
    MetricDefinition,
    Project,
    Relationship,
    Result,
    Run,
    RunConditions,
)


# ------------------------------------------------------------------ helpers


def doc_0_1() -> dict:
    return {
        "schema_version": "0.1",
        "project": {"id": "proj", "name": "Project"},
        "runs": [{"id": "run", "metadata": {}}],
        "artifacts": [{"id": "data", "path": "data.csv", "kind": "dataset"}],
        "results": [{"id": "res", "run_id": "run", "name": "accuracy", "value": 0.9}],
        "relationships": [{"source": "data", "target": "run", "type": "input_to"}],
    }


def doc_0_2(**overrides) -> dict:
    doc = {
        "schema_version": "0.2",
        "project": {"id": "proj", "name": "Project"},
        "runs": [{"id": "run", "metadata": {}}],
        "artifacts": [{"id": "data", "path": "data.csv", "kind": "dataset"}],
        "results": [{"id": "res", "run_id": "run", "name": "accuracy", "value": 0.9}],
        "relationships": [{"source": "data", "target": "run", "type": "input_to"}],
    }
    doc.update(overrides)
    return doc


def with_conditions(conditions) -> dict:
    doc = doc_0_2()
    doc["runs"][0]["conditions"] = conditions
    return doc


def with_metric(metric) -> dict:
    doc = doc_0_2()
    doc["results"][0]["metric_definition"] = metric
    return doc


def by_path(node: object, target: str) -> list[tuple[str, object]]:
    """Every (JSON-pointer, value) whose object key equals ``target``."""
    found: list[tuple[str, object]] = []
    stack = [("", node)]
    while stack:
        path, current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key == target:
                    found.append((f"{path}/{key}", value))
                if isinstance(value, (dict, list)):
                    stack.append((f"{path}/{key}", value))
        elif isinstance(current, list):
            for index, value in enumerate(current):
                if isinstance(value, (dict, list)):
                    stack.append((f"{path}/{index}", value))
    return found


# ------------------------------------------------------------- 0.1 boundary


def test_omitted_schema_version_is_0_1():
    doc = doc_0_1()
    del doc["schema_version"]

    assert Manifest.model_validate(doc).schema_version == "0.1"


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda d: d["runs"][0].update(conditions={}), id="conditions-empty"),
        pytest.param(lambda d: d["runs"][0].update(conditions=None), id="conditions-null"),
        pytest.param(
            lambda d: d["runs"][0].update(
                conditions={"evaluation_split": "test"}
            ),
            id="conditions-present",
        ),
        pytest.param(
            lambda d: d["artifacts"][0].update(dataset_id="imagenet"),
            id="dataset-id-present",
        ),
        pytest.param(
            lambda d: d["artifacts"][0].update(dataset_id=None),
            id="dataset-id-null",
        ),
        pytest.param(
            lambda d: d["results"][0].update(
                metric_definition={"definition_id": "accuracy"}
            ),
            id="metric-definition-present",
        ),
        pytest.param(
            lambda d: d["results"][0].update(metric_definition=None),
            id="metric-definition-null",
        ),
    ],
)
def test_0_1_rejects_every_0_2_only_key(mutate):
    doc = doc_0_1()
    mutate(doc)

    with pytest.raises(ValidationError):
        Manifest.model_validate(doc)


def test_omitted_schema_version_with_0_2_key_is_rejected():
    doc = doc_0_1()
    del doc["schema_version"]
    doc["runs"][0]["conditions"] = {}

    with pytest.raises(ValidationError):
        Manifest.model_validate(doc)


def test_0_1_accepts_its_own_shape_without_0_2_keys():
    manifest = Manifest.model_validate(doc_0_1())

    assert manifest.runs[0].conditions is None
    assert manifest.artifacts[0].dataset_id is None
    assert manifest.results[0].metric_definition is None


@pytest.mark.parametrize("version", ["0.4", "1.0", "2", 1, 1.0, True, None])
def test_unknown_or_nonstring_schema_version_is_rejected(version):
    doc = doc_0_1()
    doc["schema_version"] = version

    with pytest.raises(ValidationError):
        Manifest.model_validate(doc)


def _write_manifest(root: Path, doc: dict) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "runevidence.json"
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def test_load_manifest_accepts_both_versions(tmp_path):
    for version in ("0.1", "0.2"):
        path = _write_manifest(tmp_path / version, doc_0_1())
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["schema_version"] = version
        path.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8", newline="\n")

        loaded = load_manifest(path)
        assert loaded.manifest.schema_version == version


def test_load_manifest_keeps_schema_errors_as_manifest_load_error(tmp_path):
    from runevidence.findings import Code

    doc = doc_0_1()
    doc["runs"][0]["conditions"] = {}
    with pytest.raises(ManifestLoadError) as caught:
        load_manifest(_write_manifest(tmp_path / "zero-one", doc))
    assert {finding.code for finding in caught.value.findings} == {Code.schema_invalid}

    doc = with_conditions({"evaluation_dataset_artifact_id": "CANARY ID"})
    with pytest.raises(ManifestLoadError) as caught:
        load_manifest(_write_manifest(tmp_path / "zero-two", doc))
    assert {finding.code for finding in caught.value.findings} == {Code.schema_invalid}
    # No submitted value is echoed back.
    assert "CANARY" not in " ".join(f.message for f in caught.value.findings)


def test_0_2_without_new_fields_is_accepted():
    doc = doc_0_1()
    doc["schema_version"] = "0.2"

    assert Manifest.model_validate(doc).schema_version == "0.2"


def test_0_1_existing_references_keep_unpatterned_acceptance():
    doc = doc_0_1()
    doc["artifacts"][0]["run_id"] = "run with spaces"
    doc["results"][0]["run_id"] = "run with spaces"
    doc["relationships"] = [
        {"source": "source with spaces", "target": "target with spaces", "type": "input_to"}
    ]

    Manifest.model_validate(doc)


# --------------------------------------------------------- 0.2 presence rules


def test_conditions_omitted_empty_and_present_are_distinct():
    omitted = Manifest.model_validate(doc_0_2())
    empty = Manifest.model_validate(with_conditions({}))
    assert "conditions" not in omitted.runs[0].model_fields_set
    assert "conditions" in empty.runs[0].model_fields_set
    assert empty.runs[0].conditions.model_fields_set == set()


def test_conditions_explicit_null_is_rejected():
    with pytest.raises(ValidationError):
        Manifest.model_validate(with_conditions(None))


@pytest.mark.parametrize(
    "conditions",
    [
        {},
        {"evaluation_split": None},
        {"evaluation_split": "test"},
        {"evaluation_protocol": None},
        {"evaluation_protocol": {"protocol_id": "p"}},
        {"evaluation_protocol": {"protocol_id": "p", "version": 1}},
        {"evaluation_protocol": {"protocol_id": "p", "version": "v1"}},
        {"evaluation_dataset_artifact_id": "data"},
        {"model_checkpoint_artifact_id": "ckpt"},
        {"source_revision": None},
        {"source_revision": "rev"},
        {"environment": None},
        {"environment": {}},
        {"environment": {"python": "3.12", "tags": ["cpu"], "gpu": None}},
    ],
)
def test_valid_0_2_conditions_are_accepted(conditions):
    manifest = Manifest.model_validate(with_conditions(conditions))

    assert manifest.runs[0].conditions is not None


@pytest.mark.parametrize(
    "conditions",
    [
        pytest.param({"evaluation_split": ""}, id="split-empty"),
        pytest.param({"evaluation_split": "   "}, id="split-blank"),
        pytest.param({"evaluation_dataset_artifact_id": None}, id="dataset-selector-null"),
        pytest.param({"evaluation_dataset_artifact_id": "bad id"}, id="dataset-selector-space"),
        pytest.param({"evaluation_dataset_artifact_id": ""}, id="dataset-selector-empty"),
        pytest.param({"model_checkpoint_artifact_id": None}, id="checkpoint-selector-null"),
        pytest.param({"model_checkpoint_artifact_id": "-bad"}, id="checkpoint-selector-leading"),
        pytest.param({"evaluation_protocol": {}}, id="protocol-missing-id"),
        pytest.param({"evaluation_protocol": {"protocol_id": ""}}, id="protocol-empty-id"),
        pytest.param({"evaluation_protocol": {"protocol_id": "  "}}, id="protocol-blank-id"),
        pytest.param(
            {"evaluation_protocol": {"protocol_id": "p", "version": None}},
            id="protocol-version-null",
        ),
        pytest.param(
            {"evaluation_protocol": {"protocol_id": "p", "version": True}},
            id="protocol-version-bool",
        ),
        pytest.param(
            {"evaluation_protocol": {"protocol_id": "p", "version": 1.0}},
            id="protocol-version-float",
        ),
        pytest.param(
            {"evaluation_protocol": {"protocol_id": "p", "version": ""}},
            id="protocol-version-empty",
        ),
        pytest.param({"source_revision": ""}, id="source-empty"),
        pytest.param({"source_revision": "  "}, id="source-blank"),
    ],
)
def test_invalid_0_2_conditions_are_rejected(conditions):
    with pytest.raises(ValidationError):
        Manifest.model_validate(with_conditions(conditions))


@pytest.mark.parametrize(
    "metric",
    [
        None,
        {"definition_id": "accuracy"},
        {"definition_id": "accuracy", "higher_is_better": False},
        {"definition_id": "accuracy", "higher_is_better": True},
        {"definition_id": "accuracy", "averaging": "macro"},
        {"definition_id": "accuracy", "version": 1},
        {"definition_id": "accuracy", "version": "v1"},
    ],
)
def test_valid_0_2_metric_definitions_are_accepted(metric):
    Manifest.model_validate(with_metric(metric))


@pytest.mark.parametrize(
    "metric",
    [
        pytest.param({}, id="missing-definition-id"),
        pytest.param({"definition_id": ""}, id="empty-definition-id"),
        pytest.param({"definition_id": "   "}, id="blank-definition-id"),
        pytest.param(
            {"definition_id": "d", "higher_is_better": "false"}, id="higher-string"
        ),
        pytest.param({"definition_id": "d", "higher_is_better": 0}, id="higher-int"),
        pytest.param({"definition_id": "d", "higher_is_better": 1}, id="higher-one"),
        pytest.param({"definition_id": "d", "higher_is_better": None}, id="higher-null"),
        pytest.param({"definition_id": "d", "averaging": ""}, id="averaging-empty"),
        pytest.param({"definition_id": "d", "averaging": "  "}, id="averaging-blank"),
        pytest.param({"definition_id": "d", "averaging": None}, id="averaging-null"),
        pytest.param({"definition_id": "d", "version": None}, id="version-null"),
        pytest.param({"definition_id": "d", "version": True}, id="version-bool"),
        pytest.param({"definition_id": "d", "version": 1.0}, id="version-float"),
        pytest.param({"definition_id": "d", "version": ""}, id="version-empty"),
    ],
)
def test_invalid_0_2_metric_definitions_are_rejected(metric):
    with pytest.raises(ValidationError):
        Manifest.model_validate(with_metric(metric))


def test_metric_definition_explicit_null_is_recorded_unknown():
    document = with_metric(None)
    manifest = Manifest.model_validate(document)

    assert "metric_definition" in manifest.results[0].model_fields_set
    assert manifest.results[0].metric_definition is None


@pytest.mark.parametrize("dataset_id", ["imagenet", "imagenet-v2", "a/b:c.d"])
def test_dataset_id_accepts_nonblank_semantic_labels(dataset_id):
    doc = doc_0_2()
    doc["artifacts"][0]["dataset_id"] = dataset_id

    assert Manifest.model_validate(doc).artifacts[0].dataset_id == dataset_id


@pytest.mark.parametrize("dataset_id", ["", "   "])
def test_dataset_id_rejects_blank_labels(dataset_id):
    doc = doc_0_2()
    doc["artifacts"][0]["dataset_id"] = dataset_id

    with pytest.raises(ValidationError):
        Manifest.model_validate(doc)


def test_dataset_id_explicit_null_is_rejected_but_omission_is_allowed():
    doc = doc_0_2()
    doc["artifacts"][0]["dataset_id"] = None

    with pytest.raises(ValidationError):
        Manifest.model_validate(doc)
    with pytest.raises(ValidationError):
        Artifact(id="a", path="a", dataset_id=None)

    assert Manifest.model_validate(doc_0_2()).artifacts[0].dataset_id is None


def test_strict_types_do_not_coerce():
    # Strict int rejects bool and float; strict bool rejects strings and ints.
    for version in (True, False, 1.0, 1.5):
        with pytest.raises(ValidationError):
            Manifest.model_validate(
                with_conditions(
                    {"evaluation_protocol": {"protocol_id": "p", "version": version}}
                )
            )
    for higher in ("false", "true", 0, 1):
        with pytest.raises(ValidationError):
            Manifest.model_validate(
                with_metric({"definition_id": "d", "higher_is_better": higher})
            )


def test_version_forms_remain_distinct():
    values = [1, "1", "v1"]
    parsed = [
        Manifest.model_validate(
            with_conditions(
                {"evaluation_protocol": {"protocol_id": "p", "version": value}}
            )
        ).runs[0].conditions.evaluation_protocol.version
        for value in values
    ]

    assert parsed == values
    assert len({repr(value) for value in parsed}) == 3


def test_first_class_strings_are_not_trimmed_or_case_folded():
    document = with_conditions(
        {
            "evaluation_split": " Test ",
            "source_revision": " Rev ",
            "evaluation_protocol": {"protocol_id": " Protocol "},
        }
    )
    reloaded = Manifest.model_validate(json.loads(dump_manifest(Manifest.model_validate(document))))

    conditions = reloaded.runs[0].conditions
    assert conditions.evaluation_split == " Test "
    assert conditions.source_revision == " Rev "
    assert conditions.evaluation_protocol.protocol_id == " Protocol "


# ------------------------------------------------------------- serialization


def test_0_1_dump_never_contains_0_2_keys():
    manifest = Manifest.model_validate(doc_0_1())
    data = json.loads(dump_manifest(manifest))

    assert data["schema_version"] == "0.1"
    assert by_path(data, "conditions") == []
    assert by_path(data, "dataset_id") == []
    assert by_path(data, "metric_definition") == []


def test_0_1_dump_round_trips_and_reloads():
    manifest = Manifest.model_validate(doc_0_1())
    text = dump_manifest(manifest)
    reloaded = Manifest.model_validate(json.loads(text))

    assert dump_manifest(reloaded) == text
    assert reloaded.schema_version == "0.1"


def test_0_1_writer_keeps_its_historical_default_spelling():
    manifest = Manifest.model_validate(doc_0_1())
    data = json.loads(dump_manifest(manifest))
    artifact = data["artifacts"][0]

    assert list(artifact) == [
        "id",
        "path",
        "sha256",
        "size_bytes",
        "kind",
        "run_id",
        "metadata",
    ]
    assert set(data["runs"][0]) == {"id", "name", "started_at", "params", "metadata"}
    assert "conditions" not in data["runs"][0]
    assert data["project"] == {
        "id": "proj",
        "name": "Project",
        "description": None,
        "tags": [],
        "metadata": {},
    }


@pytest.mark.parametrize(
    ("conditions", "expected"),
    [
        pytest.param({}, {}, id="empty-object"),
        pytest.param({"evaluation_split": None}, {"evaluation_split": None}, id="split-null"),
        pytest.param({"evaluation_split": "test"}, {"evaluation_split": "test"}, id="split-value"),
        pytest.param(
            {"evaluation_protocol": None},
            {"evaluation_protocol": None},
            id="protocol-null",
        ),
        pytest.param(
            {"evaluation_protocol": {"protocol_id": "p"}},
            {"evaluation_protocol": {"protocol_id": "p"}},
            id="protocol-no-version",
        ),
        pytest.param(
            {"evaluation_protocol": {"protocol_id": "p", "version": 1}},
            {"evaluation_protocol": {"protocol_id": "p", "version": 1}},
            id="protocol-version",
        ),
        pytest.param(
            {"evaluation_dataset_artifact_id": "data"},
            {"evaluation_dataset_artifact_id": "data"},
            id="dataset-selector",
        ),
        pytest.param(
            {"model_checkpoint_artifact_id": "ckpt"},
            {"model_checkpoint_artifact_id": "ckpt"},
            id="checkpoint-selector",
        ),
        pytest.param({"source_revision": None}, {"source_revision": None}, id="revision-null"),
        pytest.param({"environment": None}, {"environment": None}, id="environment-null"),
        pytest.param({"environment": {}}, {"environment": {}}, id="environment-empty"),
        pytest.param(
            {"environment": {"python": "3.12", "gpu": None}},
            {"environment": {"python": "3.12", "gpu": None}},
            id="environment-values",
        ),
    ],
)
def test_0_2_conditions_presence_survives_dump_reload(conditions, expected):
    manifest = Manifest.model_validate(with_conditions(conditions))
    text = dump_manifest(manifest)
    data = json.loads(text)

    assert data["runs"][0]["conditions"] == expected
    reloaded = Manifest.model_validate(data)
    assert reloaded.runs[0].conditions.model_fields_set == set(expected)
    assert dump_manifest(reloaded) == text


def test_0_2_omitted_conditions_stay_omitted():
    data = json.loads(dump_manifest(Manifest.model_validate(doc_0_2())))

    assert "conditions" not in data["runs"][0]


@pytest.mark.parametrize(
    ("metric", "expected"),
    [
        pytest.param(None, None, id="null"),
        pytest.param({"definition_id": "d"}, {"definition_id": "d"}, id="id-only"),
        pytest.param(
            {"definition_id": "d", "higher_is_better": False},
            {"definition_id": "d", "higher_is_better": False},
            id="higher-false",
        ),
        pytest.param(
            {"definition_id": "d", "averaging": "macro"},
            {"definition_id": "d", "averaging": "macro"},
            id="averaging",
        ),
        pytest.param(
            {"definition_id": "d", "version": "v1"},
            {"definition_id": "d", "version": "v1"},
            id="version",
        ),
    ],
)
def test_0_2_metric_definition_presence_survives_dump_reload(metric, expected):
    manifest = Manifest.model_validate(with_metric(metric))
    text = dump_manifest(manifest)
    data = json.loads(text)

    assert data["results"][0]["metric_definition"] == expected
    assert dump_manifest(Manifest.model_validate(data)) == text


def test_0_2_omitted_metric_definition_stays_omitted():
    data = json.loads(dump_manifest(Manifest.model_validate(doc_0_2())))

    assert "metric_definition" not in data["results"][0]


def test_0_2_dataset_id_presence_survives_dump_reload():
    doc = doc_0_2()
    doc["artifacts"][0]["dataset_id"] = "imagenet"
    manifest = Manifest.model_validate(doc)
    text = dump_manifest(manifest)
    data = json.loads(text)

    assert data["artifacts"][0]["dataset_id"] == "imagenet"
    assert dump_manifest(Manifest.model_validate(data)) == text

    doc = doc_0_2()
    data = json.loads(dump_manifest(Manifest.model_validate(doc)))
    assert "dataset_id" not in data["artifacts"][0]


def test_0_2_dump_declares_its_version_first():
    data = json.loads(dump_manifest(Manifest.model_validate(doc_0_2())))

    assert list(data)[0] == "schema_version"
    assert data["schema_version"] == "0.2"


def test_0_2_dump_keeps_normal_defaults_and_omits_only_unset_0_2_members():
    manifest = Manifest.model_validate(
        {"schema_version": "0.2", "project": {"id": "proj", "name": "Project"}}
    )

    data = json.loads(dump_manifest(manifest))

    assert data == {
        "schema_version": "0.2",
        "project": {
            "id": "proj",
            "name": "Project",
            "description": None,
            "tags": [],
            "metadata": {},
        },
        "runs": [],
        "artifacts": [],
        "results": [],
        "evidence": [],
        "relationships": [],
    }


def test_0_2_keeps_established_default_spelling_exactly_like_0_1():
    doc = doc_0_1()
    doc["project"].update(description="description", tags=["tag"], metadata={"p": 1})
    doc["runs"][0].update(name="run", params={"lr": 0.1}, metadata={"r": 2})
    doc["artifacts"][0].update(
        sha256="a" * 64,
        size_bytes=10,
        volatile=False,
        metadata={"a": 3},
        run_id="run",
    )
    doc["results"][0].update(locator="/accuracy", artifact_id="data", metadata={"m": 4})
    doc["artifacts"].append({"id": "log", "path": "run.log", "volatile": True})

    zero_one = json.loads(dump_manifest(Manifest.model_validate(doc)))
    doc["schema_version"] = "0.2"
    zero_two = json.loads(dump_manifest(Manifest.model_validate(doc)))

    assert zero_one["schema_version"] == "0.1"
    assert zero_two["schema_version"] == "0.2"
    zero_one.pop("schema_version")
    zero_two.pop("schema_version")
    assert zero_two == zero_one

    # Established defaults are present with their normal writer spelling.
    assert zero_two["project"]["tags"] == ["tag"]
    assert zero_two["project"]["metadata"] == {"p": 1}
    assert zero_two["runs"][0]["params"] == {"lr": 0.1}
    assert zero_two["runs"][0]["metadata"] == {"r": 2}
    assert zero_two["results"][0]["metadata"] == {"m": 4}
    assert zero_two["artifacts"][0]["metadata"] == {"a": 3}
    assert "volatile" not in zero_two["artifacts"][0]
    assert "artifact_type" not in zero_two["artifacts"][0]
    assert "digest_format" not in zero_two["artifacts"][0]
    assert zero_two["artifacts"][1]["volatile"] is True


def test_0_2_dump_still_omits_unset_0_2_members_with_normal_defaults():
    data = json.loads(dump_manifest(Manifest.model_validate(doc_0_2())))
    run = data["runs"][0]
    result = data["results"][0]
    artifact = data["artifacts"][0]

    assert run["params"] == {} and run["metadata"] == {}
    assert "conditions" not in run
    assert "metric_definition" not in result
    assert result["metadata"] == {}
    assert "dataset_id" not in artifact
    assert artifact["metadata"] == {}


def test_artifact_to_dict_0_2_keeps_established_writer_defaults():
    plain = Artifact(id="a", path="a.txt")
    one = artifact_to_dict(plain, schema_version="0.1")
    two = artifact_to_dict(plain, schema_version="0.2")

    assert one == two
    assert two == {
        "id": "a",
        "path": "a.txt",
        "sha256": None,
        "size_bytes": None,
        "kind": None,
        "run_id": None,
        "metadata": {},
    }
    assert "dataset_id" not in two
    assert "volatile" not in two
    assert "artifact_type" not in two
    assert "digest_format" not in two

    volatile = Artifact(id="v", path="v.txt", volatile=True)
    assert artifact_to_dict(volatile, schema_version="0.2")["volatile"] is True

    directory = Artifact(
        id="dir",
        path="outputs",
        artifact_type="directory",
        digest_format="runevidence-directory-v1",
        volatile=False,
    )
    directory_data = artifact_to_dict(directory, schema_version="0.2")
    assert directory_data["artifact_type"] == "directory"
    assert directory_data["digest_format"] == "runevidence-directory-v1"
    assert "volatile" not in directory_data

    labelled = Artifact(id="l", path="l.txt", dataset_id="imagenet")
    assert "dataset_id" not in artifact_to_dict(labelled, schema_version="0.1")
    assert artifact_to_dict(labelled, schema_version="0.2")["dataset_id"] == "imagenet"


def test_artifact_to_dict_has_explicit_version_context():
    plain = Artifact(id="a", path="a.txt")
    labelled = Artifact(id="a", path="a.txt", dataset_id="imagenet")

    assert artifact_to_dict(plain) == artifact_to_dict(plain, schema_version="0.1")
    assert "dataset_id" not in artifact_to_dict(plain, schema_version="0.1")
    assert "dataset_id" not in artifact_to_dict(plain, schema_version="0.2")
    assert "dataset_id" not in artifact_to_dict(labelled, schema_version="0.1")
    assert artifact_to_dict(labelled, schema_version="0.2")["dataset_id"] == "imagenet"
    assert artifact_to_dict(labelled, schema_version="0.3")["dataset_id"] == "imagenet"

    with pytest.raises(ValueError):
        artifact_to_dict(plain, schema_version="0.4")


def _two_entry_doc(version: str = "0.2") -> dict:
    doc = {
        "schema_version": version,
        "project": {"id": "proj", "name": "Project"},
        "runs": [{"id": "run-0"}, {"id": "run-1"}],
        "artifacts": [
            {"id": "data-0", "path": "data-0.csv"},
            {"id": "data-1", "path": "data-1.csv"},
        ],
        "results": [
            {"id": "res-0", "run_id": "run-0", "name": "accuracy"},
            {"id": "res-1", "run_id": "run-1", "name": "accuracy"},
        ],
    }
    if version == "0.2":
        doc["runs"][1]["conditions"] = {
            "evaluation_split": None,
            "evaluation_protocol": {"protocol_id": "protocol-1"},
            "environment": {},
        }
        doc["artifacts"][1]["dataset_id"] = "imagenet"
        doc["results"][1]["metric_definition"] = None
    return doc


def _dump(manifest: Manifest, json_mode: bool, **kwargs) -> dict:
    if json_mode:
        return json.loads(manifest.model_dump_json(**kwargs))
    return manifest.model_dump(**kwargs)


def _assert_entry_one_presence(manifest: Manifest) -> None:
    conditions = manifest.runs[0].conditions
    assert manifest.runs[0].id == "run-1"
    assert conditions.evaluation_split is None
    assert conditions.environment == {}
    assert conditions.evaluation_protocol.protocol_id == "protocol-1"
    assert "version" not in conditions.evaluation_protocol.model_fields_set
    assert manifest.artifacts[0].dataset_id == "imagenet"
    assert manifest.results[0].metric_definition is None
    assert "metric_definition" in manifest.results[0].model_fields_set


@pytest.mark.parametrize("version", ["0.1", "0.2"])
@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize(
    ("index", "expected"),
    [
        (-2, ("run-1", "data-1", "res-1")),
        (0, ("run-1", "data-1", "res-1")),
        (-1, ("run-0", "data-0", "res-0")),
        (1, ("run-0", "data-0", "res-0")),
    ],
)
def test_exclude_negative_and_positive_indices_all_list_locations(
    version, json_mode, index, expected
):
    manifest = Manifest.model_validate(_two_entry_doc(version))

    data = _dump(
        manifest,
        json_mode,
        exclude={"runs": {index: True}, "artifacts": {index: True}, "results": {index: True}},
    )

    assert (data["runs"][0]["id"], data["artifacts"][0]["id"], data["results"][0]["id"]) == expected
    assert len(data["runs"]) == len(data["artifacts"]) == len(data["results"]) == 1

    reloaded = Manifest.model_validate(data)
    assert (reloaded.runs[0].id, reloaded.artifacts[0].id, reloaded.results[0].id) == expected
    if version == "0.2" and expected[0] == "run-1":
        _assert_entry_one_presence(reloaded)
    elif version == "0.2":
        assert "conditions" not in reloaded.runs[0].model_fields_set
        assert "dataset_id" not in reloaded.artifacts[0].model_fields_set
        assert "metric_definition" not in reloaded.results[0].model_fields_set


@pytest.mark.parametrize("version", ["0.1", "0.2"])
@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize(
    ("index", "expected"),
    [
        (-2, ("run-0", "data-0", "res-0")),
        (0, ("run-0", "data-0", "res-0")),
        (-1, ("run-1", "data-1", "res-1")),
        (1, ("run-1", "data-1", "res-1")),
    ],
)
def test_include_negative_and_positive_indices_all_list_locations(
    version, json_mode, index, expected
):
    manifest = Manifest.model_validate(_two_entry_doc(version))

    data = _dump(
        manifest,
        json_mode,
        include={
            "schema_version": True,
            "project": True,
            "runs": {index},
            "artifacts": {index},
            "results": {index},
        },
    )

    assert (data["runs"][0]["id"], data["artifacts"][0]["id"], data["results"][0]["id"]) == expected
    assert len(data["runs"]) == len(data["artifacts"]) == len(data["results"]) == 1

    reloaded = Manifest.model_validate(data)
    assert (reloaded.runs[0].id, reloaded.artifacts[0].id, reloaded.results[0].id) == expected
    if version == "0.2" and expected[0] == "run-1":
        _assert_entry_one_presence(reloaded)


@pytest.mark.parametrize("version", ["0.1", "0.2"])
@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("form", ["all_true", "all_ellipsis", "field_true", "field_ellipsis"])
def test_whole_list_exclusion_forms(version, json_mode, form):
    manifest = Manifest.model_validate(_two_entry_doc(version))
    if form == "all_true":
        exclusion = {name: {"__all__": True} for name in ("runs", "artifacts", "results")}
        omitted = False
    elif form == "all_ellipsis":
        exclusion = {name: {"__all__": ...} for name in ("runs", "artifacts", "results")}
        omitted = False
    elif form == "field_true":
        exclusion = {name: True for name in ("runs", "artifacts", "results")}
        omitted = True
    else:
        exclusion = {name: ... for name in ("runs", "artifacts", "results")}
        omitted = True

    data = _dump(manifest, json_mode, exclude=exclusion)

    for name in ("runs", "artifacts", "results"):
        if omitted:
            assert name not in data
        else:
            assert data[name] == []

    reloaded = Manifest.model_validate(data)
    assert reloaded.runs == reloaded.artifacts == reloaded.results == []


@pytest.mark.parametrize("version", ["0.1", "0.2"])
@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize(
    ("include_index", "exclude_index", "expected"),
    [
        (-1, -2, ("run-1", "data-1", "res-1")),
        (-2, -1, ("run-0", "data-0", "res-0")),
    ],
)
def test_combined_negative_include_and_exclude(
    version, json_mode, include_index, exclude_index, expected
):
    manifest = Manifest.model_validate(_two_entry_doc(version))

    data = _dump(
        manifest,
        json_mode,
        include={
            "schema_version": True,
            "project": True,
            "runs": {include_index},
            "artifacts": {include_index},
            "results": {include_index},
        },
        exclude={
            "runs": {exclude_index: True},
            "artifacts": {exclude_index: True},
            "results": {exclude_index: True},
        },
    )

    assert (data["runs"][0]["id"], data["artifacts"][0]["id"], data["results"][0]["id"]) == expected
    reloaded = Manifest.model_validate(data)
    assert (reloaded.runs[0].id, reloaded.artifacts[0].id, reloaded.results[0].id) == expected
    if version == "0.2" and expected[0] == "run-1":
        _assert_entry_one_presence(reloaded)


@pytest.mark.parametrize("version", ["0.1", "0.2"])
@pytest.mark.parametrize("json_mode", [False, True])
def test_all_entries_partial_field_exclusion_keeps_entries(version, json_mode):
    manifest = Manifest.model_validate(_two_entry_doc(version))

    data = _dump(
        manifest,
        json_mode,
        exclude={"runs": {"__all__": {"name"}}},
    )

    assert [run["id"] for run in data["runs"]] == ["run-0", "run-1"]
    assert all("name" not in run for run in data["runs"])
    if version == "0.2":
        assert data["runs"][1]["conditions"]["evaluation_split"] is None
        assert data["runs"][1]["conditions"]["environment"] == {}

    reloaded = Manifest.model_validate(data)
    assert [run.id for run in reloaded.runs] == ["run-0", "run-1"]
    if version == "0.2":
        assert reloaded.runs[1].conditions.environment == {}


_MATRIX_FIELD = {"runs": "name", "artifacts": "kind", "results": "locator"}
_MATRIX_PREFIX = {"runs": "run", "artifacts": "data", "results": "res"}
_MATRIX_PRESENCE = {
    "runs": "conditions",
    "artifacts": "dataset_id",
    "results": "metric_definition",
}


def _matrix_doc(version: str) -> dict:
    doc = _two_entry_doc(version)
    if version == "0.2":
        doc["results"][1]["metric_definition"] = {"definition_id": "metric-1"}
    return doc


def _has_forbidden_null(node: object) -> bool:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("conditions", "dataset_id", "metric_definition") and value is None:
                return True
            if _has_forbidden_null(value):
                return True
    elif isinstance(node, list):
        return any(_has_forbidden_null(item) for item in node)
    return False


@pytest.mark.parametrize("version", ["0.1", "0.2"])
@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("location", ["runs", "artifacts", "results"])
@pytest.mark.parametrize("index", [-2, -1])
@pytest.mark.parametrize(
    "form", ["all_true", "all_ellipsis", "all_partial_retain", "all_whole_explicit"]
)
def test_all_spec_96_case_matrix(version, json_mode, location, index, form):
    manifest = Manifest.model_validate(_matrix_doc(version))
    field = _MATRIX_FIELD[location]
    slot = 0 if index == -2 else 1

    if form == "all_true":
        spec = {"__all__": True}
    elif form == "all_ellipsis":
        spec = {"__all__": ...}
    elif form == "all_partial_retain":
        spec = {"__all__": True, index: {field}}
    else:
        spec = {"__all__": True, index: True}

    data = _dump(manifest, json_mode, exclude={location: spec})

    assert not _has_forbidden_null(data), data
    entries = data[location]
    if form == "all_partial_retain":
        assert len(entries) == 1
        assert entries[0]["id"] == f"{_MATRIX_PREFIX[location]}-{slot}"
        assert field not in entries[0]
    else:
        assert entries == []

    reloaded = Manifest.model_validate(data)
    assert reloaded.schema_version == version
    if form == "all_partial_retain" and version == "0.2":
        retained = getattr(reloaded, location)[0]
        presence = _MATRIX_PRESENCE[location]
        if slot == 0:
            assert presence not in retained.model_fields_set
        else:
            assert presence in retained.model_fields_set


@pytest.mark.parametrize("version", ["0.1", "0.2"])
@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("location", ["runs", "artifacts", "results"])
@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("form", ["partial", "whole"])
def test_all_with_explicit_positive_index(version, json_mode, location, index, form):
    manifest = Manifest.model_validate(_matrix_doc(version))
    field = _MATRIX_FIELD[location]
    if form == "partial":
        spec = {"__all__": True, index: {field}}
    else:
        spec = {"__all__": True, index: True}

    data = _dump(manifest, json_mode, exclude={location: spec})

    assert not _has_forbidden_null(data), data
    if form == "partial":
        assert len(data[location]) == 1
        assert data[location][0]["id"] == f"{_MATRIX_PREFIX[location]}-{index}"
        assert field not in data[location][0]
    else:
        assert data[location] == []
    Manifest.model_validate(data)


@pytest.mark.parametrize("version", ["0.1", "0.2"])
@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("location", ["runs", "artifacts", "results"])
@pytest.mark.parametrize("sub", ["partial", "whole"])
def test_all_ellipsis_with_explicit_negative_index(version, json_mode, location, sub):
    manifest = Manifest.model_validate(_matrix_doc(version))
    field = _MATRIX_FIELD[location]
    spec = (
        {"__all__": ..., -1: {field}}
        if sub == "partial"
        else {"__all__": ..., -1: ...}
    )

    data = _dump(manifest, json_mode, exclude={location: spec})

    assert not _has_forbidden_null(data), data
    if sub == "partial":
        assert len(data[location]) == 1
        assert data[location][0]["id"] == f"{_MATRIX_PREFIX[location]}-1"
        assert field not in data[location][0]
    else:
        assert data[location] == []
    Manifest.model_validate(data)


_ALIAS_FIELDS = {
    "runs": ("name", "started_at"),
    "artifacts": ("kind", "metadata"),
    "results": ("locator", "value"),
}
_ALIAS_INCLUDE_BASE = {
    "runs": ("id",),
    "artifacts": ("id", "path"),
    "results": ("id", "run_id", "name"),
}


@pytest.mark.parametrize("version", ["0.1", "0.2"])
@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("location", ["runs", "artifacts", "results"])
@pytest.mark.parametrize("reverse", [False, True])
def test_aliased_positive_and_negative_index_exclude_partials_merge(
    version, json_mode, location, reverse
):
    manifest = Manifest.model_validate(_matrix_doc(version))
    first, second = _ALIAS_FIELDS[location]
    spec = {1: {first}, -1: {second}}
    if reverse:
        spec = {-1: {second}, 1: {first}}

    data = _dump(manifest, json_mode, exclude={location: spec})

    assert not _has_forbidden_null(data), data
    assert [entry["id"] for entry in data[location]] == [
        f"{_MATRIX_PREFIX[location]}-0",
        f"{_MATRIX_PREFIX[location]}-1",
    ]
    retained = data[location][1]
    assert first not in retained
    assert second not in retained

    reloaded = Manifest.model_validate(data)
    reloaded_entry = getattr(reloaded, location)[1]
    assert first not in reloaded_entry.model_fields_set
    assert second not in reloaded_entry.model_fields_set


@pytest.mark.parametrize("version", ["0.1", "0.2"])
@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("location", ["runs", "artifacts", "results"])
@pytest.mark.parametrize("whole_first", [False, True])
def test_aliased_positive_and_negative_index_whole_exclusion_wins(
    version, json_mode, location, whole_first
):
    manifest = Manifest.model_validate(_matrix_doc(version))
    first, _ = _ALIAS_FIELDS[location]
    spec = {-1: True, 1: {first}} if whole_first else {1: {first}, -1: True}

    data = _dump(manifest, json_mode, exclude={location: spec})

    assert not _has_forbidden_null(data), data
    assert [entry["id"] for entry in data[location]] == [
        f"{_MATRIX_PREFIX[location]}-0"
    ]
    reloaded = Manifest.model_validate(data)
    assert getattr(reloaded, location)[0].id == f"{_MATRIX_PREFIX[location]}-0"


@pytest.mark.parametrize("version", ["0.1", "0.2"])
@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("location", ["runs", "artifacts", "results"])
@pytest.mark.parametrize("reverse", [False, True])
def test_aliased_positive_and_negative_index_include_specs_merge(
    version, json_mode, location, reverse
):
    manifest = Manifest.model_validate(_two_entry_doc(version))
    _, field = _ALIAS_FIELDS[location]
    required = _ALIAS_INCLUDE_BASE[location]
    spec = {1: set(required), -1: {field}}
    if reverse:
        spec = {-1: {field}, 1: set(required)}

    data = _dump(
        manifest,
        json_mode,
        include={"schema_version": True, "project": True, location: spec},
    )

    assert not _has_forbidden_null(data), data
    assert len(data[location]) == 1
    included = data[location][0]
    assert included["id"] == f"{_MATRIX_PREFIX[location]}-1"
    for name in required:
        assert name in included
    assert field in included

    reloaded = Manifest.model_validate(data)
    reloaded_entry = getattr(reloaded, location)[0]
    assert reloaded_entry.id == f"{_MATRIX_PREFIX[location]}-1"
    assert field in reloaded_entry.model_fields_set


@pytest.mark.parametrize("version", ["0.1", "0.2"])
@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("location", ["runs", "artifacts", "results"])
@pytest.mark.parametrize("index", [1, -1])
def test_include_all_with_explicit_index(version, json_mode, location, index):
    manifest = Manifest.model_validate(_two_entry_doc(version))
    _, field = _ALIAS_FIELDS[location]
    required = set(_ALIAS_INCLUDE_BASE[location])
    spec = {"__all__": True, index: required | {field}}

    data = _dump(
        manifest,
        json_mode,
        include={"schema_version": True, "project": True, location: spec},
    )

    assert not _has_forbidden_null(data), data
    assert len(data[location]) == 2
    narrowed = data[location][1]
    assert set(narrowed) == required | {field}
    assert len(data[location][0]) > len(narrowed)
    Manifest.model_validate(data)


@pytest.mark.parametrize("version", ["0.1", "0.2"])
@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_aliased_exclusion_combined_across_list_locations(
    version, json_mode, reverse
):
    manifest = Manifest.model_validate(_matrix_doc(version))
    exclusion = {}
    for location in ("runs", "artifacts", "results"):
        first, second = _ALIAS_FIELDS[location]
        spec = {1: {first}, -1: {second}}
        if reverse:
            spec = {-1: {second}, 1: {first}}
        exclusion[location] = spec

    data = _dump(manifest, json_mode, exclude=exclusion)

    assert not _has_forbidden_null(data), data
    for location in ("runs", "artifacts", "results"):
        first, second = _ALIAS_FIELDS[location]
        entry = data[location][1]
        assert first not in entry
        assert second not in entry
    reloaded = Manifest.model_validate(data)
    if version == "0.2":
        assert reloaded.runs[1].conditions is not None
        assert reloaded.artifacts[1].dataset_id == "imagenet"
        assert "metric_definition" in reloaded.results[1].model_fields_set


@pytest.mark.parametrize("version", ["0.1", "0.2"])
@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("location", ["runs", "artifacts", "results"])
@pytest.mark.parametrize("empty", [set(), {}])
def test_empty_partial_specs_exclude_nothing(version, json_mode, location, empty):
    manifest = Manifest.model_validate(_matrix_doc(version))

    data = _dump(manifest, json_mode, exclude={location: {1: empty}})

    assert not _has_forbidden_null(data), data
    assert len(data[location]) == 2
    for name in _ALIAS_FIELDS[location]:
        assert name in data[location][1]
    if version == "0.2":
        assert _MATRIX_PRESENCE[location] in data[location][1]
    reloaded = Manifest.model_validate(data)
    assert len(getattr(reloaded, location)) == 2


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("location", ["runs", "artifacts", "results"])
def test_nested_none_exclusion_excludes_nothing(json_mode, location):
    manifest = Manifest.model_validate(_matrix_doc("0.2"))
    field = _MATRIX_PRESENCE[location]

    data = _dump(manifest, json_mode, exclude={location: {1: {field: None}}})

    assert data == _dump(manifest, json_mode)
    assert field in data[location][1]
    assert not _has_forbidden_null(data), data
    Manifest.model_validate(data)


def test_exclude_list_filters_preserve_entry_one_0_2_presence():
    manifest = Manifest.model_validate(_two_entry_doc())

    data = manifest.model_dump(
        exclude={"runs": {0}, "artifacts": {0}, "results": {0}}
    )

    assert [run["id"] for run in data["runs"]] == ["run-1"]
    assert [artifact["id"] for artifact in data["artifacts"]] == ["data-1"]
    assert [result["id"] for result in data["results"]] == ["res-1"]
    assert data["runs"][0]["conditions"] == {
        "evaluation_split": None,
        "evaluation_protocol": {"protocol_id": "protocol-1"},
        "environment": {},
    }
    assert data["artifacts"][0]["dataset_id"] == "imagenet"
    assert data["results"][0]["metric_definition"] is None

    reloaded = Manifest.model_validate(data)
    conditions = reloaded.runs[0].conditions
    assert reloaded.runs[0].id == "run-1"
    assert "conditions" in reloaded.runs[0].model_fields_set
    assert conditions.model_fields_set == {
        "evaluation_split",
        "evaluation_protocol",
        "environment",
    }
    assert conditions.evaluation_split is None
    assert conditions.environment == {}
    assert conditions.evaluation_protocol.protocol_id == "protocol-1"
    assert "version" not in conditions.evaluation_protocol.model_fields_set
    assert reloaded.artifacts[0].dataset_id == "imagenet"
    assert reloaded.results[0].metric_definition is None
    assert "metric_definition" in reloaded.results[0].model_fields_set


def test_include_list_filters_preserve_entry_one_0_2_presence():
    manifest = Manifest.model_validate(_two_entry_doc())

    data = manifest.model_dump(
        include={
            "schema_version": True,
            "project": True,
            "runs": {1},
            "artifacts": {1},
            "results": {1},
        }
    )

    assert [run["id"] for run in data["runs"]] == ["run-1"]
    assert data["runs"][0]["conditions"]["evaluation_split"] is None
    assert data["artifacts"][0]["dataset_id"] == "imagenet"
    assert data["results"][0]["metric_definition"] is None

    reloaded = Manifest.model_validate(data)
    assert reloaded.runs[0].conditions.environment == {}
    assert reloaded.artifacts[0].dataset_id == "imagenet"
    assert "metric_definition" in reloaded.results[0].model_fields_set


def test_model_dump_json_0_1_round_trips_without_0_2_keys():
    text = Manifest.model_validate(doc_0_1()).model_dump_json()
    data = json.loads(text)

    assert by_path(data, "conditions") == []
    assert by_path(data, "dataset_id") == []
    assert by_path(data, "metric_definition") == []
    assert Manifest.model_validate_json(text) == Manifest.model_validate(doc_0_1())


def test_model_dump_json_0_2_preserves_presence_and_nested_omissions():
    document = doc_0_2()
    document["runs"][0]["conditions"] = {
        "evaluation_split": None,
        "evaluation_protocol": {"protocol_id": "protocol-1"},
        "environment": {},
    }
    document["results"][0]["metric_definition"] = {"definition_id": "accuracy-v1"}
    manifest = Manifest.model_validate(document)

    text = manifest.model_dump_json(indent=2)
    data = json.loads(text)

    assert "\n" in text
    assert data["runs"][0]["conditions"] == {
        "evaluation_split": None,
        "evaluation_protocol": {"protocol_id": "protocol-1"},
        "environment": {},
    }
    assert "version" not in data["runs"][0]["conditions"]["evaluation_protocol"]
    assert data["results"][0]["metric_definition"] == {
        "definition_id": "accuracy-v1"
    }
    assert "dataset_id" not in data["artifacts"][0]

    reloaded = Manifest.model_validate_json(text)
    conditions = reloaded.runs[0].conditions
    assert conditions.evaluation_split is None
    assert conditions.environment == {}
    assert conditions.evaluation_protocol.protocol_id == "protocol-1"
    assert "version" not in conditions.evaluation_protocol.model_fields_set
    assert reloaded.results[0].metric_definition.model_fields_set == {
        "definition_id"
    }


def test_model_dump_json_preserves_explicit_null_and_empty_members():
    document = doc_0_2()
    document["runs"][0]["conditions"] = {
        "evaluation_protocol": None,
        "source_revision": None,
        "environment": {},
    }
    document["results"][0]["metric_definition"] = None
    manifest = Manifest.model_validate(document)

    data = json.loads(manifest.model_dump_json())

    assert data["runs"][0]["conditions"] == {
        "evaluation_protocol": None,
        "source_revision": None,
        "environment": {},
    }
    assert data["results"][0]["metric_definition"] is None
    reloaded = Manifest.model_validate_json(manifest.model_dump_json())
    assert reloaded.runs[0].conditions.evaluation_protocol is None
    assert reloaded.runs[0].conditions.source_revision is None
    assert reloaded.runs[0].conditions.environment == {}
    assert "metric_definition" in reloaded.results[0].model_fields_set
    assert reloaded.results[0].metric_definition is None


def test_model_dump_json_nested_models_drop_only_unset_0_2_members():
    assert EvaluationProtocol(protocol_id="p").model_dump_json() == (
        '{"protocol_id":"p"}'
    )
    assert MetricDefinition(definition_id="d").model_dump_json() == (
        '{"definition_id":"d"}'
    )
    assert RunConditions().model_dump_json() == "{}"
    assert RunConditions(
        evaluation_protocol=EvaluationProtocol(protocol_id="p"),
        environment={},
    ).model_dump_json() == (
        '{"evaluation_protocol":{"protocol_id":"p"},"environment":{}}'
    )


def test_model_dump_json_exclude_list_filters_preserve_entry_one_presence():
    manifest = Manifest.model_validate(_two_entry_doc())

    text = manifest.model_dump_json(
        exclude={"runs": {0}, "artifacts": {0}, "results": {0}}
    )
    data = json.loads(text)

    assert [run["id"] for run in data["runs"]] == ["run-1"]
    assert data["runs"][0]["conditions"]["evaluation_split"] is None
    reloaded = Manifest.model_validate_json(text)
    assert reloaded.runs[0].conditions.environment == {}
    assert reloaded.artifacts[0].dataset_id == "imagenet"
    assert "metric_definition" in reloaded.results[0].model_fields_set


# ------------------------------------------------------------ add / rehash


def _write_experiment(root: Path, version: str) -> tuple[object, dict]:
    root.mkdir()
    (root / "data.csv").write_bytes(b"dataset bytes\n")
    digest = hashlib.sha256(b"dataset bytes\n").hexdigest()
    doc = doc_0_1() if version == "0.1" else doc_0_2()
    doc["artifacts"][0].update(sha256=digest, size_bytes=14)
    if version == "0.2":
        doc["runs"][0]["conditions"] = {"evaluation_split": "test"}
        doc["artifacts"][0]["dataset_id"] = "imagenet"
        doc["results"][0]["metric_definition"] = {"definition_id": "accuracy"}
    (root / "runevidence.json").write_text(
        json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    loaded = load_manifest(root)
    return loaded, json.loads((root / "runevidence.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("version", ["0.1", "0.2"])
def test_add_works_on_both_versions(tmp_path, version):
    root = tmp_path / "experiment"
    loaded, raw = _write_experiment(root, version)
    (root / "new.txt").write_bytes(b"new bytes\n")

    plan = plan_add(loaded, raw, [Path("new.txt")], cwd=root)

    assert len(plan.added) == 1
    entry = plan.added[0]
    assert entry["id"] == "new.txt"
    assert entry["sha256"] == hashlib.sha256(b"new bytes\n").hexdigest()
    assert "dataset_id" not in entry
    assert Manifest.model_validate(raw).schema_version == version

    (root / "runevidence.json").write_text(dump_raw(raw), encoding="utf-8", newline="\n")
    reloaded = load_manifest(root)
    assert reloaded.manifest.schema_version == version
    if version == "0.2":
        assert reloaded.manifest.runs[0].conditions.evaluation_split == "test"
        assert reloaded.manifest.artifacts[0].dataset_id == "imagenet"
        assert reloaded.manifest.results[0].metric_definition.definition_id == "accuracy"


@pytest.mark.parametrize("version", ["0.1", "0.2"])
def test_rehash_works_on_both_versions(tmp_path, version):
    root = tmp_path / "experiment"
    loaded, raw = _write_experiment(root, version)
    (root / "data.csv").write_bytes(b"changed bytes\n")
    new_digest = hashlib.sha256(b"changed bytes\n").hexdigest()

    plan = plan_rehash(loaded, raw, [0])

    entry = raw["artifacts"][0]
    assert entry["sha256"] == new_digest
    assert entry["size_bytes"] == 14
    assert plan.files[0].changed
    assert Manifest.model_validate(raw).schema_version == version

    (root / "runevidence.json").write_text(dump_raw(raw), encoding="utf-8", newline="\n")
    reloaded = load_manifest(root)
    if version == "0.2":
        assert reloaded.manifest.runs[0].conditions.evaluation_split == "test"
        assert reloaded.manifest.artifacts[0].dataset_id == "imagenet"
        assert reloaded.manifest.results[0].metric_definition.definition_id == "accuracy"
    data = json.loads(dump_manifest(reloaded.manifest))
    if version == "0.2":
        assert by_path(data, "conditions") != []
    else:
        assert by_path(data, "conditions") == []
