"""Ambiguous JSON members must not be silently accepted as evidence."""

import json

import pytest

from runevidence.cli import main
from runevidence.findings import Code
from runevidence.manifest import ManifestLoadError, load_manifest


@pytest.mark.parametrize(
    ("original", "replacement"),
    [
        ('"schema_version": "0.1"', '"schema_version": "0.1", "schema_version": "0.1"'),
        ('"name": "Tiny sentiment classifier"', '"name": "Tiny sentiment classifier", "name": "other"'),
        ('"name": "Tiny sentiment classifier"', '"name": "Tiny sentiment classifier", "\\u006eame": "other"'),
    ],
)
def test_duplicate_decoded_keys_fail_closed(experiment, capsys, original, replacement):
    path = experiment / "runevidence.json"
    source = path.read_text(encoding="utf-8")
    assert source.count(original) == 1
    data = source.replace(original, replacement).encode("utf-8")
    path.write_bytes(data)

    with pytest.raises(ManifestLoadError) as error:
        load_manifest(path)
    assert error.value.findings[0].code == Code.manifest_unreadable
    assert "duplicate object keys" in error.value.findings[0].message
    assert isinstance(error.value.__cause__, ValueError)
    assert main(["validate", str(path), "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert report["findings"][0]["code"] == "manifest-unreadable"
    assert path.read_bytes() == data


def test_same_keys_in_separate_objects_are_valid(experiment):
    loaded = load_manifest(experiment)
    assert len(loaded.manifest.runs) == 2
    assert all(run.params["seed"] == 0 for run in loaded.manifest.runs)
