"""Explicit, value-preserving migration of a manifest to schema 0.2, 0.3, 0.4 or 0.5.

The command layer owns argument parsing, output and exit codes. This module
owns the migration contract: one validated load, independent decodes, a strict
decoded-value proof, serialization eligibility, the migration-specific backup
generation and the two stale checkpoints.

The only semantic change is the top-level ``schema_version`` value. Every
other loader-decoded value, type, presence and object-key order is preserved.
No artifact is read, no adapter is called, no directory is scanned beyond the
target folder's backup namespace, and nothing is printed.
"""

from __future__ import annotations

import errno
import json
import math
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .manifest import (
    ManifestLoadError,
    _finite_float,
    _nonfinite_constant,
    _resolve_nonfinite_tokens,
    _unique_object,
    dump_raw,
    load_manifest,
    resolve_manifest_path,
    save_raw,
    validate_manifest,
)
from .models import (
    BACKUP_FILENAME,
    Manifest,
    backup_generation,
    is_valid_locator_pointer,
)

SOURCE_VERSION = "0.1"
TARGET_VERSION = "0.2"
TARGET_VERSION_0_3 = "0.3"
TARGET_VERSION_0_4 = "0.4"
TARGET_VERSION_0_5 = "0.5"
TARGET_VERSION_0_6 = "0.6"
TARGET_VERSION_0_7 = "0.7"
TARGET_VERSION_0_8 = "0.8"
TARGET_VERSIONS = (
    TARGET_VERSION,
    TARGET_VERSION_0_3,
    TARGET_VERSION_0_4,
    TARGET_VERSION_0_5,
    TARGET_VERSION_0_6,
    TARGET_VERSION_0_7,
    TARGET_VERSION_0_8,
)

# The CLI adds INVALID_ARGUMENTS and UNSUPPORTED_TARGET before calling this
# module; the core has exactly this closed set of outcomes.
MIGRATION_ERROR_CODES = frozenset(
    {
        "MANIFEST_LOAD_FAILED",
        "SERIALIZATION_FAILED",
        "READ_FAILED",
        "STALE_MANIFEST",
        "WRITE_FAILED",
        "RESERVED_PATH",
        "LOCATOR_MIGRATION_UNSUPPORTED",
        "DOWNGRADE_UNSUPPORTED",
        "INTERNAL_ERROR",
    }
)


class MigrationError(Exception):
    """A refused migration with a stable, closed error code.

    The exception text is deliberately generic: callers receive the code and
    render fixed prose. Paths, manifest values, IDs, exception messages and
    tracebacks never travel through this type.
    """

    def __init__(self, code: str) -> None:
        if code not in MIGRATION_ERROR_CODES:
            raise ValueError("unknown migration error code")
        self.code = code
        super().__init__("migration failed")


@dataclass(frozen=True)
class MigrationResult:
    """The outcome of one migration invocation.

    ``backup_path`` is set only when a backup was created or reused. JSON
    rendering must ignore it; text rendering may show it.
    """

    from_version: str
    to_version: str
    changed: bool
    dry_run: bool
    backup: str
    backup_path: Path | None = None


