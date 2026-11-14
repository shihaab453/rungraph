"""Proposals for recording a directory artifact.

Internal and development only: no command calls this, and nothing here writes
a manifest, a backup or any other file. Each function returns a proposal that
holds a changed deep copy of the caller's raw manifest JSON. The caller's
``raw`` is never modified, and a failure returns no proposal at all.
An add also captures ``kind`` and ``run_id`` as separate frozen scalar fields
``add_kind`` and ``add_run_id`` after Artifact validation. Rehash sets both to
None, since it introduces no new intent. These mandatory fields bind ordinary
mutable-field edits at apply time; they are not proof against object forgery.

* ``propose_add_directory`` records one folder as a single directory artifact.
  It never expands the folder into file records and never changes an existing
  artifact.
* ``propose_rehash_directory`` measures an already recorded directory again
  and changes only its ``sha256`` and ``size_bytes``.

Every check that needs no disk access runs first. The root path must pass the
v1 member name profile and bounds before anything is opened, and the folder is
then read only by ``directory_scanner.scan_directory`` (native, opened relative
to parent handles, links never followed). There is no path-based fallback.
Like the scanner, this assumes a quiet folder with one cooperating writer; it
is not protection against a hostile process.

No overlap model has been approved yet, so the rules are strict:

* another artifact at the same root, or one differing only in case, is refused;
* a directory artifact above or below the root is refused;
* a file artifact above the root is refused, since a file cannot hold a folder;
* a file artifact inside the root is allowed and listed in
  ``overlapping_files``. It keeps its own file checks, so its bytes are then
  vouched for twice: by its file record and by the directory digest.

A JSON ``locator`` cannot be read from a directory, so a result that has one
and names the directory is refused. Other linked results and the claims citing
them are listed in the proposal, never re-read or rewritten.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from pydantic import ValidationError

from . import directory_scanner
from .directory_hashing import DEFAULT_LIMITS, DirectoryDigest, DirectoryRecordError, check_member_path
from .directory_scanner import DirectoryScanError
from .edit import EditError, _key, _on_disk
from .manifest import LoadedManifest, artifact_to_dict, dump_raw, validate_manifest
from .models import DIRECTORY_DIGEST_FORMAT, Artifact, Manifest


class DirectoryAuthoringError(EditError):
    """No proposal can be made; nothing was changed. ``reason`` is one of:

    * ``invalid-manifest``          ``raw`` is not a valid manifest
    * ``invalid-path``              the root is not a strict relative v1 path
    * ``limit-exceeded``            the root is longer or deeper than v1 allows
    * ``invalid-artifact``          the new entry would not be a valid artifact
    * ``duplicate-id``              the ID is already used, or is not unique
    * ``unknown-run``               ``run_id`` names no run in the manifest
    * ``volatile-unsupported``      volatile directories cannot be verified yet
    * ``path-collision``            another artifact has the same root, ignoring case
    * ``overlap``                   a directory above or below, or a file above
    * ``not-found``                 no artifact has that ID
    * ``not-a-directory-artifact``  the selected artifact is a file
    * ``stale``                     ``raw`` and ``loaded`` disagree about the artifact
    * ``locator-unsupported``       a result reads a JSON locator from the directory
    * ``scan-failed``               the scanner refused; see ``scan_reason``

    ``subjects`` names the IDs involved, if any."""

    def __init__(
        self, reason: str, message: str, *, subjects: tuple[str, ...] = (), scan_reason: Optional[str] = None
    ):
        self.reason = reason
        self.subjects = tuple(subjects)
        self.scan_reason = scan_reason
        super().__init__(message)


@dataclass(frozen=True)
class DirectoryProposal:
    operation: Literal["add", "rehash"]
    artifact_id: str
    path: str
    add_kind: Optional[str]  # creation-time add intent; None for rehash
    add_run_id: Optional[str]  # separate from mutable entry/raw; None for rehash
    entry: dict  # the proposed artifact entry, a copy of the one in ``raw``
    raw: dict  # deep copy of the caller's manifest JSON with the change applied
    old_sha256: Optional[str]  # None for an add
    new_sha256: str
    old_size: Optional[int]
    new_size: int
    members: int  # records the scan found, excluding the root
    changed: bool  # False only for a rehash that found the same digest and size
    linked_results: tuple[str, ...]  # results naming the artifact; values not re-read
    claims: tuple[str, ...]  # claims citing the artifact or those results
    overlapping_files: tuple[str, ...]  # file artifacts inside the root
    notes: tuple[str, ...]


def propose_add_directory(
    loaded: LoadedManifest,
    raw: dict,
    *,
    path: str,
    artifact_id: str,
    run_id: Optional[str] = None,
    kind: Optional[str] = None,
    volatile: bool = False,
) -> DirectoryProposal:
    """Propose ``raw`` plus one directory artifact for the folder ``path``.

    ``path`` is relative to the manifest's folder, with forward slashes.
    Raises DirectoryAuthoringError."""
    proposed, current = _validated_copy(raw)
    parts = _root_parts(path)
    if volatile:
        raise DirectoryAuthoringError(
            "volatile-unsupported", "A volatile directory cannot be recorded: there is no safe way to verify one yet."
        )
    try:
        Artifact(**_directory_artifact_fields(artifact_id, path, kind=kind, run_id=run_id))
    except ValidationError as exc:
        raise DirectoryAuthoringError("invalid-artifact", f"Invalid directory artifact: {exc}") from None
    if artifact_id in set(_ids(current)) | set(_ids(loaded.manifest)):
        raise DirectoryAuthoringError(
            "duplicate-id", f"ID {artifact_id!r} is already used in the manifest.", subjects=(artifact_id,)
        )
    if run_id is not None and run_id not in {r.id for r in current.runs}:
        raise DirectoryAuthoringError(
            "unknown-run",
            f"No run {run_id!r} in the manifest. Runs: {', '.join(r.id for r in current.runs) or 'none'}.",
            subjects=(run_id,),
        )
    overlapping = _overlaps(loaded, [current, loaded.manifest], parts, path)
    linked, claims = _linkage(current, artifact_id)

    digest = _measure(loaded, parts, path)
    art = Artifact(
        **_directory_artifact_fields(
            artifact_id, path, kind=kind, run_id=run_id,
            sha256=digest.sha256, size_bytes=digest.size_bytes,
        )
    )
    entry = artifact_to_dict(art, schema_version=loaded.manifest.schema_version)
    proposed.setdefault("artifacts", []).append(entry)
    return _finish("add", proposed, entry, None, None, digest, True, linked, claims, overlapping,
                   add_kind=art.kind, add_run_id=art.run_id)


def propose_rehash_directory(loaded: LoadedManifest, raw: dict, *, artifact_id: str) -> DirectoryProposal:
    """Propose ``raw`` with the directory ``artifact_id`` measured again.

    Only that entry's ``sha256`` and ``size_bytes`` can change, and only when
    the new scan differs. Raises DirectoryAuthoringError."""
    proposed, current = _validated_copy(raw)
    art = _one_artifact(current, artifact_id, "not-found", "duplicate-id")
    before = _one_artifact(loaded.manifest, artifact_id, "stale", "stale")
    if art.artifact_type != "directory":
        raise DirectoryAuthoringError(
            "not-a-directory-artifact",
            f"Artifact {artifact_id!r} is a file; re-record files with runevidence rehash.",
            subjects=(artifact_id,),
        )
    if (before.artifact_type, before.path) != (art.artifact_type, art.path):
        raise DirectoryAuthoringError(
            "stale", f"Artifact {artifact_id!r} no longer has its loaded path {before.path!r}.", subjects=(artifact_id,)
        )
    if art.volatile:
        raise DirectoryAuthoringError(
            "volatile-unsupported",
            f"Artifact {artifact_id!r} is a volatile directory; there is no safe way to verify one yet.",
            subjects=(artifact_id,),
        )
    parts = _root_parts(art.path)
    if _ids(current).count(artifact_id) != 1:
        raise DirectoryAuthoringError(
            "duplicate-id", f"ID {artifact_id!r} is used by more than one entry.", subjects=(artifact_id,)
        )
    overlapping = _overlaps(loaded, [current, loaded.manifest], parts, art.path, own_id=artifact_id)
    linked, claims = _linkage(current, artifact_id)

    digest = _measure(loaded, parts, art.path)
    entry = next(e for e in proposed["artifacts"] if e["id"] == artifact_id)
    changed = (art.sha256, art.size_bytes) != (digest.sha256, digest.size_bytes)
    if changed:
        entry["sha256"] = digest.sha256
        entry["size_bytes"] = digest.size_bytes
    return _finish("rehash", proposed, entry, art.sha256, art.size_bytes, digest, changed, linked, claims, overlapping,
                   add_kind=None, add_run_id=None)


# --- helpers ---------------------------------------------------------------


def _directory_artifact_fields(artifact_id: str, path: str, *, kind: Optional[str],
                                run_id: Optional[str], **extra) -> dict:
    """Artifact arguments that do not mark absent optional values as explicit."""
    fields = {
        "id": artifact_id,
        "path": path,
        "artifact_type": "directory",
        "digest_format": DIRECTORY_DIGEST_FORMAT,
        **extra,
    }
    if kind is not None:
        fields["kind"] = kind
    if run_id is not None:
        fields["run_id"] = run_id
    return fields


def _validated_copy(raw: dict) -> tuple[dict, Manifest]:
    """The copy a proposal edits, and the manifest it currently describes."""
    if not isinstance(raw, dict):
        raise DirectoryAuthoringError("invalid-manifest", f"Manifest JSON must be an object, got {type(raw).__name__}")
    proposed = copy.deepcopy(raw)
    try:
        return proposed, validate_manifest(proposed)
    except ValidationError as exc:
        raise DirectoryAuthoringError("invalid-manifest", f"Manifest JSON is not a valid manifest: {exc}") from None


def _root_parts(path: object) -> list[str]:
    """Components of a strict relative v1 root, checked before any disk access."""
    try:
        raw = check_member_path(path)
    except DirectoryRecordError as exc:
        raise DirectoryAuthoringError("invalid-path", f"Directory path is not a strict relative portable path: {exc}") from None
    parts = path.split("/")
    if len(raw) > DEFAULT_LIMITS.path_bytes or len(parts) > DEFAULT_LIMITS.depth:
        raise DirectoryAuthoringError(
            "limit-exceeded",
            f"Directory path exceeds {DEFAULT_LIMITS.path_bytes} bytes or {DEFAULT_LIMITS.depth} components",
        )
    return parts


def _ids(manifest: Manifest) -> list[str]:
    return [
        manifest.project.id,
        *(r.id for r in manifest.runs),
        *(a.id for a in manifest.artifacts),
        *(r.id for r in manifest.results),
        *(e.id for e in manifest.evidence),
    ]


def _one_artifact(manifest: Manifest, artifact_id: str, missing: str, ambiguous: str) -> Artifact:
    hits = [a for a in manifest.artifacts if a.id == artifact_id]
    if not hits:
        raise DirectoryAuthoringError(missing, f"No artifact with ID {artifact_id!r}.", subjects=(artifact_id,))
    if len(hits) > 1:
        raise DirectoryAuthoringError(
            ambiguous, f"Cannot identify a unique artifact with ID {artifact_id!r}.", subjects=(artifact_id,)
        )
    return hits[0]


def _under(child: str, parent: str) -> bool:
    return child.startswith(parent if parent.endswith(os.sep) else parent + os.sep)


def _overlaps(
    loaded: LoadedManifest, manifests: list[Manifest], parts: list[str], path: str, own_id: Optional[str] = None
) -> tuple[str, ...]:
    """File artifacts inside the root. Raises for every other overlap.

    Paths are compared absolute and case-folded, so ``./data``, ``data\\`` and
    ``DATA`` all name the same root as ``data``. Nothing on disk is read."""
    new = _key(loaded.root.absolute().joinpath(*parts)).casefold()
    inside: set[str] = set()
    for manifest in manifests:
        for art in manifest.artifacts:
            if art.id == own_id:
                continue
            other = _key(_on_disk(loaded.root, art)).casefold()
            if other == new:
                raise DirectoryAuthoringError(
                    "path-collision", f"{path!r} is already tracked as {art.id!r} ({art.path!r}).", subjects=(art.id,)
                )
            if _under(other, new) and art.artifact_type == "file":
                inside.add(art.id)
            elif _under(other, new) or _under(new, other):
                where = "inside" if _under(other, new) else "above"
                raise DirectoryAuthoringError(
                    "overlap",
                    f"{path!r} overlaps {art.artifact_type} artifact {art.id!r} ({art.path!r}) {where} it; "
                    "nested directory records are not supported.",
                    subjects=(art.id,),
                )
    return tuple(sorted(inside))


def _linkage(manifest: Manifest, artifact_id: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Results naming the artifact, and claims citing it or those results."""
    results = [r for r in manifest.results if r.artifact_id == artifact_id]
    locators = sorted({r.id for r in results if r.locator is not None})
    if locators:
        raise DirectoryAuthoringError(
            "locator-unsupported",
            f"Results {', '.join(locators)} read a JSON locator from {artifact_id!r}, but a directory is not a "
            "JSON file. Point them at a JSON file artifact, or drop the locator.",
            subjects=tuple(locators),
        )
    cited = {artifact_id} | {r.id for r in results}
    claims = {e.id for e in manifest.evidence if cited & (set(e.supports) | set(e.contradicts))}
    return tuple(sorted({r.id for r in results})), tuple(sorted(claims))


