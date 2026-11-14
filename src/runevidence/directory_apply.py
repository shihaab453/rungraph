"""Saving a directory proposal to its manifest.

Internal and development only: no command calls this. It takes a proposal
from ``directory_authoring`` and the ``LoadedManifest`` it was made from, and
writes the change to that manifest's own file only after checking it again:

1. Pure checks: the arguments, that ``loaded.manifest`` matches
   ``loaded.source_bytes``, and that the proposal is exactly one change to a
   pristine copy of that source: an appended directory entry (add), or a new
   ``sha256`` and ``size_bytes`` on one directory entry (rehash).
2. The manifest file must still hold ``loaded.source_bytes``.
3. The proposal is made again from the pristine copy, with a fresh scan by the
   native scanner, and must equal the given one field for field, including
   JSON key order and value types. A folder that changed since gives
   ``stale-tree``; the proposal is never refreshed behind the caller's back.
4. Only a changed add or rehash, and not a dry run, is written, by
   ``save_raw`` with ``expected_bytes=loaded.source_bytes``.

What is written is the manifest made again in step 3, never the proposal's
own ``raw`` or ``entry``: those are mutable dictionaries, so they are only
compared. The proposal itself is never modified.

This is not an atomic compare-and-swap. Another writer can change the
manifest between ``save_raw``'s byte comparison and its ``os.replace``, and
the folder between the last scan and the save; both scan and save assume a
quiet folder with one cooperating writer. A failed save leaves the old
manifest as ``save_raw`` does: replaced whole or not at all, with its staged
copy removed on the way out, on a best-effort basis. The new file is the
staged copy, so on Windows it may inherit different permissions from the
folder; preserving the old file's custom ACL is not guaranteed.

An add's ``kind`` and ``run_id`` are bound to separate frozen scalar fields
captured at proposal creation. Pure checks compare them with the entry before
any scan, and rebuilding uses those scalars. Rehash introduces no new intent
and requires both fields to be None. Proposals without these mandatory fields
must be made again from trusted inputs; there is no automatic conversion.
This detects ordinary mutable-field changes, not arbitrary in-process object
forgery: a public dataclass and its frozen fields are not a cryptographic seal.
"""

from __future__ import annotations

import copy
import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal, Optional

from pydantic import ValidationError

from .directory_authoring import (
    DirectoryAuthoringError,
    DirectoryProposal,
    propose_add_directory,
    propose_rehash_directory,
)
from .edit import EditError
from .manifest import LoadedManifest, dump_raw, load_raw, save_raw, validate_manifest
from .models import DIRECTORY_DIGEST_FORMAT, Artifact

_FIELDS = frozenset(f.name for f in dataclasses.fields(DirectoryProposal))
_MEASURED = ("sha256", "size_bytes")


class DirectoryApplyError(EditError):
    """The proposal was not saved; the manifest file was not replaced.
    ``reason`` is one of:

    * ``invalid-request``    the arguments have the wrong types
    * ``loaded-mismatch``    ``loaded.manifest`` does not match ``loaded.source_bytes``
    * ``proposal-mismatch``  the proposal is not the one this manifest gives:
                             it was changed after it was made, or was made
                             from a different manifest
    * ``stale-source``       the manifest file is missing, unreadable or no
                             longer holds ``loaded.source_bytes``
    * ``recheck-failed``     making the proposal again was refused; see
                             ``authoring_reason`` and ``scan_reason``
    * ``stale-tree``         the folder now has a different digest, size or
                             member count; make a new proposal
    * ``write-failed``       ``save_raw`` failed and the file still holds
                             ``loaded.source_bytes``

    The reason names the first difference found; any difference refuses."""

    def __init__(
        self,
        reason: str,
        message: str,
        *,
        subjects: tuple[str, ...] = (),
        authoring_reason: Optional[str] = None,
        scan_reason: Optional[str] = None,
    ):
        self.reason = reason
        self.subjects = tuple(subjects)
        self.authoring_reason = authoring_reason
        self.scan_reason = scan_reason
        super().__init__(message)


