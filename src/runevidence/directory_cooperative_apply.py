"""Saving a directory proposal while holding a cooperative writer lock.

Internal and development only: no command calls this. It wraps
``apply_directory_proposal`` so that RunGraph writers which all call this
function for the same manifest take turns instead of racing. That function
stays the only checker of the proposal and the only writer of the manifest;
this module adds an admission step around it and nothing else.

1. ``dry_run=True``, or a ``dry_run`` that is not a bool, goes straight to
   ``apply_directory_proposal``. No lock is looked at, made or removed.
2. Otherwise ``loaded.path`` must be the manifest exactly as the file system
   spells it now: absolute, already resolved (no ``..``, link, junction,
   other case or short name), a regular file with one name, in a plain
   folder. The lock is its sibling ``<manifest name>.cooperative-writer.lock``,
   so every cooperating writer derives the same lock for the same manifest.
3. If anything is at the lock path, the call stops before any scan with
   ``busy`` (a regular file) or ``invalid-lock-target`` (anything else). That
   lock is never read, changed, removed or waited for: no retry, sleep or
   stale-lock age rule. A writer that crashed leaves its lock behind, and a
   person has to find out why before removing it.
4. ``apply_directory_proposal(..., dry_run=True)`` checks the proposal in
   full, with its own fresh scan. ``unchanged`` is returned as it is, without
   a lock: that comes from its independent rebuild, never from the proposal's
   own ``changed`` field. Its errors propagate unmodified.
5. The lock is made by one exclusive create (``O_CREAT | O_EXCL``), which fails
   if the name exists, so two cooperating writers cannot both hold it. It
   holds a random owner token, and the file's identity is recorded.
6. Holding the lock, the manifest must still hold ``loaded.source_bytes``
   (``stale-source``) and the lock must still be this call's. Then
   ``apply_directory_proposal(..., dry_run=False)`` checks everything again
   and saves, still with its own source check and ``save_raw`` guard.
7. The lock is removed only if it is still the same file with the same
   token. Otherwise it is left where it is and the call fails, even when the
   manifest was saved; the error then carries the saved ``result``.

So a changed proposal is scanned twice: once to decide, without a lock,
whether anything would be written, and once more under the lock.

What this does not do. The lock binds only callers of this function. An
editor, a script, ``runevidence add`` or any other writer ignores it, and can
still change the manifest between ``save_raw``'s byte comparison and its
``os.replace``: this is not an atomic compare-and-swap. It does not keep the
scanned folder still, and does not preserve a custom Windows ACL on the
manifest. The token and identity checks catch ordinary mistakes between
cooperating writers; they are not security against a deliberate one. On
Windows the open lock handle makes ordinary deletion or replacement of the
lock fail while it is held, but its contents can still be overwritten, and
none of this is relied on as protection.
"""

from __future__ import annotations

import os
import stat
import uuid
from pathlib import Path, PurePosixPath
from typing import Optional

from .directory_apply import DirectoryApplyResult, apply_directory_proposal
from .edit import EditError
from .manifest import LoadedManifest

LOCK_SUFFIX = ".cooperative-writer.lock"
_REPARSE_POINT = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT; st_file_attributes exists on Windows only


class CooperativeApplyError(EditError):
    """The cooperative save did not complete as asked. ``reason`` is one of:

    * ``invalid-request``      ``loaded`` is not a LoadedManifest
    * ``invalid-lock-target``  ``loaded.path`` is not the plain, resolved file
                               it names; something other than a regular file
                               is at the lock path; or the lock would be
                               inside the folder being scanned
    * ``busy``                 a lock file exists: another cooperating writer
                               holds it, or one stopped without removing it
    * ``stale-source``         the manifest is missing, or no longer holds
                               ``loaded.source_bytes`` once the lock is held
    * ``lock-failed``          the lock could not be made; nothing this call
                               made is left
    * ``lock-ownership-lost``  the lock is no longer the file and token this
                               call made; it was left in place
    * ``lock-cleanup-failed``  the lock could not be closed or removed; it was
                               left in place
    * ``unexpected-outcome``   apply returned a status this call cannot use

    ``result`` is the DirectoryApplyResult when the manifest was saved before
    a lock failure, else None. ``apply_error`` is the exception the guarded
    apply raised, if a lock failure is reported instead. ``lock_path`` is
    the lock; ``lock_left`` says whether this call may have left a file there
    that needs a person to look at it. Errors from the checks of
    ``apply_directory_proposal`` are raised as their own DirectoryApplyError."""

    def __init__(self, reason: str, message: str, *, lock_path: Optional[Path] = None, lock_left: bool = False):
        self.reason = reason
        self.lock_path = lock_path
        self.lock_left = lock_left
        self.result: Optional[DirectoryApplyResult] = None
        self.apply_error: Optional[BaseException] = None
        super().__init__(message)


