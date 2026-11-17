"""Author recorded run conditions on an existing schema 0.2-0.10 manifest.

This module is the core transformation of the ``conditions`` command. It owns
one validated load, one bounded patch read from a local file or binary stdin,
strict JSON parsing and bounds, an independently decoded candidate, the
set/clear/null matrix, patch-supplied selector validation, the preservation
proof, serialization eligibility, the reviewed migration backup generation and
the two stale checkpoints. The command layer owns argument parsing, output and
exit codes.

Nothing is printed. No artifact bytes are read or statted, no adapter, check,
hash, subprocess or network is used, no application configuration is
discovered from the environment, and the target folder is enumerated only for
a real changed write.

The only semantic change is the selected run's ``conditions`` subtree, and only
as the supplied patch and clear fields request. Every other loader-decoded
value, type, presence and object-key order is preserved.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from .manifest import (
    ManifestLoadError,
    _finite_float,
    _unique_object,
    dump_raw,
    load_manifest,
    save_raw,
    validate_manifest,
)
from .migrate import (
    MigrationError,
    _backup_for_migration,
    _classify_late_failure,
    _read_pre_backup,
    _reject_reserved_target,
    _target_mode,
    decode_snapshot,
    strict_equal,
)
from .models import Manifest, RelationshipType

# Patch and value bounds. These mirror the accepted comparison limits; the
# comparison core is deliberately not imported by production authoring code.
MAX_PATCH_BYTES = 65536
MAX_PATCH_DEPTH = 9
MAX_STRING_LENGTH = 1024
MAX_ENVIRONMENT_DEPTH = 8
MAX_ENVIRONMENT_BYTES = 16 * 1024
MAX_SAFE_INT = 9007199254740991
MAX_ABS_FLOAT = 1e100
MAX_CONTAINER_WIDTH = 128
MAX_NODES = 4096

SUPPORTED_VERSIONS = ("0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8", "0.9", "0.10", "0.11")

# Fixed field order: operation order, new-child order and result array order.
FIELDS: tuple[str, ...] = (
    "evaluation_split",
    "evaluation_protocol",
    "evaluation_dataset_artifact_id",
    "model_checkpoint_artifact_id",
    "source_revision",
    "environment",
)

# Selector field and the artifact kind it must name.
SELECTORS: tuple[tuple[str, str], ...] = (
    ("evaluation_dataset_artifact_id", "dataset"),
    ("model_checkpoint_artifact_id", "checkpoint"),
)

# The core has exactly this closed set of outcomes. Grammar codes such as
# INVALID_ARGUMENTS belong to the command layer and are never produced here.
CONDITIONS_ERROR_CODES = frozenset(
    {
        "UNSUPPORTED_VERSION",
        "RESERVED_PATH",
        "MANIFEST_LOAD_FAILED",
        "RUN_SELECTION_INVALID",
        "PATCH_INVALID",
        "SELECTOR_INVALID",
        "SERIALIZATION_FAILED",
        "READ_FAILED",
        "STALE_MANIFEST",
        "WRITE_FAILED",
        "INTERNAL_ERROR",
    }
)

_MISSING = object()


class ConditionsError(Exception):
    """A refused conditions update with a stable, closed error code.

    The exception text is deliberately generic: callers receive the code and
    render fixed prose. Paths, run IDs, field names, values, exception messages
    and tracebacks never travel through this type.
    """

    def __init__(self, code: str, *, version: Optional[str] = None) -> None:
        if code not in CONDITIONS_ERROR_CODES:
            raise ValueError("unknown conditions error code")
        self.code = code
        # The declared source schema, for version-keyed command text. It never
        # carries a path, ID, field name or value.
        self.version = version
        super().__init__("conditions update failed")


@dataclass(frozen=True)
class ConditionsResult:
    """The outcome of one conditions invocation.

    ``set_fields`` and ``cleared_fields`` are the requested operations in fixed
    field order; ``changed`` reports whether the document actually changed.
    ``backup_path`` is set only when a backup was created or reused. JSON
    rendering must ignore it; text rendering may show it.
    """

    run_id: str
    changed: bool
    dry_run: bool
    backup: str
    backup_path: Optional[Path]
    set_fields: tuple[str, ...]
    cleared_fields: tuple[str, ...]


def author_conditions(
    path: Path,
    *,
    run_id: str,
    patch_path: Optional[str],
    clears: Sequence[str],
    dry_run: bool = False,
    stdin=None,
) -> ConditionsResult:
    """Set or clear recorded conditions for one run of a schema 0.2 manifest.

    ``patch_path`` is the raw option string: the exact token ``-`` means binary
    stdin, while ``./-`` and every other string name a file. ``stdin`` defaults
    to ``sys.stdin.buffer`` and may be replaced by a binary stream in tests.
    ``clears`` is a sequence of field names; the command layer rejects unknown
    or duplicate names as grammar before calling this core.

    Refusals raise ``ConditionsError`` with a closed code. A successful real
    run has already replaced the manifest; a successful dry run or no-op wrote
    nothing.
    """
    try:
        return _author_conditions(
            Path(path),
            run_id=run_id,
            patch_path=patch_path,
            clears=clears,
            dry_run=dry_run,
            stdin=stdin,
        )
    except ConditionsError:
        raise
    except MigrationError as exc:
        raise ConditionsError(_translated_code(exc.code)) from None
    except Exception:
        # Sanitized boundary: never leak an exception message, path or value.
        raise ConditionsError("INTERNAL_ERROR") from None


def _translated_code(code: str) -> str:
    """Map a reused migration code by name only; unknown codes are internal."""
    if code in CONDITIONS_ERROR_CODES:
        return code
    return "INTERNAL_ERROR"


def _author_conditions(
    target: Path,
    *,
    run_id: str,
    patch_path: Optional[str],
    clears: Sequence[str],
    dry_run: bool,
    stdin,
) -> ConditionsResult:
    cleared = _normalize_clears(clears)
    if patch_path is None and not cleared:
        # The command layer requires at least one operation as grammar. Reaching
        # the core with neither is a caller-contract defect, not patch content.
        raise ConditionsError("INTERNAL_ERROR")

    _reject_reserved_target(target)

    try:
        loaded = load_manifest(target)
    except (ManifestLoadError, OSError, RecursionError):
        raise ConditionsError("MANIFEST_LOAD_FAILED") from None

    if loaded.manifest.schema_version not in SUPPORTED_VERSIONS:
        # The version selects the fixed command text; it is not echoed alone.
        raise ConditionsError(
            "UNSUPPORTED_VERSION", version=loaded.manifest.schema_version
        )

    run_index = _select_run(loaded.manifest, run_id)

    # Patch acquisition deliberately follows the version and run-selection
    # checks: a manifest that cannot accept the edit must not read secret patch
    # values or block on stdin.
    if patch_path is None:
        patch: dict = {}
    else:
        patch = _parse_patch(_read_patch(patch_path, stdin))
        for field in cleared:
            if field in patch:
                # Setting and clearing one field is an ambiguous whole-patch
                # request and refuses the invocation.
                raise ConditionsError("PATCH_INVALID")

    # Two independent decodes: source_a is the immutable baseline, source_b
    # supplies every candidate value. Never share containers between them.
    source_a = decode_snapshot(loaded.source_bytes)
    source_b = decode_snapshot(loaded.source_bytes)
    candidate = _build_candidate(source_b, run_index, run_id, patch, cleared)

    manifest = _validate_candidate(candidate)
    _validate_supplied_values(patch)
    _validate_selectors(patch, manifest, run_id)

    requested_sets = tuple(field for field in FIELDS if field in patch)
    changed = not strict_equal(source_a, candidate)
    if not changed:
        # No-op success before preservation, serialization and the pre-backup
        # read: no backup, no write, no backup enumeration.
        return ConditionsResult(
            run_id=run_id,
            changed=False,
            dry_run=dry_run,
            backup="none",
            backup_path=None,
            set_fields=requested_sets,
            cleared_fields=cleared,
        )

    _prove_preservation(source_a, candidate, run_index, patch, cleared)
    _serialize_candidate(candidate)

    # First stale checkpoint: immediately before the backup stage. A dry run
    # performs this read too, but never creates a backup or writes anything.
    current = _read_pre_backup(loaded.path)
    if current != loaded.source_bytes:
        raise ConditionsError("STALE_MANIFEST")
    if dry_run:
        return ConditionsResult(
            run_id=run_id,
            changed=True,
            dry_run=True,
            backup="none",
            backup_path=None,
            set_fields=requested_sets,
            cleared_fields=cleared,
        )

    source_mode = _target_mode(loaded.path)
    backup, backup_path = _backup_for_migration(
        loaded.path, loaded.source_bytes, source_mode
    )

    try:
        save_raw(candidate, loaded.path, expected_bytes=loaded.source_bytes)
    except OSError:
        # manifest.py suppresses a cleanup-only error after a committed
        # replace; any OSError here is non-committed. Classify by observing the
        # bytes now present, never by matching exception text.
        _classify_late_failure(loaded.path, loaded.source_bytes)
    except MigrationError:
        raise
    except Exception:
        raise ConditionsError("INTERNAL_ERROR") from None

    return ConditionsResult(
        run_id=run_id,
        changed=True,
        dry_run=False,
        backup=backup,
        backup_path=backup_path,
        set_fields=requested_sets,
        cleared_fields=cleared,
    )


def _normalize_clears(clears: Sequence[str]) -> tuple[str, ...]:
    """Return the requested clear fields in fixed field order.

    Unknown or duplicate names are a caller-contract defect: the command layer
    owns argument grammar and refuses them as INVALID_ARGUMENTS before this
    core runs. They are never silently ignored here.
    """
    seen: set[str] = set()
    for field in clears:
        if type(field) is not str or field not in FIELDS or field in seen:
            raise ConditionsError("INTERNAL_ERROR")
        seen.add(field)
    return tuple(field for field in FIELDS if field in seen)


def _select_run(manifest: Manifest, run_id: str) -> int:
    matches = [index for index, run in enumerate(manifest.runs) if run.id == run_id]
    if len(matches) != 1:
        raise ConditionsError("RUN_SELECTION_INVALID")
    return matches[0]


# --------------------------------------------------------------------------
# Patch acquisition and strict parsing
# --------------------------------------------------------------------------


def _read_patch(patch_path: str, stdin) -> bytes:
    """Read at most MAX_PATCH_BYTES + 1 bytes from a file or binary stdin."""
    if patch_path == "-":
        stream = stdin if stdin is not None else _default_stdin()
        if stream is None:
            # With stdin closed (``<&-``), Python sets sys.stdin to None. That
            # is a local input read failure, not an internal defect.
            raise ConditionsError("READ_FAILED")
        try:
            data = stream.read(MAX_PATCH_BYTES + 1)
        except (OSError, ValueError, AttributeError):
            # ValueError covers a closed stream; AttributeError covers a
            # stream object without a readable ``read``.
            raise ConditionsError("READ_FAILED") from None
    else:
        try:
            with open(Path(patch_path), "rb") as handle:
                data = handle.read(MAX_PATCH_BYTES + 1)
        except (OSError, ValueError, AttributeError):
            raise ConditionsError("READ_FAILED") from None

    if isinstance(data, str) or not isinstance(data, (bytes, bytearray)):
        raise ConditionsError("READ_FAILED")
    data = bytes(data)
    if len(data) > MAX_PATCH_BYTES:
        raise ConditionsError("PATCH_INVALID")
    return data


def _default_stdin():
    stream = getattr(sys.stdin, "buffer", None)
    return stream if stream is not None else sys.stdin


def _reject_nonfinite_constant(value: str):
    raise ValueError("patch contains a non-finite number")


def _parse_patch(data: bytes) -> dict:
    """Decode and parse one strict JSON patch object."""
    if data.startswith(b"\xef\xbb\xbf"):
        raise ConditionsError("PATCH_INVALID")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ConditionsError("PATCH_INVALID") from None
    try:
        document = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_nonfinite_constant,
            parse_float=_finite_float,
        )
    except (ValueError, RecursionError):
        # Also covers duplicate keys, the interpreter integer parse limit, a
        # BOM left in the text, trailing data, NaN/Infinity and parse overflow.
        raise ConditionsError("PATCH_INVALID") from None

    if type(document) is not dict:
        raise ConditionsError("PATCH_INVALID")
    _check_patch_depth(document)
    _check_patch_keys(document)
    # Reject any lone surrogate before it can reach a model or a serializer.
    _check_patch_strings_utf8(document)
    return document


def _check_patch_depth(document: dict) -> None:
    """Container depth counted from the root object; scalars add no level."""
    stack: list[tuple[object, int]] = [(document, 1)]
    while stack:
        node, depth = stack.pop()
        node_type = type(node)
        if node_type is dict:
            if depth > MAX_PATCH_DEPTH:
                raise ConditionsError("PATCH_INVALID")
            for value in node.values():
                value_type = type(value)
                if value_type is dict or value_type is list:
                    stack.append((value, depth + 1))
        elif node_type is list:
            if depth > MAX_PATCH_DEPTH:
                raise ConditionsError("PATCH_INVALID")
            for value in node:
                value_type = type(value)
                if value_type is dict or value_type is list:
                    stack.append((value, depth + 1))


def _check_patch_keys(document: dict) -> None:
    for key in document:
        if key not in FIELDS:
            raise ConditionsError("PATCH_INVALID")


def _check_patch_strings_utf8(document: object) -> None:
    stack: list[object] = [document]
    while stack:
        node = stack.pop()
        node_type = type(node)
        if node_type is str:
            _check_utf8(node)
        elif node_type is dict:
            for key, value in node.items():
                _check_utf8(key)
                stack.append(value)
        elif node_type is list:
            stack.extend(node)


def _check_utf8(value: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ConditionsError("PATCH_INVALID") from None


# --------------------------------------------------------------------------
# Candidate construction
# --------------------------------------------------------------------------


def _build_candidate(
    source: dict,
    run_index: int,
    run_id: str,
    patch: dict,
    cleared: tuple[str, ...],
) -> dict:
    """Mutate the selected run's conditions dict in place and return source."""
    runs = source.get("runs")
    if type(runs) is not list or run_index >= len(runs):
        raise ConditionsError("INTERNAL_ERROR")
    target_run = runs[run_index]
    if type(target_run) is not dict or target_run.get("id") != run_id:
        raise ConditionsError("INTERNAL_ERROR")

    operations_requested = bool(patch) or bool(cleared)
    if not operations_requested:
        return source

    if "conditions" in target_run:
        conditions = target_run["conditions"]
        if type(conditions) is not dict:
            raise ConditionsError("INTERNAL_ERROR")
        parent_created = False
    elif patch:
        # A new parent is appended as the target run's last key.
        conditions = {}
        target_run["conditions"] = conditions
        parent_created = True
    else:
        # Clears only: an absent parent is left absent and nothing changes.
        return source

    # Clears first, then sets in fixed field order; the existing dict is
    # mutated in place and never rebuilt.
    for field in cleared:
        conditions.pop(field, None)
    for field in FIELDS:
        if field in patch:
            conditions[field] = patch[field]

    if parent_created and not conditions:
        # Unreachable for a patch with any key, but never leave {} behind.
        del target_run["conditions"]
    elif not conditions:
        del target_run["conditions"]
    return source


