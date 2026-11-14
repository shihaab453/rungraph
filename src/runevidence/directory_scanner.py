"""Read a real folder into records for the v1 directory encoder.

Internal and development only: no command calls this yet.

Every member is opened by a single name relative to an already open parent
handle, never by a full path, and links are never followed:

* Windows: ``NtCreateFile`` with a parent handle and ``FILE_OPEN_REPARSE_POINT``
  (through ctypes). Folders on the current path, including every ancestor up
  to the drive root, are held open without delete sharing, so they cannot be
  renamed or replaced during the scan. Files are opened without write sharing,
  so no other handle can write to them while they are read. Only fixed NTFS
  drive letters are supported.
* Linux: component-at-a-time ``dir_fd`` traversal from ``/``, an ``O_PATH``
  type probe before any data open, a pinned reopen through ``/proc/self/fd``,
  identity checks and a proven mount id (``/proc/self/fdinfo`` or ``statx``).
  The root filesystem must be on the release allowlist, which contains only
  filesystems promoted by a reviewed qualification matrix (currently ``ext4``,
  promoted by S3a from the accepted ext4 matrix). Only
  ``ext4``/``xfs``/``btrfs``/``f2fs``/``tmpfs``/``overlay``/``zfs`` may be
  exercised through the private test-only qualification seam. Network, pseudo
  and coarse-timestamp filesystems are refused.
* Anything else is refused as ``unsupported-platform``. There is no path-based
  fallback.

What this does not do: it cannot stop another process from changing a folder's
contents between two looks. Each folder is listed again after its members are
read, each file's metadata is compared before and after reading, and a whole
second metadata pass must match the first; any difference is ``unstable``.
These are detectors for an experiment that is supposed to be quiet, not a
guarantee against a hostile writer (for example one that restores timestamps).
Alternate data streams, extended attributes, permissions and timestamps are not
part of the digest.
"""

from __future__ import annotations

import contextvars
import errno
import hashlib
import os
import stat
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from .directory_hashing import (
    DEFAULT_LIMITS,
    DIRECTORY,
    FILE,
    DirectoryDigest,
    DirectoryLimits,
    DirectoryRecord,
    DirectoryRecordError,
    check_member_path,
    directory_digest,
)

_CHUNK = 1024 * 1024

LINK = "L"  # symlink, junction or any other reparse point
SPECIAL = "S"  # device, FIFO, socket or anything else that is not D or F

# Filesystems a qualification run may exercise. This is *not* a runtime
# allowlist and grants no permission. Only CANDIDATE_FSTYPES can be named by
# the private test seam.
CANDIDATE_FSTYPES = frozenset({"ext4", "xfs", "btrfs", "f2fs", "tmpfs", "overlay", "zfs"})
# The runtime allowlist. A filesystem is added only by a separate release gate
# after a reviewed qualification matrix. S3a promotes ext4 alone from the
# accepted ext4 matrix (file sha256 f25f5a8b..., re-pinned to d82a1752... by the
# accepted S2 final); the current matrix is re-pinned by this slice. Matrix and
# tests are test evidence, never runtime input: the scanner never reads tests/.
# Every other type, including the other candidates, stays refused in production.
RELEASE_QUALIFIED_FSTYPES: frozenset[str] = frozenset({"ext4"})

# Operational caps, not DirectoryLimits the caller can raise.
_ROOT_COMPONENT_CAP = 256
_ANCESTOR_ENTRY_CAP = 4096
_TIMESTAMP_SAMPLE_ENTRIES = 32



class DirectoryScanError(Exception):
    """The folder could not be read into a trustworthy digest. ``reason`` is:

    * ``unsupported-platform``    no safe no-follow, handle-relative opens here
    * ``unsupported-filesystem``  e.g. not a fixed NTFS drive on Windows
    * ``invalid-root``            root is not an absolute, normalized path
    * ``missing``                 root or one of its ancestors does not exist
    * ``not-a-directory``         root or an ancestor is not a folder
    * ``unsafe``                  link, junction, hardlink, special file, mount,
                                  wrong on-disk spelling or a disallowed name
    * ``collision``               member names that differ only in ASCII case
    * ``limit-exceeded``          one of the DirectoryLimits bounds
    * ``unreadable``              permission or read failure
    * ``unstable``                something changed while it was being read

    ``path`` is the member path relative to the root, or None for the root."""

    def __init__(self, reason: str, message: str, path: Optional[str] = None):
        self.reason = reason
        self.path = path
        super().__init__(message)


@dataclass(frozen=True)
class DirectoryScan:
    records: tuple[DirectoryRecord, ...]
    digest: DirectoryDigest


@dataclass(frozen=True)
class ScanSupport:
    platform: str
    backend: Optional[str]
    supported: bool
    reason: str
    facilities: dict[str, Any]


@dataclass(frozen=True)
class ScanPolicy:
    """The exact filesystem-type allowlist for one scan.

    ``supported`` facilities are separate from policy: a backend can be fully
    available while a root is still refused because its filesystem is not on
    the release allowlist."""

    fstypes: frozenset[str]

    def allows(self, fstype: str) -> bool:
        return fstype in self.fstypes


_RELEASE_POLICY = ScanPolicy(RELEASE_QUALIFIED_FSTYPES)
_SCAN_POLICY: contextvars.ContextVar[ScanPolicy] = contextvars.ContextVar(
    "runevidence_directory_scan_policy", default=_RELEASE_POLICY
)


def _candidate_policy(fstypes: Iterable[str]) -> ScanPolicy:
    """Build a test qualification policy that can only name candidates.

    This is a private shipped seam, not a public option. It raises for any
    network, pseudo, coarse or unknown type instead of silently dropping it."""
    requested = frozenset(fstypes)
    refused = sorted(requested - CANDIDATE_FSTYPES)
    if refused:
        raise ValueError(
            f"only CANDIDATE_FSTYPES can be qualified for tests; refused {', '.join(refused)}"
        )
    if not requested:
        raise ValueError("a qualification policy must name at least one candidate filesystem")
    return ScanPolicy(requested)


@contextmanager
def _qualification_for_tests(fstypes: Iterable[str]):
    """Set the candidate-only test policy for the enclosed scan; reset always.

    The production default is unaffected: the ContextVar default is the release
    policy, and this token is reset in ``finally`` so it cannot leak across
    threads or async tasks."""
    token = _SCAN_POLICY.set(_candidate_policy(fstypes))
    try:
        yield
    finally:
        _SCAN_POLICY.reset(token)


@contextmanager
def _release_policy_for_tests():
    """Force the production release policy for a test that proves refusal."""
    token = _SCAN_POLICY.set(_RELEASE_POLICY)
    try:
        yield
    finally:
        _SCAN_POLICY.reset(token)


def _scan_with_test_policy(
    root: "os.PathLike[str] | str",
    fstypes: Iterable[str],
    limits: DirectoryLimits = DEFAULT_LIMITS,
) -> DirectoryScan:
    """Private test seam: scan under a candidate-only policy. Not public."""
    with _qualification_for_tests(fstypes):
        return scan_directory(root, limits)


def native_scan_support() -> ScanSupport:
    """Which backend this interpreter would use, and the facilities it found.

    This never raises, even when ``/proc`` is missing or a probe fails: tests
    call it at module import/collection time. On Linux ``supported`` is true
    once the backend/facility probes pass and at least one filesystem is
    release-qualified (S3a promotes ext4). It stays false while the release
    allowlist is empty, which is the fail-closed posture. Either way it never
    qualifies a particular root: a non-ext4 root is still refused as
    ``unsupported-filesystem``. The ``facilities`` mapping reports the probed
    backend capabilities and is used by the private test seam."""
    try:
        backend = _native_backend()
    except DirectoryScanError as exc:
        return ScanSupport(sys.platform, None, False, str(exc), _facilities())
    except Exception as exc:  # noqa: BLE001 - a probe failure must not break collection
        return ScanSupport(sys.platform, None, False, f"capability probe failed: {exc}", _facilities())
    if sys.platform.startswith("linux") and not RELEASE_QUALIFIED_FSTYPES:
        return ScanSupport(
            sys.platform,
            backend.name,
            False,
            "no release-qualified filesystem: RELEASE_QUALIFIED_FSTYPES is empty (fail-closed)",
            _facilities(),
        )
    return ScanSupport(sys.platform, backend.name, True, "ok", _facilities())


