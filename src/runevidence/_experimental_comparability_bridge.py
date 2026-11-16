"""Experimental synthetic-only bridge from recorded manifests to PC26 comparability.

Status: experimental; synthetic-only; not integrated into application workflows;
no production privacy, scientific-validity or runtime qualification.
It compares recorded conditions only. No real-manifest qualification claim is made.

The bridge maps two in-memory ``LoadedManifest`` snapshots to the bounded
``pc26-comparability-input-v2`` document and returns the unchanged public
projection produced by :func:`runevidence.comparability.evaluate`. It reads no
artifact bytes, paths, URLs or result locators and performs no filesystem or
network I/O.

Schema 0.1 keeps the Stage 2 legacy metadata mapping exactly. Schema 0.2
resolves each dimension independently: first-class conditions and selectors
when present, legacy metadata when a member is omitted, explicit null as a
recorded UNKNOWN, and canonical duplicate checks when both spellings exist. An
explicit artifact selector requires an ``input_to`` edge to the selected run;
selectors never synthesize edges and ``Artifact.run_id`` remains produced-by.

``COMPARABLE_RECORDED_CONDITIONS`` means only that the five fixed mandatory
recorded dimensions matched. It is not proof of causal equivalence,
reproducibility, statistical equivalence or correct recording.

This module is private and deliberately not imported by ``cli``, ``checks`` or
any public workflow. Its input builder and bound checker are private,
test-facing helpers; they must not be logged, rendered or exposed as an API.

Privacy note for callers: a caller's own active exception may remain reachable
through ``ComparabilityMappingError.__context__`` for interpreter reasons.
Every bridge raise uses ``from None``, so ordinary formatted tracebacks suppress
that context and no bridge input data is retained in the error, args or
attributes. Debugger/process-memory inspection is outside the privacy promise.

Stage 1 bound limitation: exact JSON numbers whose magnitude exceeds the core's
NUMBER_LIMIT are not rejected by the bridge. They reach the core's sanitised
rejected-input projection (``input_rejected``/``NUMBER_LIMIT``), not a fixed
mapping error.
"""
from __future__ import annotations

import json
import math
import re

from . import comparability
from .checks.graph import build_index
from .findings import Code
from .manifest import LoadedManifest
from .models import EvaluationProtocol, MetricDefinition, RunConditions

__all__ = [
    "ComparabilityMappingError",
    "compare_recorded_conditions",
    "side_recording_summary",
]

# ---------------------------------------------------------------- catalogue

INVALID_SELECTOR = "INVALID_SELECTOR"
RESULT_NOT_FOUND = "RESULT_NOT_FOUND"
RESULT_SELECTION_AMBIGUOUS = "RESULT_SELECTION_AMBIGUOUS"
RUN_NOT_FOUND = "RUN_NOT_FOUND"
RUN_SELECTION_AMBIGUOUS = "RUN_SELECTION_AMBIGUOUS"
DUPLICATE_ID = "DUPLICATE_ID"
DANGLING_REFERENCE = "DANGLING_REFERENCE"
CONFLICTING_OWNERSHIP = "CONFLICTING_OWNERSHIP"
INPUT_SHAPE_UNSUPPORTED = "INPUT_SHAPE_UNSUPPORTED"
INPUT_BOUND_EXCEEDED = "INPUT_BOUND_EXCEEDED"
INTERNAL_MAPPING_ERROR = "INTERNAL_MAPPING_ERROR"

SIDES = ("baseline", "target")

_MAPPING_CODES = frozenset(
    (
        INVALID_SELECTOR,
        RESULT_NOT_FOUND,
        RESULT_SELECTION_AMBIGUOUS,
        RUN_NOT_FOUND,
        RUN_SELECTION_AMBIGUOUS,
        DUPLICATE_ID,
        DANGLING_REFERENCE,
        CONFLICTING_OWNERSHIP,
        INPUT_SHAPE_UNSUPPORTED,
        INPUT_BOUND_EXCEEDED,
        INTERNAL_MAPPING_ERROR,
    )
)

FORMAT = comparability.FORMAT_IN

DERIVED = comparability.DERIVED
PROPOSED = comparability.PROPOSED

# Fixed dimension order (the core's closed v1 table order).
DIMENSION_ORDER = (
    "dataset_identity",
    "dataset_version",
    "evaluation_split",
    "metric_definition",
    "evaluation_protocol",
    "preprocessing",
    "source_revision",
    "model_checkpoint",
    "environment",
)

FIELDS = {
    "dataset_identity": "artifact.metadata.dataset_id",
    "dataset_version": "artifact.sha256 (kind=dataset, input_to run)",
    "evaluation_split": "run.metadata.evaluation.split",
    "metric_definition": "result.metadata.metric_definition",
    "evaluation_protocol": "run.metadata.evaluation.protocol",
    "preprocessing": "relationships[derived_from] dataset->parents",
    "source_revision": "run.metadata.source_revision",
    "model_checkpoint": "artifact.sha256 (kind=checkpoint, input_to run)",
    "environment": "run.metadata.environment",
}

BASIS = {
    "dataset_identity": PROPOSED,
    "dataset_version": DERIVED,
    "evaluation_split": PROPOSED,
    "metric_definition": PROPOSED,
    "evaluation_protocol": PROPOSED,
    "preprocessing": DERIVED,
    "source_revision": PROPOSED,
    "model_checkpoint": DERIVED,
    "environment": PROPOSED,
}

NAME_ONLY_KEYS = frozenset(("name", "display_name", "label"))
METRIC_DEFINITION_KEYS = frozenset(
    ("definition_id", "higher_is_better", "averaging", "version")
)
PROTOCOL_KEYS = frozenset(("protocol_id", "version"))

# Bounds mirror the named Stage 1 limits. They are checked before evaluation;
# violations are never truncated.
MAX_OBSERVATIONS_PER_DIMENSION = 16
MAX_STRING_LENGTH = 1024
MAX_DEPTH = 16
MAX_INPUT_BYTES = 65536
MAX_ENVIRONMENT_DEPTH = 8
MAX_ENVIRONMENT_BYTES = 16 * 1024

