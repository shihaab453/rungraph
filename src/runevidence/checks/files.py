"""Local-file checks: does every artifact exist where the manifest says, with
the bytes the manifest recorded?

Paths are always resolved relative to the manifest's folder, never the
current working directory, so results do not depend on where you run from.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Optional

from ..findings import Code, Finding, Severity
from ..hashing import sha256_file
from ..manifest import LoadedManifest
from ..models import BACKUP_FILENAME, MANIFEST_FILENAME, Artifact

# Folders never treated as experiment content when scanning for hints or
# untracked files.
IGNORED_DIRS = {".git", ".hg", ".svn", "__pycache__", ".venv", "venv", ".ipynb_checkpoints"}


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


def iter_files(root: Path, folder: Optional[Path] = None):
    """Yield every regular file physically under root as a POSIX relative path.

    A directory link (symbolic link or Windows junction) is followed only when
    it resolves inside root; anything outside is pruned before traversal, so a
    scan never reads another folder's bytes. Resolved directories on the
    current branch are remembered to stop link cycles deterministically."""
    base = folder if folder is not None else root
    base_real = _real(base)
    if base_real is None:
        return

    def walk(current: Path, ancestors: set[str]):
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
            yield full.relative_to(root).as_posix()
        for full, real in dirs:
            yield from walk(full, ancestors | {real})

    yield from walk(base, {base_real})


class _TreeIndex:
    """Lazy index of files under root, built only if a hint needs it."""

    def __init__(self, root: Path):
        self.root = root
        self._files: Optional[list[str]] = None

    @property
    def files(self) -> list[str]:
        if self._files is None:
            self._files = [p for p in iter_files(self.root) if p not in (MANIFEST_FILENAME, BACKUP_FILENAME)]
        return self._files

    def same_content(self, rel: str, art: Artifact) -> bool:
        """Could ``rel`` be the artifact's file? With a recorded digest this
        is a byte-for-byte check; without one (or for a volatile file),
        any file qualifies."""
        if art.sha256 is None or art.volatile:
            return True
        path = self.root / rel
        if not _within(self.root, path):
            return False
        try:
            if art.size_bytes is not None and path.stat().st_size != art.size_bytes:
                return False
            return sha256_file(path) == art.sha256
        except OSError:
            return False

    def find_by_suffix(self, parts: tuple[str, ...], art: Artifact) -> Optional[str]:
        """Longest trailing run of path components that exists under root
        (and matches the recorded digest, if any)."""
        for i in range(len(parts)):
            candidate = "/".join(parts[i:])
            if not candidate or not _within(self.root, self.root / candidate):
                continue
            if (self.root / candidate).is_file() and self.same_content(candidate, art):
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
    return PureWindowsPath(raw).is_absolute() or raw.startswith("/") or bool(PureWindowsPath(raw).drive)


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


def check_artifact(art: Artifact, root: Path, index: _TreeIndex, out: FileCheckResult) -> None:
    raw = art.path
    add = out.findings.append

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

    if _is_absolute(norm):
        target = Path(norm)
        try:
            is_file = target.is_file()
        except OSError:
            is_file = False
        inside = _within(root, target)
        parts = PurePosixPath(PureWindowsPath(norm).as_posix()).parts[1:]
        relocated = index.find_by_suffix(tuple(parts), art) if not is_file else None
        add(
            Finding(
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
        )
        if not inside:
            out.broken.add(art.id)
            if not is_file and relocated:
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
            return
        if not is_file and relocated:
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
            return
    else:
        target = root / norm
        if not _within(root, target):
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
            return

    if not target.exists():
        out.broken.add(art.id)
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
        return

    if not target.is_file():
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
        return

    if not _is_absolute(norm):
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

    if art.volatile:
        # Existence is the whole check: the bytes change on every rerun.
        out.readable_paths[art.id] = target
        out.ok_paths[art.id] = target
        return

    try:
        digest = sha256_file(target)
        size = target.stat().st_size
    except OSError as exc:
        out.broken.add(art.id)
        add(
            Finding(
                code=Code.missing_file,
                severity=Severity.error,
                subject=art.id,
                path=raw,
                message=f"Artifact {art.id!r} could not be read: {exc}",
            )
        )
        return

    out.readable_paths[art.id] = target
    if art.sha256 is None:
        add(
            Finding(
                code=Code.no_digest,
                severity=Severity.warning,
                subject=art.id,
                path=raw,
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
        add(
            Finding(
                code=Code.hash_mismatch,
                severity=Severity.error,
                subject=art.id,
                path=raw,
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
        return

    if art.size_bytes is not None and art.size_bytes != size:
        # Only reachable without a digest (a matching digest implies matching size).
        out.broken.add(art.id)
        add(
            Finding(
                code=Code.size_mismatch,
                severity=Severity.error,
                subject=art.id,
                path=raw,
                message=f"Artifact {art.id!r} size changed: {art.size_bytes} recorded, {size} on disk",
            )
        )
        return

    out.ok_paths[art.id] = target


def check_files(loaded: LoadedManifest, include_untracked: bool = False) -> FileCheckResult:
    root = loaded.root
    index = _TreeIndex(root)
    out = FileCheckResult()
    for art in loaded.manifest.artifacts:
        check_artifact(art, root, index, out)

    if include_untracked:
        tracked = {
            os.path.normcase(os.path.abspath(root / a.path.replace("\\", "/")))
            for a in loaded.manifest.artifacts
        }
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