def _validate_candidate(candidate: dict) -> Manifest:
    try:
        manifest = validate_manifest(candidate)
    except Exception:
        # The loaded source already validated, so the patch introduced the
        # invalidity. No partial repair and no write.
        raise ConditionsError("PATCH_INVALID") from None
    if manifest.schema_version not in SUPPORTED_VERSIONS:
        raise ConditionsError("INTERNAL_ERROR")
    return manifest


# --------------------------------------------------------------------------
# Patch-supplied value bounds
# --------------------------------------------------------------------------


def _validate_supplied_values(patch: dict) -> None:
    for field, value in patch.items():
        _validate_supplied_value(value)
        if field == "environment" and value is not None:
            _validate_environment(value)


def _validate_supplied_value(root: object) -> None:
    """Per-value node, width, string, UTF-8 and number bounds."""
    nodes = 0
    stack: list[object] = [root]
    while stack:
        node = stack.pop()
        nodes += 1
        if nodes > MAX_NODES:
            raise ConditionsError("PATCH_INVALID")
        node_type = type(node)
        if node is None or node_type is bool:
            continue
        if node_type is int:
            if abs(node) > MAX_SAFE_INT:
                raise ConditionsError("PATCH_INVALID")
            continue
        if node_type is float:
            if not math.isfinite(node) or abs(node) > MAX_ABS_FLOAT:
                raise ConditionsError("PATCH_INVALID")
            continue
        if node_type is str:
            _check_supplied_string(node)
            continue
        if node_type is dict:
            if len(node) > MAX_CONTAINER_WIDTH:
                raise ConditionsError("PATCH_INVALID")
            for key, value in node.items():
                if type(key) is not str:
                    raise ConditionsError("PATCH_INVALID")
                _check_supplied_string(key)
                stack.append(value)
            continue
        if node_type is list:
            if len(node) > MAX_CONTAINER_WIDTH:
                raise ConditionsError("PATCH_INVALID")
            stack.extend(node)
            continue
        # A strict JSON document cannot contain any other type.
        raise ConditionsError("PATCH_INVALID")