_UNSET = object()

# The selector pattern mirrors models.SelectorId. The bridge re-checks it
# defensively for synthetic objects that bypass model validation.
_SELECTOR_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")


class ComparabilityMappingError(Exception):
    """Fixed-code mapping failure. ``side`` never carries input-derived text."""

    def __init__(self, code: str, side: str | None = None) -> None:
        self.code = code
        self.side = side
        # Fixed text only: no selector, ID, metadata, path or message content.
        super().__init__(code)


def _raise(code: str, side: str | None) -> None:
    """Raise with ordinary traceback context rendering suppressed.

    ``__context__`` may still reference a caller's active exception for
    interpreter reasons; ``from None`` sets ``__suppress_context__`` so ordinary
    formatted tracebacks do not render it. Debugger/process-memory inspection is
    outside the privacy promise.
    """
    raise ComparabilityMappingError(code, side) from None


# ------------------------------------------------------------- small helpers

def _rel_type(relationship) -> str:
    value = getattr(relationship, "type", None)
    return getattr(value, "value", value)


def _metadata_of(obj, side: str) -> dict:
    metadata = getattr(obj, "metadata", None)
    if type(metadata) is not dict:
        _raise(INPUT_SHAPE_UNSUPPORTED, side)
    return metadata


def _metadata_entry(metadata: dict, key: str):
    """Look up an exact-string key without hashing a subclass key object."""
    for candidate, value in metadata.items():
        if type(candidate) is str and candidate == key:
            return True, value
    return False, None


def _json_size(value) -> int:
    """Exact compact-JSON byte size without recursive serialization.

    Mirrors ``json.dumps(..., ensure_ascii=True, separators=(",", ":"))`` for
    exact JSON values. Iterative so a deeply nested value cannot raise
    ``RecursionError`` before a depth bound is reported.
    """
    total = 0
    active: set[int] = set()
    stack: list[tuple[object, bool]] = [(value, False)]
    while stack:
        node, leaving = stack.pop()
        if leaving:
            active.discard(id(node))
            continue
        kind = type(node)
        if node is None:
            total += 4
            continue
        if kind is bool:
            total += 4 if node else 5
            continue
        if kind is int:
            total += len(str(node))
            continue
        if kind is float:
            if not math.isfinite(node):
                raise ValueError("non-finite number")
            total += len(repr(node))
            continue
        if kind is str:
            total += len(
                json.dumps(node, ensure_ascii=True).encode("ascii")
            )
            continue
        if kind is list:
            marker = id(node)
            if marker in active:
                raise ValueError("circular reference")
            active.add(marker)
            total += 2
            if node:
                total += len(node) - 1
            stack.append((node, True))
            for item in node:
                stack.append((item, False))
            continue
        if kind is dict:
            marker = id(node)
            if marker in active:
                raise ValueError("circular reference")
            active.add(marker)
            total += 2
            if node:
                total += len(node) - 1
            stack.append((node, True))
            for key, item in node.items():
                if type(key) is not str:
                    raise TypeError("object key is not a string")
                total += len(
                    json.dumps(key, ensure_ascii=True).encode("ascii")
                ) + 1
                stack.append((item, False))
            continue
        raise TypeError("not a JSON value")
    return total


def _contains_nonfinite(value) -> bool:
    """True when a selected metadata tree contains a NaN/Infinity float."""
    stack = [value]
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        kind = type(node)
        if kind is float:
            if not math.isfinite(node):
                return True
            continue
        if kind not in (dict, list):
            continue
        marker = id(node)
        if marker in seen:
            continue
        seen.add(marker)
        if kind is dict:
            stack.extend(node.values())
        else:
            stack.extend(node)
    return False


def _strict_json_error(value, max_depth: int):
    """Return a fixed reason for a non-JSON value or over-deep container tree.

    Depth counts nested containers only: the root container is depth 1.
    """
    stack = [(value, 1, frozenset())]
    while stack:
        node, depth, ancestors = stack.pop()
        kind = type(node)
        if node is None or kind is bool or kind is int or kind is str:
            continue
        if kind is float:
            if not math.isfinite(node):
                return "NONFINITE"
            continue
        if kind not in (dict, list):
            return "TYPE"
        if depth > max_depth:
            return "DEPTH"
        marker = id(node)
        if marker in ancestors:
            return "CYCLE"
        ancestors = ancestors | {marker}
        if kind is dict:
            for key, item in node.items():
                if type(key) is not str:
                    return "KEY"
                stack.append((item, depth + 1, ancestors))
        else:
            for item in node:
                stack.append((item, depth + 1, ancestors))
    return None


def _int_or_nonblank_string(value) -> bool:
    if type(value) is int:
        return True
    return type(value) is str and value.strip() != ""


def _nonblank_string(value) -> bool:
    return type(value) is str and value.strip() != ""


def _canonical_equal(left, right) -> bool:
    """Core canonical equality, including exact rational int/float comparison."""
    return comparability._canon(left) == comparability._canon(right)


def _conditions_of(run, schema_version: str, side: str):
    """The run's first-class conditions, or None for legacy fallback.

    Schema 0.1 always falls back to Stage 2 metadata. A 0.2 run that never
    supplied ``conditions`` also falls back for every dimension.
    """
    if schema_version == "0.1":
        return None
    if "conditions" not in getattr(run, "model_fields_set", frozenset()):
        return None
    conditions = getattr(run, "conditions", None)
    if conditions is None:
        # Explicit null is rejected by the model; defensive shape failure.
        _raise(INPUT_SHAPE_UNSUPPORTED, side)
    return conditions


