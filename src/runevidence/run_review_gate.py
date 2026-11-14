"""Policy-controlled, read-only CI gate for recorded claim-review changes.

This gate evaluates manifest assertions, not artifact contents or scientific
validity. An exit 1 is a user-selected review policy breach, not validation.
"""
from __future__ import annotations

import json
from typing import Any

from .models import Manifest
from .run_review_diff import ReviewDiffError, compare_claim_review


class ReviewGateError(ValueError):
    """A gate policy or recorded snapshot comparison cannot be evaluated."""


_POLICIES = ("new-flags", "new-flags-or-removals", "any-review")
_FOOTER = "Recorded review policy only. Run 'runevidence validate' separately."


def evaluate_review_gate(
    before: Manifest, after: Manifest, *, before_path: str, after_path: str,
    policy: str = "new-flags",
) -> dict[str, Any]:
    """Evaluate one explicit policy against an unfiltered D8 review projection.

    0: the requested review condition was not met.
    1: the requested condition was met (not an assertion of scientific failure).
    Invalid inputs/policies raise ReviewGateError so CLI returns 2.
    """
    if policy not in _POLICIES:
        raise ReviewGateError(f"unknown review-gate policy {policy!r}; choose: {', '.join(_POLICIES)}")
    try:
        report = compare_claim_review(
            before, after, before_path=before_path, after_path=after_path,
            only_new_flags=False,
        )
    except ReviewDiffError as exc:
        raise ReviewGateError(str(exc)) from exc

    candidates = report["claims"]
    new_flags = [row["id"] for row in candidates if row["introduced_structural_flags"]]
    removals = [row["id"] for row in candidates if row["presence"] == "before_only"]
    if policy == "new-flags":
        failures = new_flags
    elif policy == "new-flags-or-removals":
        failures = sorted(set(new_flags) | set(removals))
    else:
        failures = [row["id"] for row in candidates]
    # JSON is complete, with the full underlying D8 report unfiltered.
    data = {
        "before_manifest": before_path,
        "after_manifest": after_path,
        "gate": {
            "policy": policy,
            "passed": not failures,
            "exit_code": 1 if failures else 0,
            "failing_claims": len(failures),
            "failing_claim_ids": failures,
            "new_flag_claims": len(new_flags),
            "removed_claims": len(removals),
            "review_candidates": len(candidates),
        },
        "review": report,
        "note": (
            "Read-only user-selected gate over recorded review candidates. "
            "Exit 1 means review policy triggered, not that an experiment failed, "
            "artifact bytes changed, evidence is invalid, or claims are false. "
            "Exit 2 is reserved for invalid input or an unevaluable comparison. "
            "Structural flags are prompts for review; removed claims never count "
            "as cleared flags. The complete unfiltered D8 report is included. "
            "No artifact reads, manifest writes, or persistence activation."
        ),
    }
    try:
        json.dumps(data, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ReviewGateError("review-gate output contains non-JSON recorded values") from exc
    return data


def format_review_gate(data: dict[str, Any]) -> str:
    """Escaped stable terminal preview of one gate, bounded at 16,384 chars."""
    maximum, field_limit = 16384, 256
    gate = data["gate"]

    def shown(value: Any) -> str:
        def compact(item: Any) -> Any:
            if isinstance(item, str) and len(item) > field_limit:
                return item[:field_limit] + f"... [{len(item) - field_limit} chars omitted; use --json]"
            if isinstance(item, list):
                return [compact(v) for v in item]
            if isinstance(item, dict):
                return {k: compact(v) for k, v in item.items()}
            return item
        return json.dumps(compact(value), ensure_ascii=True, allow_nan=False, sort_keys=True)

    lines = [
        f"Before: {shown(data['before_manifest'])}",
        f"After: {shown(data['after_manifest'])}",
        f"Review policy: {shown(gate['policy'])}",
        f"Gate: {'PASS' if gate['passed'] else 'TRIGGERED'} (exit {gate['exit_code']})",
        f"Failing claims: {gate['failing_claims']} of {gate['review_candidates']} review candidates; "
        f"new-flag claims: {gate['new_flag_claims']}; removed claims: {gate['removed_claims']}",
    ]
    for ident in gate["failing_claim_ids"][:25]:
        lines.append(f"  {shown(ident)}")
    if len(gate["failing_claim_ids"]) > 25:
        lines.append(f"  and {len(gate['failing_claim_ids']) - 25} more failures (use --json)")
    lines.append("Gate results describe recorded review policy, not experiment validity.")
    lines.append(_FOOTER)
    output = "\n".join(lines)
    if len(output) <= maximum:
        return output
    marker = "\n... text output truncated (use --json for complete gate and review).\n"
    budget = maximum - len(marker) - len(_FOOTER)
    prefix = output[:budget]
    boundary = prefix.rfind("\n")
    if boundary >= budget // 2:
        prefix = prefix[:boundary]
    return prefix.rstrip("\n") + marker + _FOOTER
