"""Internal, bounded read-only review of explicit directory change intents.

Every intent uses the existing checked planner against the same baseline.
No intermediate manifest is composed; any refusal aborts report construction.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from typing import Literal

from .directory_plan import plan_directory_change
from .directory_preview import DirectoryPreview
from .edit import EditError
from .manifest import LoadedManifest, load_raw
from .models import Manifest

MAX_REQUESTS = 8
MAX_BATCH_JSON_BYTES = 1_048_576  # default json.dumps(asdict(review)), UTF-8
_CAVEATS = (
    "Read-only advisory review; independent per-request checks against the same loaded baseline.",
    "Non-atomic across requests and directories; no cumulative manifest or transaction is constructed.",
    "Duplicate-ID and add-path overlap refusals do not prove other changes can safely be applied together.",
    "Cooperative quiet-tree assumptions apply; previews may describe different instants.",
    "Later action must independently revalidate every directory and the manifest.",
    "No CAS, Windows custom-ACL, hostile-writer or ABA protection; source reads are not a lock or snapshot.",
    "No persisted write authorization or verified seal; unchanged is not an all-clear.",
    "No requests is not an integrity assertion.",
)


class DirectoryBatchError(EditError):
    """Fatal batch refusal with a fixed reason; no partial report is returned."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__("Directory batch review refused: " + reason)


@dataclass(frozen=True)
class DirectoryChangeRequest:
    operation: Literal["add", "rehash"]
    artifact_id: str
    path: str | None = None
    run_id: str | None = None
    kind: str | None = None


@dataclass(frozen=True)
class DirectoryBatchReview:
    state: Literal["no-requests", "advisory"]
    previews: tuple[DirectoryPreview, ...]
    total: int
    adds: int
    rehashes: int
    would_change: int
    unchanged: int
    caveats: tuple[str, ...]


def review_directory_changes(
    loaded: LoadedManifest,
    requests: tuple[DirectoryChangeRequest, ...],
) -> DirectoryBatchReview:
    """Return actual checked previews in intent order, or raise without a report.

    Shape/count/duplicate checks precede any manifest load or directory scan.
    Existing planner errors (including nested reasons) propagate without retry.
    Earlier read-only scans may have occurred when a later request is refused.
    """
    _requests(requests)
    _model(loaded)
    _source(loaded)
    previews = []
    add_paths = []
    adds = sum(r.operation == "add" for r in requests)
    for request in requests:
        preview = plan_directory_change(
            loaded, operation=request.operation, artifact_id=request.artifact_id,
            path=request.path, run_id=request.run_id, kind=request.kind,
        )
        if request.operation == "add" and adds > 1:
            # A maximum-length display path might be clipped. Refuse rather
            # than infer non-overlap from a lossy summary (IDs may clip too).
            if len(preview.path) >= 1024:
                raise DirectoryBatchError("path-summary-capacity-exceeded")
            parts = tuple(preview.path.split("/"))
            if os.name == "nt":
                parts = tuple(p.casefold() for p in parts)
            if any(parts[:len(other)] == other or other[:len(parts)] == parts for other in add_paths):
                raise DirectoryBatchError("batch-overlap")
            add_paths.append(parts)
        previews.append(preview)

    report = DirectoryBatchReview(
        "advisory" if requests else "no-requests", tuple(previews), len(previews),
        adds, len(previews) - adds,
        sum(p.status == "would-change" for p in previews),
        sum(p.status == "unchanged" for p in previews), _CAVEATS,
    )
    if len(json.dumps(asdict(report)).encode("utf-8")) > MAX_BATCH_JSON_BYTES:
        raise DirectoryBatchError("report-capacity-exceeded")
    # Recheck even an empty batch, after all work including serialization.
    # These ordinary reads provide neither CAS nor a global atomic snapshot.
    _model(loaded)
    _source(loaded)
    return report


def _requests(requests: tuple[DirectoryChangeRequest, ...]) -> None:
    if type(requests) is not tuple:
        raise DirectoryBatchError("invalid-request")
    if len(requests) > MAX_REQUESTS:
        raise DirectoryBatchError("capacity-exceeded")
    ids = set()
    for request in requests:
        if (type(request) is not DirectoryChangeRequest
                or type(request.operation) is not str or request.operation not in ("add", "rehash")
                or type(request.artifact_id) is not str):
            raise DirectoryBatchError("invalid-request")
        selectors = (request.path, request.run_id, request.kind)
        if (any(v is not None and type(v) is not str for v in selectors)
                or (request.operation == "add" and request.path is None)
                or (request.operation == "rehash" and any(v is not None for v in selectors))):
            raise DirectoryBatchError("invalid-request")
        if request.artifact_id in ids:
            raise DirectoryBatchError("duplicate-id")
        ids.add(request.artifact_id)


def _model(loaded: LoadedManifest) -> None:
    if (type(loaded) is not LoadedManifest or type(loaded.manifest) is not Manifest
            or not isinstance(loaded.path, Path) or not loaded.path.is_absolute()
            or type(loaded.source_bytes) is not bytes):
        raise DirectoryBatchError("loaded-mismatch")
    try:
        current = Manifest.model_validate(load_raw(loaded))
    except (ValueError, TypeError):
        raise DirectoryBatchError("loaded-mismatch") from None
    if current != loaded.manifest:
        raise DirectoryBatchError("loaded-mismatch")


def _source(loaded: LoadedManifest) -> None:
    try:
        same = loaded.path.read_bytes() == loaded.source_bytes
    except OSError:
        same = False
    if not same:
        raise DirectoryBatchError("stale-source")
