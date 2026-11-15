"""Read-only cross-run table of metric values recorded in one manifest.

No artifact contents, locators, filesystem writers or validators are consulted.
A missing result is distinct from a result whose recorded value is null.
"""

from __future__ import annotations

import json
import math
from typing import Any, Sequence

from .models import Manifest


class MatrixError(ValueError):
    """Invalid selection or ambiguous recorded metric information."""


def _selection(values: Sequence[str] | None, *, label: str) -> list[str] | None:
    if values is None:
        return None
    chosen: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value:
            raise MatrixError(f"{label} selectors must be nonempty strings")
        if value in seen:
            raise MatrixError(f"duplicate {label} selector {value!r}")
        seen.add(value)
        chosen.append(value)
    return chosen


def metric_matrix(
    manifest: Manifest,
    *,
    manifest_path: str,
    selected_runs: Sequence[str] | None = None,
    selected_metrics: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Collect selected run/metric cells without reading or changing artifacts.

    Default run and metric selection is lexicographically sorted. Explicit
    selectors preserve the requested order and refuse duplicates or unknown
    run IDs. Explicit metric names may be absent in every run, which gives
    visible missing cells rather than a silent empty response.
    """
    runs: dict[str, Any] = {}
    for run in manifest.runs:
        if run.id in runs:
            raise MatrixError(f"duplicate run ID {run.id!r}; refusing ambiguous matrix")
        runs[run.id] = run

    requested_runs = _selection(selected_runs, label="run")
    requested_metrics = _selection(selected_metrics, label="metric")
    run_ids = sorted(runs) if requested_runs is None else requested_runs
    for run_id in run_ids:
        if run_id not in runs:
            raise MatrixError(f"run {run_id!r} not found")
    selected_set = set(run_ids)
    metric_filter = None if requested_metrics is None else set(requested_metrics)
    recorded: dict[str, dict[str, Any]] = {run_id: {} for run_id in run_ids}
    for result in manifest.results:
        if result.run_id not in selected_set:
            continue
        if metric_filter is not None and result.name not in metric_filter:
            continue
        bucket = recorded[result.run_id]
        if result.name in bucket:
            raise MatrixError(
                f"run {result.run_id!r} has multiple results named {result.name!r}; refusing ambiguous matrix"
            )
        bucket[result.name] = result.value

    metric_names = (sorted({name for bucket in recorded.values() for name in bucket})
                    if requested_metrics is None else requested_metrics)
    rows: list[dict[str, Any]] = []
    missing_count = nonfinite_count = 0
    for run_id in run_ids:
        values: list[dict[str, Any]] = []
        for name in metric_names:
            if name not in recorded[run_id]:
                values.append({"metric": name, "status": "missing"})
                missing_count += 1
                continue
            value = recorded[run_id][name]
            if type(value) is float and not math.isfinite(value):
                values.append({"metric": name, "status": "nonfinite"})
                nonfinite_count += 1
                continue
            values.append({"metric": name, "status": "recorded", "value": value})
        rows.append({"run_id": run_id, "run_name": runs[run_id].name, "values": values})

    data = {
        "manifest": manifest_path,
        "metrics": metric_names,
        "runs": rows,
        "summary": {
            "runs": len(rows),
            "metrics": len(metric_names),
            "missing_cells": missing_count,
            "nonfinite_cells": nonfinite_count,
        },
        "note": "Recorded manifest values only. Artifact bytes and result locators were not verified.",
    }
    text_metrics = sorted(
        result.name
        for result in manifest.results
        if getattr(getattr(result, "locator", None), "kind", None)
        == "tensorboard-string"
    )
    if text_metrics:
        data["text_metrics"] = text_metrics
    try:
        json.dumps(data, ensure_ascii=True, allow_nan=False)
    except (ValueError, TypeError, OverflowError) as exc:
        raise MatrixError("matrix contains non-JSON recorded values") from exc
    return data


def _shown(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True)


def _display_value(value: Any, *, text: bool) -> str:
    if text and isinstance(value, str) and len(value) > 120:
        return json.dumps(value[:120], ensure_ascii=True, allow_nan=False) + "..."
    return _shown(value)


def format_metric_matrix(data: dict[str, Any]) -> str:
    """Render each run and its cells on separate, control-safe text lines."""
    summary = data["summary"]
    lines = [
        f"Manifest: {_shown(data['manifest'])}",
        f"Metric matrix: {summary['runs']} runs x {summary['metrics']} metrics",
    ]
    if not data["runs"]:
        lines.append("No runs selected.")
    elif not data["metrics"]:
        lines.append("No recorded metrics for the selected runs.")
    text_metrics = set(data.get("text_metrics", ()))
    for row in data["runs"]:
        lines.append(f"  {row['run_id']} ({_shown(row['run_name'])}):")
        for cell in row["values"]:
            if cell["status"] == "recorded":
                value = _display_value(
                    cell["value"], text=cell["metric"] in text_metrics
                )
            else:
                value = f"({cell['status']})"
            lines.append(f"    {_shown(cell['metric'])}: {value}")
    lines.append(
        f"Missing cells: {summary['missing_cells']}. Nonfinite cells: {summary['nonfinite_cells']}."
    )
    lines.append("Recorded values only. Run 'runevidence validate' separately to check artifact evidence.")
    return "\n".join(lines)
