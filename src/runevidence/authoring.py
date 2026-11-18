"""Author results, claims/evidence and relationships in an existing manifest.

The manifest's raw JSON is edited in place, so every field the user wrote stays
as it was. Nothing is written unless the whole edit validates.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .manifest import LoadedManifest, validate_manifest


class AuthoringError(ValueError):
    """The requested authoring operation cannot be applied."""


def _ids(raw: dict) -> set[str]:
    found = {raw["project"]["id"]}
    for section in ("runs", "artifacts", "results", "evidence"):
        found |= {entry["id"] for entry in raw.get(section, [])}
    return found


def _find(raw: dict, section: str, entry_id: str) -> int:
    for index, entry in enumerate(raw.get(section, [])):
        if entry.get("id") == entry_id:
            return index
    raise AuthoringError(f"no {section[:-1] if section.endswith('s') else section} {entry_id!r}")


def _check_link_types(manifest) -> None:
    from .checks.graph import ALLOWED_ENDPOINTS, build_index, collect_edges

    kinds, _ = build_index(manifest)
    for edge in collect_edges(manifest):
        source = kinds.get(edge.source)
        target = kinds.get(edge.target)
        if source is None or target is None:
            continue
        if (source, target) not in ALLOWED_ENDPOINTS[edge.type]:
            allowed = ", ".join(
                f"{left} -> {right}"
                for left, right in sorted(ALLOWED_ENDPOINTS[edge.type])
            )
            raise AuthoringError(
                f"{edge.origin}: {edge.type.value!r} cannot link a {source} to a "
                f"{target}; allowed: {allowed}"
            )


def _validate(raw: dict) -> None:
    try:
        manifest = validate_manifest(raw)
    except Exception as exc:
        raise AuthoringError(f"the edited manifest is not valid: {exc}") from None
    _check_link_types(manifest)


def _text(entry: dict, key: str, what: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AuthoringError(f"{what} needs a non-empty string {key!r}")
    return value


def _check_refs(raw: dict, entry: dict, keys: tuple[str, ...] = ()) -> None:
    known = _ids(raw)
    for key in keys:
        values = entry.get(key)
        if values is None:
            continue
        if not isinstance(values, list):
            raise AuthoringError(f"{key} must be a list of IDs")
        for value in values:
            if not isinstance(value, str) or value not in known:
                raise AuthoringError(f"{key} names {value!r}, which is not in the manifest")


def add_result(raw: dict, entry: dict[str, Any]) -> None:
    entry = dict(entry)
    for key in ("id", "run_id", "name"):
        _text(entry, key, "a result")
    if entry["id"] in _ids(raw):
        raise AuthoringError(f"ID {entry['id']!r} is already used")
    runs = {item["id"] for item in raw.get("runs", [])}
    if entry["run_id"] not in runs:
        raise AuthoringError(f"run {entry['run_id']!r} does not exist")
    artifact_id = entry.get("artifact_id")
    if artifact_id is not None:
        if not isinstance(artifact_id, str):
            raise AuthoringError("a result needs a string 'artifact_id' or none")
        if artifact_id not in {item["id"] for item in raw.get("artifacts", [])}:
            raise AuthoringError(f"artifact {artifact_id!r} does not exist")
    raw.setdefault("results", []).append(entry)
    _validate(raw)


def edit_result(raw: dict, entry_id: str, patch: dict[str, Any]) -> None:
    index = _find(raw, "results", entry_id)
    result = raw["results"][index]
    for key, value in patch.items():
        if key == "id":
            continue
        result[key] = value
    _text(result, "name", "a result")
    runs = {item["id"] for item in raw.get("runs", [])}
    if not isinstance(result.get("run_id"), str) or result.get("run_id") not in runs:
        raise AuthoringError(f"run {result.get('run_id')!r} does not exist")
    artifact_id = result.get("artifact_id")
    if artifact_id is not None:
        if not isinstance(artifact_id, str):
            raise AuthoringError("a result needs a string 'artifact_id' or none")
        if artifact_id not in {item["id"] for item in raw.get("artifacts", [])}:
            raise AuthoringError(f"artifact {artifact_id!r} does not exist")
    _validate(raw)


_ARTIFACT_EDITABLE = {"kind", "dataset_id", "run_id", "metadata", "volatile"}
_ARTIFACT_IMMUTABLE = {
    "id", "path", "sha256", "size_bytes", "artifact_type", "digest_format"
}
_CONDITION_ARTIFACT_FIELDS = (
    "evaluation_dataset_artifact_id",
    "model_checkpoint_artifact_id",
)


def edit_artifact(raw: dict, entry_id: str, patch: dict[str, Any]) -> None:
    """Merge selected metadata fields into one recorded artifact.

    Identity, path, digest, size and the directory-format fields stay with
    ``add``/``rehash``; hand-editing them would let a manifest vouch for bytes
    nobody read.
    """
    index = _find(raw, "artifacts", entry_id)
    artifact = raw["artifacts"][index]
    for key, value in patch.items():
        if key == "id":
            continue
        if key in _ARTIFACT_IMMUTABLE:
            raise AuthoringError(
                f"{key!r} cannot be edited here; use 'add' or 'rehash' for identity, "
                "path, bytes or directory format"
            )
        if key not in _ARTIFACT_EDITABLE:
            raise AuthoringError(f"unknown artifact field {key!r}")
        artifact[key] = value
    run_id = artifact.get("run_id")
    if run_id is not None:
        if not isinstance(run_id, str) or run_id not in {
            item["id"] for item in raw.get("runs", [])
        }:
            raise AuthoringError(f"run {run_id!r} does not exist")
    _validate(raw)


def _artifact_references(raw: dict, artifact_id: str) -> list[str]:
    used: list[str] = []
    for result in raw.get("results", []):
        if result.get("artifact_id") == artifact_id:
            used.append(f"result {result.get('id')!r}")
    for run in raw.get("runs", []):
        conditions = run.get("conditions")
        if not isinstance(conditions, dict):
            continue
        for field in _CONDITION_ARTIFACT_FIELDS:
            if conditions.get(field) == artifact_id:
                used.append(f"run {run.get('id')!r} condition {field!r}")
    return used


def remove_artifact(raw: dict, entry_id: str, *, with_results: bool = False) -> None:
    """Remove one recorded artifact, optionally with its direct results.

    The file itself is untouched. By default every reference must be gone
    first. With ``with_results`` the results whose ``artifact_id`` is this
    artifact are removed in the same edit, but only when no claim or
    relationship cites any of those results; a condition selector, claim or
    relationship that still names the artifact always refuses.
    """
    result_refs = [
        result
        for result in raw.get("results", [])
        if result.get("artifact_id") == entry_id
    ]
    used: list[str] = []
    if not with_results:
        used.extend(f"result {result.get('id')!r}" for result in result_refs)
    else:
        cited: list[str] = []
        for result in result_refs:
            refs = _citations(raw, result.get("id"))
            if refs:
                cited.append(f"result {result.get('id')!r} ({', '.join(refs)})")
        if cited:
            raise AuthoringError(
                f"artifact {entry_id!r} has results that are still cited: "
                f"{', '.join(cited)}; remove or edit those references first"
            )
    for run in raw.get("runs", []):
        conditions = run.get("conditions")
        if not isinstance(conditions, dict):
            continue
        for field in _CONDITION_ARTIFACT_FIELDS:
            if conditions.get(field) == entry_id:
                used.append(f"run {run.get('id')!r} condition {field!r}")
    used.extend(_citations(raw, entry_id))
    if used:
        raise AuthoringError(
            f"artifact {entry_id!r} is still used by {', '.join(dict.fromkeys(used))}; "
            "remove or edit those references first"
        )
    index = _find(raw, "artifacts", entry_id)
    if with_results:
        raw["results"] = [
            result
            for result in raw.get("results", [])
            if result.get("artifact_id") != entry_id
        ]
    raw["artifacts"].pop(index)
    _validate(raw)


_RUN_EDITABLE = {"name", "started_at", "params", "metadata", "conditions"}


def _validate_run_selectors(raw: dict, run_id: str) -> None:
    from .conditions_authoring import ConditionsError, _validate_selectors
    from .manifest import validate_manifest

    manifest = validate_manifest(raw)
    run = next((item for item in manifest.runs if item.id == run_id), None)
    if run is None or run.conditions is None:
        return
    supplied = {
        key: value
        for key, value in run.conditions.model_dump(mode="json").items()
        if value is not None
    }
    try:
        _validate_selectors(supplied, manifest, run_id)
    except ConditionsError:
        raise AuthoringError(
            "condition selectors must name an existing non-directory artifact of the "
            "expected kind that is an input to the run"
        ) from None


def add_run(raw: dict, entry: dict[str, Any]) -> None:
    entry = dict(entry)
    _text(entry, "id", "a run")
    if entry["id"] in _ids(raw):
        raise AuthoringError(f"ID {entry['id']!r} is already used")
    raw.setdefault("runs", []).append(entry)
    _validate(raw)
    _validate_run_selectors(raw, entry["id"])


def edit_run(raw: dict, entry_id: str, patch: dict[str, Any]) -> None:
    index = _find(raw, "runs", entry_id)
    run = raw["runs"][index]
    for key, value in patch.items():
        if key == "id":
            continue
        if key not in _RUN_EDITABLE:
            raise AuthoringError(f"unknown run field {key!r}")
        run[key] = value
    _validate(raw)
    _validate_run_selectors(raw, entry_id)


def remove_run(raw: dict, entry_id: str) -> None:
    used: list[str] = []
    for artifact in raw.get("artifacts", []):
        if artifact.get("run_id") == entry_id:
            used.append(f"artifact {artifact.get('id')!r}")
    for result in raw.get("results", []):
        if result.get("run_id") == entry_id:
            used.append(f"result {result.get('id')!r}")
    for relationship in raw.get("relationships", []):
        if relationship.get("source") == entry_id or relationship.get("target") == entry_id:
            used.append(f"relationship {relationship.get('source')!r} -> {relationship.get('target')!r}")
    for evidence in raw.get("evidence", []):
        for key in ("supports", "contradicts"):
            if entry_id in (evidence.get(key) or []):
                used.append(f"claim {evidence.get('id')!r}")
    if used:
        raise AuthoringError(
            f"run {entry_id!r} is still used by " + ", ".join(used)
            + "; remove or edit those references first"
        )
    raw["runs"].pop(_find(raw, "runs", entry_id))
    _validate(raw)


def _citations(raw: dict, target: str) -> list[str]:
    found: list[str] = []
    for evidence in raw.get("evidence", []):
        if target in (evidence.get("supports") or []) or target in (evidence.get("contradicts") or []):
            found.append(f"claim {evidence.get('id')!r}")
    for relationship in raw.get("relationships", []):
        if target in (relationship.get("source"), relationship.get("target")):
            found.append("a relationship")
    return found


def _refuse_if_cited(raw: dict, target: str, what: str) -> None:
    citations = _citations(raw, target)
    if citations:
        raise AuthoringError(
            f"{what} {target!r} is still cited by {', '.join(dict.fromkeys(citations))}; "
            "remove those references first"
        )


def remove_result(raw: dict, entry_id: str) -> None:
    _refuse_if_cited(raw, entry_id, "result")
    raw["results"].pop(_find(raw, "results", entry_id))
    _validate(raw)


def add_evidence(raw: dict, entry: dict[str, Any]) -> None:
    entry = dict(entry)
    for key in ("id", "claim"):
        _text(entry, key, "a claim")
    if entry["id"] in _ids(raw):
        raise AuthoringError(f"ID {entry['id']!r} is already used")
    _check_refs(raw, entry, ("supports", "contradicts"))
    raw.setdefault("evidence", []).append(entry)
    _validate(raw)


def edit_evidence(raw: dict, entry_id: str, patch: dict[str, Any]) -> None:
    index = _find(raw, "evidence", entry_id)
    evidence = raw["evidence"][index]
    for key, value in patch.items():
        if key == "id":
            continue
        evidence[key] = value
    _text(evidence, "claim", "a claim")
    _check_refs(raw, evidence, ("supports", "contradicts"))
    _validate(raw)


def remove_evidence(raw: dict, entry_id: str) -> None:
    _refuse_if_cited(raw, entry_id, "claim")
    raw["evidence"].pop(_find(raw, "evidence", entry_id))
    _validate(raw)


def add_relationship(raw: dict, entry: dict[str, Any]) -> None:
    entry = dict(entry)
    for key in ("source", "target", "type"):
        _text(entry, key, "a relationship")
    known = _ids(raw)
    for key in ("source", "target"):
        if entry[key] not in known:
            raise AuthoringError(f"{key} {entry[key]!r} is not in the manifest")
    raw.setdefault("relationships", []).append(entry)
    _validate(raw)


def remove_relationship(raw: dict, index: int) -> None:
    relationships = raw.get("relationships", [])
    if index < 0 or index >= len(relationships):
        raise AuthoringError("relationship index is out of range")
    relationships.pop(index)
    _validate(raw)


def read_json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuthoringError(f"cannot read {path}: {exc}") from None
    if not isinstance(data, dict):
        raise AuthoringError("the file must contain one JSON object")
    return data