def lock_path_for(manifest_path: Path) -> Path:
    return manifest_path.with_name(manifest_path.name + LOCK_SUFFIX)


def apply_directory_proposal_cooperatively(loaded: LoadedManifest, proposal, *, dry_run: bool) -> DirectoryApplyResult:
    """``apply_directory_proposal``, holding the cooperative lock of
    ``loaded.path`` around any save. ``proposal`` is the DirectoryProposal
    made from ``loaded``; it is only passed on, never read here.

    Raises CooperativeApplyError, or DirectoryApplyError from the checks."""
    if type(dry_run) is not bool or dry_run:
        return apply_directory_proposal(loaded, proposal, dry_run=dry_run)
    if type(loaded) is not LoadedManifest:
        raise CooperativeApplyError("invalid-request", "Need a LoadedManifest and the DirectoryProposal made from it.")
    lock = _lock_path(loaded)
    _refuse_existing(lock)

    check = apply_directory_proposal(loaded, proposal, dry_run=True)
    if check.status == "unchanged":
        return check
    if check.status != "dry-run" or check.manifest_path != loaded.path:
        raise CooperativeApplyError("unexpected-outcome", f"The check returned {check.status!r}; nothing was written.")
    scanned = loaded.root.joinpath(*PurePosixPath(check.path).parts)
    if lock.is_relative_to(scanned):
        raise CooperativeApplyError(
            "invalid-lock-target", f"The lock {lock} would be inside the folder being scanned; nothing was written.",
            lock_path=lock,
        )

    result, why = None, None
    held = _Lock.create(lock)
    try:
        if _read(loaded.path) != loaded.source_bytes:
            why = "stale-source"
        elif not held.owned():
            why = "lock-ownership-lost"
        else:
            result = apply_directory_proposal(loaded, proposal, dry_run=False)
    except BaseException as exc:
        problem = held.release()
        if problem is None:
            raise
        problem.apply_error = exc
        raise problem from exc
    problem = held.release()
    if problem is not None:
        problem.result = result
        raise problem
    if why == "stale-source":
        raise CooperativeApplyError(
            "stale-source", f"{loaded.path} changed since it was loaded. Nothing was written; load it again and "
            "make a new proposal.", lock_path=lock,
        )
    if why is not None:
        raise CooperativeApplyError(
            "lock-ownership-lost", f"The lock {lock} changed before saving; nothing was written.", lock_path=lock,
        )
    if result.status not in ("written", "unchanged"):
        raise CooperativeApplyError("unexpected-outcome", f"The save returned {result.status!r}.", lock_path=lock)
    return result


# --- helpers ---------------------------------------------------------------


def _lock_path(loaded: LoadedManifest) -> Path:
    """The lock of ``loaded.path``, after checking that path names the plain
    manifest file directly. Reads only metadata; makes nothing."""
    path = loaded.path
    if not isinstance(path, Path) or not path.is_absolute():
        raise CooperativeApplyError("invalid-lock-target", f"The manifest path {path!r} is not absolute.")
    try:
        resolved = path.resolve(strict=True)
        st, folder = os.lstat(path), os.lstat(path.parent)
    except FileNotFoundError:
        raise CooperativeApplyError("stale-source", f"{path} is missing. Nothing was written.") from None
    except (OSError, RuntimeError) as exc:
        raise CooperativeApplyError("invalid-lock-target", f"Cannot check the manifest path {path}: {exc}") from exc
    if str(resolved) != str(path):
        raise CooperativeApplyError(
            "invalid-lock-target", f"The manifest path {path} is spelled differently from the file {resolved}; "
            "load the manifest again by its own path.",
        )
    if not _plain(st, stat.S_ISREG) or st.st_nlink != 1 or not _plain(folder, stat.S_ISDIR):
        raise CooperativeApplyError(
            "invalid-lock-target", f"{path} is not a plain file with one name in a plain folder; no lock was made.",
        )
    return lock_path_for(path)


