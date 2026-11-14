"""Uniform presence-exclusion shape and large-list serialization cost (0.4 branch).

Pydantic 2.6 applies a per-index exclude tree quadratically while dumping a
long list. ``_presence_exclude`` therefore collapses a list whose entries all
carry the same nested exclusion into pydantic's ``{"__all__": nested}`` form.
These tests pin that shape, the mixed-presence fallback, caller exclude
interactions, and a capped large-list regression.
"""

from __future__ import annotations

import json
import time

import pytest

from runevidence.manifest import validate_manifest
from runevidence.models import (
    Artifact,
    Manifest,
    Manifest04,
    Run,
    _presence_exclude,
    _presence_exclude_value,
)


def _doc(n: int, version: str = "0.2") -> dict:
    return {
        "schema_version": version,
        "project": {"id": "proj", "name": "Project"},
        "runs": [{"id": f"run-{index}"} for index in range(n)],
        "artifacts": [
            {"id": f"data-{index}", "path": f"data-{index}.csv"}
            for index in range(n)
        ],
        "results": [
            {"id": f"res-{index}", "run_id": f"run-{index}", "name": "accuracy"}
            for index in range(n)
        ],
    }


def _doc_0_4(n: int) -> dict:
    doc = _doc(n, version="0.4")
    doc["artifacts"] = [{"id": "metrics", "path": "metrics.csv"}]
    for result in doc["results"]:
        result.update(
            value=0.5,
            artifact_id="metrics",
            locator={
                "kind": "csv-cell",
                "row": 1,
                "column": 2,
                "header": "val_rmse",
            },
        )
    return doc


def _mixed_doc() -> dict:
    doc = _doc(3)
    doc["runs"][1]["conditions"] = {
        "evaluation_split": "test",
        "evaluation_protocol": {"protocol_id": "protocol-1", "version": 1},
        "evaluation_dataset_artifact_id": "data-0",
        "model_checkpoint_artifact_id": "data-1",
        "source_revision": "abc123",
        "environment": {},
    }
    doc["artifacts"][1]["dataset_id"] = "imagenet"
    doc["results"][1]["metric_definition"] = {
        "definition_id": "accuracy-v1",
        "higher_is_better": True,
        "averaging": "macro",
        "version": 1,
    }
    doc["results"][2]["metric_definition"] = None
    return doc


def _dump(manifest: Manifest, json_mode: bool, **kwargs) -> dict:
    if json_mode:
        return json.loads(manifest.model_dump_json(**kwargs))
    return manifest.model_dump(**kwargs)


def test_uniform_presence_tree_uses_all_form_without_index_keys():
    manifest = Manifest.model_validate(_doc(3))

    tree = _presence_exclude(manifest)

    assert tree == {
        "runs": {"__all__": {"conditions": True}},
        "artifacts": {"__all__": {"dataset_id": True}},
        "results": {"__all__": {"metric_definition": True}},
    }
    for location in ("runs", "artifacts", "results"):
        assert list(tree[location]) == ["__all__"]
        assert not any(type(key) is int for key in tree[location])


def test_uniform_presence_tree_for_0_4_csv_cell_results():
    manifest = Manifest04.model_validate(_doc_0_4(3))

    tree = _presence_exclude(manifest)

    assert tree["results"] == {"__all__": {"metric_definition": True}}
    assert tree["artifacts"] == {"__all__": {"dataset_id": True}}
    assert tree["runs"] == {"__all__": {"conditions": True}}
    data = manifest.model_dump()
    assert [result["id"] for result in data["results"]] == [
        "res-0",
        "res-1",
        "res-2",
    ]
    assert all("metric_definition" not in result for result in data["results"])
    assert data["results"][0]["locator"]["kind"] == "csv-cell"


def test_presence_recursion_keeps_uniform_and_mixed_lists():
    assert _presence_exclude_value([Run(id="run-0"), Run(id="run-1")]) == {
        "__all__": {"conditions": True}
    }
    assert _presence_exclude_value(
        [
            Artifact(id="data-0", path="data-0.csv"),
            Artifact(id="data-1", path="data-1.csv", dataset_id="imagenet"),
        ]
    ) == {0: {"dataset_id": True}}


