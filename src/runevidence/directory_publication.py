"""Internal guarded publication for directory artifacts (S4-GP/1, slice S4-1).

Internal only: it is not exported by ``runevidence`` and it adds no public
write surface. No command imports the publication API. S4-2a's built-in
manifest writers import only ``builtin_writer_admission_lock`` (lazily, in the
write commands) to serialize on the same folder lock; that helper performs no
scan, staging, backup or replacement. The public directory authoring command
remains not authorized (HOLD-1); this module only provides the core that a
future, separately authorized slice could call.

The protocol is the RFC's S4-GP/1, not a compare-and-swap:

1. ``manifest_admission_lock`` resolves the manifest target and takes one
   exclusive, non-blocking ``flock`` on the manifest folder's directory
   descriptor. Acquisition is separate from loading, so a writer can lock a
   folder that has no manifest yet. The descriptor is ``O_CLOEXEC``; a second
   acquisition in the same process is ``busy`` (self-busy). The caller must not
   fork while holding it; the handle records its process id and refuses use
   from a forked child.
2. ``load_manifest_under_lock`` reopens the manifest by ``dir_fd`` with
   ``O_NOFOLLOW``, checks regular/``nlink == 1``, reads the bytes from the held
   descriptor, and verifies the name's identity. This snapshot is
   authoritative. The operation never re-locks for load, proposal, rebuild or
   publication.
3. The protocol fsyncs the loaded source and the locked folder before staging
   (``source-durability-failed`` otherwise). A process crash keeps the page
   cache and is therefore not a power-loss simulation; a power loss is only
   covered after that precondition succeeds.
4. A private staging cell (``0700``, owner-only access/default ACL, inherited
   non-security xattrs removed) is created before any content. The staged
   manifest and the backup are opened ``O_RDWR`` and verified with ``pread``
   through the held descriptor. The staged file is built up from ``0600`` to
   the source's complete metadata set, which is verified both ways:
   ``security.*`` labels are never deleted, inode flags use an exact
   user-settable mask with unknown bits refused, and project ID is preserved
   or explicitly narrowed when its ioctl is unavailable.
5. The backup is written, fsynced, linked to the newest generation + 1, and the
   folder is fsynced before replacement. A pre-link failure publishes no
   ``.bak.N``; a post-link fsync failure preserves the complete backup and
   refuses replacement (``backup-durability-failed``).
6. A final guard rechecks the locked folder, the manifest name/bytes and the
   metadata. Only then is the manifest replaced and the folder fsynced. A
   failure after replacement is ``durability-unknown`` (written, exit 0); an
   unexpected exception carries the three-phase flag used by a future public
   boundary, with fixed text and no traceback, path, member, ``strerror`` or
   digest.

All manifest, cell, staging, backup, link, unlink, replace, xattr and flag
operations use the locked directory fd or held descriptors. A parent swap
cannot redirect them; the final guard refuses when the path no longer names
the locked folder.

What is not claimed: CAS against nonparticipating writers, a frozen tree, a
snapshot, whole-file atomicity for an unrelated truncating writer, detection
of an ABA change, Windows/macOS parity, preservation on a hostile host, or
power-loss recovery from a source that failed the durability precondition.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import os
import secrets
import stat
import struct
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Literal, Optional

from . import directory_scanner
from .directory_apply import (
    DirectoryApplyError,
    DirectoryApplyResult,
    check_directory_proposal,
)
from .directory_authoring import DirectoryAuthoringError, DirectoryProposal
from .edit import EditError
from .manifest import (
    LoadedManifest,
    ManifestLoadError,
    parse_manifest_source,
    resolve_manifest_path,
)
from .models import MANIFEST_FILENAME, backup_generation

Phase = Literal["before-cell", "cell-or-backup", "at-replace"]
Status = Literal["written", "unchanged", "dry-run"]

CELL_PREFIX = ".runevidence-s4-stage-"
STAGED_NAME = "new"
BACKUP_TEMP_NAME = "backup"
MAX_CELL_ATTEMPTS = 64
MAX_BACKUP_ATTEMPTS = 32
# One resolve -> open/flock -> re-resolve cycle. A bounded retry closes the
# alias-retarget window between resolution and acquisition; continuous churn
# fails closed instead of locking an unknown folder.
WRITER_LOCK_ATTEMPTS = 3

_ACL_ACCESS = "system.posix_acl_access"
_ACL_DEFAULT = "system.posix_acl_default"
_SECURITY_PREFIX = "security."
_USER_PREFIX = "user."
_TRUSTED_PREFIX = "trusted."
_ENOATTR = getattr(errno, "ENOATTR", errno.ENODATA)

# Linux fs.h inode flags. The user-settable mask is deliberately the exact
# RFC set; every other known bit is recorded under the G6 narrowing, and an
# unclassified bit refuses.
_FS_SECRM_FL = 0x00000001
_FS_UNRM_FL = 0x00000002
_FS_COMPR_FL = 0x00000004
_FS_SYNC_FL = 0x00000008
_FS_IMMUTABLE_FL = 0x00000010
_FS_APPEND_FL = 0x00000020
_FS_NODUMP_FL = 0x00000040
_FS_NOATIME_FL = 0x00000080
_FS_DIRTY_FL = 0x00000100
_FS_COMPRBLK_FL = 0x00000200
_FS_NOCOMP_FL = 0x00000400
_FS_ENCRYPT_FL = 0x00000800
_FS_BTREE_FL = 0x00001000
_FS_INDEX_FL = 0x00001000
_FS_IMAGIC_FL = 0x00002000
_FS_JOURNAL_DATA_FL = 0x00004000
_FS_NOTAIL_FL = 0x00008000
_FS_DIRSYNC_FL = 0x00010000
_FS_TOPDIR_FL = 0x00020000
_FS_EXTENT_FL = 0x00080000
_FS_VERITY_FL = 0x00100000
_FS_EA_INODE_FL = 0x00200000
_FS_EOFBLOCKS_FL = 0x00400000
_FS_NOCOW_FL = 0x00800000
_FS_DAX_FL = 0x02000000
_FS_INLINE_DATA_FL = 0x10000000
_FS_PROJINHERIT_FL = 0x20000000
_FS_CASEFOLD_FL = 0x40000000

_INODE_IMMUTABLE = _FS_IMMUTABLE_FL | _FS_APPEND_FL
_INODE_USER_MASK = (
    _FS_NODUMP_FL | _FS_NOATIME_FL | _FS_SYNC_FL | _FS_COMPR_FL
    | _FS_JOURNAL_DATA_FL | _FS_NOTAIL_FL | _FS_PROJINHERIT_FL
)
_INODE_MANAGED_MASK = (
    _FS_SECRM_FL | _FS_UNRM_FL | _FS_DIRTY_FL | _FS_COMPRBLK_FL | _FS_NOCOMP_FL
    | _FS_ENCRYPT_FL | _FS_BTREE_FL | _FS_INDEX_FL | _FS_IMAGIC_FL | _FS_DIRSYNC_FL
    | _FS_TOPDIR_FL | _FS_EXTENT_FL | _FS_VERITY_FL | _FS_EA_INODE_FL
    | _FS_EOFBLOCKS_FL | _FS_NOCOW_FL | _FS_DAX_FL | _FS_INLINE_DATA_FL
    | _FS_CASEFOLD_FL
)
_INODE_KNOWN_MASK = _INODE_IMMUTABLE | _INODE_USER_MASK | _INODE_MANAGED_MASK

_FS_IOC_GETFLAGS = 0x80086601
_FS_IOC_SETFLAGS = 0x40086602
_FS_IOC_FSGETXATTR = 0x801C581F
_FS_IOC_FSSETXATTR = 0x401C5820
_FS_XFLAG_PROJINHERIT = 0x00000200

_ACL_USER_OBJ = 0x01
_ACL_USER = 0x02
_ACL_GROUP_OBJ = 0x04
_ACL_GROUP = 0x08
_ACL_OTHER = 0x20

_PHASES = ("before-cell", "cell-or-backup", "at-replace")


class _FsXattr(ctypes.Structure):
    _fields_ = [
        ("fsx_xflags", ctypes.c_uint32),
        ("fsx_extsize", ctypes.c_uint32),
        ("fsx_nextents", ctypes.c_uint32),
        ("fsx_projid", ctypes.c_uint32),
        ("fsx_cowextsize", ctypes.c_uint32),
        ("fsx_pad", ctypes.c_ubyte * 8),
    ]


_REASON_TEXT = {
    "busy": "another RunEvidence writer is updating the manifest",
    "lock-failed": "the manifest folder could not be locked",
    "lock-ownership-lost": "the locked manifest folder changed while it was held",
    "stale-source": "the manifest changed while it was being updated",
    "stale-metadata": "the manifest's metadata changed after the preflight",
    "stale-tree": "the directory changed while it was being checked",
    "source-durability-failed": (
        "the manifest could not be made durable before updating; old-or-new power-loss "
        "recovery is not claimed"
    ),
    "recheck-failed": "the directory proposal cannot be made again now",
    "proposal-mismatch": "the proposal does not match the manifest and the directory",
    "metadata-unsupported": "the manifest's metadata cannot be preserved exactly",
    "unsupported-platform": "directory publication is not supported on this platform",
    "unsupported-filesystem": "the manifest folder is not on a release-qualified filesystem",
    "invalid-path": "the directory path is not an accepted v1 path",
    "limit-exceeded": "the directory exceeds a v1 limit",
    "invalid-manifest": "the manifest is not a valid manifest",
    "invalid-artifact": "the new entry would not be a valid artifact",
    "invalid-request": "the internal request is not valid",
    "duplicate-id": "the artifact ID is already used",
    "unknown-run": "the run does not exist",
    "volatile-unsupported": "a volatile directory cannot be recorded",
    "path-collision": "another artifact already records that path",
    "overlap": "the directory overlaps another artifact",
    "not-found": "no such manifest or artifact",
    "not-a-directory-artifact": "the artifact is not a directory artifact",
    "stale": "the loaded manifest and the proposal disagree",
    "locator-unsupported": "a result reads a JSON locator from the directory",
    "outside-root": "the directory is outside the manifest folder",
    "permission-denied": "the manifest or its folder is not writable by this user",
    "write-failed": "the directory update could not be written",
}

_PHASE_SUFFIX = {
    "before-cell": "Nothing was written.",
    "cell-or-backup": "The manifest was not changed; a backup or staging folder may remain.",
    "at-replace": (
        "The manifest may have been replaced; run runevidence validate with "
        "--directory-artifacts."
    ),
}

_INTERNAL_PHASE_TEXT = {
    "before-cell": (
        "ERROR (INTERNAL_ERROR): the directory update failed internally. Nothing was written."
    ),
    "cell-or-backup": (
        "ERROR (INTERNAL_ERROR): the directory update failed internally. The manifest was not "
        "changed; a backup or staging folder may remain."
    ),
    "at-replace": (
        "ERROR (INTERNAL_ERROR): the directory update failed internally after the manifest may "
        "have been replaced. Run runevidence validate PATH --directory-artifacts."
    ),
}


class DirectoryPublicationError(EditError):
    """The guarded publication did not complete. ``reason`` is a fixed code and
    ``phase`` is the three-valued public boundary flag. ``note`` is a fixed
    qualifier (currently only ``project-id-mismatch``). The message is fixed
    text: no path, member name, ``strerror``, inode/device/mount id, digest or
    traceback is included."""

    def __init__(self, reason: str, *, phase: Phase = "before-cell", note: Optional[str] = None):
        if phase not in _PHASES:
            raise ValueError(f"unknown phase {phase!r}")
        self.reason = reason
        self.phase = phase
        self.note = note
        super().__init__(_message(reason, phase, note))


@dataclass(frozen=True)
class DirectoryPublicationResult:
    status: Status
    operation: Literal["add", "rehash"]
    artifact_id: str
    path: str
    manifest_path: Path
    backup_path: Optional[Path]
    old_sha256: Optional[str]
    new_sha256: str
    old_size: Optional[int]
    new_size: int
    members: int
    notes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    phase: Phase = "before-cell"
    inode_flags: Optional[int] = None
    project_id: Optional[int] = None

    @property
    def written(self) -> bool:
        return self.status == "written"


@dataclass(frozen=True)
class MetadataSnapshot:
    """The complete detectable metadata set of one descriptor."""

    mode: int
    uid: int
    gid: int
    xattrs: tuple[tuple[str, bytes], ...]
    inode_flags: int
    project: Optional[tuple[int, bool]]  # (project id, PROJINHERIT); None when narrowed
    notes: tuple[str, ...] = ()

    @property
    def acl_access(self) -> Optional[bytes]:
        return dict(self.xattrs).get(_ACL_ACCESS)

    @property
    def acl_default(self) -> Optional[bytes]:
        return dict(self.xattrs).get(_ACL_DEFAULT)


@dataclass(frozen=True)
class PublicationPreflight:
    """The step-4 admission result a caller may take before proposing a scan."""

    metadata: MetadataSnapshot
    inode_flags: int
    project_id: Optional[int]


@dataclass
class LockedManifestFolder:
    """One held admission lock: the locked folder descriptor is the token.

    ``fd`` must be passed down; do not reopen the folder to "re-lock". The
    handle refuses use from a forked child and verifies the path still names
    the locked inode before each destructive decision."""

    target: Path
    parent: Path
    fd: int
    identity: tuple[int, int]
    pid: int
    loaded: Optional[LoadedManifest] = None
    manifest_fd: Optional[int] = None
    manifest_identity: Optional[tuple[int, int]] = None
    metadata: Optional[MetadataSnapshot] = None
    release_notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def name(self) -> str:
        return self.target.name

    def verify_owner(self, *, phase: Phase = "cell-or-backup") -> None:
        """Refuse if this handle is not the live, locked folder in this process."""
        if os.getpid() != self.pid:
            raise DirectoryPublicationError("lock-ownership-lost", phase=phase)
        if self.manifest_fd is not None and not _cloexec(self.manifest_fd):
            raise DirectoryPublicationError("lock-failed", phase=phase)
        if not _cloexec(self.fd):
            raise DirectoryPublicationError("lock-failed", phase=phase)
        try:
            st = os.fstat(self.fd)
        except OSError:
            raise DirectoryPublicationError("lock-ownership-lost", phase=phase) from None
        if not stat.S_ISDIR(st.st_mode) or (st.st_dev, st.st_ino) != self.identity:
            raise DirectoryPublicationError("lock-ownership-lost", phase=phase)
        try:
            path_st = os.stat(self.parent)
        except OSError:
            raise DirectoryPublicationError("lock-ownership-lost", phase=phase) from None
        if (path_st.st_dev, path_st.st_ino) != self.identity:
            raise DirectoryPublicationError("lock-ownership-lost", phase=phase)


# --- public-internal API ------------------------------------------------------


@contextmanager
def manifest_admission_lock(target: "os.PathLike[str] | str") -> Iterator[LockedManifestFolder]:
    """Resolve ``target`` and hold the manifest folder's admission lock.

    The manifest need not exist yet. Raises DirectoryPublicationError with a
    fixed reason. The lock is released by closing the descriptor in
    ``finally``; a close failure is a cleanup warning, never a second write."""
    if not _platform_supported():
        raise DirectoryPublicationError("unsupported-platform")
    target_path, parent = _resolve_target(target)
    locked = _acquire_folder_lock(target_path, parent)
    try:
        _classify_manifest_folder(locked)
        yield locked
    finally:
        _release_folder_lock(locked)


@contextmanager
def builtin_writer_admission_lock(
    target: "os.PathLike[str] | str",
    *,
    follow_manifest_symlink: bool = True,
) -> Iterator[LockedManifestFolder]:
    """Hold the same folder admission lock for a built-in manifest writer.

    S4-2a uses this in ``init``/``add``/``rehash``/``migrate``/``conditions``:
    the caller acquires it before loading the manifest and keeps it through the
    save. It is the same exclusive ``flock`` on the same resolved folder as
    ``manifest_admission_lock``, so the two contend. Unlike that function it
    deliberately does not run the S4-1 release-filesystem classification, and
    it does not impose the S4-1 single-link regular-file rule on the manifest.

    ``follow_manifest_symlink`` is true for the load/save writers (``add``,
    ``rehash``, ``migrate``, ``conditions``), which always loaded and saved the
    resolved target, and false for ``init``, which replaces the entry it names
    in the named folder and keeps its backup there. In both modes the containing
    folder is strictly resolved, locked and re-checked after acquisition. After
    acquisition the target is resolved again and compared with the locked folder
    identity; a changed resolution releases the lock and retries a bounded
    number of times, and persistent churn is ``lock-failed`` so no caller
    proceeds under a stale lock. Only ``busy`` and ``lock-failed`` can be
    raised; the command boundary fails closed on the latter. No scan, staging,
    publication or write API is reachable through this handle.
    """
    if not _writer_lock_supported():
        raise DirectoryPublicationError("lock-failed")
    locked = None
    for _ in range(WRITER_LOCK_ATTEMPTS):
        target_path, parent = _resolve_writer_folder(
            target, follow_manifest_symlink=follow_manifest_symlink
        )
        _boundary_before_writer_lock_open(target_path, parent)
        locked = _acquire_folder_lock(target_path, parent)
        try:
            # Re-resolve after acquisition. A symlink retarget (or a directory
            # appearing in the path) between the first resolution and the flock
            # must not leave us holding a folder that no longer contains the
            # manifest the caller named. The identity validation is inside this
            # BaseException guard so an interrupt there also releases the fd.
            current_target, current_parent = _resolve_writer_folder(
                target, follow_manifest_symlink=follow_manifest_symlink
            )
            if _locked_resolution_matches(locked, current_target, current_parent):
                break
        except BaseException:
            _release_folder_lock(locked)
            locked = None
            raise
        _release_folder_lock(locked)
        locked = None
    if locked is None:
        # Resolution kept changing; never proceed under a stale lock.
        raise DirectoryPublicationError("lock-failed")
    try:
        yield locked
    finally:
        _release_folder_lock(locked)


def _acquire_folder_lock(
    target_path: Path, parent: Path
) -> LockedManifestFolder:
    """Open ``parent`` and take one non-blocking exclusive lock on it."""
    try:
        fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        raise DirectoryPublicationError("lock-failed") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode) or not _cloexec(fd):
            raise DirectoryPublicationError("lock-failed")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise DirectoryPublicationError("busy") from None
            raise DirectoryPublicationError("lock-failed") from None
        return LockedManifestFolder(
            target=target_path,
            parent=parent,
            fd=fd,
            identity=(st.st_dev, st.st_ino),
            pid=os.getpid(),
        )
    except BaseException:
        _close_quietly(fd)
        raise


def _release_folder_lock(locked: LockedManifestFolder) -> None:
    """Close both held descriptors; a close failure becomes a cleanup note."""
    for detail in _close_with_note(locked.manifest_fd), _close_with_note(locked.fd):
        if detail is not None:
            locked.release_notes = locked.release_notes + (detail,)
    locked.manifest_fd = None


def load_manifest_under_lock(
    locked: LockedManifestFolder, target: "os.PathLike[str] | str | None" = None
) -> LoadedManifest:
    """Load the manifest through ``locked``'s held folder descriptor.

    The load never re-locks and never falls back to the path. Raises
    DirectoryPublicationError."""
    if not isinstance(locked, LockedManifestFolder):
        raise DirectoryPublicationError("invalid-request")
    if target is not None:
        wanted, parent = _resolve_target(target)
        if wanted != locked.target or parent != locked.parent:
            raise DirectoryPublicationError("invalid-request")
    locked.verify_owner(phase="before-cell")
    name = locked.name
    try:
        # O_NONBLOCK: a FIFO swapped in after verify_owner must not make the
        # load wait forever while the admission lock is held. The fstat below
        # still requires a regular file, so a pipe is refused as stale-source.
        fd = os.open(
            name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOCTTY,
            dir_fd=locked.fd,
        )
    except FileNotFoundError:
        raise DirectoryPublicationError("not-found") from None
    except OSError:
        raise DirectoryPublicationError("stale-source") from None
    try:
        try:
            st = os.fstat(fd)
            path_st = os.stat(name, dir_fd=locked.fd, follow_symlinks=False)
        except OSError:
            raise DirectoryPublicationError("stale-source") from None
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
                or (st.st_dev, st.st_ino) != (path_st.st_dev, path_st.st_ino)
                or path_st.st_nlink != 1):
            raise DirectoryPublicationError("stale-source")
        try:
            source_bytes = _read_fd(fd)
        except OSError:
            raise DirectoryPublicationError("stale-source") from None
        try:
            loaded = parse_manifest_source(locked.target, source_bytes)
        except ManifestLoadError:
            raise DirectoryPublicationError("invalid-manifest") from None
        locked.loaded = loaded
        locked.manifest_fd = fd
        locked.manifest_identity = (st.st_dev, st.st_ino)
        fd = -1  # owned by the handle now
        return loaded
    finally:
        if fd >= 0:
            _close_quietly(fd)


def preflight_directory_publication(locked: LockedManifestFolder) -> PublicationPreflight:
    """Take the step-4 admission preflight before any proposal scan.

    A caller that composes ``lock -> load -> preflight -> propose -> publish``
    gets the permission/metadata/source-durability refusals before the first
    scan. ``publish_directory_proposal`` performs the same preflight when the
    caller did not, so both compositions are safe; the held lock handle keeps
    one acquisition either way."""
    if not isinstance(locked, LockedManifestFolder) or locked.manifest_fd is None:
        raise DirectoryPublicationError("invalid-request")
    locked.verify_owner(phase="before-cell")
    metadata = _capture_metadata(locked.manifest_fd)
    _preflight_metadata(metadata)
    _preflight_project(locked, metadata)
    _check_manifest_access(locked, metadata)
    _source_durability(locked)
    locked.metadata = metadata
    return PublicationPreflight(
        metadata=metadata,
        inode_flags=metadata.inode_flags,
        project_id=None if metadata.project is None else metadata.project[0],
    )


def publish_directory_proposal(
    locked: Optional[LockedManifestFolder],
    proposal: DirectoryProposal,
    *,
    dry_run: bool = False,
    loaded: Optional[LoadedManifest] = None,
) -> DirectoryPublicationResult:
    """Check and, unless ``dry_run``, durably publish one directory proposal.

    A real write needs the held ``locked`` handle from
    ``manifest_admission_lock`` and its ``load_manifest_under_lock`` result. If
    the caller did not run ``preflight_directory_publication`` first, this call
    runs the same admission preflight. A dry run is a separate branch: pass
    ``locked=None`` and ``loaded``; it takes no lock, performs no
    source/parent fsync, creates no cell/backup and requires no write
    permission.

    Raises DirectoryPublicationError (or the internal authoring/apply errors
    are mapped to it)."""
    if type(dry_run) is not bool:
        raise DirectoryPublicationError("invalid-request")
    if not _platform_supported():
        raise DirectoryPublicationError("unsupported-platform")
    if dry_run:
        if locked is not None:
            raise DirectoryPublicationError("invalid-request")
        if type(loaded) is not LoadedManifest or type(proposal) is not DirectoryProposal:
            raise DirectoryPublicationError("invalid-request")
        return _dry_run(loaded, proposal)
    if not isinstance(locked, LockedManifestFolder) or type(proposal) is not DirectoryProposal:
        raise DirectoryPublicationError("invalid-request")
    if loaded is None:
        loaded = locked.loaded
    if type(loaded) is not LoadedManifest or locked.loaded is not loaded:
        raise DirectoryPublicationError("invalid-request")
    if locked.manifest_fd is None:
        raise DirectoryPublicationError("invalid-request")
    return _publish(locked, loaded, proposal)


# --- real path ----------------------------------------------------------------


def _publish(
    locked: LockedManifestFolder, loaded: LoadedManifest, proposal: DirectoryProposal
) -> DirectoryPublicationResult:
    phase: Phase = "before-cell"
    metadata: Optional[MetadataSnapshot] = None
    checked: Optional[DirectoryApplyResult] = None
    backup_path: Optional[Path] = None
    cell_name: Optional[str] = None
    cell_fd: Optional[int] = None
    inode_flags: Optional[int] = None
    project_id: Optional[int] = None
    notes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    try:
        locked.verify_owner(phase="before-cell")
        if locked.metadata is None:
            preflight_directory_publication(locked)
        metadata = locked.metadata
        assert metadata is not None

        checked = check_directory_proposal(
            loaded, proposal, source_reader=lambda: _read_fd(locked.manifest_fd)
        )
        if checked.status == "unchanged":
            return _result(
                "unchanged", loaded, checked, backup=None, notes=(),
                warnings=(), phase="before-cell", inode_flags=None, project_id=None,
            )

        # Step 7: authoritative held-descriptor source and metadata check.
        _verify_source_state(locked, metadata, phase="before-cell")

        # From the first mkdir attempt onward a staging cell may exist, so the
        # public phase is cell-or-backup even if creation itself fails.
        phase = "cell-or-backup"
        cell_name, cell_fd = _create_cell(locked)
        _restrict_cell(locked, cell_name, cell_fd)
        _boundary_cell_created(locked, cell_name, cell_fd)

        staged_fd = _create_staged(cell_fd)
        try:
            _boundary_staged_created(cell_fd, staged_fd)
            _remove_inherited_xattrs(staged_fd, metadata)
            _assert_private(staged_fd, "file")
            _write_all(staged_fd, checked.text.encode("utf-8"))
            _fsync(staged_fd, role="staged")
            _verify_read_back(staged_fd, checked.text.encode("utf-8"))
            _assert_private(staged_fd, "file")
            _boundary_staged_written(cell_fd, staged_fd)

            _apply_metadata(staged_fd, metadata)
            extra = _verify_complete_metadata(staged_fd, metadata)
            _assert_access_matches_source(staged_fd, metadata)
            _boundary_staged_metadata(cell_fd, staged_fd)
            inode_flags = _read_inode_flags(staged_fd)
            project_id = None if metadata.project is None else metadata.project[0]
            notes = extra
        finally:
            _close_quietly(staged_fd)

        backup_path, warnings = _publish_backup(locked, cell_fd, loaded.source_bytes, phase)

        # Step 11: final guard, after the backup is published. A refusal here
        # leaves a complete backup and removes the cell best-effort.
        _verify_source_state(locked, metadata, phase="cell-or-backup")
        _boundary_final_guard(locked, backup_path)

        _boundary_before_replace(locked, backup_path)
        phase = "at-replace"
        try:
            _replace_manifest(cell_fd, locked.fd, locked.name)
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                raise DirectoryPublicationError(
                    "write-failed", phase=phase, note="project-id-mismatch"
                ) from None
            raise DirectoryPublicationError("write-failed", phase=phase) from None

        _boundary_after_replace(locked, backup_path)
        try:
            _fsync(locked.fd, role="replace-parent")
        except OSError:
            warnings = warnings + ("durability-unknown",)
        else:
            _boundary_after_parent_fsync(locked, backup_path)

        return _result(
            "written", loaded, checked, backup=backup_path, notes=notes,
            warnings=warnings, phase="at-replace", inode_flags=inode_flags,
            project_id=project_id,
        )
    except DirectoryPublicationError as exc:
        if exc.phase != phase:
            raise DirectoryPublicationError(exc.reason, phase=phase, note=exc.note) from None
        raise
    except DirectoryAuthoringError as exc:
        raise _map_authoring(exc) from None
    except DirectoryApplyError as exc:
        raise _map_apply(exc) from None
    except (OSError, ValueError):
        raise DirectoryPublicationError("write-failed", phase=phase) from None
    except Exception:
        raise DirectoryPublicationError("INTERNAL_ERROR", phase=phase) from None
    finally:
        if cell_fd is not None:
            _discard_cell(locked, cell_name, cell_fd)


def _dry_run(loaded: LoadedManifest, proposal: DirectoryProposal) -> DirectoryPublicationResult:
    """Read-only check branch: no lock, no fsync, no cell, no write."""
    metadata = None
    fd = -1
    try:
        try:
            fd = os.open(
                loaded.path,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0),
            )
        except OSError:
            raise DirectoryPublicationError("stale-source") from None
        try:
            st = os.fstat(fd)
        except OSError:
            raise DirectoryPublicationError("stale-source") from None
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise DirectoryPublicationError("stale-source")
        metadata = _capture_metadata(fd)
        _preflight_metadata(metadata)
        checked = check_directory_proposal(
            loaded, proposal, source_reader=lambda: _read_fd(fd)
        )
        return _result(
            checked.status, loaded, checked, backup=None, notes=(), warnings=(),
            phase="before-cell", inode_flags=metadata.inode_flags,
            project_id=None if metadata.project is None else metadata.project[0],
        )
    except DirectoryPublicationError:
        raise
    except DirectoryAuthoringError as exc:
        raise _map_authoring(exc) from None
    except DirectoryApplyError as exc:
        raise _map_apply(exc) from None
    except (OSError, ValueError):
        raise DirectoryPublicationError("metadata-unsupported", phase="before-cell") from None
    except Exception:
        raise DirectoryPublicationError("INTERNAL_ERROR", phase="before-cell") from None
    finally:
        _close_quietly(fd)


# --- helpers: resolution, lock, mount proof -----------------------------------


def _platform_supported() -> bool:
    return sys.platform.startswith("linux") and hasattr(os, "O_DIRECTORY") and hasattr(fcntl, "flock")


def _writer_lock_supported() -> bool:
    """Whether the S4-2a writer lock exists on this host.

    That is POSIX ``fcntl.flock`` on an open directory descriptor. It is wider
    than the S4-1 publication platform gate above (Linux-only) because the
    built-in writers only take the folder lock, not the publication protocol.
    Hosts without it are outside the admission guarantee and keep the legacy
    unlocked writer behavior at the command boundary.
    """
    return (
        os.name == "posix"
        and hasattr(os, "O_DIRECTORY")
        and hasattr(fcntl, "flock")
    )


def _resolve_target(target: "os.PathLike[str] | str") -> tuple[Path, Path]:
    try:
        path = Path(target)
    except (TypeError, ValueError):
        raise DirectoryPublicationError("lock-failed") from None
    try:
        candidate = resolve_manifest_path(path)
    except OSError:
        raise DirectoryPublicationError("lock-failed") from None
    try:
        if candidate.is_symlink() and not candidate.exists():
            raise DirectoryPublicationError("lock-failed")
        if candidate.exists():
            resolved = candidate.resolve(strict=True)
            st = os.lstat(resolved)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise DirectoryPublicationError("invalid-path")
            return resolved, resolved.parent
        parent = candidate.parent.resolve(strict=True)
        if not parent.is_dir():
            raise DirectoryPublicationError("lock-failed")
        return parent / candidate.name, parent
    except DirectoryPublicationError:
        raise
    except (OSError, RuntimeError):
        raise DirectoryPublicationError("lock-failed") from None


def _resolve_writer_folder(
    target: "os.PathLike[str] | str", *, follow_manifest_symlink: bool = True
) -> tuple[Path, Path]:
    """Resolve the folder a built-in writer will lock and replace the manifest in.

    This is deliberately weaker than ``_resolve_target``: the manifest is not
    required to be a single-link regular file, because ``init --force``,
    ``add``, ``rehash``, ``migrate`` and ``conditions`` supported hardlinked and
    symlinked manifests before S4-2a. The containing folder must resolve to a
    real directory; any failure is ``lock-failed`` and the command boundary
    fails closed.

    With ``follow_manifest_symlink`` (the default for ``add``, ``rehash``,
    ``migrate`` and ``conditions``) the loader's directory rule is mirrored
    exactly: ``resolve_manifest_path`` appends ``runevidence.json`` at most once
    when the operand names a directory, and the resulting path is the file those
    writers load/save. Its parent is the locked folder. If that single append is
    still a directory, the loader could not read it either, so admission refuses
    with ``lock-failed`` before taking any lock: ``add EXP`` (where
    ``EXP/runevidence.json`` is a directory) keeps its legacy refusal, while
    ``add EXP/runevidence.json`` reads the file inside it and locks that folder.
    A special final target is also ``lock-failed``. The returned target is the
    fully resolved path, never the caller's alias, so a later alias retarget
    cannot move the load or save outside the locked folder.

    ``init`` passes ``follow_manifest_symlink=False`` because it writes the
    entry it names: a symlinked ``<root>/runevidence.json`` is replaced in
    ``<root>`` and its backup stays in ``<root>``, while the link target is
    never written. With this flag the target stays lexical at
    ``<root>/runevidence.json`` (no directory rule and no file-symlink
    following); a directory there fails later at backup/save without a write.
    The parent folder is still strictly resolved and locked, and the post-lock
    re-resolution still verifies that folder's identity.
    """
    try:
        path = Path(target)
    except (TypeError, ValueError):
        raise DirectoryPublicationError("lock-failed") from None
    try:
        if follow_manifest_symlink:
            # Mirror the loader exactly: ``resolve_manifest_path`` appends
            # ``runevidence.json`` at most once when the operand names a
            # directory. Descending further would admit operands the loader
            # refuses and let ``add EXP`` write beside a directory the loader
            # cannot read. If that single append is still a directory, this is
            # not a loadable manifest: refuse before any lock is taken.
            candidate = resolve_manifest_path(path)
            if candidate.is_dir():
                raise DirectoryPublicationError("lock-failed")
            if candidate.exists() and not candidate.is_file():
                # A special file is not a manifest these writers can load or
                # replace.
                raise DirectoryPublicationError("lock-failed")
            if candidate.is_symlink() or candidate.is_file():
                # Follow an existing manifest symlink exactly where load/save go.
                resolved = candidate.resolve(strict=False)
                parent = resolved.parent
            else:
                parent = candidate.parent
                resolved = None
        else:
            # The loader's directory rule is deliberately not applied here: a
            # directory or directory symlink at <root>/runevidence.json must
            # keep failing at backup/save instead of locking and overwriting a
            # manifest inside it (legacy behavior). The target stays lexical.
            candidate = path
            parent = candidate.parent
            resolved = None
        parent = parent.resolve(strict=True)
        if not parent.is_dir():
            raise DirectoryPublicationError("lock-failed")
        if resolved is None:
            resolved = parent / candidate.name
    except DirectoryPublicationError:
        raise
    except (OSError, RuntimeError):
        raise DirectoryPublicationError("lock-failed") from None
    return resolved, parent


def _locked_resolution_matches(
    locked: LockedManifestFolder, target_path: Path, parent: Path
) -> bool:
    """Whether a fresh resolution still names the locked folder and manifest."""
    if target_path != locked.target or parent != locked.parent:
        return False
    try:
        st = os.stat(parent)
    except OSError:
        return False
    return (st.st_dev, st.st_ino) == locked.identity


def _mount_table():
    return directory_scanner._mountinfo()


def _proven_mount_id(fd: int) -> int:
    return directory_scanner._prove_mount_id(fd)


def _release_policy():
    return directory_scanner._SCAN_POLICY.get()


def _classify_manifest_folder(locked: LockedManifestFolder) -> None:
    """Classify the folder with the scanner's mount-info/mount-id proof."""
    try:
        policy = _release_policy()
        if not policy.fstypes:
            raise DirectoryPublicationError("unsupported-filesystem")
        parts = directory_scanner._root_components(str(locked.parent))
        table = _mount_table()
        _, chosen = directory_scanner._preflight_mounts(parts, policy, table)
        proven = _proven_mount_id(locked.fd)
        directory_scanner._validate_root_mount(proven, chosen, table, policy)
    except DirectoryPublicationError:
        raise
    except directory_scanner.DirectoryScanError as exc:
        if exc.reason == "unsupported-platform":
            raise DirectoryPublicationError("unsupported-platform") from None
        raise DirectoryPublicationError("unsupported-filesystem") from None
    except Exception:
        raise DirectoryPublicationError("unsupported-filesystem") from None