def _check_supplied_string(value: str) -> None:
    if len(value) > MAX_STRING_LENGTH:
        raise ConditionsError("PATCH_INVALID")
    _check_utf8(value)


def _validate_environment(value: object) -> None:
    if type(value) is not dict:
        raise ConditionsError("PATCH_INVALID")
    if not value:
        # Schema-valid but comparison rejects a present-empty environment.
        raise ConditionsError("PATCH_INVALID")

    stack: list[tuple[object, int]] = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        node_type = type(node)
        if node_type is dict:
            if depth > MAX_ENVIRONMENT_DEPTH:
                raise ConditionsError("PATCH_INVALID")
            for child in node.values():
                child_type = type(child)
                if child_type is dict or child_type is list:
                    stack.append((child, depth + 1))
        elif node_type is list:
            if depth > MAX_ENVIRONMENT_DEPTH:
                raise ConditionsError("PATCH_INVALID")
            for child in node:
                child_type = type(child)
                if child_type is dict or child_type is list:
                    stack.append((child, depth + 1))

    try:
        text = json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError):
        raise ConditionsError("PATCH_INVALID") from None
    if len(text.encode("ascii")) > MAX_ENVIRONMENT_BYTES:
        raise ConditionsError("PATCH_INVALID")


# --------------------------------------------------------------------------
# Patch-supplied selector validation
# --------------------------------------------------------------------------


