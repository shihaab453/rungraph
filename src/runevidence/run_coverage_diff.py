"""Read-only comparison of structural claim review flags across manifest snapshots.

This is a *coverage-diff*, not a comparison of artifact bytes or scientific truth.
Claim identities are matched only by exact recorded IDs; no rename inference.
"""
from __future__ import annotations

import json
from typing import Any

from .models import Manifest
from .run_claim import _json_safe
from .run_coverage import CoverageError, claim_coverage


class CoverageDiffError(ValueError):
    """One snapshot's recorded namespace or coverage report is invalid."""


def compare_claim_coverage(
    before: Manifest, after: Manifest, *, before_path: str, after_path: str,
    only_regressions: bool = False,
) -> dict[str, Any]:
    """Compare D6 review flags, preserving missing-versus-cleared distinction.

    Added flagged claims and surviving claims with newly introduced flags count
    as potential regressions. A removed claim is NOT a cleared review flag.
    Flag-neutral text/source/metric changes belong in ``impact-diff``.
    """
    try:
        old = claim_coverage(before, manifest_path=before_path)
        new = claim_coverage(after, manifest_path=after_path)
    except CoverageError as exc:
        raise CoverageDiffError(str(exc)) from exc

    old_by_id = {row["id"]: row for row in old["claims"]}
    new_by_id = {row["id"]: row for row in new["claims"]}
    candidates = []
    changed_claims = 0
    added_claims = removed_claims = 0
    introduced_claims = improved_claims = new_flagged_claims = 0
    introduced_count = {k: 0 for k in old["summary"]["flag_counts"]}
    cleared_count = {k: 0 for k in old["summary"]["flag_counts"]}
    for identifier in sorted(old_by_id.keys() | new_by_id.keys()):
        previous = old_by_id.get(identifier)
        current = new_by_id.get(identifier)
        earlier = set(previous["review_flags"]) if previous is not None else set()
        later = set(current["review_flags"]) if current is not None else set()
        if previous is None:
            status = "added"
            added_claims += 1
            gained = [k for k in introduced_count if k in later]
            cleared = []
            if gained:
                new_flagged_claims += 1
        elif current is None:
            status = "removed"
            removed_claims += 1
            gained = []
            cleared = []  # disappearance is never a structural improvement
        else:
            gained = [k for k in introduced_count if k in later - earlier]
            cleared = [k for k in cleared_count if k in earlier - later]
            status = "flags_changed" if gained or cleared else "unchanged"
            if gained:
                introduced_claims += 1
            if cleared:
                improved_claims += 1
        for flag in gained:
            introduced_count[flag] += 1
        for flag in cleared:
            cleared_count[flag] += 1
        if status == "unchanged":
            continue
        changed_claims += 1
        if not only_regressions or gained:
            candidates.append({
                "id": identifier,
                "status": status,
                "new_flags": gained,
                "cleared_flags": cleared,
                "before": previous,
                "after": current,
            })

    result: dict[str, Any] = {
        "before_manifest": before_path,
        "after_manifest": after_path,
        "claims": candidates,
        "summary": {
            "before_total_claims": old["summary"]["total_claims"],
            "after_total_claims": new["summary"]["total_claims"],
            "before_claims_for_review": old["summary"]["claims_for_review"],
            "after_claims_for_review": new["summary"]["claims_for_review"],
            "added_claims": added_claims,
            "removed_claims": removed_claims,
            "changed_claims": changed_claims,
            "surviving_claims_with_new_flags": introduced_claims,
            "surviving_claims_with_cleared_flags": improved_claims,
            "new_claims_with_flags": new_flagged_claims,
            "displayed_claims": len(candidates),
            "only_regressions": only_regressions,
            "before_flag_counts": old["summary"]["flag_counts"],
            "after_flag_counts": new["summary"]["flag_counts"],
            "introduced_flag_counts": introduced_count,
            "cleared_flag_counts": cleared_count,
        },
        "note": (
            "Read-only structural comparison of recorded manifest associations and "
            "provenance, not validation or a scientific verdict. Exact claim IDs "
            "are treated as stable identities; renames are not inferred. Removed "
            "claims do not count as flags cleared. Added flagged claims and surviving "
            "claims with new flags are possible regressions, not proof of invalidity. "
            "Flag-neutral record changes are not reported; use 'runevidence impact-diff' "
            "for recorded object and relationship change impact. Neither manifest, "
            "artifact files, hashes nor external sources were changed or checked."
        ),
    }
    flags: list[dict[str, str]] = []
    try:
        result = _json_safe(result, "", flags)
        if flags:
            result["nonfinite_recorded_values"] = flags
        json.dumps(result, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise CoverageDiffError("coverage-diff contains non-JSON recorded values") from exc
    return result


def format_coverage_diff(data: dict[str, Any]) -> str:
    """Escaped, deterministically bounded terminal view; JSON stays complete."""
    max_field, max_output = 256, 16384
    footer = "Recorded structural review flags only. Run 'runevidence validate' separately."

    def compact(value: Any) -> Any:
        if isinstance(value, str) and len(value) > max_field:
            return value[:max_field] + f"... [{len(value)-max_field} chars omitted; use --json]"
        if isinstance(value, list):
            return [compact(v) for v in value]
        if isinstance(value, dict):
            return {k: compact(v) for k, v in value.items()}
        return value

    def shown(value: Any) -> str:
        return json.dumps(compact(value), ensure_ascii=True, allow_nan=False, sort_keys=True)

    s = data["summary"]
    lines = [
        f"Before: {shown(data['before_manifest'])}",
        f"After: {shown(data['after_manifest'])}",
        f"Recorded claims: {s['before_total_claims']} -> {s['after_total_claims']}",
        f"Claims with review flags: {s['before_claims_for_review']} -> {s['after_claims_for_review']}",
        f"Changes: {s['changed_claims']} claims ({s['added_claims']} added, "
        f"{s['removed_claims']} removed); shown: {s['displayed_claims']}"
        + (" (--only-regressions)" if s["only_regressions"] else ""),
        f"New flags: {sum(s['introduced_flag_counts'].values())} on surviving/new claims; "
        f"cleared: {sum(s['cleared_flag_counts'].values())} on surviving claims",
    ]
    for name, n in s["introduced_flag_counts"].items():
        if n:
            lines.append(f"  +{name}: {n}")
    for name, n in s["cleared_flag_counts"].items():
        if n:
            lines.append(f"  -{name}: {n}")
    for claim in data["claims"][:30]:
        lines.append(f"\nClaim {shown(claim['id'])}: {shown(claim['status'])}")
        if claim["new_flags"]:
            lines.append(f"  introduced review flags: {shown(claim['new_flags'])}")
        if claim["cleared_flags"]:
            lines.append(f"  cleared review flags: {shown(claim['cleared_flags'])}")
        for snapshot in ("before", "after"):
            row = claim[snapshot]
            if row is not None:
                lines.append(f"  {snapshot}: {shown(row['text'])}; flags {shown(row['review_flags'])}")
    if len(data["claims"]) > 30:
        lines.append(f"\n... and {len(data['claims'])-30} more changes (use --json)")
    lines.append(footer)
    rendered = "\n".join(lines)
    if len(rendered) <= max_output:
        return rendered
    marker = "\n... text output truncated (use --json for complete recorded coverage-diff).\n"
    budget = max_output - len(marker) - len(footer)
    prefix = rendered[:budget]
    boundary = prefix.rfind("\n")
    if boundary >= budget // 2:
        prefix = prefix[:boundary]
    return prefix.rstrip("\n") + marker + footer
