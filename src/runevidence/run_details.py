"""Read-only drilldown for one run's recorded results, files and claim links.

All associations are taken from a single in-memory manifest. No artifact bytes,
locators or provenance are checked, and no filesystem writer is used.
"""
from __future__ import annotations

import json
import math
from typing import Any

from .locator_profiles import display_locator
from .models import Manifest, RelationshipType, locator_to_json


class RunDetailsError(ValueError):
    """The requested run or its recorded associations are ambiguous."""


def _unique_ids(manifest: Manifest) -> None:
    seen: set[str] = set()
    objects = ([manifest.project] + list(manifest.runs) + list(manifest.artifacts)
               + list(manifest.results) + list(manifest.evidence))
    for obj in objects:
        if obj.id in seen:
            raise RunDetailsError(f"duplicate object ID {obj.id!r}; run detail would be ambiguous")
        seen.add(obj.id)


def _owners(manifest: Manifest, *, kind: str) -> dict[str, str]:
    """Merge direct run_id fields and explicit typed ownership edges."""
    if kind == "artifact":
        objects = manifest.artifacts
        relation_type = RelationshipType.produced_by
    else:
        objects = manifest.results
        relation_type = RelationshipType.result_of
    by_id: dict[str, set[str]] = {obj.id: set() for obj in objects}
    for obj in objects:
        if obj.run_id is not None:
            by_id[obj.id].add(obj.run_id)
    for rel in manifest.relationships:
        if rel.type == relation_type and rel.source in by_id:
            by_id[rel.source].add(rel.target)
    resolved: dict[str, str] = {}
    for identifier, owners in by_id.items():
        if len(owners) > 1:
            raise RunDetailsError(f"{kind} {identifier!r} has conflicting recorded run attribution: {sorted(owners)!r}")
        if owners:
            resolved[identifier] = next(iter(owners))
    return resolved


def _unreadable(run: Any) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for namespace in ("hydra", "mlflow"):
        details = run.metadata.get(namespace) if isinstance(run.metadata, dict) else None
        if not isinstance(details, dict) or not isinstance(details.get("unreadable"), list):
            continue
        reasons = details.get("unreadable_reasons")
        reasons = reasons if isinstance(reasons, dict) else {}
        for path in details["unreadable"]:
            if not isinstance(path, str):
                continue
            row = {"adapter": namespace, "path": path}
            if isinstance(reasons.get(path), str):
                row["reason"] = reasons[path]
            entries.append(row)
    return entries


def _json_safe_nonfinite(value: Any, pointer: str, noted: list[dict[str, str]]) -> Any:
    """Represent nonfinite floats as JSON null, with exact locations in metadata.

    Nonfinite numeric observations are allowed by the existing manifest model.
    Turning them into bare null would conflate NaN/inf with a recorded null, so
    retain a separate RFC 6901 pointer to every transformed value.
    """
    if type(value) is float and not math.isfinite(value):
        kind = "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
        noted.append({"path": pointer, "kind": kind})
        return None
    if isinstance(value, list):
        return [_json_safe_nonfinite(item, f"{pointer}/{i}", noted) for i, item in enumerate(value)]
    if isinstance(value, dict):
        return {
            key: _json_safe_nonfinite(
                val, pointer + "/" + str(key).replace("~", "~0").replace("/", "~1"), noted
            )
            for key, val in value.items()
        }
    return value