def _condition_child(conditions, name: str, side: str):
    """Return ``(present, value)`` for one first-class condition member."""
    if not isinstance(conditions, RunConditions):
        _raise(INPUT_SHAPE_UNSUPPORTED, side)
    if name not in conditions.model_fields_set:
        return False, None
    return True, getattr(conditions, name)


def _explicit_artifact_selector(run, name: str, schema_version: str, side: str):
    """Return ``(present, value)`` for one selector, without resolving it."""
    if schema_version == "0.1":
        return False, None
    if "conditions" not in getattr(run, "model_fields_set", frozenset()):
        return False, None
    conditions = getattr(run, "conditions", None)
    if conditions is None:
        return False, None
    return _condition_child(conditions, name, side)


def _resolve_artifact_selector(
    manifest, attached: list, run, name: str, kind: str, side: str
):
    """Resolve one explicit artifact selector, or return ``_UNSET``.

    An explicit selector never falls back. It must name exactly one artifact
    attached to the selected run by ``input_to`` and have the expected kind.
    ``Artifact.run_id`` is produced-by and never satisfies the selector.

    Fixed failures: bad selector syntax -> ``INVALID_SELECTOR``; an unknown ID
    or a missing ``input_to`` attachment -> ``DANGLING_REFERENCE``; a
    contradictory kind -> ``INPUT_SHAPE_UNSUPPORTED``.
    """
    present, selector = _explicit_artifact_selector(
        run, name, manifest.schema_version, side
    )
    if not present:
        return _UNSET
    if type(selector) is not str or not _SELECTOR_RE.fullmatch(selector):
        _raise(INVALID_SELECTOR, side)

    artifact = _artifact_by_id(manifest, selector)
    if artifact is None:
        _raise(DANGLING_REFERENCE, side)
    if getattr(artifact, "kind", None) != kind:
        _raise(INPUT_SHAPE_UNSUPPORTED, side)
    if selector not in attached:
        _raise(DANGLING_REFERENCE, side)
    return selector


def _new_partial() -> dict:
    return {"dimensions": {}, "candidates": {}}


def _add_dimension(partial: dict, name: str, first, legacy) -> None:
    """Record one first-class candidate and its legacy fallback separately.

    ``first`` and ``legacy`` are ``(present, value)`` pairs. Equality is
    deliberately not evaluated here: the shape pass must not canonicalize an
    unbounded value. ``_resolve_duplicate_conflicts`` compares these pairs only
    after every candidate has passed its bounds.
    """
    first_present, first_value = first
    legacy_present, legacy_value = legacy
    if not first_present and not legacy_present:
        return

    partial["dimensions"][name] = (
        first_value if first_present else legacy_value
    )
    partial["candidates"][name] = {
        "first": (first_present, first_value),
        "legacy": (legacy_present, legacy_value),
    }


def _record_candidate(plan: dict, name: str, source: str) -> tuple[bool, object]:
    pair = plan["candidates"].get(name)
    if pair is None:
        return False, None
    return pair[source]


def _bound_dimension_value(name: str, value, side: str) -> None:
    """Bound one already shape-checked dimension value or candidate."""
    if value is None:
        return
    if name == "environment":
        _bound_json(value, side, environment=True)
    elif name in ("evaluation_protocol", "metric_definition"):
        _bound_json(value, side, environment=False)
    else:
        _require_string_bound(value, side)


# ---------------------------------------------------------------- selection

def _validate_selector(selector, side: str) -> None:
    if type(selector) is not str or selector.strip() == "":
        _raise(INVALID_SELECTOR, side)


def _resolve_result(manifest, selector: str, side: str):
    matches = [r for r in manifest.results if getattr(r, "id", None) == selector]
    if not matches:
        _raise(RESULT_NOT_FOUND, side)
    if len(matches) > 1:
        _raise(RESULT_SELECTION_AMBIGUOUS, side)
    return matches[0]


def _resolve_run(manifest, result, side: str):
    run_id = getattr(result, "run_id", None)
    matches = [r for r in manifest.runs if getattr(r, "id", None) == run_id]
    if not matches:
        _raise(RUN_NOT_FOUND, side)
    if len(matches) > 1:
        _raise(RUN_SELECTION_AMBIGUOUS, side)
    return matches[0]


# -------------------------------------------------------- structural checks

def _require_unique_ids(manifest, side: str) -> dict:
    kinds, findings = build_index(manifest)
    if any(finding.code == Code.duplicate_id for finding in findings):
        _raise(DUPLICATE_ID, side)
    return kinds


def _append_unique(items: list, value) -> None:
    """Order-preserving dedupe without set/sort construction."""
    if value not in items:
        items.append(value)


def _artifact_by_id(manifest, identifier):
    for artifact in manifest.artifacts:
        if artifact.id == identifier:
            return artifact
    return None


def _id_exceeds_bound(value) -> bool:
    return type(value) is str and len(value) > MAX_STRING_LENGTH


