"""Recorded run inventory shared by the single-manifest and workspace CLIs."""

from __future__ import annotations

import json
from typing import Any

from .models import Manifest


def summarize_metadata(metadata: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Whitelisted adapter summaries, never arbitrary metadata."""
    summaries: dict[str, dict[str, Any]] = {}
    mlflow = metadata.get("mlflow")
    if isinstance(mlflow, dict):
        summary = {
            key: mlflow[key]
            for key in ("experiment_id", "experiment_name", "run_id", "status", "lifecycle_stage")
            if isinstance(mlflow.get(key), str)
        }
        if summary:
            summaries["mlflow"] = summary
    sacred = metadata.get("sacred")
    if isinstance(sacred, dict):
        summary = {
            key: sacred[key]
            for key in ("status", "command", "start_time", "stop_time")
            if isinstance(sacred.get(key), str)
        }
        if summary:
            summaries["sacred"] = summary
    wandb = metadata.get("wandb")
    if isinstance(wandb, dict):
        summary = {
            key: wandb[key]
            for key in ("run_id", "summary")
            if isinstance(wandb.get(key), str)
        }
        if summary:
            summaries["wandb"] = summary
    return summaries


def inventory_rows(
    manifest: Manifest,
    *,
    member: str = "",
    include_metadata: bool = False,
    include_summaries: bool = False,
) -> list[dict[str, Any]]:
    """Rows for one manifest, with per-run artifact/result counts."""
    artifacts: dict[str, int] = {}
    results: dict[str, int] = {}
    for artifact in manifest.artifacts:
        if artifact.run_id is not None:
            artifacts[artifact.run_id] = artifacts.get(artifact.run_id, 0) + 1
    for result in manifest.results:
        results[result.run_id] = results.get(result.run_id, 0) + 1
    rows: list[dict[str, Any]] = []
    for run in manifest.runs:
        row: dict[str, Any] = {
            "member": member,
            "run": run.id,
            "name": run.name,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "params": run.params,
            "conditions": (
                run.conditions.model_dump(mode="json")
                if run.conditions is not None
                else None
            ),
            "counts": {
                "artifacts": artifacts.get(run.id, 0),
                "results": results.get(run.id, 0),
            },
        }
        if include_metadata:
            row["metadata"] = run.metadata
        if include_summaries:
            summaries = summarize_metadata(run.metadata)
            if summaries:
                row["summaries"] = summaries
        rows.append(row)
    return rows


def runs_manifest(
    manifest: Manifest,
    *,
    manifest_path: str,
    include_metadata: bool = False,
    include_summaries: bool = False,
) -> dict[str, Any]:
    return {
        "format": "runevidence-runs-v1",
        "version": "0.1",
        "manifest": manifest_path,
        "metadata": include_metadata,
        "summaries": include_summaries,
        "runs": inventory_rows(
            manifest,
            include_metadata=include_metadata,
            include_summaries=include_summaries,
        ),
        "exit_code": 0,
    }


def _shown(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False)


def format_runs(data: dict[str, Any]) -> str:
    lines = [f"Manifest: {_shown(data['manifest'])}", f"Runs ({len(data['runs'])}):"]
    for row in data["runs"]:
        name = f" ({row['name']})" if row["name"] else ""
        lines.append(
            f"  {row['run']}{name}: {row['counts']['artifacts']} artifacts, "
            f"{row['counts']['results']} results"
        )
        if row["params"]:
            lines.append(f"    params: {_shown(row['params'])[:240]}")
        if row["conditions"] is not None:
            lines.append(f"    conditions: {_shown(row['conditions'])[:240]}")
        if row["started_at"]:
            lines.append(f"    started: {_shown(row['started_at'])[:80]}")
        if data["metadata"]:
            lines.append(f"    metadata: {_shown(row['metadata'])[:240]}")
        if data.get("summaries") and row.get("summaries"):
            lines.append(f"    summaries: {_shown(row['summaries'])[:240]}")
    return "\n".join(lines)
