"""P4R2 numeric JSON type compatibility for the recorded-value comparison."""
import json

from runevidence.models import Manifest, Project, Result, Run
from runevidence.run_compare import _delta, compare_runs


def _result_delta(baseline, target):
    document = compare_runs(
        Manifest(
            project=Project(id="sample", name="Sample"),
            runs=[Run(id="before"), Run(id="after")],
            results=[
                Result(id="result_before", run_id="before", name="score", value=baseline),
                Result(id="result_after", run_id="after", name="score", value=target),
            ],
        ),
        "before", "after", manifest_path="synthetic",
    )
    row = json.loads(json.dumps(document, allow_nan=False))["results"][0]
    return row


def test_floating_pair_keeps_float_delta_type_in_json():
    row = _result_delta(1.0, 3.0)
    assert row["status"] == "changed"
    assert type(row["delta"]) is float and row["delta"] == 2.0
    assert '"delta": 2.0' in json.dumps(row)


def test_float_operand_near_integer_precision_boundary_keeps_float_type():
    row = _result_delta(2**53 + 1, float(2**53))
    assert row["status"] == "changed"
    assert type(row["delta"]) is float and row["delta"] == -1.0
    reverse = _result_delta(float(2**53), 2**53 + 1)
    assert type(reverse["delta"]) is float and reverse["delta"] == 1.0


def test_two_integers_still_produce_exact_integer_delta():
    row = _result_delta(2**60 + 1, 2**60 + 3)
    assert type(row["delta"]) is int and row["delta"] == 2


def test_float_zero_delta_and_smallest_positive_subnormal_remain_floats():
    same = _result_delta(3.0, 3.0)
    assert same["status"] == "same" and type(same["delta"]) is float and same["delta"] == 0.0
    tiny = _result_delta(0.0, 5e-324)
    assert tiny["status"] == "changed" and type(tiny["delta"]) is float and tiny["delta"] == 5e-324


def test_unrepresentable_float_differences_are_omitted_not_falsely_zero():
    overflow = _result_delta(-1e308, 1e308)
    assert overflow["status"] == "changed" and "delta" not in overflow
    huge_integer_mix = _result_delta(10**400, 1.0)
    assert huge_integer_mix["status"] == "changed" and "delta" not in huge_integer_mix
    assert _delta(float("nan"), 1.0) is None
    assert _delta(-float("inf"), 1.0) is None
