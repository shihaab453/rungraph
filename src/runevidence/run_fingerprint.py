"""Stable SHA-256 identity of recorded manifest assertions, not artifact integrity.

The v1 field projection, tagging and relationship representation are frozen here.
D5 comparison helpers must not silently change the public fingerprint ABI.
"""
from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
import math
from typing import Any

from .models import Manifest


class FingerprintError(ValueError):
    """A manifest cannot be represented unambiguously in fingerprint v1."""


_SEMANTICS = "recorded-manifest-v1"
_ALGORITHM = "sha256"
_SCHEMA_VERSION = "0.1"

# Full v1 record projection. Do not derive these names from model_fields or
# model_dump: schema additions with defaults must not silently alter the digest.
_FIELDS = {
    "project": ("id", "name", "description", "tags", "metadata"),
    "run": ("id", "name", "started_at", "params", "metadata"),
    "artifact": ("id", "path", "artifact_type", "digest_format", "sha256",
                 "size_bytes", "volatile", "kind", "run_id", "metadata"),
    "result": ("id", "run_id", "name", "value", "artifact_id", "locator", "metadata"),
    "evidence": ("id", "claim", "supports", "contradicts", "source", "metadata"),
    "relationship": ("source", "target", "type", "note"),
    "manifest": ("schema_version", "project", "runs", "artifacts", "results",
                 "evidence", "relationships"),
}
_REL_TYPES = frozenset(("produced_by", "input_to", "result_of", "reported_in",
                        "derived_from", "supports", "contradicts"))


def _frozen_v1(value: Any) -> Any:
    """ABI-stable D5-style typed representation, independent of D5 helpers."""
    if isinstance(value, dict):
        return ("dict", tuple(sorted((str(key), _frozen_v1(item))
                                     for key, item in value.items())))
    if isinstance(value, (tuple, list)):
        return ("list", tuple(_frozen_v1(item) for item in value))
    if value is None:
        return ("null",)
    if type(value) is bool:
        return ("bool", value)
    if type(value) is int:
        return ("int", value)
    if type(value) is float:
        if math.isnan(value):
            return ("float", "NaN")
        if math.isinf(value):
            return ("float", "Infinity" if value > 0 else "-Infinity")
        return ("float", repr(value))
    if isinstance(value, (date, datetime)):
        return ("date", value.isoformat())
    if isinstance(value, str):
        return ("str", value)
    raise FingerprintError(f"unsupported recorded fingerprint v1 value type: {type(value).__name__}")


def _guard_future_fields(obj: Any, kind: str) -> None:
    """Ignore only new fields still equal to their declared optional defaults.

    A newly added non-default value cannot be silently omitted or reinterpreted
    within v1; it requires a conscious v2 design/version decision.
    """
    known = set(_FIELDS[kind])
    model = type(obj)
    present = model.model_fields
    # Pydantic model_dump() includes computed fields and populated allowed
    # extras. Both are outside the frozen v1 projection and therefore must
    # fail closed instead of producing a misleading unchanged digest.
    computed = getattr(model, "model_computed_fields", {})
    if computed:
        raise FingerprintError(
            f"fingerprint v1 cannot encode computed {kind} fields: "
            f"{sorted(computed)}; version bump required"
        )
    extras = getattr(obj, "__pydantic_extra__", None)
    if extras:
        raise FingerprintError(
            f"fingerprint v1 cannot encode extra {kind} fields: "
            f"{sorted(map(str, extras))}; version bump required"
        )
    # model_copy(update=...) can bypass extra='forbid' and inject fields into
    # __dict__ without populating model_extra. Refuse those as well.
    undeclared = set(getattr(obj, "__dict__", {})) - set(present)
    if undeclared:
        raise FingerprintError(
            f"fingerprint v1 cannot encode undeclared {kind} fields: "
            f"{sorted(map(str, undeclared))}; version bump required"
        )
    missing = known - present.keys()
    if missing:
        raise FingerprintError(f"fingerprint v1 {kind} schema removed fields: {sorted(missing)}")
    for name in sorted(present.keys() - known):
        field = present[name]
        if field.is_required():
            raise FingerprintError(f"fingerprint v1 cannot encode new required {kind} field {name!r}; version bump required")
        default = field.get_default(call_default_factory=True)
        if _frozen_v1(getattr(obj, name)) != _frozen_v1(default):
            raise FingerprintError(f"fingerprint v1 cannot encode non-default new {kind} field {name!r}; version bump required")


