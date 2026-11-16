"""Verification helpers for locator-less metric summaries (RE-10).

The adapter that produced a summary owns its format; core owns the decision
that a result is a summary shape, the bounded read, and the digest binding.
Nothing here writes, and nothing here parses a tool-specific format.
"""

from __future__ import annotations

import errno
import math
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Optional

from .adapters import Adapter, summary_claimants
from .models import Manifest, Result

#: Schemas with no locator concept, where a locator-less result is otherwise
#: skipped silently. Later schemas keep their frozen unverified warning.
SUMMARY_SCHEMAS = ("0.1", "0.2")

#: A metric history is read whole into memory, so it gets the same ceiling as
#: the locator snapshot path. One byte over is refused.
MAX_METRIC_BYTES = 50 * 1024 * 1024

_READ_CHUNK = 1024 * 1024


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _normalized(path: str) -> str:
    out = path.replace("\\", "/")
    while out.startswith("./"):
        out = out[2:]
    return out


def summary_shape(manifest: Manifest, result: Result) -> bool:
    """True when ``result`` follows the delivered MLflow summary convention.

    This is the diagnostic gate: a shape match that no installed adapter
    verifies produces an unverified warning. It never authorizes a rewrite by
    itself; ``summary_claimants`` must still claim the result first."""
    if result.locator is not None or result.artifact_id is None:
        return False
    run = next((item for item in manifest.runs if item.id == result.run_id), None)
    artifact = next((item for item in manifest.artifacts if item.id == result.artifact_id), None)
    if run is None or artifact is None:
        return False
    if not isinstance(run.metadata.get("mlflow"), dict):
        return False
    if not _is_int(result.metadata.get("step")) or not _is_int(result.metadata.get("timestamp_ms")):
        return False
    if artifact.kind != "metrics" or artifact.run_id != result.run_id:
        return False
    return _normalized(artifact.path).endswith("/metrics/" + result.name)


def summary_owner(manifest: Manifest, result: Result) -> tuple[Optional[Adapter], str]:
    """One claiming adapter, or a fixed reason: ``absent``, ``broken`` or
    ``ambiguous``."""
    claimants, reason = summary_claimants(manifest, result)
    if reason == "ok" and len(claimants) == 1:
        return claimants[0], "ok"
    return None, reason


def valid_summary(summary: object) -> bool:
    """True when an adapter returned a finite numeric triple.

    A third-party plugin can return anything, including an object whose
    attribute raises; a malformed value must become a warning or a refusal,
    never a written manifest or a traceback."""
    try:
        value = getattr(summary, "value", None)
        step = getattr(summary, "step", None)
        timestamp = getattr(summary, "timestamp", None)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if isinstance(step, bool) or not isinstance(step, int):
            return False
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            return False
        return bool(math.isfinite(value))
    except Exception:
        return False


def _state(exc: OSError) -> str:
    if exc.errno == errno.ENOENT:
        return "missing"
    if exc.errno == errno.ENOTDIR:
        return "not-regular"
    if exc.errno in (errno.ELOOP, getattr(errno, "EMLINK", errno.ELOOP)):
        # O_NOFOLLOW reports a symlink as ELOOP on Linux.
        return "not-regular"
    return "unreadable"


def describe_state(state: str) -> str:
    """Fixed wording for a bounded-read refusal, used in findings and errors."""
    if state == "not-regular":
        return "a symlink or non-regular file"
    if state == "over-cap":
        return f"over the {MAX_METRIC_BYTES}-byte cap"
    return state


_LEAF_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_NOCTTY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_BINARY", 0)
)
_DIR_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_DIR_FD = bool(getattr(os, "O_DIRECTORY", 0)) and os.open in getattr(os, "supports_dir_fd", set())


def _consume(fd: int, cap: int) -> tuple[Optional[bytes], str]:
    try:
        info = os.fstat(fd)
    except OSError as exc:
        return None, _state(exc)
    if not stat.S_ISREG(info.st_mode):
        return None, "not-regular"
    buffer = bytearray()
    try:
        while len(buffer) <= cap:
            chunk = os.read(fd, min(_READ_CHUNK, cap + 1 - len(buffer)))
            if not chunk:
                break
            buffer.extend(chunk)
    except OSError as exc:
        return None, _state(exc)
    if len(buffer) > cap:
        return None, "over-cap"
    return bytes(buffer), "ok"


_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)


def _is_link_or_reparse(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    # Windows directory junctions are reparse points, not symlinks.
    return bool(_REPARSE_POINT and getattr(info, "st_file_attributes", 0) & _REPARSE_POINT)


def _read_direct(root: Path, parts: tuple[str, ...], cap: int) -> tuple[Optional[bytes], str]:
    """Platforms without dir_fd: walk every component with lstat.

    Nothing here is a descriptor walk, so it cannot rule out a replacement
    between the check and the open; it does refuse an existing symlink or
    reparse point in any component, which is the best available on these
    hosts."""
    current = root
    for part in parts:
        current = current / part
        try:
            info = os.lstat(os.fspath(current))
        except OSError as exc:
            return None, _state(exc)
        except (ValueError, UnicodeError):
            return None, "missing"
        if _is_link_or_reparse(info):
            return None, "not-regular"
        if part != parts[-1]:
            if not stat.S_ISDIR(info.st_mode):
                return None, "not-regular"
        elif not stat.S_ISREG(info.st_mode):
            # Windows cannot open a directory O_RDONLY, so classify it before
            # the open rather than reporting the permission error as unreadable.
            return None, "not-regular"
    try:
        fd = os.open(os.fspath(root.joinpath(*parts)), _LEAF_FLAGS)
    except OSError as exc:
        return None, _state(exc)
    except (ValueError, UnicodeError):
        return None, "missing"
    try:
        return _consume(fd, cap)
    finally:
        os.close(fd)


def read_bounded(root: Path, rel_path: str, cap: int = MAX_METRIC_BYTES) -> tuple[Optional[bytes], str]:
    """Read at most ``cap`` bytes from ``root/rel_path`` without following links.

    Every parent component is opened relative to its verified parent with
    ``O_NOFOLLOW|O_DIRECTORY``, so an in-root symlinked parent cannot redirect
    the read. The leaf is opened with ``O_NOFOLLOW|O_NONBLOCK``, so a symlink is
    refused and a FIFO cannot block. Returns ``(bytes, "ok")`` or
    ``(None, state)`` where state is ``missing``, ``not-regular``, ``over-cap``
    or ``unreadable``."""
    normalized = rel_path.replace("\\", "/")
    path = PurePosixPath(normalized)
    parts = path.parts
    if path.is_absolute() or not parts or any(part in ("", ".", "..") for part in parts):
        return None, "not-regular"
    if not _DIR_FD:
        return _read_direct(root, parts, cap)
    fds: list[int] = []
    leaf = -1
    try:
        fd = os.open(os.fspath(root), _DIR_FLAGS)
        fds.append(fd)
        for part in parts[:-1]:
            fd = os.open(part, _DIR_FLAGS, dir_fd=fd)
            fds.append(fd)
        leaf = os.open(parts[-1], _LEAF_FLAGS, dir_fd=fd)
    except OSError as exc:
        return None, _state(exc)
    except (ValueError, UnicodeError):
        return None, "missing"
    finally:
        for opened in fds:
            os.close(opened)
    try:
        return _consume(leaf, cap)
    finally:
        os.close(leaf)