@pytest.mark.parametrize("json_mode", [False, True])
def test_mixed_presence_omits_only_unset_0_2_members(json_mode):
    manifest = Manifest.model_validate(_mixed_doc())

    tree = _presence_exclude(manifest)

    assert tree["runs"] == {0: {"conditions": True}, 2: {"conditions": True}}
    assert tree["artifacts"] == {0: {"dataset_id": True}, 2: {"dataset_id": True}}
    assert tree["results"] == {0: {"metric_definition": True}}

    data = _dump(manifest, json_mode)

    assert "conditions" not in data["runs"][0]
    assert "conditions" not in data["runs"][2]
    assert data["runs"][1]["conditions"] == {
        "evaluation_split": "test",
        "evaluation_protocol": {"protocol_id": "protocol-1", "version": 1},
        "evaluation_dataset_artifact_id": "data-0",
        "model_checkpoint_artifact_id": "data-1",
        "source_revision": "abc123",
        "environment": {},
    }
    assert "dataset_id" not in data["artifacts"][0]
    assert "dataset_id" not in data["artifacts"][2]
    assert data["artifacts"][1]["dataset_id"] == "imagenet"
    assert "metric_definition" not in data["results"][0]
    assert data["results"][1]["metric_definition"] == {
        "definition_id": "accuracy-v1",
        "higher_is_better": True,
        "averaging": "macro",
        "version": 1,
    }
    assert "metric_definition" in data["results"][2]
    assert data["results"][2]["metric_definition"] is None

    reloaded = Manifest.model_validate(data)
    assert "conditions" not in reloaded.runs[0].model_fields_set
    assert "dataset_id" not in reloaded.artifacts[0].model_fields_set
    assert "metric_definition" not in reloaded.results[0].model_fields_set
    assert "metric_definition" in reloaded.results[1].model_fields_set
    assert "metric_definition" in reloaded.results[2].model_fields_set
    assert reloaded.results[2].metric_definition is None


@pytest.mark.parametrize("json_mode", [False, True])
def test_uniform_presence_with_whole_entry_caller_exclude(json_mode):
    manifest = Manifest.model_validate(_doc(3))
    assert _presence_exclude(manifest)["results"] == {
        "__all__": {"metric_definition": True}
    }

    data = _dump(manifest, json_mode, exclude={"results": {0}})

    assert [result["id"] for result in data["results"]] == ["res-1", "res-2"]
    assert all("metric_definition" not in result for result in data["results"])
    reloaded = Manifest.model_validate(data)
    assert all(
        "metric_definition" not in result.model_fields_set
        for result in reloaded.results
    )


@pytest.mark.parametrize("json_mode", [False, True])
def test_uniform_presence_with_partial_and_all_caller_specs(json_mode):
    manifest = Manifest.model_validate(_doc(3))

    partial = _dump(manifest, json_mode, exclude={"results": {1: {"value"}}})

    assert [result["id"] for result in partial["results"]] == [
        "res-0",
        "res-1",
        "res-2",
    ]
    assert "value" not in partial["results"][1]
    assert all("metric_definition" not in result for result in partial["results"])
    Manifest.model_validate(partial)

    retained = _dump(
        manifest,
        json_mode,
        exclude={"results": {"__all__": True, 1: {"value"}}},
    )

    assert [result["id"] for result in retained["results"]] == ["res-1"]
    assert "value" not in retained["results"][0]
    assert "metric_definition" not in retained["results"][0]
    reloaded = Manifest.model_validate(retained)
    assert "metric_definition" not in reloaded.results[0].model_fields_set


@pytest.mark.parametrize("json_mode", [False, True])
def test_caller_partial_spec_on_the_presence_field_cannot_leak_it(json_mode):
    # A caller may exclude a subfield of a 0.2-only field. The presence rule
    # still excludes the whole field where it was never supplied; otherwise a
    # null would be written and reload as "explicitly recorded unknown".
    manifest = Manifest.model_validate(_doc(3))

    data = _dump(
        manifest,
        json_mode,
        exclude={"results": {1: {"metric_definition": {"version"}}}},
    )

    assert [result["id"] for result in data["results"]] == [
        "res-0",
        "res-1",
        "res-2",
    ]
    assert all("metric_definition" not in result for result in data["results"])
    reloaded = Manifest.model_validate(data)
    assert all(
        "metric_definition" not in result.model_fields_set
        for result in reloaded.results
    )


