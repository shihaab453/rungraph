"""D14: read-only typed v1 explanation for differing recorded fingerprints.

This report describes exactly which *recorded* objects/typed relationships differ.
It does not inspect filesystem artifact bytes or infer claim-scientific validity.
The v1 comparison uses the same frozen field projection and typed equality as
run_fingerprint; no dependency on the mutable D5 private comparison helpers.
"""
from __future__ import annotations

import json
from typing import Any

from .models import Manifest
from .run_fingerprint import (
    FingerprintError, _FIELDS, _frozen_v1, _guard_future_fields, _relations_v1,
)


def _records(manifest: Manifest) -> dict[str, tuple[str, dict[str, Any]]]:
    # Same object namespace and field-normalization rules as fingerprint v1.
    records: dict[str, tuple[str, dict[str, Any]]] = {}
    _guard_future_fields(manifest, "manifest")
    if manifest.schema_version != "0.1":
        raise FingerprintError("fingerprint v1 explanation only accepts schema 0.1")
    for kind, items in (("project", (manifest.project,)), ("run", manifest.runs),
                        ("artifact", manifest.artifacts), ("result", manifest.results),
                        ("evidence", manifest.evidence)):
        for obj in items:
            if obj.id in records:
                raise FingerprintError(f"duplicate object ID {obj.id!r}; refusing ambiguous explanation")
            _guard_future_fields(obj, kind)
            values = {field: getattr(obj, field) for field in _FIELDS[kind]}
            if kind == "evidence":
                for stance in ("supports", "contradicts"):
                    values[stance] = sorted(set(values[stance]))
            records[obj.id] = (kind, {field: _frozen_v1(value)
                                      for field, value in values.items()})
    return records


def _rel_key(rel: tuple[str, str, str, str | None]) -> tuple[str, str, str, bool, str]:
    typ, source, target, note = rel
    return typ, source, target, note is not None, note if note is not None else ""


def explain_fingerprint_difference(before: Manifest, after: Manifest) -> dict[str, Any]:
    """Return a deterministic, complete v1 recorded-change explanation.

    Object field values are never displayed, only field names, IDs and kinds.
    Typed relationship rows retain their recorded notes (JSON-escaped in text).
    """
    previous = _records(before)
    current = _records(after)
    objects = []
    tally = {"added": 0, "removed": 0, "retyped": 0, "modified": 0}
    for ident in sorted(previous.keys() | current.keys()):
        left, right = previous.get(ident), current.get(ident)
        if left is None:
            change = "added"
        elif right is None:
            change = "removed"
        elif left[0] != right[0]:
            change = "retyped"
        elif left[1] != right[1]:
            change = "modified"
        else:
            continue
        fields = (sorted(key for key in left[1].keys() | right[1].keys()
                         if left[1].get(key) != right[1].get(key))
                  if change == "modified" else [])
        objects.append({"id": ident, "change": change,
                        "before_kind": left[0] if left else None,
                        "after_kind": right[0] if right else None,
                        "changed_fields": fields})
        tally[change] += 1
    prior_rels = set(_relations_v1(before))
    next_rels = set(_relations_v1(after))
    relationships = []
    for operation, rels in (("removed", prior_rels - next_rels),
                            ("added", next_rels - prior_rels)):
        for typ, source, target, note in sorted(rels, key=_rel_key):
            relationships.append({"change": operation, "type": typ,
                                  "source": source, "target": target, "note": note})
    relationships.sort(key=lambda row: (row["type"], row["source"], row["target"],
                                         row["note"] is not None, row["note"] or "",
                                         row["change"]))
    return {"objects": objects, "relationships": relationships,
            "summary": {"object_changes": len(objects), "relationship_changes": len(relationships),
                        "objects_added": tally["added"], "objects_removed": tally["removed"],
                        "objects_retyped": tally["retyped"], "objects_modified": tally["modified"],
                        "relationships_added": len(next_rels - prior_rels),
                        "relationships_removed": len(prior_rels - next_rels)}}



def redact_fingerprint_explanation(report: dict[str, Any], *, show_notes: bool = False) -> dict[str, Any]:
    """Return a presentation copy; never modify the complete internal comparison.

    Direct Python API callers of explain_fingerprint_difference still receive raw
    recorded notes. The CLI suppresses nonempty note content by default, including
    in machine-readable JSON, because CI logs can be retained or shared.
    None and empty notes remain distinguishable. Use explicit --show-notes only
    when the output destination is approved to receive sensitive annotations.
    """
    if show_notes:
        return {**report, "notes_redacted": False}
    rows = []
    for item in report["relationships"]:
        value = item["note"]
        secret = value is not None and value != ""
        rows.append({**item, "note": "<redacted>" if secret else value,
                     "note_redacted": secret})
    # D14R2: the internal explanation sorts on the *unredacted* note, so
    # preserving that order leaks a comparison bit about hidden note strings.
    # Sort the presentation copy only by public values, AFTER redaction.
    # None, empty, and nonempty are intentionally distinguishable in v1.
    def visible_key(row: dict[str, Any]) -> tuple[str, str, str, int, str]:
        note_class = 0 if row["note"] is None else (1 if row["note"] == "" else 2)
        return row["type"], row["source"], row["target"], note_class, row["change"]

    rows.sort(key=visible_key)
    return {**report, "relationships": rows, "notes_redacted": True}


def format_fingerprint_explanation(report: dict[str, Any]) -> str:
    """Bounded, escaped human-readable output; --json has all rows."""
    s = report["summary"]
    header = (f"Recorded change explanation: {s['object_changes']} object(s), "
              f"{s['relationship_changes']} typed relationship(s).")
    if not s["object_changes"] and not s["relationship_changes"]:
        return header + "\nNo recorded v1 changes."
    lines = [header]
    budget = 8192
    total_rows = len(report["objects"]) + len(report["relationships"])
    shown = 0
    for row in [*report["objects"], *report["relationships"]]:
        # Individual rows and aggregate output are bounded. Never emit raw
        # control characters, artifact contents, or unescaped malicious notes.
        display = json.dumps(row, ensure_ascii=True, allow_nan=False, sort_keys=True)
        if len(display) > 640:
            # Do not cut through a \uXXXX escape, a quote, or a JSON string.
            # A separate valid JSON object signals that the row is shortened.
            display = json.dumps({
                "row_shortened": True,
                "kind": "relationship" if "type" in row else "object",
                "change": row.get("change"),
                "message": "row shortened; use --json for the complete row",
                # A bounded prefix is JSON-escaped as a whole, never cut inside
                # a Unicode escape or string. Raw notes reach this formatter
                # only via direct API or the explicit CLI --show-notes opt-in.
                "note_preview": row["note"][:24] if isinstance(row.get("note"), str) else None,
            }, ensure_ascii=True, sort_keys=True)
            assert len(display) <= 640
        if sum(len(line) + 1 for line in lines) + len(display) + 100 > budget:
            break
        lines.append(display)
        shown += 1
    if shown < total_rows:
        lines.append(f"... [{total_rows - shown} additional change row(s); use --json]")
    if report.get("notes_redacted") and any(row.get("note_redacted") for row in report["relationships"]):
        # This message contains no recorded values and is itself bounded.
        notice = "Relationship notes redacted by default; use --show-notes only for trusted output."
        if sum(len(line) + 1 for line in lines) + len(notice) <= budget:
            lines.append(notice)
    return "\n".join(lines)