@dataclass(frozen=True)
class DirectoryApplyResult:
    status: Literal["written", "dry-run", "unchanged"]  # dry-run: checked, would write
    operation: Literal["add", "rehash"]
    artifact_id: str
    path: str
    manifest_path: Path  # loaded.path; the only file that can be written
    old_sha256: Optional[str]
    new_sha256: str
    old_size: Optional[int]
    new_size: int
    members: int
    text: Optional[str]  # the manifest text written, or that would be; None if unchanged
    notes: tuple[str, ...]

    @property
    def written(self) -> bool:
        return self.status == "written"


def apply_directory_proposal(
    loaded: LoadedManifest, proposal: DirectoryProposal, *, dry_run: bool
) -> DirectoryApplyResult:
    """Check ``proposal`` against ``loaded`` and the folder again, then save it
    to ``loaded.path`` unless ``dry_run`` or nothing changed.

    Raises DirectoryApplyError."""
    if type(dry_run) is not bool:
        raise DirectoryApplyError("invalid-request", f"dry_run must be True or False, got {dry_run!r}")
    checked, raw = check_directory_proposal_with_raw(loaded, proposal)
    if raw is None or dry_run:
        return checked
    try:
        save_raw(raw, loaded.path, expected_bytes=loaded.source_bytes)
    except (OSError, ValueError) as exc:
        if _read(loaded.path) != loaded.source_bytes:
            raise DirectoryApplyError(
                "stale-source", f"{loaded.path} changed while it was being saved: {exc}"
            ) from exc
        raise DirectoryApplyError("write-failed", f"Could not write {loaded.path}: {exc}") from exc
    return dataclasses.replace(checked, status="written")


def check_directory_proposal(
    loaded: LoadedManifest,
    proposal: DirectoryProposal,
    *,
    source_reader: Optional[Callable[[], bytes]] = None,
) -> DirectoryApplyResult:
    """Check ``proposal`` in full, with a fresh scan, and write nothing.

    The result is ``unchanged`` when a rehash finds nothing to change, else
    ``dry-run``: the checked change and the exact text a real call would save.
    ``source_reader`` is an optional descriptor-only source read for callers
    that hold the manifest fd; the default keeps the historical path read.
    Raises DirectoryApplyError for the same reasons as
    ``apply_directory_proposal``."""
    return check_directory_proposal_with_raw(
        loaded, proposal, source_reader=source_reader
    )[0]


def check_directory_proposal_with_raw(
    loaded: LoadedManifest,
    proposal: DirectoryProposal,
    *,
    source_reader: Optional[Callable[[], bytes]] = None,
) -> "tuple[DirectoryApplyResult, Optional[dict]]":
    """``check_directory_proposal`` plus the checked raw manifest to save.

    The raw value is None exactly when the result is ``unchanged``. A supplied
    ``source_reader`` is used instead of opening ``loaded.path``, so a caller
    that holds a descriptor never trusts the name again."""
    if type(loaded) is not LoadedManifest or type(proposal) is not DirectoryProposal:
        raise DirectoryApplyError(
            "invalid-request", "Need a LoadedManifest and the DirectoryProposal made from it."
        )
    pristine = _pristine(loaded)
    art = _check_one_change(proposal, pristine)

    _check_source(loaded, source_reader=source_reader)
    rebuilt = _rebuild(loaded, proposal, art, pristine)
    if not _same((rebuilt.new_sha256, rebuilt.new_size, rebuilt.members),
                 (proposal.new_sha256, proposal.new_size, proposal.members)):
        raise DirectoryApplyError(
            "stale-tree",
            f"{proposal.path!r} changed since the proposal was made (sha256 {proposal.new_sha256} is now "
            f"{rebuilt.new_sha256}). Nothing was written; make a new proposal and review it.",
            subjects=(proposal.artifact_id,),
        )
    for name in sorted(_FIELDS):
        if not _same(getattr(proposal, name), getattr(rebuilt, name)):
            raise _mismatch(f"its {name} differs from the proposal this manifest and folder give now")

    if not rebuilt.changed:
        return _result("unchanged", loaded, rebuilt, None), None
    text = dump_raw(rebuilt.raw)
    return _result("dry-run", loaded, rebuilt, text), rebuilt.raw