@pytest.mark.parametrize("json_mode", [False, True])
def test_caller_none_subs_spec_on_the_presence_field_is_safe(json_mode):
    # ``{"metric_definition": None}`` used to replace the presence ``True`` and
    # make pydantic raise PydanticSerializationError. It must be a no-op for a
    # field that was never supplied.
    manifest = Manifest.model_validate(_doc(3))

    data = _dump(
        manifest,
        json_mode,
        exclude={"results": {1: {"metric_definition": None}}},
    )

    assert len(data["results"]) == 3
    assert all("metric_definition" not in result for result in data["results"])
    reloaded = Manifest.model_validate(data)
    assert all(
        "metric_definition" not in result.model_fields_set
        for result in reloaded.results
    )


@pytest.mark.parametrize("version", ["0.1", "0.2"])
@pytest.mark.parametrize("index", [0, -1])
@pytest.mark.parametrize("json_mode", [False, True])
def test_nested_caller_filter_inside_an_omitted_field_is_safe(
    version, index, json_mode
):
    # Review repro: a per-index filter nested inside an unsupplied 0.2-only
    # field must not turn absence into an explicit null (or leak it in 0.1
    # JSON). Both index signs and both dump methods are covered.
    doc = {
        "schema_version": version,
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "r"}],
        "artifacts": [],
        "results": [],
        "evidence": [],
    }
    manifest = validate_manifest(doc)

    data = _dump(
        manifest,
        json_mode,
        exclude={"runs": {index: {"conditions": {"environment"}}}},
    )

    assert "conditions" not in data["runs"][0]
    validate_manifest(data)


@pytest.mark.parametrize("version", ["0.1", "0.2"])
@pytest.mark.parametrize("index", [0, -1])
@pytest.mark.parametrize("json_mode", [False, True])
def test_caller_all_and_explicit_entry_inside_an_omitted_field(
    version, index, json_mode
):
    # Review repro: a partial caller __all__ plus an explicit entry that
    # filters inside the same omitted field. The presence rule must survive
    # both and fold into the explicit entry.
    doc = {
        "schema_version": version,
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "r"}],
        "artifacts": [],
        "results": [],
        "evidence": [],
    }
    manifest = validate_manifest(doc)

    data = _dump(
        manifest,
        json_mode,
        exclude={
            "runs": {
                "__all__": {"conditions": True},
                index: {"conditions": {"environment"}},
            }
        },
    )

    assert "conditions" not in data["runs"][0]
    validate_manifest(data)


def _mixed_runs_doc(version="0.2"):
    return {
        "schema_version": version,
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "r0"}, {"id": "r1", "conditions": {}}],
        "artifacts": [],
        "results": [],
        "evidence": [],
    }


@pytest.mark.parametrize("version", ["0.2"])
@pytest.mark.parametrize("index", [0, -2])
@pytest.mark.parametrize("json_mode", [False, True])
def test_mixed_presence_with_all_and_explicit_entry(version, index, json_mode):
    # Review repro: the caller's partial __all__ and its explicit
    # entry must not override the per-index presence rule for the unset run.
    manifest = validate_manifest(_mixed_runs_doc(version))
    data = _dump(
        manifest,
        json_mode,
        exclude={
            "runs": {
                "__all__": {"conditions": True},
                index: {"conditions": {"environment"}},
            }
        },
    )
    # The caller's __all__ excludes conditions on every entry; the run that
    # set conditions is not exempt just because presence does not touch it.
    assert all("conditions" not in run for run in data["runs"])
    validate_manifest(data)


@pytest.mark.parametrize("version", ["0.2"])
@pytest.mark.parametrize("index,kept", [(2, "r0"), (-3, "r1")])
@pytest.mark.parametrize("json_mode", [False, True])
def test_whitelist_presence_does_not_retain_extra_entries(
    version, index, kept, json_mode
):
    # A dict entry under __all__: True is a retention whitelist. Presence rules
    # must not add an entry the caller excluded; pydantic wraps the
    # out-of-range index with modulo, so the folded rule follows the wrapped
    # entry.
    manifest = validate_manifest(_mixed_runs_doc(version))
    data = _dump(
        manifest,
        json_mode,
        exclude={"runs": {"__all__": True, index: {"name"}}},
    )
    assert [run["id"] for run in data["runs"]] == [kept]
    assert "name" not in data["runs"][0]
    validate_manifest(data)


