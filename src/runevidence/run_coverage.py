"""Read-only, structural claim-review queue for a recorded manifest.

Coverage reports *recorded* associations and upstream provenance gaps, not
scientific validity, artifact integrity, or filesystem changes. For validation
of files and values use ``runevidence validate`` separately.
"""
from __future__ import annotations

import json
from typing import Any

from .models import Manifest
from .run_claim import _json_safe
from .run_lineage import LineageError, claim_lineage


class CoverageError(ValueError):
    """A manifest namespace or recorded output cannot be safely summarized."""


_FLAGS = (
    "no_resolved_support",
    "contradictions_recorded",
    "dual_stance",
    "unresolved_direct_reference",
    "unsupported_direct_kind",
    "unresolved_provenance",
    "invalid_provenance_kind",
    "provenance_cycle",
)


def claim_coverage(manifest: Manifest, *, manifest_path: str, only_flagged: bool = False) -> dict[str, Any]:
    """Summarize every claim's structural review flags without artifact I/O.

    Return 0-style informational data even when flags exist: flags invite human
    inspection, not an assertion that evidence validation failed. Identifiers
    share one namespace and are rejected on any collision, including in an
    empty-evidence manifest.
    """
    # Explicitly reject duplicate IDs even if the manifest has zero claims.
    # D3 calls D2's global index when individual claims are evaluated, but an
    # empty queue must still refuse ambiguous recorded object namespaces.
    from .run_claim import ClaimDetailsError, _index
    try:
        _index(manifest)
    except ClaimDetailsError as exc:
        raise CoverageError(str(exc)) from exc

    rows: list[dict[str, Any]] = []
    counts = {name: 0 for name in _FLAGS}
    for claim in sorted(manifest.evidence, key=lambda item: item.id):
        try:
            graph = claim_lineage(manifest, claim.id, manifest_path=manifest_path)
        except LineageError as exc:
            raise CoverageError(str(exc)) from exc
        support = graph["roots"]["supports"]
        contradiction = graph["roots"]["contradicts"]
        summary = graph["summary"]
        resolved_support = sum(x["status"] == "recorded" for x in support)
        resolved_contradiction = sum(x["status"] == "recorded" for x in contradiction)
        flags = []
        if resolved_support == 0:
            flags.append("no_resolved_support")
        if resolved_contradiction:
            flags.append("contradictions_recorded")
        if summary["cycle_back_edges"]:
            # Place flag in canonical _FLAGS order below, not incidental order.
            flags.append("provenance_cycle")
        if graph["dual_stance_target_ids"]:
            flags.append("dual_stance")
        if summary["unresolved_direct_roots"]:
            flags.append("unresolved_direct_reference")
        if summary["unsupported_direct_roots"]:
            flags.append("unsupported_direct_kind")
        if summary["unresolved_dependency_edges"]:
            flags.append("unresolved_provenance")
        if summary["invalid_endpoint_edges"]:
            flags.append("invalid_provenance_kind")
        flags = [flag for flag in _FLAGS if flag in flags]
        for flag in flags:
            counts[flag] += 1
        row = {
            "id": claim.id,
            "text": claim.claim,
            "source": claim.source,
            "review_flags": flags,
            "resolved_support_roots": resolved_support,
            "resolved_contradiction_roots": resolved_contradiction,
            "recorded_support_roots": len(support),
            "recorded_contradiction_roots": len(contradiction),
            "unresolved_direct_root_ids": sorted({x["id"] for x in support + contradiction
                                                     if x["status"] == "unresolved"}),
            "unsupported_direct_root_ids": sorted({x["id"] for x in support + contradiction
                                                      if x["status"] == "unsupported_target_kind"}),
            "dual_stance_target_ids": graph["dual_stance_target_ids"],
            "upstream_nodes": summary["nodes"],
            "upstream_dependency_edges": summary["dependencies"],
            "unresolved_dependency_edges": summary["unresolved_dependency_edges"],
            "invalid_dependency_kind_edges": summary["invalid_endpoint_edges"],
            "cycle_back_edges": summary["cycle_back_edges"],
            "cycles": graph["cycles"],
        }
        rows.append(row)
    attention = sum(bool(row["review_flags"]) for row in rows)
    displayed = [r for r in rows if r["review_flags"]] if only_flagged else rows
    data: dict[str, Any] = {
        "manifest": manifest_path,
        "claims": displayed,
        "summary": {
            "total_claims": len(rows),
            "claims_for_review": attention,
            "claims_without_flags": len(rows) - attention,
            "displayed_claims": len(displayed),
            "only_flagged": only_flagged,
            "flag_counts": counts,
        },
        "note": (
            "Structural recorded-association review only. Flags are prompts for human review, "
            "not proof of false claims or missing files. 'contradictions_recorded' does not "
            "assert that the contradiction is valid or that the claim is false. "
            "No files, URLs, artifact hashes or metric values were checked. "
            "Run 'runevidence validate' separately for integrity findings."
        ),
    }
    flagged: list[dict[str, str]] = []
    try:
        data = _json_safe(data, "", flagged)
        if flagged:
            data["nonfinite_recorded_values"] = flagged
        json.dumps(data, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise CoverageError("coverage output contains non-JSON recorded values") from exc
    return data


def format_claim_coverage(data: dict[str, Any]) -> str:
    """Deterministic terminal-safe preview with an explicit 16 Ki-character cap."""
    max_field, max_output = 256, 16384
    footer = "Recorded assertions only. Run 'runevidence validate' separately before trusting evidence."

    def compact(value: Any) -> Any:
        if isinstance(value, str) and len(value) > max_field:
            return value[:max_field] + f"... [{len(value)-max_field} chars omitted; use --json]"
        if isinstance(value, list):
            return [compact(item) for item in value]
        if isinstance(value, dict):
            return {k: compact(v) for k, v in value.items()}
        return value

    def shown(value: Any) -> str:
        return json.dumps(compact(value), ensure_ascii=True, allow_nan=False, sort_keys=True)

    summary = data["summary"]
    lines = [
        f"Manifest: {shown(data['manifest'])}",
        f"Recorded claims: {summary['total_claims']} (review flags on {summary['claims_for_review']}, "
        f"no flags on {summary['claims_without_flags']})",
        f"Shown: {summary['displayed_claims']}" + (" (--only-flagged)" if summary["only_flagged"] else ""),
        "Review flag counts (claims, not occurrences):",
    ]
    for key in _FLAGS:
        if summary["flag_counts"][key]:
            lines.append(f"  {key}: {summary['flag_counts'][key]}")
    for row in data["claims"][:30]:
        lines.extend([
            f"\nClaim {shown(row['id'])}: {shown(row['text'])}",
            f"  supporting roots: {row['resolved_support_roots']} resolved/typed / {row['recorded_support_roots']} recorded; "
            f"contradicting roots: {row['resolved_contradiction_roots']} resolved/typed / {row['recorded_contradiction_roots']} recorded",
            f"  review flags: {shown(row['review_flags'])}",
        ])
        if row["unresolved_direct_root_ids"]:
            lines.append(f"  unresolved direct IDs: {shown(row['unresolved_direct_root_ids'][:5])}")
        if row["unsupported_direct_root_ids"]:
            lines.append(f"  invalid-kind direct IDs: {shown(row['unsupported_direct_root_ids'][:5])}")
        if row["cycle_back_edges"]:
            lines.append(f"  recorded cycles: {row['cycle_back_edges']} back edges (use --json)")
    if len(data["claims"]) > 30:
        lines.append(f"\n... and {len(data['claims'])-30} more claims (use --json)")
    lines.append(footer)
    rendered = "\n".join(lines)
    if len(rendered) <= max_output:
        return rendered
    marker = "\n... text output truncated (use --json for complete recorded coverage).\n"
    budget = max_output - len(marker) - len(footer)
    prefix = rendered[:budget]
    boundary = prefix.rfind("\n")
    if boundary >= budget // 2:
        prefix = prefix[:boundary]
    return prefix.rstrip("\n") + marker + footer
