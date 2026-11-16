"""Read-only report, impact and comparison projections for a workspace."""

from __future__ import annotations

import math
from typing import Any, Optional

from .findings import Severity
from .models import Manifest
from .workspace import LoadedWorkspace
from .workspace_graph import ClaimState, Edge, Graph, Node, _closure, node_id


def _state_targets(loaded: LoadedWorkspace, graph: Graph, state: ClaimState) -> list[str]:
    targets: list[str] = []
    if state.member is not None:
        item = loaded.members[state.member]
        if item.manifest is None:
            return targets
        for evidence in item.manifest.evidence:
            if evidence.id != state.id:
                continue
            for local in list(evidence.supports) + list(evidence.contradicts):
                for kind in ("result", "artifact", "run", "evidence"):
                    candidate = node_id(kind, state.member, local)
                    if candidate in graph.nodes:
                        targets.append(candidate)
                        break
        return targets
    for claim in loaded.document.claims:
        if claim.id != state.id:
            continue
        for ref in claim.supports + claim.contradicts:
            if ref.claim is not None:
                targets.append(node_id("claim", loaded.document.id, ref.claim))
            else:
                for kind in ("result", "evidence", "artifact", "run"):
                    candidate = node_id(kind, ref.member, ref.id)
                    if candidate in graph.nodes:
                        targets.append(candidate)
                        break
    return targets


def claim_closure(loaded: LoadedWorkspace, graph: Graph, state: ClaimState) -> set[str]:
    result: set[str] = set()
    for target in _state_targets(loaded, graph, state):
        result |= _closure(graph, target)
    return result


def report_data(
    loaded: LoadedWorkspace,
    graph: Graph,
    states: list[ClaimState],
    checks: dict[str, Any],
    *,
    directory_artifacts: bool = False,
) -> dict[str, Any]:
    members = []
    for member_id, item in loaded.members.items():
        findings = checks["findings"].get(member_id, [])
        counts = {"error": 0, "warning": 0}
        for finding in findings:
            if finding.severity == Severity.error:
                counts["error"] += 1
            else:
                counts["warning"] += 1
        members.append(
            {
                "id": member_id,
                "path": item.member.path,
                "schema_version": item.manifest.schema_version if item.manifest else None,
                "load_error": item.error,
                "counts": counts,
                "findings": [
                    {
                        "code": finding.code.value,
                        "severity": finding.severity.value,
                        "subject": finding.subject,
                        "path": finding.path,
                        "message": finding.message,
                    }
                    for finding in findings
                ],
            }
        )
    claims = [
        {
            "id": state.id,
            "member": state.member,
            "text": state.text,
            "labels": state.labels,
            "label": state.label,
        }
        for state in states
    ]
    impact = []
    for member_id, findings in checks["findings"].items():
        for finding in findings:
            if finding.severity != Severity.error or finding.subject is None:
                continue
            node = None
            for kind in ("evidence", "result", "artifact", "run", "project"):
                candidate = node_id(kind, member_id, finding.subject)
                if candidate in graph.nodes:
                    node = candidate
                    break
            if node is None:
                continue
            affected = [state.id for state in states if node in claim_closure(loaded, graph, state)]
            impact.append(
                {
                    "member": member_id,
                    "subject": finding.subject,
                    "node": node,
                    "code": finding.code.value,
                    "claims": affected,
                }
            )
    return {
        "format": "rungraph-report",
        "version": "0.1",
        "check_policy": {"directory_artifacts": directory_artifacts},
        "baseline": None,
        "members": members,
        "claims": claims,
        "impact": impact,
        "exit_code": 1 if any(m["counts"]["error"] or m["load_error"] for m in members) else 0,
    }