def _validate_selectors(patch: dict, manifest: Manifest, run_id: str) -> None:
    """Apply the four accepted rules to patch-present, non-null selectors only.

    Duplicate artifact IDs resolve by first occurrence in manifest order.
    Untouched and cleared selectors are not validated, and no edge is
    synthesized.
    """
    artifacts = {}
    for artifact in manifest.artifacts:
        artifacts.setdefault(artifact.id, artifact)

    consumed = {
        (relationship.source, relationship.target)
        for relationship in manifest.relationships
        if relationship.type is RelationshipType.input_to
    }

    for field, expected_kind in SELECTORS:
        if field not in patch:
            continue
        artifact_id = patch[field]
        if artifact_id is None:
            # The model already refuses a selector null; keep the guard local.
            raise ConditionsError("PATCH_INVALID")
        artifact = artifacts.get(artifact_id)
        if artifact is None:
            raise ConditionsError("SELECTOR_INVALID")
        if getattr(artifact, "artifact_type", "file") == "directory":
            raise ConditionsError("SELECTOR_INVALID")
        if getattr(artifact, "kind", None) != expected_kind:
            raise ConditionsError("SELECTOR_INVALID")
        if (artifact_id, run_id) not in consumed:
            raise ConditionsError("SELECTOR_INVALID")