def run_details(manifest: Manifest, run_id: str, *, manifest_path: str) -> dict[str, Any]:
    """Describe the selected run's directly attributed and explicitly linked data.

    References are recorded relationships, not attested provenance. The routine
    rejects ambiguous object IDs and conflicting ownership; it does not run the
    graph validator or prove that claimed file contents exist.
    """
    _unique_ids(manifest)
    matches = [run for run in manifest.runs if run.id == run_id]
    if len(matches) != 1:
        raise RunDetailsError(f"run {run_id!r} not found")
    run = matches[0]
    artifact_owners = _owners(manifest, kind="artifact")
    result_owners = _owners(manifest, kind="result")
    artifacts = [artifact for artifact in manifest.artifacts if artifact_owners.get(artifact.id) == run_id]
    results = [result for result in manifest.results if result_owners.get(result.id) == run_id]
    inputs = {rel.source for rel in manifest.relationships
              if rel.type == RelationshipType.input_to and rel.target == run_id}
    input_artifacts = [artifact for artifact in manifest.artifacts if artifact.id in inputs]

    def artifact_dict(item: Any) -> dict[str, Any]:
        return {"id": item.id, "path": item.path, "kind": item.kind,
                "volatile": item.volatile, "sha256": item.sha256}

    # The manifest accepts TWO equivalent spellings for where a result was
    # recorded: Result.artifact_id and explicit reported_in relationships.
    # These are source links, not producer links. A metrics file need not be
    # attributed to the inspected run to be relevant to its recorded result.
    sources_by_result: dict[str, set[str]] = {
        r.id: ({r.artifact_id} if r.artifact_id is not None else set()) for r in results
    }
    for rel in manifest.relationships:
        if rel.type == RelationshipType.reported_in and rel.source in sources_by_result:
            sources_by_result[rel.source].add(rel.target)
    source_ids = set().union(*sources_by_result.values()) if sources_by_result else set()
    result_source_artifacts = [a for a in manifest.artifacts if a.id in source_ids]

    result_rows: list[dict[str, Any]] = []
    for r in results:
        row = {"id": r.id, "name": r.name, "value": r.value,
               "artifact_id": r.artifact_id, "locator": locator_to_json(r.locator)}
        if sources_by_result[r.id]:
            # Preserve every recorded target ID, including dangling ones.
            # Source existence and edge validity are NOT asserted here.
            row["recorded_source_artifact_ids"] = sorted(sources_by_result[r.id])
        result_rows.append(row)

    related = ({run_id} | {a.id for a in artifacts} | {r.id for r in results}
               | {a.id for a in input_artifacts} | {a.id for a in result_source_artifacts})
    # A claim may reference a recorded object via either the convenience lists
    # or an explicit supports/contradicts relationship. De-duplicate links.
    evidence_rows: list[dict[str, Any]] = []
    for claim in manifest.evidence:
        supports = set(claim.supports) & related
        contradicts = set(claim.contradicts) & related
        for rel in manifest.relationships:
            if rel.source != claim.id or rel.target not in related:
                continue
            if rel.type == RelationshipType.supports:
                supports.add(rel.target)
            elif rel.type == RelationshipType.contradicts:
                contradicts.add(rel.target)
        if supports or contradicts:
            evidence_rows.append({"id": claim.id, "claim": claim.claim, "source": claim.source,
                                  "supports": sorted(supports), "contradicts": sorted(contradicts)})

    data = {
        "manifest": manifest_path,
        "run": {"id": run.id, "name": run.name, "started_at": run.started_at.isoformat() if run.started_at else None,
                "params": run.params},
        "results": result_rows,
        "produced_artifacts": [artifact_dict(a) for a in artifacts],
        "input_artifacts": [artifact_dict(a) for a in input_artifacts],
        "related_claims": evidence_rows,
        "unreadable_inputs": _unreadable(run),
        "summary": {"results": len(result_rows), "produced_artifacts": len(artifacts),
                    "input_artifacts": len(input_artifacts), "related_claims": len(evidence_rows)},
        "note": "Recorded manifest associations only. File bytes, validity, source authenticity and relationships have not been checked.",
    }
    if result_source_artifacts:
        # Additive field only when useful. Existing summary keys remain stable.
        data["result_source_artifacts"] = [artifact_dict(a) for a in result_source_artifacts]
    nonfinite: list[dict[str, str]] = []
    try:
        data = _json_safe_nonfinite(data, "", nonfinite)
        if nonfinite:
            data["nonfinite_recorded_values"] = nonfinite
        json.dumps(data, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise RunDetailsError("run detail contains non-JSON recorded values") from exc
    return data


def format_run_details(data: dict[str, Any]) -> str:
    """Deterministic terminal-safe display with a short bound on each section."""
    def shown(value: Any) -> str:
        return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True)

    def shown_locator(value: Any) -> str:
        if value is None:
            return "None"
        return '"' + display_locator(value) + '"'

    def source_suffix(row: dict[str, Any]) -> str:
        ids = row.get("recorded_source_artifact_ids", [])
        if not ids:
            return ""
        count = len(ids) - 20
        extra = f", and {count} more in --json" if count > 0 else ""
        return f" (recorded source artifact IDs: {shown(ids[:20])}{extra})"

    run = data["run"]
    lines = [f"Manifest: {shown(data['manifest'])}",
             f"Run: {shown(run['id'])} ({shown(run['name'])})",
             f"Started: {shown(run['started_at'])}"]
    sections = [
        ("Parameters", [{"key": key, "value": value} for key, value in sorted(run["params"].items())],
         lambda x: f"  {shown(x['key'])}: {shown(x['value'])}"),
        ("Results", data["results"],
         lambda x: f"  {shown(x['id'])} {shown(x['name'])}: {shown(x['value'])} (artifact: {shown(x['artifact_id'])}, locator: {shown_locator(x['locator'])})"
         + source_suffix(x)),
        ("Produced artifacts", data["produced_artifacts"],
         lambda x: f"  {shown(x['id'])}: {shown(x['path'])} (volatile: {x['volatile']})"),
        ("Input artifacts", data["input_artifacts"],
         lambda x: f"  {shown(x['id'])}: {shown(x['path'])}"),
        ("Related claims", data["related_claims"],
         lambda x: f"  {shown(x['id'])}: {shown(x['claim'])} (supports: {shown(x['supports'])}, contradicts: {shown(x['contradicts'])})"),
        ("Adapter unreadable inputs", data["unreadable_inputs"],
         lambda x: f"  {shown(x['adapter'])}: {shown(x['path'])}" + (f" ({shown(x['reason'])})" if 'reason' in x else '')),
    ]
    if data.get("result_source_artifacts"):
        sections.insert(4, ("Recorded result source artifacts", data["result_source_artifacts"],
                            lambda x: f"  {shown(x['id'])}: {shown(x['path'])}"))
    for heading, entries, render in sections:
        lines.append(f"\n{heading} ({len(entries)}):")
        for entry in entries[:20]:
            lines.append(render(entry))
        if len(entries) > 20:
            lines.append(f"  and {len(entries) - 20} more (use --json)")
    if data.get("nonfinite_recorded_values"):
        anomalies = data["nonfinite_recorded_values"]
        lines.append(f"\nNonfinite recorded values ({len(anomalies)}):")
        for item in anomalies[:20]:
            lines.append(f"  {shown(item['path'])}: {item['kind']} (JSON null)")
        if len(anomalies) > 20:
            lines.append(f"  and {len(anomalies) - 20} more (use --json)")
    lines.append("Recorded associations only. Run 'runevidence validate' separately before trusting the evidence.")
    return "\n".join(lines)