# --- helpers: metadata --------------------------------------------------------


def _capture_metadata(fd: int) -> MetadataSnapshot:
    try:
        st = os.fstat(fd)
        names = sorted(os.listxattr(fd))
    except OSError:
        raise DirectoryPublicationError("metadata-unsupported") from None
    values: dict[str, bytes] = {}
    for name in names:
        if not _xattr_namespace_supported(name):
            raise DirectoryPublicationError("metadata-unsupported")
        try:
            values[name] = os.getxattr(fd, name)
        except OSError as exc:
            if exc.errno in (errno.ENODATA, _ENOATTR):
                continue
            raise DirectoryPublicationError("metadata-unsupported") from None
    flags = _read_inode_flags(fd)
    _check_flags(flags)
    project = _read_project(fd)
    notes = () if project is not None else ("project-id: ioctl unavailable; G6 narrowed",)
    return MetadataSnapshot(
        mode=stat.S_IMODE(st.st_mode),
        uid=st.st_uid,
        gid=st.st_gid,
        xattrs=tuple(sorted(values.items())),
        inode_flags=flags,
        project=project,
        notes=notes,
    )


def _check_flags(flags: int) -> None:
    if flags & _INODE_IMMUTABLE:
        raise DirectoryPublicationError("metadata-unsupported")
    if flags & ~_INODE_KNOWN_MASK:
        raise DirectoryPublicationError("metadata-unsupported")


