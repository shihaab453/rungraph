"""Read TensorBoard event files into a schema 0.5 RunEvidence manifest.

This adapter is optional: core verification does not need it, and nothing here
imports TensorBoard, TensorFlow, protobuf or google-crc32c. The dependency-free
core reader in :mod:`runevidence.tensorboard_events` does the parsing.

What the manifest gets:

* every regular file in the folder as an Artifact with sha256 and size, like
  the generic adapter, except RunEvidence's own manifest and backup files;
* one Run per directory that directly contains event files, named by the
  directory's relative path. Event files in nested directories belong to their
  own directory's run; the root directory is ``.``;
* one Result per selected supported scalar, in discovery order, with a
  :class:`~runevidence.models.TensorboardLocator` pointing at the exact tag,
  step and occurrence;
* non-event artifacts keep the generic adapter's IDs and have no run.

Discovery uses the RFC 4.4 predicate exactly (basename contains ``.tfevents``,
case-insensitive). Event artifacts and runs use path-derived fixed IDs and
results use ``(run_id, artifact_id, tag, step, occurrence)``-derived fixed IDs;
every fixed ``tb-art``/``tb-run``/``tb-res`` ID is reserved before generic IDs
are allocated, so generic slugs suffix on any collision. Only two different
paths hashing to the same fixed ID are an error.

Each event file is read exactly once through the core protected snapshot
(128 MiB cap) and the same bytes are hashed and parsed. A missing, changed,
refused or over-cap snapshot is one fixed :class:`AdapterError`; a tag that
cannot be represented in schema 0.5 (empty, invalid UTF-8, more than 4,096
UTF-8 bytes) is another. A filter may skip unrepresentable tags instead; a
manifest that still exceeds 10,000 selected results or 16 MiB of selected tag
bytes is another error. Failures in one file do not stop the remaining files
from being profile-checked; the highest-precedence failure is raised at the
end. In every failure case nothing is written, because ``init`` never writes a
manifest returned partially.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from runevidence import hashing
from runevidence.adapters import Adapter, AdapterError, Discovery
from runevidence.adapters.generic import valid_slug
from runevidence.checks.files import (
    TENSORBOARD_MAX_FILE_BYTES,
    _parts_for,
    _read_snapshot_parts,
    _within,
    iter_files,
)
# Import the checker's predicate itself so the adapter can never disagree with
# verification about what an event file is (RFC 4.4: basename, case-insensitive).
from runevidence.checks.results import _tensorboard_path_matches
from runevidence.models import (
    MAX_TENSORBOARD_TAG,
    SCHEMA_VERSION_0_5,
    Artifact,
    Manifest05,
    Project,
    Result05,
    Run,
    TensorboardLocator,
    is_own_file,
)
from runevidence.tag_filter import TagFilter, TagPatternTooExpensive
from runevidence.tensorboard_events import EventFileError, iter_supported_scalars

#: Per-manifest result budget and total tag-byte budget (RFC section 9).
MAX_RESULTS = 10_000
MAX_TAG_BYTES = 16 * 1024 * 1024

PROFILE_MESSAGE = (
    "the event file does not satisfy the strict TensorBoard profile; nothing was written"
)
OVER_CAP_MESSAGE = (
    "the event file exceeds the 128 MiB TensorBoard limit; nothing was written"
)
TAG_MESSAGE = "a scalar tag cannot be represented in schema 0.5; nothing was written"
BUDGET_MESSAGE = (
    "the selected TensorBoard scalars exceed the 10,000-result or 16 MiB tag limit; "
    "nothing was written"
)
COLLISION_MESSAGE = "a generated id collides with an existing id; nothing was written"
NAME_MESSAGE = "a generated result name collides within a run; nothing was written"
BACKSLASH_MESSAGE = "an event path contains a backslash; nothing was written"
SURROGATE_MESSAGE = "a path is not valid UTF-8; nothing was written"

# Failure precedence, highest first (RFC 4.4 / section 6).
_PROFILE = 1
_TAG = 2
_EXPENSIVE = 3
_BUDGET = 4
_ID = 5
_NAME = 6


class TensorboardAdapter(Adapter):
    name = "tensorboard"
    supports_tag_filters = True
    description = (
        "TensorBoard log directories: one run per directory, selected scalars as results."
    )

    def discover(self, root: Path) -> Discovery:
        root = Path(root)
        files = sorted(p for p in iter_files(root) if not is_own_file(p))
        return Discovery(root=root, files=files)

    def extract(
        self,
        root: Path,
        discovery: Discovery,
        *,
        project_id: str,
        project_name: str,
        tag_filter: TagFilter | None = None,
    ) -> Manifest05:
        root = Path(root)
        paths = sorted(discovery.files)
        event_paths = [path for path in paths if _tensorboard_path_matches(path)]
        _refuse_paths(paths, event_paths)

        state = _State(tag_filter)
        events: dict[str, _EventFile] = {}
        for rel in event_paths:
            event = _read_event_file(root, rel, state)
            if event is not None:
                events[rel] = event

        used: set[str] = {project_id}
        run_ids: dict[str, str] = {}
        for rel in event_paths:
            dir_rel = _dir_rel(rel)
            run_ids.setdefault(dir_rel, _run_id(dir_rel))
        for run_id in dict.fromkeys(run_ids.values()):
            _record_reservation(run_id, used, state)
        for event in events.values():
            _record_reservation(event.artifact_id, used, state)
            for observation in event.observations:
                _record_reservation(
                    _result_id(event.run_id, event.artifact_id, observation.tag, observation.step, observation.occurrence),
                    used,
                    state,
                )

        _assign_names(events, state)

        runs: list[Run] = []
        seen_runs: set[str] = set()
        for rel in event_paths:
            dir_rel = _dir_rel(rel)
            run_id = run_ids[dir_rel]
            if run_id in seen_runs:
                continue
            seen_runs.add(run_id)
            runs.append(Run(id=run_id, name=dir_rel))

        event_set = set(event_paths)
        artifacts: list[Artifact] = []
        results: list[Result05] = []
        for rel in paths:
            event = events.get(rel)
            if event is not None:
                artifacts.append(
                    Artifact(
                        id=event.artifact_id,
                        path=event.path,
                        sha256=event.digest,
                        size_bytes=event.size,
                        run_id=event.run_id,
                    )
                )
                for observation in event.observations:
                    results.append(
                        Result05(
                            id=_result_id(
                                event.run_id,
                                event.artifact_id,
                                observation.tag,
                                observation.step,
                                observation.occurrence,
                            ),
                            run_id=event.run_id,
                            name=observation.name or "",
                            value=observation.value,
                            artifact_id=event.artifact_id,
                            locator=TensorboardLocator(
                                kind="tensorboard",
                                tag=observation.tag,
                                step=observation.step,
                                occurrence=observation.occurrence,
                            ),
                        )
                    )
                continue
            if rel in event_set:
                # The event file was refused above; do not also hash it as a
                # generic artifact (it may be missing or unreadable).
                continue
            path = root / rel
            if not _within(root, path):
                # A supplied discovery list can name a path outside the root;
                # never hash bytes that resolve elsewhere (generic behavior).
                continue
            artifacts.append(
                Artifact(
                    id=_unique(valid_slug(rel), used),
                    path=rel,
                    sha256=hashing.sha256_file(path),
                    size_bytes=path.stat().st_size,
                )
            )

        state.raise_first()
        return Manifest05(
            schema_version=SCHEMA_VERSION_0_5,
            project=Project(id=project_id, name=project_name),
            runs=runs,
            artifacts=artifacts,
            results=results,
            evidence=[],
        )


@dataclass
class _Budget:
    """Per-manifest selection budget shared by every event file."""

    results: int = 0
    tag_bytes: int = 0
    over: bool = False

    def charge(self, tag_bytes: int) -> bool:
        if self.over:
            return False
        if self.results + 1 > MAX_RESULTS or self.tag_bytes + tag_bytes > MAX_TAG_BYTES:
            self.over = True
            return False
        self.results += 1
        self.tag_bytes += tag_bytes
        return True


@dataclass
class _Observation:
    tag: str
    step: int
    occurrence: int
    value: Any
    name: str | None = None


@dataclass(frozen=True)
class _EventFile:
    """Everything one event file contributes, read through one snapshot."""

    path: str
    dir_rel: str
    basename: str
    digest: str
    size: int
    artifact_id: str
    run_id: str
    observations: list[_Observation]


@dataclass
class _State:
    """Shared per-extract filter, budget and deferred failures."""

    tag_filter: TagFilter | None
    budget: _Budget = field(default_factory=_Budget)
    failures: dict[int, Exception] = field(default_factory=dict)
    selected: dict[str, bool] = field(default_factory=dict)
    filter_stopped: bool = False

    def record(self, rank: int, error: Exception) -> None:
        self.failures.setdefault(rank, error)

    def select(self, tag: str) -> bool:
        if self.tag_filter is None:
            return True
        if self.filter_stopped:
            return False
        cached = self.selected.get(tag)
        if cached is not None:
            return cached
        try:
            hit = self.tag_filter.matches(tag)
        except TagPatternTooExpensive as exc:
            self.record(_EXPENSIVE, exc)
            self.filter_stopped = True
            return False
        self.selected[tag] = hit
        return hit

    def raise_first(self) -> None:
        if not self.failures:
            return
        error = self.failures[min(self.failures)]
        raise error


def _refuse_paths(paths: list[str], event_paths: list[str]) -> None:
    """Refuse undecodable paths and POSIX event paths containing a backslash."""

    for path in paths:
        try:
            path.encode("utf-8")
        except UnicodeEncodeError:
            raise AdapterError(SURROGATE_MESSAGE) from None
    if os.name != "nt":
        for path in event_paths:
            if "\\" in path:
                raise AdapterError(BACKSLASH_MESSAGE)


def _dir_rel(path: str) -> str:
    return PurePosixPath(path).parent.as_posix()


def _basename(path: str) -> str:
    return PurePosixPath(path).name


def _fs_bytes(value: str) -> bytes:
    return os.fsencode(value)


def _run_id(dir_rel: str) -> str:
    return f"tb-run-{hashlib.sha256(_fs_bytes(dir_rel)).hexdigest()[:16]}"


def _artifact_id(path: str) -> str:
    return f"tb-art-{hashlib.sha256(_fs_bytes(path)).hexdigest()[:16]}"


def _result_id(run_id: str, artifact_id: str, tag: str, step: int, occurrence: int) -> str:
    payload = (
        run_id.encode("utf-8")
        + b"\x00"
        + artifact_id.encode("utf-8")
        + b"\x00"
        + tag.encode("utf-8")
        + b"\x00"
        + str(step).encode("ascii")
        + b"\x00"
        + str(occurrence).encode("ascii")
    )
    return f"tb-res-{hashlib.sha256(payload).hexdigest()[:16]}"


def _name_digest(artifact_id: str, occurrence: int) -> str:
    payload = (
        artifact_id.encode("utf-8")
        + b"\x00"
        + str(occurrence).encode("ascii")
    )
    return hashlib.sha256(payload).hexdigest()[:16]


def _read_event_file(root: Path, rel: str, state: _State) -> _EventFile | None:
    """One protected read of one event file, then parse and filter the bytes."""

    path = PurePosixPath(rel).as_posix()
    dir_rel = _dir_rel(path)
    basename = _basename(path)
    artifact_id = _artifact_id(path)
    run_id = _run_id(dir_rel)

    read = _read_snapshot_parts(
        root,
        _parts_for(root, rel),
        cap=TENSORBOARD_MAX_FILE_BYTES,
        tensorboard_raised=True,
    )
    if read.state == "over-cap":
        state.record(_PROFILE, AdapterError(OVER_CAP_MESSAGE))
        return None
    snapshot = read.snapshot
    if read.state != "ok" or snapshot is None or snapshot.data is None:
        state.record(_PROFILE, AdapterError(PROFILE_MESSAGE))
        return None

    observations: list[_Observation] = []
    occurrences: dict[tuple[str, int], int] = {}
    try:
        for record in iter_supported_scalars(snapshot.data):
            try:
                tag = _decoded_tag(record.tag)
            except AdapterError as exc:
                if state.tag_filter is None:
                    state.record(_TAG, exc)
                continue
            key = (tag, record.step)
            occurrence = occurrences.get(key, 0)
            occurrences[key] = occurrence + 1
            if not state.select(tag):
                continue
            if state.budget.over or not state.budget.charge(len(record.tag)):
                state.record(_BUDGET, AdapterError(BUDGET_MESSAGE))
                continue
            observations.append(
                _Observation(tag=tag, step=record.step, occurrence=occurrence, value=record.value)
            )
    except EventFileError:
        state.record(_PROFILE, AdapterError(PROFILE_MESSAGE))
        return None

    return _EventFile(
        path=path,
        dir_rel=dir_rel,
        basename=basename,
        digest=snapshot.digest,
        size=snapshot.size,
        artifact_id=artifact_id,
        run_id=run_id,
        observations=observations,
    )


def _assign_names(events: dict[str, _EventFile], state: _State) -> None:
    """Name observations per ``(tag, step)`` group and refuse duplicates."""

    groups: dict[tuple[str, str, int], list[tuple[_EventFile, _Observation]]] = {}
    for event in events.values():
        for observation in event.observations:
            groups.setdefault((event.run_id, observation.tag, observation.step), []).append(
                (event, observation)
            )

    seen: set[tuple[str, str]] = set()
    for (run_id, tag, step), members in groups.items():
        files = {event.path for event, _ in members}
        for event, observation in members:
            if len(files) == 1:
                name = f"{tag} @ {step}"
                if observation.occurrence > 0:
                    name += f" #{observation.occurrence}"
            else:
                name = (
                    f"{tag} @ {step} [{event.basename}] "
                    f"#{_name_digest(event.artifact_id, observation.occurrence)}"
                )
            observation.name = name
            key = (run_id, name)
            if key in seen:
                state.record(_NAME, AdapterError(NAME_MESSAGE))
            seen.add(key)


def _record_reservation(value: str, used: set[str], state: _State) -> None:
    if value in used:
        state.record(_ID, AdapterError(COLLISION_MESSAGE))
        return
    used.add(value)


def _decoded_tag(raw: bytes) -> str:
    """The tag as schema 0.5 can represent it, or the fixed refusal."""
    if not raw or len(raw) > MAX_TENSORBOARD_TAG:
        raise AdapterError(TAG_MESSAGE)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise AdapterError(TAG_MESSAGE) from None


def _unique(base: str, used: set[str]) -> str:
    candidate, n = base, 2
    while candidate in used:
        candidate, n = f"{base}-{n}", n + 1
    used.add(candidate)
    return candidate