def scan_directory(root: "os.PathLike[str] | str", limits: DirectoryLimits = DEFAULT_LIMITS) -> DirectoryScan:
    """Hash the folder at the absolute path ``root``. Raises DirectoryScanError.

    Stop anything that writes to the folder first. The scan only reads."""
    if not isinstance(limits, DirectoryLimits):
        raise TypeError("limits must be a DirectoryLimits")
    return _scan(_native_backend(), os.fspath(root), limits)



# --- backend-independent policy ---------------------------------------------


@dataclass(frozen=True)
class _Entry:
    name: str
    kind: str
    identity: tuple
    mount: Optional[int] = None


@dataclass(frozen=True)
class _Info:
    kind: str
    identity: tuple  # (volume or device, file id or inode)
    size: int
    times: tuple  # compared only with values from the same API
    links: int
    mount: Optional[int] = None  # POSIX mount id; None on Windows and the fake backend


class _Failure(Exception):
    """A backend's OS error, classified. ``code`` is one of not-found, denied,
    in-use, wrong-type, link, unsupported, limit, resource or other."""

    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


_ROOT_REASON = {
    "not-found": "missing",
    "denied": "unreadable",
    "in-use": "unstable",
    "wrong-type": "not-a-directory",
    "link": "unsafe",
    "unsupported": "unsupported-filesystem",
    "limit": "limit-exceeded",
    "resource": "unreadable",
    "other": "unreadable",
}
_MEMBER_REASON = {
    "not-found": ("unstable", "disappeared while the folder was being read"),
    "denied": ("unreadable", "cannot be read"),
    "in-use": ("unstable", "is open for writing by another process"),
    "wrong-type": ("unstable", "changed type while the folder was being read"),
    "link": ("unstable", "became a link while the folder was being read"),
    "unsupported": ("unsafe", "is on an unsupported volume"),
    "limit": ("limit-exceeded", "is longer than the supported path or name limit"),
    "resource": ("unreadable", "cannot be read because a resource is exhausted"),
    "other": ("unreadable", "cannot be read"),
}


def _member_error(failure: _Failure, rel: str) -> DirectoryScanError:
    reason, what = _MEMBER_REASON[failure.code]
    return DirectoryScanError(reason, f"Member {rel!r} {what} ({failure.detail})", rel)


def _root_error(failure: _Failure, where: str) -> DirectoryScanError:
    return DirectoryScanError(_ROOT_REASON[failure.code], f"{where}: {failure.detail}")


class _Walk:
    def __init__(self, backend, limits: DirectoryLimits, read_content: bool):
        self.backend = backend
        self.limits = limits
        self.read_content = read_content
        self.seen: list[tuple] = []  # (path, kind, identity, size, times, links, sha256)
        self.members = 0
        self.path_bytes = 0
        self.file_bytes = 0
        self.volume: Any = None
        self.mount: Optional[int] = None
        # The backend declares whether mount identity is required. It is never
        # inferred from _Info.mount (a missing value must not skip the check).
        self.mount_capable = bool(getattr(backend, "supports_mount_identity", False))

    def run(self, root: str) -> tuple:
        try:
            held, info = self.backend.open_root(root)
        except _Failure as exc:
            raise _root_error(exc, f"Cannot open {root!r}") from exc
        try:
            self.volume = info.identity[0]
            if self.mount_capable:
                if info.mount is None:
                    raise DirectoryScanError(
                        "unsupported-platform",
                        "The backend requires mount identity but could not prove a mount id for the root",
                    )
                self.mount = info.mount
            self.visit(held[-1], "", 0, info)
        finally:
            for handle in reversed(held):
                self.backend.close(handle)
        return info.identity

    def list(self, handle, prefix: str, again: Optional[list[_Entry]] = None) -> list[_Entry]:
        """List a folder. With ``again`` (the first listing), more entries
        than before means the folder changed rather than a limit was hit."""
        budget = self.limits.members - self.members if again is None else len(again)
        try:
            entries = self.backend.list(handle, budget)
        except _Failure as exc:
            raise _member_error(exc, prefix.rstrip("/") or ".") from exc
        if entries is None and again is None:
            raise DirectoryScanError("limit-exceeded", f"More than {self.limits.members} members")
        if entries is None or (again is not None and set(entries) != set(again)):
            folder = prefix.rstrip("/") or None
            raise DirectoryScanError("unstable", f"Folder {folder or '.'!r} changed while it was being read", folder)
        return entries

    def visit(self, handle, prefix: str, depth: int, info: _Info) -> None:
        limits = self.limits
        before = self.list(handle, prefix)
        lowered: dict[str, str] = {}
        for entry in before:
            rel = prefix + entry.name
            try:
                raw = check_member_path(rel)
            except DirectoryRecordError as exc:
                raise DirectoryScanError("unsafe", str(exc), rel) from exc
            if depth + 1 > limits.depth:
                raise DirectoryScanError("limit-exceeded", f"Member {rel!r} is deeper than {limits.depth} levels", rel)
            if len(raw) > limits.path_bytes:
                raise DirectoryScanError("limit-exceeded", f"Member path {rel!r} is longer than {limits.path_bytes} bytes", rel)
            key = entry.name.lower()
            if key in lowered:
                raise DirectoryScanError("collision", f"Members {prefix + lowered[key]!r} and {rel!r} differ only in case", rel)
            lowered[key] = entry.name
            if entry.kind == LINK:
                raise DirectoryScanError("unsafe", f"Member {rel!r} is a link or junction; links are not followed", rel)
            if entry.kind == SPECIAL:
                raise DirectoryScanError("unsafe", f"Member {rel!r} is not a regular file or folder", rel)
            self.path_bytes += len(raw)
        self.members += len(before)
        if self.path_bytes > limits.total_path_bytes:
            raise DirectoryScanError("limit-exceeded", f"Member paths total more than {limits.total_path_bytes} bytes")

        for entry in sorted(before, key=lambda e: e.name.encode("utf-8")):
            rel = prefix + entry.name
            if entry.kind == DIRECTORY:
                self.visit_dir(handle, entry, rel, depth)
            else:
                self.visit_file(handle, entry, rel)

        self.list(handle, prefix, again=before)
        self.check_unchanged(handle, info, prefix.rstrip("/") or ".")

    def open_checked(self, opener, handle, entry: _Entry, rel: str):
        try:
            child, info = opener(handle, entry.name)
        except _Failure as exc:
            raise _member_error(exc, rel) from exc
        try:
            if info.kind == LINK:
                raise DirectoryScanError("unstable", f"Member {rel!r} became a link while the folder was being read", rel)
            if info.kind != entry.kind or info.identity != entry.identity:
                raise DirectoryScanError("unstable", f"Member {rel!r} was replaced while the folder was being read", rel)
            if info.identity[0] != self.volume:
                raise DirectoryScanError("unsafe", f"Member {rel!r} is on another volume", rel)
            if self.mount_capable:
                if info.mount is None:
                    raise DirectoryScanError(
                        "unsupported-platform",
                        f"Member {rel!r} has no proven mount id; the backend requires one",
                        rel,
                    )
                if entry.mount is not None and entry.mount != info.mount:
                    raise DirectoryScanError(
                        "unstable", f"Member {rel!r} changed mount while the folder was being read", rel
                    )
                if info.mount != self.mount:
                    raise DirectoryScanError("unsafe", f"Member {rel!r} is on a different mount", rel)
        except BaseException:
            self.backend.close(child)
            raise
        return child, info

    def check_unchanged(self, handle, info: _Info, rel: str) -> None:
        try:
            now = self.backend.stat(handle)
        except _Failure as exc:
            raise _member_error(exc, rel) from exc
        if now != info:
            raise DirectoryScanError("unstable", f"Member {rel!r} changed while it was being read", rel)

    def visit_dir(self, handle, entry: _Entry, rel: str, depth: int) -> None:
        child, info = self.open_checked(self.backend.open_dir, handle, entry, rel)
        try:
            self.seen.append((rel, DIRECTORY, info.identity, 0, info.times, info.links, b""))
            self.visit(child, rel + "/", depth + 1, info)
        finally:
            self.backend.close(child)

    def visit_file(self, handle, entry: _Entry, rel: str) -> None:
        limits = self.limits
        child, info = self.open_checked(self.backend.open_file, handle, entry, rel)
        try:
            if info.links == 0:
                # An unlinked-but-open file surfaces with zero links: the name
                # disappeared (or the object was replaced) during the scan.
                raise DirectoryScanError(
                    "unstable", f"Member {rel!r} disappeared while the folder was being read (0 hard links)", rel
                )
            if info.links != 1:
                raise DirectoryScanError("unsafe", f"Member {rel!r} has {info.links} hard links; only single-link files are allowed", rel)
            if info.size > limits.file_bytes:
                raise DirectoryScanError("limit-exceeded", f"File {rel!r} is larger than {limits.file_bytes} bytes", rel)
            self.file_bytes += info.size
            if self.file_bytes > limits.total_file_bytes:
                raise DirectoryScanError("limit-exceeded", f"Files total more than {limits.total_file_bytes} bytes", rel)
            digest = self.read(child, info.size, rel) if self.read_content else b""
            self.check_unchanged(child, info, rel)
            self.seen.append((rel, FILE, info.identity, info.size, info.times, info.links, digest))
        finally:
            self.backend.close(child)

    def read(self, handle, size: int, rel: str) -> bytes:
        h = hashlib.sha256()
        done = 0
        while True:
            try:
                chunk = self.backend.read(handle, min(_CHUNK, size - done + 1))
            except _Failure as exc:
                raise _member_error(exc, rel) from exc
            if not chunk:
                break
            done += len(chunk)
            if done > size:
                raise DirectoryScanError("unstable", f"File {rel!r} grew while it was being read", rel)
            h.update(chunk)
        if done != size:
            raise DirectoryScanError("unstable", f"File {rel!r} shrank while it was being read", rel)
        return h.digest()