def _read_inode_flags(fd: int) -> int:
    try:
        raw = fcntl.ioctl(fd, _FS_IOC_GETFLAGS, b"\0" * 8)
    except OSError:
        raise DirectoryPublicationError("metadata-unsupported") from None
    return struct.unpack("<I", raw[:4])[0]


def _write_inode_flags(fd: int, flags: int) -> None:
    try:
        fcntl.ioctl(fd, _FS_IOC_SETFLAGS, struct.pack("<I", flags) + b"\0\0\0\0")
    except OSError:
        raise DirectoryPublicationError("metadata-unsupported") from None


def _read_project(fd: int) -> Optional[tuple[int, bool]]:
    value = _FsXattr()
    try:
        fcntl.ioctl(fd, _FS_IOC_FSGETXATTR, value)  # type: ignore[arg-type]
    except OSError:
        return None
    return int(value.fsx_projid), bool(int(value.fsx_xflags) & _FS_XFLAG_PROJINHERIT)


def _write_project(fd: int, project_id: int, inherit: bool) -> bool:
    current = _FsXattr()
    try:
        fcntl.ioctl(fd, _FS_IOC_FSGETXATTR, current)  # type: ignore[arg-type]
        xflags = int(current.fsx_xflags)
        xflags = (xflags | _FS_XFLAG_PROJINHERIT) if inherit else (xflags & ~_FS_XFLAG_PROJINHERIT)
        desired = _FsXattr()
        desired.fsx_xflags = xflags
        desired.fsx_extsize = int(current.fsx_extsize)
        desired.fsx_nextents = int(current.fsx_nextents)
        desired.fsx_projid = project_id
        desired.fsx_cowextsize = int(current.fsx_cowextsize)
        fcntl.ioctl(fd, _FS_IOC_FSSETXATTR, desired)  # type: ignore[arg-type]
    except OSError:
        return False
    return True


