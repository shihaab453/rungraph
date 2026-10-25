"""Changing a manifest on purpose.

* ``rehash`` accepts an intended change: it re-records the digest and size of
  chosen files, and re-reads result values that come from those files.
* ``add`` tracks new files without regenerating the whole manifest.

Edits are applied to the manifest's raw JSON rather than to a re-serialized
model, so everything the user wrote by hand (results, claims, links, field
order, fields left out) stays as it was. Nothing is written unless every
requested change can be made.
"""

from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from .adapters.generic import _unique, slug
from .checks.files import IGNORED_DIRS, _is_absolute, _within
from .checks.graph import check_graph
from .checks.results import _MISSING, MAX_JSON_BYTES, resolve_pointer, values_match
from .findings import Code
from .hashing import sha256_file
from .manifest import LoadedManifest, artifact_to_dict
from .models import BACKUP_FILENAME, MANIFEST_FILENAME, Artifact, Manifest

GLOB_CHARS = set("*?[")


class EditError(Exception):
    """The edit cannot be made as asked; nothing was written."""


@dataclass
class FileChange:
    id: str
    path: str
    old_sha256: Optional[str]
    new_sha256: Optional[str]
    old_size: Optional[int]
    new_size: Optional[int]
    was_volatile: bool
    volatile: bool

    @property
    def changed(self) -> bool:
        return (self.old_sha256, self.old_size, self.was_volatile) != (
            self.new_sha256,
            self.new_size,
            self.volatile,
        )


@dataclass
class ValueChange:
    result_id: str
    name: str
    old: Any
    new: Any


@dataclass
class RehashPlan:
    files: list[FileChange] = field(default_factory=list)
    values: list[ValueChange] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # results that could not be re-read
    claims: list[str] = field(default_factory=list)  # claims resting on what changed

    @property
    def changed_files(self) -> list[FileChange]:
        return [f for f in self.files if f.changed]


@dataclass
class AddPlan:
    added: list[dict] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # already tracked (folder expansion only)
    inferred_runs: dict[str, str] = field(default_factory=dict)  # artifact id -> run id


# --- shared helpers --------------------------------------------------------


def _norm(raw: str) -> str:
    out = raw.replace("\\", "/")
    while out.startswith("./"):
        out = out[2:]
    return out


def _on_disk(root: Path, art: Artifact) -> Path:
    norm = _norm(art.path)
    return Path(norm) if _is_absolute(norm) else root / norm


def _key(path: Path) -> str:
    """Comparable form of a path: absolute, and case-folded where the
    filesystem ignores case."""
    return os.path.normcase(os.path.abspath(path))


def glob_match(path: str, pattern: str) -> bool:
    """Shell-style match on a manifest path. ``*`` also crosses folders, so
    ``*.log`` matches logs at any depth."""
    return fnmatch.fnmatchcase(_norm(path), _norm(pattern))


# --- rehash ----------------------------------------------------------------


def select_artifacts(loaded: LoadedManifest, targets: list[str], cwd: Optional[Path] = None) -> list[int]:
    """Indexes of the artifacts the targets name. A target may be an artifact
    ID, a run ID (every file the run produced), a file or folder path (as
    written in the manifest, or on disk relative to the current folder), or a
    glob over manifest paths such as ``runs/*/train.log``."""
    manifest, root = loaded.manifest, loaded.root
    cwd = cwd or Path.cwd()
    runs = {r.id for r in manifest.runs}
    keys = [_key(_on_disk(root, a)) for a in manifest.artifacts]
    chosen: dict[int, None] = {}
    unmatched: list[str] = []
    for target in targets:
        norm = _norm(target)
        folder = norm.rstrip("/") + "/"
        on_disk = _key(cwd / target)
        under_on_disk = on_disk.rstrip(os.sep) + os.sep
        hits = [
            i
            for i, a in enumerate(manifest.artifacts)
            if target == a.id
            or (target in runs and a.run_id == target)
            or norm == _norm(a.path)
            or _norm(a.path).startswith(folder)
            or keys[i] == on_disk
            or keys[i].startswith(under_on_disk)
            or (bool(GLOB_CHARS & set(norm)) and glob_match(a.path, norm))
        ]
        if not hits:
            unmatched.append(target)
        chosen.update(dict.fromkeys(hits))
    if unmatched:
        raise EditError(
            "No artifact matches " + ", ".join(repr(t) for t in unmatched)
            + ". Use an artifact ID, a run ID, a tracked file or folder, or a glob like 'runs/*/train.log'."
        )
    return sorted(chosen)


