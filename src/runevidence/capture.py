"""Capture recorded source revision and environment for one run."""

from __future__ import annotations

import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .manifest import LoadedManifest, load_raw, save_raw, validate_manifest

MIN_SCHEMA = "0.2"


class CaptureError(ValueError):
    """The capture cannot be recorded; nothing was written."""


@dataclass(frozen=True)
class CaptureResult:
    run_id: str
    source_revision: str
    environment: dict
    dry_run: bool
    written: bool


def _git(folder: Path) -> tuple[str, bool]:
    try:
        head = subprocess.run(
            ["git", "-C", str(folder), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        status = subprocess.run(
            ["git", "-C", str(folder), "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CaptureError(f"git is not available: {exc}") from None
    if head.returncode != 0 or not head.stdout.strip():
        raise CaptureError("the folder is not a git repository with a commit")
    return head.stdout.strip(), bool(status.stdout.strip())


def capture(loaded: LoadedManifest, run_id: str, *, dry_run: bool = False) -> CaptureResult:
    manifest = loaded.manifest
    if manifest.schema_version == "0.1":
        raise CaptureError("capture requires schema 0.2 or later; migrate first")
    if run_id not in {run.id for run in manifest.runs}:
        raise CaptureError(f"run {run_id!r} does not exist")
    commit, dirty = _git(loaded.path.parent)
    environment = {
        "python": platform.python_version(),
        "platform": sys.platform,
        "machine": platform.machine(),
        "git_dirty": dirty,
    }
    raw = load_raw(loaded)
    run = next(item for item in raw["runs"] if item["id"] == run_id)
    conditions = run.setdefault("conditions", {})
    previous = conditions.get("environment")
    if isinstance(previous, dict):
        environment = {**previous, **environment}
    conditions["source_revision"] = commit
    conditions["environment"] = environment
    try:
        validate_manifest(raw)
    except Exception as exc:
        raise CaptureError(f"the edited manifest is not valid: {exc}") from None
    if not dry_run:
        save_raw(raw, loaded.path, expected_bytes=loaded.source_bytes)
    return CaptureResult(run_id, commit, environment, dry_run, not dry_run)