def _validate_edges(manifest, kinds: dict, result, run, side: str):
    """Validate the edges the bridge relies on and collect attachments.

    Returns ``(attached_artifact_ids, parents_by_dataset_id, id_bound)``. Only
    ``input_to`` edges targeting the selected run establish dataset/checkpoint
    consumption; ``Artifact.run_id`` means produced-by and is ignored.

    Selected attachment/parent ID lengths are checked as they are collected,
    before any set/sort construction. A violation is recorded and raised only
    after that side's shape checks, preserving shape-before-bound precedence.
    """
    id_bound = False

    # A result's recorded run_id agrees with any explicit result_of edge.
    owners: list[str] = []
    for relationship in manifest.relationships:
        if _rel_type(relationship) != "result_of":
            continue
        if relationship.source != result.id:
            continue
        target = relationship.target
        if _id_exceeds_bound(target):
            id_bound = True
        if target not in kinds:
            _raise(DANGLING_REFERENCE, side)
        if kinds[target] != "run":
            _raise(DANGLING_REFERENCE, side)
        _append_unique(owners, target)

    # Attached artifacts are the sources of valid input_to edges for this run.
    attached: list[str] = []
    for relationship in manifest.relationships:
        if _rel_type(relationship) != "input_to":
            continue
        if relationship.target != run.id:
            continue
        source = relationship.source
        if _id_exceeds_bound(source):
            id_bound = True
        if source not in kinds:
            _raise(DANGLING_REFERENCE, side)
        if kinds[source] != "artifact":
            _raise(DANGLING_REFERENCE, side)
        _append_unique(attached, source)

    # Direct preprocessing ancestry: dataset -> parent artifacts.
    dataset_ids = [
        identifier
        for identifier in attached
        if getattr(_artifact_by_id(manifest, identifier), "kind", None) == "dataset"
    ]
    parents: dict[str, list[str]] = {}
    for dataset_id in dataset_ids:
        targets: list[str] = []
        for relationship in manifest.relationships:
            if _rel_type(relationship) != "derived_from":
                continue
            if relationship.source != dataset_id:
                continue
            target = relationship.target
            if _id_exceeds_bound(target):
                id_bound = True
            if target not in kinds:
                _raise(DANGLING_REFERENCE, side)
            if kinds[target] != "artifact":
                _raise(DANGLING_REFERENCE, side)
            _append_unique(targets, target)
        parents[dataset_id] = targets

    # Ownership conflicts are checked after every relied-on endpoint is known
    # to be valid (DANGLING_REFERENCE precedes CONFLICTING_OWNERSHIP).
    if len(owners) > 1:
        _raise(CONFLICTING_OWNERSHIP, side)
    if owners and owners[0] != run.id:
        _raise(CONFLICTING_OWNERSHIP, side)

    return attached, parents, id_bound


# --------------------------------------------------------------- observation

def _observation(value, dimension: str) -> dict:
    return {
        "observations": [
            {
                "value": value,
                "field": FIELDS[dimension],
                "basis": BASIS[dimension],
            }
        ]
    }


def _require_string_bound(value, side: str) -> None:
    if type(value) is str and len(value) > MAX_STRING_LENGTH:
        _raise(INPUT_BOUND_EXCEEDED, side)


def _require_json_shape(value, side: str) -> None:
    """Exact JSON-only shape check; no bounds, no copying, no subclass hooks."""
    stack = [(value, False)]
    active: set[int] = set()
    while stack:
        node, leaving = stack.pop()
        kind = type(node)
        if leaving:
            active.discard(id(node))
            continue
        if node is None or kind is bool or kind is int or kind is str:
            continue
        if kind is float:
            if not math.isfinite(node):
                _raise(INPUT_SHAPE_UNSUPPORTED, side)
            continue
        if kind is list:
            marker = id(node)
            if marker in active:
                _raise(INPUT_SHAPE_UNSUPPORTED, side)
            active.add(marker)
            stack.append((node, True))
            for item in node:
                stack.append((item, False))
            continue
        if kind is dict:
            marker = id(node)
            if marker in active:
                _raise(INPUT_SHAPE_UNSUPPORTED, side)
            active.add(marker)
            stack.append((node, True))
            for key, item in node.items():
                if type(key) is not str:
                    _raise(INPUT_SHAPE_UNSUPPORTED, side)
                stack.append((item, False))
            continue
        _raise(INPUT_SHAPE_UNSUPPORTED, side)


def _copy_json_exact(value, side: str):
    """Fresh deep copy of already shape-checked exact JSON values."""
    kind = type(value)
    if value is None or kind is bool or kind is int or kind is str:
        return value
    if kind is float:
        if not math.isfinite(value):
            _raise(INPUT_SHAPE_UNSUPPORTED, side)
        return value
    if kind is list:
        return [_copy_json_exact(item, side) for item in value]
    if kind is dict:
        # Bounds already passed; sorted exact-string keys make plain
        # json.dumps(_build_input(...)) deterministic across insertion orders.
        return {
            key: _copy_json_exact(value[key], side) for key in sorted(value)
        }
    _raise(INPUT_SHAPE_UNSUPPORTED, side)


def _bound_json(value, side: str, *, environment: bool) -> None:
    """Bound a value placed at document depth 6, before any copy is built."""
    stack = [(value, 0)]
    while stack:
        node, offset = stack.pop()
        if 6 + offset > MAX_DEPTH:
            _raise(INPUT_BOUND_EXCEEDED, side)
        kind = type(node)
        if kind is str:
            if len(node) > MAX_STRING_LENGTH:
                _raise(INPUT_BOUND_EXCEEDED, side)
            continue
        if kind is dict:
            for key, item in node.items():
                if type(key) is not str:
                    _raise(INPUT_SHAPE_UNSUPPORTED, side)
                if len(key) > MAX_STRING_LENGTH:
                    _raise(INPUT_BOUND_EXCEEDED, side)
                stack.append((item, offset + 1))
            continue
        if kind is list:
            for item in node:
                stack.append((item, offset + 1))
            continue
        if kind is float:
            if not math.isfinite(node):
                _raise(INPUT_SHAPE_UNSUPPORTED, side)
            continue
        if node is not None and kind is not bool and kind is not int:
            _raise(INPUT_SHAPE_UNSUPPORTED, side)
    try:
        size = _json_size(value)
    except (TypeError, ValueError):
        _raise(INPUT_SHAPE_UNSUPPORTED, side)
    limit = MAX_ENVIRONMENT_BYTES if environment else MAX_INPUT_BYTES
    if size > limit:
        _raise(INPUT_BOUND_EXCEEDED, side)


def _ordered_ids(identifiers) -> list[str]:
    return sorted(set(identifiers))


# ---------------------------------------------------------- dimension shapes