def _measure(loaded: LoadedManifest, parts: list[str], path: str) -> DirectoryDigest:
    root: Path = loaded.root.absolute().joinpath(*parts)
    try:
        # Looked up on the module at call time: the scanner is the only reader.
        return directory_scanner.scan_directory(root).digest
    except DirectoryScanError as exc:
        raise DirectoryAuthoringError(
            "scan-failed", f"Cannot measure {path!r} ({exc.reason}): {exc}", scan_reason=exc.reason
        ) from exc


def _finish(
    operation, proposed, entry, old_sha256, old_size, digest, changed, linked, claims, overlapping,
    *, add_kind: Optional[str], add_run_id: Optional[str],
) -> DirectoryProposal:
    validate_manifest(proposed)  # our own edit must leave a valid manifest
    dump_raw(proposed)  # and one that can be written as JSON
    notes = []
    if overlapping:
        notes.append(f"File artifacts inside the folder keep their own checks: {', '.join(overlapping)}")
    if linked:
        notes.append(f"Results linked to the directory were not re-read: {', '.join(linked)}")
    if claims:
        notes.append(f"Claims citing the directory or its results: {', '.join(claims)}")
    if not changed:
        notes.append("Digest and size are unchanged; the proposed manifest equals the input.")
    return DirectoryProposal(
        operation=operation,
        artifact_id=entry["id"],
        path=entry["path"],
        add_kind=add_kind,
        add_run_id=add_run_id,
        entry=copy.deepcopy(entry),
        raw=proposed,
        old_sha256=old_sha256,
        new_sha256=digest.sha256,
        old_size=old_size,
        new_size=digest.size_bytes,
        members=digest.members,
        changed=changed,
        linked_results=linked,
        claims=claims,
        overlapping_files=overlapping,
        notes=tuple(notes),
    )
