"""Recorded claim-to-provenance traversal, without reading artifacts or validating evidence.

Dependency direction is from the citing object to its recorded prerequisite.
The manifest's ``input_to`` relationship (artifact -> run) is deliberately
reversed for traversal (run -> input artifact) and annotated as such.
"""
from __future__ import annotations

from collections import defaultdict, deque
import json
from typing import Any

from .histogram_display import value_to_json
from .models import Manifest, RelationshipType as T, locator_to_json
from .run_claim import ClaimDetailsError, claim_details, _display_row, _json_safe


class LineageError(ValueError):
    """The selected claim or its recorded graph cannot be reported unambiguously."""


# (traversal-source kind, dependency kind), not necessarily manifest direction.
_ALLOWED: dict[T, set[tuple[str, str]]] = {
    T.result_of: {("result", "run")},
    T.reported_in: {("result", "artifact")},
    T.produced_by: {("artifact", "run")},
    T.input_to: {("run", "artifact")},
    T.derived_from: {("artifact", "artifact"), ("run", "run")},
}


def _index(manifest: Manifest) -> dict[str, tuple[str, Any]]:
    # claim_details() has already rejected global-ID collisions. Its validation
    # cannot be bypassed: claim_lineage always invokes it before this function.
    items = [(manifest.project.id, "project", manifest.project)]
    for kind, objects in (
        ("run", manifest.runs), ("artifact", manifest.artifacts),
        ("result", manifest.results), ("evidence", manifest.evidence),
    ):
        items.extend((obj.id, kind, obj) for obj in objects)
    return {identifier: (kind, obj) for identifier, kind, obj in items}


def _node(identifier: str, index: dict[str, tuple[str, Any]]) -> dict[str, Any]:
    if identifier not in index:
        return {"id": identifier, "kind": None, "status": "unresolved"}
    kind, obj = index[identifier]
    row: dict[str, Any] = {"id": identifier, "kind": kind, "status": "recorded"}
    if kind == "run":
        row["details"] = {"name": obj.name,
                          "started_at": obj.started_at.isoformat() if obj.started_at else None}
    elif kind == "artifact":
        row["details"] = {"path": obj.path, "kind": obj.kind, "run_id": obj.run_id,
                          "sha256": obj.sha256, "volatile": obj.volatile}
    elif kind == "result":
        row["details"] = {"name": obj.name, "value": value_to_json(obj.value),
                          "run_id": obj.run_id, "artifact_id": obj.artifact_id,
                          "locator": locator_to_json(obj.locator)}
    return row


def _edge_candidates(manifest: Manifest) -> dict[str, list[dict[str, Any]]]:
    # Dedupe one recorded semantic dependency across inline and typed spellings;
    # retain multiple typed notes and manifest-direction information.
    grouped: dict[tuple[str, str, T, str], dict[str, Any]] = {}

    def add(src: str, dep: str, relation: T, direction: str,
            via: str, note: str | None = None) -> None:
        key = (src, dep, relation, direction)
        record = grouped.setdefault(key, {"source": src, "dependency": dep,
                                          "type": relation.value,
                                          "manifest_direction": direction,
                                          "via": set(), "notes": set()})
        record["via"].add(via)
        if note is not None:
            record["notes"].add(note)

    for res in manifest.results:
        add(res.id, res.run_id, T.result_of, "forward", "inline")
        if res.artifact_id is not None:
            add(res.id, res.artifact_id, T.reported_in, "forward", "inline")
    for art in manifest.artifacts:
        if art.run_id is not None:
            add(art.id, art.run_id, T.produced_by, "forward", "inline")
    for rel in manifest.relationships:
        if rel.type not in _ALLOWED:
            continue
        if rel.type == T.input_to:
            add(rel.target, rel.source, rel.type, "reversed", "relationship", rel.note)
        else:
            add(rel.source, rel.target, rel.type, "forward", "relationship", rel.note)

    indexed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in grouped.values():
        record["via"] = [v for v in ("inline", "relationship") if v in record["via"]]
        notes = sorted(record.pop("notes"))
        if notes:
            record["relationship_notes"] = notes
        indexed[record["source"]].append(record)
    for items in indexed.values():
        items.sort(key=lambda r: (r["dependency"], r["type"], r["manifest_direction"]))
    return indexed


