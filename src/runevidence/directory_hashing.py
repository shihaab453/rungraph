"""Canonical bytes for a directory artifact, format ``runevidence-directory-v1``.

Development only: no command uses this yet. It encodes records the caller
already holds in memory and never touches the filesystem, so it says nothing
about how those records were collected. Rejecting links, special files and
concurrent changes is the job of a future scanner, not of this module.

The stream is::

    b"RunEvidence.directory\\x00v1\\x00"
    u64 record count (the root itself has no record)
    records sorted by their UTF-8 path bytes, each:
        kind byte b"D" or b"F" || u32 path length || path || payload

A directory's payload is empty; a file's is the 32 raw bytes of the SHA-256
of its contents. Integers are unsigned big-endian. The directory digest is the
SHA-256 of the whole stream.

Names follow a deliberately narrow ASCII-only profile so the same bytes mean
the same tree on Windows, macOS and Linux. Legitimate Unicode names are
rejected rather than normalized; a wider profile needs a new format token.
"""

from __future__ import annotations

import hashlib
import re
import struct
from dataclasses import dataclass, fields
from typing import Iterable, Optional

from .models import is_own_file

HEADER = b"RunEvidence.directory\x00v1\x00"

DIRECTORY = "D"
FILE = "F"

# Any character outside printable ASCII is rejected too.
_FORBIDDEN_CHARS = frozenset('<>:"/\\|?*')
_RESERVED_STEM = re.compile(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])$")
# Version control, virtual environments and caches, at any depth. RunEvidence's
# own manifest and backups are rejected at any depth through is_own_file.
_PROTECTED = frozenset({".git", ".hg", ".svn", ".venv", "venv", "__pycache__"})


class DirectoryRecordError(ValueError):
    """The records cannot be encoded. ``reason`` is one of:

    * ``invalid-record``  wrong kind, digest or size for the record type
    * ``invalid-name``    empty, dot, non-ASCII, control or forbidden character
    * ``reserved-name``   a Windows device name such as CON or LPT1
    * ``protected-name``  .git, .venv, __pycache__, runevidence.json, ...
    * ``path-collision``  same path ignoring ASCII case, or a file used as a parent
    * ``missing-parent``  a parent folder has no directory record
    * ``limit-exceeded``  one of the DirectoryLimits bounds
    """

    def __init__(self, reason: str, message: str, path: Optional[str] = None):
        self.reason = reason
        self.path = path
        super().__init__(message)


@dataclass(frozen=True)
class DirectoryLimits:
    """Fixed v1 bounds. A caller may tighten them but never raise them."""

    depth: int = 64  # path components below the root
    members: int = 10_000  # records, excluding the root
    total_file_bytes: int = 1024**3
    file_bytes: int = 256 * 1024**2
    path_bytes: int = 1024  # one relative path, UTF-8
    total_path_bytes: int = 4 * 1024**2
    encoded_bytes: int = 8 * 1024**2  # the whole canonical stream

    def __post_init__(self) -> None:
        for f in fields(self):
            value = getattr(self, f.name)
            if type(value) is not int or not 0 <= value <= f.default:
                raise ValueError(f"{f.name} must be an int from 0 to {f.default}, got {value!r}")


DEFAULT_LIMITS = DirectoryLimits()


@dataclass(frozen=True)
class DirectoryRecord:
    """One member below the root: a directory (``D``) or a regular file (``F``).

    ``sha256`` is the raw 32-byte digest of a file's contents and ``size_bytes``
    its length. Both stay empty/zero for a directory."""

    kind: str
    path: str
    sha256: bytes = b""
    size_bytes: int = 0


@dataclass(frozen=True)
class DirectoryDigest:
    sha256: str  # lowercase hex SHA-256 of the canonical stream
    size_bytes: int  # sum of file sizes; directories count zero
    members: int  # records, excluding the root


def check_member_path(path: object) -> bytes:
    """Validate one relative member path against the v1 name profile and
    return its UTF-8 bytes. Raises DirectoryRecordError."""
    if not isinstance(path, str) or not path:
        raise DirectoryRecordError("invalid-name", f"Member path must be a nonempty string: {path!r}")
    for part in path.split("/"):
        if part in ("", ".", ".."):
            raise DirectoryRecordError(
                "invalid-name", f"Member path {path!r} has an empty, '.' or '..' component", path
            )
        if any(not " " <= ch <= "~" or ch in _FORBIDDEN_CHARS for ch in part):
            raise DirectoryRecordError(
                "invalid-name",
                f"Member path {path!r} has a character outside the portable ASCII profile",
                path,
            )
        if part[-1] in " .":
            raise DirectoryRecordError(
                "invalid-name", f"Member path {path!r} has a component ending in a space or dot", path
            )
        low = part.lower()
        if _RESERVED_STEM.match(low.split(".", 1)[0]):
            raise DirectoryRecordError(
                "reserved-name", f"Member path {path!r} uses a reserved Windows device name", path
            )
        if low in _PROTECTED or is_own_file(low):
            raise DirectoryRecordError(
                "protected-name", f"Member path {path!r} names protected content {part!r}", path
            )
    return path.encode("utf-8")