# --------------------------------------------------------------------------
# Preservation and serialization proofs
# --------------------------------------------------------------------------


def _shared_containers(left: object, right: object) -> set[int]:
    return set(_container_ids(left)) & set(_container_ids(right))


def _container_ids(root: object) -> dict[int, object]:
    found: dict[int, object] = {}
    stack: list[object] = [root]
    while stack:
        node = stack.pop()
        node_type = type(node)
        if node_type is dict:
            found[id(node)] = node
            stack.extend(node.values())
        elif node_type is list:
            found[id(node)] = node
            stack.extend(node)
    return found


def _masked_projection(document: dict, run_index: int) -> dict:
    """A shallow copy with only the target run's conditions member removed."""
    top = dict(document)
    runs = list(top["runs"])
    run = dict(runs[run_index])
    run.pop("conditions", None)
    runs[run_index] = run
    top["runs"] = runs
    return top


def _prove_preservation(
    source: object,
    candidate: object,
    run_index: int,
    patch: dict,
    cleared: tuple[str, ...],
) -> None:
    """Prove the candidate differs from the baseline only as requested."""
    if type(source) is not dict or type(candidate) is not dict:
        raise ConditionsError("INTERNAL_ERROR")
    if _shared_containers(source, candidate):
        # The independent source decode and the candidate must not share a
        # mutable dict or list by identity.
        raise ConditionsError("INTERNAL_ERROR")

    source_runs = source.get("runs")
    candidate_runs = candidate.get("runs")
    if type(source_runs) is not list or type(candidate_runs) is not list:
        raise ConditionsError("INTERNAL_ERROR")
    if run_index >= len(source_runs) or run_index >= len(candidate_runs):
        raise ConditionsError("INTERNAL_ERROR")
    source_run = source_runs[run_index]
    candidate_run = candidate_runs[run_index]
    if type(source_run) is not dict or type(candidate_run) is not dict:
        raise ConditionsError("INTERNAL_ERROR")
    if source_run.get("id") != candidate_run.get("id"):
        raise ConditionsError("INTERNAL_ERROR")

    # 1. Only the target run's conditions member may differ.
    if not strict_equal(
        _masked_projection(source, run_index),
        _masked_projection(candidate, run_index),
    ):
        raise ConditionsError("INTERNAL_ERROR")

    source_conditions = source_run.get("conditions", _MISSING)
    candidate_conditions = candidate_run.get("conditions", _MISSING)
    if source_conditions is not _MISSING and type(source_conditions) is not dict:
        raise ConditionsError("INTERNAL_ERROR")

    cleared_set = set(cleared)
    expected_keys: list[str] = []
    if source_conditions is not _MISSING:
        for key in source_conditions:
            if key not in cleared_set:
                expected_keys.append(key)
    for field in FIELDS:
        if field in patch and field not in expected_keys:
            expected_keys.append(field)

    # 2. The result subtree exists exactly when children remain.
    if not expected_keys:
        if "conditions" in candidate_run:
            raise ConditionsError("INTERNAL_ERROR")
        if candidate_conditions is not _MISSING:
            raise ConditionsError("INTERNAL_ERROR")
    else:
        if candidate_conditions is _MISSING or type(candidate_conditions) is not dict:
            raise ConditionsError("INTERNAL_ERROR")
        if list(candidate_conditions) != expected_keys:
            raise ConditionsError("INTERNAL_ERROR")

    # 3. Every fixed child is exactly the requested result.
    for field in FIELDS:
        if field in cleared_set:
            if field in candidate_run.get("conditions", {}):
                raise ConditionsError("INTERNAL_ERROR")
            continue
        if field in patch:
            if expected_keys and field not in candidate_conditions:
                raise ConditionsError("INTERNAL_ERROR")
            if not strict_equal(candidate_conditions[field], patch[field]):
                raise ConditionsError("INTERNAL_ERROR")
            continue
        baseline_present = (
            source_conditions is not _MISSING and field in source_conditions
        )
        candidate_present = (
            candidate_conditions is not _MISSING and field in candidate_conditions
        )
        if baseline_present != candidate_present:
            raise ConditionsError("INTERNAL_ERROR")
        if baseline_present and not strict_equal(
            candidate_conditions[field], source_conditions[field]
        ):
            raise ConditionsError("INTERNAL_ERROR")

    # 4. The parent key position is exact.
    parent_present = source_conditions is not _MISSING
    source_run_keys = list(source_run)
    candidate_run_keys = list(candidate_run)
    if parent_present:
        parent_index = source_run_keys.index("conditions")
    if not expected_keys:
        if "conditions" in candidate_run:
            raise ConditionsError("INTERNAL_ERROR")
        if parent_present:
            expected_run_keys = [
                key for key in source_run_keys if key != "conditions"
            ]
            if candidate_run_keys != expected_run_keys:
                raise ConditionsError("INTERNAL_ERROR")
        else:
            if candidate_run_keys != source_run_keys:
                raise ConditionsError("INTERNAL_ERROR")
    elif parent_present:
        if "conditions" not in candidate_run:
            raise ConditionsError("INTERNAL_ERROR")
        if candidate_run_keys.index("conditions") != parent_index:
            raise ConditionsError("INTERNAL_ERROR")
        if candidate_run_keys != source_run_keys:
            raise ConditionsError("INTERNAL_ERROR")
    else:
        if candidate_run_keys != source_run_keys + ["conditions"]:
            raise ConditionsError("INTERNAL_ERROR")


def _serialize_candidate(candidate: dict) -> None:
    """dump_raw + UTF-8 encode + loader decode + strict equality."""
    try:
        text = dump_raw(candidate)
        data = text.encode("utf-8")
        emitted = decode_snapshot(data)
    except (UnicodeEncodeError, RecursionError):
        raise ConditionsError("SERIALIZATION_FAILED") from None
    if type(emitted) is not dict or not strict_equal(candidate, emitted):
        raise ConditionsError("INTERNAL_ERROR")
