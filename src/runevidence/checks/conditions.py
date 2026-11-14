"""Recorded run-condition selector checks (schema 0.2).

Every selector a run declares is resolved against the manifest, whether or not
that run is selected for comparison. Comparison must never be the first place
a dangling, wrong-kind or unattached selector is noticed.

One finding is reported per invalid selector, at the first failing rule:

1. the artifact ID is absent from ``manifest.artifacts``
2. the artifact is a directory (comparison rejects directory artifacts)
3. the artifact's ``kind`` does not match the selector's expected kind
4. the artifact is not attached to the run by an ``input_to`` relationship

Findings never echo the artifact ID, run ID, selector value, path, metadata or
recorded values: messages and hints are fixed text and ``subject``/``path`` are
always None.
"""

from __future__ import annotations

from ..findings import Code, Finding, Severity
from ..models import Artifact, Manifest, RelationshipType

DATASET, CHECKPOINT = "dataset", "checkpoint"

# Selector attribute on RunConditions, and the artifact kind it must name.
SELECTORS: tuple[tuple[str, str], ...] = (
    ("evaluation_dataset_artifact_id", DATASET),
    ("model_checkpoint_artifact_id", CHECKPOINT),
)

_FINDINGS: dict[Code, tuple[str, str]] = {
    Code.condition_reference_missing: (
        "A run condition selector refers to an artifact that is not in the manifest.",
        "Add the artifact to the manifest or correct the selector.",
    ),
    Code.condition_reference_wrong_kind: (
        "A run condition selector refers to an artifact of the wrong kind.",
        "Select an artifact whose recorded kind matches the condition.",
    ),
    Code.condition_reference_unsupported: (
        "A run condition selector refers to a directory artifact, which comparison does not support.",
        "Select a file artifact instead of a directory artifact.",
    ),
    Code.condition_reference_not_consumed: (
        "A run condition selector refers to an artifact that is not an input to its run.",
        "Record an input_to relationship from the artifact to the run.",
    ),
}


def _finding(code: Code) -> Finding:
    message, hint = _FINDINGS[code]
    return Finding(code=code, severity=Severity.error, message=message, hint=hint)


def check_conditions(manifest: Manifest) -> list[Finding]:
    findings: list[Finding] = []
    artifacts: dict[str, Artifact] = {}
    for artifact in manifest.artifacts:
        artifacts.setdefault(artifact.id, artifact)

    consumed = {
        (relationship.source, relationship.target)
        for relationship in manifest.relationships
        if relationship.type is RelationshipType.input_to
    }

    for run in manifest.runs:
        conditions = run.conditions
        if conditions is None:
            continue
        for attribute, expected_kind in SELECTORS:
            artifact_id = getattr(conditions, attribute)
            if artifact_id is None:
                continue
            artifact = artifacts.get(artifact_id)
            if artifact is None:
                findings.append(_finding(Code.condition_reference_missing))
            elif getattr(artifact, "artifact_type", "file") == "directory":
                findings.append(_finding(Code.condition_reference_unsupported))
            elif getattr(artifact, "kind", None) != expected_kind:
                findings.append(_finding(Code.condition_reference_wrong_kind))
            elif (artifact_id, run.id) not in consumed:
                findings.append(_finding(Code.condition_reference_not_consumed))
    return findings