def _xattr_namespace_supported(name: str) -> bool:
    if name in (_ACL_ACCESS, _ACL_DEFAULT):
        return True
    return name.startswith((_USER_PREFIX, _SECURITY_PREFIX, _TRUSTED_PREFIX))


def _acl_is_owner_only(data: bytes) -> bool:
    if len(data) < 4 or struct.unpack("<I", data[:4])[0] != 2 or (len(data) - 4) % 8:
        return False
    checked = False
    for offset in range(4, len(data), 8):
        tag, perm, _ident = struct.unpack("<HHI", data[offset:offset + 8])
        checked = True
        if tag in (_ACL_USER, _ACL_GROUP):
            return False
        if tag == _ACL_GROUP_OBJ and perm & 0o7:
            return False
        if tag == _ACL_OTHER and perm & 0o7:
            return False
    return checked


def _owner_only_acl(owner_perm: int) -> bytes:
    return (
        struct.pack("<I", 2)
        + struct.pack("<HHI", _ACL_USER_OBJ, owner_perm, 0xFFFFFFFF)
        + struct.pack("<HHI", _ACL_GROUP_OBJ, 0, 0xFFFFFFFF)
        + struct.pack("<HHI", _ACL_OTHER, 0, 0xFFFFFFFF)
    )


def _assert_private(fd: int, kind: Literal["cell", "file"]) -> None:
    expected = 0o700 if kind == "cell" else 0o600
    try:
        st = os.fstat(fd)
        access = _get_xattr_optional(fd, _ACL_ACCESS)
        default = _get_xattr_optional(fd, _ACL_DEFAULT) if kind == "cell" else None
    except DirectoryPublicationError:
        raise
    if stat.S_IMODE(st.st_mode) & 0o077 != 0 or stat.S_IMODE(st.st_mode) != expected:
        raise DirectoryPublicationError("metadata-unsupported")
    if access is not None and not _acl_is_owner_only(access):
        raise DirectoryPublicationError("metadata-unsupported")
    if default is not None:
        raise DirectoryPublicationError("metadata-unsupported")


