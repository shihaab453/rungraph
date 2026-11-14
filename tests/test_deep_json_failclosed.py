"""Manifest recursion failures are reported through the normal error boundary."""

import json
import sys

import pytest

from runevidence.cli import main
from runevidence.findings import Code
from runevidence.manifest import ManifestLoadError, load_manifest
from runevidence.models import Manifest


def test_valid_manifest_still_loads(experiment):
    assert load_manifest(experiment).manifest is not None


def test_malformed_json_remains_unreadable(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("{invalid", encoding="utf-8")
    with pytest.raises(ManifestLoadError) as error:
        load_manifest(path)
    assert error.value.findings[0].code == Code.manifest_unreadable


def test_deep_json_fails_closed_in_loader_and_cli(tmp_path, capsys):
    path = tmp_path / "manifest.json"
    depth = sys.getrecursionlimit() + 100
    source = ("[" * depth + "0" + "]" * depth).encode()
    path.write_bytes(source)
    with pytest.raises(ManifestLoadError) as error:
        load_manifest(path)
    assert error.value.findings[0].code in (Code.manifest_unreadable, Code.schema_invalid)
    assert main(["validate", str(path), "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["exit_code"] == 2
    assert report["findings"][0]["code"] in ("manifest-unreadable", "schema-invalid")
    assert path.read_bytes() == source


def test_decoder_recursion_is_unreadable(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    path.write_text("{}", encoding="utf-8")

    def fail(*args, **kwargs):
        raise RecursionError("decoder depth")

    monkeypatch.setattr(json, "loads", fail)
    with pytest.raises(ManifestLoadError) as error:
        load_manifest(path)
    assert error.value.findings[0].code == Code.manifest_unreadable
    assert isinstance(error.value.__cause__, RecursionError)


def test_validation_recursion_is_schema_invalid(experiment, monkeypatch):
    def fail(*args, **kwargs):
        raise RecursionError("validation depth")

    monkeypatch.setattr(Manifest, "model_validate", fail)
    with pytest.raises(ManifestLoadError) as error:
        load_manifest(experiment)
    assert error.value.findings[0].code == Code.schema_invalid
    assert isinstance(error.value.__cause__, RecursionError)


@pytest.mark.parametrize("exception", [MemoryError, RuntimeError, KeyboardInterrupt, SystemExit])
def test_unrelated_validation_exceptions_propagate(experiment, monkeypatch, exception):
    def fail(*args, **kwargs):
        raise exception()

    monkeypatch.setattr(Manifest, "model_validate", fail)
    with pytest.raises(exception):
        load_manifest(experiment)
