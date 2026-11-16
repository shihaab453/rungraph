"""Reading and writing manifest files."""

from __future__ import annotations

import errno
import json
import math
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, get_args, get_origin, Union

from pydantic import BaseModel, ValidationError

from .findings import Code, Finding, Severity
from .models import (
    MANIFEST_FILENAME,
    SCHEMA_VERSION_0_4,
    SCHEMA_VERSION_0_6,
    SCHEMA_VERSION_0_5,
    SCHEMA_VERSION_0_7,
    SCHEMA_VERSION_0_8,
    SCHEMA_VERSION_0_9,
    SCHEMA_VERSION_0_10,
    Artifact,
    CsvCellLocator,
    JsonlLocator,
    Manifest,
    Manifest04,
    Manifest05,
    Manifest06,
    Manifest07,
    Manifest08,
    Manifest09,
    Manifest10,
    AudioValue,
    HistogramValue,
    TensorValue,
    ImageValue,
    TensorboardHistogramLocator,
    TensorboardAudioLocator,
    TensorboardImageLocator,
    TensorboardTensorLocator,
    TensorboardLocator,
    TensorboardStringLocator,
    TensorboardTf2AudioLocator,
    TensorboardTf2ImageLocator,
)


class ManifestLoadError(Exception):
    """Raised when a manifest cannot be found, parsed, or schema-validated."""

    def __init__(self, findings: list[Finding]):
        self.findings = findings
        super().__init__("; ".join(f.message for f in findings))


class _SchemaFinding(Finding):
    """Additional diagnostics only for individual model validation errors."""

    issue_category: str
    issue_location: str | None


_TYPE_ERRORS = frozenset({
    "model_type", "model_attributes_type", "dict_type", "list_type",
    "string_type", "int_type", "float_type", "bool_type", "datetime_type",
})
_VALUE_ERRORS = frozenset({
    "extra_forbidden", "literal_error", "enum", "value_error", "string_pattern_mismatch",
    "string_too_short", "string_too_long", "too_short", "too_long", "greater_than",
    "greater_than_equal", "less_than", "less_than_equal", "int_parsing",
    "float_parsing", "bool_parsing", "datetime_parsing", "datetime_from_date_parsing",
    "finite_number", "int_from_float",
})


def _issue_category(error_type: str) -> str:
    if error_type == "missing":
        return "missing_required"
    if error_type == "union_tag_not_found":
        return "missing_required"
    if error_type in _TYPE_ERRORS:
        return "invalid_type"
    if error_type == "union_tag_invalid":
        return "invalid_value"
    if error_type in _VALUE_ERRORS:
        return "invalid_value"
    return "other_validation"


_LOCATOR_TAGS: dict[str, type[BaseModel]] = {
    "jsonl": JsonlLocator,
    "csv-cell": CsvCellLocator,
    "tensorboard": TensorboardLocator,
    "tensorboard-string": TensorboardStringLocator,
    "tensorboard-histogram": TensorboardHistogramLocator,
    "tensorboard-image": TensorboardImageLocator,
    "tensorboard-audio": TensorboardAudioLocator,
    "tensorboard-tensor": TensorboardTensorLocator,
    "tensorboard-tf2-image": TensorboardTf2ImageLocator,
    "tensorboard-tf2-audio": TensorboardTf2AudioLocator,
}


def _unwrap_annotation(annotation: Any) -> Any:
    """Look through ``Annotated`` and a single-option ``Optional``."""
    while True:
        if get_origin(annotation) is Annotated:
            annotation = get_args(annotation)[0]
            continue
        if get_origin(annotation) is Union:
            options = [a for a in get_args(annotation) if a is not type(None)]
            if len(options) == 1:
                annotation = options[0]
                continue
        return annotation