def _get_xattr_optional(fd: int, name: str) -> Optional[bytes]:
    try:
        return os.getxattr(fd, name)
    except OSError as exc:
        if exc.errno in (errno.ENODATA, _ENOATTR):
            return None
        raise DirectoryPublicationError("metadata-unsupported") from None


def _remove_inherited_xattrs(fd: int, source: MetadataSnapshot) -> None:
    _remove_extra_xattrs(fd, {name for name, _ in source.xattrs}, keep_acl=False)


def _remove_cell_xattrs(fd: int) -> None:
    _remove_extra_xattrs(fd, {_ACL_ACCESS}, keep_acl=True)


def _remove_all_non_security_xattrs(fd: int) -> None:
    _remove_extra_xattrs(fd, set(), keep_acl=False)


def _remove_extra_xattrs(fd: int, wanted: set[str], *, keep_acl: bool) -> None:
    if keep_acl:
        wanted = wanted | {_ACL_ACCESS}
    try:
        names = sorted(os.listxattr(fd))
    except OSError:
        raise DirectoryPublicationError("metadata-unsupported") from None
    for name in names:
        if name in wanted or name.startswith(_SECURITY_PREFIX):
            continue
        try:
            os.removexattr(fd, name)
        except OSError as exc:
            if exc.errno in (errno.ENODATA, _ENOATTR):
                continue
            raise DirectoryPublicationError("metadata-unsupported") from None


