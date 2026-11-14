"""Run-comparison correctness around mixed precision and nested JSON values."""

import json
from pathlib import Path

from runevidence.cli import main
from runevidence.manifest import dump_manifest
from runevidence.models import Manifest, Project, Result, Run
from runevidence.run_compare import compare_runs, format_comparison


def _manifest(*, results=None, base_params=None, target_params=None):
    return Manifest(
        project=Project(id="demo", name="Demo"),
        runs=[Run(id="baseline", params=base_params or {}),
              Run(id="target", params=target_params or {})],
        results=results or [],
    )


def test_mixed_float_int_precision_does_not_invent_zero_delta(tmp_path, capsys):
    manifest = _manifest(results=[
        Result(id="b", run_id="baseline", name="score", value=2**53 + 1),
        Result(id="t", run_id="target", name="score", value=float(2**53)),
    ])
    root = tmp_path / "experiment"
    root.mkdir()
    (root / "runevidence.json").write_text(dump_manifest(manifest), encoding="utf-8")
    rc = main(["compare", str(root), "--base", "baseline", "--target", "target", "--json"])
    captured = capsys.readouterr()
    assert rc == 0 and not captured.err
    row = json.loads(captured.out)["results"][0]
    assert row["status"] == "changed"
    assert row["delta"] == -1  # previously emitted misleading 0.0


def test_nested_boolean_vs_integer_is_a_change():
    manifest = _manifest(base_params={"nested": {"flag": True, "items": [1, False]}},
                         target_params={"nested": {"flag": 1, "items": [1, 0]}})
    data = compare_runs(manifest, "baseline", "target", manifest_path="synthetic")
    assert data["parameters"][0]["status"] == "changed"
    assert data["summary"]["parameters_changed"] == 1


def test_finite_float_subtraction_overflow_omits_delta():
    manifest = _manifest(results=[
        Result(id="b", run_id="baseline", name="score", value=-1e308),
        Result(id="t", run_id="target", name="score", value=1e308),
    ])
    data = compare_runs(manifest, "baseline", "target", manifest_path="synthetic")
    assert data["results"][0]["status"] == "changed"
    assert "delta" not in data["results"][0]
    json.dumps(data, allow_nan=False)


def test_manifest_path_control_characters_cannot_spoof_lines():
    data = compare_runs(_manifest(), "baseline", "target",
                        manifest_path="synthetic\nERROR forged/runevidence.json")
    rendered = format_comparison(data)
    assert "synthetic\\nERROR forged" in rendered
    assert "\nERROR forged" not in rendered


def test_large_integer_integer_delta_remains_exact():
    manifest = _manifest(results=[
        Result(id="b", run_id="baseline", name="count", value=2**60 + 1),
        Result(id="t", run_id="target", name="count", value=2**60 + 3),
    ])
    data = compare_runs(manifest, "baseline", "target", manifest_path="synthetic")
    assert data["results"][0]["delta"] == 2
