"""Internal one-call advisory review using the checked services and presenter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .directory_audit import DirectoryAudit, audit_recorded_directories
from .directory_batch import DirectoryBatchReview, DirectoryChangeRequest, review_directory_changes
from .directory_present import render_directory_review
from .manifest import LoadedManifest

_CAVEATS = (
    "Internal advisory read-only result; no write authorization is granted.",
    "Text merely displays supplied data; scanning is performed only by the delegated checked service.",
    "Separate member scans and directory checks are non-atomic; a preview may already be stale.",
    "Neither unchanged nor would-change means authorized to apply.",
    "Any later action requires fresh validation of the manifest and directories.",
)


@dataclass(frozen=True)
class DirectoryOperatorReview:
    mode: Literal["audit", "batch"]
    report: DirectoryAudit | DirectoryBatchReview
    text: str
    caveats: tuple[str, ...]


def review_directories_for_operator(
    loaded: LoadedManifest,
    *,
    requests: tuple[DirectoryChangeRequest, ...] | None = None,
) -> DirectoryOperatorReview:
    """Delegate once, then render the identical report, propagating all errors.

    None audits recorded directories; an explicit empty tuple reviews an empty
    batch. The delegated workflow performs necessary read-only filesystem I/O.
    Ordinary validation reads can race and establish no atomic snapshot.
    """
    if requests is None:
        mode = "audit"
        report = audit_recorded_directories(loaded)
    else:
        mode = "batch"
        report = review_directory_changes(loaded, requests)
    text = render_directory_review(report)
    return DirectoryOperatorReview(mode, report, text, _CAVEATS)
