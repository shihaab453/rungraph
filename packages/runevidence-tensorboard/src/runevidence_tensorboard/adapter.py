"""Read TensorBoard event files into a schema 0.5, 0.6 or 0.7 manifest.

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
are allocated, so generic slugs suffix on any collision. Any fixed-vs-fixed
collision (two paths, two runs or two results hashing to the same ID, or a
fixed ID equal to the project ID) is the single collision error.

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
import json
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
    SCHEMA_VERSION_0_6,
    SCHEMA_VERSION_0_7,
    Artifact,
    HistogramValue,
    Manifest05,
    Manifest06,
    Manifest07,
    Project,
    Result05,
    Result06,
    Result07,
    Run,
    TensorboardHistogramLocator,
    TensorboardLocator,
    TensorboardStringLocator,
    is_own_file,
)
from runevidence.tag_filter import TagFilter, TagPatternTooExpensive
from runevidence.tensorboard_events import (
    EventFileError,
    HistogramRecord,
    StringRecord,
    iter_supported_scalars,
    iter_supported_summaries,
    iter_supported_summaries_07,
)

#: Per-manifest result budget and total tag-byte budget (RFC section 9).
MAX_RESULTS = 10_000
MAX_TAG_BYTES = 16 * 1024 * 1024
MAX_TEXT_BYTES = 16 * 1024 * 1024
MAX_STRING_VALUE_BYTES = 65_536
MAX_HISTOGRAM_BYTES = 16 * 1024 * 1024

PROFILE_MESSAGE = (
    "the event file does not satisfy the strict TensorBoard profile; nothing was written"
)
OVER_CAP_MESSAGE = (
    "the event file exceeds the 128 MiB TensorBoard limit; nothing was written"
)
TAG_MESSAGE = "a scalar tag cannot be represented in schema 0.5; nothing was written"
TAG_MESSAGE_06 = "a scalar tag cannot be represented in schema 0.6; nothing was written"
TAG_MESSAGE_07 = "a scalar tag cannot be represented in schema 0.7; nothing was written"
BUDGET_MESSAGE = (
    "the selected TensorBoard scalars exceed the 10,000-result or 16 MiB tag limit; "
    "nothing was written"
)
BUDGET_MESSAGE_06 = (
    "the selected TensorBoard summaries exceed the 10,000-result, 16 MiB tag or "
    "16 MiB string-value limit; nothing was written"
)
BUDGET_MESSAGE_07_HIST = (
    "the selected TensorBoard summaries exceed the 10,000-result, 16 MiB tag or "
    "16 MiB histogram-JSON limit; nothing was written"
)
BUDGET_MESSAGE_07 = (
    "the selected TensorBoard summaries exceed the 10,000-result, 16 MiB tag, "
    "16 MiB string-value or 16 MiB histogram-JSON limit; nothing was written"
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
    supports_include_strings = True
    supports_include_histograms = True
    description = (
        "TensorBoard log directories: one run per directory, selected scalars or strings."
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
        include_strings: bool = False,
        include_histograms: bool = False,
    ) -> Manifest05 | Manifest06 | Manifest07:
        root = Path(root)
        paths = sorted(discovery.files)
        event_paths = [path for path in paths if _tensorboard_path_matches(path)]
        _refuse_paths(paths, event_paths)
        if include_histograms:
            schema_version = SCHEMA_VERSION_0_7
            result_type = Result07
            manifest_type = Manifest07
        elif include_strings:
            schema_version = SCHEMA_VERSION_0_6
            result_type = Result06
            manifest_type = Manifest06
        else:
            schema_version = SCHEMA_VERSION_0_5
            result_type = Result05
            manifest_type = Manifest05

        state = _State(
            tag_filter,
            include_strings=include_strings,
            include_histograms=include_histograms,
        )
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
                    _result_id(
                        event.run_id,
                        event.artifact_id,
                        observation.kind,
                        observation.tag,
                        observation.step,
                        observation.occurrence,
                    ),
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
        results: list[Result05 | Result06] = []
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
                    if observation.kind == "histogram":
                        locator = TensorboardHistogramLocator(
                            kind="tensorboard-histogram",
                            tag=observation.tag,
                            step=observation.step,
                            occurrence=observation.occurrence,
                        )
                    elif observation.kind == "text":
                        locator = TensorboardStringLocator(
                            kind="tensorboard-string",
                            tag=observation.tag,
                            step=observation.step,
                            occurrence=observation.occurrence,
                        )
                    else:
                        locator = TensorboardLocator(
                            kind="tensorboard",
                            tag=observation.tag,
                            step=observation.step,
                            occurrence=observation.occurrence,
                        )
                    results.append(
                        result_type(
                            id=_result_id(
                                event.run_id,
                                event.artifact_id,
                                observation.kind,
                                observation.tag,
                                observation.step,
                                observation.occurrence,
                            ),
                            run_id=event.run_id,
                            name=observation.name or "",
                            value=observation.value,
                            artifact_id=event.artifact_id,
                            locator=locator,
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
        return manifest_type(
            schema_version=schema_version,
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
    text_bytes: int = 0
    hist_bytes: int = 0
    over: bool = False

    def charge(
        self, tag_bytes: int, text_bytes: int = 0, hist_bytes: int = 0
    ) -> bool:
        if self.over:
            return False
        if self.results + 1 > MAX_RESULTS:
            self.over = True
            return False
        if self.tag_bytes + tag_bytes > MAX_TAG_BYTES:
            self.over = True
            return False
        if text_bytes and self.text_bytes + text_bytes > MAX_TEXT_BYTES:
            self.over = True
            return False
        if hist_bytes and self.hist_bytes + hist_bytes > MAX_HISTOGRAM_BYTES:
            self.over = True
            return False
        self.results += 1
        self.tag_bytes += tag_bytes
        self.text_bytes += text_bytes
        self.hist_bytes += hist_bytes
        return True


@dataclass
class _Observation:
    kind: str
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
    include_strings: bool = False
    include_histograms: bool = False
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


def _result_id(
    run_id: str,
    artifact_id: str,
    kind: str,
    tag: str,
    step: int,
    occurrence: int,
) -> str:
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
    if kind != "scalar":
        payload += b"\x00" + kind.encode("utf-8")
    return f"tb-res-{hashlib.sha256(payload).hexdigest()[:16]}"


def _name_digest(artifact_id: str, occurrence: int, kind: str) -> str:
    payload = (
        artifact_id.encode("utf-8")
        + b"\x00"
        + str(occurrence).encode("ascii")
    )
    if kind != "scalar":
        payload += b"\x00" + kind.encode("utf-8")
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
    occurrences: dict[tuple[str, str, int], int] = {}
    if state.include_histograms:
        iterator = iter_supported_summaries_07(snapshot.data)
    elif state.include_strings:
        iterator = iter_supported_summaries(snapshot.data)
    else:
        iterator = iter_supported_scalars(snapshot.data)
    try:
        for record in iterator:
            is_text = isinstance(record, StringRecord)
            is_hist = isinstance(record, HistogramRecord)
            kind = "histogram" if is_hist else ("text" if is_text else "scalar")
            try:
                tag = _decoded_tag(
                    record.tag, state.include_histograms, state.include_strings, text=is_text or is_hist
                )
            except AdapterError as exc:
                if state.tag_filter is None:
                    state.record(_TAG, exc)
                continue
            if tag is None:
                continue
            key = (kind, tag, record.step)
            occurrence = occurrences.get(key, 0)
            occurrences[key] = occurrence + 1
            if not state.select(tag):
                continue
            text_bytes = len(record.value) if is_text else 0
            hist_bytes = 0
            if is_hist:
                hist_bytes = len(
                    json.dumps(
                        record.value,
                        ensure_ascii=True,
                        allow_nan=False,
                        separators=(",", ":"),
                    ).encode("utf-8")
                )
            if state.budget.over or not state.budget.charge(
                len(record.tag), text_bytes, hist_bytes
            ):
                if state.include_histograms:
                    message = (
                        BUDGET_MESSAGE_07
                        if state.include_strings
                        else BUDGET_MESSAGE_07_HIST
                    )
                else:
                    message = BUDGET_MESSAGE_06 if state.include_strings else BUDGET_MESSAGE
                state.record(_BUDGET, AdapterError(message))
                continue
            value: Any = record.value
            if is_text:
                value = record.value.decode("utf-8")
            elif is_hist:
                value = HistogramValue.model_validate(record.value)
            observations.append(
                _Observation(
                    kind=kind,
                    tag=tag,
                    step=record.step,
                    occurrence=occurrence,
                    value=value,
                )
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
    """Name observations per ``(kind, tag, step)`` group and refuse duplicates."""

    groups: dict[
        tuple[str, str, str, int], list[tuple[_EventFile, _Observation]]
    ] = {}
    for event in events.values():
        for observation in event.observations:
            groups.setdefault(
                (
                    event.run_id,
                    observation.kind,
                    observation.tag,
                    observation.step,
                ),
                [],
            ).append((event, observation))

    seen: set[tuple[str, str]] = set()
    for (run_id, kind, tag, step), members in groups.items():
        files = {event.path for event, _ in members}
        marker = f" ({kind})" if kind != "scalar" else ""
        for event, observation in members:
            if len(files) == 1:
                name = f"{tag} @ {step}{marker}"
                if observation.occurrence > 0:
                    name += f" #{observation.occurrence}"
            else:
                name = (
                    f"{tag} @ {step}{marker} [{event.basename}] "
                    f"#{_name_digest(event.artifact_id, observation.occurrence, kind)}"
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


def _decoded_tag(
    raw: bytes,
    schema_06: bool = False,
    schema_07: bool = False,
    *,
    text: bool = False,
) -> str | None:
    """The tag as the target schema can represent it.

    Scalar tags keep the delivered refusal; a string entry with an
    unrepresentable tag is skipped (returns ``None``).
    """
    if not raw or len(raw) > MAX_TENSORBOARD_TAG:
        if text:
            return None
        if schema_07:
            raise AdapterError(TAG_MESSAGE_07)
        raise AdapterError(TAG_MESSAGE_06 if schema_06 else TAG_MESSAGE)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        if text:
            return None
        if schema_07:
            raise AdapterError(TAG_MESSAGE_07) from None
        raise AdapterError(TAG_MESSAGE_06 if schema_06 else TAG_MESSAGE) from None


def _unique(base: str, used: set[str]) -> str:
    candidate, n = base, 2
    while candidate in used:
        candidate, n = f"{base}-{n}", n + 1
    used.add(candidate)
    return candidate