def _scan(backend, root: str, limits: DirectoryLimits) -> DirectoryScan:
    first = _Walk(backend, limits, read_content=True)
    first_root = first.run(root)
    # A second, metadata-only pass catches changes to parts of the tree that
    # were already closed. It is a detector, not a snapshot guarantee.
    second = _Walk(backend, limits, read_content=False)
    second_root = second.run(root)
    if first_root != second_root or [s[:6] for s in first.seen] != [s[:6] for s in second.seen]:
        raise DirectoryScanError("unstable", f"Folder {root!r} changed between two complete passes")
    records = tuple(
        DirectoryRecord(kind, path, digest, size) for path, kind, _, size, _, _, digest in first.seen
    )
    try:
        result = directory_digest(records, limits)
    except DirectoryRecordError as exc:
        reason = {"limit-exceeded": "limit-exceeded", "path-collision": "collision"}.get(exc.reason, "unsafe")
        raise DirectoryScanError(reason, str(exc), exc.path) from exc
    return DirectoryScan(records, result)


# --- backend selection --------------------------------------------------------

_POSIX_REQUIRED = ("O_PATH", "O_NOFOLLOW", "O_DIRECTORY", "O_NONBLOCK", "O_CLOEXEC")
_POSIX_CAPABILITY_KEYS = (
    "os.O_PATH",
    "os.O_NOFOLLOW",
    "os.O_DIRECTORY",
    "os.O_CLOEXEC",
    "os.O_NONBLOCK",
    "os.open.dir_fd",
    "os.stat.dir_fd",
    "os.scandir.fd",
    "probe.fstat",
    "proc_self_fd",
    "mount_id",
    "mountinfo",
)


def _facilities() -> dict[str, Any]:
    facilities: dict[str, Any] = {
        "python": sys.version.split()[0],
        "os.name": os.name,
        "sys.platform": sys.platform,
        "os.supports_dir_fd": sorted(f.__name__ for f in os.supports_dir_fd),
        "os.supports_fd": sorted(f.__name__ for f in os.supports_fd),
        "os.supports_follow_symlinks": sorted(f.__name__ for f in os.supports_follow_symlinks),
        **{f"os.{flag}": hasattr(os, flag) for flag in _POSIX_REQUIRED},
    }
    if sys.platform.startswith("linux"):
        try:
            facilities["posix"] = _posix_capabilities()
        except Exception as exc:  # noqa: BLE001 - reporting must never raise
            facilities["posix"] = {"error": f"{type(exc).__name__}: {exc}"}
    return facilities


def _native_backend():
    if sys.platform == "win32":
        try:
            return _WindowsBackend()
        except (OSError, AttributeError) as exc:
            raise DirectoryScanError("unsupported-platform", f"Windows NT handle API unavailable: {exc}") from exc
    if sys.platform.startswith("linux"):
        try:
            capabilities = _posix_capabilities()
        except Exception as exc:  # noqa: BLE001 - probe failures are unsupported, never a crash
            raise DirectoryScanError(
                "unsupported-platform", f"The Linux POSIX scanner facilities are unavailable: {exc}"
            ) from exc
        missing = [key for key in _POSIX_CAPABILITY_KEYS if not capabilities.get(key)]
        if missing:
            raise DirectoryScanError(
                "unsupported-platform",
                "The Linux POSIX scanner is unavailable (missing: " + ", ".join(missing) + ")",
            )
        return _PosixBackend(_SCAN_POLICY.get(), capabilities)
    raise DirectoryScanError(
        "unsupported-platform",
        f"Native scanning is unavailable on {sys.platform}: race-safe data opening "
        "and native platform coverage are pending",
    )


def _split_absolute(root: str, sep: str) -> list[str]:
    parts = root.split(sep)
    if any(part in ("", ".", "..") for part in parts):
        raise DirectoryScanError("invalid-root", f"Root must be an absolute, normalized path: {root!r}")
    return parts


# --- POSIX --------------------------------------------------------------------


def _posix_kind(mode: int) -> str:
    if stat.S_ISLNK(mode):
        return LINK
    if stat.S_ISDIR(mode):
        return DIRECTORY
    if stat.S_ISREG(mode):
        return FILE
    return SPECIAL


_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _ascii_lower(text: str) -> str:
    return text.translate(_ASCII_LOWER)


def _posix_failure(exc: OSError) -> _Failure:
    code = {
        errno.ENOENT: "not-found",
        errno.EACCES: "denied",
        errno.EPERM: "denied",
        errno.ELOOP: "link",
        errno.ENOTDIR: "wrong-type",
        errno.EISDIR: "wrong-type",
        errno.EXDEV: "unsupported",
        errno.EBUSY: "in-use",
        errno.ETXTBSY: "in-use",
        errno.ENAMETOOLONG: "limit",
        errno.EMFILE: "resource",
        errno.ENFILE: "resource",
        errno.ENOMEM: "resource",
        errno.EOPNOTSUPP: "unsupported",
    }.get(exc.errno, "other")
    return _Failure(code, f"{exc.strerror} (errno {exc.errno})")


# --- POSIX mount table --------------------------------------------------------


class _MountError(Exception):
    """The mount table cannot be parsed or resolved without guessing."""


@dataclass(frozen=True)
class _Mount:
    mount_id: int
    parent_id: int
    mount_point: str
    fstype: str


_MOUNT_ESCAPES = {"040": " ", "011": "\t", "012": "\n", "134": "\\"}


def _decode_mount_field(text: str) -> str:
    """Decode the octal escapes mountinfo uses in a mount point."""
    out = []
    index = 0
    while index < len(text):
        char = text[index]
        if char != "\\":
            out.append(char)
            index += 1
            continue
        digits = text[index + 1 : index + 4]
        if len(digits) != 3 or any(digit not in "01234567" for digit in digits):
            raise _MountError(f"Invalid octal escape in mount field {text!r}")
        out.append(_MOUNT_ESCAPES.get(digits, chr(int(digits, 8))))
        index += 4
    return "".join(out)


def _parse_mountinfo(text: str) -> dict[int, _Mount]:
    """Parse /proc/self/mountinfo. Malformed or ambiguous records are refused."""
    table: dict[int, _Mount] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        left, separator, right = line.partition(" - ")
        if not separator:
            raise _MountError("A mountinfo record has no separator before the filesystem type")
        left_fields = left.split()
        right_fields = right.split()
        if len(left_fields) < 6 or not right_fields:
            raise _MountError("A mountinfo record is missing required fields")
        try:
            mount_id = int(left_fields[0])
            parent_id = int(left_fields[1])
        except ValueError as exc:
            raise _MountError("A mountinfo record has a non-numeric mount or parent id") from exc
        mount_point = _decode_mount_field(left_fields[4])
        if not mount_point.startswith("/"):
            raise _MountError(f"A mountinfo record has a relative mount point {mount_point!r}")
        if mount_id <= 0 or parent_id < 0:
            raise _MountError("A mountinfo record has a non-positive mount id")
        if mount_id in table:
            raise _MountError(f"Mount id {mount_id} appears more than once")
        table[mount_id] = _Mount(mount_id, parent_id, mount_point, right_fields[0])
    if not table:
        raise _MountError("The mount table is empty")
    return table