@pytest.mark.parametrize("json_mode", [False, True])
@pytest.mark.parametrize("sub", [None, False])
def test_caller_none_or_false_subs_spec_on_presence_field(sub, json_mode):
    # Both values crashed or leaked on d931de9; neither may change the absence
    # of a field that was never supplied.
    manifest = Manifest.model_validate(_doc(3))
    data = _dump(
        manifest,
        json_mode,
        exclude={"results": {1: {"metric_definition": sub}}},
    )
    assert all("metric_definition" not in result for result in data["results"])
    reloaded = Manifest.model_validate(data)
    assert all(
        "metric_definition" not in result.model_fields_set
        for result in reloaded.results
    )


@pytest.mark.parametrize(
    "spec",
    [{"__all__"}, {"__all__", 0}, {"__all__", -1}, frozenset({"__all__"})],
)
@pytest.mark.parametrize("json_mode", [False, True])
def test_set_containing_all_excludes_every_entry(spec, json_mode):
    # A set containing __all__ excludes every entry; indices in the same set do
    # not retain anything. Presence must not turn one into a dict whitelist.
    from pydantic import BaseModel

    manifest = validate_manifest(_mixed_runs_doc())
    exclude = {"runs": spec}
    if json_mode:
        plain = json.loads(BaseModel.model_dump_json(manifest, exclude=exclude))
        ours = json.loads(manifest.model_dump_json(exclude=exclude))
    else:
        plain = BaseModel.model_dump(manifest, exclude=exclude)
        ours = manifest.model_dump(exclude=exclude)
    assert ours == plain
    assert ours["runs"] == []


@pytest.mark.parametrize("version", ["0.2"])
@pytest.mark.parametrize("all_spec", [{"conditions": True}, {"conditions"}])
@pytest.mark.parametrize("json_mode", [False, True])
def test_caller_all_partial_drops_the_field_on_every_entry(
    version, all_spec, json_mode
):
    # A caller __all__ partial applies to entries that do not name the field,
    # including an entry that presence would otherwise touch. A presence
    # subfield rule must never override the caller's whole-field True.
    manifest = validate_manifest(_mixed_runs_doc(version))
    data = _dump(
        manifest, json_mode, exclude={"runs": {"__all__": all_spec}}
    )
    assert all("conditions" not in run for run in data["runs"])
    validate_manifest(data)


@pytest.mark.parametrize("version", ["0.2"])
@pytest.mark.parametrize("entry", [0, 1])
@pytest.mark.parametrize("json_mode", [False, True])
def test_caller_all_partial_with_an_entry_that_omits_the_field(
    version, entry, json_mode
):
    # An explicit entry that does not name the field must not override the
    # caller's __all__ rule, whether presence touches that entry or not.
    manifest = validate_manifest(_mixed_runs_doc(version))
    data = _dump(
        manifest,
        json_mode,
        exclude={"runs": {"__all__": {"conditions": True}, entry: set()}},
    )
    assert all("conditions" not in run for run in data["runs"])
    validate_manifest(data)


@pytest.mark.parametrize("json_mode", [False, True])
def test_caller_all_and_entry_naming_the_same_field_keeps_it(json_mode):
    # Plain pydantic lets an explicit entry override __all__ on a shared key.
    # The presence merge must not silently drop the field the entry kept.
    doc = {
        "schema_version": "0.2",
        "project": {"id": "p", "name": "P"},
        "runs": [
            {"id": "r0", "params": {"a": 1}},
            {"id": "r1", "params": {"b": 2}, "conditions": {}},
        ],
        "artifacts": [],
        "results": [],
        "evidence": [],
    }
    manifest = validate_manifest(doc)
    data = _dump(
        manifest,
        json_mode,
        exclude={"runs": {"__all__": {"params"}, 1: {"params": {}}}},
    )
    assert "params" not in data["runs"][0]
    assert data["runs"][1]["params"] == {"b": 2}
    validate_manifest(data)


def test_large_uniform_list_dumps_without_unset_members():
    # 2000 entries is enough to expose the quadratic 2.6 tree without making
    # the test itself a memory probe. The shape assertion is the regression
    # signal; the wall-clock bound is a coarse guard against a blow-up.
    n = 2000
    manifest = Manifest04.model_validate(_doc_0_4(n))
    assert _presence_exclude(manifest)["results"] == {
        "__all__": {"metric_definition": True}
    }

    started = time.perf_counter()
    data = manifest.model_dump()
    elapsed = time.perf_counter() - started

    assert len(data["results"]) == n
    assert all("metric_definition" not in result for result in data["results"])
    assert elapsed < 5.0

    text = manifest.model_dump_json()
    assert '"metric_definition"' not in text
    assert len(json.loads(text)["results"]) == n
