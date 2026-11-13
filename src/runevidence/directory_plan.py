"""Internal, nonpersisting single-call directory change planning.

Only the existing frozen preview escapes this interface. Each valid request
forms a proposal and immediately obtains a fresh checked dry-run preview;
even an unchanged rehash needs both native scans. Quiet-tree assumptions and
all preview caveats apply. A return value never authorizes a later write.
"""

from __future__ import annotations

from typing import Literal

from .directory_authoring import DirectoryAuthoringError, propose_add_directory, propose_rehash_directory
from .directory_preview import DirectoryPreview, preview_directory_proposal
from .manifest import LoadedManifest, load_raw


def plan_directory_change(
    loaded: LoadedManifest,
    *,
    operation: Literal["add", "rehash"],
    artifact_id: str,
    path: str | None = None,
    run_id: str | None = None,
    kind: str | None = None,
) -> DirectoryPreview:
    """Validate a narrow request, then propose and independently check it.

    Invalid types or selector combinations raise DirectoryAuthoringError with
    reason ``invalid-request`` before scanning. Existing authoring/apply errors,
    including their nested scan reasons, propagate without refresh or retry.
    Unknown keywords are rejected by the signature; volatile is unsupported.
    """
    if type(operation) is not str or operation not in ("add", "rehash"):
        raise DirectoryAuthoringError("invalid-request", "Operation must be add or rehash.")
    if type(artifact_id) is not str:
        raise DirectoryAuthoringError("invalid-request", "Artifact ID must be a string.")
    for name, value in (("path", path), ("run_id", run_id), ("kind", kind)):
        if value is not None and type(value) is not str:
            raise DirectoryAuthoringError("invalid-request", f"{name} must be a string or None.")
    if operation == "add" and path is None:
        raise DirectoryAuthoringError("invalid-request", "Add requires an explicit path.")
    if operation == "rehash" and any(value is not None for value in (path, run_id, kind)):
        raise DirectoryAuthoringError("invalid-request", "Rehash accepts only an artifact ID.")

    raw = load_raw(loaded)
    if operation == "add":
        proposal = propose_add_directory(loaded, raw, path=path, artifact_id=artifact_id, run_id=run_id, kind=kind)
    else:
        proposal = propose_rehash_directory(loaded, raw, artifact_id=artifact_id)
    return preview_directory_proposal(loaded, proposal)
