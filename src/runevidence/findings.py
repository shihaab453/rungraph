"""Findings: the single output type of every check."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class Severity(str, Enum):
    error = "error"  # evidence is broken; validate exits 1
    warning = "warning"  # works here, but fragile or incomplete
    info = "info"  # worth knowing, never fails a check


SEVERITY_ORDER = {Severity.error: 0, Severity.warning: 1, Severity.info: 2}


class Code(str, Enum):
    """Stable, greppable finding codes. Never rename one; add new ones instead."""

    # loading
    manifest_not_found = "manifest-not-found"
    manifest_unreadable = "manifest-unreadable"
    schema_invalid = "schema-invalid"
    member_check_failed = "member-check-failed"
    # local files
    missing_file = "missing-file"
    not_a_file = "not-a-file"
    hash_mismatch = "hash-mismatch"
    size_mismatch = "size-mismatch"
    no_digest = "no-digest"
    absolute_path = "absolute-path"
    non_posix_path = "non-posix-path"
    path_escapes_root = "path-escapes-root"
    path_case_mismatch = "path-case-mismatch"
    untracked_file = "untracked-file"
    # local directories (internal opt-in; no command reports these yet)
    missing_directory = "missing-directory"
    not_a_directory = "not-a-directory"
    directory_hash_mismatch = "directory-hash-mismatch"
    directory_size_mismatch = "directory-size-mismatch"
    directory_no_digest = "directory-no-digest"
    directory_unsafe = "directory-unsafe"
    directory_unreadable = "directory-unreadable"
    directory_unstable = "directory-unstable"
    directory_limit_exceeded = "directory-limit-exceeded"
    directory_path_collision = "directory-path-collision"
    # recorded values vs file contents
    result_value_mismatch = "result-value-mismatch"
    result_unverifiable = "result-unverifiable"
    directory_locator_unsupported = "directory-locator-unsupported"
    locator_verifier_unavailable = "locator-verifier-unavailable"
    # verified value locators (schema 0.3)
    locator_kind_mismatch = "locator-kind-mismatch"
    locator_mismatch = "locator-mismatch"
    locator_unreadable = "locator-unreadable"
    locator_unsupported_value = "locator-unsupported-value"
    artifact_changed = "artifact-changed"
    locator_no_digest = "locator-no-digest"
    locator_unverified = "locator-unverified"
    result_unverified = "result-unverified"
    claim_unverified = "claim-unverified"
    # references and relationships
    duplicate_id = "duplicate-id"
    dangling_reference = "dangling-reference"
    invalid_relationship = "invalid-relationship"
    conflicting_provenance = "conflicting-provenance"
    conflicting_evidence = "conflicting-evidence"
    derivation_cycle = "derivation-cycle"
    unsupported_claim = "unsupported-claim"
    claim_at_risk = "claim-at-risk"
    # recorded run conditions (schema 0.2 selectors)
    condition_reference_missing = "condition-reference-missing"
    condition_reference_wrong_kind = "condition-reference-wrong-kind"
    condition_reference_unsupported = "condition-reference-unsupported"
    condition_reference_not_consumed = "condition-reference-not-consumed"


class Finding(BaseModel):
    code: Code
    severity: Severity
    message: str
    subject: Optional[str] = None  # entity id the finding is about
    path: Optional[str] = None  # file path as written in the manifest
    hint: Optional[str] = None  # what to do about it

    def sort_key(self) -> tuple:
        # Codes sort in declaration order, so root causes (file problems) are
        # listed before their consequences (claims at risk).
        return (SEVERITY_ORDER[self.severity], _CODE_ORDER[self.code], self.subject or "", self.path or "")


_CODE_ORDER = {code: i for i, code in enumerate(Code)}