def _apply_metadata(staged_fd: int, source: MetadataSnapshot) -> None:
    try:
        st = os.fstat(staged_fd)
    except OSError:
        raise DirectoryPublicationError("metadata-unsupported") from None
    if (st.st_uid, st.st_gid) != (source.uid, source.gid):
        try:
            os.fchown(staged_fd, source.uid, source.gid)
        except OSError:
            raise DirectoryPublicationError("metadata-unsupported") from None
    try:
        os.fchmod(staged_fd, source.mode)
    except OSError:
        raise DirectoryPublicationError("metadata-unsupported") from None
    _remove_inherited_xattrs(staged_fd, source)
    for name, value in source.xattrs:
        try:
            os.setxattr(staged_fd, name, value)
        except OSError:
            # security.* is policy-assigned and never guessed or deleted; a
            # failure to set the exact source value is a refusal.
            raise DirectoryPublicationError("metadata-unsupported") from None

    current_flags = _read_inode_flags(staged_fd)
    _check_flags(current_flags)
    desired = (current_flags & ~_INODE_USER_MASK) | (source.inode_flags & _INODE_USER_MASK)
    if desired != current_flags:
        _write_inode_flags(staged_fd, desired)

    if source.project is not None:
        current = _read_project(staged_fd)
        if current is None:
            raise DirectoryPublicationError("metadata-unsupported")
        if current != source.project:
            if not _write_project(staged_fd, *source.project):
                raise DirectoryPublicationError("metadata-unsupported")
    _fsync(staged_fd, role="staged-metadata")


def _verify_complete_metadata(staged_fd: int, source: MetadataSnapshot) -> tuple[str, ...]:
    """Compare the complete detectable attribute set both ways."""
    staged = _capture_metadata(staged_fd)
    if (staged.mode, staged.uid, staged.gid) != (source.mode, source.uid, source.gid):
        raise DirectoryPublicationError("metadata-unsupported")
    source_xattrs = dict(source.xattrs)
    staged_xattrs = dict(staged.xattrs)
    for name, value in source_xattrs.items():
        if staged_xattrs.get(name) != value:
            raise DirectoryPublicationError("metadata-unsupported")
    for name, value in staged_xattrs.items():
        if name in source_xattrs:
            if value != source_xattrs[name]:
                raise DirectoryPublicationError("metadata-unsupported")
            continue
        if name.startswith(_SECURITY_PREFIX):
            # Never delete a policy label; without an exact source value there
            # is no safe expected label to verify, so fail closed.
            raise DirectoryPublicationError("metadata-unsupported")
        raise DirectoryPublicationError("metadata-unsupported")
    if (staged.inode_flags & _INODE_USER_MASK) != (source.inode_flags & _INODE_USER_MASK):
        raise DirectoryPublicationError("metadata-unsupported")
    notes = list(source.notes)
    if source.inode_flags & _INODE_MANAGED_MASK:
        notes.append("inode-flags: filesystem-managed bits are G6-narrowed, not a portability claim")
    if source.project is None:
        if "project-id: ioctl unavailable; G6 narrowed" not in notes:
            notes.append("project-id: ioctl unavailable; G6 narrowed")
    elif staged.project != source.project:
        raise DirectoryPublicationError("metadata-unsupported")
    return tuple(notes)


def _assert_access_matches_source(fd: int, source: MetadataSnapshot) -> None:
    """After the metadata copy, the staged access set must be exactly the
    source's: no inherited entry may add a second user beyond it."""
    try:
        st = os.fstat(fd)
    except OSError:
        raise DirectoryPublicationError("metadata-unsupported") from None
    if stat.S_IMODE(st.st_mode) != source.mode:
        raise DirectoryPublicationError("metadata-unsupported")
    if _get_xattr_optional(fd, _ACL_ACCESS) != source.acl_access:
        raise DirectoryPublicationError("metadata-unsupported")


def _preflight_metadata(metadata: MetadataSnapshot) -> None:
    """Refuse detectable, unreproducible metadata before any scan."""
    if metadata.inode_flags & _INODE_IMMUTABLE:
        raise DirectoryPublicationError("metadata-unsupported")
    if metadata.inode_flags & ~_INODE_KNOWN_MASK:
        raise DirectoryPublicationError("metadata-unsupported")
    for name, _ in metadata.xattrs:
        if not _xattr_namespace_supported(name):
            raise DirectoryPublicationError("metadata-unsupported")
    euid = os.geteuid()
    if euid != 0:
        if metadata.uid != euid:
            raise DirectoryPublicationError("metadata-unsupported")
        groups = set(os.getgroups()) | {os.getegid()}
        if metadata.gid != os.getegid() and metadata.gid not in groups:
            raise DirectoryPublicationError("metadata-unsupported")


def _preflight_project(locked: LockedManifestFolder, metadata: MetadataSnapshot) -> None:
    """Refuse a project-ID mismatch that staging cannot reconcile.

    A new file inherits the folder's project ID only when the folder has
    PROJINHERIT; otherwise it starts at 0. If the source needs a different ID
    and this user cannot set it, refuse before scanning rather than after the
    link/rename would fail EXDEV."""
    if metadata.project is None:
        return
    folder = _read_project(locked.fd)
    if folder is None:
        return
    predicted = folder[0] if folder[1] else 0
    if metadata.project[0] != predicted and metadata.project[0] != 0 and os.geteuid() != 0:
        raise DirectoryPublicationError("metadata-unsupported")