def plan_rehash(
    loaded: LoadedManifest,
    raw: dict,
    indexes: list[int],
    volatile: Optional[bool] = None,
) -> RehashPlan:
    """Re-record the chosen artifacts in ``raw`` (edited in place).

    ``volatile`` True marks them existence-only (digest and size cleared),
    False makes them hash-checked again, None keeps each one as it is."""
    manifest, root = loaded.manifest, loaded.root
    plan = RehashPlan()
    unreadable: list[str] = []
    for i in indexes:
        art = manifest.artifacts[i]
        entry = raw["artifacts"][i]
        target = _on_disk(root, art)
        make_volatile = art.volatile if volatile is None else volatile
        try:
            if not target.is_file():
                raise FileNotFoundError("not a file" if target.exists() else "does not exist")
            digest = None if make_volatile else sha256_file(target)
            size = None if make_volatile else target.stat().st_size
        except OSError as exc:
            unreadable.append(f"{art.id!r} ({art.path}): {exc.strerror or exc}")
            continue
        plan.files.append(
            FileChange(art.id, art.path, art.sha256, digest, art.size_bytes, size, art.volatile, make_volatile)
        )
        if make_volatile:
            # A digest that is never checked would only mislead a reader.
            for key in ("sha256", "size_bytes"):
                if key in entry:
                    entry[key] = None
            raw["artifacts"][i] = _set_volatile(entry)
        else:
            entry.pop("volatile", None)
            entry["sha256"] = digest
            entry["size_bytes"] = size
    if unreadable:
        raise EditError(
            "Cannot re-record files that are missing or unreadable:\n  "
            + "\n  ".join(unreadable)
            + "\nRestore them, or remove their entries from the manifest, then try again."
        )

    # Only a file whose bytes changed can change a value. A recorded value
    # that disagrees with an unchanged file is a typo for validate to report,
    # not something to accept.
    new_bytes = {f.id for f in plan.files if f.new_sha256 and f.new_sha256 != f.old_sha256}
    volatile_ids = {f.id for f in plan.files if f.volatile}
    for res in manifest.results:
        if res.locator is not None and res.artifact_id in volatile_ids:
            plan.notes.append(
                f"{res.id}: source is volatile, so its value was left as is; "
                "use --no-volatile to record its current digest and result value"
            )
    _reread_values(loaded, raw, new_bytes, plan)
    accepted = {f.id for f in plan.changed_files} | {v.result_id for v in plan.values}
    if accepted:
        plan.claims = sorted(
            {f.subject for f in check_graph(manifest, accepted) if f.code is Code.claim_at_risk and f.subject}
        )
    Manifest.model_validate(raw)  # our own edit must leave a valid manifest
    return plan


def _set_volatile(entry: dict) -> dict:
    """Mark an artifact volatile, placing the flag after size_bytes like a
    freshly written manifest does, so it reads next to what it overrides."""
    if "volatile" in entry or "size_bytes" not in entry:
        return {**entry, "volatile": True}
    out: dict = {}
    for key, value in entry.items():
        out[key] = value
        if key == "size_bytes":
            out["volatile"] = True
    return out


def _reread_values(loaded: LoadedManifest, raw: dict, artifact_ids: set[str], plan: RehashPlan) -> None:
    """Results read from a re-recorded file take the value now in the file.
    Without this, accepting a new metrics file would still fail on its old
    numbers."""
    manifest = loaded.manifest
    by_id = {a.id: a for a in manifest.artifacts}
    docs: dict[str, Any] = {}
    for j, res in enumerate(manifest.results):
        if res.locator is None or res.artifact_id not in artifact_ids:
            continue
        art = by_id[res.artifact_id]
        path = _on_disk(loaded.root, art)
        if path.suffix.lower() != ".json":
            plan.notes.append(f"{res.id}: {art.path} is not a .json file, so its value was not re-read")
            continue
        if art.id not in docs:
            try:
                if path.stat().st_size > MAX_JSON_BYTES:
                    raise ValueError(f"file larger than {MAX_JSON_BYTES} bytes")
                docs[art.id] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                docs[art.id] = exc
        doc = docs[art.id]
        if isinstance(doc, Exception):
            plan.notes.append(f"{res.id}: could not parse {art.path} as JSON ({doc}); value left as is")
            continue
        actual = resolve_pointer(doc, res.locator)
        if actual is _MISSING:
            plan.notes.append(f"{res.id}: {res.locator} is not in {art.path} any more; value left as is")
            continue
        if isinstance(actual, (dict, list)):
            plan.notes.append(f"{res.id}: {res.locator} in {art.path} is not a single value; value left as is")
            continue
        if not values_match(res.value, actual):
            raw["results"][j]["value"] = actual
            plan.values.append(ValueChange(res.id, res.name, res.value, actual))


