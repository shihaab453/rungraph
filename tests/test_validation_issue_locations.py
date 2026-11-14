"""Structured schema diagnostics preserve legacy output without new value leaks."""
import json

from pydantic import BaseModel, Field

from runevidence.cli import main
from runevidence.manifest import _issue_category, _issue_location
import runevidence.manifest as manifest_module


def test_categories_and_unknown_fallback():
    assert _issue_category("missing") == "missing_required"
    assert _issue_category("string_type") == "invalid_type"
    assert _issue_category("string_pattern_mismatch") == "invalid_value"
    assert _issue_category("future_error") == "other_validation"


def test_safe_schema_locations_and_escaping(monkeypatch):
    assert _issue_location(("project", "name")) == "/project/name"
    assert _issue_location(("artifacts", 0, "sha256")) == "/artifacts/0/sha256"
    # A mapping key that is also a real field name is still untrusted.
    assert _issue_location(("runs", 0, "metadata", "id")) is None
    assert _issue_location(("artifacts", 0, "secret-id")) is None

    class Escaped(BaseModel):
        field: str = Field(alias="a~/b")

    # Alias handling is intentionally conservative; demonstrate escaping using
    # a model field name that is itself schema-defined.
    Escaped.model_fields["a~/b"] = Escaped.model_fields.pop("field")
    monkeypatch.setattr(manifest_module, "Manifest", Escaped)
    assert _issue_location(("a~/b",)) == "/a~0~1b"


def test_cli_schema_findings_are_additive_and_new_fields_safe(tmp_path, capsys):
    marker = "synthetic_secret_marker"
    doc = {"project": {"id": marker}, "artifacts": [
        {"id": marker, "path": "x", "sha256": "bad", "size_bytes": "wrong"},
    ]}
    target = tmp_path / "runevidence.json"
    target.write_text(json.dumps(doc), encoding="utf-8")
    outputs = []
    for _ in range(2):
        assert main(["validate", str(target), "--json"]) == 2
        outputs.append(json.loads(capsys.readouterr().out))
    assert outputs[0] == outputs[1]
    findings = outputs[0]["findings"]
    assert [(f["issue_location"], f["issue_category"]) for f in findings] == [
        ("/project/name", "missing_required"),
        ("/artifacts/0/sha256", "invalid_value"),
        ("/artifacts/0/size_bytes", "invalid_value"),
    ]
    for finding in findings:
        assert finding["code"] == "schema-invalid"
        assert finding["severity"] == "error"
        assert finding["path"] == str(target.resolve())
        assert {"message", "subject", "hint"} <= finding.keys()
        assert marker not in json.dumps([finding["issue_category"], finding["issue_location"]])
    assert marker not in findings[1]["message"]


def test_unrelated_load_findings_do_not_gain_fields(tmp_path, capsys):
    assert main(["validate", str(tmp_path), "--json"]) == 2
    finding = json.loads(capsys.readouterr().out)["findings"][0]
    assert "issue_category" not in finding
    assert "issue_location" not in finding


def test_invalid_type_and_unknown_field_do_not_disclose_key(tmp_path, capsys):
    marker = "synthetic_private_key"
    target = tmp_path / "runevidence.json"
    target.write_text(json.dumps({"project": {"id": "p", "name": 42, marker: 1}}), encoding="utf-8")
    assert main(["validate", str(target), "--json"]) == 2
    findings = json.loads(capsys.readouterr().out)["findings"]
    assert [(f["issue_location"], f["issue_category"]) for f in findings] == [
        ("/project/name", "invalid_type"), (None, "invalid_value")
    ]
    assert marker not in findings[1]["message"]
    assert marker not in json.dumps([[f["issue_location"], f["issue_category"]] for f in findings])


def test_scalar_union_errors_point_to_safe_field_ancestor(tmp_path, capsys):
    marker = "synthetic_private_value"
    target = tmp_path / "runevidence.json"
    target.write_text(json.dumps({"project": {"id": "p", "name": "P"},
        "results": [{"id": marker, "run_id": marker, "name": "n", "value": [marker]}]}), encoding="utf-8")
    assert main(["validate", str(target), "--json"]) == 2
    findings = json.loads(capsys.readouterr().out)["findings"]
    assert len(findings) == 4
    assert all(f["issue_location"] == "/results/0/value" for f in findings)
    assert marker not in json.dumps([[f["issue_location"], f["issue_category"]] for f in findings])
    assert _issue_location(("results", 0, "value", marker)) is None
    assert _issue_location(("results", 0, "value", "str", marker)) is None
    assert _issue_location(("results", 0, "metadata", "value", "str")) is None