def _shape_dataset(dataset, parents: list[str], side: str, schema_version: str) -> dict:
    if getattr(dataset, "artifact_type", "file") == "directory":
        _raise(INPUT_SHAPE_UNSUPPORTED, side)

    metadata = _metadata_of(dataset, side)
    partial = _new_partial()

    # First-class Artifact.dataset_id requires schema 0.2; the legacy metadata
    # label remains the fallback. A null legacy label is no identity
    # observation, as in Stage 2.
    first_present = schema_version != "0.1" and (
        "dataset_id" in getattr(dataset, "model_fields_set", frozenset())
    )
    first_value = getattr(dataset, "dataset_id", None) if first_present else None
    if first_present and not _nonblank_string(first_value):
        _raise(INPUT_SHAPE_UNSUPPORTED, side)

    legacy_present, legacy_value = _metadata_entry(metadata, "dataset_id")
    if legacy_present:
        if legacy_value is None:
            legacy_present = False
        elif not _nonblank_string(legacy_value):
            _raise(INPUT_SHAPE_UNSUPPORTED, side)

    _add_dimension(
        partial,
        "dataset_identity",
        (first_present, first_value),
        (legacy_present, legacy_value),
    )

    sha256 = getattr(dataset, "sha256", None)
    volatile = bool(getattr(dataset, "volatile", False))
    if not volatile and type(sha256) is str:
        partial["dimensions"]["dataset_version"] = sha256

    if parents:
        partial["dimensions"]["preprocessing"] = parents

    return partial


def _shape_checkpoint(checkpoint, side: str) -> dict:
    if getattr(checkpoint, "artifact_type", "file") == "directory":
        _raise(INPUT_SHAPE_UNSUPPORTED, side)
    partial = _new_partial()
    sha256 = getattr(checkpoint, "sha256", None)
    volatile = bool(getattr(checkpoint, "volatile", False))
    if not volatile and type(sha256) is str:
        partial["dimensions"]["model_checkpoint"] = sha256
    return partial


def _shape_protocol(protocol, side: str) -> dict:
    _require_json_shape(protocol, side)
    if not set(protocol) <= PROTOCOL_KEYS:
        _raise(INPUT_SHAPE_UNSUPPORTED, side)
    protocol_id = protocol.get("protocol_id")
    if not _nonblank_string(protocol_id):
        _raise(INPUT_SHAPE_UNSUPPORTED, side)
    if "version" in protocol and not _int_or_nonblank_string(protocol["version"]):
        _raise(INPUT_SHAPE_UNSUPPORTED, side)
    return protocol


def _shape_definition(definition, side: str) -> dict:
    _require_json_shape(definition, side)
    if len(definition) == 0 or set(definition) <= NAME_ONLY_KEYS:
        # Name-only labels remain diagnostics; they still reach the core, but
        # only after exact JSON shape and bound checks.
        return definition
    if not set(definition) <= METRIC_DEFINITION_KEYS:
        _raise(INPUT_SHAPE_UNSUPPORTED, side)
    definition_id = definition.get("definition_id")
    if not _nonblank_string(definition_id):
        _raise(INPUT_SHAPE_UNSUPPORTED, side)
    if "higher_is_better" in definition and type(definition["higher_is_better"]) is not bool:
        _raise(INPUT_SHAPE_UNSUPPORTED, side)
    if "averaging" in definition and not _nonblank_string(definition["averaging"]):
        _raise(INPUT_SHAPE_UNSUPPORTED, side)
    if "version" in definition and not _int_or_nonblank_string(definition["version"]):
        _raise(INPUT_SHAPE_UNSUPPORTED, side)
    return definition


def _shape_environment(environment, side: str) -> dict:
    if type(environment) is not dict:
        _raise(INPUT_SHAPE_UNSUPPORTED, side)
    if not environment:
        _raise(INPUT_SHAPE_UNSUPPORTED, side)
    _require_json_shape(environment, side)
    if _strict_json_error(environment, MAX_ENVIRONMENT_DEPTH) is not None:
        _raise(INPUT_SHAPE_UNSUPPORTED, side)
    return environment


def _legacy_run_dimensions(metadata: dict, side: str) -> dict:
    """Shape the Stage 2 legacy run metadata dimensions."""
    dimensions: dict = {}

    present, evaluation = _metadata_entry(metadata, "evaluation")
    split_present = protocol_present = False
    split = protocol = None
    if present:
        if type(evaluation) is not dict:
            _raise(INPUT_SHAPE_UNSUPPORTED, side)
        split_present, split = _metadata_entry(evaluation, "split")
        if split_present and split is not None and type(split) is not str:
            _raise(INPUT_SHAPE_UNSUPPORTED, side)
        protocol_present, protocol = _metadata_entry(evaluation, "protocol")
        if protocol_present and protocol is not None:
            if type(protocol) is not dict:
                _raise(INPUT_SHAPE_UNSUPPORTED, side)
            protocol = _shape_protocol(protocol, side)
    dimensions["evaluation_split"] = (split_present, split)
    dimensions["evaluation_protocol"] = (protocol_present, protocol)

    present, revision = _metadata_entry(metadata, "source_revision")
    if present and revision is not None and type(revision) is not str:
        _raise(INPUT_SHAPE_UNSUPPORTED, side)
    dimensions["source_revision"] = (present, revision)

    present, environment = _metadata_entry(metadata, "environment")
    if present and environment is not None:
        environment = _shape_environment(environment, side)
    dimensions["environment"] = (present, environment)

    return dimensions


def _first_class_run_dimension(conditions, name: str, side: str):
    """Shape one first-class run condition as a ``(present, value)`` pair."""
    present, value = _condition_child(conditions, name, side)
    if not present:
        return False, None

    if name == "evaluation_split":
        if value is None:
            return True, None
        if type(value) is not str or value.strip() == "":
            _raise(INPUT_SHAPE_UNSUPPORTED, side)
        return True, value
    if name == "evaluation_protocol":
        if value is None:
            return True, None
        if isinstance(value, EvaluationProtocol):
            value = value.model_dump()
        elif type(value) is not dict:
            _raise(INPUT_SHAPE_UNSUPPORTED, side)
        return True, _shape_protocol(value, side)
    if name == "source_revision":
        if value is None:
            return True, None
        if type(value) is not str or value.strip() == "":
            _raise(INPUT_SHAPE_UNSUPPORTED, side)
        return True, value
    if name == "environment":
        if value is None:
            return True, None
        return True, _shape_environment(value, side)
    _raise(INPUT_SHAPE_UNSUPPORTED, side)