def impact_data(
    loaded: LoadedWorkspace,
    graph: Graph,
    states: list[ClaimState],
    node: str,
) -> dict[str, Any]:
    if node not in graph.nodes:
        return {
            "format": "rungraph-impact",
            "version": "0.1",
            "source": node,
            "claims": [],
            "unresolved": [node],
            "exit_code": 2,
        }
    affected = [
        {"id": state.id, "member": state.member, "labels": state.labels}
        for state in states
        if node in claim_closure(loaded, graph, state)
    ]
    return {
        "format": "rungraph-impact",
        "version": "0.1",
        "source": node,
        "claims": affected,
        "unresolved": [],
        "exit_code": 0,
    }


def value_rows(loaded: LoadedWorkspace) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for member_id, item in loaded.members.items():
        if item.manifest is None:
            continue
        for result in item.manifest.results:
            rows.append(
                {
                    "member": member_id,
                    "run": result.run_id,
                    "result": result.id,
                    "name": result.name,
                    "value": result.value,
                    "numeric": isinstance(result.value, (int, float))
                    and not isinstance(result.value, bool)
                    and math.isfinite(result.value),
                }
            )
    return rows


def compare_data(loaded: LoadedWorkspace, metric: str, run: Optional[str] = None) -> dict[str, Any]:
    rows = [row for row in value_rows(loaded) if row["name"] == metric]
    if run is not None:
        rows = [row for row in rows if f"{row['member']}#{row['run']}" == run]
    missing = 0
    nonnumeric = 0
    out = []
    for row in rows:
        if row["value"] is None:
            missing += 1
        elif not row["numeric"]:
            nonnumeric += 1
        out.append(row)
    return {
        "format": "rungraph-compare",
        "version": "0.1",
        "metric": metric,
        "rows": out,
        "missing": missing,
        "non_numeric": nonnumeric,
        "comparability": "unavailable: schema or conditions",
        "exit_code": 0,
    }


def matrix_data(loaded: LoadedWorkspace) -> dict[str, Any]:
    rows = value_rows(loaded)
    metrics = sorted({row["name"] for row in rows})
    cells = []
    missing = 0
    nonnumeric = 0
    ambiguous = 0
    grouped: dict[tuple[str, str], dict[str, list[Any]]] = {}
    for row in rows:
        grouped.setdefault((row["member"], row["run"]), {}).setdefault(row["name"], []).append(row["value"])
    for (member, run), values in sorted(grouped.items()):
        for metric in metrics:
            entries = values.get(metric)
            if entries is None:
                missing += 1
                cells.append({"member": member, "run": run, "metric": metric, "state": "missing"})
            elif len(entries) > 1:
                ambiguous += 1
                cells.append({"member": member, "run": run, "metric": metric, "state": "ambiguous"})
            else:
                value = entries[0]
                numeric = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                if not numeric:
                    nonnumeric += 1
                cells.append({"member": member, "run": run, "metric": metric, "state": "value" if numeric else "non-numeric", "value": value})
    return {
        "format": "rungraph-matrix",
        "version": "0.1",
        "metrics": metrics,
        "cells": cells,
        "missing": missing,
        "non_numeric": nonnumeric,
        "ambiguous": ambiguous,
        "exit_code": 0,
    }


def leaderboard_data(loaded: LoadedWorkspace, metric: str, direction: str, top: int = 10) -> dict[str, Any]:
    rows = [row for row in value_rows(loaded) if row["name"] == metric]
    eligible = [row for row in rows if row["numeric"]]
    eligible.sort(key=lambda row: row["value"], reverse=direction == "max")
    ranked = []
    rank = 0
    previous: Any = object()
    for index, row in enumerate(eligible, 1):
        if row["value"] != previous:
            rank = index
            previous = row["value"]
        if len(ranked) >= top:
            break
        ranked.append({**row, "rank": rank})
    return {
        "format": "rungraph-leaderboard",
        "version": "0.1",
        "metric": metric,
        "direction": direction,
        "rows": ranked,
        "excluded": {"missing": sum(1 for row in rows if row["value"] is None), "non_numeric": sum(1 for row in rows if row["value"] is not None and not row["numeric"])},
        "conditions": "unavailable",
        "exit_code": 0,
    }