def _read_mountinfo_text(path: str = "/proc/self/mountinfo") -> str:
    with open(path, "rb") as handle:
        return handle.read().decode("utf-8", "surrogateescape")


def _mountinfo() -> dict[int, _Mount]:
    """Read and parse the mount table, classifying failures as facilities."""
    try:
        text = _read_mountinfo_text()
    except OSError as exc:
        raise DirectoryScanError(
            "unsupported-platform", f"Cannot read /proc/self/mountinfo: {exc.strerror or exc}"
        ) from exc
    try:
        return _parse_mountinfo(text)
    except _MountError as exc:
        raise DirectoryScanError(
            "unsupported-filesystem", f"Cannot identify the filesystem from /proc/self/mountinfo: {exc}"
        ) from exc


def _covers(mount_point: str, path: str) -> bool:
    """Component-boundary exact prefix matching."""
    return mount_point == "/" or path == mount_point or path.startswith(mount_point + "/")


def _is_descendant(child_id: int, ancestor_id: int, table: dict[int, _Mount]) -> bool:
    seen: set[int] = set()
    current = child_id
    while current in table:
        if current == ancestor_id:
            return True
        if current in seen:
            raise _MountError(f"The mount parent chain is cyclic at id {current}")
        seen.add(current)
        current = table[current].parent_id
    return False


def _topmost(mounts: list[_Mount], table: dict[int, _Mount]) -> _Mount:
    """The unique topmost mount among entries sharing one decoded mount point."""
    if len(mounts) == 1:
        return mounts[0]
    top = [
        candidate
        for candidate in mounts
        if all(
            other is candidate or _is_descendant(candidate.mount_id, other.mount_id, table)
            for other in mounts
        )
    ]
    if len(top) != 1:
        raise _MountError(
            "Mounts stacked at one path have no unique topmost mount: ids "
            + ", ".join(str(m.mount_id) for m in mounts)
        )
    return top[0]


def _covering_mount(table: dict[int, _Mount], path: str) -> _Mount:
    """The topmost mount covering ``path``; never assumes a default mount."""
    matching = [mount for mount in table.values() if _covers(mount.mount_point, path)]
    if not matching:
        raise _MountError(f"No mount entry covers {path!r}")
    longest = max(len(mount.mount_point) for mount in matching)
    candidates = [mount for mount in matching if len(mount.mount_point) == longest]
    return _topmost(candidates, table)


def _root_components(root: str) -> list[str]:
    """Validate an absolute POSIX scan root and split it into components."""
    if not isinstance(root, str):
        raise DirectoryScanError("invalid-root", f"Root must be a string path: {root!r}")
    if not root.startswith("/"):
        raise DirectoryScanError("invalid-root", f"Root must be an absolute path: {root!r}")
    if "\x00" in root:
        raise DirectoryScanError("invalid-root", "Root must not contain a NUL character")
    if root == "/":
        raise DirectoryScanError("invalid-root", "The filesystem root itself cannot be scanned")
    parts = root.split("/")[1:]
    if any(part in ("", ".", "..") for part in parts):
        raise DirectoryScanError("invalid-root", f"Root must be an absolute, normalized path: {root!r}")
    if len(parts) > _ROOT_COMPONENT_CAP:
        raise DirectoryScanError(
            "limit-exceeded", f"Root has more than {_ROOT_COMPONENT_CAP} path components"
        )
    return parts


def _ancestor_paths(parts: list[str]) -> list[str]:
    paths = ["/"]
    current = ""
    for part in parts:
        current += "/" + part
        paths.append(current)
    return paths


def _preflight_mounts(parts: list[str], policy: ScanPolicy, table: Optional[dict[int, _Mount]] = None):
    """Check every ancestor's exact filesystem type before any open or listing."""
    table = _mountinfo() if table is None else table
    chosen = None
    checked: list[tuple[str, _Mount]] = []
    for path in _ancestor_paths(parts):
        try:
            mount = _covering_mount(table, path)
        except _MountError as exc:
            raise DirectoryScanError("unsupported-filesystem", f"Cannot resolve {path!r} to a mount: {exc}") from exc
        if not policy.allows(mount.fstype):
            allowed = ", ".join(sorted(policy.fstypes)) or "none"
            raise DirectoryScanError(
                "unsupported-filesystem",
                f"{path!r} is on {mount.fstype!r}; the allowed filesystems are {allowed}",
            )
        checked.append((path, mount))
        chosen = mount
    return checked, chosen


def _validate_root_mount(
    actual_id: int,
    preflight_mount: _Mount,
    table: dict[int, _Mount],
    policy: ScanPolicy,
) -> _Mount:
    """Validate the proven root id against a fresh post-open mount table."""
    record = table.get(actual_id)
    if record is None:
        raise DirectoryScanError(
            "unsupported-filesystem",
            f"The root mount id {actual_id} is absent from the fresh mount table",
        )
    if actual_id != preflight_mount.mount_id:
        raise DirectoryScanError(
            "unsupported-filesystem",
            f"The root mount changed from id {preflight_mount.mount_id} to id {actual_id} while it was opened",
        )
    if record.mount_point != preflight_mount.mount_point:
        raise DirectoryScanError(
            "unsupported-filesystem",
            f"The root mount id {actual_id} now names {record.mount_point!r}, not "
            f"{preflight_mount.mount_point!r}",
        )
    if not policy.allows(record.fstype):
        allowed = ", ".join(sorted(policy.fstypes)) or "none"
        raise DirectoryScanError(
            "unsupported-filesystem",
            f"The root mount id {actual_id} is now {record.fstype!r}; the allowed filesystems are {allowed}",
        )
    return record


# --- POSIX mount-id proof and statx -------------------------------------------


def _safe_close(fd: int) -> None:
    """Close a scanner-owned descriptor without leaking a raw OSError.

    A close failure cannot be acted on and must not replace the scan's real
    error. Every other POSIX OS call is normalized to ``_Failure``."""
    try:
        os.close(fd)
    except OSError:
        pass

_STATX_MNT_ID = 0x1000
_STATX_MNT_ID_UNIQUE = 0x4000  # deliberately not requested and not accepted
_STATX_MTIME = 0x40
_STATX_CTIME = 0x80
_AT_EMPTY_PATH = 0x1000
_AT_SYMLINK_NOFOLLOW = 0x100

_STATX_CACHE: dict[str, Any] = {"loaded": False, "function": None}


def _statx_function():
    """libc statx if present. Optional: fdinfo is the primary mount-id source."""
    if not _STATX_CACHE["loaded"]:
        _STATX_CACHE["loaded"] = True
        try:
            import ctypes

            class _Timestamp(ctypes.Structure):
                _fields_ = [
                    ("tv_sec", ctypes.c_int64),
                    ("tv_nsec", ctypes.c_uint32),
                    ("reserved", ctypes.c_int32),
                ]

            class _Statx(ctypes.Structure):
                _fields_ = [
                    ("stx_mask", ctypes.c_uint32),
                    ("stx_blksize", ctypes.c_uint32),
                    ("stx_attributes", ctypes.c_uint64),
                    ("stx_nlink", ctypes.c_uint32),
                    ("stx_uid", ctypes.c_uint32),
                    ("stx_gid", ctypes.c_uint32),
                    ("stx_mode", ctypes.c_uint16),
                    ("stx_spare0", ctypes.c_uint16),
                    ("stx_ino", ctypes.c_uint64),
                    ("stx_size", ctypes.c_uint64),
                    ("stx_blocks", ctypes.c_uint64),
                    ("stx_attributes_mask", ctypes.c_uint64),
                    ("stx_atime", _Timestamp),
                    ("stx_btime", _Timestamp),
                    ("stx_ctime", _Timestamp),
                    ("stx_mtime", _Timestamp),
                    ("stx_rdev_major", ctypes.c_uint32),
                    ("stx_rdev_minor", ctypes.c_uint32),
                    ("stx_dev_major", ctypes.c_uint32),
                    ("stx_dev_minor", ctypes.c_uint32),
                    ("stx_mnt_id", ctypes.c_uint64),
                    ("stx_dio_mem_align", ctypes.c_uint32),
                    ("stx_dio_offset_align", ctypes.c_uint32),
                    ("stx_spare3", ctypes.c_uint64 * 12),
                ]

            libc = ctypes.CDLL(None, use_errno=True)
            function = libc.statx
            function.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_uint,
                ctypes.POINTER(_Statx),
            ]
            function.restype = ctypes.c_int
            _STATX_CACHE["function"] = function
            _STATX_CACHE["struct"] = _Statx
        except (OSError, AttributeError, TypeError):
            _STATX_CACHE["function"] = None
    return _STATX_CACHE["function"]


