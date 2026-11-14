"""Policy for supplied directory scan outcomes.

The public read-only ``validate``/``inspect --directory-artifacts`` opt-in
uses this module after the scanner has produced an outcome. It does no
filesystem access and performs no writing or authoring.
"""

from dataclasses import dataclass
from typing import Literal, Optional, Union

from ..directory_scanner import DirectoryScan, DirectoryScanError
from ..models import Artifact, DIRECTORY_DIGEST_FORMAT


@dataclass(frozen=True)
class DirectoryPolicyResult:
    status: Literal["match", "incomplete", "changed", "scan_error", "needs_root_check"]
    broken: bool
    reason: str
    digest_matches: Optional[bool] = None
    size_matches: Optional[bool] = None
    members: Optional[int] = None


def check_directory_policy(
    artifact: Artifact, outcome: Union[DirectoryScan, DirectoryScanError, None]
) -> DirectoryPolicyResult:
    """Interpret an already obtained outcome; never scan or mutate anything."""
    if not isinstance(artifact, Artifact) or artifact.artifact_type != "directory":
        raise ValueError("directory artifact required")
    if artifact.digest_format != DIRECTORY_DIGEST_FORMAT:
        raise ValueError("directory digest format is invalid")
    if outcome is not None and not isinstance(outcome, (DirectoryScan, DirectoryScanError)):
        raise TypeError("outcome must be a directory scan, scan error, or None")
    if isinstance(outcome, DirectoryScanError):
        return DirectoryPolicyResult("scan_error", True, outcome.reason)
    if artifact.volatile:
        return DirectoryPolicyResult("needs_root_check", True, "safe root check not implemented")
    if outcome is None:
        return DirectoryPolicyResult("scan_error", True, "scan not supplied")
    digest = outcome.digest
    digest_matches = None if artifact.sha256 is None else artifact.sha256 == digest.sha256
    size_matches = None if artifact.size_bytes is None else artifact.size_bytes == digest.size_bytes
    if digest_matches is False or size_matches is False:
        return DirectoryPolicyResult("changed", True, "digest or size mismatch", digest_matches, size_matches, digest.members)
    if digest_matches is None:
        return DirectoryPolicyResult("incomplete", False, "recorded digest missing", digest_matches, size_matches, digest.members)
    return DirectoryPolicyResult("match", False, "digest and size match", digest_matches, size_matches, digest.members)
