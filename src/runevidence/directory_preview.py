"""Internal, read-only summary of an already-created directory proposal.

Every call makes a fresh checked dry run, including the source-byte check and
native rescan. A preview is not reusable authorization: any later operation
must independently revalidate. The quiet-tree/cooperating-writer assumptions
and refusals of that checker still apply. No public entry point imports this.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from .directory_apply import DirectoryApplyError, apply_directory_proposal
from .directory_authoring import DirectoryProposal
from .manifest import LoadedManifest

_ID_CHARS = 160
_PATH_CHARS = 1024
_IMPACT_IDS = 16
_CAVEATS = (
    "Preview only, not a write authorization.",
    "Cooperative quiet-tree, no CAS/ACL guarantee.",
    "Later operations must independently revalidate the manifest and directory.",
    "Member count describes coverage, not integrity verification.",
    "Linked result values and claims were not re-read, accepted or re-recorded.",
    "Child file artifacts keep their own checks and were not rehashed.",
    "Display strings may be truncated where explicitly flagged; no cryptographic seal is provided.",
)


@dataclass(frozen=True)
class DirectoryPreviewIds:
    """Sorted ID sample, never claim text or result values.

    At most 16 IDs of 160 characters each. ``omitted`` counts unsampled IDs;
    ``truncated`` flags shortened sampled strings, which are display labels.
    """

    ids: tuple[str, ...]
    total: int
    omitted: int
    truncated: bool


@dataclass(frozen=True)
class DirectoryPreview:
    operation: Literal["add", "rehash"]
    status: Literal["would-change", "unchanged"]
    artifact_id: str  # display label, at most 160 characters
    path: str  # display label, at most 1024 characters
    display_truncated: bool
    old_sha256: Optional[str]
    new_sha256: str
    old_size: Optional[int]
    new_size: int
    members: int
    linked_results: DirectoryPreviewIds
    claims: DirectoryPreviewIds
    overlapping_files: DirectoryPreviewIds
    caveats: tuple[str, ...]


def preview_directory_proposal(loaded: LoadedManifest, proposal: DirectoryProposal) -> DirectoryPreview:
    """Check again and summarize; never save or inspect a cooperative lock.

    DirectoryApplyError and DirectoryAuthoringError propagate unchanged. The
    checker compares *every* proposal field, including linkage/overlap tuples,
    with an independently rebuilt proposal. Only after that succeeds may those
    tuples supply display IDs under that contract. They are not authenticated
    against arbitrary in-process object forgery or concurrent mutation.
    """
    checked = apply_directory_proposal(loaded, proposal, dry_run=True)
    if checked.status not in ("dry-run", "unchanged") or checked.manifest_path != loaded.path:
        raise DirectoryApplyError("invalid-request", "The preview requires a checked, nonwriting dry-run result.")

    # Bind the extra display metadata to the checked result. In particular,
    # proposal.changed and its mutable raw/entry never decide preview status.
    fields = ("operation", "artifact_id", "path", "old_sha256", "new_sha256", "old_size", "new_size", "members")
    if any(getattr(proposal, name) != getattr(checked, name) for name in fields):
        raise DirectoryApplyError("proposal-mismatch", "Proposal display fields differ from the checked result.")

    return DirectoryPreview(
        operation=checked.operation,
        status="would-change" if checked.status == "dry-run" else "unchanged",
        artifact_id=_clip(checked.artifact_id, _ID_CHARS),
        path=_clip(checked.path, _PATH_CHARS),
        display_truncated=len(checked.artifact_id) > _ID_CHARS or len(checked.path) > _PATH_CHARS,
        old_sha256=checked.old_sha256,
        new_sha256=checked.new_sha256,
        old_size=checked.old_size,
        new_size=checked.new_size,
        members=checked.members,
        linked_results=_ids(proposal.linked_results),
        claims=_ids(proposal.claims),
        overlapping_files=_ids(proposal.overlapping_files),
        caveats=_CAVEATS,
    )


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit - 3] + "..."


def _ids(values: tuple[str, ...]) -> DirectoryPreviewIds:
    # These are already sorted/unique by the checked authoring contract.
    if type(values) is not tuple or any(type(v) is not str for v in values) or values != tuple(sorted(set(values))):
        raise DirectoryApplyError("proposal-mismatch", "Impact IDs do not follow the checked proposal contract.")
    sample = values[:_IMPACT_IDS]
    return DirectoryPreviewIds(
        ids=tuple(_clip(v, _ID_CHARS) for v in sample),
        total=len(values),
        omitted=max(0, len(values) - len(sample)),
        truncated=any(len(v) > _ID_CHARS for v in sample),
    )
