"""Schema 0.5 TensorBoard scalar checker integration (slice D phase 3).

The reader is pinned in ``test_tensorboard_events.py``; this file pins the
checker routing, display/privacy, per-kind file caps, the section-5 snapshot
race table, outcome precedence and the independent per-manifest caps from RFC
sections 4.4-8 and 12.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from runevidence.checks import (
    _tensorboard_cap,
    _tensorboard_snapshot_ids,
    run_checks,
)
from runevidence.checks import files as file_checks
from runevidence.checks import results as result_checks
from runevidence.checks.results import (
    _extension_matches,
    _tensorboard_path_matches,
)
from runevidence.cli import main
from runevidence.locator_profiles import display_locator
from runevidence.manifest import dump_manifest, load_manifest
from runevidence.models import (
    Artifact,
    CsvCellLocator,
    EvidenceReference,
    JsonlLocator,
    Manifest,
    Manifest04,
    Manifest05,
    Project,
    Result,
    Result04,
    Result05,
    Run,
    TensorboardLocator,
)
from runevidence.run_claim import (
    _display_details,
    claim_details,
    format_claim_details,
)
from runevidence.run_details import format_run_details, run_details
from runevidence.run_lineage import claim_lineage, format_claim_lineage

import tensorboard_builder as tb

MIB = 1024 * 1024
EVENT_PATH = "events.tfevents"
TAG = "loss/val"

# Literal shipped texts. Do not import these from the implementation: a test
# that compares a finding to the same module constant that produced it cannot
# detect a changed message.
TENSORBOARD_PROFILE_TEXT = (
    "the TensorBoard event file does not satisfy the strict profile"
)
TENSORBOARD_CAP_TEXT = (
    "the manifest exceeds the tensorboard verification limit (10,000 entries or "
    "16 MiB of tag metadata); no TensorBoard scalar was read"
)
CSV_CELL_CAP_TEXT = (
    "the manifest exceeds the csv-cell verification limit (10,000 entries or "
    "16 MiB of header metadata); no CSV cell was read"
)


# --- helpers ---------------------------------------------------------------


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tb(tag: str = TAG, step: int = 40, occurrence: int = 0) -> dict:
    return {
        "kind": "tensorboard",
        "tag": tag,
        "step": step,
        "occurrence": occurrence,
    }


def _cell(row: int = 1, column: int = 2, header: str = "val") -> dict:
    return {"kind": "csv-cell", "row": row, "column": column, "header": header}


def _scalar_event(
    tag: bytes = TAG.encode(), value: float = 0.5, *, step: int = 40
) -> bytes:
    return tb.records(
        tb.event(step=step, summary_bytes=tb.summary(tb.value(tag=tag, simple_value=value)))
    )


def _artifact(
    artifact_id: str = "a",
    *,
    path: str = EVENT_PATH,
    data: bytes = b"",
    sha256=...,
    size_bytes=...,
    volatile: bool = False,
    artifact_type: str | None = None,
) -> dict:
    artifact = {"id": artifact_id, "path": path, "volatile": volatile}
    if artifact_type == "directory":
        artifact["artifact_type"] = "directory"
        artifact["digest_format"] = "runevidence-directory-v1"
    else:
        artifact["sha256"] = _digest(data) if sha256 is ... else sha256
        artifact["size_bytes"] = len(data) if size_bytes is ... else size_bytes
        if artifact_type is not None:
            artifact["artifact_type"] = artifact_type
    artifact["_data"] = data
    return artifact


def _result(
    result_id: str = "r1",
    *,
    value=0.5,
    locator=None,
    artifact_id: str = "a",
    name: str = "metric",
    run_id: str = "run",
) -> dict:
    row = {
        "id": result_id,
        "run_id": run_id,
        "name": name,
        "value": value,
        "artifact_id": artifact_id,
    }
    if locator is not None:
        row["locator"] = locator
    return row


def _write(
    root: Path,
    results: list[dict],
    artifacts: list[dict] | None = None,
    *,
    schema: str = "0.5",
    evidence: list[dict] | None = None,
    write_files: bool = True,
) -> list[dict]:
    root.mkdir(parents=True, exist_ok=True)
    artifacts = artifacts if artifacts is not None else [_artifact(data=_scalar_event())]
    model_artifacts: list[dict] = []
    for artifact in artifacts:
        data = artifact.pop("_data", b"")
        if write_files and artifact.get("artifact_type") != "directory":
            path = root / artifact["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        model_artifacts.append(artifact)
    doc = {
        "schema_version": schema,
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "name": "Run"}],
        "artifacts": model_artifacts,
        "results": results,
        "evidence": evidence or [],
    }
    if schema == "0.5":
        manifest = Manifest05.model_validate(doc)
    elif schema == "0.4":
        manifest = Manifest04.model_validate(doc)
    else:
        manifest = Manifest.model_validate(doc)
    (root / "runevidence.json").write_text(
        dump_manifest(manifest), encoding="utf-8", newline="\n"
    )
    return artifacts


def _report(root: Path):
    return run_checks(load_manifest(root))


def _codes(report) -> list[str]:
    return [finding.code.value for finding in report.findings]


def _finding(report, code: str):
    return next(finding for finding in report.findings if finding.code.value == code)


def _many_tb(count: int, *, artifact_id: str = "a", tag: str = TAG) -> list[dict]:
    return [
        _result(
            f"tb-{index}",
            value=0.5,
            locator=_tb(tag=tag, occurrence=0),
            artifact_id=artifact_id,
            name=f"metric{index}",
        )
        for index in range(count)
    ]


def _many_csv(count: int, *, artifact_id: str = "a") -> list[dict]:
    return [
        _result(
            f"csv-{index}",
            value=1,
            locator=_cell(1, 1, "v"),
            artifact_id=artifact_id,
            name=f"v{index}",
        )
        for index in range(count)
    ]


# --- predicates ------------------------------------------------------------


def test_tensorboard_predicate_is_basename_and_case_insensitive():
    tb_locator = TensorboardLocator(kind="tensorboard", tag=TAG, step=40, occurrence=0)
    csv_locator = CsvCellLocator(kind="csv-cell", row=1, column=1, header="v")
    assert _extension_matches("EVENTS.TFEVENTS", tb_locator)
    assert _extension_matches(r"nested\EVENTS.TfEvents", tb_locator)
    # A path may match more than one kind; each locator uses its own predicate.
    assert _extension_matches("metrics.tfevents.csv", tb_locator)
    assert _extension_matches("metrics.tfevents.csv", csv_locator)
    # The predicate is "basename contains .tfevents", not a suffix rule.
    assert _extension_matches("x/events.tfevents.bak", tb_locator)
    assert _tensorboard_path_matches("x/events.tfevents.bak")
    # A directory component named .tfevents never makes a child eligible.
    assert not _extension_matches("events.tfevents/child.txt", tb_locator)
    assert not _tensorboard_path_matches("events.tfevents/child.txt")
    assert not _tensorboard_path_matches("x/.tfevents/events.log")


def test_directory_named_tfevents_never_raises_the_cap_or_matches_a_child():
    directory = _artifact(
        "dir", path="events.tfevents", artifact_type="directory"
    )
    child = _artifact("child", path="events.tfevents/child.txt", data=b"x")
    doc = {
        "schema_version": "0.5",
        "project": {"id": "p", "name": "P"},
        "artifacts": [
            {key: value for key, value in directory.items() if key != "_data"},
            {key: value for key, value in child.items() if key != "_data"},
        ],
        "results": [
            _result("on-dir", locator=_tb(), artifact_id="dir"),
            _result("on-child", locator=_tb(), artifact_id="child"),
        ],
    }
    manifest = Manifest05.model_validate(doc)
    capped, eligible = _tensorboard_cap(manifest)
    assert (capped, eligible) == (False, frozenset())
    assert _tensorboard_snapshot_ids(manifest) == frozenset()


def test_tensorboard_snapshot_ids_ignore_kind_mismatched_locators():
    doc = {
        "schema_version": "0.5",
        "project": {"id": "p", "name": "P"},
        "artifacts": [
            {
                "id": "csv",
                "path": "metrics.csv",
                "sha256": "a" * 64,
                "size_bytes": 1,
            }
        ],
        "results": [_result(locator=_tb(), artifact_id="csv")],
    }
    manifest = Manifest05.model_validate(doc)
    assert _tensorboard_snapshot_ids(manifest) == frozenset()
    assert _tensorboard_cap(manifest) == (False, frozenset())


# --- outcomes and fixed texts ---------------------------------------------


def test_matching_scalar_is_verified(tmp_path):
    _write(tmp_path, [_result(locator=_tb())])
    report = _report(tmp_path)
    assert report.findings == []
    assert report.exit_code() == 0


def test_float32_tolerance_is_used(tmp_path):
    _write(tmp_path, [_result(value=0.5000004, locator=_tb())])
    report = _report(tmp_path)
    assert report.findings == []


def test_absent_tag_is_locator_mismatch(tmp_path):
    _write(tmp_path, [_result(locator=_tb(tag="other"))])
    report = _report(tmp_path)
    assert _codes(report) == ["locator-mismatch"]
    assert _finding(report, "locator-mismatch").message == (
        "result 'r1' locator tensorboard:step=40,occurrence=0 was not found "
        f"in {EVENT_PATH!r}"
    )


def test_occurrence_beyond_supported_is_locator_mismatch(tmp_path):
    _write(tmp_path, [_result(locator=_tb(occurrence=1))])
    report = _report(tmp_path)
    assert _codes(report) == ["locator-mismatch"]
    assert "was not found" in _finding(report, "locator-mismatch").message


def test_unsupported_scalar_encoding_has_its_own_text(tmp_path):
    data = tb.records(
        tb.event(
            step=40,
            summary_bytes=tb.summary(
                tb.value(
                    tag=TAG.encode(),
                    tensor_bytes=tb.tensor(dtype=3, shape_bytes=tb.shape()),
                )
            ),
        )
    )
    _write(
        tmp_path,
        [_result(locator=_tb())],
        [_artifact(data=data)],
    )
    report = _report(tmp_path)
    assert _codes(report) == ["locator-unsupported-value"]
    assert _finding(report, "locator-unsupported-value").message == (
        "result 'r1' locator tensorboard:step=40,occurrence=0 selects an "
        f"unsupported scalar encoding in {EVENT_PATH!r}"
    )


def test_non_scalar_selects_an_unsupported_value(tmp_path):
    data = tb.records(
        tb.event(
            step=40,
            summary_bytes=tb.summary(
                tb.value(tag=TAG.encode(), histogram=b"")
            ),
        )
    )
    _write(tmp_path, [_result(locator=_tb())], [_artifact(data=data)])
    report = _report(tmp_path)
    assert _codes(report) == ["locator-unsupported-value"]
    assert _finding(report, "locator-unsupported-value").message == (
        "result 'r1' locator tensorboard:step=40,occurrence=0 selects an "
        f"unsupported value in {EVENT_PATH!r}"
    )


@pytest.mark.parametrize("value", ["0.5", True, None])
def test_recorded_type_is_an_unsupported_value(tmp_path, value):
    _write(tmp_path, [_result(value=value, locator=_tb())])
    report = _report(tmp_path)
    assert _codes(report) == ["locator-unsupported-value"]
    assert _finding(report, "locator-unsupported-value").message == (
        "result 'r1' locator tensorboard:step=40,occurrence=0 selects an "
        f"unsupported value in {EVENT_PATH!r}"
    )


def test_value_mismatch_uses_the_shared_text(tmp_path):
    _write(tmp_path, [_result(value=0.75, locator=_tb())])
    report = _report(tmp_path)
    assert _codes(report) == ["result-value-mismatch"]
    assert _finding(report, "result-value-mismatch").message == (
        "result 'r1' locator tensorboard:step=40,occurrence=0 does not match "
        f"the recorded value in {EVENT_PATH!r}"
    )


def test_structural_refusal_is_per_result_with_the_fixed_hint(tmp_path):
    data = tb.record(b"broken")[:-1]
    _write(
        tmp_path,
        [
            _result("r1", locator=_tb()),
            _result("r2", locator=_tb(tag="other"), name="other"),
        ],
        [_artifact(data=data)],
    )
    report = _report(tmp_path)
    assert _codes(report) == ["locator-unreadable", "locator-unreadable"]
    for finding in report.findings:
        assert finding.hint == TENSORBOARD_PROFILE_TEXT
        assert finding.message == (
            f"result {finding.subject!r} locator "
            "tensorboard:step=40,occurrence=0 could not be read under the "
            f"strict TensorBoard profile in {EVENT_PATH!r}"
        )


def test_structural_refusal_never_stops_a_csv_result_on_the_same_artifact(tmp_path):
    data = b"metric,val\nx,0.5\n"
    _write(
        tmp_path,
        [
            _result("tb", locator=_tb(), name="tb-metric"),
            _result("csv", value=0.5, locator=_cell(1, 2, "val"), name="csv-metric"),
        ],
        [_artifact(path="events.tfevents.csv", data=data)],
    )
    report = _report(tmp_path)
    assert _codes(report) == ["locator-unreadable"]
    finding = _finding(report, "locator-unreadable")
    assert finding.subject == "tb"
    assert finding.hint == TENSORBOARD_PROFILE_TEXT
    assert "csv-cell" not in finding.message


# --- 0.5 absent locator and claims ----------------------------------------


def test_absent_locator_0_5_is_result_unverified(tmp_path):
    _write(tmp_path, [_result(locator=None)])
    report = _report(tmp_path)
    assert _codes(report) == ["result-unverified"]
    assert _finding(report, "result-unverified").message == (
        "result 'r1' has no locator in schema 0.5; its recorded value was not checked"
    )
    assert report.exit_code() == 0
    assert report.exit_code(strict=True) == 1


def test_claim_only_from_unverified_0_5_is_claim_unverified(tmp_path):
    _write(
        tmp_path,
        [_result(locator=None)],
        evidence=[{"id": "claim", "claim": "text", "supports": ["r1"]}],
    )
    report = _report(tmp_path)
    assert set(_codes(report)) == {"result-unverified", "claim-unverified"}
    assert _finding(report, "claim-unverified").message == (
        "Claim 'claim' cites only results with no locator in schema 0.5; "
        "their recorded values were not checked"
    )


def test_directory_hint_names_tensorboard_0_5(tmp_path):
    _write(
        tmp_path,
        [_result(locator=_tb())],
        [_artifact(path="data", data=b"", artifact_type="directory")],
    )
    report = _report(tmp_path)
    assert "directory-locator-unsupported" in _codes(report)
    assert _finding(report, "directory-locator-unsupported").hint == (
        "Point the result at a single JSON, JSONL, CSV or TensorBoard event "
        "file artifact, or drop the locator."
    )


# --- precedence ------------------------------------------------------------


def test_missing_file_is_the_only_finding(tmp_path):
    _write(
        tmp_path,
        [_result(locator=_tb())],
        [_artifact(path="missing.tfevents", data=b"")],
        write_files=False,
    )
    report = _report(tmp_path)
    assert _codes(report) == ["missing-file"]


def test_kind_mismatch_needs_a_tensorboard_event_artifact(tmp_path):
    data = b"metric,val\nx,0.5\n"
    _write(
        tmp_path,
        [_result(locator=_tb())],
        [_artifact(path="metrics.csv", data=data)],
    )
    report = _report(tmp_path)
    assert _codes(report) == ["locator-kind-mismatch"]
    assert _finding(report, "locator-kind-mismatch").message == (
        "result 'r1' locator tensorboard:step=40,occurrence=0 needs a "
        "TensorBoard event artifact, not 'metrics.csv'"
    )


def test_volatile_is_only_locator_unverified(tmp_path):
    _write(
        tmp_path,
        [_result(value=99, locator=_tb())],
        [_artifact(data=_scalar_event(), sha256=None, volatile=True)],
    )
    report = _report(tmp_path)
    assert _codes(report) == ["locator-unverified"]


def test_no_digest_is_only_locator_no_digest(tmp_path):
    _write(
        tmp_path,
        [_result(locator=_tb())],
        [_artifact(data=_scalar_event(), sha256=None)],
    )
    report = _report(tmp_path)
    assert _codes(report) == ["locator-no-digest"]


def test_hash_and_size_still_compare_on_state_ok(tmp_path):
    data = _scalar_event()
    _write(
        tmp_path,
        [_result(locator=_tb())],
        [_artifact(data=data, sha256="0" * 64)],
    )
    report = _report(tmp_path)
    assert set(_codes(report)) == {"hash-mismatch"}

    _write(
        tmp_path,
        [_result(locator=_tb())],
        [_artifact(data=data, size_bytes=len(data) + 1)],
    )
    assert set(_codes(_report(tmp_path))) == {"size-mismatch"}


# --- per-manifest caps -----------------------------------------------------


def test_exactly_10000_tensorboard_entries_are_not_capped(tmp_path):
    data = _scalar_event()
    _write(tmp_path, _many_tb(10_000), [_artifact(data=data)])
    report = _report(tmp_path)
    assert report.findings == []


def test_10001_tensorboard_entries_emit_one_artifact_cap(tmp_path):
    data = _scalar_event()
    _write(tmp_path, _many_tb(10_001), [_artifact(data=data)])
    report = _report(tmp_path)
    assert _codes(report) == ["locator-unreadable"]
    finding = report.findings[0]
    assert finding.subject == "a"
    assert finding.path == EVENT_PATH
    assert finding.hint is None
    assert finding.message == TENSORBOARD_CAP_TEXT


def test_tensorboard_tag_metadata_at_16_mib_is_uncapped(tmp_path):
    tag = "t" * 4096
    data = _scalar_event(tag=tag.encode())
    _write(tmp_path, _many_tb(4096, tag=tag), [_artifact(data=data)])
    report = _report(tmp_path)
    assert report.findings == []


def test_tensorboard_tag_metadata_one_byte_over_16_mib_is_capped(tmp_path):
    tag = "t" * 4096
    data = _scalar_event(tag=tag.encode())
    _write(tmp_path, _many_tb(4097, tag=tag), [_artifact(data=data)])
    report = _report(tmp_path)
    assert _codes(report) == ["locator-unreadable"]
    assert report.findings[0].message == TENSORBOARD_CAP_TEXT


def test_cap_unit_is_utf8_bytes_not_code_points():
    # 2,048 code points of "é" are 4,096 UTF-8 bytes. 4,097 entries are
    # 16,781,312 bytes, already over 16 MiB; 4,096 is exactly 16,777,216.
    tag = "é" * 2048
    assert len(tag) == 2048 and len(tag.encode("utf-8")) == 4096
    artifact = Artifact(id="a", path=EVENT_PATH, sha256="a" * 64)
    results = [
        Result05(
            id=f"r{index}",
            run_id="run",
            name=f"m{index}",
            value=0.5,
            artifact_id="a",
            locator=TensorboardLocator(
                kind="tensorboard", tag=tag, step=40, occurrence=0
            ),
        )
        for index in range(4097)
    ]
    manifest = Manifest05(
        schema_version="0.5",
        project=Project(id="p", name="P"),
        runs=[Run(id="run", name="Run")],
        artifacts=[artifact],
        results=results,
    )
    assert _tensorboard_cap(manifest)[0] is True


def test_csv_cell_and_tensorboard_caps_are_independent(tmp_path):
    csv_data = b"v\n1\n"
    tb_data = _scalar_event()
    _write(
        tmp_path,
        _many_csv(10_001) + _many_tb(10_001, artifact_id="b"),
        [
            _artifact("a", path="metrics.csv", data=csv_data),
            _artifact("b", path=EVENT_PATH, data=tb_data),
        ],
    )
    report = _report(tmp_path)
    messages = sorted(
        finding.message
        for finding in report.findings
        if finding.code.value == "locator-unreadable"
    )
    assert messages == sorted([CSV_CELL_CAP_TEXT, TENSORBOARD_CAP_TEXT])
    assert {finding.subject for finding in report.findings} == {"a", "b"}


def test_tensorboard_cap_does_not_suppress_another_kind_or_a_kind_mismatch(tmp_path):
    data = b"metric,val\nx,0.5\n"
    results = _many_tb(10_001)
    results.append(
        _result(
            "csv",
            value=0.5,
            locator=_cell(1, 2, "val"),
            artifact_id="a",
            name="csv-metric",
        )
    )
    results.append(
        _result(
            "jsonl",
            value=0.5,
            locator={"kind": "jsonl", "line": 1, "pointer": "/val"},
            artifact_id="a",
            name="jsonl-metric",
        )
    )
    _write(
        tmp_path,
        results,
        [_artifact(path="events.tfevents.csv", data=data)],
    )
    report = _report(tmp_path)
    assert _codes(report).count("locator-unreadable") == 1
    assert _finding(report, "locator-unreadable").message == TENSORBOARD_CAP_TEXT
    kind = _finding(report, "locator-kind-mismatch")
    assert kind.subject == "jsonl"
    assert "needs a .jsonl artifact" in kind.message
    # The capped TensorBoard kind does not stop the csv-cell result verifying.
    assert not [f for f in report.findings if f.subject == "csv"]


def test_capped_artifact_propagates_claim_risk_with_the_cap_hint(tmp_path):
    data = _scalar_event()
    _write(
        tmp_path,
        _many_tb(10_001),
        [_artifact(data=data)],
        evidence=[{"id": "claim", "claim": "tuned model", "supports": ["tb-0"]}],
    )
    report = _report(tmp_path)
    assert len([f for f in report.findings if f.message == TENSORBOARD_CAP_TEXT]) == 1
    at_risk = _finding(report, "claim-at-risk")
    assert at_risk.severity.value == "error"
    assert at_risk.hint == (
        "a resource cap is not a change; narrow the manifest or raise the limit"
    )


# --- per-kind file caps ----------------------------------------------------


def _padded_event(total: int) -> bytes:
    """One valid event record of at least ``total`` bytes with one scalar."""
    base = tb.event(
        step=40,
        summary_bytes=tb.summary(tb.value(tag=TAG.encode(), simple_value=0.5)),
    )
    # A single unknown field is skipped by the reader; only framing and CRC
    # scan the padding, which keeps this fixture valid at >100 MiB.
    payload = base + tb.bytes_field(15, b"x" * total)
    return tb.records(payload)


def test_100_mib_tensorboard_artifact_uses_128_for_tb_and_50_for_csv(tmp_path):
    data = _padded_event(100 * MIB)
    assert 50 * MIB < len(data) <= 128 * MIB
    _write(
        tmp_path,
        [
            _result("tb", value=0.5, locator=_tb(), name="tb-metric"),
            _result("csv", value=0.5, locator=_cell(1, 2, "val"), name="csv-metric"),
        ],
        [_artifact(path="events.tfevents.csv", data=data)],
    )
    report = _report(tmp_path)
    assert _codes(report) == ["locator-unreadable"]
    finding = report.findings[0]
    assert finding.subject == "csv"
    assert finding.message == (
        "result 'csv' locator csv-cell:1,2 was not read because the artifact "
        "exceeds 50 MiB in 'events.tfevents.csv'"
    )
    assert not [f for f in report.findings if f.subject == "tb"]


def test_over_128_mib_tensorboard_artifact_still_hashes_and_sizes(tmp_path):
    data = b"x" * (129 * MIB)
    _write(
        tmp_path,
        [_result(locator=_tb())],
        [_artifact(data=data, sha256="0" * 64)],
    )
    report = _report(tmp_path)
    codes = _codes(report)
    assert "hash-mismatch" in codes
    assert codes.count("locator-unreadable") == 1
    finding = _finding(report, "locator-unreadable")
    assert finding.message == (
        "result 'r1' locator tensorboard:step=40,occurrence=0 was not read "
        f"because the artifact exceeds 128 MiB in {EVENT_PATH!r}"
    )


def test_0_4_keeps_read_to_eof_and_the_50_mib_message(tmp_path, monkeypatch):
    data = b"v\n1234567890\n"
    _write(
        tmp_path,
        [_result(value=1234567890, locator=_cell(1, 1, "v"))],
        [_artifact(path="metrics.csv", data=data)],
        schema="0.4",
    )
    real_fstat = os.fstat

    def huge_fstat(fd):
        stat = real_fstat(fd)
        return os.stat_result(
            (
                stat.st_mode,
                stat.st_ino,
                stat.st_dev,
                stat.st_nlink,
                stat.st_uid,
                stat.st_gid,
                200 * MIB,
                stat.st_atime,
                stat.st_mtime,
                stat.st_ctime,
            )
        )

    monkeypatch.setattr(file_checks.os, "fstat", huge_fstat)
    assert _report(tmp_path).findings == []

    monkeypatch.setattr(file_checks, "MAX_FILE_BYTES", 8)
    report = _report(tmp_path)
    assert _codes(report) == ["locator-unreadable"]
    assert report.findings[0].message == (
        "result 'r1' locator csv-cell:1,1 was not read because the artifact "
        "exceeds 50 MiB in 'metrics.csv'"
    )


# --- snapshot races (RFC section 5 table) ---------------------------------


def _fake_size_stat(real):
    def fake_fstat(fd):
        stat = real(fd)
        return os.stat_result(
            (
                stat.st_mode,
                stat.st_ino,
                stat.st_dev,
                stat.st_nlink,
                stat.st_uid,
                stat.st_gid,
                fake_fstat.size,
                stat.st_atime,
                stat.st_mtime,
                stat.st_ctime,
            )
        )

    fake_fstat.size = 0
    return fake_fstat


@pytest.mark.parametrize(
    "fstat_size,eof,expected",
    [
        (10_000_000, 10_000_300, "changed"),
        (10_000_000, 9_999_700, "changed"),
        (100 * MIB, 129 * MIB, "over-cap"),
        (140 * MIB, 100 * MIB, "changed"),
        (140 * MIB, 128 * MIB, "changed"),
        (140 * MIB, 140 * MIB, "over-cap"),
        (140 * MIB, 141 * MIB, "over-cap"),
    ],
)
def test_tensorboard_snapshot_race_table(
    tmp_path, monkeypatch, fstat_size, eof, expected
):
    data = _scalar_event()
    _write(
        tmp_path,
        [_result(locator=_tb())],
        [_artifact(data=data, sha256="0" * 64)],
    )
    real_fstat = os.fstat
    fake_fstat = _fake_size_stat(real_fstat)
    fake_fstat.size = fstat_size
    remaining = eof

    def fake_read(fd, size):
        nonlocal remaining
        if remaining <= 0:
            return b""
        take = min(size, remaining, MIB)
        remaining -= take
        return b"x" * take

    monkeypatch.setattr(file_checks.os, "fstat", fake_fstat)
    monkeypatch.setattr(file_checks.os, "read", fake_read)
    report = _report(tmp_path)
    codes = _codes(report)
    if expected == "changed":
        assert codes == ["artifact-changed"]
    else:
        assert "hash-mismatch" in codes
        assert codes.count("locator-unreadable") == 1
        assert "exceeds 128 MiB" in _finding(
            report, "locator-unreadable"
        ).message


# --- routing ---------------------------------------------------------------


def test_one_scan_per_artifact_for_all_tensorboard_results(tmp_path, monkeypatch):
    data = _scalar_event()
    _write(
        tmp_path,
        [
            _result("r1", locator=_tb(), name="one"),
            _result("r2", locator=_tb(), name="two"),
            _result("r3", locator=_tb(tag="other"), name="three"),
        ],
        [_artifact(data=data)],
    )
    calls = []
    real_scan = result_checks.scan_scalars

    def counting_scan(buffer, requests):
        calls.append(list(requests))
        return real_scan(buffer, requests)

    monkeypatch.setattr(result_checks, "scan_scalars", counting_scan)
    report = _report(tmp_path)
    assert len(calls) == 1
    assert len(calls[0]) == 3
    assert _codes(report) == ["locator-mismatch"]


# --- display and privacy ---------------------------------------------------


def test_display_locator_renders_tensorboard_without_the_tag():
    locator = TensorboardLocator(
        kind="tensorboard", tag="SECRET_TAG_7f3a", step=40, occurrence=0
    )
    assert display_locator(locator) == "tensorboard:step=40,occurrence=0"
    assert display_locator(
        {
            "kind": "tensorboard",
            "tag": "SECRET_TAG_7f3a",
            "step": 40,
            "occurrence": 0,
        }
    ) == "tensorboard:step=40,occurrence=0"
    # 0.3/0.4 forms stay byte-identical.
    assert display_locator("/x") == "json#/x"
    assert display_locator({"kind": "jsonl", "line": 2, "pointer": "/v"}) == "jsonl:2#/v"
    assert display_locator(
        {"kind": "csv-cell", "row": 1, "column": 2, "header": "val"}
    ) == "csv-cell:1,2"


def test_display_details_replaces_tensorboard_dict_and_model_locators():
    token = "TAG_9f3c2b7d"
    model = TensorboardLocator(kind="tensorboard", tag=token, step=40, occurrence=0)
    details = {"name": "metric", "locator": model}
    displayed = _display_details(details)
    assert displayed == {
        "name": "metric",
        "locator": "tensorboard:step=40,occurrence=0",
    }
    assert details["locator"] is model

    dict_details = {"name": "metric", "locator": _tb(tag=token)}
    assert _display_details(dict_details) == {
        "name": "metric",
        "locator": "tensorboard:step=40,occurrence=0",
    }
    assert dict_details["locator"]["tag"] == token


def _privacy_manifest() -> Manifest05:
    token = "TAG_PRIVACY_9f3c2b7d"
    data = _scalar_event(tag=token.encode())
    return Manifest05(
        schema_version="0.5",
        project=Project(id="proj", name="Proj"),
        runs=[Run(id="run", name="Run")],
        artifacts=[
            Artifact(
                id="art",
                path=EVENT_PATH,
                sha256=_digest(data),
                size_bytes=len(data),
            )
        ],
        results=[
            Result05(
                id="res",
                run_id="run",
                name="accuracy",
                value=0.6,
                artifact_id="art",
                locator=TensorboardLocator(
                    kind="tensorboard", tag=token, step=40, occurrence=0
                ),
            )
        ],
        evidence=[
            EvidenceReference(id="claim", claim="claim text", supports=["res"])
        ],
    )


def _human_bundle(manifest):
    return {
        "run": format_run_details(
            run_details(manifest, "run", manifest_path="exp/runevidence.json")
        ),
        "claim": format_claim_details(
            claim_details(manifest, "claim", manifest_path="exp/runevidence.json")
        ),
        "lineage": format_claim_lineage(
            claim_lineage(manifest, "claim", manifest_path="exp/runevidence.json")
        ),
    }


def test_tag_token_is_absent_from_findings_and_human_output_but_in_json(
    tmp_path, capsys
):
    token = "TAG_PRIVACY_9f3c2b7d"
    manifest = _privacy_manifest()
    rendered = _human_bundle(manifest)
    for text in rendered.values():
        assert token not in text
        assert "tensorboard:step=40,occurrence=0" in text

    folder = tmp_path / "exp"
    folder.mkdir()
    (folder / EVENT_PATH).write_bytes(_scalar_event(tag=token.encode()))
    (folder / "runevidence.json").write_text(
        dump_manifest(manifest), encoding="utf-8", newline="\n"
    )
    report = _report(folder)
    for finding in report.findings:
        haystack = " ".join(
            str(part)
            for part in (finding.subject, finding.path, finding.message, finding.hint)
        )
        assert token not in haystack
    assert "result-value-mismatch" in _codes(report)

    for route, identifier in (("run", "run"), ("claim", "claim"), ("lineage", "claim")):
        assert main([route, str(folder), "--id", identifier]) == 0
        text = capsys.readouterr().out
        assert token not in text
        assert "tensorboard:step=40,occurrence=0" in text
        assert main([route, str(folder), "--id", identifier, "--json"]) == 0
        assert token in capsys.readouterr().out


def test_03_human_output_bytes_are_unchanged():
    manifest = Manifest(
        schema_version="0.3",
        project=Project(id="proj", name="Proj"),
        runs=[Run(id="run", name="Run")],
        artifacts=[
            Artifact(id="json", path="metrics.json", sha256="a" * 64, size_bytes=10),
            Artifact(id="jsonl", path="metrics.jsonl", sha256="b" * 64, size_bytes=20),
        ],
        results=[
            Result(
                id="str",
                run_id="run",
                name="score",
                value=0.5,
                artifact_id="json",
                locator="/val",
            ),
            Result(
                id="obj",
                run_id="run",
                name="loss",
                value=0.25,
                artifact_id="jsonl",
                locator=JsonlLocator(kind="jsonl", line=2, pointer="/x"),
            ),
        ],
        evidence=[
            EvidenceReference(id="claim", claim="claim text", supports=["str", "obj"])
        ],
    )
    rendered = {
        "run": format_run_details(
            run_details(manifest, "run", manifest_path="exp/runevidence.json")
        ),
        "claim": format_claim_details(
            claim_details(manifest, "claim", manifest_path="exp/runevidence.json")
        ),
        "lineage": format_claim_lineage(
            claim_lineage(manifest, "claim", manifest_path="exp/runevidence.json")
        ),
    }
    expected = {
        "run": "5737ef6559a50e2ec6585f0f926c797675890c58fcfa1095c8edf36b6d8a0851",
        "claim": "4cf307f41866f166c80ba4cb36fb2d38e82eed4b6aece6a73bb9cbc2f9d70d3e",
        "lineage": "9aabd7057554e913238f438ce295934cf60688f852f1f549f026c6d19500caba",
    }
    for name, text in rendered.items():
        assert hashlib.sha256(text.encode("utf-8")).hexdigest() == expected[name]


def test_04_csv_display_is_unchanged():
    manifest = Manifest04(
        schema_version="0.4",
        project=Project(id="proj", name="Proj"),
        runs=[Run(id="run", name="Run")],
        artifacts=[
            Artifact(id="art", path="metrics.csv", sha256="a" * 64, size_bytes=21)
        ],
        results=[
            Result04(
                id="res",
                run_id="run",
                name="score",
                value=0.5,
                artifact_id="art",
                locator=CsvCellLocator(
                    kind="csv-cell", row=1, column=2, header="val_rmse"
                ),
            )
        ],
        evidence=[
            EvidenceReference(id="claim", claim="claim text", supports=["res"])
        ],
    )
    text = format_run_details(
        run_details(manifest, "run", manifest_path="exp/runevidence.json")
    )
    assert "csv-cell:1,2" in text
    assert "val_rmse" not in text
    assert "tensorboard" not in text
