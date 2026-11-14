"""Internal read-only review of an existing manifest file or containing folder."""

from __future__ import annotations

from pathlib import Path

from .directory_review import DirectoryChangeRequest, DirectoryOperatorReview, review_directories_for_operator
from .manifest import load_manifest


def review_existing_directory_manifest(
    target: Path,
    *,
    requests: tuple[DirectoryChangeRequest, ...] | None = None,
) -> DirectoryOperatorReview:
    """Load once using existing locator rules, then return the checked review.

    target accepts a manifest file or a folder containing the standard manifest,
    exactly as load_manifest does. None audits recorded directories; () reviews
    an empty batch. Requests and all errors pass through unchanged. This advisory
    read is non-atomic, can become stale, and grants no write authorization.
    """
    loaded = load_manifest(target)
    return review_directories_for_operator(loaded, requests=requests)
