"""Opt-in redaction changes only designated report fields."""
import json

import pytest

from runevidence.cli import main


def invoke(command, path, capsys, *options):
    code = main([command, str(path), "--json", *options])
    captured = capsys.readouterr()
    return code, json.loads(captured.out)


@pytest.mark.parametrize("command", ["validate", "inspect"])
def test_success_preserves_other_fields(command, experiment, capsys):
    manifest = experiment / "runevidence.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    # A real artifact path and identifier contain the manifest filename.
    data["artifacts"][0]["path"] = "runevidence.json.extra"
    data["artifacts"][0]["id"] = "runevidence.json.extra"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    before = manifest.read_bytes()
    code, plain = invoke(command, manifest, capsys)
    redacted_code, redacted = invoke(command, manifest, capsys, "--redact-manifest-path")
    assert redacted_code == code
    assert plain["manifest"] == str(manifest.resolve())
    assert redacted["manifest"] == "<redacted>"
    assert isinstance(redacted["manifest"], str)
    assert any(f["path"] == "runevidence.json.extra" for f in redacted["findings"])
    redacted["manifest"] = plain["manifest"]
    assert redacted == plain
    assert manifest.read_bytes() == before


@pytest.mark.parametrize("command", ["validate", "inspect"])
def test_invalid_schema_redacts_finding_paths_only(command, tmp_path, capsys):
    marker = "private_input_681"
    manifest = tmp_path / "private_filename_681.json"
    manifest.write_text(json.dumps({
        "project": {"id": marker, "name": "Project", marker: marker},
        "results": [{"id": marker, "run_id": marker, "name": "result", "value": [marker]}],
    }), encoding="utf-8")
    before = manifest.read_bytes()
    code, plain = invoke(command, manifest, capsys)
    redacted_code, redacted = invoke(command, manifest, capsys, "--redact-manifest-path")
    assert code == redacted_code == 2
    assert redacted["manifest"] == "<redacted>"
    assert all(f["path"] == "<redacted>" for f in redacted["findings"])
    assert marker not in json.dumps(redacted["findings"])
    assert any(f["issue_location"] == "/results/0/value" for f in redacted["findings"])
    redacted["manifest"] = plain["manifest"]
    for actual, original in zip(redacted["findings"], plain["findings"]):
        assert isinstance(actual["path"], str)
        actual["path"] = original["path"]
    assert redacted == plain
    assert manifest.read_bytes() == before


@pytest.mark.parametrize("command", ["validate", "inspect"])
def test_missing_file_and_usage(command, tmp_path, capsys):
    target = tmp_path / "missing_marker.json"
    plain_code, plain = invoke(command, target, capsys)
    code, report = invoke(command, target, capsys, "--redact-manifest-path")
    assert code == 2
    assert plain_code == code
    assert report["manifest"] == report["findings"][0]["path"] == "<redacted>"
    assert "missing_marker.json" in plain["findings"][0]["message"]
    assert str(target.parent.resolve()) in plain["findings"][0]["hint"]
    assert "missing_marker.json" not in report["findings"][0]["message"]
    assert str(target.parent.resolve()) not in report["findings"][0]["hint"]
    assert main([command, str(target), "--redact-manifest-path"]) == 2
    captured = capsys.readouterr()
    assert not captured.out
    assert "requires --json" in captured.err