def _shape_run(run, side: str, schema_version: str) -> dict:
    metadata = _metadata_of(run, side)
    legacy = _legacy_run_dimensions(metadata, side)
    conditions = _conditions_of(run, schema_version, side)

    partial = _new_partial()
    for name in (
        "evaluation_split",
        "evaluation_protocol",
        "source_revision",
        "environment",
    ):
        first = (
            (False, None)
            if conditions is None
            else _first_class_run_dimension(conditions, name, side)
        )
        _add_dimension(partial, name, first, legacy[name])
    return partial


def _shape_result(result, side: str, schema_version: str) -> dict:
    metadata = _metadata_of(result, side)
    legacy_present, legacy_definition = _metadata_entry(
        metadata, "metric_definition"
    )
    if legacy_present and legacy_definition is not None:
        if type(legacy_definition) is not dict:
            _raise(INPUT_SHAPE_UNSUPPORTED, side)
        legacy_definition = _shape_definition(legacy_definition, side)

    first_present = schema_version != "0.1" and (
        "metric_definition"
        in getattr(result, "model_fields_set", frozenset())
    )
    if first_present:
        first_value = getattr(result, "metric_definition", None)
        if first_value is None:
            first = (True, None)
        elif isinstance(first_value, MetricDefinition):
            first = (True, _shape_definition(first_value.model_dump(), side))
        elif type(first_value) is dict:
            first = (True, _shape_definition(first_value, side))
        else:
            _raise(INPUT_SHAPE_UNSUPPORTED, side)
    else:
        first = (False, None)

    partial = _new_partial()
    _add_dimension(
        partial,
        "metric_definition",
        first,
        (legacy_present, legacy_definition),
    )
    return partial


# ------------------------------------------------------------- side pipeline

def _analyse_side(loaded: LoadedManifest, selector, side: str) -> dict:
    _validate_selector(selector, side)
    manifest = loaded.manifest

    result = _resolve_result(manifest, selector, side)
    run = _resolve_run(manifest, result, side)
    kinds = _require_unique_ids(manifest, side)
    attached, parents, id_bound = _validate_edges(
        manifest, kinds, result, run, side
    )
    dataset_selection = _resolve_artifact_selector(
        manifest, attached, run, "evaluation_dataset_artifact_id", "dataset", side
    )
    checkpoint_selection = _resolve_artifact_selector(
        manifest, attached, run, "model_checkpoint_artifact_id", "checkpoint", side
    )
    # Selection IDs are deferred bound candidates so shape checks still win.
    id_bound = (
        id_bound
        or _id_exceeds_bound(result.id)
        or _id_exceeds_bound(run.id)
    )
    return {
        "manifest": manifest,
        "result": result,
        "run": run,
        "attached": attached,
        "parents": parents,
        "dataset_selection": dataset_selection,
        "checkpoint_selection": checkpoint_selection,
        "id_bound": id_bound,
    }


def _shape_side(context: dict, side: str) -> dict:
    manifest = context["manifest"]
    result = context["result"]
    run = context["run"]
    attached = context["attached"]
    parents = context["parents"]
    schema_version = manifest.schema_version

    datasets = [
        identifier
        for identifier in attached
        if getattr(_artifact_by_id(manifest, identifier), "kind", None) == "dataset"
    ]
    checkpoints = [
        identifier
        for identifier in attached
        if getattr(_artifact_by_id(manifest, identifier), "kind", None) == "checkpoint"
    ]

    # Bounded traversals only: the selected result/run and attached artifacts.
    # Metadata of unselected attached datasets is still scanned.
    scanned = [result, run] + [
        _artifact_by_id(manifest, identifier) for identifier in attached
    ]
    for obj in scanned:
        metadata = _metadata_of(obj, side)
        if _contains_nonfinite(metadata):
            _raise(INPUT_SHAPE_UNSUPPORTED, side)

    dataset_selection = context["dataset_selection"]
    if dataset_selection is _UNSET:
        if len(datasets) > 1:
            _raise(INPUT_SHAPE_UNSUPPORTED, side)
        selected_dataset = datasets[0] if datasets else None
    else:
        selected_dataset = dataset_selection

    checkpoint_selection = context["checkpoint_selection"]
    if checkpoint_selection is _UNSET:
        if len(checkpoints) > 1:
            _raise(INPUT_SHAPE_UNSUPPORTED, side)
        selected_checkpoint = checkpoints[0] if checkpoints else None
    else:
        selected_checkpoint = checkpoint_selection

    partial = _new_partial()
    if selected_dataset is not None:
        dataset = _artifact_by_id(manifest, selected_dataset)
        partial = _merge_partial(
            partial,
            _shape_dataset(
                dataset,
                parents[selected_dataset],
                side,
                schema_version,
            ),
        )
    if selected_checkpoint is not None:
        partial = _merge_partial(
            partial,
            _shape_checkpoint(_artifact_by_id(manifest, selected_checkpoint), side),
        )

    partial = _merge_partial(partial, _shape_run(run, side, schema_version))
    partial = _merge_partial(partial, _shape_result(result, side, schema_version))

    name = getattr(result, "name", None)
    if name is None:
        metric_name = None
    elif type(name) is str:
        metric_name = name
    else:
        _raise(INPUT_SHAPE_UNSUPPORTED, side)

    return {
        "dimensions": partial["dimensions"],
        "candidates": partial["candidates"],
        "metric_name": metric_name,
        "attached": attached,
        "parents": parents,
        "id_bound": context["id_bound"],
    }