# --- add -------------------------------------------------------------------


def plan_add(
    loaded: LoadedManifest,
    raw: dict,
    paths: list[Path],
    *,
    art_id: Optional[str] = None,
    kind: Optional[str] = None,
    run_id: Optional[str] = None,
    infer_run: bool = True,
    volatile: bool = False,
    cwd: Optional[Path] = None,
) -> AddPlan:
    """Append new artifacts for ``paths`` to ``raw`` (edited in place).

    A folder adds every file inside it that is not tracked yet. Without
    ``run_id``, a new file joins a run only if the tracked files nearest to it
    (same folder, else the closest parent folder holding any) all belong to
    that one run."""
    manifest, root = loaded.manifest, loaded.root
    cwd = cwd or Path.cwd()
    tracked = {_key(_on_disk(root, a)): a.id for a in manifest.artifacts}
    used = (
        {manifest.project.id}
        | {r.id for r in manifest.runs}
        | {a.id for a in manifest.artifacts}
        | {r.id for r in manifest.results}
        | {e.id for e in manifest.evidence}
    )
    if run_id is not None and run_id not in {r.id for r in manifest.runs}:
        raise EditError(f"No run {run_id!r} in the manifest. Runs: {', '.join(r.id for r in manifest.runs) or 'none'}.")
    if art_id is not None and art_id in used:
        raise EditError(f"ID {art_id!r} is already used in the manifest; pick another with --id.")

    plan = AddPlan()
    rels: list[str] = []
    for given in paths:
        full = (cwd / given).resolve()
        if not full.exists():
            raise EditError(f"{str(given)!r} does not exist")
        if not _within(root, full):
            raise EditError(f"{str(given)!r} is outside the experiment folder {root}")
        if full.is_dir():
            for dirpath, dirnames, filenames in os.walk(full):
                dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
                for name in sorted(filenames):
                    rel = (Path(dirpath) / name).relative_to(root).as_posix()
                    if rel in (MANIFEST_FILENAME, BACKUP_FILENAME):
                        continue
                    if _key(root / rel) in tracked:
                        plan.skipped.append(rel)
                    elif rel not in rels:
                        rels.append(rel)
            continue
        rel = full.relative_to(root).as_posix()
        if rel in (MANIFEST_FILENAME, BACKUP_FILENAME):
            raise EditError(f"{rel} is RunEvidence's own file and cannot be tracked")
        if _key(full) in tracked:
            raise EditError(
                f"{rel} is already tracked as {tracked[_key(full)]!r}. "
                f"To accept a change to it, use: runevidence rehash <folder> {tracked[_key(full)]}"
            )
        if rel not in rels:
            rels.append(rel)

    if art_id is not None and len(rels) != 1:
        raise EditError(f"--id needs exactly one file, but {len(rels)} would be added")

    for rel in rels:
        path = root / rel
        new_id = art_id or _unique(slug(rel), used)
        used.add(new_id)
        run = run_id
        if run is None and infer_run:
            run = _nearest_run(manifest, rel)
            if run:
                plan.inferred_runs[new_id] = run
        art = Artifact(
            id=new_id,
            path=rel,
            sha256=None if volatile else sha256_file(path),
            size_bytes=None if volatile else path.stat().st_size,
            volatile=volatile,
            kind=kind,
            run_id=run,
        )
        plan.added.append(artifact_to_dict(art))
    raw.setdefault("artifacts", []).extend(plan.added)
    Manifest.model_validate(raw)  # our own edit must leave a valid manifest
    return plan


def _nearest_run(manifest: Manifest, rel: str) -> Optional[str]:
    folders: dict[str, set[Optional[str]]] = {}
    for a in manifest.artifacts:
        parent = str(PurePosixPath(_norm(a.path)).parent)
        folders.setdefault(parent, set()).add(a.run_id)
    for folder in PurePosixPath(rel).parents:
        owners = folders.get(str(folder))
        if owners:
            return next(iter(owners)) if len(owners) == 1 else None
    return None