def _cycles(nodes: set[str], edges: list[dict[str, Any]]) -> tuple[list[list[str]], int]:
    """Iterative DFS: distinguish cycles from ordinary shared DAG ancestors."""
    outgoing: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        if edge["status"] == "recorded":
            outgoing[edge["source"]].add(edge["dependency"])
    colors: dict[str, int] = {}  # 1 on stack, 2 completed
    examples: list[list[str]] = []
    count = 0
    for start in sorted(nodes):
        if start in colors:
            continue
        stack_nodes = [start]
        indexes = {start: 0}
        colors[start] = 1
        pending = [iter(sorted(outgoing.get(start, ())))]
        while pending:
            candidate = next(pending[-1], None)
            if candidate is None:
                finished = stack_nodes.pop()
                pending.pop()
                indexes.pop(finished)
                colors[finished] = 2
                continue
            state = colors.get(candidate, 0)
            if state == 1:
                count += 1
                if len(examples) < 10:
                    examples.append(stack_nodes[indexes[candidate]:] + [candidate])
            elif state == 0:
                colors[candidate] = 1
                indexes[candidate] = len(stack_nodes)
                stack_nodes.append(candidate)
                pending.append(iter(sorted(outgoing.get(candidate, ()))))
    return examples, count


def claim_lineage(manifest: Manifest, claim_id: str, *, manifest_path: str) -> dict[str, Any]:
    """Traverse upstream recorded dependencies for both stances of one claim.

    Missing and incorrectly typed links are disclosed but never expanded.
    Nothing here inspects file contents, follows URLs, mutates a manifest or
    determines whether an asserted relationship is true.
    """
    try:
        direct = claim_details(manifest, claim_id, manifest_path=manifest_path)
    except ClaimDetailsError as exc:
        raise LineageError(str(exc)) from exc
    index = _index(manifest)
    roots = {stance: direct[stance] for stance in ("supports", "contradicts")}
    # A direct claim target in project/evidence is not a provenance dependency.
    seeds = sorted({row["id"] for rows in roots.values() for row in rows
                    if row["status"] == "recorded"})
    candidates = _edge_candidates(manifest)
    to_visit: deque[str] = deque(seeds)
    seen: set[str] = set()
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    while to_visit:
        identifier = to_visit.popleft()
        if identifier in seen:
            continue
        seen.add(identifier)
        nodes[identifier] = _node(identifier, index)
        for candidate in candidates.get(identifier, ()):
            target = candidate["dependency"]
            relation = T(candidate["type"])
            src_kind = index[identifier][0]
            dst_kind = index[target][0] if target in index else None
            if target not in index:
                status = "unresolved"
            elif (src_kind, dst_kind) not in _ALLOWED[relation]:
                status = "invalid_endpoint_kinds"
            else:
                status = "recorded"
            row = {**candidate, "status": status}
            edges.append(row)
            nodes.setdefault(target, _node(target, index))
            if status == "recorded" and target not in seen:
                to_visit.append(target)

    # Ordering is explicit and stable regardless of manifest relationship order.
    edges.sort(key=lambda e: (e["source"], e["dependency"], e["type"], e["manifest_direction"]))
    cycles, cycle_back_edges = _cycles(seen, edges)
    data: dict[str, Any] = {
        "manifest": manifest_path,
        "claim": direct["claim"],
        "roots": roots,
        "dual_stance_target_ids": direct["dual_stance_target_ids"],
        "nodes": [nodes[identifier] for identifier in sorted(nodes)],
        "edges": edges,
        "cycles": cycles,
        "summary": {
            "support_roots": len(roots["supports"]),
            "contradiction_roots": len(roots["contradicts"]),
            "nodes": len(nodes), "dependencies": len(edges),
            "unresolved_nodes": sum(n["status"] == "unresolved" for n in nodes.values()),
            "invalid_endpoint_edges": sum(e["status"] == "invalid_endpoint_kinds" for e in edges),
            "unresolved_dependency_edges": sum(e["status"] == "unresolved" for e in edges),
            "unsupported_direct_roots": sum(r["status"] == "unsupported_target_kind"
                                             for rows in roots.values() for r in rows),
            "unresolved_direct_roots": sum(r["status"] == "unresolved"
                                          for rows in roots.values() for r in rows),
            "cycle_back_edges": cycle_back_edges,
        },
        "note": (
            "Recorded manifest assertions only, not validated provenance or claim truth. "
            "Dependencies are followed upstream; input_to is traversed in reverse. "
            "Unresolved and invalid-kind links are shown but not followed. "
            "Cited runs do not automatically imply every produced artifact is supporting evidence. "
            "Run 'runevidence validate' separately before trusting evidence."
        ),
    }
    flagged: list[dict[str, str]] = []
    try:
        data = _json_safe(data, "", flagged)
        # D2's direct rows were already sanitized. Keep their exact flag paths,
        # relocated under D3's roots key, alongside flags on provenance nodes.
        flagged.extend({"path": "/roots" + item["path"], "kind": item["kind"]}
                       for item in direct.get("nonfinite_recorded_values", ()))
        if flagged:
            data["nonfinite_recorded_values"] = sorted(flagged, key=lambda x: (x["path"], x["kind"]))
        json.dumps(data, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise LineageError("lineage contains non-JSON recorded values") from exc
    return data


def format_claim_lineage(data: dict[str, Any]) -> str:
    """Bounded, terminal-safe explanation of the recorded dependency graph."""
    max_field, max_output = 256, 16384
    footer = "Recorded assertions only. Run 'runevidence validate' separately before trusting evidence."

    def compact(value: Any) -> Any:
        if isinstance(value, str) and len(value) > max_field:
            return value[:max_field] + f"... [{len(value) - max_field} chars omitted; use --json]"
        if isinstance(value, list):
            return [compact(item) for item in value]
        if isinstance(value, dict):
            return {key: compact(val) for key, val in value.items()}
        return value

    def shown(value: Any) -> str:
        return json.dumps(compact(value), ensure_ascii=True, sort_keys=True, allow_nan=False)

    lines = [f"Manifest: {shown(data['manifest'])}",
             f"Claim: {shown(data['claim']['id'])}: {shown(data['claim']['text'])}"]
    # Display copies cover both the direct roots and every upstream node; the
    # recorded data stays untouched for --json.
    roots = {stance: [_display_row(row) for row in data["roots"][stance]]
             for stance in ("supports", "contradicts")}
    nodes = [_display_row(node) for node in data["nodes"]]
    for stance in ("supports", "contradicts"):
        rows = roots[stance]
        lines.append(f"\n{stance.capitalize()} ({len(rows)} direct recorded associations):")
        for row in rows[:15]:
            lines.append(f"  {shown(row['id'])}: {row['status']} ({shown(row['kind'])})")
        if len(rows) > 15:
            lines.append(f"  and {len(rows) - 15} more roots (use --json)")
    lines.append(f"\nUpstream nodes: {data['summary']['nodes']}, dependencies: {data['summary']['dependencies']}")
    for node in nodes[:25]:
        detail = f" {shown(node['details'])}" if "details" in node else ""
        lines.append(f"  {shown(node['id'])}: {node['status']} ({shown(node['kind'])}){detail}")
    if len(nodes) > 25:
        lines.append(f"  and {len(nodes) - 25} more nodes (use --json)")
    lines.append("\nRecorded dependency edges:")
    for edge in data["edges"][:50]:
        suffix = " [input_to reversed]" if edge["manifest_direction"] == "reversed" else ""
        lines.append(f"  {shown(edge['source'])} -> {shown(edge['dependency'])} "
                     f"({edge['type']}, {edge['status']}){suffix}")
    if len(data["edges"]) > 50:
        lines.append(f"  and {len(data['edges']) - 50} more dependencies (use --json)")
    if data["summary"]["cycle_back_edges"]:
        lines.append(f"\nRecorded dependency cycles: {data['summary']['cycle_back_edges']} back edges")
        for cycle in data["cycles"]:
            lines.append(f"  {shown(cycle)}")
    lines.append(f"\nUnresolved dependency links: {data['summary']['unresolved_dependency_edges']}; "
                 f"invalid-kind dependency links: {data['summary']['invalid_endpoint_edges']}")
    if data.get("nonfinite_recorded_values"):
        lines.append(f"Nonfinite recorded values: {len(data['nonfinite_recorded_values'])} (use --json for paths)")
    lines.append(footer)
    rendered = "\n".join(lines)
    if len(rendered) <= max_output:
        return rendered
    marker = "\n... text output truncated (use --json for complete recorded lineage).\n"
    budget = max_output - len(marker) - len(footer)
    prefix = rendered[:budget]
    boundary = prefix.rfind("\n")
    if boundary >= budget // 2:
        prefix = prefix[:boundary]
    return prefix.rstrip("\n") + marker + footer