def _statx_fields(dir_fd: int, name: Optional[str] = None) -> Optional[dict[str, int]]:
    """One read-only statx result, or None when statx is unavailable/fails."""
    function = _statx_function()
    if function is None:
        return None
    import ctypes

    struct = _STATX_CACHE["struct"]()
    flags = _AT_EMPTY_PATH if name is None else _AT_SYMLINK_NOFOLLOW
    encoded = b"" if name is None else os.fsencode(name)
    mask = _STATX_MNT_ID | _STATX_MTIME | _STATX_CTIME
    try:
        if function(dir_fd, encoded, flags, mask, ctypes.byref(struct)) != 0:
            return None
    except (OSError, ValueError):
        return None
    return {
        "mask": struct.stx_mask,
        "mtime_ns": struct.stx_mtime.tv_sec * 1_000_000_000 + struct.stx_mtime.tv_nsec,
        "ctime_ns": struct.stx_ctime.tv_sec * 1_000_000_000 + struct.stx_ctime.tv_nsec,
        "mnt_id": int(struct.stx_mnt_id),
    }


def _parse_fdinfo_mount_id(text: str) -> Optional[int]:
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        if not separator or key.strip() != "mnt_id":
            continue
        value = value.strip()
        if not value.isdigit():
            return None
        mount_id = int(value)
        return mount_id if mount_id > 0 else None
    return None


def _mount_id_from_fdinfo(fd: int) -> Optional[int]:
    try:
        with open(f"/proc/self/fdinfo/{fd}", "r", encoding="utf-8", errors="surrogateescape") as handle:
            text = handle.read()
    except OSError:
        return None
    try:
        return _parse_fdinfo_mount_id(text)
    except ValueError:
        return None


def _mount_id_from_statx(fd: int) -> Optional[int]:
    info = _statx_fields(fd)
    if info is None:
        return None
    # A success without the classic mask bit is not proof. Unique-only replies
    # are deliberately rejected: their numbering space is not interchangeable.
    if not info["mask"] & _STATX_MNT_ID:
        return None
    return info["mnt_id"] if info["mnt_id"] > 0 else None


def _name_mount_id(parent: int, name: str) -> Optional[int]:
    """Best-effort listing-time mount id from statx at a name (no open)."""
    info = _statx_fields(parent, name)
    if info is None or not info["mask"] & _STATX_MNT_ID:
        return None
    return info["mnt_id"] if info["mnt_id"] > 0 else None



def _prove_mount_id(fd: int) -> int:
    mount_id = _mount_id_from_fdinfo(fd)
    if mount_id is None:
        mount_id = _mount_id_from_statx(fd)
    if mount_id is None or mount_id <= 0:
        raise DirectoryScanError(
            "unsupported-platform",
            f"Cannot prove a mount id for descriptor {fd}: neither /proc/self/fdinfo nor statx "
            "returned STATX_MNT_ID with a nonzero value",
        )
    return mount_id


def _timestamp_probe(fd: int, names: list[str]) -> None:
    """Require at least one nonzero nanosecond timestamp in a bounded sample."""
    samples: list[tuple[int, int]] = []
    info = _statx_fields(fd)
    if info is not None:
        if not (info["mask"] & _STATX_MTIME and info["mask"] & _STATX_CTIME):
            raise DirectoryScanError(
                "unsupported-filesystem", "statx did not report both mtime and ctime"
            )
        samples.append((info["mtime_ns"], info["ctime_ns"]))
    else:
        try:
            st = os.fstat(fd)
        except OSError as exc:
            raise _posix_failure(exc) from exc
        samples.append((st.st_mtime_ns, st.st_ctime_ns))
    for name in names[:_TIMESTAMP_SAMPLE_ENTRIES]:
        info = _statx_fields(fd, name)
        if info is not None:
            if not (info["mask"] & _STATX_MTIME and info["mask"] & _STATX_CTIME):
                raise DirectoryScanError(
                    "unsupported-filesystem", "statx did not report both mtime and ctime"
                )
            samples.append((info["mtime_ns"], info["ctime_ns"]))
            continue
        try:
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except OSError:
            continue
        samples.append((st.st_mtime_ns, st.st_ctime_ns))
    if not any(mtime % 1_000_000_000 or ctime % 1_000_000_000 for mtime, ctime in samples):
        raise DirectoryScanError(
            "unsupported-filesystem",
            "Every sampled timestamp has zero nanoseconds; the filesystem's timestamp "
            "resolution is unproven",
        )


# --- POSIX capability probe --------------------------------------------------


def _probe_mountinfo_available() -> bool:
    try:
        _mountinfo()
    except DirectoryScanError:
        return False
    return True