# --- helpers ---------------------------------------------------------------


def _pristine(loaded: LoadedManifest) -> dict:
    """A fresh parse of ``loaded.source_bytes``, checked against ``loaded.manifest``."""
    same = isinstance(loaded.path, Path) and loaded.path.is_absolute() and type(loaded.source_bytes) is bytes
    try:
        pristine = load_raw(loaded) if same else None
        same = same and validate_manifest(copy.deepcopy(pristine)) == loaded.manifest
    except (ValueError, ValidationError, TypeError):  # JSONDecodeError and UnicodeDecodeError are ValueErrors
        same = False
    if not same:
        raise DirectoryApplyError(
            "loaded-mismatch",
            "The loaded manifest does not match its own source bytes or path; load the manifest again.",
        )
    return pristine


def _check_one_change(proposal: DirectoryProposal, pristine: dict) -> Artifact:
    """The proposal's directory entry, after checking that ``proposal.raw`` is
    ``pristine`` with only that entry appended (add) or only its ``sha256``
    and ``size_bytes`` changed (rehash), and that the proposal's own fields
    agree with it. Pure; reads nothing from disk."""
    if set(vars(proposal)) != _FIELDS:
        raise _mismatch("it has attributes a DirectoryProposal does not")
    operation, raw, entry = proposal.operation, proposal.raw, proposal.entry
    if operation not in ("add", "rehash"):
        raise DirectoryApplyError("invalid-request", f"Unknown operation {operation!r}")
    if type(raw) is not dict or type(entry) is not dict:
        raise _mismatch("its raw manifest or entry is not a JSON object")
    intent = (proposal.add_kind, proposal.add_run_id)
    if any(value is not None and type(value) is not str for value in intent):
        raise _mismatch("its add intent must contain only strings or None")
    if operation == "add":
        if not _same(intent, (entry.get("kind"), entry.get("run_id"))):
            raise _mismatch("its entry disagrees with its creation-time add intent")
    elif not _same(intent, (None, None)):
        raise _mismatch("a rehash cannot introduce add intent")

    adds_key = operation == "add" and "artifacts" not in pristine
    if list(raw) != list(pristine) + (["artifacts"] if adds_key else []):
        raise _mismatch("its top-level keys were added, removed or reordered")
    for key in pristine:
        if key != "artifacts" and not _same(raw[key], pristine[key]):
            raise _mismatch(f"its {key!r} differs from the loaded manifest")
    before, after = pristine.get("artifacts", []), raw["artifacts"]
    if type(after) is not list:
        raise _mismatch("its artifacts are not a list")

    if operation == "add":
        if len(after) != len(before) + 1 or not _same(after[:-1], before):
            raise _mismatch("it does more than append one artifact")
        target, original = after[-1], None
    else:
        hits = [i for i, e in enumerate(before) if type(e) is dict and e.get("id") == proposal.artifact_id]
        if len(hits) != 1 or len(after) != len(before):
            raise _mismatch(f"it does not name exactly one loaded artifact {proposal.artifact_id!r}")
        index = hits[0]
        if not _same(after[:index] + after[index + 1:], before[:index] + before[index + 1:]):
            raise _mismatch("it changes artifacts other than the one re-measured")
        target, original = after[index], before[index]
        if (type(target) is not dict or list(target)[:len(original)] != list(original)
                or not set(list(target)[len(original):]) <= set(_MEASURED)
                or any(not _same(target[k], v) for k, v in original.items() if k not in _MEASURED)):
            raise _mismatch("it changes more of the entry than sha256 and size_bytes")

    if not _same(entry, target):
        raise _mismatch("its entry differs from the entry in its raw manifest")
    try:
        art = Artifact.model_validate(copy.deepcopy(entry))
        old = None if original is None else Artifact.model_validate(copy.deepcopy(original))
    except ValidationError:
        raise _mismatch("its entry is not a valid artifact") from None
    expected = (
        (proposal.artifact_id, proposal.path, "directory", DIRECTORY_DIGEST_FORMAT,
         proposal.new_sha256, proposal.new_size, proposal.old_sha256, proposal.old_size, proposal.changed)
    )
    actual = (
        art.id, art.path, art.artifact_type, art.digest_format, art.sha256, art.size_bytes,
        None if old is None else old.sha256, None if old is None else old.size_bytes,
        original is None or not _same(target, original),
    )
    if not _same(expected, actual):
        raise _mismatch("its fields disagree with its own entry")
    return art


