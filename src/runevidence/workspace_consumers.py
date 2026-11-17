"""Read-only report, impact and comparison projections for a workspace."""

from __future__ import annotations

import math
import re
from typing import Any, Optional

from pydantic import BaseModel

from .findings import Severity
from .models import Manifest
from .workspace import LoadedWorkspace
from .workspace_graph import ClaimState, Graph, claim_support_closure, node_id


def claim_closure(loaded: LoadedWorkspace, graph: Graph, state: ClaimState) -> set[str]:
    if state.member is not None:
        start = node_id("evidence", state.member, state.id)
        relations = {"supports"}
    else:
        start = node_id("claim", loaded.document.id, state.id)
        relations = {"supports", "contradicts"}
    return claim_support_closure(graph, start, relations)


def report_data(
    loaded: LoadedWorkspace,
    graph: Graph,
    states: list[ClaimState],
    checks: dict[str, Any],
    *,
    directory_artifacts: bool = False,
    baseline_root=None,
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
        "baseline": baseline_diff(loaded, baseline_root) if baseline_root is not None else None,
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


def _json_value(value: Any) -> Any:
    """A result value as JSON data.

    Scalar results pass through; structured 0.8-0.11 values are Pydantic
    models and must be dumped before any consumer prints them.
    """

    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


_STEP_NAME = re.compile(r"^(?P<base>.+) @ (?P<step>-?\d+)(?: #\d+)?$")


def _step_name(name: str) -> tuple[str, Optional[int]]:
    match = _STEP_NAME.fullmatch(name)
    if match is None:
        return name, None
    return match.group("base"), int(match.group("step"))


def _latest_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the highest ``<name> @ <step>`` value per member, run and base name.

    Rows without a step suffix are kept only when the group has no stepped row.
    Ties at the highest step are all kept, so the matrix still reports them as
    ambiguous instead of choosing silently.
    """
    grouped: dict[tuple[str, str, str], list[tuple[Optional[int], dict[str, Any]]]] = {}
    for row in rows:
        base, step = _step_name(row["name"])
        grouped.setdefault((row["member"], row["run"], base), []).append((step, row))
    keep: set[tuple[str, str, str, str]] = set()
    for (member, run, base), items in grouped.items():
        steps = [step for step, _ in items if step is not None]
        if steps:
            highest = max(steps)
            chosen = [row for step, row in items if step == highest]
        else:
            chosen = [row for _, row in items]
        for row in chosen:
            keep.add((member, run, base, row["result"]))
    selected: list[dict[str, Any]] = []
    for row in rows:
        base, step = _step_name(row["name"])
        if (row["member"], row["run"], base, row["result"]) not in keep:
            continue
        latest = dict(row)
        latest["name"] = base
        latest["result_name"] = row["name"]
        if step is not None:
            latest["step"] = step
        selected.append(latest)
    return selected


def value_rows(loaded: LoadedWorkspace, *, latest: bool = False) -> list[dict[str, Any]]:
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
                    "value": _json_value(result.value),
                    "numeric": isinstance(result.value, (int, float))
                    and not isinstance(result.value, bool)
                    and math.isfinite(result.value),
                }
            )
    return _latest_rows(rows) if latest else rows


def compare_data(
    loaded: LoadedWorkspace,
    metric: str,
    run: Optional[str] = None,
    *,
    latest: bool = False,
) -> dict[str, Any]:
    rows = [row for row in value_rows(loaded, latest=latest) if row["name"] == metric]
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
    pairs = []
    if len(out) <= 50:
        from .run_comparability import compare_paths

        for index, left in enumerate(out):
            for right in out[index + 1 :]:
                left_member = loaded.members.get(left["member"])
                right_member = loaded.members.get(right["member"])
                if left_member is None or right_member is None:
                    continue
                try:
                    outcome = compare_paths(
                        left_member.manifest_path,
                        right_member.manifest_path,
                        baseline_result_id=left["result"],
                        target_result_id=right["result"],
                    )
                except Exception:
                    status = "unavailable"
                else:
                    status = {0: "comparable", 1: "not-comparable"}.get(outcome.exit_code, "unavailable")
                pairs.append(
                    {
                        "baseline": {"member": left["member"], "result": left["result"]},
                        "target": {"member": right["member"], "result": right["result"]},
                        "conditions": status,
                    }
                )
    else:
        pairs = [{"conditions": "unavailable: too-many-pairs"}]
    return {
        "format": "rungraph-compare",
        "version": "0.1",
        "metric": metric,
        "latest": latest,
        "rows": out,
        "missing": missing,
        "non_numeric": nonnumeric,
        "comparability": pairs,
        "exit_code": 0,
    }


def matrix_data(loaded: LoadedWorkspace, *, latest: bool = False) -> dict[str, Any]:
    rows = value_rows(loaded, latest=latest)
    metrics = sorted({row["name"] for row in rows})
    cells = []
    missing = 0
    nonnumeric = 0
    ambiguous = 0
    grouped: dict[tuple[str, str], dict[str, list[Any]]] = {}
    for row in rows:
        grouped.setdefault((row["member"], row["run"]), {}).setdefault(row["name"], []).append(row)
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
                row = entries[0]
                value = row["value"]
                numeric = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                if not numeric:
                    nonnumeric += 1
                cell = {"member": member, "run": run, "metric": metric, "state": "value" if numeric else "non-numeric", "value": value}
                if latest and row.get("step") is not None:
                    cell["step"] = row["step"]
                cells.append(cell)
    return {
        "format": "rungraph-matrix",
        "version": "0.1",
        "metrics": metrics,
        "latest": latest,
        "cells": cells,
        "missing": missing,
        "non_numeric": nonnumeric,
        "ambiguous": ambiguous,
        "exit_code": 0,
    }


def leaderboard_data(
    loaded: LoadedWorkspace,
    metric: str,
    direction: str,
    top: int = 10,
    *,
    latest: bool = False,
) -> dict[str, Any]:
    rows = [row for row in value_rows(loaded, latest=latest) if row["name"] == metric]
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
        "latest": latest,
        "rows": ranked,
        "excluded": {"missing": sum(1 for row in rows if row["value"] is None), "non_numeric": sum(1 for row in rows if row["value"] is not None and not row["numeric"])},
        "conditions": "available",
        "exit_code": 0,
    }


def _projection(manifest: Manifest) -> list[dict[str, Any]]:
    items = [("project", manifest.project)]
    items += [("run", item) for item in manifest.runs]
    items += [("artifact", item) for item in manifest.artifacts]
    items += [("result", item) for item in manifest.results]
    items += [("evidence", item) for item in manifest.evidence]
    return [
        {"kind": kind, "id": obj.id, "data": obj.model_dump(mode="json")}
        for kind, obj in sorted(items, key=lambda item: (item[0], item[1].id))
    ]


def _by_key(items: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(item["kind"], item["id"]): item["data"] for item in items}


def baseline_diff(loaded: LoadedWorkspace, baseline_root) -> dict[str, Any]:
    from pathlib import Path

    from .workspace import WORKSPACE_FILENAME, WorkspaceError, load_workspace

    baseline_root = Path(baseline_root)
    base_loaded = None
    base_doc = None
    try:
        if (baseline_root / WORKSPACE_FILENAME).exists():
            base_loaded = load_workspace(baseline_root)
            base_doc = base_loaded.document
    except WorkspaceError:
        base_loaded = None
        base_doc = None
    base_by_id = {member.id: member for member in (base_doc.members if base_doc else [])}
    base_by_path = {member.path: member for member in (base_doc.members if base_doc else [])}
    members = []
    for member_id, item in loaded.members.items():
        baseline_member = base_by_id.get(member_id) or base_by_path.get(item.member.path)
        if baseline_member is None:
            members.append({"id": member_id, "status": "added"})
            continue
        status = "unchanged"
        if baseline_member.id != member_id:
            status = "re-keyed"
        elif baseline_member.path != item.member.path:
            status = "re-pathed"
        current = _projection(item.manifest) if item.manifest else []
        previous_member = (
            base_loaded.members.get(baseline_member.id) if base_loaded is not None else None
        )
        if previous_member is None or previous_member.manifest is None:
            members.append({"id": member_id, "status": "baseline-missing"})
            continue
        previous_manifest = previous_member
        previous = _projection(previous_manifest.manifest)
        before = _by_key(previous)
        after = _by_key(current)
        members.append(
            {
                "id": member_id,
                "status": status,
                "schema_changed": previous_manifest.manifest.schema_version
                != (item.manifest.schema_version if item.manifest else None),
                "added": [key for key in after if key not in before],
                "removed": [key for key in before if key not in after],
                "changed": [
                    key for key in after if key in before and before[key] != after[key]
                ],
            }
        )
    removed = [
        member.id for member in (base_doc.members if base_doc else []) if member.id not in loaded.members
    ]
    claims_before = {claim.id: claim.model_dump(mode="json") for claim in (base_doc.claims if base_doc else [])}
    claims_after = {claim.id: claim.model_dump(mode="json") for claim in loaded.document.claims}
    relationships_before = {
        item.id: item.model_dump(mode="json") for item in (base_doc.relationships if base_doc else [])
    }
    relationships_after = {
        item.id: item.model_dump(mode="json") for item in loaded.document.relationships
    }
    return {
        "members": members,
        "removed_members": removed,
        "claims": {
            "added": sorted(set(claims_after) - set(claims_before)),
            "removed": sorted(set(claims_before) - set(claims_after)),
            "changed": sorted(key for key in claims_after if key in claims_before and claims_after[key] != claims_before[key]),
        },
        "relationships": {
            "added": sorted(set(relationships_after) - set(relationships_before)),
            "removed": sorted(set(relationships_before) - set(relationships_after)),
            "changed": sorted(key for key in relationships_after if key in relationships_before and relationships_after[key] != relationships_before[key]),
        },
    }


def _json_number(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _safe_mean(values: list[Any]) -> Any:
    from fractions import Fraction

    return _json_number(float(sum(Fraction(value) for value in values) / len(values)))


def _safe_median(values: list[Any]) -> Any:
    from fractions import Fraction

    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return _json_number(float((Fraction(ordered[middle - 1]) + Fraction(ordered[middle])) / 2))


def _safe_pstdev(values: list[Any]) -> Any:
    from decimal import Decimal, localcontext
    from fractions import Fraction

    exact = [Fraction(value) for value in values]
    mean = sum(exact) / len(exact)
    variance = sum((item - mean) ** 2 for item in exact) / len(exact)
    if variance == 0:
        return 0.0
    # Decimal sqrt keeps the exponent range of the exact rational variance;
    # converting the tiny normalized variance to float first would underflow.
    with localcontext() as context:
        context.prec = 80
        root = (
            Decimal(variance.numerator) / Decimal(variance.denominator)
        ).sqrt()
    if not root.is_finite():
        value = sys.float_info.max
    else:
        try:
            value = float(root)
        except OverflowError:
            # The population standard deviation of finite binary64 values
            # cannot exceed the largest finite float; Decimal can round a
            # value within half an ulp above it.
            value = sys.float_info.max
    return _json_number(value)


def stats_data(loaded: LoadedWorkspace, metric: Optional[str] = None) -> dict[str, Any]:
    """Per-metric count and distribution statistics.

    Every recorded result contributes one row. ``numeric`` counts finite
    numbers; ``missing`` counts recorded nulls; ``non_numeric`` counts strings,
    booleans and structured values; ``ambiguous`` counts a ``(member, run)``
    pair that recorded the same metric more than once, because one aggregate
    cannot represent it. Distribution fields cover the numeric rows and are
    null when there are none. ``stddev`` is the population standard deviation.
    """

    rows = value_rows(loaded)
    if metric is not None:
        rows = [row for row in rows if row["name"] == metric]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["name"], []).append(row)
    metrics: list[dict[str, Any]] = []
    for name in sorted(grouped):
        items = grouped[name]
        numeric_values = [row["value"] for row in items if row["numeric"]]
        cells: dict[tuple[str, str], int] = {}
        for row in items:
            key = (row["member"], row["run"])
            cells[key] = cells.get(key, 0) + 1
        entry: dict[str, Any] = {
            "name": name,
            "rows": len(items),
            "numeric": len(numeric_values),
            "missing": sum(1 for row in items if row["value"] is None),
            "non_numeric": sum(
                1 for row in items if row["value"] is not None and not row["numeric"]
            ),
            "ambiguous": sum(1 for count in cells.values() if count > 1),
            "min": None,
            "max": None,
            "mean": None,
            "median": None,
            "stddev": None,
        }
        if numeric_values:
            entry["min"] = min(numeric_values)
            entry["max"] = max(numeric_values)
            entry["mean"] = _safe_mean(numeric_values)
            entry["median"] = _safe_median(numeric_values)
            entry["stddev"] = _safe_pstdev(numeric_values)
        metrics.append(entry)
    return {
        "format": "rungraph-stats",
        "version": "0.1",
        "metric": metric,
        "metrics": metrics,
        "exit_code": 0,
    }
