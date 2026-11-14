"""Read-only explanation of a claim's recorded support and contradiction links.

This describes the manifest's assertions; it never proves an asserted link,
opens an artifact, follows a source URL, or runs any validator or writer.
"""
from __future__ import annotations

import json
import math
from typing import Any

from .locator_profiles import display_locator
from .models import Manifest, RelationshipType, locator_to_json


class ClaimDetailsError(ValueError):
    """Claim selection or the recorded object namespace is ambiguous."""


def _display_details(details: dict[str, Any]) -> dict[str, Any]:
    """A human-display copy of one target/node ``details`` mapping.

    A 0.4 ``csv-cell`` locator is shown as ``csv-cell:<row>,<column>`` so the
    recorded ``header`` never reaches the terminal, and a 0.5 ``tensorboard``
    locator as ``tensorboard:step=<step>,occurrence=<n>`` so the recorded tag
    never does. Every other locator form (0.3 strings and ``jsonl`` objects)
    stays exactly as recorded, which keeps the 0.3 human bytes unchanged. The
    caller's data is never mutated; this returns a shallow copy only when the
    locator is actually replaced.
    """
    locator = details.get("locator")
    kind = (
        locator.get("kind")
        if isinstance(locator, dict)
        else getattr(locator, "kind", None)
    )
    if kind in ("csv-cell", "tensorboard"):
        return {**details, "locator": display_locator(locator)}
    return details


def _display_row(row: dict[str, Any]) -> dict[str, Any]:
    """A human-display copy of one target/node row, with a safe locator copy."""
    details = row.get("details")
    if isinstance(details, dict):
        displayed = _display_details(details)
        if displayed is not details:
            return {**row, "details": displayed}
    return row


def _index(manifest: Manifest) -> dict[str, tuple[str, Any]]:
    # IDs share a global namespace in RunGraph; never silently pick one kind.
    index: dict[str, tuple[str, Any]] = {}
    for kind, objects in (
        ("project", (manifest.project,)),
        ("run", manifest.runs),
        ("artifact", manifest.artifacts),
        ("result", manifest.results),
        ("evidence", manifest.evidence),
    ):
        for obj in objects:
            if obj.id in index:
                raise ClaimDetailsError(f"duplicate object ID {obj.id!r}; refusing ambiguous claim drilldown")
            index[obj.id] = (kind, obj)
    return index


def _target(identifier: str, index: dict[str, tuple[str, Any]]) -> dict[str, Any]:
    if identifier not in index:
        return {"id": identifier, "status": "unresolved", "kind": None}
    kind, obj = index[identifier]
    if kind in ("project", "evidence"):
        # A supports/contradicts edge is documented for result, artifact or run.
        # Existing but unexpected kinds must not be presented as valid evidence.
        return {"id": identifier, "status": "unsupported_target_kind", "kind": kind}
    if kind == "run":
        details = {"name": obj.name, "started_at": obj.started_at.isoformat() if obj.started_at else None}
    elif kind == "artifact":
        details = {"path": obj.path, "kind": obj.kind, "run_id": obj.run_id,
                   "volatile": obj.volatile, "sha256": obj.sha256}
    else:
        details = {"name": obj.name, "value": obj.value, "run_id": obj.run_id,
                   "artifact_id": obj.artifact_id, "locator": locator_to_json(obj.locator)}
    return {"id": identifier, "status": "recorded", "kind": kind, "details": details}


def _json_safe(value: Any, pointer: str, flagged: list[dict[str, str]]) -> Any:
    """Keep nonfinite recorded values visible without emitting invalid JSON."""
    if type(value) is float and not math.isfinite(value):
        kind = "NaN" if math.isnan(value) else ("Infinity" if value > 0 else "-Infinity")
        flagged.append({"path": pointer, "kind": kind})
        return None
    if isinstance(value, list):
        return [_json_safe(item, f"{pointer}/{i}", flagged) for i, item in enumerate(value)]
    if isinstance(value, dict):
        return {
            key: _json_safe(item, pointer + "/" + str(key).replace("~", "~0").replace("/", "~1"), flagged)
            for key, item in value.items()
        }
    return value