def _issue_location(
    loc: tuple,
    *,
    root_model: type[BaseModel] | None = None,
    error_type: str = "",
) -> str | None:
    """Return a safe pointer, including the field ancestor of a scalar union error."""
    # Resolve the version-specific root at call time: a default argument would
    # capture the class and defeat a monkeypatched root.
    if root_model is None:
        root_model = Manifest
    annotation: Any = root_model
    parts = []
    for index, part in enumerate(loc):
        annotation = _unwrap_annotation(annotation)
        # Optional fields have one non-None type. A multi-option union adds a
        # branch label; accept only a label naming one of its own options.
        if get_origin(annotation) is Union:
            options = [a for a in get_args(annotation) if a is not type(None)]
            if isinstance(part, str):
                # A discriminated-union tag names the branch model directly;
                # map it before generic branch-label matching (the raw tag is
                # not a class name and would otherwise lose the location).
                tagged = _LOCATOR_TAGS.get(part)
                if tagged is not None and tagged in options:
                    annotation = tagged
                    continue
                # pydantic may leave its internal union label in `loc`.
                if (
                    part.startswith("tagged-union[")
                    and part.endswith("]")
                    and JsonlLocator in options
                    and CsvCellLocator in options
                ):
                    continue
                match = next(
                    (a for a in options if isinstance(a, type) and a.__name__ == part),
                    None,
                )
                if match is None:
                    return None
                if (index == len(loc) - 1
                        and all(
                            a in (
                                bool,
                                int,
                                float,
                                str,
                                HistogramValue,
                                ImageValue,
                                AudioValue,
                                TensorValue,
                            )
                            for a in options
                        )):
                    return "".join("/" + component for component in parts)
                annotation = match
                continue
            return None
        if get_origin(annotation) is list and type(part) is int and part >= 0:
            annotation = get_args(annotation)[0]
            parts.append(str(part))
        elif get_origin(annotation) is None and isinstance(annotation, type) and issubclass(annotation, BaseModel):
            if not isinstance(part, str) or part not in annotation.model_fields:
                return None
            annotation = annotation.model_fields[part].annotation
            parts.append(part.replace("~", "~0").replace("/", "~1"))
        else:
            return None
    # The raw location of a missing/invalid discriminator is the same as a
    # value_error on the locator field; only the error type distinguishes them.
    if error_type in ("union_tag_invalid", "union_tag_not_found"):
        parts.append("kind")
    return "".join("/" + part for part in parts)


@dataclass(frozen=True)
class LoadedManifest:
    manifest: Manifest
    path: Path  # the manifest file itself
    source_bytes: bytes  # the exact snapshot that was parsed and validated

    @property
    def root(self) -> Path:
        """Folder that artifact paths are relative to: the manifest's folder."""
        return self.path.parent


