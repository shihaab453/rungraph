"""Manifest JSON accepts legacy non-finite tokens only in result values."""

import json
import math

import pytest

from conftest import DEEP_JSON_PARSES
from runevidence.cli import main
from runevidence.findings import Code
from runevidence.manifest import (
    ManifestLoadError, load_manifest, load_raw, save_manifest,
)
from runevidence.models import Manifest, Project, Result, Run


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e9999", "-1e9999"])
@pytest.mark.parametrize("location", ['"probe": TOKEN', '"probe": [{"nested": TOKEN}]'])
def test_nonfinite_numbers_fail_closed(experiment, capsys, token, location):
    path = experiment / "runevidence.json"
    source = path.read_text(encoding="utf-8")
    source = source.replace('"seed": 0', '"seed": 0, ' + location.replace("TOKEN", token), 1)
    data = source.encode("utf-8")
    path.write_bytes(data)

    with pytest.raises(ManifestLoadError) as error:
        load_manifest(path)
    assert error.value.findings[0].code == Code.manifest_unreadable
    assert "non-finite number" in error.value.findings[0].message
    assert isinstance(error.value.__cause__, ValueError)
    for command in ("validate", "fingerprint"):
        assert main([command, str(path), "--json"]) == 2
        report = json.loads(capsys.readouterr().out)
        assert report["findings"][0]["code"] == "manifest-unreadable"
    assert path.read_bytes() == data


def test_finite_numbers_and_numeric_strings_keep_decoder_semantics(experiment):
    path = experiment / "runevidence.json"
    source = path.read_text(encoding="utf-8")
    values = '[0, -12, 1.25, 1e2, -0.0, 1e-320, 1e-9999, 1e308, 123456789012345678901234567890, "NaN", "Infinity", "-Infinity"]'
    source = source.replace('"seed": 0', '"seed": 0, "probe": ' + values, 1)
    path.write_text(source, encoding="utf-8")
    loaded = load_manifest(path)
    actual = loaded.manifest.runs[0].params["probe"]
    expected = json.loads(values)
    assert actual == expected
    assert [type(v) for v in actual] == [type(v) for v in expected]
    assert math.copysign(1, actual[4]) == -1
    assert load_raw(loaded)["runs"][0]["params"]["probe"] == expected


# --- legacy non-finite tokens: only results[*].value -----------------------


def _source(value="1", *, runs='[{"id": "r"}]', results=None, root_extra=""):
    if results is None:
        results = '[{"id": "x", "run_id": "r", "name": "n", "value": ' + value + '}]'
    return ('{"schema_version": "0.1", "project": {"id": "p", "name": "P"},'
            + root_extra + '"runs": ' + runs + ', "results": ' + results + '}')


def _write(tmp_path, text):
    path = tmp_path / "runevidence.json"
    path.write_text(text, encoding="utf-8")
    return path


def _assert_unreadable(path, text="non-finite number"):
    with pytest.raises(ManifestLoadError) as error:
        load_manifest(path)
    [finding] = error.value.findings
    assert finding.code is Code.manifest_unreadable
    assert text in finding.message
    assert isinstance(error.value.__cause__, ValueError)
    return finding


def test_committed_mlp_sweep_example_loads_its_nan_result():
    from conftest import REPO

    loaded = load_manifest(REPO / "examples" / "dogfood-mlp-sweep")
    [diverged] = [r for r in loaded.manifest.results
                  if isinstance(r.value, float) and math.isnan(r.value)]
    assert diverged.id == "job2-final-loss"


@pytest.mark.parametrize(
    ("token", "check"),
    [
        ("NaN", math.isnan),
        ("Infinity", lambda value: value == math.inf),
        ("-Infinity", lambda value: value == -math.inf),
    ],
)
def test_legacy_nonfinite_token_loads_at_results_value(tmp_path, token, check):
    value = load_manifest(_write(tmp_path, _source(token))).manifest.results[0].value
    assert type(value) is float
    assert check(value)


def test_every_result_value_can_hold_a_legacy_nonfinite_token(tmp_path):
    results = (
        '[{"id": "a", "run_id": "r", "name": "n", "value": NaN},'
        ' {"id": "b", "run_id": "r", "name": "n", "value": Infinity},'
        ' {"id": "c", "run_id": "r", "name": "n", "value": -Infinity}]'
    )
    values = [r.value for r in load_manifest(
        _write(tmp_path, _source(results=results))).manifest.results]
    assert math.isnan(values[0])
    assert values[1] == math.inf
    assert values[2] == -math.inf