def _posix_capabilities() -> dict[str, Any]:
    """Probe the facilities the backend needs. Never raises by design; the
    caller turns a raised OSError into unsupported-platform."""
    capabilities: dict[str, Any] = {f"os.{flag}": hasattr(os, flag) for flag in _POSIX_REQUIRED}
    # Functional probes, not ``os.open in os.supports_dir_fd``: test harnesses
    # legitimately wrap os.open/os.stat while proving the scanner writes nothing.
    capabilities["os.open.dir_fd"] = False
    capabilities["os.stat.dir_fd"] = False
    capabilities["os.scandir.fd"] = False
    capabilities["proc_self_fd"] = False
    capabilities["mount_id"] = False
    capabilities["mountinfo"] = False
    capabilities["probe.fstat"] = False
    if not all(capabilities[key] for key in ("os.O_PATH", "os.O_NOFOLLOW", "os.O_DIRECTORY",
                                             "os.O_CLOEXEC", "os.O_NONBLOCK")):
        return capabilities
    probe = os.open("/", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        capabilities["probe.fstat"] = stat.S_ISDIR(os.fstat(probe).st_mode)
        try:
            st = os.stat(".", dir_fd=probe, follow_symlinks=False)
            capabilities["os.stat.dir_fd"] = stat.S_ISDIR(st.st_mode)
        except OSError:
            pass
        try:
            child = os.open(".", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=probe)
        except OSError:
            pass
        else:
            _safe_close(child)
            capabilities["os.open.dir_fd"] = True
        try:
            with os.scandir(probe) as entries:
                next(entries, None)
            capabilities["os.scandir.fd"] = True
        except OSError:
            pass
        try:
            reopened = os.open(
                f"/proc/self/fd/{probe}", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
            )
        except OSError:
            pass
        else:
            capabilities["proc_self_fd"] = True
            try:
                with os.scandir(reopened) as entries:
                    next(entries, None)
                capabilities["os.scandir.fd"] = True
            except OSError:
                pass
            _safe_close(reopened)
        try:
            capabilities["mount_id"] = _prove_mount_id(probe) > 0
        except DirectoryScanError:
            capabilities["mount_id"] = False
        capabilities["mountinfo"] = _probe_mountinfo_available()
        capabilities["statx"] = _statx_function() is not None
        capabilities["backend"] = "posix-openat-nofollow"
    finally:
        _safe_close(probe)
    return capabilities


class _PosixBackend:
    name = "posix-openat-nofollow"
    supports_mount_identity = True

    def __init__(self, policy: Optional[ScanPolicy] = None, capabilities: Optional[dict] = None) -> None:
        self.policy = _RELEASE_POLICY if policy is None else policy
        self.capabilities = capabilities or {}
        # O_NOFOLLOW on a name-based open; the /proc/self/fd reopen of an
        # already-probed descriptor must NOT pass O_NOFOLLOW (it is a magic link).
        self.probe_flags = os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC
        self.reopen_dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        self.reopen_file_flags = (
            os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | getattr(os, "O_NOCTTY", 0)
        )

    # identity / info

    def _info(self, fd: int, mount: Any = ...) -> _Info:
        try:
            st = os.fstat(fd)
        except OSError as exc:
            raise _posix_failure(exc) from exc
        kind = _posix_kind(st.st_mode)
        if mount is ...:
            mount = _prove_mount_id(fd)
        return _Info(
            kind,
            (st.st_dev, st.st_ino),
            st.st_size if kind == FILE else 0,
            (st.st_mtime_ns, st.st_ctime_ns),
            st.st_nlink,
            mount,
        )

    def _probe(self, parent: int, name: str) -> int:
        try:
            return os.open(name, self.probe_flags, dir_fd=parent)
        except OSError as exc:
            raise _posix_failure(exc) from exc

    def _fstat(self, fd: int) -> os.stat_result:
        try:
            return os.fstat(fd)
        except OSError as exc:
            raise _posix_failure(exc) from exc

    def _reopen(self, probe: int, flags: int, expected: os.stat_result) -> int:
        try:
            reopened = os.open(f"/proc/self/fd/{probe}", flags)
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ELOOP):
                raise DirectoryScanError(
                    "unsupported-platform",
                    f"The pinned /proc/self/fd reopen is unavailable: {exc.strerror or exc}",
                ) from exc
            raise _posix_failure(exc) from exc
        try:
            try:
                opened = os.fstat(reopened)
            except OSError as exc:
                raise _posix_failure(exc) from exc
            if (opened.st_dev, opened.st_ino, stat.S_IFMT(opened.st_mode)) != (
                expected.st_dev,
                expected.st_ino,
                stat.S_IFMT(expected.st_mode),
            ):
                raise _Failure("in-use", "the object was replaced while it was being opened")
        except BaseException:
            _safe_close(reopened)
            raise
        return reopened

    # root traversal

    def _check_spelling(self, parent: int, part: str) -> None:
        """Require the exact on-disk spelling, bounded by the ancestor cap."""
        variant = None
        try:
            with os.scandir(parent) as entries:
                for count, entry in enumerate(entries, 1):
                    if entry.name == part:
                        return
                    if variant is None and _ascii_lower(entry.name) == _ascii_lower(part):
                        variant = entry.name
                    if count >= _ANCESTOR_ENTRY_CAP:
                        raise DirectoryScanError(
                            "limit-exceeded",
                            f"An ancestor has more than {_ANCESTOR_ENTRY_CAP} entries before {part!r}",
                        )
        except OSError as exc:
            raise _posix_failure(exc) from exc
        if variant is not None:
            raise DirectoryScanError(
                "unsafe", f"{part!r} is spelled {variant!r} on disk; case aliases are not allowed"
            )
        raise DirectoryScanError("unstable", f"{part!r} disappeared between stat and listing")

    def open_root(self, root: str):
        parts = _root_components(root)
        _, preflight_mount = _preflight_mounts(parts, self.policy)
        held: list[int] = []
        try:
            try:
                held.append(os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC))
            except OSError as exc:
                raise _posix_failure(exc) from exc
            for index, part in enumerate(parts):
                is_root = index == len(parts) - 1
                parent = held[-1]
                try:
                    st = os.stat(part, dir_fd=parent, follow_symlinks=False)
                except OSError as exc:
                    raise _posix_failure(exc) from exc
                kind = _posix_kind(st.st_mode)
                if kind == LINK:
                    raise _Failure("link", f"{part!r} is a symbolic link")
                if kind != DIRECTORY:
                    raise _Failure("wrong-type", f"{part!r} is not a folder")
                self._check_spelling(parent, part)
                probe = self._probe(parent, part)
                try:
                    probed = self._fstat(probe)
                    if _posix_kind(probed.st_mode) == LINK:
                        raise _Failure("link", f"{part!r} is a symbolic link")
                    if _posix_kind(probed.st_mode) != DIRECTORY:
                        raise _Failure("wrong-type", f"{part!r} is not a folder")
                    if (probed.st_dev, probed.st_ino) != (st.st_dev, st.st_ino):
                        raise _Failure("in-use", f"{part!r} was replaced while it was opened")
                    try:
                        mount = _prove_mount_id(probe)
                    except DirectoryScanError:
                        # Ancestors above the artifact root are exempt from
                        # mount-id equality; the preflight established their type.
                        if is_root:
                            raise
                        mount = None
                    if is_root:
                        fresh = _mountinfo()
                        self._validate_root(mount, preflight_mount, fresh)
                    reopened = self._reopen(probe, self.reopen_dir_flags, probed)
                finally:
                    _safe_close(probe)
                held.append(reopened)
            info = self._info(held[-1])
            if info.mount is None:
                raise DirectoryScanError("unsupported-platform", "The root has no proven mount id")
            self._validate_root(info.mount, preflight_mount, _mountinfo())
            try:
                with os.scandir(held[-1]) as entries:
                    names = [entry.name for _, entry in zip(range(_TIMESTAMP_SAMPLE_ENTRIES), entries)]
            except OSError as exc:
                raise _posix_failure(exc) from exc
            try:
                _timestamp_probe(held[-1], names)
            except OSError as exc:
                raise _posix_failure(exc) from exc
            return held, info
        except BaseException:
            for fd in reversed(held):
                _safe_close(fd)
            raise

    def _validate_root(self, mount_id: Optional[int], preflight_mount: _Mount,
                       table: dict[int, _Mount]) -> None:
        if mount_id is None:
            raise DirectoryScanError("unsupported-platform", "The root has no proven mount id")
        _validate_root_mount(mount_id, preflight_mount, table, self.policy)

    # backend interface

    def list(self, fd: int, budget: int) -> Optional[list[_Entry]]:
        out: list[_Entry] = []
        try:
            with os.scandir(fd) as entries:
                for entry in entries:
                    if len(out) >= budget:
                        return None
                    st = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                    out.append(
                        _Entry(
                            entry.name,
                            _posix_kind(st.st_mode),
                            (st.st_dev, st.st_ino),
                            _name_mount_id(fd, entry.name),
                        )
                    )
        except OSError as exc:
            raise _posix_failure(exc) from exc
        return out

    def open_dir(self, fd: int, name: str):
        probe = self._probe(fd, name)
        try:
            probed = self._fstat(probe)
            if _posix_kind(probed.st_mode) != DIRECTORY:
                return probe, self._info(probe)
            reopened = self._reopen(probe, self.reopen_dir_flags, probed)
        except BaseException:
            _safe_close(probe)
            raise
        _safe_close(probe)
        try:
            return reopened, self._info(reopened)
        except BaseException:
            _safe_close(reopened)
            raise

    def open_file(self, fd: int, name: str):
        probe = self._probe(fd, name)
        try:
            probed = self._fstat(probe)
            if _posix_kind(probed.st_mode) != FILE:
                # No data open: the shared walk classifies this as unstable.
                return probe, self._info(probe)
            reopened = self._reopen(probe, self.reopen_file_flags, probed)
        except BaseException:
            _safe_close(probe)
            raise
        _safe_close(probe)
        try:
            return reopened, self._info(reopened)
        except BaseException:
            _safe_close(reopened)
            raise

    def stat(self, fd: int) -> _Info:
        return self._info(fd)

    def read(self, fd: int, size: int) -> bytes:
        try:
            return os.read(fd, size)
        except OSError as exc:
            raise _posix_failure(exc) from exc

    def close(self, fd: int) -> None:
        _safe_close(fd)



# --- Windows ------------------------------------------------------------------

_STATUS_CODES = {
    0xC0000034: "not-found",  # STATUS_OBJECT_NAME_NOT_FOUND
    0xC000003A: "not-found",  # STATUS_OBJECT_PATH_NOT_FOUND
    0xC0000056: "not-found",  # STATUS_DELETE_PENDING
    0xC0000022: "denied",  # STATUS_ACCESS_DENIED
    0xC0000043: "in-use",  # STATUS_SHARING_VIOLATION
    0xC0000103: "wrong-type",  # STATUS_NOT_A_DIRECTORY
    0xC00000BA: "wrong-type",  # STATUS_FILE_IS_A_DIRECTORY
}
_STATUS_NO_MORE_FILES = 0x80000006
_STATUS_NO_SUCH_FILE = 0xC000000F