def encode_directory(
    records: Iterable[DirectoryRecord], limits: DirectoryLimits = DEFAULT_LIMITS
) -> bytes:
    """The canonical v1 stream for ``records``, in any order.

    Every limit is checked before the stream is built, so an oversized tree
    fails without allocating it. Raises DirectoryRecordError."""
    return _encode(records, limits)[0]


def directory_digest(
    records: Iterable[DirectoryRecord], limits: DirectoryLimits = DEFAULT_LIMITS
) -> DirectoryDigest:
    stream, size_bytes, members = _encode(records, limits)
    return DirectoryDigest(hashlib.sha256(stream).hexdigest(), size_bytes, members)


def _limit(message: str, path: Optional[str] = None) -> DirectoryRecordError:
    return DirectoryRecordError("limit-exceeded", message, path)


def _encode(
    records: Iterable[DirectoryRecord], limits: DirectoryLimits
) -> tuple[bytes, int, int]:
    if not isinstance(limits, DirectoryLimits):
        raise TypeError("limits must be a DirectoryLimits")
    kinds: dict[str, str] = {}  # exact path -> kind
    seen: dict[str, str] = {}  # ASCII-lowercased path -> first spelling
    rows: list[tuple[bytes, bytes]] = []
    size_bytes = path_bytes = 0
    for count, rec in enumerate(records, 1):
        if count > limits.members:
            raise _limit(f"More than {limits.members} members")
        if not isinstance(rec, DirectoryRecord):
            raise DirectoryRecordError("invalid-record", f"Expected a DirectoryRecord, got {rec!r}")
        raw = check_member_path(rec.path)
        path = rec.path
        if rec.kind == DIRECTORY:
            if rec.sha256 != b"" or type(rec.size_bytes) is not int or rec.size_bytes != 0:
                raise DirectoryRecordError(
                    "invalid-record", f"Directory record {path!r} must have no digest and size 0", path
                )
        elif rec.kind == FILE:
            if not isinstance(rec.sha256, bytes) or len(rec.sha256) != 32:
                raise DirectoryRecordError(
                    "invalid-record", f"File record {path!r} needs a raw 32-byte SHA-256", path
                )
            if type(rec.size_bytes) is not int or rec.size_bytes < 0:
                raise DirectoryRecordError(
                    "invalid-record", f"File record {path!r} needs a size of 0 or more bytes", path
                )
            if rec.size_bytes > limits.file_bytes:
                raise _limit(f"File {path!r} is larger than {limits.file_bytes} bytes", path)
            size_bytes += rec.size_bytes
            if size_bytes > limits.total_file_bytes:
                raise _limit(f"Files total more than {limits.total_file_bytes} bytes", path)
        else:
            raise DirectoryRecordError(
                "invalid-record", f"Record {path!r} has kind {rec.kind!r}; expected 'D' or 'F'", path
            )
        if len(raw) > limits.path_bytes:
            raise _limit(f"Member path {path!r} is longer than {limits.path_bytes} bytes", path)
        if path.count("/") + 1 > limits.depth:
            raise _limit(f"Member path {path!r} is deeper than {limits.depth} levels", path)
        path_bytes += len(raw)
        if path_bytes > limits.total_path_bytes:
            raise _limit(f"Member paths total more than {limits.total_path_bytes} bytes", path)
        key = path.lower()  # ASCII-only by now, so this is ASCII case folding
        if key in seen:
            raise DirectoryRecordError(
                "path-collision", f"Member path {path!r} collides with {seen[key]!r}", path
            )
        seen[key] = path
        kinds[path] = rec.kind
        rows.append((raw, rec.kind.encode("ascii") + struct.pack(">I", len(raw)) + raw + rec.sha256))

    for path in kinds:
        parts = path.split("/")
        for i in range(1, len(parts)):
            parent = "/".join(parts[:i])
            kind = kinds.get(parent)
            if kind == FILE:
                raise DirectoryRecordError(
                    "path-collision", f"Member path {path!r} uses file {parent!r} as a folder", path
                )
            if kind is None:
                raise DirectoryRecordError(
                    "missing-parent", f"Member path {path!r} has no directory record for {parent!r}", path
                )

    encoded = len(HEADER) + 8 + sum(len(row) for _, row in rows)
    if encoded > limits.encoded_bytes:
        raise _limit(f"Canonical encoding is longer than {limits.encoded_bytes} bytes")
    # Paths are unique, so sorting the (path, row) pairs orders by path bytes alone.
    rows.sort()
    stream = HEADER + struct.pack(">Q", len(rows)) + b"".join(row for _, row in rows)
    return stream, size_bytes, len(rows)