_REJECTED = [
    pytest.param(_source("[NaN]"), id="value-list"),
    pytest.param(_source('{"x": NaN}'), id="value-object"),
    pytest.param(_source("1", results='[{"id": NaN, "run_id": "r", "name": "n", "value": 1}]'),
                 id="result-id"),
    pytest.param(_source("1", results='[{"id": "x", "run_id": "r", "name": NaN, "value": 1}]'),
                 id="result-name"),
    pytest.param(_source("1", results='[{"id": "x", "run_id": "r", "name": "n", "value": 1,'
                                     ' "artifact_id": NaN}]'), id="result-artifact-id"),
    pytest.param(_source("1", results='[{"id": "x", "run_id": "r", "name": "n", "value": 1,'
                                     ' "locator": NaN}]'), id="result-locator"),
    pytest.param(_source("1", runs='[{"id": "r", "params": {"seed": NaN}}]'), id="run-params"),
    pytest.param(_source("1", results='[{"id": "x", "run_id": "r", "name": "n", "value": 1,'
                                     ' "metadata": {"k": NaN}}]'), id="result-metadata"),
    pytest.param(_source("1", results='{"0": {"id": "x", "run_id": "r", "name": "n",'
                                     ' "value": NaN}}'), id="results-dict"),
    pytest.param('{"schema_version": "0.1", "project": {"id": "p", "name": "P",'
                 ' "metadata": {"x": {"results": [{"value": NaN}]}}},'
                 ' "runs": [{"id": "r"}], "results": []}', id="nested-imitation"),
    pytest.param(_source("1", results='[{"id": "x", "run_id": "r", "name": "n", "value": 1,'
                                     ' "metadata": {"a": [{"b": [NaN]}]}}]'), id="deep-nesting"),
    pytest.param(_source("1", root_extra='"extra": NaN,'), id="unknown-root-field"),
    pytest.param("NaN", id="bare-nan"),
    pytest.param("[NaN]", id="top-level-array"),
    pytest.param(_source("1e9999"), id="result-overflow"),
    pytest.param(_source("-1e9999"), id="result-negative-overflow"),
    pytest.param(_source("1", runs='[{"id": "r", "params": {"x": 1e9999}}]'),
                 id="params-overflow"),
]


@pytest.mark.parametrize("source", _REJECTED)
def test_nonfinite_tokens_are_rejected_outside_result_values(tmp_path, source):
    _assert_unreadable(_write(tmp_path, source))


_DUPLICATES = [
    pytest.param('{"schema_version": "0.1", "project": {"id": "p", "name": "P"},'
                 ' "runs": [{"id": "r"}], "results": [], "results": []}',
                 id="duplicate-results"),
    pytest.param(_source("1", results='[{"id": "x", "run_id": "r", "name": "n",'
                                     ' "value": 1, "value": 2}]'), id="duplicate-value-finite"),
    pytest.param(_source("1", results='[{"id": "x", "run_id": "r", "name": "n",'
                                     ' "value": NaN, "value": 2}]'), id="duplicate-value-nan-first"),
    pytest.param(_source("1", results='[{"id": "x", "run_id": "r", "name": "n",'
                                     ' "value": 2, "value": NaN}]'), id="duplicate-value-nan-last"),
    pytest.param(_source("1", results='[{"id": "x", "run_id": "r", "name": "n",'
                                     ' "value": 1, "\\u0076alue": 2}]'),
                 id="duplicate-value-escaped-equivalent"),
]


@pytest.mark.parametrize("source", _DUPLICATES)
def test_duplicate_results_and_value_keys_stay_unreadable(tmp_path, source):
    _assert_unreadable(_write(tmp_path, source), text="duplicate object keys")


def test_finite_values_keep_types_signs_and_strings(tmp_path):
    values = [True, False, None, -0.0, 1e-320, 1e-9999, int("9" * 4000),
              "NaN", "Infinity", "-Infinity"]
    source = _source("1", runs='[{"id": "r", "params": {"probe": '
                     + json.dumps(values) + '}}]')
    actual = load_manifest(_write(tmp_path, source)).manifest.runs[0].params["probe"]
    expected = json.loads(json.dumps(values))
    assert actual == expected
    assert [type(v) for v in actual] == [type(v) for v in expected]
    assert math.copysign(1, actual[3]) == -1
    assert actual[6] == int("9" * 4000) and type(actual[6]) is int
    assert actual[7:] == ["NaN", "Infinity", "-Infinity"]


def test_large_integer_result_value_is_not_overflow_checked(tmp_path):
    digits = "9" * 4000
    value = load_manifest(_write(tmp_path, _source(digits))).manifest.results[0].value
    assert type(value) is int
    assert value == int(digits)