def _refuse_existing(lock: Path) -> None:
    """Fail if anything is at ``lock``; never read, change or remove it."""
    try:
        st = os.lstat(lock)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise CooperativeApplyError("invalid-lock-target", f"Cannot check the lock {lock}: {exc}", lock_path=lock) from exc
    if _plain(st, stat.S_ISREG):
        raise CooperativeApplyError(
            "busy", f"{lock} exists: another writer is saving, or one stopped without removing it. Nothing was "
            "written. Do not delete it until you know which writer made it and that it has stopped.", lock_path=lock,
        )
    raise CooperativeApplyError(
        "invalid-lock-target", f"{lock} exists and is not a regular file; it was left alone and nothing was written.",
        lock_path=lock,
    )


def _plain(st: os.stat_result, is_kind) -> bool:
    return is_kind(st.st_mode) and not getattr(st, "st_file_attributes", 0) & _REPARSE_POINT


def _read(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except OSError:
        return None


class _Lock:
    """A lock file this call created, kept open until ``release``."""

    def __init__(self, path: Path, fd: int, content: bytes):
        self.path, self.fd, self.content = path, fd, content
        self.identity: Optional[tuple[int, int]] = None

    @classmethod
    def create(cls, path: Path) -> "_Lock":
        content = f"runevidence cooperative writer lock v1\ntoken {uuid.uuid4().hex}\npid {os.getpid()}\n".encode("ascii")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o644)
        except OSError as exc:
            _refuse_existing(path)  # busy or invalid-lock-target if something is there now
            raise CooperativeApplyError("lock-failed", f"Could not create the lock {path}: {exc}", lock_path=path) from exc
        held = cls(path, fd, content)
        try:
            st = os.fstat(fd)
            held.identity = (st.st_dev, st.st_ino)
            if os.write(fd, content) != len(content):
                raise OSError("short write")
            os.fsync(fd)
        except OSError as exc:
            problem = held.release(check_content=False)
            if problem is not None:
                problem.apply_error = exc
                raise problem from exc
            raise CooperativeApplyError(
                "lock-failed", f"Could not write the lock {path}: {exc}; it was removed.", lock_path=path,
            ) from exc
        return held

    def owned(self, *, check_content: bool = True) -> bool:
        """Still the file this call created, holding its token."""
        try:
            st = os.lstat(self.path)
            if not _plain(st, stat.S_ISREG) or st.st_nlink != 1 or (st.st_dev, st.st_ino) != self.identity:
                return False
            if not check_content:
                return True
            with open(self.path, "rb") as fh:
                return fh.read(len(self.content) + 1) == self.content
        except OSError:
            return False

    def release(self, *, check_content: bool = True) -> Optional[CooperativeApplyError]:
        """Close and remove the lock if it is still this call's. Returns the
        problem instead of raising, so no earlier error is lost."""
        try:
            os.close(self.fd)
        except OSError as exc:
            return CooperativeApplyError(
                "lock-cleanup-failed", f"Could not close the lock {self.path}: {exc}; it was left in place.",
                lock_path=self.path, lock_left=True,
            )
        if self.identity is None or not self.owned(check_content=check_content):
            return CooperativeApplyError(
                "lock-ownership-lost", f"{self.path} is no longer the lock this writer made, so it was not removed. "
                "Find out which writer changed it before removing it.", lock_path=self.path, lock_left=True,
            )
        try:
            os.unlink(self.path)
        except OSError as exc:
            return CooperativeApplyError(
                "lock-cleanup-failed", f"Could not remove the lock {self.path}: {exc}; it was left in place.",
                lock_path=self.path, lock_left=True,
            )
        return None
