"""Local-file checks: does every artifact exist where the manifest says, with
the bytes the manifest recorded?

Paths are always resolved relative to the manifest's folder, never the
current working directory, so results do not depend on where you run from.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Optional

from ..findings import Code, Finding, Severity
from ..hashing import sha256_file
from ..locator_profiles import MAX_FILE_BYTES
from ..manifest import LoadedManifest
from ..models import Artifact, is_own_file

# Folders never treated as experiment content when scanning for hints or
# untracked files.
IGNORED_DIRS = {".git", ".hg", ".svn", "__pycache__", ".venv", "venv", ".ipynb_checkpoints"}

# Declared bounds for recursive scans. A partial list is never returned: the
# caller gets ScanLimitError and refuses before writing anything.
MAX_SCAN_DEPTH = 64
MAX_SCAN_DIRECTORIES = 10_000
MAX_SCAN_FILES = 1_000_000


class ScanLimitError(OSError):
    """A recursive file scan exceeded one of its declared caps."""


@dataclass
class FileCheckResult:
    findings: list[Finding] = field(default_factory=list)
    # artifact id -> resolved path, only for files that exist and match
    ok_paths: dict[str, Path] = field(default_factory=dict)
    # artifact id -> resolved path for every file that exists and was read,
    # including changed ones (so result values can still be compared)
    readable_paths: dict[str, Path] = field(default_factory=dict)
    # artifact ids whose file is missing, unreadable or changed
    broken: set[str] = field(default_factory=set)


def _real(path: Path) -> Optional[str]:
    """The physical path with links resolved, or None when it cannot be known.

    Returning None lets callers fail closed instead of trusting the lexical
    spelling of a path whose real location is unknown."""
    try:
        return os.path.normcase(str(path.resolve(strict=False)))
    except (OSError, ValueError, RuntimeError):
        return None


def _within(root: Path, path: Path) -> bool:
    """True when ``path`` resolves physically inside ``root``.

    Symbolic links and Windows junctions are resolved, so a relative path
    cannot reach outside the experiment folder. A link back inside stays
    usable. If either path cannot be resolved, containment is unknown and the
    answer is False."""
    root_s = _real(root)
    path_s = _real(path)
    if root_s is None or path_s is None:
        return False
    root_s = root_s.rstrip(os.sep)
    return path_s == root_s or path_s.startswith(root_s + os.sep)


def _realpath_lives_in(root: Path, target: Path) -> bool:
    """Where a path physically lives, even when resolving it loops.

    Diagnosis only: this decides whether a loop or a denied parent keeps the
    containment finding or may be handed to the protected reader. It never
    authorises an open; the descriptor walk remains the only boundary.
    ``Path.resolve`` gives up on a symlink loop, but ``os.path.realpath``
    still reports the entry's physical location, so an in-root self-loop stays
    distinguishable from a link that leaves the folder."""
    root_s = _real(root)
    if root_s is None:
        return False
    try:
        target_s = os.path.normcase(os.path.realpath(target))
    except (OSError, ValueError, RuntimeError):
        return False
    root_s = root_s.rstrip(os.sep)
    return target_s == root_s or target_s.startswith(root_s + os.sep)


def iter_files(root: Path, folder: Optional[Path] = None):
    """Yield every regular file physically under root as a POSIX relative path.

    A directory link (symbolic link or Windows junction) is followed only when
    it resolves inside root; anything outside is pruned before traversal, so a
    scan never reads another folder's bytes. Resolved directories on the
    current branch are remembered to stop link cycles deterministically. A
    scan is bounded by ``MAX_SCAN_DEPTH``, ``MAX_SCAN_DIRECTORIES`` and
    ``MAX_SCAN_FILES``; exceeding one raises ``ScanLimitError`` instead of
    silently returning a partial list."""
    base = folder if folder is not None else root
    base_real = _real(base)
    if base_real is None:
        return
    state = {"directories": 0, "files": 0}

    def walk(current: Path, ancestors: set[str], depth: int):
        if depth > MAX_SCAN_DEPTH:
            raise ScanLimitError(f"folder depth exceeds {MAX_SCAN_DEPTH}")
        state["directories"] += 1
        if state["directories"] > MAX_SCAN_DIRECTORIES:
            raise ScanLimitError(
                f"more than {MAX_SCAN_DIRECTORIES} folders were scanned"
            )
        try:
            entries = sorted(os.scandir(current), key=lambda entry: entry.name)
        except OSError:
            return
        dirs: list[tuple[Path, str]] = []
        files: list[Path] = []
        for entry in entries:
            full = Path(entry.path)
            try:
                is_dir = entry.is_dir(follow_symlinks=True)
            except OSError:
                is_dir = False
            if is_dir:
                if entry.name in IGNORED_DIRS or not _within(root, full):
                    continue
                real = _real(full)
                if real is None or real in ancestors:
                    continue
                dirs.append((full, real))
                continue
            try:
                if not entry.is_file(follow_symlinks=True):
                    continue
            except OSError:
                continue
            if _within(root, full):
                files.append(full)
        for full in files:
            state["files"] += 1
            if state["files"] > MAX_SCAN_FILES:
                raise ScanLimitError(f"more than {MAX_SCAN_FILES} files were scanned")
            yield full.relative_to(root).as_posix()
        for full, real in dirs:
            yield from walk(full, ancestors | {real}, depth + 1)

    yield from walk(base, {base_real}, 0)


# --- protected path reading -------------------------------------------------
#
# The safety boundary for a 0.3-targeted artifact is the descriptor walk below,
# never a resolve()/path-prefix check: each parent component is opened relative
# to its verified parent with O_NOFOLLOW|O_DIRECTORY, so a parent swapped for a
# symlink after containment resolution makes the next open fail instead of
# reading an outside file.

_DIR_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_BINARY", 0)
)
_LEAF_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_NOCTTY", 0)
    # Without O_BINARY Windows reads a CRLF file through the text-mode CRT and
    # the hashed bytes no longer match the recorded LF/CRLF bytes on disk.
    | getattr(os, "O_BINARY", 0)
)
_MAX_SYMLINK_DEPTH = 40
_READ_CHUNK = 1024 * 1024

# Raised snapshot cap for artifacts targeted by a kind-eligible TensorBoard
# locator (RFC sections 5 and 6). The delivered 50 MiB cap lives in
# ``locator_profiles.MAX_FILE_BYTES`` and stays the default for every other
# artifact, including 0.5 string/jsonl/csv-cell targets.
TENSORBOARD_MAX_FILE_BYTES = 128 * 1024 * 1024


def _dir_fd_supported() -> bool:
    try:
        return (
            os.open in os.supports_dir_fd
            and os.stat in os.supports_dir_fd
            and os.readlink in os.supports_dir_fd
        )
    except AttributeError:  # pragma: no cover - every supported Python has it
        return False


_DIR_FD = _dir_fd_supported()


def _normalize(raw: str) -> str:
    """Manifest artifact spellings only.

    A manifest written on Windows may use backslashes as separators, so those
    are rewritten for the artifact path (and a ``non-posix-path`` warning is
    reported). A symbolic-link target is never normalised: on POSIX a
    backslash is a literal filename byte, and rewriting it would open a
    different file than the kernel does."""
    return raw.replace("\\", "/") if "\\" in raw else raw


def _spelled_components(norm: str) -> Optional[list[str]]:
    """Split a normalized spelling without normalizing it.

    Only the leading separator of a host-absolute spelling is dropped; every
    other empty component, ``.``, ``..`` and NUL refuses the whole spelling.
    Running this before ``os.path.relpath`` is what stops a ``..`` from being
    collapsed on paper: the kernel opens a different file when a component is
    a link. A ``.`` component is refused too, because a trailing ``/.`` after
    a regular file is ``ENOTDIR`` for the kernel while a walk that dropped the
    ``.`` would open and verify the file."""
    if "\x00" in norm:
        return None
    parts = norm.split("/")
    if _is_absolute(norm) and parts and parts[0] == "":
        parts = parts[1:]
    if not parts:
        return None
    for part in parts:
        if part in ("", ".", "..") or part.startswith("/"):
            return None
    return parts


def _absolute_parts(root: Path, norm: str) -> tuple[Optional[tuple[str, ...]], str]:
    """Components of a host-absolute spelling, classified when refused.

    ``relpath`` is only ever given a spelling the running host calls absolute.
    A Windows-shaped name (``C:/x.jsonl``, ``\\\\srv\\share\\x``) is relative on
    POSIX, and ``relpath`` would silently resolve it against the process cwd,
    opening a different file than the caller named. Such a spelling is refused
    here; callers that accept relative names must send it through their own
    root-relative walk instead.

    The spelling is shape-checked component-wise first, so ``..``, ``.`` and
    empty components are refused before any prefix arithmetic. Only then is the
    root prefix removed with ``relpath``, which cannot collapse anything because
    none survived. Returns ``(parts, state)`` where state is ``ok``,
    ``refused-path`` (bad shape) or ``escapes-root`` (outside the folder)."""
    if not _is_absolute(norm):
        return None, "refused-path"
    if _spelled_components(norm) is None:
        return None, "refused-path"
    try:
        rel = os.path.relpath(norm, os.fspath(root)).replace(os.sep, "/")
    except (OSError, ValueError):
        return None, "refused-path"
    parts = tuple(part for part in rel.split("/") if part != ".")
    if not parts:
        return None, "refused-path"
    for part in parts:
        if part in ("", "..") or part.startswith("/") or "\x00" in part:
            return None, "escapes-root"
    return parts, "ok"


def _parts_for(root: Path, raw: str) -> Optional[tuple[str, ...]]:
    """Relative components for an accepted artifact path, or None.

    ``..``, ``.``, absolute components, empty components and NUL are refused,
    and for a host-absolute spelling they are refused in the raw spelling
    before any ``relpath``, so ``..`` is never collapsed on paper. A ``.``
    component is refused rather than dropped: the kernel treats a trailing
    ``/.`` after a regular file as ``ENOTDIR``, so dropping it would let the
    walk verify a file the spelled path cannot open. This is only a shape
    check; containment is enforced by the descriptor walk."""
    norm = _normalize(raw)
    if _is_absolute(norm):
        return _absolute_parts(root, norm)[0]
    parts = tuple(norm.split("/"))
    if not parts:
        return None
    for part in parts:
        if part in ("", ".", "..") or part.startswith("/") or "\x00" in part:
            return None
    return parts


def _targeted_parts(root: Path, raw: str) -> tuple[Optional[tuple[str, ...]], str]:
    """Components for a 0.3-targeted artifact, and why they were refused.

    ``_parts_for`` reports a refusal as ``None`` and loses the distinction
    between a bad shape and a host-absolute spelling whose root prefix cannot
    be removed. The targeted reader needs that distinction so it never blames
    an empty or ``.`` component on a plain spelling that has neither."""
    norm = _normalize(raw)
    if _is_absolute(norm):
        return _absolute_parts(root, norm)
    parts = _parts_for(root, norm)
    return parts, "ok" if parts is not None else "refused-path"


def _shape_problem(raw: str) -> Optional[str]:
    """The fixed shape category of a refused spelling: dotdot, empty or None.

    Diagnosis only, for choosing between fixed messages; it never decides
    whether a path is accepted (``_parts_for`` does that)."""
    norm = _normalize(raw)
    parts = norm.split("/")
    if _is_absolute(norm) and parts and parts[0] == "":
        parts = parts[1:]
    if ".." in parts:
        return "dotdot"
    if any(part in ("", ".") for part in parts) or all(part == "." for part in parts):
        return "empty"
    return None


def _errno_state(exc: OSError) -> str:
    if exc.errno in (errno.ENOENT, errno.ENOTDIR):
        return "missing"
    if exc.errno == errno.ELOOP:
        return "changed"
    return "unreadable"


def _component_state(exc: OSError) -> str:
    """A parent component that is not the directory it was is a swap."""
    if exc.errno == errno.ENOENT:
        return "missing"
    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
        return "changed"
    return "unreadable"


def _parent_component_state(part: str, parent_fd: int, exc: OSError) -> str:
    """Map a failed parent-component open, naming a symbolic link explicitly.

    ``O_NOFOLLOW|O_DIRECTORY`` reports a symlink as ``ENOTDIR`` on Linux, so a
    plain ``lstat`` of the component (relative to the verified parent) is the
    only way to tell a link from a real non-directory. That read is a name
    lookup on an already-open parent descriptor, never an open of the target."""
    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
        try:
            probe = os.stat(part, dir_fd=parent_fd, follow_symlinks=False)
        except OSError:
            probe = None
        if exc.errno == errno.ELOOP or (
            probe is not None and stat.S_ISLNK(probe.st_mode)
        ):
            return "symlinked-parent"
    return _component_state(exc)


def _stat_path(path: Path) -> tuple[str, Optional[os.stat_result]]:
    """One stat, one errno map. ``exists()``/``is_file()`` are not used: on
    Python before 3.14 they raise on an unsearchable parent, and on 3.14 they
    report a present file as missing. ``os.stat`` raises ``ValueError`` (not
    ``OSError``) for an embedded NUL; that is the legacy "not there" outcome,
    never a traceback."""
    try:
        return "ok", os.stat(path)
    except OSError as exc:
        return _errno_state(exc), None
    except (ValueError, UnicodeError):
        return "missing", None


@dataclass
class _Protected:
    state: str
    fd: Optional[int] = None
    parent_fd: Optional[int] = None
    leaf: Optional[str] = None
    before: Optional[os.stat_result] = None


def _resolve_alias(
    root: Path, parents: tuple[str, ...], link: str
) -> tuple[Optional[tuple[str, ...]], str]:
    """Components a leaf symlink resolves to, and why a target was refused.

    Absoluteness follows the **host**, not Windows syntax: on POSIX only a
    target starting with ``/`` is absolute. A Windows-shaped spelling such as
    ``C:x.jsonl``, ``C:/x.jsonl`` or ``\\\\srv\\share\\x.jsonl`` is a literal
    relative name there, exactly as the kernel reads it, so it goes through the
    ``parents`` + ``pushed`` walk below. It must never reach ``relpath``, which
    would resolve it against the process cwd and open a different file
    depending on where validation runs. Backslashes are literal too: the
    target is split on ``/`` only and is never normalised.

    ``..`` may climb only out of the artifact's real parent directories: the
    descriptor walk has already opened each of them with
    ``O_NOFOLLOW|O_DIRECTORY``, so climbing one is exactly what the kernel
    does. A component pushed from the link target itself is not known to be a
    directory (it can be a symlink, whose target then supplies the ``..``), so
    a following ``..`` is refused instead of normalised. ``.`` components are
    refused as well, because a trailing ``/.`` after a regular file is
    ``ENOTDIR`` for the kernel while dropping it would verify that file. Empty
    components (a doubled separator or a trailing slash) are refused for the
    same reason: dropping one would verify a path the kernel's pre-stat may
    reject, and the shape refusal is cheap defense in depth. An absolute target
    is shape-checked first, so its ``..`` is never collapsed by ``relpath``.

    Returns ``(parts, state)`` with state ``ok``, ``refused-alias`` or
    ``escapes-root``; ``parts`` is None unless the state is ``ok``."""
    if _is_absolute(link):
        parts, state = _absolute_parts(root, link)
        if parts is None and state == "refused-path":
            return None, "refused-alias"
        return parts, state
    out = list(parents)
    pushed = 0
    for part in link.split("/"):
        if part == "":
            return None, "refused-alias"
        if part == ".":
            return None, "refused-alias"
        if part == "..":
            if pushed:
                return None, "refused-alias"
            if not out:
                return None, "escapes-root"
            out.pop()
            continue
        if part.startswith("/") or "\x00" in part:
            return None, "refused-alias"
        out.append(part)
        pushed += 1
    parts = tuple(out)
    if not parts:
        return None, "escapes-root"
    return parts, "ok"


def _open_protected_path(root: Path, parts: tuple[str, ...], depth: int) -> _Protected:
    """Path-based fallback for platforms without directory-descriptor support.

    The final component is still opened with O_NOFOLLOW|O_NONBLOCK, and the
    identity checks keep a replacement visible, but a parent swap is not
    detectable without a descriptor walk. POSIX uses the descriptor version."""
    path = root.joinpath(*parts)
    if not _within(root, path):
        return _Protected("changed")
    try:
        before = os.lstat(path)
    except OSError as exc:
        return _Protected(_errno_state(exc))
    except (ValueError, UnicodeError):
        return _Protected("missing")
    if stat.S_ISLNK(before.st_mode):
        if depth >= _MAX_SYMLINK_DEPTH:
            return _Protected("changed")
        try:
            link = os.readlink(path)
        except OSError as exc:
            return _Protected(_errno_state(exc))
        except (ValueError, UnicodeError):
            return _Protected("missing")
        resolved, reason = _resolve_alias(root, parts[:-1], link)
        if resolved is None:
            # A target that climbs above the folder is containment; a target
            # whose own components would be popped by '..' is a shape refusal.
            return _Protected(reason)
        return _open_protected_path(root, resolved, depth + 1)
    if not stat.S_ISREG(before.st_mode):
        return _Protected("changed")
    try:
        fd = os.open(path, _LEAF_FLAGS)
    except OSError as exc:
        return _Protected(_errno_state(exc))
    except (ValueError, UnicodeError):
        return _Protected("missing")
    return _Protected("ok", fd=fd, parent_fd=None, leaf=None, before=before)


def _open_protected(root: Path, parts: tuple[str, ...], depth: int = 0) -> _Protected:
    if not _DIR_FD:
        return _open_protected_path(root, parts, depth)
    if not parts:
        return _Protected("changed")
    *parents, leaf = parts
    try:
        parent_fd = os.open(os.fspath(root), _DIR_FLAGS)
    except OSError as exc:
        return _Protected(_errno_state(exc))
    keep_parent = False
    try:
        for part in parents:
            try:
                child = os.open(part, _DIR_FLAGS, dir_fd=parent_fd)
            except OSError as exc:
                return _Protected(_parent_component_state(part, parent_fd, exc))
            os.close(parent_fd)
            parent_fd = child
        try:
            before = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as exc:
            return _Protected(_errno_state(exc))
        if stat.S_ISLNK(before.st_mode):
            if depth >= _MAX_SYMLINK_DEPTH:
                return _Protected("changed")
            try:
                link = os.readlink(leaf, dir_fd=parent_fd)
            except OSError as exc:
                return _Protected(_errno_state(exc))
            resolved, reason = _resolve_alias(root, tuple(parents), link)
            os.close(parent_fd)
            parent_fd = None
            if resolved is None:
                # A link target that climbs above the folder, even one that
                # lexically re-enters, is a containment refusal; a target whose
                # own component would be popped by '..' is a shape refusal.
                # Neither is a mid-read change and neither is opened.
                return _Protected(reason)
            return _open_protected(root, resolved, depth + 1)
        if not stat.S_ISREG(before.st_mode):
            return _Protected("changed")
        try:
            fd = os.open(leaf, _LEAF_FLAGS, dir_fd=parent_fd)
        except OSError as exc:
            return _Protected(_errno_state(exc))
        keep_parent = True
        return _Protected("ok", fd=fd, parent_fd=parent_fd, leaf=leaf, before=before)
    finally:
        if parent_fd is not None and not keep_parent:
            os.close(parent_fd)


def _close_protected(opened: _Protected) -> None:
    if opened.fd is not None:
        os.close(opened.fd)
    if opened.parent_fd is not None:
        os.close(opened.parent_fd)


def _identity(st: os.stat_result) -> tuple[int, int]:
    return (st.st_dev, st.st_ino)


def _post_read_stat(opened: _Protected) -> Optional[os.stat_result]:
    if opened.parent_fd is not None and opened.leaf is not None:
        return os.stat(opened.leaf, dir_fd=opened.parent_fd, follow_symlinks=False)
    return None


def _digest_protected(root: Path, parts: tuple[str, ...]) -> Optional[tuple[str, int]]:
    """Stream a candidate's digest through the protected open, never a
    blocking read and never the unprotected ``sha256_file``."""
    opened = _open_protected(root, parts)
    if opened.state != "ok" or opened.fd is None or opened.before is None:
        return None
    try:
        try:
            current = os.fstat(opened.fd)
        except OSError:
            return None
        if not stat.S_ISREG(current.st_mode):
            return None
        if _identity(current) != _identity(opened.before):
            return None
        digest = hashlib.sha256()
        total = 0
        while True:
            try:
                chunk = os.read(opened.fd, _READ_CHUNK)
            except OSError:
                return None
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
        after = None
        try:
            after = _post_read_stat(opened)
        except OSError:
            return None
        if after is None or _identity(after) != _identity(opened.before):
            # A path-based platform cannot re-stat through a descriptor; the
            # opened inode is still the one that was read.
            if opened.parent_fd is not None:
                return None
        return digest.hexdigest(), total
    finally:
        _close_protected(opened)


def _candidate_is_regular(root: Path, parts: tuple[str, ...]) -> bool:
    opened = _open_protected(root, parts)
    if opened.state != "ok" or opened.fd is None:
        return False
    try:
        try:
            return stat.S_ISREG(os.fstat(opened.fd).st_mode)
        except OSError:
            return False
    finally:
        _close_protected(opened)


class _TreeIndex:
    """Lazy index of files under root, built only if a hint needs it.

    Hint candidates are hashed through the same protected open as a targeted
    artifact. A digest already taken for a targeted artifact is reused instead
    of opening that artifact a second time."""

    def __init__(self, root: Path):
        self.root = root
        self._files: Optional[list[str]] = None
        self._known: dict[str, tuple[str, int]] = {}

    @property
    def files(self) -> list[str]:
        if self._files is None:
            try:
                self._files = [p for p in iter_files(self.root) if not is_own_file(p)]
            except ScanLimitError:
                # Hints are advisory and never change a finding. A tree over
                # the declared scan caps simply gets no moved-file hints.
                self._files = []
        return self._files

    def remember(self, raw: str, digest: str, size: int) -> None:
        parts = _parts_for(self.root, raw)
        if parts is not None:
            self._known["/".join(parts)] = (digest, size)

    def _same_digest(self, rel: str, art: Artifact) -> bool:
        if art.sha256 is None or art.volatile:
            return True
        if not _within(self.root, self.root / rel):
            return False
        parts = _parts_for(self.root, rel)
        if parts is None:
            return False
        known = self._known.get("/".join(parts))
        if known is not None:
            digest, size = known
        else:
            read = _digest_protected(self.root, parts)
            if read is None:
                return False
            digest, size = read
        if art.size_bytes is not None and size != art.size_bytes:
            return False
        return digest == art.sha256

    def same_content(self, rel: str, art: Artifact) -> bool:
        """Could ``rel`` be the artifact's file? With a recorded digest this
        is a byte-for-byte check; without one (or for a volatile file),
        any file qualifies."""
        return self._same_digest(rel, art)

    def find_by_suffix(self, parts: tuple[str, ...], art: Artifact) -> Optional[str]:
        """Longest trailing run of path components that exists under root
        (and matches the recorded digest, if any)."""
        for i in range(len(parts)):
            candidate = "/".join(parts[i:])
            if not candidate:
                continue
            candidate_parts = _parts_for(self.root, candidate)
            if candidate_parts is None:
                continue
            if not _candidate_is_regular(self.root, candidate_parts):
                continue
            if self.same_content(candidate, art):
                return candidate
        return None

    def find_moved(self, name: str, art: Artifact) -> list[str]:
        """Files elsewhere in the tree that could be the missing artifact.

        In ML folders every run tends to have the same file names
        (metrics.json, predictions.csv), so a name match alone is weak
        evidence. When a digest is recorded, only identical files count."""
        lowered = name.lower()
        same_name = [p for p in self.files if PurePosixPath(p).name.lower() == lowered]
        return [p for p in same_name if self.same_content(p, art)]


def _is_absolute(raw: str) -> bool:
    """True only when the running host treats ``raw`` as an absolute path.

    Windows-shaped drive or UNC spellings are *not* absolute on POSIX; they are
    literal relative names and must be resolved relative to the manifest folder
    (or, for a link target, to the link's own directory) exactly as the kernel
    does. Using ``PureWindowsPath`` here sent them to ``os.path.relpath``,
    which made the verdict depend on the process cwd."""
    return os.path.isabs(raw)


def _windows_absolute_shape(norm: str) -> bool:
    """True when a normalised spelling looks Windows-absolute: a drive root.

    Diagnosis only. Resolution stays host-native: a drive spelling is still a
    literal relative name under the manifest folder on POSIX and is never
    turned into ``Path(raw)`` or resolved against the process cwd. The legacy
    0.1/0.2 branch uses this to keep the ``absolute-path`` warning and the
    "(folder looks relocated)" hint a Windows-written manifest received before
    absoluteness followed the host. ``C:x`` is drive-relative, not absolute,
    and is deliberately excluded; backslashes are already normalised for
    manifest artifact paths, so ``C:\\x`` arrives as ``C:/x``. A UNC spelling
    needs no help here: its normalised ``//server/share`` form is already
    host-absolute and takes the host-absolute branch."""
    if len(norm) >= 3 and norm[0].isalpha() and norm[1] == ":" and norm[2] == "/":
        return True
    return False


def _case_mismatch(root: Path, rel_parts: tuple[str, ...]) -> Optional[str]:
    """If the path only resolves because the filesystem ignores case, return
    the on-disk spelling. That path would break on Linux or in a zip."""
    current = root
    actual: list[str] = []
    changed = False
    for part in rel_parts:
        if part in (".", ""):
            continue
        if part == "..":
            current = current.parent
            actual.append(part)
            continue
        try:
            names = os.listdir(current)
        except OSError:
            return None
        if part in names:
            match = part
        else:
            lowered = [n for n in names if n.lower() == part.lower()]
            if not lowered:
                return None
            match = lowered[0]
            changed = True
        actual.append(match)
        current = current / match
    return "/".join(actual) if changed else None


def _absolute_path_finding(art: Artifact, raw: str, relocated: Optional[str]) -> Finding:
    """The ``absolute-path`` warning, with a relocation hint when one is known.

    Shared by a host-absolute spelling and by the legacy Windows-shaped
    spelling, which is resolved host-natively but keeps this diagnostic so a
    Windows-written 0.1/0.2 manifest is not silently treated as portable."""
    return Finding(
        code=Code.absolute_path,
        severity=Severity.warning,
        subject=art.id,
        path=raw,
        message=f"Absolute path breaks when the folder is moved or shared: {raw!r}",
        hint=(
            f"Store it relative to the manifest folder, e.g. {relocated!r}"
            if relocated
            else "Store paths relative to the manifest folder."
        ),
    )


def _prepare_target(
    art: Artifact, root: Path, index: _TreeIndex, out: FileCheckResult, *, targeted: bool
) -> Optional[Path]:
    """Path/reference checks shared by legacy and snapshot file checks.

    Returns the lexical target when a read should be attempted. Missing paths
    get ``missing-file``; a path that is not a regular file gets ``not-a-file``.
    An errno other than ENOENT/ENOTDIR is deliberately left to the reader:
    the targeted path maps it to ``locator-unreadable``/``artifact-changed``
    and the legacy path to a fixed ``missing-file``."""
    raw = art.path
    add = out.findings.append

    # ``os.stat``/``os.open`` raise ``ValueError`` for an embedded NUL rather
    # than an ``OSError``. The pre-protected code's ``Path.exists()`` returned
    # False for it, so the fixed legacy outcome is a missing artifact; reject
    # it here before any absolute/containment branch can mislabel it.
    if "\x00" in raw:
        out.broken.add(art.id)
        add(
            Finding(
                code=Code.missing_file,
                severity=Severity.error,
                subject=art.id,
                path=raw,
                message=f"Artifact {art.id!r} is missing: {raw!r} does not exist",
            )
        )
        return None

    norm = raw
    if "\\" in raw:
        norm = raw.replace("\\", "/")
        add(
            Finding(
                code=Code.non_posix_path,
                severity=Severity.warning,
                subject=art.id,
                path=raw,
                message=f"Path uses backslashes: {raw!r}",
                hint=f"Use forward slashes so the manifest works on every OS: {norm!r}",
            )
        )

    absolute = _is_absolute(norm)
    # Diagnosis only, and only on the legacy (locator-less) path: a drive-root
    # spelling is still resolved host-natively below, never via Path(raw)/cwd.
    windows_shape = (
        not targeted and not absolute and _windows_absolute_shape(norm)
    )
    windows_relocated: Optional[str] = None
    target = Path(norm) if absolute else root / norm

    if absolute:
        # The legacy branch stat'ed the absolute spelling before containment;
        # keep that order so an outside path is still reported as escaping.
        state, probe = _stat_path(target)
        is_file = probe is not None and stat.S_ISREG(probe.st_mode)
        inside = _within(root, target)
        parts = PurePosixPath(PureWindowsPath(norm).as_posix()).parts[1:]
        relocated = index.find_by_suffix(tuple(parts), art) if not is_file else None
        add(_absolute_path_finding(art, raw, relocated))
        if not inside:
            if (
                targeted
                and state in ("unreadable", "changed")
                and _parts_for(root, norm) is not None
                and _realpath_lives_in(root, target)
            ):
                # A loop or denied parent on a path that is both lexically and
                # physically inside the root is mapped by the protected
                # reader. A spelling that only looks inside (a link whose loop
                # entry resolves elsewhere) was never openable by that walk,
                # so it keeps the legacy containment finding below.
                return target
            out.broken.add(art.id)
            if not is_file and state == "missing" and relocated:
                # The old absolute location is gone, so offer the copy that
                # moved with the folder instead of only rejecting the path.
                add(
                    Finding(
                        code=Code.missing_file,
                        severity=Severity.error,
                        subject=art.id,
                        path=raw,
                        message=f"Artifact {art.id!r} not found at {raw!r} (folder looks relocated)",
                        hint=f"A file matching it exists at {relocated!r}; update the path to that.",
                    )
                )
            else:
                add(
                    Finding(
                        code=Code.path_escapes_root,
                        severity=Severity.error,
                        subject=art.id,
                        path=raw,
                        message=f"Absolute path resolves outside the experiment folder: {raw!r}",
                        hint="Copy the file into the folder (or move the manifest up) so the evidence travels together.",
                    )
                )
            return None
        if not is_file and state == "missing" and relocated:
            out.broken.add(art.id)
            add(
                Finding(
                    code=Code.missing_file,
                    severity=Severity.error,
                    subject=art.id,
                    path=raw,
                    message=f"Artifact {art.id!r} not found at {raw!r} (folder looks relocated)",
                    hint=f"A file matching it exists at {relocated!r}; update the path to that.",
                )
            )
            return None
    else:
        inside = _within(root, target)
        state, probe = _stat_path(target)
        if windows_shape:
            # A Windows-written manifest was warned before absoluteness followed
            # the host. Keep only the diagnostic: the artifact is still read
            # from the manifest folder, so the verdict cannot depend on the cwd.
            is_file = probe is not None and stat.S_ISREG(probe.st_mode)
            if not is_file:
                parts = PurePosixPath(PureWindowsPath(norm).as_posix()).parts[1:]
                windows_relocated = index.find_by_suffix(tuple(parts), art)
            add(_absolute_path_finding(art, raw, windows_relocated))
        if not inside:
            if (
                targeted
                and state == "changed"
                and _parts_for(root, norm) is not None
                and _realpath_lives_in(root, target)
            ):
                # An in-root leaf loop is mapped by the protected reader. A
                # loop whose entry resolves outside is a containment failure
                # even though every component is spelled inside; the reader
                # would report a mid-read change and lose that diagnosis.
                return target
            out.broken.add(art.id)
            add(
                Finding(
                    code=Code.path_escapes_root,
                    severity=Severity.error,
                    subject=art.id,
                    path=raw,
                    message=f"Path points outside the experiment folder: {raw!r}",
                    hint="Copy the file into the folder (or move the manifest up) so the evidence travels together.",
                )
            )
            return None

    if state == "missing":
        out.broken.add(art.id)
        if windows_shape and windows_relocated:
            # The old drive spelling is gone, so offer the copy that moved with
            # the folder, exactly as the legacy absolute branch does.
            add(
                Finding(
                    code=Code.missing_file,
                    severity=Severity.error,
                    subject=art.id,
                    path=raw,
                    message=f"Artifact {art.id!r} not found at {raw!r} (folder looks relocated)",
                    hint=f"A file matching it exists at {windows_relocated!r}; update the path to that.",
                )
            )
            return None
        name = PurePosixPath(norm).name
        candidates = [c for c in index.find_moved(name, art) if c != norm] if name else []
        hint = None
        if candidates:
            shown = ", ".join(repr(c) for c in candidates[:3])
            if art.sha256 and not art.volatile:
                hint = f"An identical file (same sha256) exists at {shown}. Was it moved?"
            else:
                hint = (
                    f"A file with the same name exists at {shown}, but with no sha256 "
                    "recorded it cannot be confirmed to be the same file."
                )
        add(
            Finding(
                code=Code.missing_file,
                severity=Severity.error,
                subject=art.id,
                path=raw,
                message=f"Artifact {art.id!r} is missing: {raw!r} does not exist",
                hint=hint,
            )
        )
        return None

    if state in ("unreadable", "changed"):
        # The reader maps the errno; existence branching must not crash here.
        return target

    if not stat.S_ISREG(probe.st_mode):
        out.broken.add(art.id)
        add(
            Finding(
                code=Code.not_a_file,
                severity=Severity.error,
                subject=art.id,
                path=raw,
                message=f"Artifact {art.id!r} path is not a regular file: {raw!r}",
                hint="Directory artifacts are not supported yet; list the files inside individually.",
            )
        )
        return None

    if not absolute:
        actual = _case_mismatch(root, PurePosixPath(norm).parts)
        if actual:
            add(
                Finding(
                    code=Code.path_case_mismatch,
                    severity=Severity.warning,
                    subject=art.id,
                    path=raw,
                    message=f"Path only matches because this filesystem ignores case: {raw!r} vs {actual!r}",
                    hint=f"Use the exact on-disk spelling {actual!r}; it will break on Linux or macOS otherwise.",
                )
            )
    return target


def _legacy_read_failure(art: Artifact, out: FileCheckResult) -> None:
    """Fixed legacy text: never echo OS or exception text."""
    out.broken.add(art.id)
    out.findings.append(
        Finding(
            code=Code.missing_file,
            severity=Severity.error,
            subject=art.id,
            path=art.path,
            message=f"Artifact {art.id!r} could not be read: {art.path!r}",
        )
    )


def _targeted_failure(
    art: Artifact, out: FileCheckResult, state: str, *, shape: Optional[str] = None
) -> TargetCheck:
    out.broken.add(art.id)
    if state == "missing":
        out.findings.append(
            Finding(
                code=Code.missing_file,
                severity=Severity.error,
                subject=art.id,
                path=art.path,
                message=f"Artifact {art.id!r} is missing: {art.path!r} does not exist",
            )
        )
        return TargetCheck(art.id, "missing")
    if state == "changed":
        out.findings.append(
            Finding(
                code=Code.artifact_changed,
                severity=Severity.error,
                subject=art.id,
                path=art.path,
                message="the artifact changed while it was being read for its locators",
            )
        )
        return TargetCheck(art.id, "changed")
    if state == "symlinked-parent":
        out.findings.append(
            Finding(
                code=Code.artifact_changed,
                severity=Severity.error,
                subject=art.id,
                path=art.path,
                message="a parent directory is a symbolic link; use the real path",
                hint="Point the artifact path at the real folder; a parent link is never followed.",
            )
        )
        return TargetCheck(art.id, "symlinked-parent")
    if state == "refused-path":
        # The path is inside the folder and unchanged; only its spelling is
        # refused, so it must not be described as a mid-read change. This uses
        # the existing locator-unreadable code, not a new one. The message is
        # fixed and value-free, and names the actual shape: a '..' refusal is
        # not the only one (an empty, '.' or trailing-slash component is too).
        if shape is None:
            # Diagnosis is unknown (for example a relpath failure on a plain
            # spelling). Never invent a component the spelling does not have.
            out.findings.append(
                Finding(
                    code=Code.locator_unreadable,
                    severity=Severity.error,
                    subject=art.id,
                    path=art.path,
                    message="the artifact could not be read safely for its locators",
                )
            )
            return TargetCheck(art.id, "refused-path")
        if shape == "dotdot":
            message = "artifact path uses '..'; write it without parent references"
            hint = (
                "A '..' spelling is never normalised, because it can name a "
                "different file than the one the reader opens. Use the plain path."
            )
        else:
            message = (
                "artifact path has an empty, '.' or trailing-slash component; "
                "write the plain file path"
            )
            hint = (
                "Write the path exactly as the file inside the folder: no doubled "
                "separator, no '.' component and no trailing slash."
            )
        out.findings.append(
            Finding(
                code=Code.locator_unreadable,
                severity=Severity.error,
                subject=art.id,
                path=art.path,
                message=message,
                hint=hint,
            )
        )
        return TargetCheck(art.id, "refused-path")
    if state == "refused-alias":
        # The artifact path is plain; the leaf link's target is the shape that
        # cannot be verified (a '..' after a component of the target itself).
        # Nothing was opened, so this is never a mid-read change.
        out.findings.append(
            Finding(
                code=Code.locator_unreadable,
                severity=Severity.error,
                subject=art.id,
                path=art.path,
                message=(
                    "the artifact's symbolic link target is not verified; point the "
                    "link at a plain path inside the folder"
                ),
                hint=(
                    "A target may use '..' only to climb the artifact's real parent "
                    "folders; a '..' after a component of the target itself is never "
                    "normalised."
                ),
            )
        )
        return TargetCheck(art.id, "refused-alias")
    if state == "escapes-root":
        # A leaf symlink whose target climbs above the folder (even one that
        # re-enters) or resolves outside is a containment refusal, exactly like
        # a path spelled outside. It was never opened.
        out.findings.append(
            Finding(
                code=Code.path_escapes_root,
                severity=Severity.error,
                subject=art.id,
                path=art.path,
                message=f"Path points outside the experiment folder: {art.path!r}",
                hint=(
                    "Copy the file into the folder (or move the manifest up) so the "
                    "evidence travels together."
                ),
            )
        )
        return TargetCheck(art.id, "escapes-root")
    out.findings.append(
        Finding(
            code=Code.locator_unreadable,
            severity=Severity.error,
            subject=art.id,
            path=art.path,
            message="the artifact could not be read safely for its locators",
        )
    )
    return TargetCheck(art.id, "unreadable")


def check_artifact(
    art: Artifact,
    root: Path,
    index: _TreeIndex,
    out: FileCheckResult,
    *,
    targeted: bool = False,
    tensorboard_raised: bool = False,
) -> TargetCheck:
    """The one artifact-level check. ``targeted`` chooses the protected
    snapshot instead of a streaming hash; every other rule is identical.

    ``tensorboard_raised`` selects the 128 MiB cap and the TensorBoard-only
    fstat sizing/race rules. It is set only when a kind-eligible 0.5
    tensorboard locator targets this artifact."""
    target = _prepare_target(art, root, index, out, targeted=targeted)
    if target is None:
        return TargetCheck(art.id, "reference-failed")

    if art.volatile:
        # Existence is the whole check: the bytes change on every rerun.
        out.readable_paths[art.id] = target
        out.ok_paths[art.id] = target
        return TargetCheck(art.id, "volatile" if targeted else "ok")

    if targeted:
        parts, path_state = _targeted_parts(root, art.path)
        if parts is None:
            # Keep a containment refusal as containment: a plain spelling must
            # never be described as having an empty or '.' component merely
            # because its root prefix could not be removed.
            shape = _shape_problem(art.path) if path_state == "refused-path" else None
            return _targeted_failure(art, out, path_state, shape=shape)
        read = _read_snapshot_parts(
            root,
            parts,
            cap=TENSORBOARD_MAX_FILE_BYTES if tensorboard_raised else MAX_FILE_BYTES,
            tensorboard_raised=tensorboard_raised,
        )
        if read.state not in ("ok", "over-cap"):
            shape = _shape_problem(art.path) if read.state == "refused-path" else None
            return _targeted_failure(art, out, read.state, shape=shape)
        snapshot = read.snapshot
        assert snapshot is not None
        out.readable_paths[art.id] = target
        index.remember(art.path, snapshot.digest, snapshot.size)
        if art.sha256 is None:
            # The result-level locator-no-digest finding is authoritative, but
            # readability and size are still checked before it is returned.
            if art.size_bytes is not None and art.size_bytes != snapshot.size:
                out.broken.add(art.id)
                out.findings.append(
                    Finding(
                        code=Code.size_mismatch,
                        severity=Severity.error,
                        subject=art.id,
                        path=art.path,
                        message=f"Artifact {art.id!r} size changed: {art.size_bytes} recorded, {snapshot.size} on disk",
                    )
                )
            return TargetCheck(art.id, "no-digest")
        if snapshot.digest != art.sha256:
            out.broken.add(art.id)
            size_note = ""
            if art.size_bytes is not None and art.size_bytes != snapshot.size:
                size_note = f" Size also changed: {art.size_bytes} -> {snapshot.size} bytes."
            out.findings.append(
                Finding(
                    code=Code.hash_mismatch,
                    severity=Severity.error,
                    subject=art.id,
                    path=art.path,
                    message=(
                        f"Artifact {art.id!r} changed since it was recorded: "
                        f"sha256 {art.sha256[:12]}... recorded, {snapshot.digest[:12]}... on disk.{size_note}"
                    ),
                    hint=(
                        f"If the change is intended, accept it with `runevidence rehash <folder> {art.id}`; "
                        "otherwise restore the original file."
                    ),
                )
            )
        elif art.size_bytes is not None and art.size_bytes != snapshot.size:
            out.broken.add(art.id)
            out.findings.append(
                Finding(
                    code=Code.size_mismatch,
                    severity=Severity.error,
                    subject=art.id,
                    path=art.path,
                    message=f"Artifact {art.id!r} size changed: {art.size_bytes} recorded, {snapshot.size} on disk",
                )
            )
        else:
            out.ok_paths[art.id] = target
        return TargetCheck(
            art.id,
            "over-cap" if snapshot.over_cap else "ok",
            None if snapshot.over_cap else snapshot,
        )

    try:
        digest = sha256_file(target)
        size = os.stat(target).st_size
    except OSError:
        _legacy_read_failure(art, out)
        return TargetCheck(art.id, "unreadable")

    out.readable_paths[art.id] = target
    if art.sha256 is None:
        out.findings.append(
            Finding(
                code=Code.no_digest,
                severity=Severity.warning,
                subject=art.id,
                path=art.path,
                message=f"Artifact {art.id!r} has no recorded sha256, so changes cannot be detected",
                hint=(
                    f'Record it with `runevidence rehash <folder> {art.id}` (or "sha256": "{digest}"); '
                    "if its bytes change on every rerun, add --volatile"
                ),
            )
        )
    elif digest != art.sha256:
        out.broken.add(art.id)
        size_note = ""
        if art.size_bytes is not None and art.size_bytes != size:
            size_note = f" Size also changed: {art.size_bytes} -> {size} bytes."
        out.findings.append(
            Finding(
                code=Code.hash_mismatch,
                severity=Severity.error,
                subject=art.id,
                path=art.path,
                message=(
                    f"Artifact {art.id!r} changed since it was recorded: "
                    f"sha256 {art.sha256[:12]}... recorded, {digest[:12]}... on disk.{size_note}"
                ),
                hint=(
                    f"If the change is intended, accept it with `runevidence rehash <folder> {art.id}`; "
                    "otherwise restore the original file."
                ),
            )
        )
        return TargetCheck(art.id, "ok")

    if art.size_bytes is not None and art.size_bytes != size:
        # Only reachable without a digest (a matching digest implies matching size).
        out.broken.add(art.id)
        out.findings.append(
            Finding(
                code=Code.size_mismatch,
                severity=Severity.error,
                subject=art.id,
                path=art.path,
                message=f"Artifact {art.id!r} size changed: {art.size_bytes} recorded, {size} on disk",
            )
        )
        return TargetCheck(art.id, "ok")

    out.ok_paths[art.id] = target
    return TargetCheck(art.id, "ok")


@dataclass(frozen=True)
class Snapshot:
    """Bounded bytes and digest read through one protected descriptor."""

    data: Optional[bytes | bytearray]
    digest: str
    size: int
    over_cap: bool


@dataclass(frozen=True)
class SnapshotRead:
    state: str  # ok | over-cap | missing | unreadable | changed | symlinked-parent | refused-path | refused-alias | escapes-root
    snapshot: Optional[Snapshot] = None


@dataclass(frozen=True)
class TargetCheck:
    """The file-side state of one artifact targeted by a 0.3 locator."""

    artifact_id: str
    state: str
    snapshot: Optional[Snapshot] = None


def _read_snapshot_parts(
    root: Path,
    parts: Optional[tuple[str, ...]],
    *,
    cap: Optional[int] = None,
    tensorboard_raised: bool = False,
) -> SnapshotRead:
    """One protected read of ``parts`` under ``root``.

    Containment is enforced by the descriptor walk alone. There is deliberately
    no pre-open ``resolve``/prefix check: such a check cannot tell an in-root
    parent alias from an escaping one and would report both as a mid-read
    change. ``parts is None`` means the path shape itself is refused (``..``,
    absolute, empty or NUL components) and gets its own state, never a race.
    A leaf link whose target cannot be resolved on shape gets ``refused-alias``
    from the walk, also never a race.

    The delivered read-to-EOF behavior is used for every artifact except one
    raised by a kind-eligible TensorBoard locator. That branch sizes from
    ``os.fstat`` on the already-open descriptor, allocates once, truncates a
    short read in place and applies the RFC section 5 race table. An over-cap
    file is stream-hashed without retaining a snapshot."""
    if parts is None:
        return SnapshotRead("refused-path")
    limit = MAX_FILE_BYTES if cap is None else cap
    opened = _open_protected(root, parts)
    if opened.state != "ok":
        return SnapshotRead(opened.state)
    assert opened.fd is not None and opened.before is not None
    try:
        try:
            current = os.fstat(opened.fd)
        except OSError:
            return SnapshotRead("unreadable")
        if not stat.S_ISREG(current.st_mode):
            return SnapshotRead("changed")
        if _identity(current) != _identity(opened.before):
            return SnapshotRead("changed")

        if tensorboard_raised:
            return _read_tensorboard_snapshot(opened, current, limit)

        digest = hashlib.sha256()
        buffer = bytearray()
        total = 0
        over_cap = False
        while True:
            try:
                chunk = os.read(opened.fd, _READ_CHUNK)
            except OSError:
                return SnapshotRead("unreadable")
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                over_cap = True
                buffer.clear()
            elif not over_cap:
                buffer.extend(chunk)
            digest.update(chunk)

        try:
            after = _post_read_stat(opened)
        except OSError as exc:
            return SnapshotRead("changed" if _errno_state(exc) == "missing" else "unreadable")
        if after is not None and _identity(after) != _identity(opened.before):
            return SnapshotRead("changed")

        snapshot = Snapshot(
            data=None if over_cap else bytes(buffer),
            digest=digest.hexdigest(),
            size=total,
            over_cap=over_cap,
        )
        return SnapshotRead("over-cap" if over_cap else "ok", snapshot)
    finally:
        _close_protected(opened)


def _read_tensorboard_snapshot(
    opened: _Protected, current: os.stat_result, limit: int
) -> SnapshotRead:
    """The TensorBoard-raised read (RFC sections 5 and 6).

    ``current`` is the fstat taken on the opened protected descriptor. The
    pre-read size decides the allocation: at most the cap, never the actual
    file path. EOF is the running total, so a concurrent grow or shrink is
    classified from the same bytes that were hashed. An over-cap file keeps no
    snapshot but is still fully stream-hashed for the artifact checks."""
    assert opened.fd is not None and opened.before is not None
    fstat_size = current.st_size
    digest = hashlib.sha256()
    total = 0
    buffer: Optional[bytearray] = None
    over_cap = False
    if fstat_size <= limit:
        # Allocate once from the descriptor's own size; never from a path stat
        # and never above the cap.
        buffer = bytearray(fstat_size)
    while True:
        try:
            chunk = os.read(opened.fd, _READ_CHUNK)
        except OSError:
            return SnapshotRead("unreadable")
        if not chunk:
            break
        start = total
        total += len(chunk)
        digest.update(chunk)
        if buffer is not None:
            if total <= fstat_size:
                buffer[start:total] = chunk
            else:
                # Growth beyond the fstat size is artifact-changed, or over-cap
                # if it passes the cap; neither path needs the retained bytes.
                buffer = None
        if total > limit:
            over_cap = True
            buffer = None

    try:
        after = _post_read_stat(opened)
    except OSError as exc:
        return SnapshotRead("changed" if _errno_state(exc) == "missing" else "unreadable")
    if after is not None and _identity(after) != _identity(opened.before):
        return SnapshotRead("changed")

    if fstat_size <= limit:
        if total > limit:
            state = "over-cap"
        elif total != fstat_size:
            state = "changed"
        else:
            state = "ok"
    else:
        # fstat over the cap: only a read that also ends past the cap is
        # over-cap; everything at or below the cap is a shrink race.
        state = "over-cap" if total > limit else "changed"

    if state == "changed":
        return SnapshotRead("changed")
    if state == "over-cap":
        return SnapshotRead(
            "over-cap",
            Snapshot(data=None, digest=digest.hexdigest(), size=total, over_cap=True),
        )
    assert buffer is not None
    del buffer[total:]
    return SnapshotRead(
        "ok",
        Snapshot(data=buffer, digest=digest.hexdigest(), size=total, over_cap=False),
    )


def _read_snapshot(target: Path, root: Path) -> SnapshotRead:
    """Compatibility wrapper: read an absolute or root-relative path."""
    return _read_snapshot_parts(root, _parts_for(root, os.fspath(target)))


# Fixed public texts for directory findings. Scanner exceptions may contain an
# absolute root, member names, OS strerror text, inode/device/mount ids or a
# fresh digest; none of that may reach a finding (human or JSON output).
_SCAN_REASONS = frozenset({
    "unsupported-platform", "unsupported-filesystem", "invalid-root", "missing",
    "not-a-directory", "unsafe", "collision", "limit-exceeded", "unreadable", "unstable",
})
_RECORD_REASONS = frozenset({
    "invalid-record", "invalid-name", "reserved-name", "protected-name",
    "path-collision", "missing-parent", "limit-exceeded",
})


def _fixed_reason(reason: object, allowed: frozenset[str]) -> str:
    return reason if isinstance(reason, str) and reason in allowed else "unknown"


def _check_directory_internal(art: Artifact, root: Path, out: FileCheckResult) -> set[str]:
    """Opt-in development path; the native scanner enforces root safety.

    Findings are public: messages use fixed reason/class text only, never the
    scanner's verbose exception text. Returns the files a successful,
    nonvolatile scan recorded, as ``os.path.normcase(os.path.abspath(...))``
    keys under the validated root, so untracked-file reporting can treat
    exactly those files as covered. Any failure returns an empty set: nothing
    is hidden on a guess."""
    from ..directory_hashing import DEFAULT_LIMITS, FILE, DirectoryRecordError, check_member_path
    from ..directory_scanner import DirectoryScanError, scan_directory
    from .directory_policy import check_directory_policy

    def issue(code: Code, severity: Severity, message: str) -> None:
        out.findings.append(Finding(code=code, severity=severity, subject=art.id, path=art.path, message=message))
        if severity == Severity.error:
            out.broken.add(art.id)

    # The root path follows the same v1 name profile as its members, checked
    # before the scanner is called at all.
    path = art.path
    try:
        raw = check_member_path(path)
    except DirectoryRecordError as exc:
        issue(Code.directory_unsafe, Severity.error,
              f"Directory path is not a strict relative portable path ({_fixed_reason(exc.reason, _RECORD_REASONS)})")
        return set()
    parts = path.split("/")
    if len(raw) > DEFAULT_LIMITS.path_bytes or len(parts) > DEFAULT_LIMITS.depth:
        issue(Code.directory_limit_exceeded, Severity.error,
              f"Directory path exceeds {DEFAULT_LIMITS.path_bytes} bytes or {DEFAULT_LIMITS.depth} components")
        return set()
    if art.volatile:
        # Never scanned: a volatile folder has nothing stable to compare.
        check_directory_policy(art, None)
        issue(Code.directory_unsafe, Severity.error, "Volatile directory is unverified")
        return set()
    scan_root = root.absolute().joinpath(*parts)
    try:
        outcome = scan_directory(scan_root)
    except DirectoryScanError as exc:
        outcome = exc
    result = check_directory_policy(art, outcome)
    if result.status == "scan_error":
        codes = {"missing": Code.missing_directory, "not-a-directory": Code.not_a_directory,
                 "unsafe": Code.directory_unsafe, "invalid-root": Code.directory_unsafe,
                 "unreadable": Code.directory_unreadable, "unstable": Code.directory_unstable,
                 "limit-exceeded": Code.directory_limit_exceeded, "collision": Code.directory_path_collision}
        issue(codes.get(result.reason, Code.directory_unsafe), Severity.error,
              f"Directory scan failed ({_fixed_reason(result.reason, _SCAN_REASONS)})")
        return set()
    if result.status == "changed":
        if result.digest_matches is False:
            issue(Code.directory_hash_mismatch, Severity.error, "Directory digest changed")
        if result.size_matches is False:
            issue(Code.directory_size_mismatch, Severity.error, "Directory size changed")
    elif result.status == "incomplete":
        issue(Code.directory_no_digest, Severity.warning, "Directory has no recorded digest")
    elif result.status != "match":
        issue(Code.directory_unsafe, Severity.error,
              f"Directory could not be verified ({_fixed_reason(result.reason, _SCAN_REASONS)})")
        return set()
    return {
        os.path.normcase(os.path.abspath(scan_root.joinpath(*rec.path.split("/"))))
        for rec in outcome.records
        if rec.kind == FILE
    }


def check_files(
    loaded: LoadedManifest,
    include_untracked: bool = False,
    *,
    enable_directory_artifacts: bool = False,
    targeted_ids: Optional[set[str]] = None,
    on_targeted=None,
    tensorboard_snapshot_ids: Optional[set[str]] = None,
) -> FileCheckResult:
    """Check every artifact through one path.

    ``targeted_ids`` names artifacts that must use the protected snapshot
    instead of a streaming hash. ``on_targeted`` is called immediately after
    that artifact's check so its locators consume the snapshot and then let it
    be released before the next artifact is read (peak memory is one
    snapshot). ``tensorboard_snapshot_ids`` raises the snapshot cap to 128 MiB
    and selects the TensorBoard-only sizing rules for exactly those artifacts;
    the caller computes the set from the 0.5 locators and the basename
    predicate so this module never re-reads the model."""
    root = loaded.root
    index = _TreeIndex(root)
    out = FileCheckResult()
    covered: set[str] = set()
    targeted_ids = targeted_ids or set()
    tensorboard_snapshot_ids = tensorboard_snapshot_ids or set()
    for art in loaded.manifest.artifacts:
        targeted = art.id in targeted_ids
        state: Optional[TargetCheck] = None
        if art.artifact_type == "directory":
            # A targeted directory still runs the ordinary directory or
            # file check; the locator layer adds directory-locator-unsupported
            # on top, with or without --directory-artifacts.
            if enable_directory_artifacts:
                covered |= _check_directory_internal(art, root, out)
            else:
                check_artifact(art, root, index, out, targeted=False)
            if targeted:
                state = TargetCheck(art.id, "directory")
        else:
            state = check_artifact(
                art,
                root,
                index,
                out,
                targeted=targeted,
                tensorboard_raised=targeted and art.id in tensorboard_snapshot_ids,
            )
        if targeted and on_targeted is not None:
            on_targeted(art, state, out)
        # Drop the state (and its snapshot) before reading the next artifact.
        state = None
        del state

    if include_untracked:
        # An opted-in directory covers only the files its scan recorded, never
        # whatever happens to sit under its raw path.
        tracked = {
            os.path.normcase(os.path.abspath(root / a.path.replace("\\", "/")))
            for a in loaded.manifest.artifacts
            if not (enable_directory_artifacts and a.artifact_type == "directory")
        } | covered
        for rel in index.files:
            if os.path.normcase(os.path.abspath(root / rel)) not in tracked:
                out.findings.append(
                    Finding(
                        code=Code.untracked_file,
                        severity=Severity.info,
                        path=rel,
                        message=f"File is in the folder but not in the manifest: {rel!r}",
                    )
                )
    return out


def assert_tree_readable(root: Path) -> None:
    """Raise OSError when any traversed directory under ``root`` cannot be listed.

    ``iter_files`` deliberately suppresses directory read errors for the
    delivered generic scan. Adapters that promise to hash every file use this
    check so an unreadable folder cannot silently omit artifacts. Ignored
    directories, outside links and cycles are skipped exactly as ``iter_files``
    skips them.
    """
    base = Path(root)
    base_real = _real(base)
    if base_real is None:
        raise OSError("root cannot be resolved")
    state = {"directories": 0, "files": 0}

    def walk(current: Path, ancestors: set[str], depth: int) -> None:
        if depth > MAX_SCAN_DEPTH:
            raise ScanLimitError(f"folder depth exceeds {MAX_SCAN_DEPTH}")
        state["directories"] += 1
        if state["directories"] > MAX_SCAN_DIRECTORIES:
            raise ScanLimitError(f"more than {MAX_SCAN_DIRECTORIES} folders were scanned")
        entries = sorted(os.scandir(current), key=lambda entry: entry.name)
        for entry in entries:
            try:
                is_dir = entry.is_dir(follow_symlinks=True)
            except OSError:
                # Match iter_files: an unreadable link target is not a directory.
                is_dir = False
            if is_dir:
                if entry.name in IGNORED_DIRS or not _within(base, Path(entry.path)):
                    continue
                real = _real(Path(entry.path))
                if real is None or real in ancestors:
                    continue
                walk(Path(entry.path), ancestors | {real}, depth + 1)
                continue
            try:
                is_file = entry.is_file(follow_symlinks=True)
            except OSError:
                continue
            if is_file:
                state["files"] += 1
                if state["files"] > MAX_SCAN_FILES:
                    raise ScanLimitError(f"more than {MAX_SCAN_FILES} files were scanned")

    walk(base, {base_real}, 0)