def _check_manifest_access(locked: LockedManifestFolder, metadata: MetadataSnapshot) -> None:
    try:
        manifest_st = os.fstat(locked.manifest_fd)
        parent_st = os.fstat(locked.fd)
    except OSError:
        raise DirectoryPublicationError("permission-denied") from None
    euid = os.geteuid()
    if not _mode_allows(manifest_st, euid, 0o200, 0o020, 0o002):
        raise DirectoryPublicationError("permission-denied")
    if not _mode_allows(parent_st, euid, 0o300, 0o030, 0o003):
        raise DirectoryPublicationError("permission-denied")
    if euid != 0 and metadata.uid != euid:
        raise DirectoryPublicationError("metadata-unsupported")


def _mode_allows(st: os.stat_result, euid: int, owner: int, group: int, other: int) -> bool:
    if euid == 0:
        return True
    mode = stat.S_IMODE(st.st_mode)
    if st.st_uid == euid:
        return mode & owner == owner
    groups = set(os.getgroups()) | {os.getegid()}
    if st.st_gid in groups:
        return mode & group == group
    return mode & other == other


def _same_metadata(a: MetadataSnapshot, b: MetadataSnapshot) -> bool:
    return (
        (a.mode, a.uid, a.gid, a.xattrs, a.inode_flags, a.project)
        == (b.mode, b.uid, b.gid, b.xattrs, b.inode_flags, b.project)
    )


def _source_durability(locked: LockedManifestFolder) -> None:
    try:
        _fsync(locked.manifest_fd, role="source-file")
        _fsync(locked.fd, role="source-parent")
    except OSError:
        raise DirectoryPublicationError("source-durability-failed") from None


def _verify_source_state(
    locked: LockedManifestFolder, metadata: MetadataSnapshot, *, phase: Phase
) -> None:
    locked.verify_owner(phase=phase)
    try:
        path_st = os.stat(locked.name, dir_fd=locked.fd, follow_symlinks=False)
        fd_st = os.fstat(locked.manifest_fd)
        source = _read_fd(locked.manifest_fd)
    except OSError:
        raise DirectoryPublicationError("stale-source", phase=phase) from None
    if (not stat.S_ISREG(path_st.st_mode) or path_st.st_nlink != 1
            or (path_st.st_dev, path_st.st_ino) != locked.manifest_identity
            or (fd_st.st_dev, fd_st.st_ino) != locked.manifest_identity):
        raise DirectoryPublicationError("stale-source", phase=phase)
    if source != locked.loaded.source_bytes:
        raise DirectoryPublicationError("stale-source", phase=phase)
    now = _capture_metadata(locked.manifest_fd)
    if not _same_metadata(now, metadata):
        raise DirectoryPublicationError("stale-metadata", phase=phase)


# --- helpers: cell, staged file, backup --------------------------------------


def _create_cell(locked: LockedManifestFolder) -> tuple[str, int]:
    for _ in range(MAX_CELL_ATTEMPTS):
        name = f"{CELL_PREFIX}{os.getpid()}-{secrets.token_hex(8)}"
        try:
            os.mkdir(name, 0o700, dir_fd=locked.fd)
        except FileExistsError:
            continue
        except OSError:
            raise DirectoryPublicationError("write-failed", phase="cell-or-backup") from None
        # The directory now exists. Every failure below must close the fd (if
        # opened) and remove the just-created empty cell, through the held
        # folder descriptor, before propagating. A boundary hook is included:
        # an exception there must not leak the fd or leave an empty cell.
        fd = -1
        try:
            fd = os.open(
                name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=locked.fd,
            )
            st = os.fstat(fd)
            path_st = os.stat(name, dir_fd=locked.fd, follow_symlinks=False)
            if (not stat.S_ISDIR(st.st_mode)
                    or (st.st_dev, st.st_ino) != (path_st.st_dev, path_st.st_ino)
                    or not _cloexec(fd)):
                raise OSError(errno.EINVAL, "cell identity mismatch")
            _boundary_cell_before_restrict(locked, name, fd)
        except BaseException:
            _close_quietly(fd)
            _remove_cell_dir(locked, name)
            raise
        return name, fd
    raise DirectoryPublicationError("write-failed", phase="cell-or-backup")


def _remove_cell_dir(locked: LockedManifestFolder, name: str) -> bool:
    """Remove the just-created, still-empty cell by its held folder fd."""
    try:
        os.rmdir(name, dir_fd=locked.fd)
    except OSError:
        return False
    return True


def _restrict_cell(locked: LockedManifestFolder, cell_name: str, cell_fd: int) -> None:
    try:
        os.fchmod(cell_fd, 0o700)
    except OSError:
        raise DirectoryPublicationError("metadata-unsupported") from None
    try:
        os.setxattr(cell_fd, _ACL_ACCESS, _owner_only_acl(0o7))
    except OSError as exc:
        if exc.errno not in (errno.EOPNOTSUPP, errno.ENOTSUP, errno.EINVAL):
            raise DirectoryPublicationError("metadata-unsupported") from None
        if _get_xattr_optional(cell_fd, _ACL_ACCESS) is not None:
            raise DirectoryPublicationError("metadata-unsupported") from None
    try:
        os.removexattr(cell_fd, _ACL_DEFAULT)
    except OSError as exc:
        if exc.errno in (errno.ENODATA, _ENOATTR):
            pass
        elif exc.errno in (errno.EOPNOTSUPP, errno.ENOTSUP, errno.EINVAL):
            if _get_xattr_optional(cell_fd, _ACL_DEFAULT) is not None:
                raise DirectoryPublicationError("metadata-unsupported") from None
        else:
            raise DirectoryPublicationError("metadata-unsupported") from None
    _remove_cell_xattrs(cell_fd)
    _assert_private(cell_fd, "cell")
    _boundary_cell_restricted(locked, cell_name, cell_fd)


def _create_staged(cell_fd: int) -> int:
    try:
        fd = os.open(
            STAGED_NAME,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=cell_fd,
        )
    except OSError:
        raise DirectoryPublicationError("write-failed", phase="cell-or-backup") from None
    try:
        access = fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE
        if access != os.O_RDWR:
            raise DirectoryPublicationError("write-failed", phase="cell-or-backup")
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) & 0o077:
            raise DirectoryPublicationError("metadata-unsupported")
    except DirectoryPublicationError:
        _close_quietly(fd)
        raise
    except OSError:
        _close_quietly(fd)
        raise DirectoryPublicationError("write-failed", phase="cell-or-backup") from None
    return fd


def _publish_backup(
    locked: LockedManifestFolder, cell_fd: int, old_bytes: bytes, phase: Phase
) -> tuple[Path, tuple[str, ...]]:
    try:
        fd = os.open(
            BACKUP_TEMP_NAME,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=cell_fd,
        )
    except OSError:
        raise DirectoryPublicationError("write-failed", phase=phase) from None
    try:
        access = fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE
        if access != os.O_RDWR:
            raise DirectoryPublicationError("write-failed", phase=phase)
        _boundary_backup_before_restrict(cell_fd, fd)
        _remove_all_non_security_xattrs(fd)
        _assert_private(fd, "file")
        _boundary_backup_created(cell_fd, fd)
        _write_all(fd, old_bytes)
        _fsync(fd, role="backup")
        _verify_read_back(fd, old_bytes)
        _assert_private(fd, "file")
    except DirectoryPublicationError:
        raise
    except OSError:
        raise DirectoryPublicationError("write-failed", phase=phase) from None
    finally:
        _close_quietly(fd)
    _boundary_backup_written(locked, cell_fd)

    generation = _next_backup_generation(locked)
    bak_name = None
    for _ in range(MAX_BACKUP_ATTEMPTS):
        candidate = _backup_name(generation)
        try:
            _link(
                BACKUP_TEMP_NAME, candidate,
                src_dir_fd=cell_fd, dst_dir_fd=locked.fd,
            )
            bak_name = candidate
            break
        except FileExistsError:
            generation += 1
            continue
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                raise DirectoryPublicationError(
                    "write-failed", phase=phase, note="project-id-mismatch"
                ) from None
            raise DirectoryPublicationError("write-failed", phase=phase) from None
    if bak_name is None:
        raise DirectoryPublicationError("write-failed", phase=phase)

    cleanup_ok = _remove_name_quietly(cell_fd, BACKUP_TEMP_NAME)
    try:
        _fsync(locked.fd, role="backup-parent")
    except OSError:
        raise DirectoryPublicationError("backup-durability-failed", phase=phase) from None
    warnings = () if cleanup_ok else ("cleanup-warning",)
    _boundary_backup_linked(locked, bak_name)
    return locked.parent / bak_name, warnings


