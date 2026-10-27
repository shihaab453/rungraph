"""Domain-agnostic data model for a RunEvidence manifest.

A manifest describes one experiment folder:

* ``project``       what the folder is about
* ``runs``          executions (training, evaluation, preprocessing, anything)
* ``artifacts``     files on disk, each pinned by a sha256 digest
* ``results``       named values a run produced (metrics, scores, counts)
* ``evidence``      human claims, each pointing at the results/artifacts/runs
                    that support or contradict it
* ``relationships`` extra typed edges (``derived_from``, ``input_to``, ...)

Nothing here is specific to any ML domain. Domain-specific detail goes in the
free-form ``metadata`` / ``params`` dictionaries.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

SCHEMA_VERSION = "0.1"
MANIFEST_FILENAME = "runevidence.json"
BACKUP_FILENAME = MANIFEST_FILENAME + ".bak"  # written by `init --force`
# `init --force` never overwrites an earlier backup. The first one is
# runevidence.json.bak (generation 1); later ones are runevidence.json.bak.2,
# runevidence.json.bak.3, and so on. Nothing is ever deleted automatically.
_BACKUP_RE = re.compile(r"^" + re.escape(BACKUP_FILENAME) + r"(?:\.([1-9][0-9]*))?$")


def backup_generation(name: str) -> Optional[int]:
    """The generation number of an ``init --force`` backup file name, else None."""
    match = _BACKUP_RE.match(name)
    if match is None:
        return None
    return int(match.group(1)) if match.group(1) else 1


def is_own_file(rel: str) -> bool:
    """True for RunEvidence's own files at the experiment root: the manifest
    and every backup generation. They are never tracked as artifacts. A file
    with the same name in a subfolder is ordinary content."""
    return rel == MANIFEST_FILENAME or backup_generation(rel) is not None

# IDs are short, path-safe tokens so they can appear in CLI output and URLs.
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"
# Either case is accepted (PowerShell's Get-FileHash prints uppercase) and
# stored lowercase.
SHA256_PATTERN = r"^[0-9a-fA-F]{64}$"

Id = str
Scalar = Union[bool, int, float, str, None]


class _Strict(BaseModel):
    # extra="forbid" turns typos like "sha265" into a schema error instead of
    # silently ignoring them. Put custom fields in ``metadata``.
    model_config = ConfigDict(extra="forbid")


class Project(_Strict):
    id: Id = Field(pattern=ID_PATTERN)
    name: str
    description: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Run(_Strict):
    id: Id = Field(pattern=ID_PATTERN)
    name: Optional[str] = None
    started_at: Optional[datetime] = None
    params: dict[str, Any] = Field(
        default_factory=dict, description="Hyperparameters or settings, free-form."
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class Artifact(_Strict):
    id: Id = Field(pattern=ID_PATTERN)
    path: str = Field(
        min_length=1,
        description="Path relative to the manifest's folder, using forward slashes.",
    )
    sha256: Optional[str] = Field(default=None, pattern=SHA256_PATTERN)
    size_bytes: Optional[int] = Field(default=None, ge=0)
    volatile: bool = Field(
        default=False,
        description=(
            "Content is expected to change on every rerun (e.g. a log with timestamps). "
            "Only the file's existence is checked; sha256 and size_bytes are ignored."
        ),
    )
    kind: Optional[str] = Field(
        default=None,
        description="Free-form label, e.g. dataset, config, checkpoint, metrics, log.",
    )
    run_id: Optional[Id] = Field(default=None, description="Run that produced this file.")
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("sha256")
    @classmethod
    def _lowercase_digest(cls, v: Optional[str]) -> Optional[str]:
        return v.lower() if v is not None else v


class Result(_Strict):
    id: Id = Field(pattern=ID_PATTERN)
    run_id: Id = Field(description="Run this result belongs to.")
    name: str = Field(description="What was measured, e.g. 'accuracy' or 'val_loss'.")
    value: Scalar = None
    artifact_id: Optional[Id] = Field(
        default=None, description="Artifact the value was read from (e.g. a metrics file)."
    )
    locator: Optional[str] = Field(
        default=None,
        description=(
            "RFC 6901 JSON Pointer into a .json artifact (e.g. '/eval/accuracy'). "
            "When set, the recorded value is compared against the file."
        ),
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceReference(_Strict):
    id: Id = Field(pattern=ID_PATTERN)
    claim: str = Field(min_length=1, description="The statement being made, in plain words.")
    supports: list[Id] = Field(
        default_factory=list, description="IDs of results, artifacts or runs backing the claim."
    )
    contradicts: list[Id] = Field(
        default_factory=list, description="IDs of results, artifacts or runs that cut against it."
    )
    source: Optional[str] = Field(
        default=None, description="Where the claim is made, e.g. 'README.md#results'."
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class RelationshipType(str, Enum):
    produced_by = "produced_by"  # artifact -> run
    input_to = "input_to"  # artifact -> run
    result_of = "result_of"  # result -> run
    reported_in = "reported_in"  # result -> artifact
    derived_from = "derived_from"  # artifact -> artifact, run -> run
    supports = "supports"  # evidence -> result | artifact | run
    contradicts = "contradicts"  # evidence -> result | artifact | run


class Relationship(_Strict):
    source: Id
    target: Id
    type: RelationshipType
    note: Optional[str] = None


class Manifest(_Strict):
    schema_version: Literal["0.1"] = SCHEMA_VERSION
    project: Project
    runs: list[Run] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    results: list[Result] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)


def json_schema() -> dict[str, Any]:
    """JSON Schema for the manifest, generated from the Pydantic models."""
    schema = Manifest.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:runevidence:manifest:{SCHEMA_VERSION}"
    return schema
