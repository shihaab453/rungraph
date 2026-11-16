"""Verification helpers for locator-less metric summaries (RE-10).

The adapter that produced a summary owns its format; core owns the decision
that a result is a summary shape, the bounded read, and the digest binding.
Nothing here writes, and nothing here parses a tool-specific format.
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path
from typing import Optional

from .adapters import Adapter, summary_claimants
from .models import Manifest, Result

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
    """One claiming adapter, or a fixed reason: ``absent`` or ``ambiguous``."""
    claimants = summary_claimants(manifest, result)
    if len(claimants) == 1:
        return claimants[0], "ok"
    if not claimants:
        return None, "absent"
    return None, "ambiguous"


def _state(exc: OSError) -> str:
    if exc.errno in (errno.ENOENT, errno.ENOTDIR):
        return "missing"
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


def read_bounded(path: Path, cap: int = MAX_METRIC_BYTES) -> tuple[Optional[bytes], str]:
    """Read at most ``cap`` bytes from a regular file, without following links.

    Returns ``(bytes, "ok")`` or ``(None, state)`` where state is ``missing``,
    ``not-regular``, ``over-cap`` or ``unreadable``. A FIFO cannot block the
    open and is refused as non-regular; a symlink is refused rather than
    followed."""
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOCTTY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
    )
    try:
        fd = os.open(os.fspath(path), flags)
    except OSError as exc:
        return None, _state(exc)
    except (ValueError, UnicodeError):
        return None, "missing"
    try:
        try:
            info = os.fstat(fd)
        except OSError as exc:
            return None, _state(exc)
        if not stat.S_ISREG(info.st_mode):
            return None, "not-regular"
        buffer = bytearray()
        while len(buffer) <= cap:
            chunk = os.read(fd, min(_READ_CHUNK, cap + 1 - len(buffer)))
            if not chunk:
                break
            buffer.extend(chunk)
        if len(buffer) > cap:
            return None, "over-cap"
        return bytes(buffer), "ok"
    except OSError as exc:
        return None, _state(exc)
    finally:
        os.close(fd)
