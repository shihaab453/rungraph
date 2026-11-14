"""Schema 0.4 ``csv-cell`` checker integration (slice C phase 3).

The standalone profile is pinned in ``test_csv_profile.py``; this file pins the
checker routing, the normal/capped CSV paths, the outcome precedence and the
per-manifest eligibility caps from RFC sections 6-8 and 11.4.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from runevidence.checks import files as file_checks
from runevidence.checks import run_checks
from runevidence.checks.results import CSV_CELL_CAP_MESSAGE, _extension_matches
from runevidence.locator_profiles import display_locator
from runevidence.manifest import dump_manifest, load_manifest
from runevidence.models import CsvCellLocator, Manifest04

CSV = b"metric,val_rmse\nx,0.5\n"


# --- helpers ---------------------------------------------------------------


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cell(row: int = 1, column: int = 2, header: str = "val_rmse") -> dict:
    return {"kind": "csv-cell", "row": row, "column": column, "header": header}


def _result(
    result_id: str = "r1",
    *,
    value=0.5,
    locator=None,
    artifact_id: str = "a",
    name: str = "val",
) -> dict:
    row = {
        "id": result_id,
        "run_id": "run",
        "name": name,
        "value": value,
        "artifact_id": artifact_id,
    }
    if locator is not None:
        row["locator"] = locator
    return row


def _artifact(
    artifact_id: str = "a",
    *,
    path: str = "metrics.csv",
    data: bytes = CSV,
    sha256=...,
    size_bytes=...,
    volatile: bool = False,
    artifact_type: str | None = None,
) -> dict:
    artifact = {
        "id": artifact_id,
        "path": path,
        "sha256": _digest(data) if sha256 is ... else sha256,
        "volatile": volatile,
    }
    if artifact_type == "directory":
        artifact["artifact_type"] = "directory"
        artifact["digest_format"] = "runevidence-directory-v1"
    else:
        artifact["size_bytes"] = len(data) if size_bytes is ... else size_bytes
        if artifact_type is not None:
            artifact["artifact_type"] = artifact_type
    return artifact


def _write(
    root: Path,
    results: list[dict],
    artifacts: list[dict] | None = None,
    *,
    data: bytes = CSV,
    write_files: bool = True,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    if write_files:
        for artifact in artifacts or [_artifact()]:
            if artifact.get("artifact_type") == "directory":
                continue
            path = root / artifact["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = data if artifact["path"] == "metrics.csv" else (
                b"v\n1\n" if artifact["path"].endswith(".csv") else b'{"val": 1}\n'
            )
            path.write_bytes(payload)
    doc = {
        "schema_version": "0.4",
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "name": "Run"}],
        "artifacts": artifacts if artifacts is not None else [_artifact()],
        "results": results,
        "evidence": [],
    }
    (root / "runevidence.json").write_text(
        dump_manifest(Manifest04.model_validate(doc)), encoding="utf-8"
    )


def _report(root: Path):
    return run_checks(load_manifest(root))


def _codes(report) -> list[str]:
    return [finding.code.value for finding in report.findings]


def _finding(report, code: str):
    return next(finding for finding in report.findings if finding.code.value == code)


def _cap_findings(report):
    return [f for f in report.findings if f.code.value == "locator-unreadable"
            and f.message == CSV_CELL_CAP_MESSAGE]


def _many_results(count: int, *, artifact_id: str = "a", value=1) -> list[dict]:
    return [
        _result(
            f"{artifact_id}-r{index}",
            value=value,
            locator=_cell(1, 1, "v"),
            artifact_id=artifact_id,
            name=f"v{index}",
        )
        for index in range(count)
    ]


# --- normal CSV path -------------------------------------------------------


def test_matching_cell_is_verified(tmp_path):
    _write(tmp_path, [_result(locator=_cell())])
    report = _report(tmp_path)
    assert report.findings == []
    assert report.exit_code() == 0


def test_wrong_value_is_a_shared_rendered_mismatch(tmp_path):
    _write(tmp_path, [_result(value=0.6, locator=_cell())])
    report = _report(tmp_path)
    assert _codes(report) == ["result-value-mismatch"]
    finding = _finding(report, "result-value-mismatch")
    assert finding.message == (
        "result 'r1' locator csv-cell:1,2 does not match the recorded value "
        "in 'metrics.csv'"
    )
    assert finding.hint is None


def test_header_mismatch_is_locator_mismatch(tmp_path):
    _write(tmp_path, [_result(locator=_cell(header="other"))])
    report = _report(tmp_path)
    assert _codes(report) == ["locator-mismatch"]
    assert _finding(report, "locator-mismatch").message == (
        "result 'r1' locator csv-cell:1,2 selected header does not match the "
        "header at this column in 'metrics.csv'"
    )


def test_row_beyond_data_is_not_found(tmp_path):
    _write(tmp_path, [_result(locator=_cell(row=2))])
    report = _report(tmp_path)
    assert _codes(report) == ["locator-mismatch"]
    assert _finding(report, "locator-mismatch").message == (
        "result 'r1' locator csv-cell:2,2 was not found in 'metrics.csv'"
    )


def test_column_beyond_header_is_not_selectable(tmp_path):
    _write(tmp_path, [_result(locator=_cell(column=3, header="third"))])
    report = _report(tmp_path)
    assert _codes(report) == ["locator-mismatch"]
    assert _finding(report, "locator-mismatch").message == (
        "result 'r1' locator csv-cell:1,3 was not selectable in 'metrics.csv'"
    )


@pytest.mark.parametrize(
    "cell",
    ["abc", "1_000", "+1", ".5", "1.", " 1", "1 ", "1,5", "", "001", "1e999"],
)
def test_non_numeric_cell_is_the_d2_locator_unreadable(tmp_path, cell):
    # Quote every cell so the refusal comes from the numeric grammar, not the
    # CSV structure (an unquoted comma is a ragged record; a blank line is a
    # blank record).
    data = f'v\n"{cell}"\n'.encode()
    _write(
        tmp_path,
        [_result(value=1, locator=_cell(1, 1, "v"))],
        [_artifact(data=data)],
        data=data,
    )
    report = _report(tmp_path)
    assert _codes(report) == ["locator-unreadable"]
    assert _finding(report, "locator-unreadable").message == (
        "result 'r1' locator csv-cell:1,1 selected cell is not a number under "
        "the csv-cell profile in 'metrics.csv'"
    )


def test_structural_refusal_is_per_csv_result_with_the_fixed_hint(tmp_path):
    data = b"a,b\n1\n"  # ragged record: the whole file is refused
    _write(
        tmp_path,
        [
            _result("r1", value=1, locator=_cell(1, 1, "a")),
            _result("r2", value=2, locator=_cell(1, 2, "b"), name="other"),
        ],
        [_artifact(data=data)],
        data=data,
    )
    report = _report(tmp_path)
    assert _codes(report) == ["locator-unreadable", "locator-unreadable"]
    for finding in report.findings:
        assert finding.message == (
            f"result {finding.subject!r} locator csv-cell:1,"
            f"{1 if finding.subject == 'r1' else 2} could not be read under "
            "the strict profile in 'metrics.csv'"
        )
        assert finding.hint == "the CSV artifact does not satisfy the strict csv-cell profile"


@pytest.mark.parametrize("value", ["1", True, None])
def test_recorded_non_numeric_value_is_unsupported(tmp_path, value):
    _write(tmp_path, [_result(value=value, locator=_cell())])
    report = _report(tmp_path)
    assert _codes(report) == ["locator-unsupported-value"]
    assert _finding(report, "locator-unsupported-value").message == (
        "result 'r1' locator csv-cell:1,2 selects an unsupported value in "
        "'metrics.csv'"
    )


def test_one_csv_cell_on_a_csv_artifact_never_enters_the_jsonl_prescan(tmp_path):
    data = b"metric\n1\n"
    _write(
        tmp_path,
        [_result(value=1, locator=_cell(1, 1, "metric"))],
        [_artifact(data=data)],
        data=data,
    )
    report = _report(tmp_path)
    assert report.findings == []


def test_extension_matching_is_kind_keyed_and_case_insensitive():
    csv = CsvCellLocator(kind="csv-cell", row=1, column=1, header="v")
    assert _extension_matches("DATA.CSV", csv)
    assert _extension_matches(r"nested\metrics.Csv", csv)
    assert not _extension_matches("metrics.json", csv)
    assert _extension_matches("metrics.JSON", "/x")
    assert _extension_matches("metrics.jsonl", {"kind": "jsonl"})


def test_display_locator_renders_csv_cell_without_the_header():
    csv = CsvCellLocator(kind="csv-cell", row=41, column=4, header="val_rmse")
    assert display_locator(csv) == "csv-cell:41,4"
    assert display_locator(
        {"kind": "csv-cell", "row": 41, "column": 4, "header": "val_rmse"}
    ) == "csv-cell:41,4"
    # 0.3 forms stay byte-identical.
    assert display_locator("/x") == "json#/x"
    assert display_locator({"kind": "jsonl", "line": 2, "pointer": "/v"}) == "jsonl:2#/v"


# --- precedence ------------------------------------------------------------


def test_volatile_digestless_csv_is_only_unverified(tmp_path):
    _write(
        tmp_path,
        [_result(value=99, locator=_cell())],
        [_artifact(sha256=None, volatile=True)],
    )
    report = _report(tmp_path)
    assert _codes(report) == ["locator-unverified"]
    assert "no-digest" not in _codes(report)


def test_digestless_all_kind_mismatched_restores_the_no_digest_warning(tmp_path):
    data = b'{"val": 1}\n'
    _write(
        tmp_path,
        [_result(value=99, locator=_cell())],
        [_artifact(path="metrics.json", sha256=None, data=data)],
        data=data,
    )
    report = _report(tmp_path)
    assert _codes(report) == ["locator-kind-mismatch", "no-digest"]
    assert _finding(report, "locator-kind-mismatch").message == (
        "result 'r1' locator csv-cell:1,2 needs a .csv artifact, not 'metrics.json'"
    )
    assert "locator-no-digest" not in _codes(report)


def test_missing_wrong_extension_is_only_missing_file(tmp_path):
    _write(
        tmp_path,
        [_result(locator=_cell())],
        [_artifact(path="missing.json")],
        write_files=False,
    )
    report = _report(tmp_path)
    assert _codes(report) == ["missing-file"]


def test_refused_path_spelling_gets_only_its_artifact_finding(tmp_path):
    (tmp_path / "a").mkdir(parents=True)
    data = CSV
    (tmp_path / "metrics.csv").write_bytes(data)
    _write(
        tmp_path,
        [_result(locator=_cell())],
        [_artifact(path="a/../metrics.csv", data=data)],
        write_files=False,
    )
    report = _report(tmp_path)
    assert _codes(report) == ["locator-unreadable"]
    finding = _finding(report, "locator-unreadable")
    assert finding.subject == "a"
    assert "uses '..'" in finding.message
    assert "result" not in finding.message
    assert not _cap_findings(report)


def test_size_mismatch_with_equal_value_is_the_only_finding(tmp_path):
    _write(
        tmp_path,
        [_result(value=0.5, locator=_cell())],
        [_artifact(size_bytes=len(CSV) + 1)],
    )
    report = _report(tmp_path)
    assert _codes(report) == ["size-mismatch"]


def test_size_mismatch_with_different_value_compares_the_cell(tmp_path):
    _write(
        tmp_path,
        [_result(value=0.6, locator=_cell())],
        [_artifact(size_bytes=len(CSV) + 1)],
    )
    report = _report(tmp_path)
    assert set(_codes(report)) == {"size-mismatch", "result-value-mismatch"}


def test_no_digest_wrong_size_never_compares_the_value(tmp_path):
    _write(
        tmp_path,
        [_result(value=0.6, locator=_cell())],
        [_artifact(sha256=None, size_bytes=len(CSV) + 1)],
    )
    report = _report(tmp_path)
    assert set(_codes(report)) == {"size-mismatch", "locator-no-digest"}
    assert "result-value-mismatch" not in _codes(report)


def test_normal_over_cap_csv_is_per_result_unreadable(tmp_path, monkeypatch):
    monkeypatch.setattr(file_checks, "MAX_FILE_BYTES", 8)
    data = b"v\n1234567890\n"
    _write(
        tmp_path,
        [_result(value=1234567890, locator=_cell(1, 1, "v"))],
        [_artifact(data=data)],
        data=data,
    )
    report = _report(tmp_path)
    assert _codes(report) == ["locator-unreadable"]
    assert _finding(report, "locator-unreadable").message == (
        "result 'r1' locator csv-cell:1,1 was not read because the artifact "
        "exceeds 50 MiB in 'metrics.csv'"
    )


# --- per-manifest caps -----------------------------------------------------


def test_exactly_10000_entries_are_not_capped(tmp_path):
    data = b"v\n1\n"
    _write(
        tmp_path,
        _many_results(10_000),
        [_artifact(data=data)],
        data=data,
    )
    report = _report(tmp_path)
    assert _cap_findings(report) == []
    assert report.findings == []


def test_10001_entries_emit_one_cap_finding_on_the_artifact(tmp_path):
    data = b"v\n1\n"
    _write(
        tmp_path,
        _many_results(10_001),
        [_artifact(data=data)],
        data=data,
    )
    report = _report(tmp_path)
    caps = _cap_findings(report)
    assert len(caps) == 1
    assert caps[0].subject == "a"
    assert caps[0].path == "metrics.csv"
    assert caps[0].hint is None
    assert len(report.findings) == 1


@pytest.mark.parametrize("ineligible", ["digestless", "volatile", "directory"])
def test_ineligible_entries_do_not_count_toward_the_cap(tmp_path, ineligible):
    data = b"v\n1\n"
    results = _many_results(10_000)
    if ineligible == "directory":
        artifact = _artifact(
            "b", path="other.csv", data=data, artifact_type="directory"
        )
    else:
        artifact = _artifact(
            "b",
            path="other.csv",
            data=data,
            sha256=None if ineligible == "digestless" else ...,
            volatile=ineligible == "volatile",
        )
    results.append(
        _result(
            "extra",
            value=1,
            locator=_cell(1, 1, "v"),
            artifact_id="b",
            name="extra",
        )
    )
    _write(
        tmp_path,
        results,
        [_artifact(data=data), artifact],
        data=data,
    )
    report = _report(tmp_path)
    assert _cap_findings(report) == []
    if ineligible == "directory":
        assert "directory-locator-unsupported" in _codes(report)
    elif ineligible == "digestless":
        assert "locator-no-digest" in _codes(report)
    else:
        assert "locator-unverified" in _codes(report)


def test_5001_plus_5001_emits_one_cap_per_artifact(tmp_path):
    data = b"v\n1\n"
    artifacts = [
        _artifact("a", path="a.csv", data=data),
        _artifact("b", path="b.csv", data=data),
    ]
    results = _many_results(5_001, artifact_id="a")
    results += _many_results(5_001, artifact_id="b")
    _write(tmp_path, results, artifacts, data=data)
    report = _report(tmp_path)
    caps = _cap_findings(report)
    assert len(caps) == 2
    assert {finding.subject for finding in caps} == {"a", "b"}
    assert len(report.findings) == 2


def test_header_metadata_at_the_cap_is_uncapped(tmp_path):
    header = "h" * 65_536
    data = header.encode() + b"\n1\n"
    results = [
        _result(
            f"r{index}",
            value=1,
            locator=_cell(1, 1, header),
            name=f"h{index}",
        )
        for index in range(256)
    ]
    _write(tmp_path, results, [_artifact(data=data)], data=data)
    report = _report(tmp_path)
    assert _cap_findings(report) == []
    assert report.findings == []


def test_header_metadata_one_byte_over_the_cap_is_capped(tmp_path):
    header = "h" * 65_536
    oversized = "é" * 32_768 + "a"  # 32,769 code points, 65,537 UTF-8 bytes
    assert len(oversized.encode("utf-8")) == 65_537
    results = [
        _result(f"r{index}", value=1, locator=_cell(1, 1, header), name=f"h{index}")
        for index in range(255)
    ]
    results.append(
        _result("oversized", value=1, locator=_cell(1, 1, oversized), name="big")
    )
    _write(tmp_path, results, [_artifact(data=b"v\n1\n")], data=b"v\n1\n")
    report = _report(tmp_path)
    caps = _cap_findings(report)
    assert len(caps) == 1
    assert caps[0].message == CSV_CELL_CAP_MESSAGE
    assert len(report.findings) == 1


def test_capped_artifact_keeps_a_jsonl_kind_mismatch(tmp_path):
    data = b"v\n1\n"
    results = _many_results(10_001)
    results.append(
        _result(
            "jsonl",
            value=1,
            locator={"kind": "jsonl", "line": 1, "pointer": "/v"},
            name="jsonl",
        )
    )
    _write(tmp_path, results, [_artifact(data=data)], data=data)
    report = _report(tmp_path)
    assert len(_cap_findings(report)) == 1
    kind = _finding(report, "locator-kind-mismatch")
    assert kind.message == (
        "result 'jsonl' locator jsonl:1#/v needs a .jsonl artifact, not 'metrics.csv'"
    )
    assert len(report.findings) == 2


def test_capped_missing_artifact_has_no_cap_finding(tmp_path):
    artifacts = [_artifact("a", path="missing.csv")]
    _write(tmp_path, _many_results(10_001), artifacts, write_files=False)
    report = _report(tmp_path)
    assert _cap_findings(report) == []
    assert _codes(report) == ["missing-file"]


def test_capped_over_cap_artifact_gets_only_the_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(file_checks, "MAX_FILE_BYTES", 8)
    data = b"v\n1234567890\n"
    _write(
        tmp_path,
        _many_results(10_001, value=1234567890),
        [_artifact(data=data)],
        data=data,
    )
    report = _report(tmp_path)
    assert len(_cap_findings(report)) == 1
    assert len(report.findings) == 1


@pytest.mark.parametrize("mismatch", ["hash", "size"])
def test_capped_artifact_keeps_its_hash_or_size_finding(tmp_path, mismatch):
    data = b"v\n1\n"
    if mismatch == "hash":
        artifact = _artifact(data=data, sha256="0" * 64)
    else:
        artifact = _artifact(data=data, size_bytes=len(data) + 1)
    _write(tmp_path, _many_results(10_001), [artifact], data=data)
    report = _report(tmp_path)
    assert len(_cap_findings(report)) == 1
    assert f"{mismatch}-mismatch" in _codes(report)
    assert len(report.findings) == 2


# --- 0.4 absent locator ----------------------------------------------------


def test_absent_locator_0_4_is_result_unverified(tmp_path):
    _write(tmp_path, [_result(locator=None)])
    report = _report(tmp_path)
    assert _codes(report) == ["result-unverified"]
    assert _finding(report, "result-unverified").message == (
        "result 'r1' has no locator in schema 0.4; its recorded value was not checked"
    )
    assert report.exit_code() == 0
    assert report.exit_code(strict=True) == 1