def resolve_manifest_path(target: Path) -> Path:
    """Accept either a manifest file or a folder that contains one."""
    if target.is_dir():
        return target / MANIFEST_FILENAME
    return target


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    """Reject ambiguous object members before the decoder discards them."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Manifest JSON contains duplicate object keys")
        result[key] = value
    return result


class _NonFiniteToken:
    """Private marker for the three legacy JSON non-finite constants.

    The decoder creates it, so submitted data cannot imitate the marker. The
    text is resolved only after the whole document has been decoded.
    """

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text


_NONFINITE_FLOATS = {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf}


def _nonfinite_constant(value: str) -> _NonFiniteToken:
    return _NonFiniteToken(value)


def _resolve_nonfinite_tokens(raw: object) -> None:
    """Resolve legacy tokens only at results[*].value; reject every other use."""
    stack: list[tuple[object, tuple[object, ...]]] = [(raw, ())]
    while stack:
        node, path = stack.pop()
        if isinstance(node, _NonFiniteToken):
            raise ValueError("Manifest JSON contains an unsupported non-finite number")
        if isinstance(node, dict):
            for key, child in node.items():
                if isinstance(child, _NonFiniteToken):
                    permitted = (len(path) == 2 and path[0] == "results"
                                 and type(path[1]) is int and key == "value")
                    if not permitted:
                        raise ValueError("Manifest JSON contains an unsupported non-finite number")
                    node[key] = _NONFINITE_FLOATS[child.text]
                elif isinstance(child, (dict, list)):
                    stack.append((child, path + (key,)))
        elif isinstance(node, list):
            for index, child in enumerate(node):
                if isinstance(child, _NonFiniteToken):
                    raise ValueError("Manifest JSON contains an unsupported non-finite number")
                if isinstance(child, (dict, list)):
                    stack.append((child, path + (index,)))


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Manifest JSON contains an unsupported non-finite number")
    return number


def _unreadable(path: Path, exc: Exception) -> ManifestLoadError:
    return ManifestLoadError(
        [
            Finding(
                code=Code.manifest_unreadable,
                severity=Severity.error,
                message=f"Could not read manifest as UTF-8 JSON: {exc}",
                path=str(path),
            )
        ]
    )


_WINERROR_CANT_RESOLVE_FILENAME = 1921


def _is_resolution_loop(exc: RuntimeError) -> bool:
    """Recognize only the loop error that Path.resolve raises before 3.13.

    CPython raises RuntimeError("Symlink loop from %r" % e.filename) while
    handling an OSError with errno ELOOP or Windows error 1921, so that
    OSError is the implicit context. Any other RuntimeError is not a loop.
    """
    context = exc.__context__
    return (
        type(exc) is RuntimeError
        and exc.__cause__ is None
        and not exc.__suppress_context__
        and isinstance(context, OSError)
        and (context.errno == errno.ELOOP
             or getattr(context, "winerror", None) == _WINERROR_CANT_RESOLVE_FILENAME)
        and exc.args == ("Symlink loop from %r" % (context.filename,),)
    )


def validate_manifest(raw: object) -> Manifest:
    """Validate decoded manifest data with the model its version requires.

    Only ``schema_version == "0.4"`` dispatches to ``Manifest04`` and only an
    explicit ``schema_version == "0.5"`` dispatches to ``Manifest05``; every
    other value keeps the historical base-model behavior (including its
    refusals).
    """
    if isinstance(raw, dict):
        version = raw.get("schema_version")
        if version == SCHEMA_VERSION_0_4:
            return Manifest04.model_validate(raw)
        if version == SCHEMA_VERSION_0_5:
            return Manifest05.model_validate(raw)
        if version == SCHEMA_VERSION_0_6:
            return Manifest06.model_validate(raw)
        if version == SCHEMA_VERSION_0_7:
            return Manifest07.model_validate(raw)
        if version == SCHEMA_VERSION_0_8:
            return Manifest08.model_validate(raw)
        if version == SCHEMA_VERSION_0_9:
            return Manifest09.model_validate(raw)
        if version == SCHEMA_VERSION_0_10:
            return Manifest10.model_validate(raw)
    return Manifest.model_validate(raw)


def _root_model_for(raw: object) -> type[BaseModel]:
    """The model whose fields an error location should be resolved against."""
    if isinstance(raw, dict):
        version = raw.get("schema_version")
        if version == SCHEMA_VERSION_0_4:
            return Manifest04
        if version == SCHEMA_VERSION_0_5:
            return Manifest05
        if version == SCHEMA_VERSION_0_6:
            return Manifest06
        if version == SCHEMA_VERSION_0_7:
            return Manifest07
        if version == SCHEMA_VERSION_0_8:
            return Manifest08
        if version == SCHEMA_VERSION_0_9:
            return Manifest09
        if version == SCHEMA_VERSION_0_10:
            return Manifest10
    return Manifest


def load_manifest(target: Path) -> LoadedManifest:
    path = Path(target)
    try:
        # Path checks swallow "not found" errors, but e.g. access denial raises.
        candidate = resolve_manifest_path(path)
        try:
            path = candidate.resolve()
        except RuntimeError as exc:
            if not _is_resolution_loop(exc):
                raise
            raise _unreadable(path, exc) from exc
        is_file = path.is_file()
    except OSError as exc:
        raise _unreadable(path, exc) from exc
    if not is_file:
        raise ManifestLoadError(
            [
                Finding(
                    code=Code.manifest_not_found,
                    severity=Severity.error,
                    message=f"No manifest at {path}",
                    path=str(path),
                    hint=f"Create one with: runevidence init {path.parent}",
                )
            ]
        )
    try:
        source_bytes = path.read_bytes()
    except OSError as exc:
        raise _unreadable(path, exc) from exc
    return parse_manifest_source(path, source_bytes)


def parse_manifest_source(path: Path, source_bytes: bytes) -> LoadedManifest:
    """Validate manifest bytes that a caller already read from ``path``.

    Internal helper for descriptor-relative readers: the bytes stay the
    authoritative snapshot, exactly as ``load_manifest`` treats a path read.
    """
    try:
        raw = json.loads(
            source_bytes.decode("utf-8"), object_pairs_hook=_unique_object,
            parse_constant=_nonfinite_constant, parse_float=_finite_float,
        )
        _resolve_nonfinite_tokens(raw)
    except (ValueError, RecursionError) as exc:
        raise _unreadable(path, exc) from exc
    try:
        manifest = validate_manifest(raw)
    except RecursionError as exc:
        raise ManifestLoadError(
            [
                Finding(
                    code=Code.schema_invalid,
                    severity=Severity.error,
                    message="Manifest exceeds the supported validation recursion depth",
                    path=str(path),
                )
            ]
        ) from exc
    except ValidationError as exc:
        root_model = _root_model_for(raw)
        raise ManifestLoadError(
            [
                _SchemaFinding(
                    code=Code.schema_invalid,
                    severity=Severity.error,
                    message=_schema_message(
                        err['type'], err['loc'], root_model=root_model
                    ),
                    path=str(path),
                    issue_category=_issue_category(err['type']),
                    issue_location=_issue_location(
                        err['loc'], root_model=root_model, error_type=err['type']
                    ),
                )
                for err in exc.errors()
            ]
        ) from None
    return LoadedManifest(manifest=manifest, path=path, source_bytes=source_bytes)


def _schema_message(
    error_type: str,
    loc: tuple,
    *,
    root_model: type[BaseModel] | None = None,
) -> str:
    """Describe a validation error using only controlled, safe information."""
    category = _issue_category(error_type).replace("_", " ")
    location = _issue_location(loc, root_model=root_model, error_type=error_type)
    where = f" at {location}" if location else ""
    return f"Manifest schema validation failed{where}: {category}"


def artifact_to_dict(artifact: Artifact, schema_version: str = "0.1") -> dict:
    """Serialize one artifact for a containing manifest of ``schema_version``.

    0.1/0.2/0.3 keep the same historical writer spelling for established
    defaults (``volatile: false``, file ``artifact_type`` and absent
    ``digest_format`` are omitted). ``dataset_id`` is 0.2+ and is emitted only
    when it was supplied; an omitted label stays omitted.
    """
    if schema_version not in (
        "0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8", "0.9", "0.10"
    ):
        raise ValueError(f"unsupported schema version {schema_version!r}")
    data = artifact.model_dump(mode="json", exclude_defaults=False)
    # Written only when set, so manifests from before the field existed
    # come out of a rewrite unchanged.
    if not data["volatile"]:
        del data["volatile"]
    if data["artifact_type"] == "file":
        del data["artifact_type"]
    if data["digest_format"] is None:
        del data["digest_format"]
    if schema_version == "0.1" or "dataset_id" not in artifact.model_fields_set:
        data.pop("dataset_id", None)
    return data


def dump_manifest(manifest: Manifest) -> str:
    data = manifest.model_dump(mode="json", exclude_defaults=False)
    if "artifacts" in data:
        data["artifacts"] = [
            artifact_to_dict(a, schema_version=manifest.schema_version)
            for a in manifest.artifacts
        ]
    return dump_raw(data)


def dump_raw(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def save_manifest(manifest: Manifest, path: Path) -> None:
    """Write a whole manifest. An existing file is replaced only after the
    complete new text is staged beside it, so a failed write leaves the old
    manifest intact instead of truncated. A new file that fails part-way is
    removed, so no truncated manifest is left behind."""
    text = dump_manifest(manifest)
    if not path.exists():
        try:
            _write(path, text)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return
    staged = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as fh:
            staged = Path(fh.name)
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if os.name != "nt":
            os.chmod(staged, stat.S_IMODE(path.stat().st_mode))
        os.replace(staged, path)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def load_raw(loaded: LoadedManifest) -> dict:
    """Editable JSON from the validated snapshot, without a second disk read."""
    return json.loads(loaded.source_bytes.decode("utf-8"))


def save_raw(data: dict, path: Path, *, expected_bytes: bytes) -> None:
    """Reject a stale snapshot immediately before replacing the manifest.

    This byte comparison is not an atomic compare-and-swap: another writer
    can still change the file between the comparison and os.replace.
    """
    # Stage a complete edit beside the manifest before replacing it, so a
    # failed write cannot truncate the user's existing evidence record.
    text = dump_raw(data)
    staged = None
    replaced = False
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as fh:
            staged = Path(fh.name)
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if os.name != "nt":
            os.chmod(staged, stat.S_IMODE(path.stat().st_mode))
        if path.read_bytes() != expected_bytes:
            raise OSError("Manifest changed since it was loaded. Review the current manifest and retry.")
        os.replace(staged, path)
        replaced = True
    finally:
        if staged is not None:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                # Once the replacement is committed, a temp-file cleanup error
                # must not turn a successful write into a reported failure.
                if not replaced:
                    raise


def _write(path: Path, text: str) -> None:
    # newline="\n" keeps the file byte-identical across Windows and POSIX.
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _loc(loc: tuple, raw: object = None) -> str:
    """Render a Pydantic error location; name the entry by its id when the
    raw data has one, e.g. ``artifacts[13] (id 'report').sha256``."""
    out = ""
    node = raw
    for part in loc:
        if isinstance(part, int):
            out += f"[{part}]"
            node = node[part] if isinstance(node, list) and part < len(node) else None
            if isinstance(node, dict) and isinstance(node.get("id"), str):
                out += f" (id {node['id']!r})"
        else:
            out += f".{part}" if out else str(part)
            node = node.get(part) if isinstance(node, dict) else None
    return out or "<root>"
