"""Load error privacy is opt-in and preserves error identity."""
import json
from pathlib import Path

import pytest

from runevidence.cli import main


def invoke(command, target, capsys, *options):
    code = main([command, str(target), "--json", *options])
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("command", ["validate", "inspect"])
@pytest.mark.parametrize("failure", ["missing", "unreadable"])
def test_load_error_redaction(command, failure, tmp_path, capsys, monkeypatch):
    parent = tmp_path / "private_parent_781"
    parent.mkdir()
    target = parent / "private_filename_781.json"
    if failure == "unreadable":
        target.write_bytes(b"{}")
        original_read = Path.read_bytes

        def fail_read(path):
            if path == target.resolve():
                raise OSError(f"private_parent_781 private_filename_781 {path}")
            return original_read(path)

        monkeypatch.setattr(Path, "read_bytes", fail_read)

    plain_code, plain = invoke(command, target, capsys)
    redacted_code, redacted = invoke(command, target, capsys, "--redact-manifest-path")
    assert plain_code == redacted_code == 2
    assert "private_filename_781" in json.dumps(plain)
    assert "private_parent_781" not in json.dumps(redacted)
    assert "private_filename_781" not in json.dumps(redacted)
    assert redacted["manifest"] == "<redacted>"
    assert redacted["findings"][0]["path"] == "<redacted>"
    assert redacted["findings"][0]["code"] == (
        "manifest-not-found" if failure == "missing" else "manifest-unreadable"
    )
    redacted["manifest"] = plain["manifest"]
    for actual, expected in zip(redacted["findings"], plain["findings"]):
        for field in ("path", "message", "hint"):
            if field in expected:
                actual[field] = expected[field]
    assert redacted == plain
    if failure == "unreadable":
        assert original_read(target) == b"{}"


@pytest.mark.parametrize("command", ["validate", "inspect"])
def test_invalid_json_is_not_described_as_access_failure(command, tmp_path, capsys):
    target = tmp_path / "private_filename_781.json"
    target.write_bytes(b"{invalid")
    code, report = invoke(command, target, capsys, "--redact-manifest-path")
    assert code == 2
    assert report["findings"][0]["code"] == "manifest-unreadable"
    assert report["findings"][0]["message"] == "Could not read manifest as UTF-8 JSON."
    assert target.read_bytes() == b"{invalid"