_FILE_READ_DATA = 0x0001  # FILE_LIST_DIRECTORY on a folder
_FILE_TRAVERSE = 0x0020
_FILE_READ_ATTRIBUTES = 0x0080
_SYNCHRONIZE = 0x00100000
_FILE_SHARE_READ = 0x1
_FILE_SHARE_WRITE = 0x2
_FILE_OPEN = 1
_FILE_DIRECTORY_FILE = 0x1
_FILE_SEQUENTIAL_ONLY = 0x4
_FILE_SYNCHRONOUS_IO_NONALERT = 0x20
_FILE_NON_DIRECTORY_FILE = 0x40
_FILE_OPEN_REPARSE_POINT = 0x00200000
_ATTR_DIRECTORY = 0x10
_ATTR_DEVICE = 0x40
_ATTR_REPARSE_POINT = 0x400
_FILE_TYPE_DISK = 1
_DRIVE_FIXED = 3
_FILE_ID_FULL_DIRECTORY_INFORMATION = 38
_FILE_BASIC_INFO = 0


def _parse_windows_directory_records(buffer: bytes, information: int, info_type, name_offset: int):
    """Validate a complete NT directory response before exposing any entries."""
    import ctypes

    header_size = ctypes.sizeof(info_type)
    if (name_offset != header_size or name_offset != info_type.FileId.offset + 8
            or info_type.NextEntryOffset.offset != 0
            or info_type.FileNameLength.offset != 60
            or info_type.FileAttributes.offset != 56
            or info_type.FileId.offset != 72):
        raise _Failure("other", "Unexpected NT directory record layout")
    if not isinstance(information, int) or information <= 0 or information > len(buffer):
        raise _Failure("other", "Invalid NT directory response length")
    entries = []
    offset = 0
    while True:
        if information - offset < header_size:
            raise _Failure("other", "Truncated NT directory record header")
        info = info_type.from_buffer_copy(buffer, offset)
        next_offset = info.NextEntryOffset
        if next_offset:
            if next_offset % 8 or next_offset < header_size or offset + next_offset + header_size > information:
                raise _Failure("other", "Invalid NT directory record offset")
            end = offset + next_offset
        else:
            end = information
        length = info.FileNameLength
        if not length or length % 2 or length > end - offset - name_offset:
            raise _Failure("other", "Invalid NT directory filename length")
        try:
            name = buffer[offset + name_offset:offset + name_offset + length].decode("utf-16-le", "strict")
        except UnicodeDecodeError as exc:
            raise _Failure("other", "Invalid NT directory filename encoding") from exc
        entries.append((name, info.FileAttributes, info.FileId & 0xFFFFFFFFFFFFFFFF))
        if not next_offset:
            return entries
        offset = end