def claim_details(manifest: Manifest, claim_id: str, *, manifest_path: str) -> dict[str, Any]:
    """Explain one claim's inline and typed links without validating their truth."""
    index = _index(manifest)
    if claim_id not in index or index[claim_id][0] != "evidence":
        raise ClaimDetailsError(f"claim {claim_id!r} not found")
    claim = index[claim_id][1]

    # One target can appear through both shorthand and explicit edges, with
    # optional notes. Deduplicate per stance without suppressing dual stances.
    links: dict[str, dict[str, dict[str, Any]]] = {"supports": {}, "contradicts": {}}
    for stance in ("supports", "contradicts"):
        for identifier in getattr(claim, stance):
            record = links[stance].setdefault(identifier, {"via": set(), "notes": set()})
            record["via"].add("inline")
    for rel in manifest.relationships:
        if rel.source != claim_id:
            continue
        if rel.type == RelationshipType.supports:
            stance = "supports"
        elif rel.type == RelationshipType.contradicts:
            stance = "contradicts"
        else:
            continue
        record = links[stance].setdefault(rel.target, {"via": set(), "notes": set()})
        record["via"].add("relationship")
        if rel.note is not None:
            record["notes"].add(rel.note)

    data: dict[str, Any] = {
        "manifest": manifest_path,
        "claim": {"id": claim.id, "text": claim.claim, "source": claim.source},
    }
    for stance in ("supports", "contradicts"):
        rows = []
        for identifier, record in sorted(links[stance].items()):
            row = _target(identifier, index)
            row["via"] = [label for label in ("inline", "relationship") if label in record["via"]]
            if record["notes"]:
                row["relationship_notes"] = sorted(record["notes"])
            rows.append(row)
        data[stance] = rows
    overlap = sorted(set(links["supports"]) & set(links["contradicts"]))
    data["dual_stance_target_ids"] = overlap
    all_rows = data["supports"] + data["contradicts"]
    data["summary"] = {
        "supports": len(data["supports"]),
        "contradicts": len(data["contradicts"]),
        "unresolved_links": sum(r["status"] == "unresolved" for r in all_rows),
        "unsupported_kind_links": sum(r["status"] == "unsupported_target_kind" for r in all_rows),
        "dual_stance_targets": len(overlap),
    }
    data["note"] = (
        "Recorded assertions only. Targets, provenance, referenced files and claim truth were not validated. "
        "An ID appearing under both stances is shown in both, not resolved or judged."
    )
    flagged: list[dict[str, str]] = []
    try:
        data = _json_safe(data, "", flagged)
        if flagged:
            data["nonfinite_recorded_values"] = flagged
        json.dumps(data, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ClaimDetailsError("claim drilldown contains non-JSON recorded values") from exc
    return data


def format_claim_details(data: dict[str, Any]) -> str:
    """Terminal-safe and bounded human-readable claim detail."""
    # JSON output is complete. Terminal output is deliberately size-limited:
    # claims, paths and relationship notes are user-supplied strings.
    MAX_FIELD_CHARS = 256
    MAX_TEXT_CHARS = 16384
    DISCLAIMER = "Recorded assertions only. Run 'runevidence validate' separately before trusting evidence."

    def compact(value: Any) -> Any:
        if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
            omitted = len(value) - MAX_FIELD_CHARS
            return value[:MAX_FIELD_CHARS] + f"... [{omitted} chars omitted; use --json]"
        if isinstance(value, dict):
            return {key: compact(item) for key, item in value.items()}
        if isinstance(value, list):
            return [compact(item) for item in value]
        return value

    def shown(value: Any) -> str:
        return json.dumps(compact(value), ensure_ascii=True, allow_nan=False, sort_keys=True)

    claim = data["claim"]
    lines = [
        f"Manifest: {shown(data['manifest'])}",
        f"Claim: {shown(claim['id'])}: {shown(claim['text'])}",
        f"Claim source (recorded, not opened): {shown(claim['source'])}",
    ]
    for stance, heading in (("supports", "Supports"), ("contradicts", "Contradicts")):
        items = data[stance]
        lines.append(f"\n{heading} ({len(items)} recorded targets):")
        for item in items[:20]:
            item = _display_row(item)
            desc = f"  {shown(item['id'])}: {item['status']} ({shown(item['kind'])}) via {shown(item['via'])}"
            if "details" in item:
                desc += f" {shown(item['details'])}"
            lines.append(desc)
            for note in item.get("relationship_notes", [])[:5]:
                lines.append(f"    recorded note: {shown(note)}")
            if len(item.get("relationship_notes", [])) > 5:
                lines.append(f"    and {len(item['relationship_notes']) - 5} more notes (use --json)")
        if len(items) > 20:
            lines.append(f"  and {len(items) - 20} more targets (use --json)")
    if data["dual_stance_target_ids"]:
        ids = data["dual_stance_target_ids"]
        lines.append(f"\nRecorded under BOTH stances ({len(ids)}): {shown(ids[:20])}")
        if len(ids) > 20:
            lines.append(f"  and {len(ids)-20} more (use --json)")
    if data.get("nonfinite_recorded_values"):
        items = data["nonfinite_recorded_values"]
        lines.append(f"\nNonfinite recorded values ({len(items)}):")
        for item in items[:20]:
            lines.append(f"  {shown(item['path'])}: {item['kind']} (JSON null)")
        if len(items) > 20:
            lines.append(f"  and {len(items)-20} more (use --json)")
    lines.append(DISCLAIMER)
    rendered = "\n".join(lines)
    if len(rendered) <= MAX_TEXT_CHARS:
        return rendered
    marker = "\n... text output truncated (use --json for complete recorded values).\n"
    budget = MAX_TEXT_CHARS - len(marker) - len(DISCLAIMER)
    prefix = rendered[:budget]
    # Prefer ending at a line boundary rather than mid-JSON escape sequence.
    boundary = prefix.rfind("\n")
    if boundary >= budget // 2:
        prefix = prefix[:boundary]
    return prefix.rstrip("\n") + marker + DISCLAIMER
