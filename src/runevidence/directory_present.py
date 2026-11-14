"""Pure internal display of supplied advisory records, never verification."""

from __future__ import annotations

import json
from typing import get_type_hints

from .directory_audit import (
    DirectoryAudit, DirectoryAuditEntry, DirectoryPreview,
    _CAVEATS as _AUDIT_CAVEATS, _REASONS, _SCAN_REASONS,
)
from .directory_batch import DirectoryBatchReview, _CAVEATS as _BATCH_CAVEATS
from .edit import EditError

MAX_RENDER_BYTES = 262_144
# Resolve the existing nested record type through the permitted report API.
# No second schema, direct preview dependency, or caller-controlled type lookup.
_IDS_TYPE = get_type_hints(DirectoryPreview)["linked_results"]
_PREVIEW_CAVEATS = (
    "Preview only, not a write authorization.",
    "Cooperative quiet-tree, no CAS/ACL guarantee.",
    "Later operations must independently revalidate the manifest and directory.",
    "Member count describes coverage, not integrity verification.",
    "Linked result values and claims were not re-read, accepted or re-recorded.",
    "Child file artifacts keep their own checks and were not rehashed.",
    "Display strings may be truncated where explicitly flagged; no cryptographic seal is provided.",
)
_DISPLAY_CAVEATS = (
    "Display of supplied advisory data, not independent verification or authentication of origin.",
    "Rendering makes no fresh source/tree check and never grants write authorization.",
    "A forged or stale record does not establish that any scan happened; non-atomic advisory caveats still apply.",
    "Impact IDs are identifiers only, not result values or claim content.",
)


