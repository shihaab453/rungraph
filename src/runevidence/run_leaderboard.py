"""Rank recorded run results by an explicitly selected numeric metric.

No artifact, locator, writer, or validator I/O is performed. Rankings describe
only values already stored in the supplied in-memory manifest, not model quality.
"""

from __future__ import annotations

import json
import math
from typing import Any, Literal

from .histogram_display import value_to_json
from .metric_steps import latest_names, split_step_name
from .models import Manifest


class LeaderboardError(ValueError):
    """Ambiguous metric or run data prevents an honest recorded-value ranking."""


def _safe_number(value: object) -> bool:
    return (type(value) is int or
            (type(value) is float and math.isfinite(value)))


def rank_runs(
    manifest: Manifest, metric: str, direction: Literal["max", "min"], *,
    top: int = 10, manifest_path: str, latest: bool = False,
) -> dict[str, Any]:
    """Return deterministic competition ranks and explicit missing/excluded runs.

    Rank 1 is first, ties share a rank, and the next rank skips tied positions.
    Exact Python int/float ordering avoids lossy conversion near 2**53.
    """
    if not isinstance(metric, str) or not metric:
        raise LeaderboardError("metric must be a nonempty name")
    if direction not in ("min", "max"):
        raise LeaderboardError("direction must be 'min' or 'max'")
    if type(top) is not int or not (1 <= top <= 1000):
        raise LeaderboardError("top must be an integer from 1 to 1000")

    runs: dict[str, Any] = {}
    for run in manifest.runs:
        if run.id in runs:
            raise LeaderboardError(f"duplicate run ID {run.id!r}; refusing ambiguous leaderboard")
        runs[run.id] = run

    base_metric = split_step_name(metric)[0] if latest else metric
    values: dict[str, Any] = {}
    for run_id in runs:
        results = [result for result in manifest.results if result.run_id == run_id]
        if latest:
            keep = latest_names(result.name for result in results)
            results = [result for result in results if result.name in keep]
        matches = [
            result
            for result in results
            if (split_step_name(result.name)[0] if latest else result.name) == base_metric
        ]
        if not matches:
            continue
        if len(matches) > 1:
            raise LeaderboardError(
                f"run {run_id!r} has multiple results named {base_metric!r}; refusing ambiguous leaderboard"
            )
        values[run_id] = value_to_json(matches[0].value)

    eligible: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for run_id in sorted(runs):
        run = runs[run_id]
        display = {"run_id": run.id, "run_name": run.name}
        if run_id not in values:
            missing.append(display)
        elif not _safe_number(values[run_id]):
            excluded.append({**display, "reason": "non_numeric_or_nonfinite"})
        else:
            eligible.append({**display, "value": values[run_id]})

    # Stable sorts preserve ascending run ID within ties for both directions.
    eligible.sort(key=lambda row: row["value"], reverse=direction == "max")
    previous: int | float | None = None
    for index, row in enumerate(eligible):
        value = row["value"]
        if index == 0 or value != previous:
            rank = index + 1
        row["rank"] = rank
        previous = value
    listed = eligible[:top]
    data = {
        "manifest": manifest_path,
        "metric": base_metric,
        "direction": direction,
        "latest": latest,
        "ranked": listed,
        "missing": missing,
        "excluded": excluded,
        "summary": {
            "runs_total": len(runs),
            "eligible": len(eligible),
            "shown": len(listed),
            "omitted_from_ranking": len(eligible) - len(listed),
            "missing": len(missing),
            "excluded": len(excluded),
        },
        "note": "Recorded manifest values only. No artifact or locator was checked; direction is user-selected, not an assessment of model quality.",
    }
    try:
        json.dumps(data, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise LeaderboardError("leaderboard includes non-JSON recorded values") from exc
    return data


def _shown(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def format_leaderboard(data: dict[str, Any]) -> str:
    """Render a bounded, control-character-safe human-facing view."""
    summary = data["summary"]
    lines = [
        f"Manifest: {_shown(data['manifest'])}",
        f"Metric: {_shown(data['metric'])} ({data['direction']} selected by user)",
        f"Eligible: {summary['eligible']}/{summary['runs_total']} runs",
    ]
    if not data["ranked"]:
        lines.append("No recorded finite numeric values for this metric.")
    for row in data["ranked"]:
        lines.append(
            f"  {row['rank']:>3}. {row['run_id']} ({_shown(row['run_name'])}): {_shown(row['value'])}"
        )
    omitted = summary["omitted_from_ranking"]
    if omitted:
        lines.append(f"  and {omitted} more ranked runs (use --top to show more)")
    lines.append(f"Missing metric: {summary['missing']} runs. Excluded nonnumeric: {summary['excluded']} runs.")
    for label in ("missing", "excluded"):
        for entry in data[label][:10]:
            lines.append(f"  {label}: {entry['run_id']} ({_shown(entry['run_name'])})")
        if len(data[label]) > 10:
            lines.append(f"  and {len(data[label]) - 10} more {label} runs (see --json)")
    lines.append("Recorded values only. Run 'runevidence validate' separately before relying on artifact evidence.")
    return "\n".join(lines)
