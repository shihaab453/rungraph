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

from ..checks.files import iter_files
from ..hashing import sha256_file
from ..models import BACKUP_FILENAME, MANIFEST_FILENAME, Artifact, Manifest, Project, Run
from .base import Adapter, Discovery

RUNS_DIR = "runs"


def slug(text: str) -> str:
    """Turn a path or name into a valid manifest ID."""
    out = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.")
    return out or "item"


class GenericAdapter(Adapter):
    name = "generic"
    description = "Any folder: hash every file, treat runs/<name>/ subfolders as runs."

    def discover(self, root: Path) -> Discovery:
        files = [p for p in iter_files(root) if p not in (MANIFEST_FILENAME, BACKUP_FILENAME)]
        runs = sorted({PurePosixPath(p).parts[1] for p in files if _run_of(p)})
        return Discovery(root=root, files=files, details={"runs": runs})

    def extract(self, root: Path, discovery: Discovery, *, project_id: str, project_name: str) -> Manifest:
        runs = [Run(id=f"run-{slug(name)}", name=name) for name in discovery.details.get("runs", [])]
        artifacts = []
        used: set[str] = {r.id for r in runs} | {project_id}
        for rel in discovery.files:
            run = _run_of(rel)
            art_id = _unique(slug(rel), used)
            path = root / rel
            artifacts.append(
                Artifact(
                    id=art_id,
                    path=rel,
                    sha256=sha256_file(path),
                    size_bytes=path.stat().st_size,
                    run_id=f"run-{slug(run)}" if run else None,
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
