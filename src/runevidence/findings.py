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
    # recorded values vs file contents
    result_value_mismatch = "result-value-mismatch"
    result_unverifiable = "result-unverifiable"
    # references and relationships
    duplicate_id = "duplicate-id"
    dangling_reference = "dangling-reference"
    invalid_relationship = "invalid-relationship"
    conflicting_provenance = "conflicting-provenance"
    conflicting_evidence = "conflicting-evidence"
    derivation_cycle = "derivation-cycle"
    unsupported_claim = "unsupported-claim"
    claim_at_risk = "claim-at-risk"


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