def _merge_partial(left: dict, right: dict) -> dict:
    for name in ("dimensions", "candidates"):
        left[name].update(right[name])
    return left


def _preprocessing_includes_hash(artifact) -> bool:
    sha256 = getattr(artifact, "sha256", None)
    return (
        getattr(artifact, "artifact_type", "file") == "file"
        and not bool(getattr(artifact, "volatile", False))
        and type(sha256) is str
    )


def _record_from_plan(
    plan: dict,
    context: dict,
    side: str,
    *,
    sort_parents: bool,
    copy_values: bool,
) -> dict:
    manifest = context["manifest"]
    dimensions: dict = {}

    for name in DIMENSION_ORDER:
        if name not in plan["dimensions"]:
            continue
        raw = plan["dimensions"][name]
        if name == "preprocessing":
            parent_ids = _ordered_ids(raw) if sort_parents else raw
            value = []
            for parent_id in parent_ids:
                item = {"id": parent_id}
                artifact = _artifact_by_id(manifest, parent_id)
                if _preprocessing_includes_hash(artifact):
                    item["sha256"] = getattr(artifact, "sha256")
                value.append(item)
        elif name in ("evaluation_protocol", "metric_definition", "environment"):
            if raw is None:
                value = None
            elif copy_values:
                value = _copy_json_exact(raw, side)
            else:
                value = raw
        else:
            value = raw
        dimensions[name] = _observation(value, name)

    record: dict = {
        "run_id": context["run"].id,
        "result_id": context["result"].id,
    }
    if plan["metric_name"] is not None:
        record["metric_name"] = plan["metric_name"]
    record["dimensions"] = dimensions
    return record


def _bound_side(plan: dict, context: dict, side: str) -> dict:
    """All bounds for one side, after shape checks and before copying/sorting.

    Returns a provisional unsorted, uncopied record used for the size bound.
    """
    if plan["id_bound"]:
        _raise(INPUT_BOUND_EXCEEDED, side)

    manifest = context["manifest"]
    dimensions = plan["dimensions"]

    # Bound every duplicate candidate first, using iterative checks. An
    # oversized or over-deep first-class or legacy value cannot be
    # canonicalized unbounded and a bound wins over a conflict error.
    for name in DIMENSION_ORDER:
        for source in ("first", "legacy"):
            present, value = _record_candidate(plan, name, source)
            if present and value is not None:
                _bound_dimension_value(name, value, side)

    for name in DIMENSION_ORDER:
        if name not in dimensions:
            continue
        value = dimensions[name]
        if name == "preprocessing":
            for parent_id in value:
                _require_string_bound(parent_id, side)
                artifact = _artifact_by_id(manifest, parent_id)
                sha256 = getattr(artifact, "sha256", None)
                if _preprocessing_includes_hash(artifact):
                    _require_string_bound(sha256, side)
        else:
            _bound_dimension_value(name, value, side)

    if plan["metric_name"] is not None:
        _require_string_bound(plan["metric_name"], side)

    # The aggregate provisional record bound also applies before duplicate
    # conflicts. The provisional record uses the chosen (first-class) value and
    # is measured iteratively, so a large preprocessing fan-out cannot be
    # reclassified by a small conflict in another dimension.
    provisional = _record_from_plan(
        plan, context, side, sort_parents=False, copy_values=False
    )
    _bound_record(provisional, side)
    try:
        size = _json_size(provisional)
    except (TypeError, ValueError):
        _raise(INPUT_SHAPE_UNSUPPORTED, side)
    if size > MAX_INPUT_BYTES:
        _raise(INPUT_BOUND_EXCEEDED, side)

    # Canonical equality runs only now, after every applicable per-dimension
    # and aggregate bound.
    _resolve_duplicate_conflicts(plan, side)
    return provisional


def _resolve_duplicate_conflicts(plan: dict, side: str) -> None:
    """Compare first-class and legacy duplicates after all bounds passed.

    Equal duplicates collapse to the single chosen observation. Unequal or
    malformed duplicates are an inconsistent recording.
    """
    for name in DIMENSION_ORDER:
        first_present, first_value = _record_candidate(plan, name, "first")
        legacy_present, legacy_value = _record_candidate(plan, name, "legacy")
        if not (first_present and legacy_present):
            continue
        try:
            equal = _canonical_equal(first_value, legacy_value)
        except (RecursionError, TypeError, ValueError):
            _raise(INPUT_SHAPE_UNSUPPORTED, side)
        if not equal:
            _raise(INPUT_SHAPE_UNSUPPORTED, side)


def _materialize_side(
    plan: dict, context: dict, side: str, provisional: dict
) -> dict:
    # The provisional record already passed every bound. Only now sort parents
    # and make the deterministic checked JSON copies.
    return _record_from_plan(
        plan, context, side, sort_parents=True, copy_values=True
    )


def _build_input(
    baseline: LoadedManifest,
    target: LoadedManifest,
    *,
    baseline_result_id: str,
    target_result_id: str,
) -> dict:
    """Private test-facing builder: raw synthetic comparability input.

    Never log, render or expose this document as a user API.
    """
    baseline_context = _analyse_side(baseline, baseline_result_id, "baseline")
    baseline_plan = _shape_side(baseline_context, "baseline")
    baseline_provisional = _bound_side(
        baseline_plan, baseline_context, "baseline"
    )

    target_context = _analyse_side(target, target_result_id, "target")
    target_plan = _shape_side(target_context, "target")
    target_provisional = _bound_side(target_plan, target_context, "target")

    # No provisional path may copy or sort. Only after both sides and their
    # combination pass every bound do the deterministic materialisations run.
    combined = {
        "format": FORMAT,
        "baseline": baseline_provisional,
        "target": target_provisional,
    }
    try:
        combined_size = _json_size(combined)
    except (TypeError, ValueError):
        _raise(INPUT_SHAPE_UNSUPPORTED, "target")
    if combined_size > MAX_INPUT_BYTES:
        _raise(INPUT_BOUND_EXCEEDED, "target")

    baseline_record = _materialize_side(
        baseline_plan, baseline_context, "baseline", baseline_provisional
    )
    target_record = _materialize_side(
        target_plan, target_context, "target", target_provisional
    )

    return {
        "format": FORMAT,
        "baseline": baseline_record,
        "target": target_record,
    }


