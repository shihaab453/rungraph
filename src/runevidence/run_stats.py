"""Per-metric statistics over one recorded manifest (no artifact I/O)."""

from __future__ import annotations

import json
import math
from typing import Any, Optional

from .metric_stats import json_value, stats_from_rows
from .metric_steps import latest_names, split_step_name
from .models import Manifest


def stats_manifest(
    manifest: Manifest,
    *,
    manifest_path: str,
    metric: Optional[str] = None,
    latest: bool = False,
) -> dict[str, Any]:
    """Summarize recorded result rows without reading any artifact bytes."""
    rows: list[dict[str, Any]] = []
    for result in manifest.results:
        value = json_value(result.value)
        numeric = (
            isinstance(result.value, (int, float))
            and not isinstance(result.value, bool)
            and math.isfinite(result.value)
        )
        rows.append(
            {
                "member": "",
                "run": result.run_id,
                "result": result.id,
                "name": result.name,
                "value": value,
                "numeric": numeric,
            }
        )
    if latest:
        keep = latest_names(row["name"] for row in rows)
        rows = [row for row in rows if row["name"] in keep]
        for row in rows:
            row["name"] = split_step_name(row["name"])[0]
    if metric is not None:
        rows = [row for row in rows if row["name"] == metric]
    return {
        "format": "runevidence-stats-v1",
        "version": "0.1",
        "manifest": manifest_path,
        "metric": metric,
        "latest": latest,
        "metrics": stats_from_rows(rows),
        "exit_code": 0,
    }


def _shown(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def format_stats(data: dict[str, Any]) -> str:
    lines = [f"Manifest: {_shown(data['manifest'])}"]
    selected = data["metric"]
    if selected is not None:
        lines.append(f"Metric: {_shown(selected)}")
    if not data["metrics"]:
        lines.append("No recorded metrics.")
        return "\n".join(lines)
    lines.append("Metrics:")
    for entry in data["metrics"]:
        lines.append(
            f"  {_shown(entry['name'])}: {entry['rows']} rows, "
            f"{entry['numeric']} numeric, {entry['missing']} missing, "
            f"{entry['non_numeric']} non-numeric, {entry['ambiguous']} ambiguous"
        )
        if entry["numeric"]:
            lines.append(
                f"    min {_shown(entry['min'])}  max {_shown(entry['max'])}  "
                f"mean {_shown(entry['mean'])}  median {_shown(entry['median'])}  "
                f"stddev {_shown(entry['stddev'])}"
            )
    lines.append("Recorded values only. Artifact contents and locators were not checked.")
    return "\n".join(lines)
