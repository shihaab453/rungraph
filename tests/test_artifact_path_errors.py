"""Embedded NUL in an artifact path: a fixed finding, never a traceback.

``os.stat``/``os.open`` raise ``ValueError`` (not ``OSError``) for a path with
an embedded NUL. The pre-protected path used ``Path.exists()``/``is_file()``,
which return False for this input, so every schema version reported a fixed
missing artifact and kept broken-set membership. These tests pin that behavior
for 0.1, 0.2 and 0.3 (string and object locators) and for a NUL in a parent
component.
"""

from __future__ import annotations

import pytest

from runevidence.checks import run_checks
from runevidence.checks import files as file_checks
from runevidence.manifest import dump_manifest, load_manifest
from runevidence.models import Manifest

NUL_PATHS = pytest.mark.parametrize("path", ["bad\x00name", "sub\x00/bad.jsonl"])


def _document(version: str, path: str, locator) -> dict:
    result = {"id": "res", "run_id": "run", "name": "val", "value": 0.9}
    if locator is not None:
        result["artifact_id"] = "a"
        result["locator"] = locator
    return {
        "schema_version": version,
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "name": "Run"}],
        "artifacts": [{"id": "a", "path": path}],
        "results": [result],
        "evidence": [{"id": "claim", "claim": "c", "supports": ["res"]}],
    }


def _report(tmp_path, version: str, path: str, locator):
    root = tmp_path / "case"
    root.mkdir()
    document = _document(version, path, locator)
    (root / "runevidence.json").write_text(dump_manifest(Manifest.model_validate(document)))
    return root, run_checks(load_manifest(root))


@NUL_PATHS
@pytest.mark.parametrize("version", ["0.1", "0.2"])
def test_legacy_embedded_nul_is_a_fixed_missing_file(tmp_path, version, path):
    _root, report = _report(tmp_path, version, path, None)
    assert report.exit_code() == 1
    assert [finding.code.value for finding in report.findings] == ["missing-file"]
    finding = report.findings[0]
    assert finding.path == path
    assert "Traceback" not in finding.message
    assert "embedded null byte" not in finding.message and "null" not in finding.message.lower()


@NUL_PATHS
@pytest.mark.parametrize("version", ["0.1", "0.2"])
def test_legacy_embedded_nul_keeps_broken_membership(tmp_path, version, path):
    root, _report_result = _report(tmp_path, version, path, None)
    files = file_checks.check_files(load_manifest(root))
    assert files.broken == {"a"}
    assert not files.readable_paths


@NUL_PATHS
@pytest.mark.parametrize(
    "locator",
    ["/v", {"kind": "jsonl", "line": 1, "pointer": "/v"}],
    ids=["string", "object"],
)
def test_targeted_embedded_nul_is_a_fixed_missing_file(tmp_path, path, locator):
    _root, report = _report(tmp_path, "0.3", path, locator)
    assert report.exit_code() == 1
    assert [finding.code.value for finding in report.findings] == [
        "missing-file", "claim-at-risk",
    ]
    assert "locator-kind-mismatch" not in {finding.code.value for finding in report.findings}
    assert "artifact-changed" not in {finding.code.value for finding in report.findings}
    finding = report.findings[0]
    assert "Traceback" not in finding.message
    assert "embedded null byte" not in finding.message and "null" not in finding.message.lower()
