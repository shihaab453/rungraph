"""Schema diagnostics do not render submitted validation details."""
import json
import traceback

import pytest

from runevidence.cli import main
from runevidence.manifest import ManifestLoadError, load_manifest


def test_schema_messages_are_safe_in_cli_and_traceback(tmp_path, capsys):
    marker = "private_marker_497"
    target = tmp_path / "runevidence.json"
    target.write_text(json.dumps({
        "project": {"id": marker, "name": "Project", marker: marker},
        "results": [{"id": marker, "run_id": marker, "name": "result", "value": [marker]}],
    }), encoding="utf-8")

    with pytest.raises(ManifestLoadError) as caught:
        load_manifest(target)
    assert marker not in "".join(traceback.format_exception(caught.value))

    assert main(["validate", str(target), "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    findings = report["findings"]
    assert [(f["issue_location"], f["issue_category"]) for f in findings] == [
        (None, "invalid_value"),
        *[("/results/0/value", "invalid_type")] * 4,
    ]
    assert all(f["code"] == "schema-invalid" and f["severity"] == "error" for f in findings)
    assert all(f["subject"] is None and f["hint"] is None for f in findings)
    assert marker not in json.dumps(findings)
    assert findings[0]["message"] == "Manifest schema validation failed: invalid value"
    assert findings[1]["message"] == "Manifest schema validation failed at /results/0/value: invalid type"

    assert main(["inspect", str(target)]) == 2
    human = capsys.readouterr().out
    assert marker not in human
    assert findings[1]["message"] in human


def test_unrelated_missing_manifest_message_is_unchanged(tmp_path, capsys):
    assert main(["validate", str(tmp_path), "--json"]) == 2
    finding = json.loads(capsys.readouterr().out)["findings"][0]
    assert finding["code"] == "manifest-not-found"
    assert "No manifest at" in finding["message"]


def test_artifact_id_is_not_rendered_in_schema_findings(tmp_path, capsys):
    marker = "private_artifact_938"
    target = tmp_path / "runevidence.json"
    target.write_text(json.dumps({
        "project": {"id": "p", "name": "Project"},
        "artifacts": [{"id": marker, "path": "data.bin", "sha256": "not-a-digest"}],
    }), encoding="utf-8")

    with pytest.raises(ManifestLoadError) as caught:
        load_manifest(target)
    [finding] = caught.value.findings
    assert finding.issue_location == "/artifacts/0/sha256"
    assert finding.issue_category == "invalid_value"
    assert marker not in finding.message

    assert main(["validate", str(target), "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    [reported] = report["findings"]
    assert reported["issue_location"] == "/artifacts/0/sha256"
    assert marker not in json.dumps(report)
