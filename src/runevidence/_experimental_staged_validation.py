"""Private receipt-based staged validation; no public route or persistence cutover."""

from __future__ import annotations

from dataclasses import dataclass

from . import _experimental_staged_reader as _reader
from ._experimental_versioned_manifest import StagingReceipt
from .checks import Report, run_checks
from .report import format_findings, format_overview, to_json


@dataclass(frozen=True)
class StagedValidationResult:
    """Two-observation result; freezing does not make nested objects immutable."""

    staged: _reader.StagedLoadedRead
    report: Report
    findings_text: str
    findings_json: str
    overview: str
    exit_code: int


def validate_staged_readonly(receipt: StagingReceipt, *, strict: bool = False) -> StagedValidationResult:
    """Check a synthetic stage, refusing if either advisory observation fails.

    Artifact files may be read during checks. Neither these reads nor the two
    stage/source observations form an atomic snapshot or prevent later changes.
    """
    first = _reader.load_staged_for_read(receipt)
    report = run_checks(first.loaded)
    text = format_findings(report, strict)
    json = to_json(report, strict)
    overview = format_overview(first.loaded.manifest)
    code = report.exit_code(strict)
    second = _reader.load_staged_for_read(receipt)
    if first != second:
        raise _reader.StagedReadError('invalid-stage', 'stage', 'observation-changed')
    return StagedValidationResult(first, report, text, json, overview, code)
