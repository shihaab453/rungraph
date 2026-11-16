"""Evidence-aware comparison: recorded conditions plus artifact/value integrity."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import run_comparability
from .checks import run_checks
from .findings import Severity
from .manifest import ManifestLoadError, load_manifest

FORMAT = "runevidence-assess-v1"
NOTICE = (
    "Assessment is consistency evidence only. It does not establish scientific truth, "
    "causal equivalence, reproducibility or a fair comparison."
)


@dataclass(frozen=True)
class AssessOutcome:
    exit_code: int
    text: str
    document: dict


def _counts(report) -> tuple[int, int]:
    errors = sum(1 for finding in report.findings if finding.severity == Severity.error)
    warnings = len(report.findings) - errors
    return errors, warnings


def assess(
    baseline: Path,
    target: Path,
    *,
    baseline_result_id: str,
    target_result_id: str,
    directory_artifacts: bool = False,
) -> AssessOutcome:
    try:
        baseline_loaded = load_manifest(baseline)
        target_loaded = load_manifest(target)
    except (ManifestLoadError, OSError, ValueError):
        document = {"format": FORMAT, "ok": False, "error": {"code": "MANIFEST_LOAD_FAILED"}}
        return AssessOutcome(2, "ERROR: a manifest could not be loaded. Nothing was checked.", document)

    try:
        outcome = run_comparability.compare_paths(
            baseline,
            target,
            baseline_result_id=baseline_result_id,
            target_result_id=target_result_id,
        )
    except Exception:
        outcome = None
    conditions = "unavailable"
    if outcome is not None:
        conditions = {0: "comparable", 1: "not-comparable"}.get(outcome.exit_code, "unavailable")

    baseline_report = run_checks(baseline_loaded, enable_directory_artifacts=directory_artifacts)
    target_report = run_checks(target_loaded, enable_directory_artifacts=directory_artifacts)
    baseline_errors, baseline_warnings = _counts(baseline_report)
    target_errors, target_warnings = _counts(target_report)
    evidence = {
        "baseline": {"errors": baseline_errors, "warnings": baseline_warnings, "intact": baseline_errors == 0},
        "target": {"errors": target_errors, "warnings": target_warnings, "intact": target_errors == 0},
    }
    permitted = conditions == "comparable" and evidence["baseline"]["intact"] and evidence["target"]["intact"]
    exit_code = 0 if permitted else 1
    document = {
        "format": FORMAT,
        "ok": True,
        "conditions": conditions,
        "evidence": evidence,
        "permitted": permitted,
        "check_policy": {"directory_artifacts": directory_artifacts},
        "exit_code": exit_code,
        "notice": NOTICE,
    }
    lines = [
        f"Recorded conditions: {conditions}",
        f"Baseline evidence: {baseline_errors} errors, {baseline_warnings} warnings",
        f"Target evidence: {target_errors} errors, {target_warnings} warnings",
        f"Assessment: {'PERMITTED' if permitted else 'NOT PERMITTED'}",
        NOTICE,
    ]
    return AssessOutcome(exit_code, "\n".join(lines), document)


def assess_json(outcome: AssessOutcome) -> str:
    return json.dumps(outcome.document, indent=2, ensure_ascii=True, allow_nan=False)