def _object_v1(obj: Any, kind: str) -> Any:
    _guard_future_fields(obj, kind)
    record = {key: getattr(obj, key) for key in _FIELDS[kind]}
    if kind == "evidence":
        for stance in ("supports", "contradicts"):
            record[stance] = sorted(set(record[stance]))
    return _frozen_v1(record)


def _relations_v1(manifest: Manifest) -> list[tuple[str, str, str, str | None]]:
    keys = set()
    for rel in manifest.relationships:
        _guard_future_fields(rel, "relationship")
        typ = rel.type.value
        if typ not in _REL_TYPES:
            raise FingerprintError(f"fingerprint v1 does not know relationship type {typ!r}; version bump required")
        keys.add((typ, rel.source, rel.target, rel.note))
    return sorted(keys, key=lambda r: (r[0], r[1], r[2], r[3] is not None, r[3] if r[3] is not None else ""))


def manifest_fingerprint(manifest: Manifest, *, manifest_path: str) -> dict[str, Any]:
    """Fingerprint v1 recorded assertions with frozen current-model semantics.

    This ABI fingerprints the *loaded representation* under schema 0.1,
    not original bytes, artifact files, or actual scientific reproducibility.
    """
    try:
        _guard_future_fields(manifest, "manifest")
        if manifest.schema_version != _SCHEMA_VERSION:
            raise FingerprintError("fingerprint v1 only accepts manifest schema 0.1; explicit version decision required")
        index = {}
        for kind, records in (
            ("project", (manifest.project,)), ("run", manifest.runs),
            ("artifact", manifest.artifacts), ("result", manifest.results),
            ("evidence", manifest.evidence),
        ):
            for obj in records:
                if obj.id in index:
                    raise FingerprintError(f"duplicate object ID {obj.id!r}; refusing ambiguous fingerprint")
                index[obj.id] = (kind, obj)
        objects = [(ident, kind, _object_v1(obj, kind))
                   for ident, (kind, obj) in sorted(index.items())]
        relationships = _relations_v1(manifest)
        canonical = json.dumps(
            {"semantic_format": _SEMANTICS, "objects": objects, "relationships": relationships},
            ensure_ascii=True, allow_nan=False, separators=(",", ":"), sort_keys=True,
        ).encode("ascii")
    except FingerprintError:
        raise
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError) as exc:
        raise FingerprintError("cannot canonically fingerprint this recorded manifest") from exc
    result: dict[str, Any] = {
        "manifest": manifest_path,
        "fingerprint": {
            "algorithm": _ALGORITHM,
            "semantic_format": _SEMANTICS,
            "digest": hashlib.sha256(canonical).hexdigest(),
            "objects": len(objects),
            "relationships": len(relationships),
        },
        "note": (
            "SHA-256 of normalized recorded manifest assertions only, not a digest of the "
            "source JSON file or any artifact contents. The v1 projection is explicitly frozen "
            "to its original schema-0.1 fields; optional future fields at their defaults "
            "are ignored and non-default new fields require a fingerprint format bump. "
            "It matches D5's original comparison semantics but no longer imports D5 helpers. "
            "Project metadata, claim assertions and relationship notes are included. "
            "Identical fingerprints are not scientific reproducibility or file-integrity proof. "
            "No files besides the manifest were read, and no data was modified. "
            "Run 'runevidence validate' separately before trusting evidence."
        ),
    }
    return result


def format_fingerprint(data: dict[str, Any]) -> str:
    """Escaped terminal presentation; JSON provides complete machine-readable data."""
    fp = data["fingerprint"]
    path = data["manifest"]
    shown = json.dumps(path[:256] + f"... [{len(path) - 256} chars omitted; use --json]"
                       if len(path) > 256 else path, ensure_ascii=True)
    return (f"Manifest: {shown}\n"
            f"Recorded semantic format: {fp['semantic_format']}\n"
            f"Recorded fingerprint ({fp['algorithm']}): {fp['digest']}\n"
            f"Recorded objects: {fp['objects']}; unique typed relationships: {fp['relationships']}\n"
            "Recorded assertions only, not artifact/file integrity. Run 'runevidence validate' separately.")
