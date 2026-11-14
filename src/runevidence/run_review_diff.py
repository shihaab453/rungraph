"""One read-only claim review queue combining recorded impact and structural change.

This is a *triage projection*, not evidence validation, a risk score, proof that
files changed, or a judgement about scientific truth. The D5 and D7 reports
retain their independent semantics and can still be requested separately.
"""
from __future__ import annotations

import json
from typing import Any

from .models import Manifest
from .run_claim import _json_safe
from .run_impact_diff import ImpactDiffError, compare_claim_impact
from .run_coverage_diff import CoverageDiffError, compare_claim_coverage


class ReviewDiffError(ValueError):
    """The snapshot comparison cannot be represented safely or unambiguously."""


_CATEGORIES = (
    "new_structural_flags", "claim_removed", "direct_claim_change",
    "dependency_impact", "structural_flags_cleared", "claim_added",
)


def _has_paths(impact_row: dict[str, Any]) -> bool:
    return any(
        snapshot is not None and (snapshot["supports"] or snapshot["contradicts"])
        for snapshot in (impact_row["before"], impact_row["after"])
    )


def _tier(categories: list[str]) -> int:
    """Presentation ordering only, not numerical confidence or scientific risk."""
    return min(_CATEGORIES.index(name) for name in categories)


def compare_claim_review(
    before: Manifest, after: Manifest, *, before_path: str, after_path: str,
    only_new_flags: bool = False,
) -> dict[str, Any]:
    """Union D5 record-impact candidates and D7 coverage-flag transitions.

    Show one object per claim ID. Removals are visible but NEVER improvements.
    Data comes from schema-loaded manifest assertions, without reading artifact
    bytes, following references, or writing either manifest.
    """
    try:
        impact = compare_claim_impact(before, after, before_path=before_path, after_path=after_path)
        coverage = compare_claim_coverage(before, after, before_path=before_path, after_path=after_path)
    except (ImpactDiffError, CoverageDiffError) as exc:
        raise ReviewDiffError(str(exc)) from exc

    impacts = {row["id"]: row for row in impact["claims"]}
    transitions = {row["id"]: row for row in coverage["claims"]}
    # Presence is based on actual evidence identity in each snapshot, not on
    # whether the source engines happened to include a row for that claim.
    before_claim_ids = {claim.id for claim in before.evidence}
    after_claim_ids = {claim.id for claim in after.evidence}
    all_candidates = []
    for identifier in impacts.keys() | transitions.keys():
        impact_row = impacts.get(identifier)
        transition = transitions.get(identifier)
        gained = transition["new_flags"] if transition else []
        cleared = transition["cleared_flags"] if transition else []
        categories = []
        if gained:
            categories.append("new_structural_flags")
        if identifier in before_claim_ids and identifier not in after_claim_ids:
            categories.append("claim_removed")
        if impact_row is not None and impact_row["direct_change"] is not None:
            categories.append("direct_claim_change")
        if impact_row is not None and _has_paths(impact_row):
            categories.append("dependency_impact")
        if cleared:
            categories.append("structural_flags_cleared")
        if identifier not in before_claim_ids and identifier in after_claim_ids:
            categories.append("claim_added")
        categories = [name for name in _CATEGORIES if name in categories]
        if not categories:
            # Every union member should have a tangible reason; avoid a silent
            # misleading row if upstream engine contracts ever change.
            raise ReviewDiffError(f"no review reason for candidate {identifier!r}")
        presence = ("both" if identifier in before_claim_ids and identifier in after_claim_ids
                    else "before_only" if identifier in before_claim_ids else "after_only")
        all_candidates.append({
            "id": identifier, "presence": presence,
            "review_categories": categories,
            "introduced_structural_flags": gained,
            "cleared_structural_flags": cleared,
            "record_impact": impact_row,
            "structural_transition": transition,
        })

    all_candidates.sort(key=lambda row: (_tier(row["review_categories"]), row["id"]))
    displayed = ([row for row in all_candidates if row["introduced_structural_flags"]]
                 if only_new_flags else all_candidates)
    category_counts = {category: sum(category in row["review_categories"] for row in all_candidates)
                       for category in _CATEGORIES}
    data: dict[str, Any] = {
        "before_manifest": before_path,
        "after_manifest": after_path,
        "claims": displayed,
        "summary": {
            "candidates_total": len(all_candidates),
            "displayed_claims": len(displayed),
            "only_new_flags": only_new_flags,
            "category_counts": category_counts,
            "record_impact": impact["summary"],
            "structural_coverage_diff": coverage["summary"],
        },
        "recorded_changes": impact["changes"],
        "note": (
            "Review queue is the union of recorded change-impact candidates and structural "
            "flag transitions. Categories are workflow labels, not severity, confidence or "
            "scientific risk. A claim removed from the second snapshot is not an improvement. "
            "A resolved structural flag does not prove artifact integrity. Paths are recorded "
            "potential dependencies, not verified executions. Compare exact claim IDs only; "
            "no rename inference. Manifest contents are not altered and artifact files, URLs, "
            "hashes and experimental truth were not checked. Run 'runevidence validate' "
            "separately on each snapshot."
        ),
    }
    flags: list[dict[str, str]] = []
    try:
        data = _json_safe(data, "", flags)
        if flags:
            data["nonfinite_recorded_values"] = flags
        json.dumps(data, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ReviewDiffError("review-diff contains non-JSON recorded values") from exc
    return data


def format_claim_review(data: dict[str, Any]) -> str:
    """Escaped terminal preview capped to 16,384 characters, full JSON separate."""
    max_field, max_output = 256, 16384
    footer = "Recorded review suggestions only. Run 'runevidence validate' separately."

    def compact(value: Any) -> Any:
        if isinstance(value, str) and len(value) > max_field:
            return value[:max_field] + f"... [{len(value) - max_field} chars omitted; use --json]"
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
        f"Claims suggested for review: {s['candidates_total']}; displayed: {s['displayed_claims']}"
        + (" (--only-new-flags)" if s["only_new_flags"] else ""),
        "Recorded review reasons (a claim can have multiple):",
    ]
    for reason, count in s["category_counts"].items():
        if count:
            lines.append(f"  {reason}: {count}")
    for row in data["claims"][:25]:
        lines.append(f"\nClaim {shown(row['id'])} ({row['presence']})")
        lines.append(f"  reasons: {shown(row['review_categories'])}")
        if row["introduced_structural_flags"]:
            lines.append(f"  introduced flags: {shown(row['introduced_structural_flags'])}")
        if row["cleared_structural_flags"]:
            lines.append(f"  cleared flags: {shown(row['cleared_structural_flags'])}")
        impact = row["record_impact"]
        if impact is not None:
            if impact["direct_change"] is not None:
                lines.append(f"  direct claim change: {shown(impact['direct_change'])}")
            for snapshot in ("before", "after"):
                side = impact[snapshot]
                if side is None:
                    continue
                for stance in ("supports", "contradicts"):
                    paths = side[stance]
                    if paths:
                        preview = paths[0]
                        nodes = preview["path"]
                        abbreviated = (nodes if len(nodes) <= 12 else
                                       nodes[:9] + [f"... [{len(nodes)-11} nodes omitted; use --json]"] + nodes[-2:])
                        lines.append(f"  {snapshot} {stance}: {len(paths)} impacted roots; "
                                     f"example {shown(abbreviated)} (changed {shown(preview['changed_id'])})")
    if len(data["claims"]) > 25:
        lines.append(f"\nand {len(data['claims'])-25} more claims (use --json)")
    if data.get("nonfinite_recorded_values"):
        lines.append(f"Nonfinite recorded values: {len(data['nonfinite_recorded_values'])} (use --json)")
    lines.append(footer)
    rendered = "\n".join(lines)
    if len(rendered) <= max_output:
        return rendered
    marker = "\n... text output truncated (use --json for complete recorded review).\n"
    budget = max_output - len(marker) - len(footer)
    prefix = rendered[:budget]
    boundary = prefix.rfind("\n")
    if boundary >= budget // 2:
        prefix = prefix[:boundary]
    return prefix.rstrip("\n") + marker + footer
