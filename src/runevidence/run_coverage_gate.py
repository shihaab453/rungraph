"""Read-only single-manifest CI gate over recorded structural claim-coverage flags.

Gate failures are user-selected review conditions, not validation findings,
artifact integrity verdicts, or evidence about scientific truth.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from .models import Manifest
from .run_coverage import CoverageError, _FLAGS, claim_coverage


class CoverageGateError(ValueError):
    """Selected structural flag policy or manifest namespace cannot be assessed."""


_FOOTER = "Recorded structural review policy only. Run 'runevidence validate' separately."


def evaluate_coverage_gate(
    manifest: Manifest, *, manifest_path: str,
    selected_flags: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Apply a caller-selected set of D6 structural review flags to one snapshot.

    ``None`` selects all known flags, but an explicit empty selection is
    rejected rather than silently defining an always-passing check. An ID can
    have multiple matching flags but counts as one failing claim. The complete
    unfiltered D6 coverage report remains present in machine-readable output.
    """
    if selected_flags is None:
        chosen = list(_FLAGS)
    elif isinstance(selected_flags, (str, bytes)):
        raise CoverageGateError("selected_flags must be a nonempty sequence of flag names")
    else:
        try:
            provided = list(selected_flags)
        except TypeError as exc:
            raise CoverageGateError("selected_flags must be a nonempty sequence of flag names") from exc
        if not provided:
            raise CoverageGateError("selected_flags cannot be empty; omit it to select all flags")
        if any(type(name) is not str or name not in _FLAGS for name in provided):
            raise CoverageGateError(f"unknown coverage flag; valid flags: {', '.join(_FLAGS)}")
        chosen = [name for name in _FLAGS if name in provided]
    try:
        coverage = claim_coverage(manifest, manifest_path=manifest_path, only_flagged=False)
    except CoverageError as exc:
        raise CoverageGateError(str(exc)) from exc
    failing_rows = [row for row in coverage["claims"] if set(row["review_flags"]) & set(chosen)]
    failing_ids = [row["id"] for row in failing_rows]
    selected_counts = {name: coverage["summary"]["flag_counts"][name] for name in chosen}
    result: dict[str, Any] = {
        "manifest": manifest_path,
        "gate": {
            "passed": not failing_ids,
            "exit_code": 1 if failing_ids else 0,
            "selected_flags": chosen,
            "failing_claims": len(failing_ids),
            "failing_claim_ids": failing_ids,
            "total_claims": coverage["summary"]["total_claims"],
            "claims_with_any_flags": coverage["summary"]["claims_for_review"],
            "selected_flag_claim_counts": selected_counts,
        },
        "coverage": coverage,
        "note": (
            "This is a read-only, single-snapshot policy check of recorded structural "
            "claim flags, not a scientific truth judgement or artifact integrity check. "
            "Exit 1 means the selected review policy triggered; exit 2 means unable "
            "to evaluate. Complete unfiltered D6 coverage is included. No artifacts "
            "or URLs were read, no manifests were written, and SQLite is not used. "
            "Run 'runevidence validate' separately before trusting evidence."
        ),
    }
    try:
        json.dumps(result, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise CoverageGateError("coverage-gate contains non-JSON recorded values") from exc
    return result


def format_coverage_gate(data: dict[str, Any]) -> str:
    """Stable, JSON-escaped terminal preview bounded to 16,384 characters."""
    max_field, max_output = 256, 16384

    def compact(value: Any) -> Any:
        if isinstance(value, str) and len(value) > max_field:
            return value[:max_field] + f"... [{len(value) - max_field} chars omitted; use --json]"
        if isinstance(value, list):
            return [compact(item) for item in value]
        if isinstance(value, dict):
            return {key: compact(item) for key, item in value.items()}
        return value

    def shown(value: Any) -> str:
        return json.dumps(compact(value), ensure_ascii=True, allow_nan=False, sort_keys=True)

    gate = data["gate"]
    lines = [
        f"Manifest: {shown(data['manifest'])}",
        f"Coverage gate: {'PASS' if gate['passed'] else 'TRIGGERED'} (exit {gate['exit_code']})",
        f"Selected review flags: {shown(gate['selected_flags'])}",
        f"Failing claims: {gate['failing_claims']} of {gate['total_claims']} recorded; "
        f"claims with any flags: {gate['claims_with_any_flags']}",
    ]
    for name, count in gate["selected_flag_claim_counts"].items():
        if count:
            lines.append(f"  {shown(name)}: {count} claims")
    for ident in gate["failing_claim_ids"][:25]:
        lines.append(f"  {shown(ident)}")
    if gate["failing_claims"] > 25:
        lines.append(f"  and {gate['failing_claims'] - 25} more failing claims (use --json)")
    lines.append("Review flags describe recorded structure, not experiment validity.")
    lines.append(_FOOTER)
    output = "\n".join(lines)
    if len(output) <= max_output:
        return output
    marker = "\n... text output truncated (use --json for complete coverage-gate).\n"
    budget = max_output - len(marker) - len(_FOOTER)
    prefix = output[:budget]
    boundary = prefix.rfind("\n")
    if boundary >= budget // 2:
        prefix = prefix[:boundary]
    return prefix.rstrip("\n") + marker + _FOOTER
