"""Evidence-aware comparison: recorded conditions plus artifact/value integrity."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import comparability, run_comparability
from .checks import run_checks
from .findings import Code, Severity
from .manifest import ManifestLoadError, load_manifest
from .summaries import SUMMARY_SCHEMAS, summary_shape

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


# A selected value is "verified" only when a checker actually read its source
# and found the recorded value there. Absence of an error is not verification:
# schemas 0.1/0.2 results without a locator, and locator-less results in later
# schemas, are recorded assertions that no check can compare.
_UNVERIFIED_CODES = {
    Code.result_unverified,
    Code.result_unverifiable,
    Code.locator_unverified,
    Code.locator_no_digest,
    Code.locator_kind_mismatch,
    Code.locator_unreadable,
    Code.locator_unsupported_value,
    Code.locator_mismatch,
    Code.directory_locator_unsupported,
    Code.artifact_changed,
}
# Only a completed value comparison that found a different value is "failed".
# A locator that cannot be used or read is "unverified", never a mismatch.
_FAILED_CODES = {Code.result_value_mismatch}


def _artifact_errors(findings, artifact_id: Optional[str]) -> list:
    if artifact_id is None:
        return []
    return [
        finding
        for finding in findings
        if finding.severity == Severity.error and finding.subject == artifact_id
    ]


def _result_state(manifest, result_id: str, findings) -> dict:
    """How one selected result is backed, without inferring from silence.

    ``verified`` means a locator or a verifiable 0.1/0.2 summary was read and
    matched. ``failed`` means a check ran and disagreed. ``unverified`` means
    no check could establish the value. ``missing`` means the ID is absent.
    """
    result = next((item for item in manifest.results if item.id == result_id), None)
    if result is None:
        return {"id": result_id, "state": "missing", "reason": "result-not-found"}
    subjects = [finding for finding in findings if finding.subject == result_id]
    errors = [finding for finding in subjects if finding.severity == Severity.error]
    failed = next((finding for finding in errors if finding.code in _FAILED_CODES), None)
    if failed is not None:
        return {"id": result_id, "state": "failed", "reason": failed.code.value}
    unverified = next(
        (finding for finding in findings if finding.code in _UNVERIFIED_CODES
         and (finding.subject == result_id or finding.subject == result.artifact_id)),
        None,
    )
    if unverified is not None:
        return {"id": result_id, "state": "unverified", "reason": unverified.code.value}
    if errors:
        return {"id": result_id, "state": "failed", "reason": errors[0].code.value}
    if result.locator is None:
        if (
            manifest.schema_version in SUMMARY_SCHEMAS
            and summary_shape(manifest, result)
        ):
            source_errors = _artifact_errors(findings, result.artifact_id)
            if source_errors:
                return {
                    "id": result_id,
                    "state": "unverified",
                    "reason": source_errors[0].code.value,
                }
            return {"id": result_id, "state": "verified", "reason": "summary"}
        return {
            "id": result_id,
            "state": "unverified",
            "reason": "no-source-locator",
        }
    if result.artifact_id is None:
        return {"id": result_id, "state": "unverified", "reason": "no-artifact"}
    artifact = next(
        (item for item in manifest.artifacts if item.id == result.artifact_id), None
    )
    if artifact is None:
        return {"id": result_id, "state": "unverified", "reason": "no-artifact"}
    if getattr(artifact, "artifact_type", None) == "directory":
        return {
            "id": result_id,
            "state": "unverified",
            "reason": "directory-artifact",
        }
    source_errors = _artifact_errors(findings, result.artifact_id)
    if source_errors:
        return {
            "id": result_id,
            "state": "unverified",
            "reason": source_errors[0].code.value,
        }
    return {"id": result_id, "state": "verified", "reason": "locator"}


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

    conditions_error: Optional[str] = None
    try:
        outcome = run_comparability.compare_paths(
            baseline,
            target,
            baseline_result_id=baseline_result_id,
            target_result_id=target_result_id,
        )
    except Exception:
        outcome = None
        conditions_error = "INTERNAL_ERROR"
    conditions = "unavailable"
    if outcome is not None:
        conditions = {0: "comparable", 1: "not-comparable"}.get(
            outcome.exit_code, "unavailable"
        )
        if conditions == "unavailable":
            error = outcome.document.get("error")
            code = error.get("code") if isinstance(error, dict) else None
            if isinstance(code, str):
                conditions_error = code
            else:
                core = outcome.document.get("core")
                decision = core.get("decision") if isinstance(core, dict) else None
                conditions_error = (
                    "INSUFFICIENT_EVIDENCE"
                    if decision == comparability.INSUFFICIENT
                    else "INTERNAL_ERROR"
                )

    baseline_report = run_checks(baseline_loaded, enable_directory_artifacts=directory_artifacts)
    target_report = run_checks(target_loaded, enable_directory_artifacts=directory_artifacts)
    baseline_errors, baseline_warnings = _counts(baseline_report)
    target_errors, target_warnings = _counts(target_report)
    evidence = {
        "baseline": {"errors": baseline_errors, "warnings": baseline_warnings, "intact": baseline_errors == 0},
        "target": {"errors": target_errors, "warnings": target_warnings, "intact": target_errors == 0},
    }
    results = {
        "baseline": _result_state(
            baseline_loaded.manifest, baseline_result_id, baseline_report.findings
        ),
        "target": _result_state(
            target_loaded.manifest, target_result_id, target_report.findings
        ),
    }
    values_verified = all(
        results[side]["state"] == "verified" for side in ("baseline", "target")
    )
    permitted = (
        conditions == "comparable"
        and evidence["baseline"]["intact"]
        and evidence["target"]["intact"]
        and values_verified
    )
    exit_code = 0 if permitted else 1
    document = {
        "format": FORMAT,
        "ok": True,
        "conditions": conditions,
        "conditions_error": conditions_error,
        "evidence": evidence,
        "results": results,
        "values_verified": values_verified,
        "permitted": permitted,
        "check_policy": {"directory_artifacts": directory_artifacts},
        "exit_code": exit_code,
        "notice": NOTICE,
    }
    condition_text = conditions
    if conditions_error is not None:
        condition_text += f" ({conditions_error})"
    value_text = ", ".join(
        f"{side} {results[side]['state']} ({results[side]['reason']})"
        for side in ("baseline", "target")
    )
    lines = [
        f"Recorded conditions: {condition_text}",
        f"Selected result values: {value_text}",
        f"Baseline evidence: {baseline_errors} errors, {baseline_warnings} warnings",
        f"Target evidence: {target_errors} errors, {target_warnings} warnings",
        f"Assessment: {'PERMITTED' if permitted else 'NOT PERMITTED'}",
        NOTICE,
    ]
    return AssessOutcome(exit_code, "\n".join(lines), document)


def assess_json(outcome: AssessOutcome) -> str:
    return json.dumps(outcome.document, indent=2, ensure_ascii=True, allow_nan=False)
