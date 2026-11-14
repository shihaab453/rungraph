"""Private R5 synthetic-only, non-authoritative staging. No application imports.

Callers must create fresh made-up manifests and a fresh scratch parent named
``runevidence-staging-test-<32 lowercase hex digits>`` under current-user Temp.
This Windows gate checks ordinary path metadata, not DACLs or authenticity.
Trusted caller/filesystem and no hostile path swaps are assumptions. Source
readback is advisory, never an atomic JSON/SQLite snapshot or a cutover seal.
No restore, retries, native scanning, JSON saving or production authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from .manifest import LoadedManifest
from .models import Manifest

MAX_SOURCE_BYTES = 256 * 1024
MAX_DB_BYTES = 16 * 1024 * 1024
MAX_RECEIPTS = 32
MAX_REVISION = (1 << 63) - 1
_APPLICATION_ID = 0x52473553
_PARENT = re.compile(r"runevidence-staging-test-[0-9a-f]{32}")
_OPERATION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_STATE_SQL = """CREATE TABLE state (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    store_id TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation=1),
    revision INTEGER NOT NULL CHECK(revision>=1), project_id TEXT NOT NULL,
    schema_version TEXT NOT NULL, source BLOB NOT NULL,
    nbytes INTEGER NOT NULL, sha256 TEXT NOT NULL)"""
_RECEIPT_SQL = """CREATE TABLE receipts (
    operation_id TEXT PRIMARY KEY, expected_revision INTEGER NOT NULL,
    replacement BLOB NOT NULL, nbytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
    result_revision INTEGER NOT NULL UNIQUE)"""


class StageError(Exception):
    """Safe refusal; partial scratch files are deliberately retained."""

    def __init__(self, reason: Literal["missing", "invalid", "busy", "fatal"], message: str):
        self.reason = reason
        super().__init__(message)


@dataclass(frozen=True)
class StagingReceipt:
    database_path: Path
    store_id: str
    project_id: str
    schema_version: str
    generation: int
    revision: int
    source_sha256: str
    source_byte_count: int
    source_path: Path
    source_identity: tuple[int, ...]
    directory_identity: tuple[int, ...]
    database_identity: tuple[int, ...]
    source_readback: Literal["advisory-before-and-after"]


@dataclass(frozen=True)
class StagedReadback:
    source_bytes: bytes
    manifest: Manifest
    revision: int
    generation: int
    sha256: str


@dataclass(frozen=True)
class StageOutcome:
    status: Literal["applied", "conflict", "already_committed", "operation_id_collision",
                    "busy", "missing", "invalid", "fatal"]
    revision: int | None = None
    detail: str = ""


def _invalid(message: str) -> StageError:
    return StageError("invalid", message)


def _sha(source: bytes) -> str:
    return hashlib.sha256(source).hexdigest()


def _model(source: bytes) -> Manifest:
    # Bound and validate before opening any database, using the loader's parser
    # and existing model. No independent manifest schema or canonical rewrite.
    if type(source) is not bytes or not 0 < len(source) <= MAX_SOURCE_BYTES:
        raise _invalid("Expected bounded nonempty exact source bytes")
    try:
        return Manifest.model_validate(json.loads(source.decode("utf-8")))
    except (ValueError, TypeError, RecursionError, ValidationError) as exc:
        raise _invalid("Source is not a supported UTF-8 Manifest") from exc


def _identity(s: os.stat_result) -> tuple[int, ...]:
    return s.st_dev, s.st_ino, s.st_nlink


def _ordinary(path: Path, *, directory: bool) -> os.stat_result:
    if not isinstance(path, Path) or not path.is_absolute():
        raise _invalid("An absolute Path is required")
    # Inspect each lexical component before resolve, which would follow links.
    if any(p in (".", "..") or ":" in p for p in path.parts[1:]):
        raise _invalid("Unsafe path component")
    if str(path).startswith("\\\\"):
        raise _invalid("Network/device paths are unsupported")
    for p in reversed((path, *path.parents)):
        s = p.lstat()
        if stat.S_ISLNK(s.st_mode) or getattr(s, "st_file_attributes", 0) & 0x400:
            raise _invalid("Reparse/link paths are unsupported")
        is_directory = p != path or directory
        if not (stat.S_ISDIR(s.st_mode) if is_directory else stat.S_ISREG(s.st_mode)):
            raise _invalid("Expected ordinary directory/file")
        if not is_directory and s.st_nlink != 1:
            raise _invalid("Multiply linked files are unsupported")
    if str(path.resolve(strict=True)) != str(path):
        raise _invalid("Aliased path spelling")
    return path.lstat()


def _scratch_parent(parent: Path) -> None:
    # Deliberately Windows-only at R5. Environment values are checked against
    # the current profile layout, not treated as permission/ownership evidence.
    if os.name != "nt":
        raise _invalid("R5 only validates Windows current-user LocalAppData Temp")
    profile = Path(os.environ.get("USERPROFILE", ""))
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    temp = local / "Temp"
    if not profile.is_absolute() or local != profile / "AppData" / "Local":
        raise _invalid("Unverified current-user Temp layout")
    _ordinary(temp, directory=True)
    _ordinary(parent, directory=True)
    if not parent.is_relative_to(temp) or parent == temp or not _PARENT.fullmatch(parent.name):
        raise _invalid("Scratch must be an explicit synthetic test parent under user Temp")
    if any((p / ".git").exists() for p in (parent, *parent.parents) if p != temp and p.is_relative_to(temp)):
        raise _invalid("Repository scratch is forbidden")


def _read_source(path: Path, source: bytes) -> tuple[int, ...]:
    before = _ordinary(path, directory=False)
    if before.st_size != len(source):
        raise _invalid("Source size changed")
    with path.open("rb") as fh:
        opened = os.fstat(fh.fileno())
        data = fh.read(MAX_SOURCE_BYTES + 1)
        after_open = os.fstat(fh.fileno())
    after = _ordinary(path, directory=False)
    signature = lambda s: (*_identity(s), s.st_size, s.st_mtime_ns)
    if data != source or len({signature(s) for s in (before, opened, after_open, after)}) != 1:
        raise _invalid("Source identity/content/freshness changed")
    return signature(after)


def _connect(path: Path, *, writable: bool) -> sqlite3.Connection:
    # mode=rw never recreates a missing store. No automatic SQLite busy retry.
    connection = sqlite3.connect(path.as_uri() + ("?mode=rw" if writable else "?mode=ro"),
                                 uri=True, timeout=0, isolation_level=None)
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_SOURCE_BYTES + 4096)
    connection.execute("PRAGMA trusted_schema=OFF")
    return connection


def _stage_path(stage: StagingReceipt) -> Path:
    if type(stage) is not StagingReceipt:
        raise _invalid("Expected a StagingReceipt")
    path = stage.database_path
    if not isinstance(path, Path):
        raise _invalid("Expected a database Path")
    _scratch_parent(path.parent.parent)
    if (not re.fullmatch(r"[0-9a-f]{32}", stage.store_id)
            or path.name != "stage.sqlite" or path.parent.name != "stage-" + stage.store_id
            or type(stage.generation) is not int or stage.generation != 1
            or type(stage.revision) is not int or stage.revision != 1
            or stage.source_readback != "advisory-before-and-after"):
        raise _invalid("Receipt identity/layout mismatch")
    if _identity(_ordinary(path.parent, directory=True)) != stage.directory_identity:
        raise _invalid("Stage directory identity changed")
    s = _ordinary(path, directory=False)
    if _identity(s) != stage.database_identity or not 0 < s.st_size <= MAX_DB_BYTES:
        raise _invalid("Database identity/size changed")
    # Reject pre-existing sidecars: this gate uses DELETE journaling, trusted
    # quiescent reads and participating writes only. Never recover an old store.
    for suffix in ("-journal", "-wal", "-shm"):
        if os.path.lexists(str(path) + suffix):
            raise _invalid("Unexpected sidecar; recovery is outside R5")
    return path


def _validated_state(connection: sqlite3.Connection, stage: StagingReceipt) -> StagedReadback:
    if (connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
            or connection.execute("PRAGMA user_version").fetchone()[0] != 1
            or connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete"):
        raise _invalid("SQLite identity/version/journal mismatch")
    schema = connection.execute(
        "SELECT name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    if schema != [("receipts", _RECEIPT_SQL), ("state", _STATE_SQL)]:
        raise _invalid("SQLite schema mismatch")
    if connection.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
        raise _invalid("SQLite integrity failed")
    rows = connection.execute(
        "SELECT singleton,store_id,generation,revision,project_id,schema_version,source,nbytes,sha256 FROM state"
    ).fetchall()
    if len(rows) != 1:
        raise _invalid("Expected one state row")
    key, store, generation, revision, project, version, source, nbytes, digest = rows[0]
    model = _model(source)
    if (key != 1 or store != stage.store_id or generation != 1
            or type(revision) is not int or not 1 <= revision <= MAX_REVISION
            or project != stage.project_id or project != model.project.id
            or version != stage.schema_version or version != model.schema_version
            or type(nbytes) is not int or nbytes != len(source) or digest != _sha(source)):
        raise _invalid("Staged identity/content mismatch")
    if revision == 1 and (digest != stage.source_sha256 or nbytes != stage.source_byte_count):
        raise _invalid("Initial content differs from receipt")
    count = connection.execute("SELECT count(*) FROM receipts").fetchone()[0]
    if count > MAX_RECEIPTS or revision != count + 1:
        raise _invalid("Receipt count/revision mismatch")
    receipts = connection.execute(
        "SELECT operation_id,expected_revision,replacement,nbytes,sha256,result_revision "
        "FROM receipts ORDER BY result_revision"
    ).fetchall()
    for result, (op, expected, replacement, size, sha, committed) in enumerate(receipts, 2):
        replacement_model = _model(replacement)
        if (type(op) is not str or not _OPERATION.fullmatch(op)
                or type(expected) is not int or expected != result - 1 or committed != result
                or type(size) is not int or size != len(replacement) or sha != _sha(replacement)
                or replacement_model.project.id != project or replacement_model.schema_version != version):
            raise _invalid("Invalid operation receipt")
    if receipts and receipts[-1][2] != source:
        raise _invalid("Latest receipt disagrees with state")
    return StagedReadback(source, model, revision, generation, digest)


def _failure(exc: Exception) -> StageError:
    if isinstance(exc, StageError):
        return exc
    if isinstance(exc, FileNotFoundError):
        return StageError("missing", "Scratch store/source missing")
    if isinstance(exc, sqlite3.Error):
        code = getattr(exc, "sqlite_errorcode", 0) & 0xff
        if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            return StageError("busy", "Scratch database busy; no retry")
        if code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB, sqlite3.SQLITE_SCHEMA,
                    sqlite3.SQLITE_TOOBIG):
            return _invalid("Invalid scratch database")
        return StageError("fatal", "SQLite operation failed; no retry")
    return _invalid("Scratch path/content could not be validated")


def stage_validated_manifest(loaded: LoadedManifest, *, scratch_parent: Path) -> StagingReceipt:
    """Stage a freshly made-up Temp manifest; retain exact bytes, never save JSON.

    Failures retain disposable partial files. No deletion or fallback is done.
    The receipt freezes metadata, not the returned Pydantic model or OS state.
    """
    try:
        if type(loaded) is not LoadedManifest or type(loaded.manifest) is not Manifest:
            raise _invalid("Expected the actual LoadedManifest and Manifest")
        model = _model(loaded.source_bytes)
        if model != loaded.manifest:
            raise _invalid("Loaded model disagrees with exact source bytes")
        _scratch_parent(scratch_parent)
        if not isinstance(loaded.path, Path) or not loaded.path.is_relative_to(scratch_parent):
            raise _invalid("Only synthetic sources inside the scratch parent are admitted")
        identity = _read_source(loaded.path, loaded.source_bytes)
        store_id = uuid.uuid4().hex
        folder = scratch_parent / ("stage-" + store_id)
        folder.mkdir(exist_ok=False)
        directory_identity = _identity(_ordinary(folder, directory=True))
        path = folder / "stage.sqlite"
        with path.open("xb"):
            pass
        database_identity = _identity(_ordinary(path, directory=False))
        with closing(_connect(path, writable=True)) as connection:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA max_page_count=4096")  # 4096-byte default pages
            connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
            connection.execute("PRAGMA user_version=1")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(_STATE_SQL)
            connection.execute(_RECEIPT_SQL)
            connection.execute("INSERT INTO state VALUES (1,?,1,1,?,?,?,?,?)",
                               (store_id, model.project.id, model.schema_version, loaded.source_bytes,
                                len(loaded.source_bytes), _sha(loaded.source_bytes)))
            connection.commit()
        if _read_source(loaded.path, loaded.source_bytes) != identity:
            raise _invalid("Source identity changed across staging")
        receipt = StagingReceipt(path, store_id, model.project.id, model.schema_version, 1, 1,
                                 _sha(loaded.source_bytes), len(loaded.source_bytes), loaded.path,
                                 identity, directory_identity, database_identity, "advisory-before-and-after")
        read_staged_manifest(receipt)
        return receipt
    except (StageError, OSError, ValueError, TypeError, sqlite3.Error) as exc:
        raise _failure(exc) from exc


def read_staged_manifest(stage: StagingReceipt) -> StagedReadback:
    """Read one SQLite snapshot; never reopen/fall back to source JSON."""
    try:
        path = _stage_path(stage)
        with closing(_connect(path, writable=False)) as connection:
            connection.execute("BEGIN")
            result = _validated_state(connection, stage)
            connection.rollback()
        _stage_path(stage)
        return result
    except (StageError, OSError, ValueError, TypeError, sqlite3.Error) as exc:
        raise _failure(exc) from exc


def compare_and_update_stage(stage: StagingReceipt, *, expected_revision: int,
                             operation_id: str, replacement_source_bytes: bytes) -> StageOutcome:
    """Single-generation participating-writer CAS and same-transaction receipt.

    Matching replay binds exact bytes AND original expected revision. Historical
    replay returns its committed revision, even after subsequent commits.
    Identical-byte new operations still advance revision. No automatic retries.
    """
    try:
        model = _model(replacement_source_bytes)  # before path access/connection
        if (type(expected_revision) is not int or not 1 <= expected_revision <= MAX_REVISION
                or type(operation_id) is not str or not _OPERATION.fullmatch(operation_id)):
            raise _invalid("Invalid revision/operation identifier")
        path = _stage_path(stage)
        if model.project.id != stage.project_id or model.schema_version != stage.schema_version:
            raise _invalid("Replacement identity differs from staged project/schema")
        with closing(_connect(path, writable=True)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = _validated_state(connection, stage)
            prior = connection.execute(
                "SELECT expected_revision,replacement,result_revision FROM receipts WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if prior is not None:
                same = prior[0] == expected_revision and prior[1] == replacement_source_bytes
                return StageOutcome("already_committed" if same else "operation_id_collision", prior[2])
            if current.revision != expected_revision:
                return StageOutcome("conflict", current.revision)
            if current.revision == MAX_REVISION or current.revision > MAX_RECEIPTS:
                raise _invalid("Revision/receipt capacity exhausted")
            revision = current.revision + 1
            digest = _sha(replacement_source_bytes)
            changed = connection.execute(
                "UPDATE state SET source=?,nbytes=?,sha256=?,revision=? WHERE singleton=1 AND revision=?",
                (replacement_source_bytes, len(replacement_source_bytes), digest, revision, expected_revision),
            ).rowcount
            if changed != 1:
                raise _invalid("Conditional update did not change exactly one row")
            connection.execute("INSERT INTO receipts VALUES (?,?,?,?,?,?)",
                               (operation_id, expected_revision, replacement_source_bytes,
                                len(replacement_source_bytes), digest, revision))
            connection.commit()
            return StageOutcome("applied", revision)
    except (StageError, OSError, ValueError, TypeError, sqlite3.Error) as exc:
        error = _failure(exc)
        return StageOutcome(error.reason, detail=str(error))
