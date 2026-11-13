"""Pure reporting contract tests: no CLI, no filesystem fixture or adapter discovery."""
import json
from pathlib import Path

from runevidence.checks import Report
from runevidence.findings import Code, Finding, Severity
from runevidence.models import Manifest, Project, Run
from runevidence.report import format_overview, to_json


def make_manifest(*runs):
    return Manifest(project=Project(id="demo", name="Demo"), runs=list(runs))


def make_report(*findings):
    return Report(manifest_path=Path("runevidence.json"), findings=list(findings))


def test_mlflow_text_and_json_visibility():
    manifest = make_manifest(Run(id="one", metadata={"mlflow": {"unreadable": ["metrics/val_loss"]}}))
    text = format_overview(manifest)
    assert "Inputs the adapter could not read (1):" in text
    assert "  one (mlflow): metrics/val_loss" in text
    payload = json.loads(to_json(make_report(), manifest=manifest))
    assert payload["summary"]["adapter_unreadable"] == [
        {"run": "one", "adapter": "mlflow", "path": "metrics/val_loss"}
    ]
    assert payload["ok"] is True
    assert payload["exit_code"] == 0
    assert payload["findings"] == []


def test_hydra_reason_and_missing_reason():
    manifest = make_manifest(Run(id="one", metadata={"hydra": {
        "unreadable": [".hydra/config.yaml", ".hydra/hydra.yaml"],
        "unreadable_reasons": {".hydra/config.yaml": "invalid YAML"}
    }}))
    text = format_overview(manifest)
    assert "one (hydra): .hydra/config.yaml\n    invalid YAML" in text
    assert "one (hydra): .hydra/hydra.yaml\n" in text
    got = json.loads(to_json(make_report(), manifest=manifest))["summary"]["adapter_unreadable"]
    assert got == [
        {"run": "one", "adapter": "hydra", "path": ".hydra/config.yaml", "reason": "invalid YAML"},
        {"run": "one", "adapter": "hydra", "path": ".hydra/hydra.yaml"},
    ]


def test_no_unreadable_exact_legacy_text_json():
    manifest = make_manifest(Run(id="one"))
    assert format_overview(manifest) == (
        "Project: Demo (demo)\n\nRuns (1):\n  one: 0 artifacts, 0 results\n\nArtifacts (0):"
    )
    payload = json.loads(to_json(make_report(), manifest=manifest))
    assert payload == {
        "manifest": "runevidence.json", "ok": True, "exit_code": 0,
        "counts": {"error": 0, "warning": 0, "info": 0}, "findings": [],
        "summary": {"project": "demo", "runs": 1, "artifacts": 0, "results": 0, "evidence": 0},
    }


def test_malformed_shapes_are_ignored_and_valid_items_survive():
    manifest = make_manifest(
        Run(id="none", metadata={"hydra": [1], "mlflow": {"unreadable": "not a list"}}),
        Run(id="many", metadata={"hydra": {
            "unreadable": [None, 123, {}, "ok", "second"],
            "unreadable_reasons": ["wrong shape"],
        }}),
        Run(id="bad", metadata={"mlflow": {"unreadable": {"not": "list"}}}),
    )
    entries = json.loads(to_json(make_report(), manifest=manifest))["summary"]["adapter_unreadable"]
    assert entries == [
        {"run": "many", "adapter": "hydra", "path": "ok"},
        {"run": "many", "adapter": "hydra", "path": "second"},
    ]


def test_order_is_run_then_namespace_then_list_order():
    manifest = make_manifest(
        Run(id="first", metadata={"mlflow": {"unreadable": ["m2", "m1"]},
                                  "hydra": {"unreadable": ["h2", "h1"]}}),
        Run(id="second", metadata={"mlflow": {"unreadable": ["end"]}}),
    )
    entries = json.loads(to_json(make_report(), manifest=manifest))["summary"]["adapter_unreadable"]
    assert [(e["run"], e["adapter"], e["path"]) for e in entries] == [
        ("first", "hydra", "h2"), ("first", "hydra", "h1"),
        ("first", "mlflow", "m2"), ("first", "mlflow", "m1"),
        ("second", "mlflow", "end"),
    ]


def test_text_cap_is_per_run_json_preserves_all_entries():
    manifest = make_manifest(
        Run(id="first", metadata={"hydra": {"unreadable": [f"f{i}" for i in range(25)]}}),
        Run(id="second", metadata={"mlflow": {"unreadable": ["ok"]}}),
    )
    text = format_overview(manifest)
    assert "Inputs the adapter could not read (26):" in text
    assert "  first (hydra): f19" in text
    assert "  first (hydra): f20" not in text
    assert "    and 5 more" in text
    assert "  second (mlflow): ok" in text
    entries = json.loads(to_json(make_report(), manifest=manifest))["summary"]["adapter_unreadable"]
    assert len(entries) == 26
    assert entries[24]["path"] == "f24"


def test_validate_style_to_json_without_manifest_is_byte_compatible():
    actual = to_json(make_report(), strict=False)
    expected = json.dumps({
        "manifest": "runevidence.json", "ok": True, "exit_code": 0,
        "counts": {"error": 0, "warning": 0, "info": 0}, "findings": [],
    }, indent=2)
    assert actual == expected
    assert "summary" not in json.loads(actual)


def test_strict_and_exit_code_unchanged_with_unreadable():
    finding = Finding(code=Code.no_digest, severity=Severity.warning, message="missing digest")
    report = make_report(finding)
    manifest = make_manifest(Run(id="one", metadata={"hydra": {"unreadable": ["bad"]}}))
    ordinary = json.loads(to_json(report, strict=False, manifest=manifest))
    strict = json.loads(to_json(report, strict=True, manifest=manifest))
    assert (ordinary["exit_code"], ordinary["ok"]) == (0, True)
    assert (strict["exit_code"], strict["ok"]) == (1, False)
    assert ordinary["counts"] == strict["counts"] == {"error": 0, "warning": 1, "info": 0}
    assert ordinary["findings"] == strict["findings"]
    assert ordinary["summary"]["adapter_unreadable"] == strict["summary"]["adapter_unreadable"]


def test_control_character_metadata_does_not_inject_lines():
    manifest = make_manifest(Run(id="one", metadata={"hydra": {
        "unreadable": ["bad\nERROR forged", "other\x1b[31m"],
        "unreadable_reasons": {"bad\nERROR forged": "oops\rline2"},
    }}))
    text = format_overview(manifest)
    assert "bad\\nERROR forged" in text
    assert "oops\\rline2" in text
    assert "other\\u001b[31m" in text
    assert "\x1b" not in text
    assert "\nERROR forged" not in text
    payload = json.loads(to_json(make_report(), manifest=manifest))
    assert payload["summary"]["adapter_unreadable"][0]["path"] == "bad\nERROR forged"


def test_empty_and_wrong_namespace_no_added_section():
    manifest = make_manifest(Run(id="one", metadata={"custom": {"unreadable": ["ignored"]}, "hydra": {"unreadable": []}}))
    assert "Inputs the adapter" not in format_overview(manifest)
    assert "adapter_unreadable" not in json.loads(to_json(make_report(), manifest=manifest))["summary"]


def test_no_metadata_mutation():
    metadata = {"hydra": {"unreadable": ["a"], "unreadable_reasons": {"a": "bad"}}}
    manifest = make_manifest(Run(id="one", metadata=metadata))
    before = json.dumps(manifest.model_dump(mode="json"), sort_keys=True)
    _ = format_overview(manifest)
    _ = to_json(make_report(), manifest=manifest)
    assert json.dumps(manifest.model_dump(mode="json"), sort_keys=True) == before