class DirectoryPresentationError(EditError):
    """Fixed safe refusal, without a partial report or caller data."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__("Directory presentation refused: " + reason)


def _need(ok: bool) -> None:
    if not ok:
        raise DirectoryPresentationError("invalid-report")


def _count(value: object) -> None:
    _need(type(value) is int and value >= 0)


def _label(value: object, limit: int) -> None:
    _need(type(value) is str and 0 < len(value) <= limit)


def _choice(value: object, choices: tuple[str, ...]) -> None:
    _need(type(value) is str and value in choices)


def _caveats(value: object, required: tuple[str, ...]) -> None:
    _need(type(value) is tuple and all(type(s) is str and s for s in value))
    _need(all(s in value for s in required))


def _digest(value: object) -> None:
    _need(type(value) is str and len(value) == 64
          and all(c in "0123456789abcdef" for c in value))


def _impact(group: object) -> None:
    _need(type(group) is _IDS_TYPE)
    _need(type(group.ids) is tuple and len(group.ids) <= 16)
    for label in group.ids:
        _label(label, 160)
    _count(group.total)
    _count(group.omitted)
    _need(type(group.truncated) is bool)
    _need(len(group.ids) == min(group.total, 16)
          and group.omitted == group.total - len(group.ids))
    # Sorted distinct source IDs may collide or sort differently after clipping.
    if not group.truncated:
        _need(group.ids == tuple(sorted(set(group.ids))))
    else:
        _need(any(len(s) == 160 and s.endswith("...") for s in group.ids))


def _preview(preview: object) -> None:
    _need(type(preview) is DirectoryPreview)
    _choice(preview.operation, ("add", "rehash"))
    _choice(preview.status, ("would-change", "unchanged"))
    _label(preview.artifact_id, 160)
    _label(preview.path, 1024)
    _need(type(preview.display_truncated) is bool)
    if preview.display_truncated:
        _need(any(len(s) == limit and s.endswith("...")
                  for s, limit in ((preview.artifact_id, 160), (preview.path, 1024))))
    _digest(preview.new_sha256)
    for n in (preview.new_size, preview.members):
        _count(n)
    _need(preview.members != 0 or preview.new_size == 0)
    if preview.operation == "add":
        _need(preview.old_sha256 is None and preview.old_size is None
              and preview.status == "would-change")
    else:
        # Existing directory records may lack either old measurement.
        if preview.old_sha256 is not None:
            _digest(preview.old_sha256)
        if preview.old_size is not None:
            _count(preview.old_size)
        same = (preview.old_sha256 == preview.new_sha256 and preview.old_size == preview.new_size)
        _need(preview.status == ("unchanged" if same else "would-change"))
    for group in (preview.linked_results, preview.claims, preview.overlapping_files):
        _impact(group)
    _caveats(preview.caveats, _PREVIEW_CAVEATS)


def _audit(report: DirectoryAudit) -> None:
    _choice(report.state, ("no-directory-records", "advisory", "refused"))
    _need(type(report.entries) is tuple and len(report.entries) <= 16)
    for n in (report.total, report.unchanged, report.would_change, report.refused):
        _count(n)
    statuses = []
    for entry in report.entries:
        _need(type(entry) is DirectoryAuditEntry)
        _label(entry.artifact_id, 160)
        _need(type(entry.display_truncated) is bool)
        if entry.display_truncated:
            _need(len(entry.artifact_id) == 160 and entry.artifact_id.endswith("..."))
        _choice(entry.status, ("unchanged", "would-change", "refused"))
        statuses.append(entry.status)
        if entry.status == "refused":
            _need(entry.preview is None)
            _choice(entry.reason, (*_REASONS, "refused"))
            if entry.authoring_reason is not None:
                _choice(entry.authoring_reason, (*_REASONS, "refused"))
            if entry.scan_reason is not None:
                _choice(entry.scan_reason, (*_SCAN_REASONS, "refused"))
        else:
            _need(entry.reason is None and entry.authoring_reason is None and entry.scan_reason is None)
            _preview(entry.preview)
            _need(entry.preview.operation == "rehash" and entry.preview.status == entry.status
                  and entry.preview.artifact_id == entry.artifact_id)
            if entry.display_truncated:
                _need(entry.preview.display_truncated)
    _need(report.total == len(statuses)
          and report.unchanged == statuses.count("unchanged")
          and report.would_change == statuses.count("would-change")
          and report.refused == statuses.count("refused"))
    _need(report.state == ("refused" if report.refused else "advisory" if report.total else "no-directory-records"))
    _caveats(report.caveats, _AUDIT_CAVEATS)


def _batch(report: DirectoryBatchReview) -> None:
    _choice(report.state, ("no-requests", "advisory"))
    _need(type(report.previews) is tuple and len(report.previews) <= 8)
    for n in (report.total, report.adds, report.rehashes, report.would_change, report.unchanged):
        _count(n)
    for preview in report.previews:
        _preview(preview)
    _need(report.total == len(report.previews)
          and report.adds == sum(p.operation == "add" for p in report.previews)
          and report.rehashes == report.total - report.adds
          and report.would_change == sum(p.status == "would-change" for p in report.previews)
          and report.unchanged == report.total - report.would_change)
    _need(report.state == ("advisory" if report.total else "no-requests"))
    _caveats(report.caveats, _BATCH_CAVEATS)


def _decimal(value: int) -> str:
    # Avoid the interpreter's configurable decimal conversion limit without
    # modifying process globals. Huge in-memory integers still fail boundedly.
    if value.bit_length() > MAX_RENDER_BYTES * 4:
        raise DirectoryPresentationError("render-capacity-exceeded")
    parts = []
    while value >= 1_000_000_000:
        value, part = divmod(value, 1_000_000_000)
        parts.append(f"{part:09d}")
    return str(value) + "".join(reversed(parts))


def render_directory_review(report: DirectoryAudit | DirectoryBatchReview) -> str:
    """Display supplied advisory data as bounded, escaped text, with no I/O.

    Shape validation cannot authenticate origin or establish that checks ran.
    Invalid shape or capacity refuses without returning partial output.
    """
    try:
        if type(report) is DirectoryAudit:
            _audit(report)
        elif type(report) is DirectoryBatchReview:
            _batch(report)
        else:
            raise DirectoryPresentationError("invalid-report")
    except AttributeError:
        # An exact frozen dataclass can still be forged with missing fields.
        raise DirectoryPresentationError("invalid-report") from None

    lines = []
    size = 0

    def emit(label: str, **fields: object) -> None:
        nonlocal size
        values = [k + "=" + (_decimal(v) if type(v) is int else json.dumps(v, ensure_ascii=True))
                  for k, v in fields.items()]
        line = label + (" " + " ".join(values) if values else "") + "\n"
        size += len(line.encode("utf-8"))
        if size > MAX_RENDER_BYTES:
            raise DirectoryPresentationError("render-capacity-exceeded")
        lines.append(line)

    def warnings(scope: str, caveats: tuple[str, ...]) -> None:
        for text in caveats:
            emit("caveat", scope=scope, text=text)

    def show_preview(preview: DirectoryPreview) -> None:
        emit("preview", operation=preview.operation, status=preview.status,
             artifact_id=preview.artifact_id, path=preview.path,
             display_truncated=preview.display_truncated)
        emit("digest", old=preview.old_sha256, new=preview.new_sha256)
        emit("logical-size", old=preview.old_size, new=preview.new_size, members=preview.members)
        for label, group in (("linked-results", preview.linked_results), ("claims", preview.claims),
                             ("child-file-overlaps", preview.overlapping_files)):
            emit("impact", group=label, ids=group.ids, shown=len(group.ids), total=group.total,
                 omitted=group.omitted, truncated=group.truncated)
        warnings("preview", preview.caveats)

    warnings("formatter", _DISPLAY_CAVEATS)
    if type(report) is DirectoryAudit:
        emit("audit", state=report.state, total=report.total, unchanged=report.unchanged,
             would_change=report.would_change, refused=report.refused)
        warnings("audit", report.caveats)
        for index, entry in enumerate(report.entries, 1):
            emit("entry", index=index, status=entry.status, artifact_id=entry.artifact_id,
                 display_truncated=entry.display_truncated)
            if entry.preview is None:
                emit("refusal", reason=entry.reason, authoring_reason=entry.authoring_reason,
                     scan_reason=entry.scan_reason)
            else:
                show_preview(entry.preview)
    else:
        emit("batch", state=report.state, total=report.total, adds=report.adds,
             rehashes=report.rehashes, would_change=report.would_change, unchanged=report.unchanged)
        warnings("batch", report.caveats)
        for index, preview in enumerate(report.previews, 1):
            emit("request", index=index)
            show_preview(preview)
    result = "".join(lines)
    if len(result.encode("utf-8")) > MAX_RENDER_BYTES:
        raise DirectoryPresentationError("render-capacity-exceeded")
    return result
