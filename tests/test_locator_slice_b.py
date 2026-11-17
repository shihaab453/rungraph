"""Schema 0.3 verified locators: outcome table and read-path integration."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

from runevidence.checks import run_checks
from runevidence.manifest import dump_manifest, load_manifest
from runevidence.models import Manifest

SECRET = "M53-CANARY-READ-VALUE"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(
    root: Path,
    *,
    data: bytes = b'{"val": 0.9}\n',
    locator=...,
    value=0.9,
    artifact_path="metrics.jsonl",
    sha256=...,
    volatile=False,
    artifact_type="file",
    result_id="res",
    claim=True,
    version="0.3",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / artifact_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if artifact_type == "dir-on-disk":
        path.mkdir(exist_ok=True)
        artifact_type = "file"
    elif artifact_type == "directory":
        path.mkdir(exist_ok=True)
    else:
        path.write_bytes(data)
    artifact = {
        "id": "a",
        "path": artifact_path,
        "artifact_type": artifact_type,
        "volatile": volatile,
        "sha256": _digest(data) if sha256 is ... else sha256,
        "size_bytes": len(data),
    }
    if artifact_type == "directory":
        artifact["digest_format"] = "runevidence-directory-v1"
        artifact.pop("size_bytes", None)
    doc = {
        "schema_version": version,
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "name": "Run"}],
        "artifacts": [artifact],
        "results": [
            {
                "id": result_id,
                "run_id": "run",
                "name": "val",
                "value": value,
                "artifact_id": "a",
            }
        ],
        "evidence": [{"id": "e", "claim": "c", "supports": [result_id]}] if claim else [],
    }
    if locator is not ...:
        doc["results"][0]["locator"] = locator
    (root / "runevidence.json").write_text(dump_manifest(Manifest.model_validate(doc)))
    return path


def _report(root: Path, *, flag: bool = False):
    return run_checks(load_manifest(root), enable_directory_artifacts=flag)


def _codes(report):
    return [finding.code.value for finding in report.findings]


def _finding(report, code):
    return next(finding for finding in report.findings if finding.code.value == code)


def test_jsonl_object_locator_matches_and_string_locator_matches(tmp_path):
    root = tmp_path / "jsonl"
    _write(root, locator={"kind": "jsonl", "line": 1, "pointer": "/val"})
    report = _report(root)
    assert report.findings == []
    assert report.exit_code() == 0

    root = tmp_path / "json"
    _write(
        root,
        data=b'{"val": 0.9}\n',
        locator="/val",
        artifact_path="metrics.json",
    )
    report = _report(root)
    assert report.findings == []


def test_absent_locator_is_unverified_and_claim_is_unverified(tmp_path):
    root = tmp_path / "absent"
    _write(root)
    report = _report(root)
    assert _codes(report) == ["result-unverified", "claim-unverified"]
    assert report.exit_code() == 0
    assert report.exit_code(strict=True) == 1


def test_kind_extension_mismatch_is_an_error(tmp_path):
    root = tmp_path / "object-on-json"
    _write(root, locator={"kind": "jsonl", "line": 1, "pointer": "/val"}, artifact_path="metrics.json")
    report = _report(root)
    assert _codes(report) == ["locator-kind-mismatch", "claim-at-risk"]

    root = tmp_path / "string-on-jsonl"
    _write(root, locator="/val", artifact_path="metrics.jsonl")
    report = _report(root)
    assert "locator-kind-mismatch" in _codes(report)


def test_dangling_artifact_reference_marks_result_broken(tmp_path):
    root = tmp_path / "dangling"
    _write(root, locator={"kind": "jsonl", "line": 1, "pointer": "/val"})
    doc = json.loads((root / "runevidence.json").read_text())
    doc["artifacts"] = []
    (root / "runevidence.json").write_text(dump_manifest(Manifest.model_validate(doc)))
    report = _report(root)
    assert "dangling-reference" in _codes(report)


@pytest.mark.parametrize("flag", [False, True])
def test_directory_artifact_locator_is_refused_regardless_of_flag(tmp_path, flag):
    root = tmp_path / f"directory-{flag}"
    _write(
        root,
        data=b"",
        locator={"kind": "jsonl", "line": 1, "pointer": "/val"},
        artifact_path="data",
        artifact_type="directory",
    )
    report = _report(root, flag=flag)
    assert "directory-locator-unsupported" in _codes(report)
    # The normal artifact-level check still runs on top: without the flag the
    # directory is not-a-file, with it the directory policy check runs.
    assert ("not-a-file" in _codes(report)) is (not flag)


def test_not_a_file_reference_is_reported_once(tmp_path):
    root = tmp_path / "not-file"
    _write(root, locator={"kind": "jsonl", "line": 1, "pointer": "/val"}, artifact_type="dir-on-disk")
    report = _report(root)
    assert _codes(report) == ["not-a-file", "claim-at-risk"]


def test_targeted_no_digest_suppresses_artifact_warning_and_is_kind_aware(tmp_path):
    root = tmp_path / "no-digest-object"
    _write(root, sha256=None, locator={"kind": "jsonl", "line": 1, "pointer": "/val"})
    report = _report(root)
    assert "no-digest" not in _codes(report)
    finding = _finding(report, "locator-no-digest")
    assert "rehash cannot record" in finding.hint

    root = tmp_path / "no-digest-string"
    _write(root, sha256=None, locator="/val", artifact_path="metrics.json")
    report = _report(root)
    assert "no-digest" not in _codes(report)
    assert "rehash can record" in _finding(report, "locator-no-digest").hint


def _append_result(root: Path, row: dict) -> None:
    doc = json.loads((root / "runevidence.json").read_text())
    doc["results"].append(row)
    (root / "runevidence.json").write_text(dump_manifest(Manifest.model_validate(doc)))


def test_a_kind_mismatched_object_locator_keeps_the_artifact_scoped_refusal(tmp_path):
    """A kind-mismatched object locator still targets this artifact.

    It is not in ``usable`` but rehash still refuses this artifact, so the
    artifact-scoped hint must not promise a refresh.
    """
    root = tmp_path / "mixed-kind-mismatch"
    _write(root, sha256=None, locator="/val", artifact_path="a.json")
    _append_result(
        root,
        {
            "id": "res2",
            "run_id": "run",
            "name": "other",
            "value": 0.9,
            "artifact_id": "a",
            "locator": {"kind": "jsonl", "line": 1, "pointer": "/val"},
        },
    )

    report = _report(root)
    assert "locator-kind-mismatch" in _codes(report)
    hint = _finding(report, "locator-no-digest").hint
    assert "reads this artifact through an object locator" in hint
    assert "only string locators target this artifact" not in hint


def test_an_object_locator_on_another_artifact_does_not_block_this_hint(tmp_path):
    root = tmp_path / "mixed-artifacts"
    _write(root, sha256=None, locator="/val", artifact_path="a.json")
    doc = json.loads((root / "runevidence.json").read_text())
    doc["artifacts"].append(
        {
            "id": "b",
            "path": "b.jsonl",
            "sha256": _digest(b'{"val": 0.9}\n'),
            "size_bytes": len(b'{"val": 0.9}\n'),
        }
    )
    (root / "b.jsonl").write_bytes(b'{"val": 0.9}\n')
    doc["results"].append(
        {
            "id": "res2",
            "run_id": "run",
            "name": "other",
            "value": 0.9,
            "artifact_id": "b",
            "locator": {"kind": "jsonl", "line": 1, "pointer": "/val"},
        }
    )
    (root / "runevidence.json").write_text(dump_manifest(Manifest.model_validate(doc)))

    report = _report(root)
    hint = _finding(report, "locator-no-digest").hint
    assert "rehash can record the missing digest" in hint
    assert "object locator" not in hint


def test_kind_mismatched_object_locator_keeps_the_artifact_scoped_refusal(tmp_path):
    """Only kind-mismatched locators on a digestless artifact.

    Nothing is ``usable``, so the restored artifact-level ``no-digest`` warning
    speaks. An object locator anywhere makes ``rehash`` refuse the whole
    manifest, so that warning must not promise a rehash refresh.
    """
    root = tmp_path / "kind-mismatch-object-on-json"
    _write(
        root,
        sha256=None,
        locator={"kind": "jsonl", "line": 1, "pointer": "/val"},
        artifact_path="metrics.json",
    )
    report = _report(root)
    assert "locator-kind-mismatch" in _codes(report)
    assert "locator-no-digest" not in _codes(report)
    hint = _finding(report, "no-digest").hint
    assert "reads this artifact through an object locator" in hint
    assert "record sha256 manually or regenerate the manifest" in hint
    assert "Record it with" not in hint


def test_kind_mismatched_string_locator_keeps_the_rehash_can_record_hint(tmp_path):
    """A string locator does not block rehash, so the hint must say it can."""
    root = tmp_path / "kind-mismatch-string-on-jsonl"
    _write(root, sha256=None, locator="/val", artifact_path="metrics.jsonl")
    report = _report(root)
    assert "locator-kind-mismatch" in _codes(report)
    hint = _finding(report, "no-digest").hint
    assert "rehash can record the missing digest" in hint
    assert "any object locator exists in the manifest" not in hint
    assert "Record it with" not in hint


def test_distinct_jsonl_locator_lines_are_indexed_in_one_pass(tmp_path, monkeypatch):
    """The results layer hands the whole requested line set to one scan."""
    import runevidence.checks.results as results

    lines = 400
    data = b"".join(b'{"v": %d}\n' % i for i in range(1, lines + 1))
    root = tmp_path / "index-once"
    _write(root, data=data, locator={"kind": "jsonl", "line": 1, "pointer": "/v"}, value=1)
    doc = json.loads((root / "runevidence.json").read_text())
    for line in (50, 120, 399, 400):
        row = dict(doc["results"][0])
        row.update(
            {
                "id": f"res{line}",
                "name": f"v{line}",
                "value": line,
                "locator": {"kind": "jsonl", "line": line, "pointer": "/v"},
            }
        )
        doc["results"].append(row)
    (root / "runevidence.json").write_text(dump_manifest(Manifest.model_validate(doc)))

    calls: list[list[int]] = []
    documents: list = []
    original = results.JsonlDocument

    class RecordingJsonlDocument(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            documents.append(self)

        def index_lines(self, wanted):
            calls.append(sorted(set(wanted)))
            super().index_lines(wanted)

    monkeypatch.setattr(results, "JsonlDocument", RecordingJsonlDocument)
    report = _report(root)
    assert report.findings == []
    assert calls == [[1, 50, 120, 399, 400]]
    # The one scan must retain the requested offsets, and only those: a
    # patched-to-no-op `index_lines` would leave this empty and every selection
    # would silently rescan from line 1.
    assert len(documents) == 1
    offsets = documents[0]._offsets
    assert sorted(offsets) == [1, 50, 120, 399, 400]
    for line, start in offsets.items():
        assert documents[0]._text[:start].count("\n") == line - 1


def test_volatile_target_is_locator_unverified(tmp_path):
    root = tmp_path / "volatile"
    _write(root, volatile=True, locator={"kind": "jsonl", "line": 1, "pointer": "/val"})
    report = _report(root)
    assert "locator-unverified" in _codes(report)
    assert "claim-at-risk" in _codes(report)


def test_hash_mismatch_still_compares_the_value_but_is_not_verified(tmp_path):
    root = tmp_path / "hash-mismatch"
    _write(root, sha256="0" * 64, locator={"kind": "jsonl", "line": 1, "pointer": "/val"})
    report = _report(root)
    assert _codes(report) == ["hash-mismatch", "claim-at-risk"]

    root = tmp_path / "hash-and-value"
    _write(
        root,
        sha256="0" * 64,
        value=0.1,
        locator={"kind": "jsonl", "line": 1, "pointer": "/val"},
    )
    report = _report(root)
    assert "hash-mismatch" in _codes(report)
    assert "result-value-mismatch" in _codes(report)


@pytest.mark.parametrize(
    "locator",
    [
        {"kind": "jsonl", "line": 1, "pointer": "/missing"},
        {"kind": "jsonl", "line": 2, "pointer": "/val"},
        {"kind": "jsonl", "line": 1, "pointer": "/nested/leaf"},
    ],
)
def test_locator_mismatch_is_value_free(tmp_path, locator):
    data = b'{"val": 0.9, "nested": {"other": 1}}\n'
    root = tmp_path / "mismatch"
    _write(root, data=data, locator=locator)
    report = _report(root)
    finding = _finding(report, "locator-mismatch")
    assert finding.severity.value == "error"
    assert "0.9" not in finding.message and SECRET not in finding.message


def test_non_scalar_target_is_unsupported_value(tmp_path):
    data = b'{"val": {"nested": 1}, "arr": [1, 2]}\n'
    for pointer in ("/val", "/arr", ""):
        root = tmp_path / pointer.strip("/").replace("/", "-") or tmp_path / "empty"
        _write(root, data=data, locator={"kind": "jsonl", "line": 1, "pointer": pointer}, value=1)
        report = _report(root)
        assert "locator-unsupported-value" in _codes(report)


@pytest.mark.parametrize(
    "data",
    [
        b'{"val": 1, "val": 2}\n',
        b'{"val": 1e999}\n',
        b'{"val": 1e-999}\n',
        b'{"val": 1}\x00\n',
        b'{"val": 1}\rX\n',
        b'\xff\n',
    ],
)
def test_jsonl_profile_refusals_are_locator_unreadable(tmp_path, data):
    root = tmp_path / "bad-profile"
    _write(root, data=data, locator={"kind": "jsonl", "line": 1, "pointer": "/val"})
    report = _report(root)
    assert "locator-unreadable" in _codes(report)
    assert "claim-at-risk" in _codes(report)


def test_jsonl_profile_acceptances(tmp_path):
    cases = [
        (b'\xef\xbb\xbf{"val": 0.9}\n', 1),
        (b'{"val": 0.9}\r\n', 1),
        (b'{"val": 0.9}', 1),
        (b'not json\n{"val": 0.9}\n', 2),
        (b'\n{"val": 0.9}\n', 2),
    ]
    for index, (data, line) in enumerate(cases):
        root = tmp_path / f"ok-{index}"
        _write(root, data=data, locator={"kind": "jsonl", "line": line, "pointer": "/val"})
        report = _report(root)
        assert report.findings == [], (index, _codes(report))


def test_string_locator_uses_strict_whole_document_profile(tmp_path):
    root = tmp_path / "strict-json"
    _write(root, data=b'{"val": 1, "val": 2}', locator="/val", artifact_path="m.json")
    assert "locator-unreadable" in _codes(_report(root))

    for index, data in enumerate(
        (b'{"val": 1e999}', b'{"val": 1e-999}', b'{"val": 1}\x00', b'{"val": 1}\rX')
    ):
        root = tmp_path / f"strict-{index}"
        _write(root, data=data, locator="/val", artifact_path="m.json")
        assert "locator-unreadable" in _codes(_report(root))


def test_number_and_scalar_comparison_rules(tmp_path):
    cases = [
        (b'{"val": 9007199254740993}\n', 9007199254740993, True),
        (b'{"val": 9007199254740993}\n', 9007199254740992, False),
        (b'{"val": 1.0000000005}\n', 1, True),
        (b'{"val": 1}\n', True, False),
        (b'{"val": NaN}\n', float("nan"), True),
        (b'{"val": Infinity}\n', float("inf"), True),
        (b'{"val": -Infinity}\n', float("-inf"), True),
        (b'{"val": null}\n', None, True),
    ]
    for index, (data, value, matches) in enumerate(cases):
        root = tmp_path / f"number-{index}"
        _write(root, data=data, value=value, locator={"kind": "jsonl", "line": 1, "pointer": "/val"})
        report = _report(root)
        mismatch = "result-value-mismatch" in _codes(report)
        assert mismatch is not matches, (index, _codes(report))


def test_unsupported_value_and_parser_text_are_not_echoed(tmp_path):
    root = tmp_path / "privacy"
    _write(
        root,
        data=b'{"val": "' + SECRET.encode() + b'", "nested": {"x": 1}}\n',
        locator={"kind": "jsonl", "line": 1, "pointer": "/nested"},
    )
    report = _report(root)
    blob = "\n".join(f.message + " " + (f.hint or "") for f in report.findings)
    assert SECRET not in blob
    assert "Expecting" not in blob and "Traceback" not in blob


def test_legacy_0_2_non_json_locator_stays_a_warning(tmp_path):
    root = tmp_path / "legacy-non-json"
    _write(
        root,
        data=b"v\n0.9\n",
        locator="/val",
        artifact_path="metrics.csv",
        version="0.2",
    )
    report = _report(root)
    assert _codes(report) == ["result-unverifiable"]
    assert report.exit_code() == 0
    assert report.exit_code(strict=True) == 1


def test_legacy_0_2_json_mismatch_keeps_the_value_echo(tmp_path):
    root = tmp_path / "legacy-echo"
    _write(
        root,
        data=b'{"val": 0.9}',
        locator="/val",
        value=0.1,
        artifact_path="metrics.json",
        version="0.2",
    )
    report = _report(root)
    finding = _finding(report, "result-value-mismatch")
    assert "0.1" in finding.message and "0.9" in finding.message


def test_0_1_absent_locator_is_still_silent(tmp_path):
    root = tmp_path / "legacy-absent"
    _write(root, version="0.1")
    report = _report(root)
    assert report.findings == []


def test_claim_with_verified_and_unverified_results_is_not_claim_unverified(tmp_path):
    root = tmp_path / "mixed-claim"
    _write(root, locator={"kind": "jsonl", "line": 1, "pointer": "/val"})
    doc = json.loads((root / "runevidence.json").read_text())
    doc["results"].append(
        {
            "id": "res2",
            "run_id": "run",
            "name": "other",
            "value": 1,
            "artifact_id": "a",
        }
    )
    doc["evidence"][0]["supports"].append("res2")
    (root / "runevidence.json").write_text(dump_manifest(Manifest.model_validate(doc)))
    report = _report(root)
    assert "result-unverified" in _codes(report)
    assert "claim-unverified" not in _codes(report)
    assert "claim-at-risk" not in _codes(report)


def test_hash_mismatch_and_malformed_profile_are_both_reported(tmp_path):
    root = tmp_path / "stale-malformed"
    _write(
        root,
        data=b'{"val": 1, "val": 2}\n',
        sha256="0" * 64,
        locator={"kind": "jsonl", "line": 1, "pointer": "/val"},
    )
    report = _report(root)
    assert {"hash-mismatch", "locator-unreadable"} <= set(_codes(report))


def test_cli_exit_precedence_and_strict_for_absent_locator(tmp_path, capsys):
    from runevidence import cli

    root = tmp_path / "cli-absent"
    _write(root)
    assert cli.main(["validate", str(root), "--json"]) == 0
    out = capsys.readouterr()
    assert out.err == ""
    assert "result-unverified" in out.out

    assert cli.main(["validate", str(root), "--json", "--strict"]) == 1
    out = capsys.readouterr()
    assert out.err == "" and "Traceback" not in out.out


def test_cli_mismatch_stdout_is_clean_and_stderr_empty(tmp_path, capsys):
    from runevidence import cli

    root = tmp_path / "cli-mismatch"
    _write(
        root,
        data=b'{"val": "' + SECRET.encode() + b'"}\n',
        locator={"kind": "jsonl", "line": 1, "pointer": "/val"},
        value=0.9,
    )
    assert cli.main(["validate", str(root), "--json"]) == 1
    out = capsys.readouterr()
    assert out.err == "" and "Traceback" not in out.out
    assert SECRET not in out.out


def test_targeted_no_digest_size_mismatch_is_reported_and_broken(tmp_path):
    root = tmp_path / "targeted-no-digest-size"
    data = b'{"val": 0.9}\n'
    _write(root, data=data, sha256=None, locator={"kind": "jsonl", "line": 1, "pointer": "/val"})
    doc = json.loads((root / "runevidence.json").read_text())
    doc["artifacts"][0]["size_bytes"] = 999
    (root / "runevidence.json").write_text(dump_manifest(Manifest.model_validate(doc)))
    report = _report(root)
    # Review 1: the size check still runs before no-digest is returned, and its
    # error keeps the claim at risk.
    assert "size-mismatch" in _codes(report)
    assert "locator-no-digest" in _codes(report)
    assert "no-digest" not in _codes(report)
    assert "claim-at-risk" in _codes(report)


def test_targeted_size_mismatch_with_matching_digest_is_reported(tmp_path):
    root = tmp_path / "targeted-size"
    data = b'{"val": 0.9}\n'
    _write(root, data=data, locator={"kind": "jsonl", "line": 1, "pointer": "/val"})
    doc = json.loads((root / "runevidence.json").read_text())
    doc["artifacts"][0]["size_bytes"] = 999
    (root / "runevidence.json").write_text(dump_manifest(Manifest.model_validate(doc)))
    report = _report(root)
    assert "size-mismatch" in _codes(report)
    assert "claim-at-risk" in _codes(report)
    # A matching digest still lets the locator compare the value.
    assert "result-value-mismatch" not in _codes(report)


@pytest.mark.parametrize("flag", [False, True])
def test_targeted_missing_directory_keeps_normal_finding(tmp_path, flag):
    root = tmp_path / f"targeted-directory-{flag}"
    _write(
        root,
        data=b"",
        locator={"kind": "jsonl", "line": 1, "pointer": "/val"},
        artifact_path="data",
        artifact_type="directory",
    )
    # _write created the directory; remove it so the artifact is missing.
    (root / "data").rmdir()
    report = _report(root, flag=flag)
    expected = "missing-directory" if flag else "missing-file"
    assert expected in _codes(report)
    assert "directory-locator-unsupported" in _codes(report)
    assert "claim-at-risk" in _codes(report)


def test_targeted_wrong_digest_directory_keeps_normal_finding(tmp_path):
    root = tmp_path / "targeted-directory-digest"
    _write(
        root,
        data=b"",
        locator={"kind": "jsonl", "line": 1, "pointer": "/val"},
        artifact_path="data",
        artifact_type="directory",
    )
    report = _report(root, flag=True)
    assert "directory-hash-mismatch" in _codes(report)
    assert "directory-locator-unsupported" in _codes(report)


def test_targeted_matching_directory_only_adds_the_locator_finding(tmp_path):
    from runevidence.directory_scanner import scan_directory

    root = tmp_path / "targeted-directory-match"
    data_dir = root / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "a.txt").write_bytes(b"a")
    digest = scan_directory(data_dir).digest
    document = {
        "schema_version": "0.3",
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "name": "Run"}],
        "artifacts": [{
            "id": "a", "path": "data", "artifact_type": "directory",
            "digest_format": "runevidence-directory-v1",
            "sha256": digest.sha256, "size_bytes": digest.size_bytes,
        }],
        "results": [{
            "id": "res", "run_id": "run", "name": "val", "value": 0.9,
            "artifact_id": "a", "locator": {"kind": "jsonl", "line": 1, "pointer": "/val"},
        }],
        "evidence": [{"id": "claim", "claim": "c", "supports": ["res"]}],
    }
    (root / "runevidence.json").write_text(dump_manifest(Manifest.model_validate(document)))
    report = _report(root, flag=True)
    assert not [code for code in _codes(report) if code.startswith("directory-") and code != "directory-locator-unsupported"]
    assert "directory-locator-unsupported" in _codes(report)


def test_many_json_document_locators_parse_the_file_once(tmp_path, monkeypatch):
    import runevidence.checks.results as results

    root = tmp_path / "parse-once-json"
    _write(root, data=b'{"a": 0.9, "b": 1, "c": 2}', locator="/a", artifact_path="m.json", value=0.9)
    doc = json.loads((root / "runevidence.json").read_text())
    for index, pointer in enumerate(("/b", "/c")):
        row = dict(doc["results"][0])
        row.update({"id": f"res{index + 1}", "name": f"val{index + 1}",
                    "value": 1 if pointer == "/b" else 2, "locator": pointer})
        doc["results"].append(row)
    (root / "runevidence.json").write_text(dump_manifest(Manifest.model_validate(doc)))

    calls = []
    real = results.parse_json_document
    monkeypatch.setattr(results, "parse_json_document", lambda data: calls.append(data) or real(data))
    report = _report(root)
    assert report.findings == []
    assert len(calls) == 1


def test_many_jsonl_locators_split_and_decode_the_file_once(tmp_path, monkeypatch):
    import runevidence.checks.results as results

    root = tmp_path / "parse-once-jsonl"
    _write(root, data=b'{"a": 0.9}\n{"b": 1}\n{"c": 2}\n', locator={"kind": "jsonl", "line": 1, "pointer": "/a"}, value=0.9)
    doc = json.loads((root / "runevidence.json").read_text())
    for index, (line, pointer, value) in enumerate(((2, "/b", 1), (3, "/c", 2))):
        row = dict(doc["results"][0])
        row.update({"id": f"res{index + 1}", "name": f"val{index + 1}", "value": value,
                    "locator": {"kind": "jsonl", "line": line, "pointer": pointer}})
        doc["results"].append(row)
    (root / "runevidence.json").write_text(dump_manifest(Manifest.model_validate(doc)))

    constructed = []
    original = results.JsonlDocument

    class CountingJsonlDocument(original):
        def __init__(self, data):
            constructed.append(data)
            super().__init__(data)

    monkeypatch.setattr(results, "JsonlDocument", CountingJsonlDocument)
    report = _report(root)
    assert report.findings == []
    assert len(constructed) == 1


HUGE = int("9" * 400)


@pytest.mark.parametrize("kind", ["jsonl", "json", "legacy"])
@pytest.mark.parametrize("direction", ["huge-recorded", "huge-actual"])
def test_huge_int_versus_large_float_is_a_fixed_finding(tmp_path, kind, direction):
    root = tmp_path / f"n2-{kind}-{direction}"
    huge_bytes = b"9" * 400
    if direction == "huge-recorded":
        data = b'{"val": 1.5e308}\n'
        recorded = HUGE
    else:
        data = b'{"val": ' + huge_bytes + b"}\n"
        recorded = 1.5e308
    if kind == "jsonl":
        locator = {"kind": "jsonl", "line": 1, "pointer": "/val"}
        path = "metrics.jsonl"
        version = "0.3"
    elif kind == "json":
        locator = "/val"
        path = "metrics.json"
        version = "0.3"
    else:
        locator = "/val"
        path = "metrics.json"
        version = "0.2"
    _write(root, data=data, value=recorded, locator=locator, artifact_path=path, version=version)
    report = _report(root)
    blob = " ".join(f.message for f in report.findings)
    assert "Traceback" not in blob
    assert "OverflowError" not in blob and "int too large" not in blob
    # The comparison completed and produced an ordinary mismatch/unverifiable
    # finding rather than escaping as a Python exception.
    assert report.findings, (kind, direction, _codes(report))


def test_a_changed_structured_source_does_not_suggest_rehash(tmp_path):
    root = tmp_path / "changed-object"
    _write(root, locator={"kind": "jsonl", "line": 1, "pointer": "/val"})
    (root / "metrics.jsonl").write_bytes(b'{"val": 0.3}\n')

    report = _report(root)

    hint = _finding(report, "hash-mismatch").hint
    assert "rehash" in hint and "cannot accept" in hint
    assert "accept it with" not in hint
