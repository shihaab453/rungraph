"""Compare recorded result values with the value inside their source file.

Only JSON artifacts are read, with the standard-library parser: it builds
plain dicts and lists and cannot execute anything.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from ..findings import Code, Finding, Severity
from ..models import Manifest

MAX_JSON_BYTES = 50 * 1024 * 1024
_MISSING = object()


def resolve_pointer(doc: Any, pointer: str) -> Any:
    """RFC 6901 JSON Pointer lookup. Returns _MISSING if it does not resolve."""
    if pointer == "":
        return doc
    if not pointer.startswith("/"):
        return _MISSING
    current = doc
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            return _MISSING
    return current


def values_match(recorded: Any, actual: Any) -> bool:
    numeric = (int, float)
    if isinstance(recorded, bool) or isinstance(actual, bool):
        return recorded is actual
    if isinstance(recorded, numeric) and isinstance(actual, numeric):
        return math.isclose(recorded, actual, rel_tol=1e-9, abs_tol=1e-12)
    return recorded == actual


def check_results(
    manifest: Manifest, ok_paths: dict[str, Path], broken_artifacts: set[str]
) -> tuple[list[Finding], set[str]]:
    findings: list[Finding] = []
    mismatched: set[str] = set()
    artifact_ids = {a.id for a in manifest.artifacts}

    def unverifiable(res_id: str, msg: str) -> None:
        findings.append(
            Finding(code=Code.result_unverifiable, severity=Severity.warning, subject=res_id, message=msg)
        )

    cache: dict[str, Any] = {}
    for res in manifest.results:
        if res.locator is None:
            continue
        if res.artifact_id is None:
            unverifiable(res.id, f"Result {res.id!r} has a locator but no artifact_id to read it from")
            continue
        if res.artifact_id not in artifact_ids or res.artifact_id in broken_artifacts:
            continue  # already reported as dangling / missing / changed
        path = ok_paths.get(res.artifact_id)
        if path is None:
            continue
        if path.suffix.lower() != ".json":
            unverifiable(res.id, f"Result {res.id!r}: locators are only supported for .json files ({path.name})")
            continue
        if res.artifact_id not in cache:
            try:
                if path.stat().st_size > MAX_JSON_BYTES:
                    raise ValueError(f"file larger than {MAX_JSON_BYTES} bytes")
                cache[res.artifact_id] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                cache[res.artifact_id] = exc
        doc = cache[res.artifact_id]
        if isinstance(doc, Exception):
            unverifiable(res.id, f"Result {res.id!r}: could not parse {path.name} as JSON: {doc}")
            continue
        actual = resolve_pointer(doc, res.locator)
        if actual is _MISSING:
            unverifiable(res.id, f"Result {res.id!r}: locator {res.locator!r} not found in {path.name}")
            continue
        if not values_match(res.value, actual):
            mismatched.add(res.id)
            findings.append(
                Finding(
                    code=Code.result_value_mismatch,
                    severity=Severity.error,
                    subject=res.id,
                    message=(
                        f"Result {res.id!r} ({res.name}) records {res.value!r} but "
                        f"{path.name}{res.locator} contains {actual!r}"
                    ),
                )
            )
    return findings, mismatched
