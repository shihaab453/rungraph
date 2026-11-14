"""Read TensorBoard event files into a schema 0.5 RunEvidence manifest.

This adapter is optional: core verification does not need it, and nothing here
imports TensorBoard, TensorFlow, protobuf or google-crc32c. The dependency-free
core reader in :mod:`runevidence.tensorboard_events` does the parsing.

What the manifest gets:

* every regular file in the folder as an Artifact with sha256 and size, like
  the generic adapter, except RunEvidence's own manifest and backup files;
* one Run per event file, named by the recorded relative path. The generic
  adapter's ``runs/<name>/`` folder convention is deliberately not inherited,
  so two event files in one folder are two runs and no multi-file run exists;
* one Result per supported scalar, in record order and then ``Summary.Value``
  order, with a :class:`~runevidence.models.TensorboardLocator` pointing at the
  exact tag, step and occurrence;
* non-event artifacts keep the generic adapter's IDs and have no run.

Discovery uses the RFC 4.4 predicate exactly (basename contains ``.tfevents``,
case-insensitive). Event artifacts and runs use path-derived fixed IDs and
results use ``(run_id, tag, step, occurrence)``-derived fixed IDs; every fixed
``tb-art``/``tb-run``/``tb-res`` ID is reserved before generic IDs are
allocated, so generic slugs suffix on any collision. Only two event files
hashing to the same fixed ID is an error.

Each event file is read exactly once through the core protected snapshot
(128 MiB cap) and the same bytes are hashed and parsed. A missing, changed,
refused or over-cap snapshot is one fixed :class:`AdapterError`; a tag that
cannot be represented in schema 0.5 (empty, invalid UTF-8, more than 4,096
UTF-8 bytes) or a manifest exceeding 10,000 results or 16 MiB of tag bytes is
another. In every failure case nothing is written, because ``init`` never
writes a manifest returned partially.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

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


class TensorboardAdapter(Adapter):
    name = "tensorboard"
    description = (
        "TensorBoard event files: one run per .tfevents file, every supported scalar as a result."
    )

    def discover(self, root: Path) -> Discovery:
        root = Path(root)
        files = sorted(p for p in iter_files(root) if not is_own_file(p))
        return Discovery(root=root, files=files)

    def extract(
        self, root: Path, discovery: Discovery, *, project_id: str, project_name: str
    ) -> Manifest05:
        events: dict[str, "_EventFile"] = {}
        budget = _Budget()
        for rel in discovery.files:
            if _tensorboard_path_matches(rel):
                events[rel] = _read_event_file(root, rel, budget)

        # Reserve every fixed tb-art/tb-run/tb-res id before any generic slug is
        # allocated, so a generic file can never take a fixed id and a
        # fixed-vs-fixed collision cannot be hidden by a suffix.
        used: set[str] = {project_id}
        for rel in discovery.files:
            event = events.get(rel)
            if event is None:
                continue
            _reserve(event.artifact_id, used)
            _reserve(event.run_id, used)
            for result in event.results:
                _reserve(result.id, used)

        runs: list[Run] = []
        artifacts: list[Artifact] = []
        results: list[Result05] = []
        for rel in discovery.files:
            event = events.get(rel)
            if event is not None:
                runs.append(Run(id=event.run_id, name=event.name))
                artifacts.append(
                    Artifact(
                        id=event.artifact_id,
                        path=event.path,
                        sha256=event.digest,
                        size_bytes=event.size,
                        run_id=event.run_id,
                    )
                )
                results.extend(event.results)
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

    def charge(self, tag_bytes: int) -> None:
        if self.results + 1 > MAX_RESULTS or self.tag_bytes + tag_bytes > MAX_TAG_BYTES:
            raise AdapterError(BUDGET_MESSAGE)
        self.results += 1
        self.tag_bytes += tag_bytes


@dataclass(frozen=True)
class _EventFile:
    """Everything one event file contributes, read through one snapshot."""

    path: str
    name: str
    digest: str
    size: int
    artifact_id: str
    run_id: str
    results: list[Result05]


def _read_event_file(root: Path, rel: str, budget: _Budget) -> _EventFile:
    """One protected read of one event file, then parse the same bytes."""
    path = PurePosixPath(rel).as_posix()
    suffix = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
    artifact_id = f"tb-art-{suffix}"
    run_id = f"tb-run-{suffix}"

    read = _read_snapshot_parts(
        root,
        _parts_for(root, rel),
        cap=TENSORBOARD_MAX_FILE_BYTES,
        tensorboard_raised=True,
    )
    if read.state == "over-cap":
        raise AdapterError(OVER_CAP_MESSAGE)
    snapshot = read.snapshot
    if read.state != "ok" or snapshot is None or snapshot.data is None:
        # missing, unreadable, changed, symlinked-parent, refused-path,
        # refused-alias and escapes-root are all refusals of this one file.
        raise AdapterError(PROFILE_MESSAGE)

    results: list[Result05] = []
    occurrences: dict[tuple[bytes, int], int] = {}
    try:
        for record in iter_supported_scalars(snapshot.data):
            tag = _decoded_tag(record.tag)
            budget.charge(len(record.tag))
            key = (record.tag, record.step)
            occurrence = occurrences.get(key, 0)
            occurrences[key] = occurrence + 1
            results.append(
                Result05(
                    id=_result_id(run_id, tag, record.step, occurrence),
                    run_id=run_id,
                    name=f"{tag} @ {record.step}"
                    + (f" #{occurrence}" if occurrence > 0 else ""),
                    value=record.value,
                    artifact_id=artifact_id,
                    locator=TensorboardLocator(
                        kind="tensorboard",
                        tag=tag,
                        step=record.step,
                        occurrence=occurrence,
                    ),
                )
            )
    except EventFileError:
        raise AdapterError(PROFILE_MESSAGE) from None

    return _EventFile(
        path=path,
        name=path,
        digest=snapshot.digest,
        size=snapshot.size,
        artifact_id=artifact_id,
        run_id=run_id,
        results=results,
    )


def _decoded_tag(raw: bytes) -> str:
    """The tag as schema 0.5 can represent it, or the fixed refusal."""
    if not raw or len(raw) > MAX_TENSORBOARD_TAG:
        raise AdapterError(TAG_MESSAGE)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise AdapterError(TAG_MESSAGE) from None


def _result_id(run_id: str, tag: str, step: int, occurrence: int) -> str:
    payload = (
        run_id.encode("utf-8")
        + b"\x00"
        + tag.encode("utf-8")
        + b"\x00"
        + str(step).encode("utf-8")
        + b"\x00"
        + str(occurrence).encode("utf-8")
    )
    return f"tb-res-{hashlib.sha256(payload).hexdigest()[:16]}"


def _reserve(fixed_id: str, used: set[str]) -> None:
    """Reserve one generated fixed id; a second use is a hard collision."""
    if fixed_id in used:
        raise AdapterError(COLLISION_MESSAGE)
    used.add(fixed_id)


def _unique(base: str, used: set[str]) -> str:
    candidate, n = base, 2
    while candidate in used:
        candidate, n = f"{base}-{n}", n + 1
    used.add(candidate)
    return candidate
