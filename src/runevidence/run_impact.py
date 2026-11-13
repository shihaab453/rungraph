"""Read-only reverse provenance impact projection for a recorded object.

A potential impact is a claim whose supporting or contradicting root has a
recorded dependency path to the queried run, result or artifact. This is not
validation, a damage assessment, or a statement about scientific truth.
"""
from __future__ import annotations

from collections import defaultdict, deque
import json
from typing import Any

from .models import Manifest, RelationshipType as T
from .run_claim import ClaimDetailsError, _index, _json_safe
from .run_lineage import _ALLOWED, _edge_candidates, _node


class ImpactError(ValueError):
    """The selected object or manifest namespace cannot be assessed."""


def claim_impact(manifest: Manifest, object_id: str, *, manifest_path: str) -> dict[str, Any]:
    """Find claims with recorded support/contradiction paths depending on an ID.

    Uses only schema-loaded manifest fields. No file reads, external requests,
    validators, or persistent changes are performed.
    """
    try:
        index = _index(manifest)
    except ClaimDetailsError as exc:
        raise ImpactError(str(exc)) from exc
    if object_id not in index or index[object_id][0] not in ("run", "artifact", "result"):
        raise ImpactError(f"impact target {object_id!r} not found as a run, artifact or result")

    # Every candidate has the same dependency direction as D3 (object ->
    # prerequisite). Only correctly typed, resolved edges may propagate impact.
    reverse: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ignored_missing = ignored_kinds = 0
    for source, candidates in _edge_candidates(manifest).items():
        for edge in candidates:
            dependency = edge["dependency"]
            relation = T(edge["type"])
            if source not in index or dependency not in index:
                ignored_missing += 1
                continue
            if (index[source][0], index[dependency][0]) not in _ALLOWED[relation]:
                ignored_kinds += 1
                continue
            reverse[dependency].append(edge)

    for dependents in reverse.values():
        dependents.sort(key=lambda e: (e["source"], e["type"], e["manifest_direction"]))

    # Reverse BFS obtains one deterministic shortest *example* path per root,
    # rather than materializing every potentially exponential DAG path.
    queue: deque[str] = deque([object_id])
    next_edge: dict[str, dict[str, Any]] = {}
    reached = {object_id}
    while queue:
        dependency = queue.popleft()
        for edge in reverse.get(dependency, ()):
            source = edge["source"]
            if source not in reached:
                reached.add(source)
                next_edge[source] = edge
                queue.append(source)

    assoc: dict[str, dict[str, set[str]]] = {
        claim.id: {"supports": set(claim.supports), "contradicts": set(claim.contradicts)}
        for claim in manifest.evidence
    }
    for relation in manifest.relationships:
        if relation.source not in assoc:
            continue
        if relation.type == T.supports:
            assoc[relation.source]["supports"].add(relation.target)
        elif relation.type == T.contradicts:
            assoc[relation.source]["contradicts"].add(relation.target)

    def example_path(root: str) -> dict[str, Any]:
        node = root
        path = [root]
        steps: list[dict[str, Any]] = []
        while node != object_id:
            edge = next_edge[node]
            steps.append({"source": edge["source"], "dependency": edge["dependency"],
                          "type": edge["type"], "manifest_direction": edge["manifest_direction"],
                          "via": edge["via"]})
            node = edge["dependency"]
            path.append(node)
        return {"root_id": root, "path": path, "steps": steps}

    affected = []
    for claim in sorted(manifest.evidence, key=lambda item: item.id):
        memberships = assoc[claim.id]
        support = [example_path(root) for root in sorted(memberships["supports"] & reached)]
        contra = [example_path(root) for root in sorted(memberships["contradicts"] & reached)]
        if support or contra:
            affected.append({"id": claim.id, "text": claim.claim, "source": claim.source,
                             "supports": support, "contradicts": contra})

    data: dict[str, Any] = {
        "manifest": manifest_path,
        "target": _node(object_id, index),
        "claims": affected,
        "summary": {
            "impacted_claims": len(affected),
            "supporting_claims": sum(bool(c["supports"]) for c in affected),
            "contradicting_claims": sum(bool(c["contradicts"]) for c in affected),
            "both_stances_claims": sum(bool(c["supports"]) and bool(c["contradicts"]) for c in affected),
            "reachable_objects": len(reached),
            "ignored_unresolved_links": ignored_missing,
            "ignored_invalid_kind_links": ignored_kinds,
        },
        "note": (
            "Potential impact from recorded manifest dependencies only. "
            "This does not establish that the target changed, that claims are false, "
            "or that any artifact, URL or referenced value was checked. "
            "Each direct root shows one deterministic shortest example path; other "
            "paths may exist. Only resolved, correctly typed dependencies propagate. "
            "Run 'runevidence validate' separately before trusting evidence."
        ),
    }
    flags: list[dict[str, str]] = []
    try:
        data = _json_safe(data, "", flags)
        if flags:
            data["nonfinite_recorded_values"] = flags
        json.dumps(data, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ImpactError("impact output contains non-JSON recorded values") from exc
    return data


def format_claim_impact(data: dict[str, Any]) -> str:
    """Terminal-safe, explicitly truncated display. JSON stays complete."""
    max_field, max_output = 256, 16384
    footer = "Recorded assertions only. Run 'runevidence validate' separately before trusting evidence."

    def compact(value: Any) -> Any:
        if isinstance(value, str) and len(value) > max_field:
            return value[:max_field] + f"... [{len(value)-max_field} chars omitted; use --json]"
        if isinstance(value, dict):
            return {k: compact(v) for k, v in value.items()}
        if isinstance(value, list):
            return [compact(v) for v in value]
        return value

    def shown(value: Any) -> str:
        return json.dumps(compact(value), ensure_ascii=True, allow_nan=False, sort_keys=True)

    summary = data["summary"]
    lines = [f"Manifest: {shown(data['manifest'])}",
             f"Object: {shown(data['target']['id'])} ({data['target']['kind']})",
             f"Potentially affected recorded claims: {summary['impacted_claims']} "
             f"(support {summary['supporting_claims']}, contradiction {summary['contradicting_claims']}, "
             f"both {summary['both_stances_claims']})"]
    for claim in data["claims"][:20]:
        lines.append(f"\nClaim {shown(claim['id'])}: {shown(claim['text'])}")
        for stance in ("supports", "contradicts"):
            paths = claim[stance]
            if not paths:
                continue
            lines.append(f"  {stance}: {len(paths)} impacted direct root(s)")
            for entry in paths[:3]:
                nodes = entry["path"]
                if len(nodes) > 12:
                    preview = nodes[:9] + [f"... [{len(nodes)-11} intermediate nodes omitted; use --json]"] + nodes[-2:]
                else:
                    preview = nodes
                lines.append(f"    root {shown(entry['root_id'])}: {shown(preview)}")
            if len(paths) > 3:
                lines.append(f"    and {len(paths)-3} more roots (use --json)")
    if len(data["claims"]) > 20:
        lines.append(f"\nand {len(data['claims'])-20} more claims (use --json)")
    lines.append(f"\nExcluded malformed dependency links: "
                 f"{summary['ignored_unresolved_links']} unresolved, "
                 f"{summary['ignored_invalid_kind_links']} invalid kind")
    if data.get("nonfinite_recorded_values"):
        lines.append(f"Nonfinite recorded values: {len(data['nonfinite_recorded_values'])} (use --json)")
    lines.append(footer)
    rendered = "\n".join(lines)
    if len(rendered) <= max_output:
        return rendered
    marker = "\n... text output truncated (use --json for complete recorded impact).\n"
    budget = max_output - len(marker) - len(footer)
    prefix = rendered[:budget]
    boundary = prefix.rfind("\n")
    if boundary >= budget // 2:
        prefix = prefix[:boundary]
    return prefix.rstrip("\n") + marker + footer
