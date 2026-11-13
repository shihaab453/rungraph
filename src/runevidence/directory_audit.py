"""Internal bounded advisory rehash audit; no public entry point imports this.

Each recorded directory goes through the existing planner exactly once. Its
checked preview is retained verbatim, with no persistence or lock inspection.
Source comparisons are ordinary reads, not a multi-directory snapshot or CAS.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from .directory_plan import plan_directory_change
from .directory_preview import DirectoryPreview
from .edit import EditError
from .manifest import LoadedManifest, load_raw
from .models import Manifest

MAX_DIRECTORIES = 16
MAX_AUDIT_JSON_BYTES = 1_048_576  # default json.dumps(asdict(report)), UTF-8
_REASONS = frozenset((
    "invalid-path", "limit-exceeded", "invalid-artifact", "unknown-run",
    "volatile-unsupported", "path-collision", "overlap", "not-found",
    "not-a-directory-artifact", "stale", "locator-unsupported", "scan-failed",
    "recheck-failed", "stale-tree", "proposal-mismatch", "invalid-request",
))
_SCAN_REASONS = frozenset((
    "unsupported-platform", "invalid-root", "unsafe", "limit-exceeded",
    "collision", "unstable", "missing", "unreadable", "not-a-directory", "unsupported-filesystem",
))
_FATAL_REASONS = frozenset(("stale-source", "loaded-mismatch", "invalid-manifest", "duplicate-id"))
_CAVEATS = (
    "Advisory read-only audit under quiet-tree assumptions.",
    "Non-atomic across directories: previews may describe different instants.",
    "Source-byte reads do not defeat ABA, races or nonparticipating writers.",
    "Later action must independently revalidate each directory and the manifest.",
    "Would-change suggests reviewing bytes and metadata, not accepting a digest.",
    "Unchanged is not a cryptographic authenticity certificate or save approval.",
    "Refused entries prevent any all-clear inference; no records is not an integrity assertion.",
)


class DirectoryAuditError(EditError):
    """Fatal audit refusal; no complete report is available."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__("Directory audit refused: " + reason)


@dataclass(frozen=True)
class DirectoryAuditEntry:
    artifact_id: str  # display only, at most 160 characters
    display_truncated: bool
    status: Literal["unchanged", "would-change", "refused"]
    preview: DirectoryPreview | None
    reason: str | None
    authoring_reason: str | None
    scan_reason: str | None


@dataclass(frozen=True)
class DirectoryAudit:
    state: Literal["no-directory-records", "advisory", "refused"]
    entries: tuple[DirectoryAuditEntry, ...]
    unchanged: int
    would_change: int
    refused: int
    total: int
    caveats: tuple[str, ...]


def audit_recorded_directories(loaded: LoadedManifest) -> DirectoryAudit:
    """Return sorted checked previews or bounded per-directory refusals.

    Capacity, source/model invariants and stale source are fatal, including
    for an empty directory set. Expected EditError refusals are never retried;
    unexpected exceptions propagate. All existing preview caveats still apply.
    """
    current = _model(loaded)
    _source(loaded)
    ids = [current.project.id, *(r.id for r in current.runs),
           *(a.id for a in current.artifacts), *(r.id for r in current.results),
           *(e.id for e in current.evidence)]
    if len(ids) != len(set(ids)):
        raise DirectoryAuditError("duplicate-id")
    directories = sorted(a.id for a in current.artifacts if a.artifact_type == "directory")
    if len(directories) > MAX_DIRECTORIES:
        raise DirectoryAuditError("capacity-exceeded")

    entries = []
    for artifact_id in directories:
        label = artifact_id if len(artifact_id) <= 160 else artifact_id[:157] + "..."
        truncated = len(artifact_id) > 160
        try:
            preview = plan_directory_change(loaded, operation="rehash", artifact_id=artifact_id)
        except EditError as exc:
            reason = getattr(exc, "reason", None)
            if type(reason) is str and reason in _FATAL_REASONS:
                raise
            entries.append(DirectoryAuditEntry(
                label, truncated, "refused", None, _code(reason, _REASONS),
                _code(getattr(exc, "authoring_reason", None), _REASONS, optional=True),
                _code(getattr(exc, "scan_reason", None), _SCAN_REASONS, optional=True),
            ))
        else:
            entries.append(DirectoryAuditEntry(label, truncated, preview.status, preview, None, None, None))

    # Check even an empty audit and after a last-item refusal. Revalidate the
    # mutable loaded model too; ordinary reads provide no global atomicity.
    _model(loaded)
    _source(loaded)
    unchanged = sum(e.status == "unchanged" for e in entries)
    changed = sum(e.status == "would-change" for e in entries)
    refused = sum(e.status == "refused" for e in entries)
    report = DirectoryAudit(
        "refused" if refused else "advisory" if entries else "no-directory-records",
        tuple(entries), unchanged, changed, refused, len(entries), _CAVEATS,
    )
    if len(json.dumps(asdict(report)).encode("utf-8")) > MAX_AUDIT_JSON_BYTES:
        raise DirectoryAuditError("report-capacity-exceeded")
    return report


def _model(loaded: LoadedManifest) -> Manifest:
    # Importing the writer's private helpers would expand protected importer
    # lists. Use the ordinary parsed model and source-byte contract directly.
    if (type(loaded) is not LoadedManifest or type(loaded.manifest) is not Manifest
            or not isinstance(loaded.path, Path) or not loaded.path.is_absolute()
            or type(loaded.source_bytes) is not bytes):
        raise DirectoryAuditError("loaded-mismatch")
    try:
        current = Manifest.model_validate(load_raw(loaded))
    except (ValueError, TypeError, ValidationError):
        raise DirectoryAuditError("loaded-mismatch") from None
    if current != loaded.manifest:
        raise DirectoryAuditError("loaded-mismatch")
    return current


def _source(loaded: LoadedManifest) -> None:
    try:
        same = loaded.path.read_bytes() == loaded.source_bytes
    except OSError:
        same = False
    if not same:
        raise DirectoryAuditError("stale-source")


def _code(value: object, allowed: frozenset[str], *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    return value if type(value) is str and value in allowed else "refused"