def _check_source(
    loaded: LoadedManifest, *, source_reader: Optional[Callable[[], bytes]] = None
) -> None:
    if source_reader is None:
        current = _read(loaded.path)
    else:
        try:
            current = source_reader()
        except OSError:
            current = None
    if current != loaded.source_bytes:
        what = "is missing or unreadable" if current is None else "changed since it was loaded"
        raise DirectoryApplyError(
            "stale-source", f"{loaded.path} {what}. Nothing was written; load it again and make a new proposal."
        )


def _rebuild(loaded: LoadedManifest, proposal: DirectoryProposal, art: Artifact, pristine: dict) -> DirectoryProposal:
    """The proposal made again from ``pristine``, scanning the folder now."""
    try:
        if proposal.operation == "add":
            return propose_add_directory(
                loaded, pristine, path=proposal.path, artifact_id=proposal.artifact_id,
                run_id=proposal.add_run_id, kind=proposal.add_kind, volatile=art.volatile,
            )
        return propose_rehash_directory(loaded, pristine, artifact_id=proposal.artifact_id)
    except DirectoryAuthoringError as exc:
        raise DirectoryApplyError(
            "recheck-failed", f"The proposal cannot be made again now: {exc}",
            subjects=exc.subjects, authoring_reason=exc.reason, scan_reason=exc.scan_reason,
        ) from exc
    except (EditError, OSError, ValueError, ValidationError) as exc:
        raise DirectoryApplyError("recheck-failed", f"The proposal cannot be made again now: {exc}") from exc


def _read(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _same(a: object, b: object) -> bool:
    """Equal as JSON values, with key order and exact types: 1, 1.0 and True differ."""
    try:
        return _same_value(a, b)
    except RecursionError:
        return False


def _same_value(a: object, b: object) -> bool:
    if type(a) is not type(b):
        return False
    if type(a) is dict:
        return list(a) == list(b) and all(_same_value(a[k], b[k]) for k in a)
    if type(a) in (list, tuple):
        return len(a) == len(b) and all(map(_same_value, a, b))
    if type(a) is float and a != a:
        return b != b  # NaN, which json.loads accepts
    return a == b


def _mismatch(why: str) -> DirectoryApplyError:
    return DirectoryApplyError(
        "proposal-mismatch",
        f"The proposal does not match the loaded manifest: {why}. It was changed after it was made, or made "
        "from a different manifest. Nothing was written.",
    )


def _result(status, loaded: LoadedManifest, proposal: DirectoryProposal, text: Optional[str]) -> DirectoryApplyResult:
    return DirectoryApplyResult(
        status=status,
        operation=proposal.operation,
        artifact_id=proposal.artifact_id,
        path=proposal.path,
        manifest_path=loaded.path,
        old_sha256=proposal.old_sha256,
        new_sha256=proposal.new_sha256,
        old_size=proposal.old_size,
        new_size=proposal.new_size,
        members=proposal.members,
        text=text,
        notes=proposal.notes,
    )
