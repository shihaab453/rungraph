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


def _validate(raw: dict) -> None:
    try:
        validate_manifest(raw)
    except Exception as exc:
        raise AuthoringError(f"the edited manifest is not valid: {exc}") from None


def _text(entry: dict, key: str, what: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value:
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
