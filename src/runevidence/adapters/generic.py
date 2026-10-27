"""The built-in fallback adapter: works on any folder.

* Every regular file becomes an Artifact with its sha256 and size.
* Each immediate subfolder of ``runs/`` (if present) becomes a Run, and files
  under it are marked as produced by that run.
* Nothing else is guessed: results and evidence are left for you to add,
  because only you know which numbers and claims matter.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

from ..checks.files import _within, iter_files
from ..hashing import sha256_file
from ..models import Artifact, Manifest, Project, Run, is_own_file
from .base import Adapter, AdapterError, Discovery

RUNS_DIR = "runs"


def slug(text: str) -> str:
    """Turn a path or name into a manifest-ID-shaped token.

    Callers that add a valid prefix (``run-``, ``artifact-``) can use this as
    is; both names and paths kept as IDs need :func:`valid_slug` instead.
    """
    out = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.")
    return out or "item"


def valid_slug(text: str) -> str:
    """Like :func:`slug`, but always starts with an ASCII alphanumeric.

    Manifest IDs must match ``ID_PATTERN``; a leading ``_`` (``_summary.json``,
    ``__init__.py``) is the only shape ``slug`` leaves unusable. Prefix only
    those, so every other generated ID stays byte-for-byte unchanged.
    """
    out = slug(text)
    return out if re.match(r"[A-Za-z0-9]", out) else f"item-{out}"


class GenericAdapter(Adapter):
    name = "generic"
    description = "Any folder: hash every file, treat runs/<name>/ subfolders as runs."

    def discover(self, root: Path) -> Discovery:
        files = [p for p in iter_files(root) if not is_own_file(p)]
        runs = sorted({PurePosixPath(p).parts[1] for p in files if _run_of(p)})
        return Discovery(root=root, files=files, details={"runs": runs})

    def extract(self, root: Path, discovery: Discovery, *, project_id: str, project_name: str) -> Manifest:
        # Names can arrive in any order when a discovery list is supplied;
        # allocate in sorted order so each name always gets the same ID.
        run_names = sorted(set(discovery.details.get("runs", [])))
        used: set[str] = {project_id}
        run_ids: dict[str, str] = {}
        runs = []
        for name in run_names:
            run_ids[name] = _unique(f"run-{slug(name)}", used)
            runs.append(Run(id=run_ids[name], name=name))

        artifacts = []
        for rel in discovery.files:
            path = root / rel
            if not _within(root, path):
                # A discovery list can be supplied directly; never hash bytes
                # that resolve outside the folder being extracted.
                continue
            run = _run_of(rel)
            if run is not None and run not in run_ids:
                raise AdapterError(f"Discovery omitted run folder {run!r}; rediscover the experiment folder.")
            art_id = _unique(valid_slug(rel), used)
            artifacts.append(
                Artifact(
                    id=art_id,
                    path=rel,
                    sha256=sha256_file(path),
                    size_bytes=path.stat().st_size,
                    run_id=run_ids[run] if run is not None else None,
                )
            )
        return Manifest(
            project=Project(id=project_id, name=project_name),
            runs=runs,
            artifacts=artifacts,
        )


def _run_of(rel: str) -> str | None:
    parts = PurePosixPath(rel).parts
    if len(parts) >= 3 and parts[0] == RUNS_DIR:
        return parts[1]
    return None


def _unique(base: str, used: set[str]) -> str:
    candidate, n = base, 2
    while candidate in used:
        candidate, n = f"{base}-{n}", n + 1
    used.add(candidate)
    return candidate
