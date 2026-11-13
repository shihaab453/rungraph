"""Read-only manifest-to-manifest potential claim review impact.

This is a comparison of *recorded assertions*, not of artifact bytes, actual
experiment executions, or the truth or scientific validity of evidence.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import date, datetime
import json
import math
from typing import Any

from .models import Manifest, RelationshipType as T
from .run_claim import ClaimDetailsError, _index, _json_safe
from .run_lineage import _ALLOWED, _edge_candidates


class ImpactDiffError(ValueError):
    """The two manifest namespaces or their change report are ambiguous."""


def _frozen(value: Any) -> Any:
    """Stable typed equality, including NaN and JSON's bool/int distinction."""
    if hasattr(value, "model_dump"):
        return _frozen(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return ("dict", tuple(sorted((str(k), _frozen(v)) for k, v in value.items())))
    if isinstance(value, (tuple, list)):
        return ("list", tuple(_frozen(v) for v in value))
    if value is None:
        return ("null",)
    if type(value) is bool:
        return ("bool", value)
    if type(value) is int:
        return ("int", value)
    if type(value) is float:
        if math.isnan(value):
            return ("float", "NaN")
        if math.isinf(value):
            return ("float", "Infinity" if value > 0 else "-Infinity")
        return ("float", repr(value))
    if isinstance(value, (date, datetime)):
        return ("date", value.isoformat())
    if isinstance(value, str):
        return ("str", value)
    # A schema-loaded JSON manifest should never contain another kind.
    raise ImpactDiffError(f"unsupported recorded value type in comparison: {type(value).__name__}")


def _semantic_object(kind: str, obj: Any) -> dict[str, Any]:
    """Ignore harmless permutations/repetition in claim association shorthand."""
    data = obj.model_dump(mode="python")
    if kind == "evidence":
        for stance in ("supports", "contradicts"):
            data[stance] = sorted(set(data[stance]))
    return data


def _object_changes(before: dict[str, tuple[str, Any]],
                    after: dict[str, tuple[str, Any]]) -> tuple[list[dict[str, Any]], set[str]]:
    changes = []
    seeded = set()
    for ident in sorted(before.keys() | after.keys()):
        old, new = before.get(ident), after.get(ident)
        if old is None:
            change = "added"
        elif new is None:
            change = "removed"
        elif old[0] != new[0]:
            change = "retyped"
        elif _frozen(_semantic_object(old[0], old[1])) != _frozen(_semantic_object(new[0], new[1])):
            change = "modified"
        else:
            continue
        old_kind = old[0] if old else None
        new_kind = new[0] if new else None
        fields: list[str] = []
        if change == "modified":
            previous = _semantic_object(old_kind, old[1])
            current = _semantic_object(new_kind, new[1])
            fields = sorted(field for field in previous.keys() | current.keys()
                            if _frozen(previous.get(field)) != _frozen(current.get(field)))
        changes.append({"id": ident, "change": change, "before_kind": old_kind,
                        "after_kind": new_kind, "changed_fields": fields})
        if old_kind in ("run", "artifact", "result") or new_kind in ("run", "artifact", "result"):
            seeded.add(ident)
    return changes, seeded


def _relationship_keys(manifest: Manifest) -> set[tuple[str, str, str, str | None]]:
    return {(r.type.value, r.source, r.target, r.note) for r in manifest.relationships}


def _relationship_sort_key(rel: tuple[str, str, str, str | None]) -> tuple[str, str, str, bool, str]:
    """Order nullable notes without comparing None to str (None before strings)."""
    typ, src, target, note = rel
    return (typ, src, target, note is not None, note if note is not None else "")


def _relation_status(rel: tuple[str, str, str, str | None],
                     index: dict[str, tuple[str, Any]]) -> tuple[str, str | None]:
    name, src, target, _note = rel
    typ = T(name)
    if src not in index or target not in index:
        return "unresolved", None
    kinds = (index[src][0], index[target][0])
    if typ in (T.supports, T.contradicts):
        valid = kinds[0] == "evidence" and kinds[1] in ("run", "artifact", "result")
        return ("recorded" if valid else "invalid_endpoint_kinds"), (src if valid else None)
    if typ not in _ALLOWED:
        return "unsupported_type", None
    if typ == T.input_to:
        reversed_kinds = (kinds[1], kinds[0])
        valid = reversed_kinds in _ALLOWED[typ]
        return ("recorded" if valid else "invalid_endpoint_kinds"), (target if valid else None)
    valid = kinds in _ALLOWED[typ]
    return ("recorded" if valid else "invalid_endpoint_kinds"), (src if valid else None)


def _relationship_changes(before_m: Manifest, after_m: Manifest,
                          before_idx: dict[str, tuple[str, Any]],
                          after_idx: dict[str, tuple[str, Any]]) -> tuple[list[dict[str, Any]], set[str], set[str], set[str]]:
    previous = _relationship_keys(before_m)
    current = _relationship_keys(after_m)
    rows = []
    before_seeds: set[str] = set()
    after_seeds: set[str] = set()
    direct_claim_ids: set[str] = set()
    for operation, keys, idx, seeds in (
        ("removed", sorted(previous - current, key=_relationship_sort_key), before_idx, before_seeds),
        ("added", sorted(current - previous, key=_relationship_sort_key), after_idx, after_seeds),
    ):
        for key in keys:
            status, anchor = _relation_status(key, idx)
            typ, src, target, note = key
            rows.append({"change": operation, "type": typ, "source": src,
                         "target": target, "note": note, "status": status})
            # Even an invalid newly asserted claim association is a direct
            # review-worthy change, though it never propagates through the graph.
            if typ in (T.supports.value, T.contradicts.value) and src in idx and idx[src][0] == "evidence":
                direct_claim_ids.add(src)
            elif status == "recorded" and anchor is not None:
                seeds.add(anchor)
    rows.sort(key=lambda e: (e["type"], e["source"], e["target"],
                             e["note"] is not None, e["note"] or "", e["change"]))
    return rows, before_seeds, after_seeds, direct_claim_ids


def _reached(manifest: Manifest, idx: dict[str, tuple[str, Any]],
             seeds: set[str]) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """One shortest deterministic route from each reachable object to a changed seed."""
    reverse: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for src, candidates in _edge_candidates(manifest).items():
        if src not in idx:
            continue
        for edge in candidates:
            dependency = edge["dependency"]
            if dependency not in idx:
                continue
            if (idx[src][0], idx[dependency][0]) in _ALLOWED[T(edge["type"])]:
                reverse[dependency].append(edge)
    for edges in reverse.values():
        edges.sort(key=lambda e: (e["source"], e["type"], e["manifest_direction"]))
    start = sorted(seed for seed in seeds if seed in idx and idx[seed][0] in ("run", "artifact", "result"))
    queue = deque(start)
    cause = {ident: ident for ident in start}
    next_edge: dict[str, dict[str, Any]] = {}
    while queue:
        node = queue.popleft()
        for edge in reverse.get(node, ()):
            src = edge["source"]
            if src in cause:
                continue
            cause[src] = cause[node]
            next_edge[src] = edge
            queue.append(src)
    return cause, next_edge


def _claim_memberships(manifest: Manifest) -> dict[str, dict[str, set[str]]]:
    assoc = {c.id: {"supports": set(c.supports), "contradicts": set(c.contradicts)}
             for c in manifest.evidence}
    for rel in manifest.relationships:
        if rel.source in assoc and rel.type in (T.supports, T.contradicts):
            assoc[rel.source][rel.type.value].add(rel.target)
    return assoc


def _snapshot_claims(manifest: Manifest, cause: dict[str, str],
                     next_edge: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    memberships = _claim_memberships(manifest)
    result: dict[str, dict[str, Any]] = {}
    for claim in manifest.evidence:
        row: dict[str, Any] = {"text": claim.claim, "source": claim.source,
                               "supports": [], "contradicts": []}
        for stance in ("supports", "contradicts"):
            for root in sorted(memberships[claim.id][stance] & cause.keys()):
                node = root
                path = [root]
                while node in next_edge:
                    edge = next_edge[node]
                    node = edge["dependency"]
                    path.append(node)
                row[stance].append({"root_id": root, "changed_id": cause[root],
                                    "path": path})
        result[claim.id] = row
    return result


def compare_claim_impact(before_m: Manifest, after_m: Manifest, *,
                         before_path: str, after_path: str) -> dict[str, Any]:
    """Explain potentially affected claims from changes in two recorded manifests."""
    try:
        before_idx, after_idx = _index(before_m), _index(after_m)
    except ClaimDetailsError as exc:
        raise ImpactDiffError(str(exc)) from exc
    object_rows, object_seeds = _object_changes(before_idx, after_idx)
    relation_rows, prior_relation_seeds, later_relation_seeds, direct_association_changes = (
        _relationship_changes(before_m, after_m, before_idx, after_idx))
    before_cause, before_edges = _reached(before_m, before_idx, object_seeds | prior_relation_seeds)
    after_cause, after_edges = _reached(after_m, after_idx, object_seeds | later_relation_seeds)
    before_claims = _snapshot_claims(before_m, before_cause, before_edges)
    after_claims = _snapshot_claims(after_m, after_cause, after_edges)
    directly_changed = {
        row["id"]: row["change"] for row in object_rows
        if row["before_kind"] == "evidence" or row["after_kind"] == "evidence"
    }
    directly_changed.update({ident: "association_changed" for ident in direct_association_changes
                             if ident not in directly_changed})
    impacted = []
    for ident in sorted(before_claims.keys() | after_claims.keys()):
        old, new = before_claims.get(ident), after_claims.get(ident)
        if ident not in directly_changed and not any(
            row and (row["supports"] or row["contradicts"]) for row in (old, new)
        ):
            continue
        presence = "both" if old and new else ("before_only" if old else "after_only")
        impacted.append({"id": ident, "presence": presence,
                         "direct_change": directly_changed.get(ident),
                         "before": old, "after": new})
    data: dict[str, Any] = {
        "before_manifest": before_path, "after_manifest": after_path,
        "changes": {"objects": object_rows, "relationships": relation_rows},
        "claims": impacted,
        "summary": {
            "object_changes": len(object_rows), "relationship_changes": len(relation_rows),
            "direct_claim_changes": len(directly_changed),
            "claims_for_review": len(impacted),
            "before_reachable_objects": len(before_cause),
            "after_reachable_objects": len(after_cause),
            "before_only_claims": sum(c["presence"] == "before_only" for c in impacted),
            "after_only_claims": sum(c["presence"] == "after_only" for c in impacted),
        },
        "note": (
            "This compares recorded manifest assertions, not filesystem contents or actual file changes. "
            "Review candidates are potential, not proof of invalid results or claims. "
            "Changed object records, provenance edges, and direct claim associations are included. "
            "One deterministic shortest path to a changed recorded object/dependency anchor is shown "
            "per affected root; alternative causes and paths can exist. "
            "Only resolved, correctly typed provenance edges propagate. "
            "Project-only changes do not imply effects on individual claims. "
            "Run 'runevidence validate' separately on each manifest before trusting evidence."
        ),
    }
    flagged: list[dict[str, str]] = []
    try:
        data = _json_safe(data, "", flagged)
        if flagged:
            data["nonfinite_recorded_values"] = flagged
        json.dumps(data, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ImpactDiffError("impact-diff output contains non-JSON recorded values") from exc
    return data


def format_impact_diff(data: dict[str, Any]) -> str:
    """Terminal-safe bounded report, with full machine-readable JSON available."""
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

    s = data["summary"]
    lines = [f"Before: {shown(data['before_manifest'])}", f"After: {shown(data['after_manifest'])}",
             f"Recorded changes: {s['object_changes']} objects, {s['relationship_changes']} relationships",
             f"Claims needing review: {s['claims_for_review']} (direct changes: {s['direct_claim_changes']})"]
    for change in data["changes"]["objects"][:15]:
        names = (" [" + ", ".join(change["changed_fields"]) + "]") if change["changed_fields"] else ""
        lines.append(f"  {shown(change['id'])}: {change['change']}{names}")
    if len(data["changes"]["objects"]) > 15:
        lines.append(f"  and {len(data['changes']['objects'])-15} more changed objects (use --json)")
    for c in data["claims"][:25]:
        lines.append(f"\nClaim {shown(c['id'])}: {c['presence']}" +
                     (f" [direct {c['direct_change']}]" if c["direct_change"] else ""))
        for snapshot in ("before", "after"):
            row = c[snapshot]
            if row is None:
                continue
            lines.append(f"  {snapshot} statement: {shown(row['text'])}")
            for stance in ("supports", "contradicts"):
                paths = row[stance]
                if paths:
                    lines.append(f"  {snapshot} {stance}: {len(paths)} affected roots")
                for p in paths[:3]:
                    nodes = p["path"]
                    preview = nodes if len(nodes) <= 12 else (
                        nodes[:9] + [f"... [{len(nodes)-11} nodes omitted; use --json]"] + nodes[-2:])
                    lines.append(f"    root {shown(p['root_id'])}, changed {shown(p['changed_id'])}: {shown(preview)}")
                if len(paths) > 3:
                    lines.append(f"    and {len(paths)-3} more affected roots (use --json)")
    if len(data["claims"]) > 25:
        lines.append(f"\nand {len(data['claims'])-25} more claims (use --json)")
    if data.get("nonfinite_recorded_values"):
        lines.append(f"Nonfinite recorded values: {len(data['nonfinite_recorded_values'])} (use --json)")
    lines.append(footer)
    rendered = "\n".join(lines)
    if len(rendered) <= max_output:
        return rendered
    marker = "\n... text output truncated (use --json for complete recorded comparison).\n"
    budget = max_output - len(marker) - len(footer)
    prefix = rendered[:budget]
    boundary = prefix.rfind("\n")
    if boundary >= budget // 2:
        prefix = prefix[:boundary]
    return prefix.rstrip("\n") + marker + footer