class _WindowsBackend:
    name = "windows-nt-relative-open-reparse-point"

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wt = wintypes

        class UnicodeString(ctypes.Structure):
            _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT),
                        ("Buffer", ctypes.c_void_p)]

        class ObjectAttributes(ctypes.Structure):
            _fields_ = [("Length", wintypes.ULONG), ("RootDirectory", wintypes.HANDLE),
                        ("ObjectName", ctypes.POINTER(UnicodeString)), ("Attributes", wintypes.ULONG),
                        ("SecurityDescriptor", ctypes.c_void_p), ("SecurityQualityOfService", ctypes.c_void_p)]

        class IoStatusBlock(ctypes.Structure):
            _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

        class DirInfo(ctypes.Structure):  # FILE_ID_FULL_DIR_INFORMATION without FileName
            _fields_ = [("NextEntryOffset", wintypes.ULONG), ("FileIndex", wintypes.ULONG),
                        ("CreationTime", ctypes.c_longlong), ("LastAccessTime", ctypes.c_longlong),
                        ("LastWriteTime", ctypes.c_longlong), ("ChangeTime", ctypes.c_longlong),
                        ("EndOfFile", ctypes.c_longlong), ("AllocationSize", ctypes.c_longlong),
                        ("FileAttributes", wintypes.ULONG), ("FileNameLength", wintypes.ULONG),
                        ("EaSize", wintypes.ULONG), ("FileId", ctypes.c_longlong)]

        class ByHandleInfo(ctypes.Structure):
            _fields_ = [("dwFileAttributes", wintypes.DWORD), ("ftCreationTime", wintypes.FILETIME),
                        ("ftLastAccessTime", wintypes.FILETIME), ("ftLastWriteTime", wintypes.FILETIME),
                        ("dwVolumeSerialNumber", wintypes.DWORD), ("nFileSizeHigh", wintypes.DWORD),
                        ("nFileSizeLow", wintypes.DWORD), ("nNumberOfLinks", wintypes.DWORD),
                        ("nFileIndexHigh", wintypes.DWORD), ("nFileIndexLow", wintypes.DWORD)]

        class BasicInfo(ctypes.Structure):
            _fields_ = [("CreationTime", ctypes.c_longlong), ("LastAccessTime", ctypes.c_longlong),
                        ("LastWriteTime", ctypes.c_longlong), ("ChangeTime", ctypes.c_longlong),
                        ("FileAttributes", wintypes.DWORD)]

        self.UnicodeString, self.ObjectAttributes, self.IoStatusBlock = UnicodeString, ObjectAttributes, IoStatusBlock
        self.DirInfo, self.ByHandleInfo, self.BasicInfo = DirInfo, ByHandleInfo, BasicInfo
        self.name_offset = DirInfo.FileId.offset + ctypes.sizeof(ctypes.c_longlong)

        ntdll = ctypes.WinDLL("ntdll")
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.NtCreateFile = ntdll.NtCreateFile
        self.NtCreateFile.restype = ctypes.c_long
        self.NtCreateFile.argtypes = [
            ctypes.POINTER(wintypes.HANDLE), wintypes.ULONG, ctypes.POINTER(ObjectAttributes),
            ctypes.POINTER(IoStatusBlock), ctypes.c_void_p, wintypes.ULONG, wintypes.ULONG,
            wintypes.ULONG, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
        ]
        self.NtQueryDirectoryFile = ntdll.NtQueryDirectoryFile
        self.NtQueryDirectoryFile.restype = ctypes.c_long
        self.NtQueryDirectoryFile.argtypes = [
            wintypes.HANDLE, wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(IoStatusBlock), ctypes.c_void_p, wintypes.ULONG, ctypes.c_int,
            wintypes.BOOLEAN, ctypes.POINTER(UnicodeString), wintypes.BOOLEAN,
        ]
        self.GetFileInformationByHandle = k32.GetFileInformationByHandle
        self.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(ByHandleInfo)]
        self.GetFileInformationByHandle.restype = wintypes.BOOL
        self.GetFileInformationByHandleEx = k32.GetFileInformationByHandleEx
        self.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.GetFileInformationByHandleEx.restype = wintypes.BOOL
        self.GetFileType = k32.GetFileType
        self.GetFileType.argtypes = [wintypes.HANDLE]
        self.GetFileType.restype = wintypes.DWORD
        self.GetVolumeInformationByHandleW = k32.GetVolumeInformationByHandleW
        self.GetVolumeInformationByHandleW.argtypes = [
            wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD,
        ]
        self.GetVolumeInformationByHandleW.restype = wintypes.BOOL
        self.GetDriveTypeW = k32.GetDriveTypeW
        self.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        self.GetDriveTypeW.restype = wintypes.UINT
        self.QueryDosDeviceW = k32.QueryDosDeviceW
        self.QueryDosDeviceW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        self.QueryDosDeviceW.restype = wintypes.DWORD
        self.ReadFile = k32.ReadFile
        self.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                  ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        self.ReadFile.restype = wintypes.BOOL
        self.CloseHandle = k32.CloseHandle
        self.CloseHandle.argtypes = [wintypes.HANDLE]
        self.CloseHandle.restype = wintypes.BOOL
        self.read_buffer = ctypes.create_string_buffer(_CHUNK)

    # NT primitives

    def _unicode(self, text: str):
        buf = self.ctypes.create_unicode_buffer(text)
        length = len(text.encode("utf-16-le", "surrogatepass"))
        us = self.UnicodeString(length, length + 2, self.ctypes.cast(buf, self.ctypes.c_void_p))
        return us, buf  # keep buf alive while us is used

    def _nt_open(self, parent, name: str, directory: bool, ancestor: bool = False) -> int:
        ctypes = self.ctypes
        us, keep = self._unicode(name)
        attrs = self.ObjectAttributes(ctypes.sizeof(self.ObjectAttributes), parent, ctypes.pointer(us), 0, None, None)
        handle = self.wt.HANDLE()
        iosb = self.IoStatusBlock()
        if directory:
            access = _FILE_READ_DATA | _FILE_TRAVERSE | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE
            options = _FILE_DIRECTORY_FILE
        else:
            access = _FILE_READ_DATA | _FILE_READ_ATTRIBUTES | _SYNCHRONIZE
            options = _FILE_NON_DIRECTORY_FILE | _FILE_SEQUENTIAL_ONLY
        # No delete sharing: while this handle is open the object cannot be
        # deleted, renamed or replaced. Inside the artifact, no write sharing
        # either, so files cannot be written and nothing can be renamed or
        # hard-linked into its folders. Ancestors allow writes so the scan does
        # not block other programs saving into, e.g., the user's home folder.
        # FILE_OPEN_REPARSE_POINT opens a link or junction itself instead of
        # what it points to.
        share = _FILE_SHARE_READ | (_FILE_SHARE_WRITE if ancestor else 0)
        status = self.NtCreateFile(
            ctypes.byref(handle), access, ctypes.byref(attrs), ctypes.byref(iosb), None, 0,
            share, _FILE_OPEN, options | _FILE_SYNCHRONOUS_IO_NONALERT | _FILE_OPEN_REPARSE_POINT,
            None, 0,
        ) & 0xFFFFFFFF
        del keep
        if status:
            raise _Failure(_STATUS_CODES.get(status, "other"), f"NTSTATUS 0x{status:08X}")
        return handle.value

    def _query(self, handle, pattern: Optional[str] = None, limit: Optional[int] = None) -> Optional[list[tuple]]:
        """Entries of the folder ``handle``; with ``pattern``, at most the one
        entry it matches. Returns None as soon as there are more than ``limit``."""
        ctypes = self.ctypes
        buf = ctypes.create_string_buffer(64 * 1024)
        us = keep = None
        if pattern is not None:
            us, keep = self._unicode(pattern)
        out = []
        restart = True
        while True:
            iosb = self.IoStatusBlock()
            status = self.NtQueryDirectoryFile(
                handle, None, None, None, ctypes.byref(iosb), buf, len(buf),
                _FILE_ID_FULL_DIRECTORY_INFORMATION, pattern is not None,
                ctypes.byref(us) if us is not None else None, restart,
            ) & 0xFFFFFFFF
            restart = False
            if status in (_STATUS_NO_MORE_FILES, _STATUS_NO_SUCH_FILE):
                break
            if status:
                raise _Failure(_STATUS_CODES.get(status, "other"), f"NTSTATUS 0x{status:08X} listing folder")
            records = _parse_windows_directory_records(buf.raw, iosb.Information, self.DirInfo, self.name_offset)
            for name, attributes, file_id in records:
                if name not in (".", ".."):
                    out.append((name, attributes, file_id))
                    if limit is not None and len(out) > limit:
                        return None
            if pattern is not None:
                break
        del keep
        return out

    def _win_error(self, what: str) -> _Failure:
        code = self.ctypes.get_last_error()
        return _Failure("denied" if code == 5 else "other", f"{what} failed (WinError {code})")

    @staticmethod
    def _kind(attrs: int) -> str:
        if attrs & _ATTR_REPARSE_POINT:
            return LINK
        if attrs & _ATTR_DIRECTORY:
            return DIRECTORY
        if attrs & _ATTR_DEVICE:
            return SPECIAL
        return FILE

    # backend interface

    def open_root(self, root: str):
        wt = self.wt
        path = root.replace("/", "\\")
        if len(path) < 4 or not (path[0].isascii() and path[0].isalpha()) or path[1:3] != ":\\":
            raise DirectoryScanError("invalid-root", f"Root must be an absolute path on a drive letter, e.g. C:\\data: {root!r}")
        parts = _split_absolute(path[3:], "\\")
        if any(ch in part for part in parts for ch in '*?<>"'):
            raise DirectoryScanError("invalid-root", f"Root contains wildcard characters: {root!r}")
        drive = path[0].upper() + ":"
        if self.GetDriveTypeW(drive + "\\") != _DRIVE_FIXED:
            raise _Failure("unsupported", f"{drive} is not a fixed local drive")
        target = self.ctypes.create_unicode_buffer(1024)
        if not self.QueryDosDeviceW(drive, target, len(target)) or not target.value.startswith("\\Device\\"):
            raise _Failure("unsupported", f"{drive} is a substituted or redirected drive")
        held = [self._nt_open(None, "\\??\\" + drive + "\\", True, ancestor=True)]
        try:
            fs = self.ctypes.create_unicode_buffer(64)
            if not self.GetVolumeInformationByHandleW(held[0], None, 0, None, None, None, fs, len(fs)):
                raise self._win_error("GetVolumeInformationByHandleW")
            if fs.value != "NTFS":
                raise _Failure("unsupported", f"{drive} uses {fs.value or 'an unknown file system'}, not NTFS")
            for i, part in enumerate(parts):
                found = self._query(held[-1], part)
                if not found:
                    raise _Failure("not-found", f"{part!r} does not exist")
                name, attrs, file_id = found[0]
                if name != part:
                    raise DirectoryScanError("unsafe", f"{part!r} is spelled {name!r} on disk")
                kind = self._kind(attrs)
                if kind == LINK:
                    raise _Failure("link", f"{part!r} is a link, junction or other reparse point")
                if kind != DIRECTORY:
                    raise _Failure("wrong-type", f"{part!r} is not a folder")
                held.append(self._nt_open(held[-1], part, True, ancestor=i < len(parts) - 1))
                info = self.stat(held[-1])
                if info.kind != DIRECTORY or info.identity[1] != file_id:
                    raise _Failure("in-use", f"{part!r} was replaced while it was opened")
            return held, self.stat(held[-1])
        except BaseException:
            for handle in reversed(held):
                self.close(handle)
            raise

    def list(self, handle, budget: int) -> Optional[list[_Entry]]:
        volume = self.stat(handle).identity[0]
        found = self._query(handle, limit=budget)
        if found is None:
            return None
        return [_Entry(name, self._kind(attrs), (volume, file_id)) for name, attrs, file_id in found]

    def open_dir(self, handle, name: str):
        child = self._nt_open(handle, name, True)
        try:
            return child, self.stat(child)
        except BaseException:
            self.close(child)
            raise

    def open_file(self, handle, name: str):
        child = self._nt_open(handle, name, False)
        try:
            return child, self.stat(child)
        except BaseException:
            self.close(child)
            raise

    def stat(self, handle) -> _Info:
        ctypes = self.ctypes
        info = self.ByHandleInfo()
        if not self.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise self._win_error("GetFileInformationByHandle")
        basic = self.BasicInfo()
        if not self.GetFileInformationByHandleEx(handle, _FILE_BASIC_INFO, ctypes.byref(basic), ctypes.sizeof(basic)):
            raise self._win_error("GetFileInformationByHandleEx")
        kind = self._kind(info.dwFileAttributes)
        if kind == FILE and self.GetFileType(handle) != _FILE_TYPE_DISK:
            kind = SPECIAL
        size = (info.nFileSizeHigh << 32) | info.nFileSizeLow
        return _Info(
            kind,
            (info.dwVolumeSerialNumber, (info.nFileIndexHigh << 32) | info.nFileIndexLow),
            size if kind == FILE else 0,
            (basic.LastWriteTime, basic.ChangeTime),
            info.nNumberOfLinks,
        )

    def read(self, handle, size: int) -> bytes:
        got = self.wt.DWORD()
        size = min(size, len(self.read_buffer))
        if not self.ReadFile(handle, self.read_buffer, size, self.ctypes.byref(got), None):
            raise self._win_error("ReadFile")
        return self.ctypes.string_at(self.read_buffer, got.value)

    def close(self, handle) -> None:
        self.CloseHandle(handle)