def decode_snapshot(source_bytes: bytes) -> dict:
    """Decode one manifest snapshot with exactly the loader's JSON rules.

    Uses the same duplicate-key rejection, non-finite-token marker and finite
    float parsing as ``load_manifest``, then resolves legacy tokens only at
    ``results[*].value``. Every call builds a fresh object graph.
    """
    raw = json.loads(
        source_bytes.decode("utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_nonfinite_constant,
        parse_float=_finite_float,
    )
    _resolve_nonfinite_tokens(raw)
    return raw


def strict_equal(a: object, b: object) -> bool:
    """Decoded-value equality with JSON types, key order and array order.

    This is deliberately not Python ``==``:

    * only exact ``dict``/``list`` types match, and object key order matters;
    * ``bool`` never equals ``int`` and ``int`` never equals ``float``;
    * NaN equals NaN, but not infinity; infinities compare by sign;
    * signed zeros differ (``-0.0`` is not ``0.0``);
    * any other type is unequal.

    The walk is iterative so deeply nested metadata cannot raise
    ``RecursionError`` from the proof itself.
    """
    stack: list[tuple[object, object]] = [(a, b)]
    while stack:
        left, right = stack.pop()
        left_type = type(left)
        if left_type is not type(right):
            return False
        if left_type is dict:
            if list(left) != list(right):
                return False
            for key in left:
                stack.append((left[key], right[key]))
        elif left_type is list:
            if len(left) != len(right):
                return False
            stack.extend(zip(left, right))
        elif left_type is bool:
            if left != right:
                return False
        elif left_type is int:
            if left != right:
                return False
        elif left_type is float:
            if not _same_float(left, right):
                return False
        elif left_type is str:
            if left != right:
                return False
        elif left is not None:
            return False
    return True


def _same_float(left: float, right: float) -> bool:
    left_nan = math.isnan(left)
    right_nan = math.isnan(right)
    if left_nan or right_nan:
        return left_nan and right_nan
    if math.isinf(left) or math.isinf(right):
        return math.isinf(left) and math.isinf(right) and (left > 0) == (right > 0)
    if left == 0.0 and right == 0.0:
        return math.copysign(1.0, left) == math.copysign(1.0, right)
    return left == right


def without_schema_version(obj: dict) -> dict:
    """A new top-level dict without ``schema_version``, order otherwise intact."""
    return {key: value for key, value in obj.items() if key != "schema_version"}


def migrate_manifest(
    path: Path, *, dry_run: bool = False, target_version: str = TARGET_VERSION
) -> MigrationResult:
    """Declare an existing valid 0.1-0.4 manifest as 0.2, 0.3, 0.4 or 0.5 additively.

    The only value changed is ``schema_version``. A 0.1/0.2-to-0.3,
    0.1/0.2-to-0.4 or 0.1/0.2-to-0.5 migration is pre-checked for locators the
    target cannot accept or verify and refuses the whole migration before any
    write. A 0.3-to-0.4, 0.3-to-0.5 or 0.4-to-0.5 migration is a version-only
    bump (each target admits every locator of its source) and skips that
    pre-check. Refusals raise ``MigrationError`` with a closed code; a
    successful real run has already replaced the manifest and a dry run wrote
    nothing.
    """
    try:
        return _migrate_manifest(
            Path(path), dry_run=dry_run, target_version=target_version
        )
    except MigrationError:
        raise
    except Exception:
        # Sanitized boundary: never leak an exception message, path or traceback.
        raise MigrationError("INTERNAL_ERROR") from None


def _migrate_manifest(
    target: Path, *, dry_run: bool, target_version: str = TARGET_VERSION
) -> MigrationResult:
    """Execute one 0.2/0.3/0.4/0.5 migration after the sanitized boundary."""
    _reject_reserved_target(target)

    if target_version not in TARGET_VERSIONS:
        raise MigrationError("INTERNAL_ERROR")

    try:
        loaded = load_manifest(target)
    except (ManifestLoadError, OSError, RecursionError):
        raise MigrationError("MANIFEST_LOAD_FAILED") from None

    source_version = loaded.manifest.schema_version
    if source_version == target_version:
        # Idempotent success: no backup enumeration, no serialization, no write.
        return MigrationResult(
            from_version=target_version,
            to_version=target_version,
            changed=False,
            dry_run=dry_run,
            backup="none",
            backup_path=None,
        )
    if target_version == TARGET_VERSION:
        # The existing --to 0.2 path is additive from 0.1 only; no downgrade.
        if source_version != SOURCE_VERSION:
            raise MigrationError("DOWNGRADE_UNSUPPORTED")
    elif target_version == TARGET_VERSION_0_3:
        # --to 0.3 is additive from 0.1 or 0.2; a 0.3 source is a no-op above.
        # A loaded 0.4 or 0.5 source is a downgrade, never an internal failure.
        if source_version in (
            TARGET_VERSION_0_4,
            TARGET_VERSION_0_5,
            TARGET_VERSION_0_6,
            TARGET_VERSION_0_7,
            TARGET_VERSION_0_8,
        ):
            raise MigrationError("DOWNGRADE_UNSUPPORTED")
        if source_version not in (SOURCE_VERSION, TARGET_VERSION):
            raise MigrationError("INTERNAL_ERROR")
        _check_locator_migration(loaded.manifest)
    elif target_version == TARGET_VERSION_0_4:
        # --to 0.4 is additive from every earlier version. 0.1/0.2 keep the
        # same manifest-only pre-check as 0.2->0.3; 0.3 is a version-only bump
        # because 0.4 admits every locator 0.3 admits. A 0.5 source is a
        # downgrade, never an internal failure.
        if source_version in (SOURCE_VERSION, TARGET_VERSION):
            _check_locator_migration(loaded.manifest)
        elif source_version in (
            TARGET_VERSION_0_5,
            TARGET_VERSION_0_6,
            TARGET_VERSION_0_7,
            TARGET_VERSION_0_8,
        ):
            raise MigrationError("DOWNGRADE_UNSUPPORTED")
        elif source_version != TARGET_VERSION_0_3:
            raise MigrationError("INTERNAL_ERROR")
    elif target_version == TARGET_VERSION_0_6:
        # --to 0.6 is additive from every earlier version. 0.1/0.2 keep the
        # manifest-only locator precheck; 0.3/0.4/0.5 are version-only bumps.
        if source_version in (SOURCE_VERSION, TARGET_VERSION):
            _check_locator_migration(loaded.manifest)
        elif source_version in (TARGET_VERSION_0_7, TARGET_VERSION_0_8):
            raise MigrationError("DOWNGRADE_UNSUPPORTED")
        elif source_version not in (
            TARGET_VERSION_0_3,
            TARGET_VERSION_0_4,
            TARGET_VERSION_0_5,
        ):
            raise MigrationError("INTERNAL_ERROR")
    elif target_version == TARGET_VERSION_0_7:
        # --to 0.7 is additive from every earlier version. 0.1/0.2 keep the
        # precheck; 0.3/0.4/0.5/0.6 are version-only bumps.
        if source_version in (SOURCE_VERSION, TARGET_VERSION):
            _check_locator_migration(loaded.manifest)
        elif source_version == TARGET_VERSION_0_8:
            raise MigrationError("DOWNGRADE_UNSUPPORTED")
        elif source_version not in (
            TARGET_VERSION_0_3,
            TARGET_VERSION_0_4,
            TARGET_VERSION_0_5,
            TARGET_VERSION_0_6,
        ):
            raise MigrationError("INTERNAL_ERROR")
    elif target_version == TARGET_VERSION_0_8:
        # --to 0.8 is additive from every earlier version. 0.1/0.2 keep the
        # precheck; 0.3-0.7 are version-only bumps because 0.8 admits every
        # delivered locator and value.
        if source_version in (SOURCE_VERSION, TARGET_VERSION):
            _check_locator_migration(loaded.manifest)
        elif source_version not in (
            TARGET_VERSION_0_3,
            TARGET_VERSION_0_4,
            TARGET_VERSION_0_5,
            TARGET_VERSION_0_6,
            TARGET_VERSION_0_7,
        ):
            raise MigrationError("INTERNAL_ERROR")
    else:
        # --to 0.5 is additive from every earlier version. 0.1/0.2 keep the
        # same manifest-only pre-check as the earlier targets; 0.3/0.4 are
        # version-only bumps because 0.5 admits every locator they admit.
        if source_version in (SOURCE_VERSION, TARGET_VERSION):
            _check_locator_migration(loaded.manifest)
        elif source_version in (
            TARGET_VERSION_0_6,
            TARGET_VERSION_0_7,
            TARGET_VERSION_0_8,
        ):
            raise MigrationError("DOWNGRADE_UNSUPPORTED")
        elif source_version not in (TARGET_VERSION_0_3, TARGET_VERSION_0_4):
            raise MigrationError("INTERNAL_ERROR")

    # Two independent decodes: source_a is the immutable baseline, source_b
    # supplies every candidate value. Never share containers between them.
    source_a = decode_snapshot(loaded.source_bytes)
    source_b = decode_snapshot(loaded.source_bytes)
    candidate = _candidate_from(source_b, target_version)

    _prove_preservation(source_a, candidate, source_version, target_version)
    _validate_candidate(candidate, target_version)
    emitted = _serialize_candidate(candidate)
    _prove_emitted(source_a, emitted, target_version)

    # First stale checkpoint: immediately before the backup stage. A dry run
    # performs this read too, but never creates a backup or writes anything.
    current = _read_pre_backup(loaded.path)
    if current != loaded.source_bytes:
        raise MigrationError("STALE_MANIFEST")
    if dry_run:
        return MigrationResult(
            from_version=source_version,
            to_version=target_version,
            changed=True,
            dry_run=True,
            backup="none",
            backup_path=None,
        )

    source_mode = _target_mode(loaded.path)
    backup, backup_path = _backup_for_migration(
        loaded.path, loaded.source_bytes, source_mode
    )

    try:
        save_raw(candidate, loaded.path, expected_bytes=loaded.source_bytes)
    except OSError:
        # manifest.py suppresses a cleanup-only error after a committed
        # replace; any OSError here is non-committed. Classify by observing
        # the bytes now present, never by matching exception text.
        _classify_late_failure(loaded.path, loaded.source_bytes)
    except Exception:
        raise MigrationError("INTERNAL_ERROR") from None

    return MigrationResult(
        from_version=source_version,
        to_version=target_version,
        changed=True,
        dry_run=False,
        backup=backup,
        backup_path=backup_path,
    )


def _reject_reserved_target(target: Path) -> None:
    """Refuse an operand that resolves to the backup namespace, before load."""
    try:
        resolved = resolve_manifest_path(target).resolve()
    except (OSError, RuntimeError):
        # Let load_manifest produce the sanitized MANIFEST_LOAD_FAILED code.
        return
    if backup_generation(resolved.name) is not None:
        raise MigrationError("RESERVED_PATH")


def _check_locator_migration(manifest: Manifest) -> None:
    """Refuse a 0.1/0.2 -> 0.3, 0.1/0.2 -> 0.4 or 0.1/0.2 -> 0.5 migration
    when a locator cannot be safe there.

    The check is purely manifest-level (no file read, no artifact I/O): a
    prefixed/invalid pointer, a locator without artifact_id, a string locator
    on a non-``.json`` artifact (including a ``.csv`` file), or a locator on a
    volatile/no-digest/directory artifact. The refusal code is fixed and
    value-free. 0.3/0.4 sources skip this check because 0.5 is a superset.
    """
    artifacts = {artifact.id: artifact for artifact in manifest.artifacts}
    for result in manifest.results:
        locator = result.locator
        if locator is None:
            continue
        if type(locator) is not str:
            # 0.1/0.2 cannot load an object locator; keep the check defensive.
            raise MigrationError("LOCATOR_MIGRATION_UNSUPPORTED")
        if result.artifact_id is None:
            raise MigrationError("LOCATOR_MIGRATION_UNSUPPORTED")
        if not is_valid_locator_pointer(locator):
            raise MigrationError("LOCATOR_MIGRATION_UNSUPPORTED")
        artifact = artifacts.get(result.artifact_id)
        if artifact is None:
            # A dangling artifact_id is a later validate-time reference error,
            # not one of the migration gate's locator classes.
            continue
        if artifact.artifact_type == "directory":
            raise MigrationError("LOCATOR_MIGRATION_UNSUPPORTED")
        if artifact.volatile:
            raise MigrationError("LOCATOR_MIGRATION_UNSUPPORTED")
        if artifact.sha256 is None:
            raise MigrationError("LOCATOR_MIGRATION_UNSUPPORTED")
        if not artifact.path.lower().endswith(".json"):
            raise MigrationError("LOCATOR_MIGRATION_UNSUPPORTED")


def _candidate_from(source: dict, target_version: str = TARGET_VERSION) -> dict:
    """A new top-level object with schema_version first and other values as-is."""
    candidate = {"schema_version": target_version}
    for key, value in source.items():
        if key == "schema_version":
            continue
        candidate[key] = value
    return candidate


def _prove_preservation(
    source: object,
    candidate: object,
    source_version: str = SOURCE_VERSION,
    target_version: str = TARGET_VERSION,
) -> None:
    if type(source) is not dict or type(candidate) is not dict:
        raise MigrationError("INTERNAL_ERROR")
    declared = source.get("schema_version", SOURCE_VERSION)
    if declared != source_version or candidate.get("schema_version") != target_version:
        raise MigrationError("INTERNAL_ERROR")
    if not candidate or next(iter(candidate)) != "schema_version":
        raise MigrationError("INTERNAL_ERROR")
    source_rest = without_schema_version(source)
    candidate_rest = without_schema_version(candidate)
    if list(source_rest) != list(candidate_rest):
        raise MigrationError("INTERNAL_ERROR")
    if not strict_equal(source_rest, candidate_rest):
        raise MigrationError("INTERNAL_ERROR")


def _validate_candidate(candidate: dict, target_version: str = TARGET_VERSION) -> None:
    try:
        manifest = validate_manifest(candidate)
    except Exception:
        raise MigrationError("INTERNAL_ERROR") from None
    if manifest.schema_version != target_version:
        raise MigrationError("INTERNAL_ERROR")


def _serialize_candidate(candidate: dict) -> dict:
    """dump_raw + UTF-8 encode + loader decode, or SERIALIZATION_FAILED."""
    try:
        text = dump_raw(candidate)
        data = text.encode("utf-8")
        return decode_snapshot(data)
    except (UnicodeEncodeError, RecursionError):
        raise MigrationError("SERIALIZATION_FAILED") from None


def _prove_emitted(
    source: object, emitted: object, target_version: str = TARGET_VERSION
) -> None:
    if type(emitted) is not dict or emitted.get("schema_version") != target_version:
        raise MigrationError("INTERNAL_ERROR")
    if type(source) is not dict:
        raise MigrationError("INTERNAL_ERROR")
    if not strict_equal(
        without_schema_version(source), without_schema_version(emitted)
    ):
        raise MigrationError("INTERNAL_ERROR")


def _read_pre_backup(target: Path) -> bytes:
    try:
        return target.read_bytes()
    except OSError:
        raise MigrationError("READ_FAILED") from None


def _read_diagnostic(target: Path) -> bytes:
    try:
        return target.read_bytes()
    except OSError:
        raise MigrationError("WRITE_FAILED") from None


def _classify_late_failure(target: Path, expected_bytes: bytes) -> None:
    """Classify a non-committed save_raw raise from the observed bytes."""
    if _read_diagnostic(target) != expected_bytes:
        raise MigrationError("STALE_MANIFEST") from None
    raise MigrationError("WRITE_FAILED") from None


def _target_mode(target: Path) -> int:
    try:
        return stat.S_IMODE(os.stat(target).st_mode)
    except OSError:
        raise MigrationError("WRITE_FAILED") from None


def _backup_for_migration(
    target: Path, source_bytes: bytes, source_mode: int
) -> tuple[str, Path]:
    """Reuse an eligible newest generation or create the next one.

    Returns ``("reused"|"created", path)``. The target folder is enumerated
    exactly once here. Prior generations are never overwritten, modified or
    deleted, and an incomplete file created by this attempt is removed.
    """
    folder = target.parent
    try:
        names = os.listdir(folder)
    except OSError:
        raise MigrationError("WRITE_FAILED") from None

    newest_generation = 0
    newest_path: Path | None = None
    for name in names:
        generation = backup_generation(name)
        if generation is not None and generation > newest_generation:
            newest_generation = generation
            newest_path = folder / name

    if newest_path is not None and _can_reuse_backup(
        newest_path, target, source_bytes, source_mode
    ):
        return "reused", newest_path

    if newest_generation == 0:
        name = BACKUP_FILENAME
    else:
        name = f"{BACKUP_FILENAME}.{newest_generation + 1}"
    path = folder / name
    try:
        _create_backup_file(path, source_bytes, source_mode)
    except OSError:
        raise MigrationError("WRITE_FAILED") from None
    return "created", path


def _can_reuse_backup(
    candidate: Path, target: Path, source_bytes: bytes, source_mode: int
) -> bool:
    try:
        link_stat = os.lstat(candidate)
    except OSError:
        return False
    if not stat.S_ISREG(link_stat.st_mode) or stat.S_ISLNK(link_stat.st_mode):
        return False

    same = _same_file(candidate, target)
    if same is not False:
        # True is a target alias; None means no identity check is available.
        return False

    try:
        if candidate.read_bytes() != source_bytes:
            return False
        candidate_mode = stat.S_IMODE(os.stat(candidate).st_mode)
    except OSError:
        return False
    return (candidate_mode & ~source_mode) == 0


def _same_file(candidate: Path, target: Path) -> bool | None:
    """True/False for same-file, or None when no identity check is available."""
    samefile = getattr(os.path, "samefile", None)
    if samefile is not None:
        try:
            return samefile(candidate, target)
        except OSError:
            pass
    try:
        candidate_stat = os.stat(candidate)
        target_stat = os.stat(target)
    except (OSError, AttributeError):
        return None
    return (candidate_stat.st_dev, candidate_stat.st_ino) == (
        target_stat.st_dev,
        target_stat.st_ino,
    )


def _create_backup_file(path: Path, data: bytes, mode: int) -> None:
    """Exclusively create one backup generation and verify it before trust."""
    # O_EXCL also covers a name created concurrently. A collision raises
    # before anything is ours to delete, so another writer's file is intact.
    # O_BINARY keeps the copied bytes exact on Windows; without it the CRT
    # translates LF to CRLF and the backup no longer matches the source.
    fd = os.open(
        path,
        os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_BINARY", 0),
        mode,
    )

    try:
        _write_all(fd, data)
        os.fsync(fd)
        if _read_all(fd) != data:
            raise OSError(errno.EIO, "backup readback mismatch")
        actual_mode = _file_mode(fd)
        if actual_mode & ~mode:
            raise OSError(errno.EPERM, "backup permissions exceed the source")
    except BaseException:
        _close_quietly(fd)
        _unlink_quietly(path)
        raise

    try:
        os.close(fd)
    except OSError:
        _unlink_quietly(path)
        raise


def _file_mode(fd: int) -> int:
    return stat.S_IMODE(os.fstat(fd).st_mode)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(errno.EIO, "backup write failed")
        view = view[written:]


def _read_all(fd: int) -> bytes:
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _close_quietly(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass


def _unlink_quietly(path: Path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