# -------------------------------------------------------------------- bounds

def _bound_record(record, side: str) -> None:
    dimensions = record.get("dimensions") if type(record) is dict else None
    if type(dimensions) is dict:
        for name in sorted(dimensions):
            entry = dimensions[name]
            observations = entry.get("observations") if type(entry) is dict else None
            if type(observations) is list and len(observations) > MAX_OBSERVATIONS_PER_DIMENSION:
                _raise(INPUT_BOUND_EXCEEDED, side)

    # Iterative walk: strings (keys and values) and container depth.
    stack = [(record, 0)]
    seen: set[int] = set()
    while stack:
        node, depth = stack.pop()
        if depth > MAX_DEPTH:
            _raise(INPUT_BOUND_EXCEEDED, side)
        kind = type(node)
        if kind is str:
            if len(node) > MAX_STRING_LENGTH:
                _raise(INPUT_BOUND_EXCEEDED, side)
            continue
        if kind is dict:
            marker = id(node)
            if marker in seen:
                continue
            seen.add(marker)
            for key, value in node.items():
                if type(key) is str and len(key) > MAX_STRING_LENGTH:
                    _raise(INPUT_BOUND_EXCEEDED, side)
                stack.append((value, depth + 1))
        elif kind is list:
            marker = id(node)
            if marker in seen:
                continue
            seen.add(marker)
            for value in node:
                stack.append((value, depth + 1))


def _require_bounds(document) -> None:
    """Enforce Stage 1 bounds; never truncate. Private and test-facing."""
    for side in SIDES:
        record = document.get(side) if type(document) is dict else None
        if type(record) is dict:
            _bound_record(record, side)

    try:
        size = _json_size(document)
    except (TypeError, ValueError):
        # A non-JSON passthrough value is left for the core to reject with its
        # own sanitised projection; bounds only apply to representable input.
        return
    if size <= MAX_INPUT_BYTES:
        return
    probe = {
        "format": document.get("format") if type(document) is dict else None,
        "baseline": document.get("baseline") if type(document) is dict else None,
        "target": {"dimensions": {}},
    }
    try:
        baseline_size = _json_size(probe)
    except (TypeError, ValueError):
        baseline_size = 0
    _raise(
        INPUT_BOUND_EXCEEDED,
        "baseline" if baseline_size > MAX_INPUT_BYTES else "target",
    )


# ------------------------------------------------------------------- public

def compare_recorded_conditions(
    baseline: LoadedManifest,
    target: LoadedManifest,
    *,
    baseline_result_id: str,
    target_result_id: str,
) -> dict:
    """Return the PC26 public projection, or raise ComparabilityMappingError.

    Both result IDs are required. There is no name-based or automatic
    selection, no trimming and no case folding. Results are resolved first and
    the owning run is derived from ``result.run_id``; the first run is never
    used.

    Schema 0.1 keeps the Stage 2 legacy metadata mapping exactly. Schema 0.2
    resolves each dimension independently from first-class fields, explicit
    artifact selectors and legacy fallbacks.
    """
    failed_internal = False
    projection = None
    try:
        document = _build_input(
            baseline,
            target,
            baseline_result_id=baseline_result_id,
            target_result_id=target_result_id,
        )
        _require_bounds(document)
        projection = comparability.evaluate(document)
    except ComparabilityMappingError:
        raise
    except Exception:
        failed_internal = True
    if failed_internal:
        # Constructed and raised outside the handler: no retained cause,
        # context, argument or attribute from the original exception.
        _raise(INTERNAL_MAPPING_ERROR, None)
    return projection


def side_recording_summary(loaded: LoadedManifest, selector: str, side: str) -> dict:
    """Run one side's mapping/shape/bound pipeline for preflight listing.

    Returns a fixed-shape internal summary::

        {"code": None, "attachment": bool,
         "states": {dimension: "present" | "recorded-unknown" | "missing"}}

    ``code`` is the fixed bridge mapping code when that candidate cannot be
    mapped/bounded, in which case ``states`` is empty. The recording state is
    separate from usability: a present-but-ambiguous or present-but-conflicting
    value is ``present`` here; the core's readiness check handles usability.
    This runs the bridge's own ``_analyse_side``/``_shape_side``/``_bound_side``;
    it does not copy their logic and reads no artifact bytes.
    """
    if side not in SIDES:
        return {"code": INTERNAL_MAPPING_ERROR, "attachment": False, "states": {}}
    try:
        context = _analyse_side(loaded, selector, side)
        plan = _shape_side(context, side)
        record = _bound_side(plan, context, side)
    except ComparabilityMappingError as error:
        code = error.code
        if type(code) is not str or code not in _MAPPING_CODES:
            code = INTERNAL_MAPPING_ERROR
        return {"code": code, "attachment": False, "states": {}}
    except Exception:
        return {"code": INTERNAL_MAPPING_ERROR, "attachment": False, "states": {}}

    states: dict[str, str] = {}
    dimensions = record.get("dimensions") if type(record) is dict else None
    if type(dimensions) is dict:
        for name, entry in dimensions.items():
            observations = (
                entry.get("observations") if type(entry) is dict else None
            )
            if type(observations) is not list or not observations:
                states[name] = "missing"
                continue
            values = [
                observation.get("value")
                for observation in observations
                if type(observation) is dict
            ]
            if not values or all(value is None for value in values):
                states[name] = "recorded-unknown"
            else:
                states[name] = "present"
    attachment = (
        states.get("dataset_identity") == "present"
        and states.get("dataset_version") == "present"
    )
    return {"code": None, "attachment": attachment, "states": states}