def _next_backup_generation(locked: LockedManifestFolder) -> int:
    try:
        names = os.listdir(locked.fd)
    except OSError:
        raise DirectoryPublicationError("write-failed", phase="cell-or-backup") from None
    newest = 0
    for name in names:
        generation = backup_generation(name)
        if generation is not None:
            newest = max(newest, generation)
    return newest + 1


def _backup_name(generation: int) -> str:
    return MANIFEST_FILENAME + ".bak" if generation == 1 else f"{MANIFEST_FILENAME}.bak.{generation}"


def _discard_cell(locked: LockedManifestFolder, cell_name: Optional[str], cell_fd: Optional[int]) -> None:
    try:
        if cell_fd is not None:
            _remove_name_quietly(cell_fd, STAGED_NAME)
            _remove_name_quietly(cell_fd, BACKUP_TEMP_NAME)
            _close_quietly(cell_fd)
        if cell_name is not None:
            try:
                os.rmdir(cell_name, dir_fd=locked.fd)
            except OSError:
                pass
    except OSError:
        pass


def _remove_name_quietly(dir_fd: int, name: str) -> bool:
    try:
        os.unlink(name, dir_fd=dir_fd)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


# --- wrappers used by tests to inject failures and record order --------------


def _fsync(fd: int, *, role: str) -> None:
    os.fsync(fd)


def _link(src: str, dst: str, *, src_dir_fd: int, dst_dir_fd: int) -> None:
    os.link(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd, follow_symlinks=False)


def _replace_manifest(src_dir_fd: int, dst_dir_fd: int, dst_name: str) -> None:
    os.replace(STAGED_NAME, dst_name, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)


# --- named boundaries (no-ops; tests replace them to crash at a step) ---------


def _boundary_before_writer_lock_open(target_path, parent) -> None:
    """After the built-in writer resolves, immediately before open/flock.

    Tests replace this to retarget an alias in the resolution/acquisition gap.
    """


def _boundary_cell_before_restrict(locked, cell_name, cell_fd) -> None:
    """After the cell directory exists, before its ACL/xattrs are restricted."""


def _boundary_cell_restricted(locked, cell_name, cell_fd) -> None:
    """After the cell is owner-only, before any file is created in it."""


def _boundary_cell_created(locked, cell_name, cell_fd) -> None:
    """After the cell is created and restricted (pre-content boundary)."""


def _boundary_staged_created(cell_fd, staged_fd) -> None:
    """After the staged file exists 0600, before any content is written."""


def _boundary_staged_written(cell_fd, staged_fd) -> None:
    """After the staged bytes are written, fsynced and read back."""


def _boundary_staged_metadata(cell_fd, staged_fd) -> None:
    """After the staged metadata is applied and verified."""


def _boundary_backup_written(locked, cell_fd) -> None:
    """After the backup cell file is complete, before the link."""


def _boundary_backup_created(cell_fd, backup_fd) -> None:
    """After the backup file exists 0600 in the cell, before content."""


def _boundary_backup_before_restrict(cell_fd, backup_fd) -> None:
    """After the backup file exists, before inherited xattrs are stripped."""


def _boundary_backup_linked(locked, bak_name) -> None:
    """After the backup name is published and the folder fsynced."""


def _boundary_final_guard(locked, backup_path) -> None:
    """After the final guard passes, before replacement."""


def _boundary_before_replace(locked, backup_path) -> None:
    """Immediately before the manifest replacement."""


def _boundary_after_replace(locked, backup_path) -> None:
    """Immediately after the manifest replacement, before the folder fsync."""


def _boundary_after_parent_fsync(locked, backup_path) -> None:
    """After the post-replacement folder fsync succeeds."""


# --- helpers: result and error mapping ---------------------------------------


def _result(
    status: Status,
    loaded: LoadedManifest,
    checked: DirectoryApplyResult,
    *,
    backup: Optional[Path],
    notes: tuple[str, ...],
    warnings: tuple[str, ...],
    phase: Phase,
    inode_flags: Optional[int],
    project_id: Optional[int],
) -> DirectoryPublicationResult:
    return DirectoryPublicationResult(
        status=status,
        operation=checked.operation,
        artifact_id=checked.artifact_id,
        path=checked.path,
        manifest_path=loaded.path,
        backup_path=backup,
        old_sha256=checked.old_sha256,
        new_sha256=checked.new_sha256,
        old_size=checked.old_size,
        new_size=checked.new_size,
        members=checked.members,
        notes=checked.notes + tuple(notes),
        warnings=tuple(warnings),
        phase=phase,
        inode_flags=inode_flags,
        project_id=project_id,
    )


def _map_authoring(exc: DirectoryAuthoringError) -> DirectoryPublicationError:
    reason = exc.reason
    if reason == "scan-failed":
        reason = "recheck-failed"
    if reason not in _REASON_TEXT:
        reason = "recheck-failed"
    return DirectoryPublicationError(reason, phase="before-cell")


def _map_apply(exc: DirectoryApplyError) -> DirectoryPublicationError:
    reason = exc.reason
    if reason in ("loaded-mismatch",):
        return DirectoryPublicationError("INTERNAL_ERROR", phase="before-cell")
    if reason not in _REASON_TEXT:
        reason = "write-failed"
    return DirectoryPublicationError(reason, phase="before-cell")


def _message(reason: str, phase: Phase, note: Optional[str]) -> str:
    if reason == "INTERNAL_ERROR":
        return _INTERNAL_PHASE_TEXT[phase]
    if reason == "backup-durability-failed":
        return (
            "ERROR (BACKUP_DURABILITY_FAILED): the backup may remain; the manifest was not "
            "changed."
        )
    if reason == "durability-unknown":
        return (
            "WARNING (DURABILITY_UNKNOWN): the manifest was written, but its durability is not "
            "proven."
        )
    text = _REASON_TEXT.get(reason, "the directory update was refused")
    code = reason.upper().replace("-", "_")
    message = f"ERROR ({code}): {text}."
    if note == "project-id-mismatch":
        message += " Fixed note: project-id-mismatch."
    message += " " + _PHASE_SUFFIX[phase]
    return message


# --- helpers: low-level descriptor operations ---------------------------------


def _cloexec(fd: int) -> bool:
    try:
        return bool(fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC)
    except OSError:
        return False


def _close_quietly(fd: Optional[int]) -> None:
    if fd is None or fd < 0:
        return
    try:
        os.close(fd)
    except OSError:
        pass


def _close_with_note(fd: Optional[int]) -> Optional[str]:
    """Close in release; return a fixed cleanup note instead of raising."""
    if fd is None or fd < 0:
        return None
    try:
        os.close(fd)
    except OSError:
        return "lock-cleanup-failed"
    return None


def _read_fd(fd: int) -> bytes:
    chunks = []
    offset = 0
    while True:
        chunk = os.pread(fd, 1024 * 1024, offset)
        if not chunk:
            break
        chunks.append(chunk)
        offset += len(chunk)
    return b"".join(chunks)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(errno.EIO, "short write")
        view = view[written:]


def _verify_read_back(fd: int, expected: bytes) -> None:
    try:
        got = _read_fd(fd)
        size = os.fstat(fd).st_size
    except OSError:
        raise DirectoryPublicationError("write-failed", phase="cell-or-backup") from None
    if got != expected or size != len(expected):
        raise DirectoryPublicationError("write-failed", phase="cell-or-backup")