def test_nonfinite_result_values_survive_save_and_load(tmp_path):
    manifest = Manifest(
        project=Project(id="p", name="P"),
        runs=[Run(id="r")],
        results=[
            Result(id="nan", run_id="r", name="n", value=math.nan),
            Result(id="pos", run_id="r", name="n", value=math.inf),
            Result(id="neg", run_id="r", name="n", value=-math.inf),
        ],
    )
    path = tmp_path / "runevidence.json"
    save_manifest(manifest, path)
    assert '"value": NaN' in path.read_text(encoding="utf-8")
    values = [r.value for r in load_manifest(path).manifest.results]
    assert math.isnan(values[0])
    assert values[1] == math.inf
    assert values[2] == -math.inf


def test_programmatic_nonfinite_params_and_metadata_serialize_to_null(tmp_path):
    manifest = Manifest(
        project=Project(id="p", name="P"),
        runs=[Run(id="r", params={"x": math.nan}, metadata={"y": math.inf})],
    )
    path = tmp_path / "runevidence.json"
    save_manifest(manifest, path)
    loaded = load_manifest(path).manifest.runs[0]
    assert loaded.params == {"x": None}
    assert loaded.metadata == {"y": None}


# --- M1 hardening probes ----------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            '{"schema_version": "0.1", "project": {"id": "p", "name": "P"},'
            ' "runs": [{"id": "r"}], "results": [],'
            ' "рesults": [{"value": NaN}]}',
            id="root-unknown-field"),
        pytest.param(
            _source("1", results='[{"id": "x", "run_id": "r", "name": "n",'
                                 ' "value": {"рesults": [{"value": NaN}]}}]'),
            id="nested-imitation"),
    ],
)
def test_confusable_results_keys_do_not_permit_nonfinite_tokens(tmp_path, source):
    # "рesults" starts with Cyrillic small er (U+0440), not ASCII "r".
    finding = _assert_unreadable(_write(tmp_path, source))
    assert finding.code is Code.manifest_unreadable


@pytest.mark.parametrize(
    "value",
    [
        '{"_NonFiniteToken": "NaN"}',
        '{"text": "NaN"}',
        '{"_NonFiniteToken": "Infinity", "text": "-Infinity"}',
    ],
)
def test_submitted_nonfinite_marker_imitations_are_schema_invalid(tmp_path, value):
    path = _write(tmp_path, _source(value))
    with pytest.raises(ManifestLoadError) as error:
        load_manifest(path)
    findings = error.value.findings
    assert findings
    for finding in findings:
        assert finding.code is Code.schema_invalid
        assert finding.issue_location == "/results/0/value"
        assert "_NonFiniteToken" not in finding.message
    assert error.value.__cause__ is None


@pytest.mark.parametrize("text", ["NaN", "Infinity", "-Infinity"])
def test_string_nonfinite_names_at_result_value_stay_strings(tmp_path, text):
    value = load_manifest(
        _write(tmp_path, _source(json.dumps(text)))).manifest.results[0].value
    assert type(value) is str
    assert value == text


def _nested_arrays_to_nan(depth):
    return '"extra": ' + "[" * depth + '{"x": NaN}' + "]" * depth + ","


def test_nan_below_deep_unknown_nesting_is_unreadable_not_recursive(tmp_path):
    path = _write(tmp_path, _source("1", results="[]",
                                    root_extra=_nested_arrays_to_nan(600)))
    with pytest.raises(ManifestLoadError) as error:
        load_manifest(path)
    [finding] = error.value.findings
    assert finding.code is Code.manifest_unreadable
    assert "non-finite number" in finding.message
    assert isinstance(error.value.__cause__, ValueError)
    assert not isinstance(error.value.__cause__, RecursionError)


def test_json_recursion_failure_stays_a_controlled_unreadable_error(tmp_path, capsys):
    depth = 20000
    source = ('{"schema_version": "0.1", "project": {"id": "p", "name": "P"},'
              ' "runs": [{"id": "r"}], "results": [], "extra": '
              + "[" * depth + "NaN" + "]" * depth + "}")
    path = _write(tmp_path, source)
    with pytest.raises(ManifestLoadError) as error:
        load_manifest(path)
    [finding] = error.value.findings
    assert finding.code is Code.manifest_unreadable
    assert "Could not read manifest as UTF-8 JSON" in finding.message
    if DEEP_JSON_PARSES:
        # 3.14 parses this depth, so the embedded NaN token is the controlled
        # refusal; earlier versions stop at the decoder recursion limit. Both
        # are the same unreadable error, and the cause type is not pinned.
        assert "non-finite number" in finding.message
    assert main(["validate", str(path), "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert [f["code"] for f in report["findings"]] == ["manifest-unreadable"]
