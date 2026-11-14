"""Private R7 opt-in read-only view of an R5 staged synthetic manifest. No application imports.

Only an explicit caller reaches this module; no package export, command or hook
does. The view is the actual stage readback wrapped in an ordinary
``LoadedManifest`` whose path is the original synthetic JSON file, so artifact
paths still resolve against the manifest's folder. Never a writer, migration,
cutover or proof that SQLite is canonical. JSON is never a fallback: any stage
failure refuses. The JSON file must match the staged exact bytes and the
receipt's source identity before and after the view is produced. That parity is
advisory and holds only at each observation: it is not an atomic JSON/SQLite
snapshot, a hostile-writer barrier or an authenticated path seal.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import _experimental_versioned_manifest as _stage
from .manifest import LoadedManifest, validate_manifest
from .models import is_manifest

Reason = Literal["stale-source", "invalid-stage", "missing", "busy", "fatal"]
_STAGE_REASONS: dict[str, Reason] = {"invalid": "invalid-stage", "missing": "missing", "busy": "busy",
                                     "fatal": "fatal"}
_FAILURES = (_stage.StageError, OSError, ValueError, TypeError, RecursionError, sqlite3.Error)


class StagedReadError(Exception):
    """Read-only refusal. Messages and diagnostics are fixed strings, never paths."""

    def __init__(self, reason: Reason, subject: Literal["receipt", "stage", "source"], check: str,
                 stage_reason: str | None = None):
        self.reason = reason
        self.subject = subject
        self.check = check
        self.stage_reason = stage_reason
        super().__init__(f"Staged read refused: {reason} ({subject}: {check})")

    @property
    def diagnostics(self) -> dict[str, str | None]:
        return {"reason": self.reason, "subject": self.subject, "check": self.check,
                "stage_reason": self.stage_reason}


@dataclass(frozen=True)
class StagedLoadedRead:
    loaded: LoadedManifest
    stage_revision: int
    stage_generation: int
    stage_sha256: str
    source_path: Path
    parity: Literal["verified-at-observation-only"]


def _refusal(exc: BaseException, subject: Literal["receipt", "stage", "source"], check: str,
             default: Reason) -> StagedReadError:
    if isinstance(exc, StagedReadError):
        return exc
    if isinstance(exc, FileNotFoundError):
        return StagedReadError("missing", subject, check)
    if isinstance(exc, _stage.StageError):
        reason = _STAGE_REASONS.get(exc.reason, "fatal") if subject == "stage" else default
        return StagedReadError(reason, subject, check, exc.reason)
    if isinstance(exc, OSError):
        return StagedReadError("fatal", subject, check)
    return StagedReadError(default, subject, check)


def _receipt_paths(stage: _stage.StagingReceipt) -> tuple[Path, Path]:
    # Bound every later read to the reviewed scratch layout before touching the source.
    if type(stage) is not _stage.StagingReceipt:
        raise StagedReadError("invalid-stage", "receipt", "type")
    database, source = stage.database_path, stage.source_path
    if not isinstance(database, Path) or not isinstance(source, Path) or not source.is_absolute():
        raise StagedReadError("invalid-stage", "receipt", "path-type")
    folder = database.parent
    _stage._scratch_parent(folder.parent)
    if not source.is_relative_to(folder.parent) or source.is_relative_to(folder) or source == database:
        raise StagedReadError("invalid-stage", "receipt", "source-outside-scratch")
    try:
        _stage._ordinary(source, directory=False)
    except FileNotFoundError:
        raise StagedReadError("missing", "source", "layout") from None
    return source, folder


def _stage_state(folder: Path, database: Path) -> tuple[tuple[str, ...], tuple[int, ...]]:
    entries = tuple(sorted(os.listdir(folder)))
    if database.name not in entries:
        raise StagedReadError("missing", "stage", "database")
    if entries != (database.name,):
        raise StagedReadError("invalid-stage", "stage", "unexpected-entry-or-sidecar")
    s = os.lstat(database)
    return entries, (s.st_dev, s.st_ino, s.st_nlink, s.st_size, s.st_mtime_ns)


def _source_signature(source: Path) -> tuple[int, ...]:
    s = _stage._ordinary(source, directory=False)
    return s.st_dev, s.st_ino, s.st_nlink, s.st_size, s.st_mtime_ns


def _view(readback: _stage.StagedReadback, source: Path) -> LoadedManifest:
    # Revalidate the exact BLOB with the loader's parser and existing model contract.
    if type(readback.source_bytes) is not bytes or not is_manifest(readback.manifest):
        raise StagedReadError("invalid-stage", "stage", "readback-type")
    model = validate_manifest(json.loads(readback.source_bytes.decode("utf-8")))
    if model != readback.manifest:
        raise StagedReadError("invalid-stage", "stage", "readback-model")
    return LoadedManifest(manifest=readback.manifest, path=source, source_bytes=readback.source_bytes)


def load_staged_for_read(stage: _stage.StagingReceipt) -> StagedLoadedRead:
    """Return a read-only view of a revision-1 stage only while its JSON source still matches.

    Order: receipt layout, stage folder state, source identity (no content read),
    actual ``read_staged_manifest``, revision/digest parity, opened-handle source
    bytes, view construction, then source bytes and stage folder state again.
    Any change, divergence, link, alias, sidecar or stage failure refuses.
    A stage revision above 1 refuses, because the stage never updates JSON.
    """
    try:
        source, folder = _receipt_paths(stage)
    except _FAILURES as exc:
        raise _refusal(exc, "receipt", "layout", "invalid-stage") from exc
    try:
        before = _stage_state(folder, stage.database_path)
    except _FAILURES as exc:
        raise _refusal(exc, "stage", "state-before", "invalid-stage") from exc
    try:
        if _source_signature(source) != stage.source_identity:
            raise StagedReadError("stale-source", "source", "identity-before")
    except _FAILURES as exc:
        raise _refusal(exc, "source", "identity-before", "stale-source") from exc
    try:
        readback = _stage.read_staged_manifest(stage)
    except _FAILURES as exc:
        raise _refusal(exc, "stage", "readback", "invalid-stage") from exc
    if (readback.revision != 1 or readback.generation != 1 or readback.sha256 != stage.source_sha256
            or len(readback.source_bytes) != stage.source_byte_count):
        raise StagedReadError("stale-source", "stage", "revision-or-digest-divergence")
    try:
        if _stage._read_source(source, readback.source_bytes) != stage.source_identity:
            raise StagedReadError("stale-source", "source", "identity-at-read")
    except _FAILURES as exc:
        raise _refusal(exc, "source", "bytes-at-read", "stale-source") from exc
    try:
        loaded = _view(readback, source)
    except _FAILURES as exc:
        raise _refusal(exc, "stage", "view", "invalid-stage") from exc
    try:
        if _stage._read_source(source, loaded.source_bytes) != stage.source_identity:
            raise StagedReadError("stale-source", "source", "identity-after")
    except _FAILURES as exc:
        raise _refusal(exc, "source", "bytes-after", "stale-source") from exc
    try:
        if _stage_state(folder, stage.database_path) != before:
            raise StagedReadError("invalid-stage", "stage", "state-changed")
    except _FAILURES as exc:
        raise _refusal(exc, "stage", "state-after", "invalid-stage") from exc
    return StagedLoadedRead(loaded, readback.revision, readback.generation, readback.sha256, source,
                            "verified-at-observation-only")
