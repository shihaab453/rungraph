"""Domain-agnostic data model for a RunEvidence manifest.

A manifest describes one experiment folder:

* ``project``       what the folder is about
* ``runs``          executions (training, evaluation, preprocessing, anything)
* ``artifacts``     files on disk, each pinned by a sha256 digest
* ``results``       named values a run produced (metrics, scores, counts)
* ``evidence``      human claims, each pointing at the results/artifacts/runs
                    that support or contradict it
* ``relationships`` extra typed edges (``derived_from``, ``input_to``, ...)

Nothing here is specific to any ML domain. Domain-specific detail goes in the
free-form ``metadata`` / ``params`` dictionaries.
"""

from __future__ import annotations

import copy
import math
import re
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, ClassVar, Literal, Optional, Union

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    TypeAdapter,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

SCHEMA_VERSION = "0.1"
# First-class comparability fields require an explicit 0.2 declaration. An
# omitted schema_version still means 0.1.
SCHEMA_VERSION_0_2 = "0.2"
# 0.3 adds structured locators. It is an explicit opt-in; omitted still means 0.1.
SCHEMA_VERSION_0_3 = "0.3"
# 0.4 adds csv-cell locators. The base Manifest stays at 0.1-0.3; Manifest04
# is only reached through the manifest dispatcher.
SCHEMA_VERSION_0_4 = "0.4"
# 0.5 adds tensorboard scalar locators. Like 0.4 it is only reached through
# the manifest dispatcher; the base Manifest stays at 0.1-0.3.
SCHEMA_VERSION_0_5 = "0.5"
# 0.6 adds TensorBoard string-tensor locators. It is only reached through the
# manifest dispatcher; the base Manifest stays at 0.1-0.3.
SCHEMA_VERSION_0_6 = "0.6"
# 0.7 adds TensorBoard histogram locators. Only the manifest dispatcher reaches
# Manifest07; the base Manifest stays at 0.1-0.3.
SCHEMA_VERSION_0_7 = "0.7"
# 0.8 adds TensorBoard image and audio references. Only the manifest dispatcher
# reaches Manifest08; the base Manifest stays at 0.1-0.3.
SCHEMA_VERSION_0_8 = "0.8"
# 0.9 adds TensorBoard numeric tensor digests. Only the manifest dispatcher
# reaches Manifest09; the base Manifest stays at 0.1-0.3.
SCHEMA_VERSION_0_9 = "0.9"
SCHEMA_VERSIONS = (SCHEMA_VERSION, SCHEMA_VERSION_0_2, SCHEMA_VERSION_0_3)
MAX_JSONL_LINE = 2**63 - 1
MAX_CSV_CELL_HEADER = 65536
MAX_TENSORBOARD_TAG = 4096
MAX_TENSORBOARD_OCCURRENCE = 2**31 - 1
# 0.3 JSON Pointer grammar: empty, or a slash-prefixed pointer where every
# tilde is the start of ~0/~1. A prefixed string such as "jsonl:..." fails.
# CR, LF, U+2028 and U+2029 are forbidden anywhere: JSON Schema pattern `$`
# otherwise accepts a lone trailing newline, and the line terminators are not
# valid pointer content for this contract.
LOCATOR_POINTER_PATTERN_TEXT = r"^(?![\s\S]*[\r\n\u2028\u2029])(?:/(?:[^~]|~[01])*)?$"
LOCATOR_POINTER_PATTERN = re.compile(LOCATOR_POINTER_PATTERN_TEXT)


def is_valid_locator_pointer(value: object) -> bool:
    """True for the 0.3 JSON Pointer grammar (empty is a valid pointer)."""
    return type(value) is str and LOCATOR_POINTER_PATTERN.fullmatch(value) is not None


def locator_to_json(value: Any) -> Any:
    """A JSON-serializable locator: strings/null unchanged, objects as dicts."""
    if value is None or type(value) is str:
        return value
    return value.model_dump(mode="json")

MANIFEST_FILENAME = "runevidence.json"
BACKUP_FILENAME = MANIFEST_FILENAME + ".bak"  # written by `init --force`
# `init --force` never overwrites an earlier backup. The first one is
# runevidence.json.bak (generation 1); later ones are runevidence.json.bak.2,
# runevidence.json.bak.3, and so on. Nothing is ever deleted automatically.
_BACKUP_RE = re.compile(r"^" + re.escape(BACKUP_FILENAME) + r"(?:\.([1-9][0-9]*))?$")


def backup_generation(name: str) -> Optional[int]:
    """The generation number of an ``init --force`` backup file name, else None."""
    match = _BACKUP_RE.match(name)
    if match is None:
        return None
    return int(match.group(1)) if match.group(1) else 1


def is_own_file(rel: str) -> bool:
    """True for RunEvidence's own files at the experiment root: the manifest
    and every backup generation. They are never tracked as artifacts. A file
    with the same name in a subfolder is ordinary content."""
    return rel == MANIFEST_FILENAME or backup_generation(rel) is not None

# IDs are short, path-safe tokens so they can appear in CLI output and URLs.
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"
# Either case is accepted (PowerShell's Get-FileHash prints uppercase) and
# stored lowercase.
SHA256_PATTERN = r"^[0-9a-fA-F]{64}$"
# Media references pin lowercase so a digest cannot be re-spelled on rewrite.
MEDIA_SHA256_PATTERN = r"^[0-9a-f]{64}$"

Id = str
# Strict IDs are scoped to the new 0.2 selectors only. The global ``Id`` and
# every existing 0.1 reference field keep their historical acceptance behavior.
SelectorId = Annotated[StrictStr, Field(pattern=ID_PATTERN)]
Scalar = Union[bool, int, float, str, None]

# Directory artifacts are checked only by the public read-only
# `validate`/`inspect --directory-artifacts` opt-in. No command adds, rehashes
# or authors one. See docs/directory-artifacts-foundation.md.
DIRECTORY_DIGEST_FORMAT = "runevidence-directory-v1"

# The JSON Schema spelling of Artifact._type_and_format_agree, so the
# committed schema rejects the same type/format combinations as the model.
# An absent artifact_type means file.
_ARTIFACT_TYPE_RULES: dict[str, Any] = {
    "if": {
        "properties": {"artifact_type": {"const": "directory"}},
        "required": ["artifact_type"],
    },
    "then": {
        "properties": {"digest_format": {"const": DIRECTORY_DIGEST_FORMAT}},
        "required": ["digest_format"],
    },
    "else": {"properties": {"digest_format": {"type": "null"}}},
}


def _add_artifact_type_rules(schema: dict[str, Any]) -> None:
    schema.update(copy.deepcopy(_ARTIFACT_TYPE_RULES))


_MISSING = object()


def _normalize_selection(spec: Any, value: Any) -> Any:
    """Normalize caller include/exclude special values and list indices."""
    if spec is Ellipsis:
        return True
    if spec is None or spec is True or spec is False:
        return spec
    if isinstance(value, BaseModel):
        if isinstance(spec, dict):
            normalized: dict[Any, Any] = {}
            fields = type(value).model_fields
            for key, sub in spec.items():
                if isinstance(key, str) and key in fields:
                    normalized[key] = _normalize_selection(
                        sub, getattr(value, key, None)
                    )
                else:
                    normalized[key] = sub
            return normalized
        return spec
    if isinstance(value, (list, tuple)):
        return _normalize_list_selection(spec, value)
    return spec


def _normalize_list_selection(spec: Any, values: list | tuple) -> Any:
    length = len(values)

    def normalized_index(key: Any) -> int | None:
        if type(key) is not int:
            return None
        if length == 0:
            # An empty list has no entry to address; pydantic ignores it.
            return key
        # Pydantic wraps out-of-range indices with Python's modulo, so
        # normalize here too: the merge must agree with the serializer about
        # which retained entry a caller spec addresses.
        return key % length

    if isinstance(spec, (set, frozenset)):
        indices: set[Any] = set()
        for item in spec:
            index = normalized_index(item)
            indices.add(item if index is None else index)
        return indices
    if isinstance(spec, dict):
        out: dict[Any, Any] = {}
        for key, sub in spec.items():
            if key == "__all__":
                # Pydantic's per-entry form. Ellipsis means every entry.
                # A partial sub-spec is passed through unchanged: entry models
                # have no nested list of models today. Normalize it here if one
                # is added, so alias merging applies recursively.
                out[key] = True if sub is Ellipsis else sub
                continue
            index = normalized_index(key)
            if index is None or not 0 <= index < length:
                out[key] = sub
            else:
                normalized = _normalize_selection(sub, values[index])
                if index in out:
                    # ``1`` and ``-1`` can address the same entry. Combine the
                    # specifications instead of letting dict order silently
                    # drop one of them. The same rules serve include unions and
                    # exclude merges: whole-entry True wins, dicts merge by key
                    # and sets union.
                    normalized = _merge_exclude_values(out[index], normalized)
                out[index] = normalized
        return out
    return spec


def _presence_exclude(model: BaseModel) -> Any:
    """An ``exclude`` tree for 0.2-only members that were never supplied.

    Building this before serialization lets Pydantic apply field and list-index
    filters natively. Nothing is inferred from a dumped list's positions.
    """
    excluded: dict[Any, Any] = {}
    for name in model._zero_two_only_fields:
        if name not in model.model_fields_set:
            excluded[name] = True
    for name in type(model).model_fields:
        if name in excluded:
            continue
        nested = _presence_exclude_value(getattr(model, name, None))
        if nested is not None:
            excluded[name] = nested
    return excluded or None


def _presence_exclude_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _presence_exclude(value)
    if isinstance(value, (list, tuple)):
        indices: dict[int, Any] = {}
        for index, item in enumerate(value):
            nested = _presence_exclude_value(item)
            if nested is not None:
                indices[index] = nested
        if not indices:
            return None
        if len(indices) == len(value):
            # Every entry carries the same nested exclusion. Pydantic's
            # per-entry form collapses that to one rule. A per-index tree of
            # the same size makes 2.6 serialization quadratic in the list
            # length, so emit the uniform form whenever it is exact.
            first = indices[0]
            if all(nested == first for nested in indices.values()):
                return {"__all__": first}
        return indices
    return None


def _merge_exclude(caller: Any, presence: Any) -> Any:
    """Merge a caller's exclude tree with the presence tree.

    Either side may be a set of names or a nested dict keyed by field/index.
    ``True`` excludes the whole node and wins over a nested tree.
    """
    if presence is None:
        return caller
    if caller is None:
        return presence
    return _merge_exclude_values(caller, presence)


def _spec_as_dict(spec: Any) -> dict[Any, Any] | None:
    """A caller field/index spec as a dict, or None when it is not partial."""
    if isinstance(spec, dict):
        return dict(spec)
    if isinstance(spec, (set, frozenset)):
        return {key: True for key in spec}
    return None


_DROP = object()


def _strip_none_false(spec: Any) -> Any:
    """Remove None/False leaves from an ``__all__`` spec, recursively.

    Those leaves select nothing. If an entry or the presence tree carries the
    same path, leaving them under ``__all__`` makes pydantic reject the
    duplicate key. Explicit entries are never stripped: there None/False means
    "keep this key" and overrides ``__all__``.
    """
    if spec is None or spec is False:
        return _DROP
    if isinstance(spec, dict):
        out: dict[Any, Any] = {}
        for key, value in spec.items():
            cleaned = _strip_none_false(value)
            if cleaned is not _DROP:
                out[key] = cleaned
        return out
    return spec


def _caller_all_base(left_all: Any) -> Any:
    """The caller's ``__all__`` spec as the base for one entry."""
    if left_all is _MISSING or left_all is None or left_all is False:
        return {}
    if left_all is True or left_all is Ellipsis:
        return {}
    as_dict = _spec_as_dict(left_all)
    if as_dict is None:
        return {}
    cleaned = _strip_none_false(as_dict)
    return cleaned if isinstance(cleaned, dict) else {}


def _merge_all_entry(base: Any, entry: Any) -> Any:
    """Merge a caller entry into ``__all__`` the way pydantic does.

    The entry wins on a leaf it names, but a nested dict merges recursively:
    excluding ``conditions.evaluation_protocol`` under ``__all__`` and
    ``conditions.source_revision`` on one entry keeps both exclusions.
    """
    if entry is True or entry is Ellipsis:
        return True
    if entry is None or entry is False:
        # The entry names this key and keeps it. Returning the value (rather
        # than dropping the key) is what makes pydantic override __all__.
        return entry
    if base is _MISSING:
        return entry
    if isinstance(base, dict) and isinstance(entry, dict):
        out = dict(base)
        for key, value in entry.items():
            out[key] = (
                _merge_all_entry(out[key], value) if key in out else value
            )
        return out
    if isinstance(base, (set, frozenset)) and isinstance(entry, dict):
        out = {key: True for key in base}
        for key, value in entry.items():
            out[key] = (
                _merge_all_entry(out[key], value) if key in out else value
            )
        return out
    if isinstance(base, dict) and isinstance(entry, (set, frozenset)):
        out = dict(base)
        for key in entry:
            out[key] = True
        return out
    if isinstance(base, (set, frozenset)) and isinstance(entry, (set, frozenset)):
        return set(base) | set(entry)
    return entry


def _caller_entry(left_all: Any, entry: Any) -> Any:
    """One caller entry with ``__all__`` applied; the entry wins on its keys."""
    if entry is None or entry is False:
        # An explicit None/False entry keeps the entry; it names no field, so
        # __all__ still applies to fields the entry does not override.
        return _caller_all_base(left_all)
    if entry is True or entry is Ellipsis:
        return True
    base = _caller_all_base(left_all)
    entry_dict = _spec_as_dict(entry)
    if entry_dict is None:
        return entry
    if not isinstance(base, dict):
        return entry
    out = dict(base)
    for key, value in entry_dict.items():
        out[key] = _merge_all_entry(out[key], value) if key in out else value
    return out


def _merge_exclude_values(left: Any, right: Any) -> Any:
    if left is True or right is True or left is Ellipsis or right is Ellipsis:
        return True
    # A set containing ``__all__`` excludes every entry; pydantic does not let
    # an index in the same set retain anything. Presence rules are irrelevant
    # here, and merging a partial presence index beside ``__all__`` would turn
    # it into a dict whitelist entry and keep a run the caller excluded.
    if isinstance(left, (set, frozenset)) and "__all__" in left:
        return {**{key: True for key in left}, "__all__": True}
    # A caller may pass False to keep a field. It selects nothing, so the
    # other side is the whole answer.
    if left is None or left is False:
        return right
    if right is None or right is False:
        return left
    if isinstance(left, dict) and isinstance(right, dict):
        left_all = left.get("__all__", _MISSING)
        if left_all is True or left_all is Ellipsis:
            # The caller lists exactly the entries to retain. Presence rules
            # must apply to those entries but must not add entries of their own:
            # a dict entry key under ``__all__: True`` is a retention whitelist,
            # so merging an untouched presence index would keep a run the caller
            # excluded. Fold the rule for each retained (already normalized)
            # entry and leave the whitelist as written.
            merged = dict(left)
            right_all = right.get("__all__", _MISSING)
            for key in left:
                if key == "__all__":
                    continue
                current = merged[key]
                if current is True or current is Ellipsis:
                    continue
                rule = (
                    right_all
                    if isinstance(right_all, (dict, set, frozenset))
                    else right.get(key, _MISSING)
                )
                if rule is _MISSING:
                    continue
                merged[key] = _merge_exclude_values(current, rule)
            return merged
        right_all = right.get("__all__", _MISSING)
        merged: dict[Any, Any] = {}
        if left_all is not _MISSING or right_all is not _MISSING:
            left_spec = (
                _strip_none_false(left_all)
                if isinstance(left_all, dict)
                else left_all
            )
            right_spec = (
                _spec_as_dict(right_all)
                if isinstance(right_all, dict)
                else right_all
            )
            combined = _merge_exclude_values(
                left_spec if left_spec is not _MISSING else None,
                right_spec if right_spec is not _MISSING else None,
            )
            if combined is not None and combined is not False:
                merged["__all__"] = combined
        keys = [key for key in left if key != "__all__"]
        keys.extend(
            key for key in right if key != "__all__" and key not in left
        )
        for key in keys:
            caller_entry = left.get(key, _MISSING)
            presence_rule = right.get(key, _MISSING)
            if presence_rule is _MISSING and isinstance(
                right_all, (dict, set, frozenset)
            ):
                presence_rule = right_all
            if caller_entry is _MISSING:
                base = _caller_all_base(left_all)
            elif left_all is _MISSING or left_all is None or left_all is False:
                # No __all__ here: keep the caller's leaf exactly, including
                # None/False (which mean "keep") and nested dicts.
                base = caller_entry
            else:
                base = _caller_entry(left_all, caller_entry)
            if presence_rule is _MISSING:
                if caller_entry is not _MISSING:
                    merged[key] = base
                continue
            merged[key] = _merge_exclude_values(base, presence_rule)
        return merged
    if isinstance(left, dict):
        merged = dict(left)
        for key in right:
            merged[key] = True
        return merged
    if isinstance(right, dict):
        merged = dict(right)
        for key in left:
            merged[key] = True
        return merged
    return set(left) | set(right)


class _Strict(BaseModel):
    # extra="forbid" turns typos like "sha265" into a schema error instead of
    # silently ignoring them. Put custom fields in ``metadata``.
    model_config = ConfigDict(extra="forbid")

    # 0.2-only fields whose presence must survive dump/reload. A field listed
    # here is omitted from serialization when it was not supplied explicitly.
    # Explicit null/empty values remain present (and are validated separately).
    _zero_two_only_fields: ClassVar[tuple[str, ...]] = ()

    def _dump_kwargs(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        exclude = kwargs.get("exclude")
        include = kwargs.get("include")
        if exclude is not None:
            exclude = _normalize_selection(exclude, self)
            kwargs["exclude"] = exclude
        if include is not None:
            kwargs["include"] = _normalize_selection(include, self)

        # Presence rules are always merged, never pruned against the caller's
        # spec: pydantic lets an explicit entry win over ``__all__`` on a shared
        # key, so a caller partial spec must not be trusted to have dropped the
        # rule. The merge folds the rule into every explicit entry.
        merged = _merge_exclude(exclude, _presence_exclude(self))
        if merged is not None:
            kwargs["exclude"] = merged
        return kwargs

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return super().model_dump(*args, **self._dump_kwargs(kwargs))

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        return super().model_dump_json(*args, **self._dump_kwargs(kwargs))


def _reject_explicit_null(model: BaseModel, names: tuple[str, ...]) -> None:
    """Reject an explicit null where only omission or a value is allowed."""
    for name in names:
        if name in model.model_fields_set and getattr(model, name) is None:
            raise ValueError(f"{name} may be omitted but must not be null")


def _require_nonblank(value: Any, name: str) -> Any:
    if type(value) is str and value.strip() == "":
        raise ValueError(f"{name} must not be blank")
    return value


class EvaluationProtocol(_Strict):
    """A recorded evaluation protocol, resolved as a whole value."""

    _zero_two_only_fields = ("version",)

    protocol_id: StrictStr = Field(min_length=1)
    version: Optional[Union[StrictInt, StrictStr]] = None

    @field_validator("protocol_id")
    @classmethod
    def _protocol_id_nonblank(cls, v: str) -> str:
        return _require_nonblank(v, "protocol_id")

    @field_validator("version")
    @classmethod
    def _version_nonblank(cls, v: Any) -> Any:
        return _require_nonblank(v, "version")

    @model_validator(mode="after")
    def _no_explicit_null_version(self) -> "EvaluationProtocol":
        _reject_explicit_null(self, ("version",))
        return self


class MetricDefinition(_Strict):
    """A recorded metric definition, resolved as a whole value."""

    _zero_two_only_fields = ("higher_is_better", "averaging", "version")

    definition_id: StrictStr = Field(min_length=1)
    higher_is_better: Optional[StrictBool] = None
    averaging: Optional[StrictStr] = Field(default=None, min_length=1)
    version: Optional[Union[StrictInt, StrictStr]] = None

    @field_validator("definition_id", "averaging")
    @classmethod
    def _definition_strings_nonblank(cls, v: Any) -> Any:
        return _require_nonblank(v, "definition_id/averaging")

    @field_validator("version")
    @classmethod
    def _version_nonblank(cls, v: Any) -> Any:
        return _require_nonblank(v, "version")

    @model_validator(mode="after")
    def _no_explicit_nulls(self) -> "MetricDefinition":
        _reject_explicit_null(self, ("higher_is_better", "averaging", "version"))
        return self


class RunConditions(_Strict):
    """First-class recorded run conditions (schema 0.2).

    Omitted members fall back to legacy metadata. Explicit null means recorded
    UNKNOWN for the split/protocol/source_revision/environment dimensions;
    selector nulls and a null parent are invalid.
    """

    # The checkpoint selector intentionally starts with "model_"; it is data,
    # not a Pydantic protected name.
    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    _zero_two_only_fields = (
        "evaluation_split",
        "evaluation_protocol",
        "evaluation_dataset_artifact_id",
        "model_checkpoint_artifact_id",
        "source_revision",
        "environment",
    )

    evaluation_split: Optional[StrictStr] = Field(default=None, min_length=1)
    evaluation_protocol: Optional[EvaluationProtocol] = None
    evaluation_dataset_artifact_id: Optional[SelectorId] = None
    model_checkpoint_artifact_id: Optional[SelectorId] = None
    source_revision: Optional[StrictStr] = Field(default=None, min_length=1)
    environment: Optional[dict[str, Any]] = None

    @field_validator("evaluation_split", "source_revision")
    @classmethod
    def _strings_nonblank(cls, v: Any) -> Any:
        return _require_nonblank(v, "condition string")

    @model_validator(mode="after")
    def _no_explicit_null_selectors(self) -> "RunConditions":
        _reject_explicit_null(
            self,
            ("evaluation_dataset_artifact_id", "model_checkpoint_artifact_id"),
        )
        return self


class Project(_Strict):
    id: Id = Field(pattern=ID_PATTERN)
    name: str
    description: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Run(_Strict):
    _zero_two_only_fields = ("conditions",)

    id: Id = Field(pattern=ID_PATTERN)
    name: Optional[str] = None
    started_at: Optional[datetime] = None
    params: dict[str, Any] = Field(
        default_factory=dict, description="Hyperparameters or settings, free-form."
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    conditions: Optional[RunConditions] = None

    @model_validator(mode="after")
    def _no_explicit_null_conditions(self) -> "Run":
        _reject_explicit_null(self, ("conditions",))
        return self


class Artifact(_Strict):
    _zero_two_only_fields = ("dataset_id",)

    model_config = ConfigDict(json_schema_extra=_add_artifact_type_rules)


    id: Id = Field(pattern=ID_PATTERN)
    path: str = Field(
        min_length=1,
        description="Path relative to the manifest's folder, using forward slashes.",
    )
    artifact_type: Literal["file", "directory"] = Field(
        default="file",
        description=(
            "What the path names on disk. Omitted means file. 'directory' "
            "records a folder checked with --directory-artifacts and authored "
            "with add --directory or rehash --directory."
        ),
    )
    digest_format: Optional[Literal["runevidence-directory-v1"]] = Field(
        default=None,
        description=(
            "How sha256 was computed for a directory. Required for directories, "
            "absent for files."
        ),
    )
    sha256: Optional[str] = Field(default=None, pattern=SHA256_PATTERN)
    size_bytes: Optional[int] = Field(default=None, ge=0)
    volatile: bool = Field(
        default=False,
        description=(
            "Content is expected to change on every rerun (e.g. a log with timestamps). "
            "Only the file's existence is checked; sha256 and size_bytes are ignored."
        ),
    )
    kind: Optional[str] = Field(
        default=None,
        description="Free-form label, e.g. dataset, config, checkpoint, metrics, log.",
    )
    run_id: Optional[Id] = Field(default=None, description="Run that produced this file.")
    metadata: dict[str, Any] = Field(default_factory=dict)
    dataset_id: Optional[StrictStr] = Field(
        default=None,
        min_length=1,
        description=(
            "Semantic dataset label (schema 0.2). Not an artifact ID; multiple "
            "artifacts may share it."
        ),
    )

    @field_validator("sha256")
    @classmethod
    def _lowercase_digest(cls, v: Optional[str]) -> Optional[str]:
        return v.lower() if v is not None else v

    @field_validator("dataset_id")
    @classmethod
    def _dataset_id_nonblank(cls, v: Any) -> Any:
        return _require_nonblank(v, "dataset_id")

    @model_validator(mode="after")
    def _no_explicit_null_dataset_id(self) -> "Artifact":
        _reject_explicit_null(self, ("dataset_id",))
        return self

    @model_validator(mode="after")
    def _type_and_format_agree(self) -> "Artifact":
        if self.artifact_type == "directory" and self.digest_format is None:
            raise ValueError(
                f"a directory artifact needs digest_format {DIRECTORY_DIGEST_FORMAT!r}"
            )
        if self.artifact_type == "file" and self.digest_format is not None:
            raise ValueError("digest_format is only allowed on a directory artifact")
        return self


class JsonlLocator(_Strict):
    """Structured 0.3 locator for one physical JSONL line and a JSON Pointer.

    Only the ``jsonl`` kind is admitted by 0.3. ``line`` is one-based and uses
    ``StrictInt`` at runtime (the JSON Schema draft integer accepts ``1.0``;
    the model rejects it and booleans). ``pointer`` is empty or a slash-prefixed
    pointer using only ``~0``/``~1`` escapes.
    """

    kind: Literal["jsonl"] = Field(description="Locator kind; 0.3 admits only jsonl.")
    line: StrictInt = Field(
        ge=1,
        le=MAX_JSONL_LINE,
        description=(
            "One-based physical JSONL line. StrictInt runtime: 1.0 is schema-valid "
            "under draft 2020-12 but model-rejected; true/string are rejected."
        ),
    )
    pointer: StrictStr = Field(
        description="JSON Pointer inside the selected line; empty or /-prefixed, ~0/~1 only."
    )

    @field_validator("pointer")
    @classmethod
    def _pointer_grammar(cls, value: str) -> str:
        if not is_valid_locator_pointer(value):
            raise ValueError("locator pointer must be empty or use the JSON Pointer grammar")
        return value


class CsvCellLocator(_Strict):
    """Structured 0.4 locator for one CSV data cell.

    ``row`` is one-based over data records (the header is never selectable) and
    ``column`` is one-based over the parsed header. Both use ``StrictInt`` at
    runtime (the JSON Schema draft integer accepts ``1.0``; the model rejects
    it and booleans). ``header`` is the recorded header assertion at that
    column; it never appears in a finding or human display.
    """

    kind: Literal["csv-cell"] = Field(description="Locator kind; 0.4 admits csv-cell.")
    row: StrictInt = Field(
        ge=1,
        le=MAX_JSONL_LINE,
        description=(
            "One-based data record. StrictInt runtime: 1.0 is schema-valid under "
            "draft 2020-12 but model-rejected; true/string are rejected."
        ),
    )
    column: StrictInt = Field(
        ge=1,
        le=MAX_JSONL_LINE,
        description=(
            "One-based column in the parsed header. StrictInt runtime: 1.0 is "
            "schema-valid under draft 2020-12 but model-rejected; true/string "
            "are rejected."
        ),
    )
    header: StrictStr = Field(
        min_length=1,
        max_length=MAX_CSV_CELL_HEADER,
        description=(
            "Recorded header assertion at this column, in code points. A lone "
            "surrogate is rejected."
        ),
    )

    @field_validator("header", mode="after")
    @classmethod
    def _header_is_utf8_encodable(cls, value: str) -> str:
        # StrictStr accepts a lone surrogate in Python-mode validation. Encode
        # so a hand-built value is rejected exactly like the dict path, which
        # already reports string_unicode through the discriminated adapter.
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise PydanticCustomError(
                "string_unicode", "header contains a lone surrogate"
            ) from None
        return value


_LOCATOR_ADAPTER = TypeAdapter(
    Annotated[
        Union[JsonlLocator, CsvCellLocator],
        Field(discriminator="kind"),
    ]
)


class TensorboardLocator(_Strict):
    """Structured 0.5 locator for one supported TensorBoard scalar.

    ``tag`` is the exact event tag (no normalization or glob), at most 4,096
    code points and at most 4,096 UTF-8 bytes; a lone surrogate is rejected.
    ``step`` is a signed 64-bit integer; an absent ``Event.step`` means 0.
    ``occurrence`` is the required zero-based position among supported scalar
    entries with that exact ``(tag, step)`` in file order.
    """

    kind: Literal["tensorboard"] = Field(
        description="Locator kind; 0.5 admits tensorboard."
    )
    tag: StrictStr = Field(
        min_length=1,
        max_length=MAX_TENSORBOARD_TAG,
        description=(
            "Exact event tag, at most 4,096 code points and at most 4,096 "
            "UTF-8 bytes. A lone surrogate is rejected."
        ),
    )
    step: StrictInt = Field(
        ge=-(2**63),
        le=2**63 - 1,
        description=(
            "Signed 64-bit event step; an absent Event.step means 0. StrictInt "
            "runtime: 1.0 is schema-valid under draft 2020-12 but "
            "model-rejected; true/string are rejected."
        ),
    )
    occurrence: StrictInt = Field(
        ge=0,
        le=MAX_TENSORBOARD_OCCURRENCE,
        description=(
            "Required zero-based occurrence among supported scalars with this "
            "exact (tag, step). StrictInt runtime: 1.0 is schema-valid under "
            "draft 2020-12 but model-rejected; true/string are rejected."
        ),
    )

    @field_validator("tag", mode="after")
    @classmethod
    def _tag_is_utf8_encodable_and_bounded(cls, value: str) -> str:
        # StrictStr accepts a lone surrogate in Python-mode validation. Encode
        # so a hand-built value is rejected exactly like the dict path, which
        # already reports string_unicode through the discriminated adapter.
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError:
            raise PydanticCustomError(
                "string_unicode", "tag contains a lone surrogate"
            ) from None
        if len(encoded) > MAX_TENSORBOARD_TAG:
            raise ValueError(
                "tag must be at most 4096 UTF-8 bytes"
            )
        return value


class TensorboardStringLocator(_Strict):
    """Structured 0.6 locator for one supported TensorBoard string tensor.

    The fields keep the 0.5 ``tensorboard`` locator rules: exact tag, signed
    64-bit step and zero-based occurrence. Occurrence counts conforming string
    entries per artifact, separately from scalars.
    """

    kind: Literal["tensorboard-string"] = Field(
        description="Locator kind; 0.6 admits tensorboard-string."
    )
    tag: StrictStr = Field(
        min_length=1,
        max_length=MAX_TENSORBOARD_TAG,
        description=(
            "Exact event tag, at most 4,096 code points and at most 4,096 "
            "UTF-8 bytes. A lone surrogate is rejected."
        ),
    )
    step: StrictInt = Field(
        ge=-(2**63),
        le=2**63 - 1,
        description="Signed 64-bit event step; an absent Event.step means 0.",
    )
    occurrence: StrictInt = Field(
        ge=0,
        le=MAX_TENSORBOARD_OCCURRENCE,
        description=(
            "Required zero-based occurrence among conforming string entries "
            "with this exact (tag, step) in this artifact."
        ),
    )

    @field_validator("tag", mode="after")
    @classmethod
    def _tag_is_utf8_encodable_and_bounded(cls, value: str) -> str:
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError:
            raise PydanticCustomError(
                "string_unicode", "tag contains a lone surrogate"
            ) from None
        if len(encoded) > MAX_TENSORBOARD_TAG:
            raise ValueError("tag must be at most 4096 UTF-8 bytes")
        return value


class HistogramValue(_Strict):
    """Structured schema-0.7 TensorBoard histogram value.

    All eight keys are required. Numbers are finite binary64; JSON integers are
    accepted only when exactly representable. Limits are strictly increasing,
    arrays match, and there are at most 1,024 pairs.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    kind: Literal["histogram"] = Field(
        description="Value kind; 0.7 admits histogram."
    )
    min: float
    max: float
    num: float
    sum: float
    sum_squares: float
    bucket_limit: list[float]
    bucket: list[float]

    @field_validator("min", "max", "sum", "sum_squares", mode="before")
    @classmethod
    def _finite_float(cls, value: Any) -> float:
        return _exact_binary64(value)

    @field_validator("num", mode="before")
    @classmethod
    def _finite_nonnegative_num(cls, value: Any) -> float:
        return _exact_binary64(value, nonnegative=True)

    @field_validator("bucket_limit", "bucket", mode="before")
    @classmethod
    def _float_array(cls, value: Any) -> list[float]:
        if type(value) is not list:
            raise ValueError("a histogram array must be a list")
        return [_exact_binary64(item) for item in value]

    @field_validator("bucket", mode="after")
    @classmethod
    def _counts_nonnegative(cls, value: list[float]) -> list[float]:
        if any(item < 0 for item in value):
            raise ValueError("histogram counts must be nonnegative")
        return value

    @model_validator(mode="after")
    def _shape(self) -> "HistogramValue":
        if len(self.bucket_limit) != len(self.bucket):
            raise ValueError("histogram bucket arrays must have equal lengths")
        if len(self.bucket_limit) > 1024:
            raise ValueError("histogram has more than 1024 buckets")
        if any(a >= b for a, b in zip(self.bucket_limit, self.bucket_limit[1:])):
            raise ValueError("histogram bucket limits must be strictly increasing")
        return self


class ImageValue(_Strict):
    """Schema-0.8 reference to one PNG payload inside a TensorBoard event file.

    The digest covers the encoded bytes exactly as stored; nothing is
    extracted or decoded. ``width``/``height`` are the verified IHDR values.
    """

    kind: Literal["image"] = Field(description="Value kind; 0.8 admits image.")
    media_type: Literal["image/png"] = Field(
        description="Payload type; only PNG is a supported image."
    )
    sha256: str = Field(
        pattern=MEDIA_SHA256_PATTERN,
        description="Lowercase SHA-256 over the stored encoded_image_string bytes.",
    )
    size_bytes: StrictInt = Field(
        ge=1, le=16_777_216, description="Encoded payload length in bytes."
    )
    width: StrictInt = Field(ge=1, le=65_535, description="Verified IHDR width.")
    height: StrictInt = Field(ge=1, le=65_535, description="Verified IHDR height.")


class AudioValue(_Strict):
    """Schema-0.8 reference to one RIFF/WAVE payload inside a TensorBoard event file."""

    kind: Literal["audio"] = Field(description="Value kind; 0.8 admits audio.")
    media_type: Literal["audio/wav"] = Field(
        description="Payload type; only RIFF/WAVE is a supported audio payload."
    )
    sha256: str = Field(
        pattern=MEDIA_SHA256_PATTERN,
        description="Lowercase SHA-256 over the stored encoded_audio_string bytes.",
    )
    size_bytes: StrictInt = Field(
        ge=1, le=16_777_216, description="Encoded payload length in bytes."
    )


class TensorValue(_Strict):
    """Schema-0.9 canonical digest of one numeric tensor.

    ``sha256`` covers a canonical little-endian row-major encoding; nothing is
    decoded into the manifest. ``element_count`` must equal the shape product,
    which JSON Schema cannot express.
    """

    kind: Literal["tensor"] = Field(description="Value kind; 0.9 admits tensor.")
    dtype: Literal["float32", "float64", "int32", "int64"] = Field(
        description="Tensor element type; DT_FLOAT, DT_DOUBLE, DT_INT32 or DT_INT64."
    )
    shape: list[StrictInt] = Field(
        min_length=1,
        max_length=8,
        description="1-8 dimensions, each 1..2**31-1, row-major.",
    )
    element_count: StrictInt = Field(
        ge=1, le=1_000_000, description="Shape product; at most one million elements."
    )
    sha256: str = Field(
        pattern=MEDIA_SHA256_PATTERN,
        description="Lowercase SHA-256 over the canonical little-endian encoding.",
    )

    @field_validator("shape", mode="after")
    @classmethod
    def _shape_bounds(cls, value: list[int]) -> list[int]:
        if any(dimension < 1 or dimension > 2**31 - 1 for dimension in value):
            raise ValueError("tensor dimensions must be in 1..2**31-1")
        return value

    @model_validator(mode="after")
    def _count_matches_shape(self) -> "TensorValue":
        product = 1
        for dimension in self.shape:
            product *= dimension
        if product != self.element_count:
            raise ValueError("element_count must equal the shape product")
        return self


def _exact_binary64(value: Any, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("histogram numbers must be JSON numbers, not booleans or strings")
    if type(value) is int:
        try:
            converted = float(value)
        except OverflowError:
            raise ValueError("histogram integer is outside binary64") from None
        if converted != value:
            raise ValueError("histogram integer is not exactly representable as binary64")
        value = converted
    if not math.isfinite(value):
        raise ValueError("histogram numbers must be finite")
    if nonnegative and value < 0:
        raise ValueError("histogram num must be nonnegative")
    return 0.0 if value == 0.0 else value


class TensorboardHistogramLocator(_Strict):
    """Structured 0.7 locator for one supported TensorBoard histogram."""

    kind: Literal["tensorboard-histogram"] = Field(
        description="Locator kind; 0.7 admits tensorboard-histogram."
    )
    tag: StrictStr = Field(
        min_length=1,
        max_length=MAX_TENSORBOARD_TAG,
        description=(
            "Exact event tag, at most 4,096 code points and at most 4,096 "
            "UTF-8 bytes. A lone surrogate is rejected."
        ),
    )
    step: StrictInt = Field(
        ge=-(2**63),
        le=2**63 - 1,
        description="Signed 64-bit event step; an absent Event.step means 0.",
    )
    occurrence: StrictInt = Field(
        ge=0,
        le=MAX_TENSORBOARD_OCCURRENCE,
        description=(
            "Required zero-based occurrence among conforming histograms with "
            "this exact (tag, step) in this artifact."
        ),
    )

    @field_validator("tag", mode="after")
    @classmethod
    def _tag_is_utf8_encodable_and_bounded(cls, value: str) -> str:
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError:
            raise PydanticCustomError(
                "string_unicode", "tag contains a lone surrogate"
            ) from None
        if len(encoded) > MAX_TENSORBOARD_TAG:
            raise ValueError("tag must be at most 4096 UTF-8 bytes")
        return value


class TensorboardImageLocator(_Strict):
    """Structured 0.8 locator for one supported TensorBoard image summary."""

    kind: Literal["tensorboard-image"] = Field(
        description="Locator kind; 0.8 admits tensorboard-image."
    )
    tag: StrictStr = Field(
        min_length=1,
        max_length=MAX_TENSORBOARD_TAG,
        description=(
            "Exact event tag, at most 4,096 code points and at most 4,096 "
            "UTF-8 bytes. A lone surrogate is rejected."
        ),
    )
    step: StrictInt = Field(
        ge=-(2**63),
        le=2**63 - 1,
        description="Signed 64-bit event step; an absent Event.step means 0.",
    )
    occurrence: StrictInt = Field(
        ge=0,
        le=MAX_TENSORBOARD_OCCURRENCE,
        description=(
            "Required zero-based occurrence among conforming images with "
            "this exact (tag, step) in this artifact."
        ),
    )

    @field_validator("tag", mode="after")
    @classmethod
    def _tag_is_utf8_encodable_and_bounded(cls, value: str) -> str:
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError:
            raise PydanticCustomError(
                "string_unicode", "tag contains a lone surrogate"
            ) from None
        if len(encoded) > MAX_TENSORBOARD_TAG:
            raise ValueError("tag must be at most 4096 UTF-8 bytes")
        return value


class TensorboardAudioLocator(_Strict):
    """Structured 0.8 locator for one supported TensorBoard audio summary."""

    kind: Literal["tensorboard-audio"] = Field(
        description="Locator kind; 0.8 admits tensorboard-audio."
    )
    tag: StrictStr = Field(
        min_length=1,
        max_length=MAX_TENSORBOARD_TAG,
        description=(
            "Exact event tag, at most 4,096 code points and at most 4,096 "
            "UTF-8 bytes. A lone surrogate is rejected."
        ),
    )
    step: StrictInt = Field(
        ge=-(2**63),
        le=2**63 - 1,
        description="Signed 64-bit event step; an absent Event.step means 0.",
    )
    occurrence: StrictInt = Field(
        ge=0,
        le=MAX_TENSORBOARD_OCCURRENCE,
        description=(
            "Required zero-based occurrence among conforming audio entries "
            "with this exact (tag, step) in this artifact."
        ),
    )

    @field_validator("tag", mode="after")
    @classmethod
    def _tag_is_utf8_encodable_and_bounded(cls, value: str) -> str:
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError:
            raise PydanticCustomError(
                "string_unicode", "tag contains a lone surrogate"
            ) from None
        if len(encoded) > MAX_TENSORBOARD_TAG:
            raise ValueError("tag must be at most 4096 UTF-8 bytes")
        return value


class TensorboardTensorLocator(_Strict):
    """Structured 0.9 locator for one supported TensorBoard numeric tensor."""

    kind: Literal["tensorboard-tensor"] = Field(
        description="Locator kind; 0.9 admits tensorboard-tensor."
    )
    tag: StrictStr = Field(
        min_length=1,
        max_length=MAX_TENSORBOARD_TAG,
        description=(
            "Exact event tag, at most 4,096 code points and at most 4,096 "
            "UTF-8 bytes. A lone surrogate is rejected."
        ),
    )
    step: StrictInt = Field(
        ge=-(2**63),
        le=2**63 - 1,
        description="Signed 64-bit event step; an absent Event.step means 0.",
    )
    occurrence: StrictInt = Field(
        ge=0,
        le=MAX_TENSORBOARD_OCCURRENCE,
        description=(
            "Required zero-based occurrence among conforming tensors with "
            "this exact (tag, step) in this artifact."
        ),
    )

    @field_validator("tag", mode="after")
    @classmethod
    def _tag_is_utf8_encodable_and_bounded(cls, value: str) -> str:
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError:
            raise PydanticCustomError(
                "string_unicode", "tag contains a lone surrogate"
            ) from None
        if len(encoded) > MAX_TENSORBOARD_TAG:
            raise ValueError("tag must be at most 4096 UTF-8 bytes")
        return value


_LOCATOR_ADAPTER_05 = TypeAdapter(
    Annotated[
        Union[JsonlLocator, CsvCellLocator, TensorboardLocator],
        Field(discriminator="kind"),
    ]
)
_LOCATOR_ADAPTER_06 = TypeAdapter(
    Annotated[
        Union[
            JsonlLocator,
            CsvCellLocator,
            TensorboardLocator,
            TensorboardStringLocator,
        ],
        Field(discriminator="kind"),
    ]
)


def _dispatch_locator_04(value: Any) -> Any:
    """Normalize a 0.4 locator before the field union validates it."""
    if value is None:
        return None
    if type(value) is str:
        if not is_valid_locator_pointer(value):
            raise ValueError("locator must be empty or use the JSON Pointer grammar")
        return value
    if isinstance(value, (JsonlLocator, CsvCellLocator)):
        # Models are already validated; returning them avoids a second parse.
        return value
    if type(value) is dict:
        # A raised ValidationError propagates with the field location prefixed
        # and the discriminator tag in ``loc`` for the error-aware walker.
        return _LOCATOR_ADAPTER.validate_python(value)
    raise ValueError("locator must be a string, an object, or null")


def _dispatch_locator_05(value: Any) -> Any:
    """Normalize a 0.5 locator before the field union validates it."""
    if value is None:
        return None
    if type(value) is str:
        if not is_valid_locator_pointer(value):
            raise ValueError("locator must be empty or use the JSON Pointer grammar")
        return value
    if isinstance(value, (JsonlLocator, CsvCellLocator, TensorboardLocator)):
        # Models are already validated; returning them avoids a second parse.
        return value
    if type(value) is dict:
        # A raised ValidationError propagates with the field location prefixed
        # and the discriminator tag in ``loc`` for the error-aware walker.
        return _LOCATOR_ADAPTER_05.validate_python(value)
    raise ValueError("locator must be a string, an object, or null")


_LOCATOR_ADAPTER_07 = TypeAdapter(
    Annotated[
        Union[
            JsonlLocator,
            CsvCellLocator,
            TensorboardLocator,
            TensorboardStringLocator,
            TensorboardHistogramLocator,
        ],
        Field(discriminator="kind"),
    ]
)


def _dispatch_locator_06(value: Any) -> Any:
    """Normalize a 0.6 locator before the field union validates it."""
    if value is None:
        return None
    if type(value) is str:
        if not is_valid_locator_pointer(value):
            raise ValueError("locator must be empty or use the JSON Pointer grammar")
        return value
    if isinstance(
        value,
        (
            JsonlLocator,
            CsvCellLocator,
            TensorboardLocator,
            TensorboardStringLocator,
        ),
    ):
        return value
    if type(value) is dict:
        return _LOCATOR_ADAPTER_06.validate_python(value)
    raise ValueError("locator must be a string, an object, or null")


class Result(_Strict):
    _zero_two_only_fields = ("metric_definition",)

    id: Id = Field(pattern=ID_PATTERN)
    run_id: Id = Field(description="Run this result belongs to.")
    name: str = Field(description="What was measured, e.g. 'accuracy' or 'val_loss'.")
    value: Scalar = None
    artifact_id: Optional[Id] = Field(
        default=None, description="Artifact the value was read from (e.g. a metrics file)."
    )
    locator: Optional[Union[str, JsonlLocator]] = Field(
        default=None,
        description=(
            "RFC 6901 JSON Pointer into a .json artifact (e.g. '/eval/accuracy'), "
            "or a structured 0.3 locator object. In 0.3 any non-null locator "
            "requires artifact_id."
        ),
    )
    metadata: dict[str, Any] = Field(default_factory=dict)
    metric_definition: Optional[MetricDefinition] = None

    @field_validator("locator", mode="before")
    @classmethod
    def _locator_scalar_shape(cls, value: Any) -> Any:
        # A plain union reports only branch-level locations for a scalar such
        # as 5. Reject it here so load diagnostics point at
        # /results/N/locator instead of losing the location.
        if value is None or type(value) is str or type(value) is dict:
            return value
        if isinstance(value, JsonlLocator):
            return value
        raise ValueError("locator must be a string, an object, or null")


class Result04(Result):
    """0.4 result: a string pointer, a ``jsonl`` object, a ``csv-cell`` object, or null."""

    locator: Annotated[
        Optional[Union[StrictStr, JsonlLocator, CsvCellLocator]],
        BeforeValidator(_dispatch_locator_04),
    ] = None

    @field_validator("locator", mode="before")
    @classmethod
    def _locator_scalar_shape(cls, value: Any) -> Any:
        # Override the 0.3 scalar-shape guard: an already-built CsvCellLocator
        # must reach the dispatcher instead of being rejected by the base
        # validator. A plain union would report only branch-level locations
        # for a scalar such as 5, so keep rejecting it at the field.
        if value is None or type(value) is str or type(value) is dict:
            return value
        if isinstance(value, (JsonlLocator, CsvCellLocator)):
            return value
        raise ValueError("locator must be a string, an object, or null")


class Result05(Result):
    """0.5 result: a string pointer, a structured locator object, or null.

    The objects are ``jsonl``, ``csv-cell`` and ``tensorboard``.
    """

    locator: Annotated[
        Optional[
            Union[StrictStr, JsonlLocator, CsvCellLocator, TensorboardLocator]
        ],
        BeforeValidator(_dispatch_locator_05),
    ] = None

    @field_validator("locator", mode="before")
    @classmethod
    def _locator_scalar_shape(cls, value: Any) -> Any:
        # Override the base scalar-shape guard: every 0.5 locator model must
        # reach the dispatcher instead of being rejected by the base validator.
        # A plain union would report only branch-level locations for a scalar
        # such as 5, so keep rejecting it at the field.
        if value is None or type(value) is str or type(value) is dict:
            return value
        if isinstance(
            value, (JsonlLocator, CsvCellLocator, TensorboardLocator)
        ):
            return value
        raise ValueError("locator must be a string, an object, or null")


def _dispatch_locator_07(value: Any) -> Any:
    """Normalize a 0.7 locator before the field union validates it."""
    if value is None:
        return None
    if type(value) is str:
        if not is_valid_locator_pointer(value):
            raise ValueError("locator must be empty or use the JSON Pointer grammar")
        return value
    if isinstance(
        value,
        (
            JsonlLocator,
            CsvCellLocator,
            TensorboardLocator,
            TensorboardStringLocator,
            TensorboardHistogramLocator,
        ),
    ):
        return value
    if type(value) is dict:
        return _LOCATOR_ADAPTER_07.validate_python(value)
    raise ValueError("locator must be a string, an object, or null")


def _dispatch_value_07(value: Any) -> Any:
    if type(value) is dict and value.get("kind") == "histogram":
        return HistogramValue.model_validate(value)
    return value


class Result06(Result):
    """0.6 result: 0.5 locators plus the ``tensorboard-string`` object."""

    locator: Annotated[
        Optional[
            Union[
                StrictStr,
                JsonlLocator,
                CsvCellLocator,
                TensorboardLocator,
                TensorboardStringLocator,
            ]
        ],
        BeforeValidator(_dispatch_locator_06),
    ] = None

    @field_validator("locator", mode="before")
    @classmethod
    def _locator_scalar_shape(cls, value: Any) -> Any:
        if value is None or type(value) is str or type(value) is dict:
            return value
        if isinstance(
            value,
            (
                JsonlLocator,
                CsvCellLocator,
                TensorboardLocator,
                TensorboardStringLocator,
            ),
        ):
            return value
        raise ValueError("locator must be a string, an object, or null")


class Result07(Result):
    """0.7 result: 0.6 locators plus ``tensorboard-histogram`` and a histogram value."""

    value: Annotated[
        Union[Scalar, HistogramValue], BeforeValidator(_dispatch_value_07)
    ] = None
    locator: Annotated[
        Optional[
            Union[
                StrictStr,
                JsonlLocator,
                CsvCellLocator,
                TensorboardLocator,
                TensorboardStringLocator,
                TensorboardHistogramLocator,
            ]
        ],
        BeforeValidator(_dispatch_locator_07),
    ] = None

    @field_validator("locator", mode="before")
    @classmethod
    def _locator_scalar_shape(cls, value: Any) -> Any:
        if value is None or type(value) is str or type(value) is dict:
            return value
        if isinstance(
            value,
            (
                JsonlLocator,
                CsvCellLocator,
                TensorboardLocator,
                TensorboardStringLocator,
                TensorboardHistogramLocator,
            ),
        ):
            return value
        raise ValueError("locator must be a string, an object, or null")

    @model_validator(mode="after")
    def _histogram_value_locator(self) -> "Result07":
        if isinstance(self.value, HistogramValue):
            locator = self.locator
            if locator is not None and getattr(locator, "kind", None) != "tensorboard-histogram":
                raise ValueError(
                    "a histogram value requires a tensorboard-histogram or null locator"
                )
        return self


_LOCATOR_ADAPTER_08 = TypeAdapter(
    Annotated[
        Union[
            JsonlLocator,
            CsvCellLocator,
            TensorboardLocator,
            TensorboardStringLocator,
            TensorboardHistogramLocator,
            TensorboardImageLocator,
            TensorboardAudioLocator,
        ],
        Field(discriminator="kind"),
    ]
)


def _dispatch_locator_08(value: Any) -> Any:
    """Normalize a 0.8 locator before the field union validates it."""
    if value is None:
        return None
    if type(value) is str:
        if not is_valid_locator_pointer(value):
            raise ValueError("locator must be empty or use the JSON Pointer grammar")
        return value
    if isinstance(
        value,
        (
            JsonlLocator,
            CsvCellLocator,
            TensorboardLocator,
            TensorboardStringLocator,
            TensorboardHistogramLocator,
            TensorboardImageLocator,
            TensorboardAudioLocator,
        ),
    ):
        return value
    if type(value) is dict:
        return _LOCATOR_ADAPTER_08.validate_python(value)
    raise ValueError("locator must be a string, an object, or null")


def _dispatch_value_08(value: Any) -> Any:
    if type(value) is dict:
        kind = value.get("kind")
        if kind == "histogram":
            return HistogramValue.model_validate(value)
        if kind == "image":
            return ImageValue.model_validate(value)
        if kind == "audio":
            return AudioValue.model_validate(value)
    return value


class Result08(Result):
    """0.8 result: 0.7 locators plus ``tensorboard-image`` and ``tensorboard-audio``.

    Object values are pinned to their own locator kind or ``null``; scalars stay
    legal on every locator kind, exactly as in 0.7.
    """

    value: Annotated[
        Union[Scalar, HistogramValue, ImageValue, AudioValue],
        BeforeValidator(_dispatch_value_08),
    ] = None
    locator: Annotated[
        Optional[
            Union[
                StrictStr,
                JsonlLocator,
                CsvCellLocator,
                TensorboardLocator,
                TensorboardStringLocator,
                TensorboardHistogramLocator,
                TensorboardImageLocator,
                TensorboardAudioLocator,
            ]
        ],
        BeforeValidator(_dispatch_locator_08),
    ] = None

    @field_validator("locator", mode="before")
    @classmethod
    def _locator_scalar_shape(cls, value: Any) -> Any:
        if value is None or type(value) is str or type(value) is dict:
            return value
        if isinstance(
            value,
            (
                JsonlLocator,
                CsvCellLocator,
                TensorboardLocator,
                TensorboardStringLocator,
                TensorboardHistogramLocator,
                TensorboardImageLocator,
                TensorboardAudioLocator,
            ),
        ):
            return value
        raise ValueError("locator must be a string, an object, or null")

    @model_validator(mode="after")
    def _media_value_locator(self) -> "Result08":
        expected = (
            (HistogramValue, "histogram", "tensorboard-histogram"),
            (ImageValue, "image", "tensorboard-image"),
            (AudioValue, "audio", "tensorboard-audio"),
        )
        for model, label, kind in expected:
            if isinstance(self.value, model):
                locator = self.locator
                if locator is not None and getattr(locator, "kind", None) != kind:
                    raise ValueError(
                        f"a {label} value requires a {kind} or null locator"
                    )
        return self


_LOCATOR_ADAPTER_09 = TypeAdapter(
    Annotated[
        Union[
            JsonlLocator,
            CsvCellLocator,
            TensorboardLocator,
            TensorboardStringLocator,
            TensorboardHistogramLocator,
            TensorboardImageLocator,
            TensorboardAudioLocator,
            TensorboardTensorLocator,
        ],
        Field(discriminator="kind"),
    ]
)


def _dispatch_locator_09(value: Any) -> Any:
    """Normalize a 0.9 locator before the field union validates it."""
    if value is None:
        return None
    if type(value) is str:
        if not is_valid_locator_pointer(value):
            raise ValueError("locator must be empty or use the JSON Pointer grammar")
        return value
    if isinstance(
        value,
        (
            JsonlLocator,
            CsvCellLocator,
            TensorboardLocator,
            TensorboardStringLocator,
            TensorboardHistogramLocator,
            TensorboardImageLocator,
            TensorboardAudioLocator,
            TensorboardTensorLocator,
        ),
    ):
        return value
    if type(value) is dict:
        return _LOCATOR_ADAPTER_09.validate_python(value)
    raise ValueError("locator must be a string, an object, or null")


def _dispatch_value_09(value: Any) -> Any:
    if type(value) is dict:
        kind = value.get("kind")
        if kind == "histogram":
            return HistogramValue.model_validate(value)
        if kind == "image":
            return ImageValue.model_validate(value)
        if kind == "audio":
            return AudioValue.model_validate(value)
        if kind == "tensor":
            return TensorValue.model_validate(value)
    return value


class Result09(Result):
    """0.9 result: 0.8 locators plus ``tensorboard-tensor`` and a tensor value."""

    value: Annotated[
        Union[Scalar, HistogramValue, ImageValue, AudioValue, TensorValue],
        BeforeValidator(_dispatch_value_09),
    ] = None
    locator: Annotated[
        Optional[
            Union[
                StrictStr,
                JsonlLocator,
                CsvCellLocator,
                TensorboardLocator,
                TensorboardStringLocator,
                TensorboardHistogramLocator,
                TensorboardImageLocator,
                TensorboardAudioLocator,
                TensorboardTensorLocator,
            ]
        ],
        BeforeValidator(_dispatch_locator_09),
    ] = None

    @field_validator("locator", mode="before")
    @classmethod
    def _locator_scalar_shape(cls, value: Any) -> Any:
        if value is None or type(value) is str or type(value) is dict:
            return value
        if isinstance(
            value,
            (
                JsonlLocator,
                CsvCellLocator,
                TensorboardLocator,
                TensorboardStringLocator,
                TensorboardHistogramLocator,
                TensorboardImageLocator,
                TensorboardAudioLocator,
                TensorboardTensorLocator,
            ),
        ):
            return value
        raise ValueError("locator must be a string, an object, or null")

    @model_validator(mode="after")
    def _media_value_locator(self) -> "Result09":
        expected = (
            (HistogramValue, "histogram", "tensorboard-histogram"),
            (ImageValue, "image", "tensorboard-image"),
            (AudioValue, "audio", "tensorboard-audio"),
            (TensorValue, "tensor", "tensorboard-tensor"),
        )
        for model, label, kind in expected:
            if isinstance(self.value, model):
                locator = self.locator
                if locator is not None and getattr(locator, "kind", None) != kind:
                    raise ValueError(
                        f"a {label} value requires a {kind} or null locator"
                    )
        return self


class EvidenceReference(_Strict):
    id: Id = Field(pattern=ID_PATTERN)
    claim: str = Field(min_length=1, description="The statement being made, in plain words.")
    supports: list[Id] = Field(
        default_factory=list, description="IDs of results, artifacts or runs backing the claim."
    )
    contradicts: list[Id] = Field(
        default_factory=list, description="IDs of results, artifacts or runs that cut against it."
    )
    source: Optional[str] = Field(
        default=None, description="Where the claim is made, e.g. 'README.md#results'."
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class RelationshipType(str, Enum):
    produced_by = "produced_by"  # artifact -> run
    input_to = "input_to"  # artifact -> run
    result_of = "result_of"  # result -> run
    reported_in = "reported_in"  # result -> artifact
    derived_from = "derived_from"  # artifact -> artifact, run -> run
    supports = "supports"  # evidence -> result | artifact | run
    contradicts = "contradicts"  # evidence -> result | artifact | run


class Relationship(_Strict):
    source: Id
    target: Id
    type: RelationshipType
    note: Optional[str] = None


class Manifest(_Strict):
    schema_version: Literal["0.1", "0.2", "0.3"] = SCHEMA_VERSION
    project: Project
    runs: list[Run] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    results: list[Result] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    relationships: list[Relationship] = Field(default_factory=list)

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        data = super().model_dump(*args, **kwargs)
        if self.schema_version in (
            SCHEMA_VERSION_0_2,
            SCHEMA_VERSION_0_3,
            SCHEMA_VERSION_0_4,
            SCHEMA_VERSION_0_5,
            SCHEMA_VERSION_0_6,
            SCHEMA_VERSION_0_7,
            SCHEMA_VERSION_0_8,
            SCHEMA_VERSION_0_9,
        ):
            # Default fields keep their normal 0.1 spelling. The per-model
            # presence overrides already removed only unset 0.2+ members.
            if "schema_version" not in data:
                data = {"schema_version": self.schema_version, **data}
            return data
        # 0.1 output never contains a 0.2-only key, including null/default forms.
        _strip_0_2_keys(data)
        return data

    @model_validator(mode="after")
    def _version_specific_shape(self) -> "Manifest":
        if self.schema_version == SCHEMA_VERSION:
            for run in self.runs:
                if "conditions" in run.model_fields_set:
                    raise ValueError("conditions requires schema_version 0.2")
            for artifact in self.artifacts:
                if "dataset_id" in artifact.model_fields_set:
                    raise ValueError("dataset_id requires schema_version 0.2")
            for result in self.results:
                if "metric_definition" in result.model_fields_set:
                    raise ValueError("metric_definition requires schema_version 0.2")

        if self.schema_version == SCHEMA_VERSION_0_3:
            for result in self.results:
                locator = result.locator
                if locator is None:
                    continue
                if result.artifact_id is None:
                    raise ValueError("a schema 0.3 locator requires artifact_id")
                if type(locator) is str:
                    if not is_valid_locator_pointer(locator):
                        raise ValueError(
                            "a schema 0.3 string locator must be empty or a JSON Pointer"
                        )
                elif not isinstance(locator, JsonlLocator):
                    raise ValueError("a schema 0.3 locator must be a string or a jsonl object")
        elif self.schema_version == SCHEMA_VERSION_0_4:
            for result in self.results:
                locator = result.locator
                if locator is None:
                    continue
                if result.artifact_id is None:
                    raise ValueError("a schema 0.4 locator requires artifact_id")
                if type(locator) is str:
                    if not is_valid_locator_pointer(locator):
                        raise ValueError(
                            "a schema 0.4 string locator must be empty or a JSON Pointer"
                        )
                elif not isinstance(locator, (JsonlLocator, CsvCellLocator)):
                    raise ValueError(
                        "a schema 0.4 locator must be a string, a jsonl object, "
                        "or a csv-cell object"
                    )
        elif self.schema_version == SCHEMA_VERSION_0_5:
            for result in self.results:
                locator = result.locator
                if locator is None:
                    continue
                if result.artifact_id is None:
                    raise ValueError("a schema 0.5 locator requires artifact_id")
                if type(locator) is str:
                    if not is_valid_locator_pointer(locator):
                        raise ValueError(
                            "a schema 0.5 string locator must be empty or a JSON Pointer"
                        )
                elif not isinstance(
                    locator,
                    (JsonlLocator, CsvCellLocator, TensorboardLocator),
                ):
                    raise ValueError(
                        "a schema 0.5 locator must be a string, a jsonl object, "
                        "a csv-cell object, or a tensorboard object"
                    )
        elif self.schema_version == SCHEMA_VERSION_0_6:
            for result in self.results:
                locator = result.locator
                if locator is None:
                    continue
                if result.artifact_id is None:
                    raise ValueError("a schema 0.6 locator requires artifact_id")
                if type(locator) is str:
                    if not is_valid_locator_pointer(locator):
                        raise ValueError(
                            "a schema 0.6 string locator must be empty or a JSON Pointer"
                        )
                elif not isinstance(
                    locator,
                    (
                        JsonlLocator,
                        CsvCellLocator,
                        TensorboardLocator,
                        TensorboardStringLocator,
                    ),
                ):
                    raise ValueError(
                        "a schema 0.6 locator must be a string, a jsonl object, "
                        "a csv-cell object, a tensorboard object, or a "
                        "tensorboard-string object"
                    )
        elif self.schema_version == SCHEMA_VERSION_0_7:
            for result in self.results:
                locator = result.locator
                if locator is None:
                    continue
                if result.artifact_id is None:
                    raise ValueError("a schema 0.7 locator requires artifact_id")
                if type(locator) is str:
                    if not is_valid_locator_pointer(locator):
                        raise ValueError(
                            "a schema 0.7 string locator must be empty or a JSON Pointer"
                        )
                elif not isinstance(
                    locator,
                    (
                        JsonlLocator,
                        CsvCellLocator,
                        TensorboardLocator,
                        TensorboardStringLocator,
                        TensorboardHistogramLocator,
                    ),
                ):
                    raise ValueError(
                        "a schema 0.7 locator must be a string, a jsonl object, "
                        "a csv-cell object, a tensorboard object, a "
                        "tensorboard-string object, or a tensorboard-histogram object"
                    )
        elif self.schema_version == SCHEMA_VERSION_0_8:
            for result in self.results:
                locator = result.locator
                if locator is None:
                    continue
                if result.artifact_id is None:
                    raise ValueError("a schema 0.8 locator requires artifact_id")
                if type(locator) is str:
                    if not is_valid_locator_pointer(locator):
                        raise ValueError(
                            "a schema 0.8 string locator must be empty or a JSON Pointer"
                        )
                elif not isinstance(
                    locator,
                    (
                        JsonlLocator,
                        CsvCellLocator,
                        TensorboardLocator,
                        TensorboardStringLocator,
                        TensorboardHistogramLocator,
                        TensorboardImageLocator,
                        TensorboardAudioLocator,
                    ),
                ):
                    raise ValueError(
                        "a schema 0.8 locator must be a string, a jsonl object, "
                        "a csv-cell object, a tensorboard object, a "
                        "tensorboard-string object, a tensorboard-histogram object, "
                        "a tensorboard-image object, or a tensorboard-audio object"
                    )
        elif self.schema_version == SCHEMA_VERSION_0_9:
            for result in self.results:
                locator = result.locator
                if locator is None:
                    continue
                if result.artifact_id is None:
                    raise ValueError("a schema 0.9 locator requires artifact_id")
                if type(locator) is str:
                    if not is_valid_locator_pointer(locator):
                        raise ValueError(
                            "a schema 0.9 string locator must be empty or a JSON Pointer"
                        )
                elif not isinstance(
                    locator,
                    (
                        JsonlLocator,
                        CsvCellLocator,
                        TensorboardLocator,
                        TensorboardStringLocator,
                        TensorboardHistogramLocator,
                        TensorboardImageLocator,
                        TensorboardAudioLocator,
                        TensorboardTensorLocator,
                    ),
                ):
                    raise ValueError(
                        "a schema 0.9 locator must be a string, a jsonl object, "
                        "a csv-cell object, a tensorboard object, a "
                        "tensorboard-string object, a tensorboard-histogram object, "
                        "a tensorboard-image object, a tensorboard-audio object, or a "
                        "tensorboard-tensor object"
                    )
        else:
            for result in self.results:
                if isinstance(result.locator, JsonlLocator):
                    raise ValueError("object locators require schema_version 0.3")
        return self


class Manifest04(Manifest):
    """Schema 0.4 manifest: string, jsonl and csv-cell locators."""

    schema_version: Literal["0.4"]
    results: list[Result04] = Field(default_factory=list)


class Manifest05(Manifest):
    """Schema 0.5 manifest: string, jsonl, csv-cell and tensorboard locators."""

    schema_version: Literal["0.5"]
    results: list[Result05] = Field(default_factory=list)


class Manifest06(Manifest):
    """Schema 0.6 manifest: 0.5 locators plus tensorboard-string."""

    schema_version: Literal["0.6"]
    results: list[Result06] = Field(default_factory=list)


class Manifest07(Manifest):
    """Schema 0.7 manifest: 0.6 locators plus tensorboard-histogram."""

    schema_version: Literal["0.7"]
    results: list[Result07] = Field(default_factory=list)


class Manifest08(Manifest):
    """Schema 0.8 manifest: 0.7 locators plus tensorboard-image and -audio."""

    schema_version: Literal["0.8"]
    results: list[Result08] = Field(default_factory=list)


class Manifest09(Manifest):
    """Schema 0.9 manifest: 0.8 locators plus tensorboard-tensor."""

    schema_version: Literal["0.9"]
    results: list[Result09] = Field(default_factory=list)


def is_manifest(value: object) -> bool:
    """True for every manifest model the dispatcher can return."""
    return isinstance(
        value, (Manifest, Manifest04, Manifest05, Manifest06, Manifest07, Manifest08)
    )


def _strip_0_2_keys(data: dict[str, Any]) -> None:
    for run in data.get("runs") or []:
        if isinstance(run, dict):
            run.pop("conditions", None)
    for artifact in data.get("artifacts") or []:
        if isinstance(artifact, dict):
            artifact.pop("dataset_id", None)
    for result in data.get("results") or []:
        if isinstance(result, dict):
            result.pop("metric_definition", None)


def _nonnull_property(prop: dict[str, Any]) -> dict[str, Any]:
    """Publish a property as optional but non-null when present."""
    out = copy.deepcopy(prop)
    out.pop("default", None)
    options = out.get("anyOf")
    if isinstance(options, list):
        remaining = [option for option in options if option.get("type") != "null"]
        if len(remaining) == 1:
            merged = dict(remaining[0])
            for key in ("title", "description"):
                if key in out:
                    merged[key] = out[key]
            return merged
        out["anyOf"] = remaining
    return out


def _pin_schema_version(schema: dict[str, Any], version: str, *, required: bool) -> None:
    prop = schema["properties"]["schema_version"]
    prop.clear()
    prop.update({"const": version, "default": version, "title": "Schema Version", "type": "string"})
    if required:
        entries = schema.setdefault("required", [])
        if "schema_version" not in entries:
            entries.insert(0, "schema_version")


_LOCATOR_POINTER_JSON_PATTERN = LOCATOR_POINTER_PATTERN_TEXT


def _locator_0_1_property() -> dict[str, Any]:
    """The frozen 0.1/0.2 locator property, restored from the 0.3 model union."""
    return {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "default": None,
        "description": (
            "RFC 6901 JSON Pointer into a .json artifact (e.g. '/eval/accuracy'). "
            "When set, the recorded value is compared against the file."
        ),
        "title": "Locator",
    }


def _restore_locator_0_1(schema: dict[str, Any]) -> None:
    """Remove the 0.3 object branch and restore the frozen string-only locator."""
    defs = schema.get("$defs", {})
    defs.pop("JsonlLocator", None)
    defs.pop("CsvCellLocator", None)
    defs["Result"]["properties"]["locator"] = _locator_0_1_property()


def _restrict_schema_to_0_1(schema: dict[str, Any]) -> None:
    """Remove every 0.2/0.3-only branch from the generated model schema."""
    defs = schema.get("$defs", {})
    for name in ("EvaluationProtocol", "MetricDefinition", "RunConditions"):
        defs.pop(name, None)
    defs["Run"]["properties"].pop("conditions", None)
    defs["Artifact"]["properties"].pop("dataset_id", None)
    defs["Result"]["properties"].pop("metric_definition", None)
    _restore_locator_0_1(schema)
    _pin_schema_version(schema, SCHEMA_VERSION, required=False)


def _add_nonblank_constraints(prop: dict[str, Any]) -> dict[str, Any]:
    """Publish nonblank string constraints on every string branch."""
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "string":
                node["minLength"] = max(int(node.get("minLength", 0)), 1)
                node["pattern"] = r"\S"
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    out = copy.deepcopy(prop)
    walk(out)
    return out


def _publish_common_0_2(schema: dict[str, Any]) -> None:
    defs = schema["$defs"]
    # Explicit null is invalid for the conditions parent, dataset_id, the two
    # selectors and nested protocol/definition optionals. Null remains valid
    # for split, protocol, metric_definition, source_revision and environment.
    defs["Run"]["properties"]["conditions"] = _nonnull_property(
        defs["Run"]["properties"]["conditions"]
    )
    defs["Artifact"]["properties"]["dataset_id"] = _nonnull_property(
        defs["Artifact"]["properties"]["dataset_id"]
    )
    conditions = defs["RunConditions"]["properties"]
    for name in ("evaluation_dataset_artifact_id", "model_checkpoint_artifact_id"):
        conditions[name] = _nonnull_property(conditions[name])
    for name in ("version",):
        defs["EvaluationProtocol"]["properties"][name] = _nonnull_property(
            defs["EvaluationProtocol"]["properties"][name]
        )
    for name in ("higher_is_better", "averaging", "version"):
        defs["MetricDefinition"]["properties"][name] = _nonnull_property(
            defs["MetricDefinition"]["properties"][name]
        )

    # Nonblank constraints, applied to every first-class string property and
    # to the string branch of each version union.
    for owner, name in (
        ("RunConditions", "evaluation_split"),
        ("RunConditions", "source_revision"),
        ("EvaluationProtocol", "protocol_id"),
        ("EvaluationProtocol", "version"),
        ("MetricDefinition", "definition_id"),
        ("MetricDefinition", "averaging"),
        ("MetricDefinition", "version"),
        ("Artifact", "dataset_id"),
    ):
        defs[owner]["properties"][name] = _add_nonblank_constraints(
            defs[owner]["properties"][name]
        )


def _canonicalize_schema(schema: dict[str, Any]) -> None:
    """Fill the keys pydantic 2.6 omits but 2.14 emits, in the 2.14 position.

    The frozen files under ``schema/`` were generated with pydantic 2.14.3.
    Pydantic 2.6.0 leaves two shapes out, so the same models render different
    bytes: a bare object node has no ``additionalProperties: true``, and a
    string ``const`` node (a ``Literal``) has no ``type: "string"``. Both are
    pure restatements of what the runtime validator already means, so adding
    them here keeps the generated bytes identical on both versions. 2.14
    already has both keys, which makes this a no-op there.
    """
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "additionalProperties" not in node:
                # 2.14 lists additionalProperties first; insertion order is
                # part of the frozen bytes.
                rest = {key: value for key, value in node.items()}
                node.clear()
                node["additionalProperties"] = True
                node.update(rest)
            if (
                "const" in node
                and "type" not in node
                and type(node["const"]) is str
            ):
                # 2.14 renders a Literal branch as const, description, title,
                # type; `type` comes last.
                node["type"] = "string"
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)


def _generated_schema() -> dict[str, Any]:
    """The canonical JSON Schema rendered by the installed pydantic."""
    schema = Manifest.model_json_schema()
    _canonicalize_schema(schema)
    # The artifact_type/digest_format guard is hand-authored and already has
    # the frozen spelling. Canonicalization gives its bare `const` branches a
    # `type`, so restore the rules verbatim afterwards. Re-applying the same
    # keys keeps their position in the definition.
    _add_artifact_type_rules(schema["$defs"]["Artifact"])
    return schema


def _generated_schema_0_4() -> dict[str, Any]:
    """The canonical JSON Schema for Manifest04, before version fixes."""
    schema = Manifest04.model_json_schema()
    _canonicalize_schema(schema)
    _add_artifact_type_rules(schema["$defs"]["Artifact"])
    return schema


def _generated_schema_0_5() -> dict[str, Any]:
    """The canonical JSON Schema for Manifest05, before version fixes."""
    schema = Manifest05.model_json_schema()
    _canonicalize_schema(schema)
    _add_artifact_type_rules(schema["$defs"]["Artifact"])
    return schema


def _publish_schema_0_2(schema: dict[str, Any]) -> None:
    _publish_common_0_2(schema)
    _restore_locator_0_1(schema)
    _pin_schema_version(schema, SCHEMA_VERSION_0_2, required=True)


def json_schema() -> dict[str, Any]:
    """JSON Schema for the 0.1 manifest, generated from the Pydantic models."""
    schema = _generated_schema()
    _restrict_schema_to_0_1(schema)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:runevidence:manifest:{SCHEMA_VERSION}"
    return schema


def json_schema_0_2() -> dict[str, Any]:
    """JSON Schema for the 0.2 manifest, generated from the Pydantic models.

    Distinct ``$id`` and an explicit ``schema_version`` const 0.2. Omitted
    schema_version means 0.1, so the 0.2 schema requires the declaration.
    """
    schema = _generated_schema()
    _publish_schema_0_2(schema)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:runevidence:manifest:{SCHEMA_VERSION_0_2}"
    return schema


def json_schema_0_3() -> dict[str, Any]:
    """JSON Schema for the 0.3 manifest (structured locator union).

    It keeps every 0.2 field and adds the string/``jsonl`` object locator
    union. A non-null locator requires ``artifact_id``. Draft 2020-12 accepts
    ``1.0`` for an integer; the runtime model uses ``StrictInt`` and rejects it,
    as documented on the ``line`` property.
    """
    schema = _generated_schema()
    _publish_common_0_2(schema)
    defs = schema["$defs"]
    jsonl = defs["JsonlLocator"]
    jsonl["properties"]["pointer"]["pattern"] = _LOCATOR_POINTER_JSON_PATTERN
    jsonl["properties"]["pointer"]["description"] = (
        "JSON Pointer inside the selected line; empty or /-prefixed, "
        "with only ~0/~1 escapes."
    )
    defs["Result"]["properties"]["locator"] = {
        "oneOf": [
            {
                "type": "string",
                "pattern": _LOCATOR_POINTER_JSON_PATTERN,
                "description": (
                    "Empty or a JSON Pointer string with only ~0/~1 escapes into "
                    "a .json artifact."
                ),
            },
            {"$ref": "#/$defs/JsonlLocator"},
            {"type": "null"},
        ],
        "default": None,
        "title": "Locator",
        "description": (
            "RFC 6901 JSON Pointer string, a structured jsonl locator object, "
            "or null. In 0.3 any non-null locator requires artifact_id."
        ),
    }
    defs["Result"]["if"] = {
        "required": ["locator"],
        "properties": {"locator": {"not": {"type": "null"}}},
    }
    defs["Result"]["then"] = {
        "required": ["artifact_id"],
        "properties": {"artifact_id": {"type": "string"}},
    }
    _pin_schema_version(schema, SCHEMA_VERSION_0_3, required=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:runevidence:manifest:{SCHEMA_VERSION_0_3}"
    return schema


def json_schema_0_4() -> dict[str, Any]:
    """JSON Schema for the 0.4 manifest (string/``jsonl``/``csv-cell`` locators).

    The 0.3 file stays byte-frozen. This schema keeps every 0.2 field, the
    0.3 ``jsonl`` object and adds the ``csv-cell`` object. A non-null locator
    requires ``artifact_id``. Draft 2020-12 accepts ``1.0`` for an integer;
    the runtime model uses ``StrictInt`` and rejects it, as documented on the
    ``row``/``column``/``line`` properties.
    """
    schema = _generated_schema_0_4()
    _publish_common_0_2(schema)
    defs = schema["$defs"]
    jsonl = defs["JsonlLocator"]
    jsonl["description"] = (
        "Structured 0.4 jsonl locator for one physical JSONL line and a JSON "
        "Pointer.\n\n0.4 admits ``jsonl``; the locator union admits "
        "``jsonl``/``csv-cell``. ``line`` is one-based and uses ``StrictInt`` "
        "at runtime (the JSON Schema draft integer accepts ``1.0``; the model "
        "rejects it and booleans). ``pointer`` is empty or a slash-prefixed "
        "pointer using only ``~0``/``~1`` escapes."
    )
    jsonl["properties"]["kind"]["description"] = (
        "Locator kind; 0.4 admits jsonl; the locator union admits jsonl/csv-cell."
    )
    jsonl["properties"]["pointer"]["pattern"] = _LOCATOR_POINTER_JSON_PATTERN
    jsonl["properties"]["pointer"]["description"] = (
        "JSON Pointer inside the selected line; empty or /-prefixed, "
        "with only ~0/~1 escapes."
    )
    defs["Result04"]["properties"]["locator"] = {
        "oneOf": [
            {
                "type": "string",
                "pattern": _LOCATOR_POINTER_JSON_PATTERN,
                "description": (
                    "Empty or a JSON Pointer string with only ~0/~1 escapes into "
                    "a .json artifact."
                ),
            },
            {"$ref": "#/$defs/JsonlLocator"},
            {"$ref": "#/$defs/CsvCellLocator"},
            {"type": "null"},
        ],
        "default": None,
        "title": "Locator",
        "description": (
            "RFC 6901 JSON Pointer string, a structured jsonl or csv-cell "
            "locator object, or null. In 0.4 any non-null locator requires "
            "artifact_id."
        ),
    }
    defs["Result04"]["if"] = {
        "required": ["locator"],
        "properties": {"locator": {"not": {"type": "null"}}},
    }
    defs["Result04"]["then"] = {
        "required": ["artifact_id"],
        "properties": {"artifact_id": {"type": "string"}},
    }
    _pin_schema_version(schema, SCHEMA_VERSION_0_4, required=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:runevidence:manifest:{SCHEMA_VERSION_0_4}"
    return schema


def json_schema_0_5() -> dict[str, Any]:
    """JSON Schema for the 0.5 manifest (adds the ``tensorboard`` locator).

    The 0.4 file stays byte-frozen. This schema keeps every 0.2 field and the
    0.3/0.4 locator objects, and adds the ``tensorboard`` object. A non-null
    locator requires ``artifact_id``. Draft 2020-12 accepts ``1.0`` for an
    integer and counts ``maxLength`` in code points; the runtime model uses
    ``StrictInt``, rejects ``1.0``, and additionally enforces 4,096 UTF-8
    bytes for ``tag``, as documented on the properties.
    """
    schema = _generated_schema_0_5()
    _publish_common_0_2(schema)
    defs = schema["$defs"]
    jsonl = defs["JsonlLocator"]
    jsonl["description"] = (
        "Structured 0.5 jsonl locator for one physical JSONL line and a JSON "
        "Pointer.\n\n0.5 admits ``jsonl``/``csv-cell``/``tensorboard``; "
        "``line`` is one-based and uses ``StrictInt`` at runtime (the JSON "
        "Schema draft integer accepts ``1.0``; the model rejects it and "
        "booleans). ``pointer`` is empty or a slash-prefixed pointer using "
        "only ``~0``/``~1`` escapes."
    )
    jsonl["properties"]["kind"]["description"] = (
        "Locator kind; 0.5 admits jsonl/csv-cell/tensorboard."
    )
    jsonl["properties"]["pointer"]["pattern"] = _LOCATOR_POINTER_JSON_PATTERN
    jsonl["properties"]["pointer"]["description"] = (
        "JSON Pointer inside the selected line; empty or /-prefixed, "
        "with only ~0/~1 escapes."
    )
    csv_cell = defs["CsvCellLocator"]
    csv_cell["description"] = (
        "Structured 0.5 csv-cell locator for one CSV data cell.\n\n``row`` is "
        "one-based over data records (the header is never selectable) and "
        "``column`` is one-based over the parsed header. Both use "
        "``StrictInt`` at runtime (the JSON Schema draft integer accepts "
        "``1.0``; the model rejects it and booleans). ``header`` is the "
        "recorded header assertion at that column; it never appears in a "
        "finding or human display."
    )
    csv_cell["properties"]["kind"]["description"] = (
        "Locator kind; 0.5 admits csv-cell."
    )
    defs["Result05"]["properties"]["locator"] = {
        "oneOf": [
            {
                "type": "string",
                "pattern": _LOCATOR_POINTER_JSON_PATTERN,
                "description": (
                    "Empty or a JSON Pointer string with only ~0/~1 escapes into "
                    "a .json artifact."
                ),
            },
            {"$ref": "#/$defs/JsonlLocator"},
            {"$ref": "#/$defs/CsvCellLocator"},
            {"$ref": "#/$defs/TensorboardLocator"},
            {"type": "null"},
        ],
        "default": None,
        "title": "Locator",
        "description": (
            "RFC 6901 JSON Pointer string, a structured jsonl, csv-cell or "
            "tensorboard locator object, or null. In 0.5 any non-null locator "
            "requires artifact_id."
        ),
    }
    defs["Result05"]["if"] = {
        "required": ["locator"],
        "properties": {"locator": {"not": {"type": "null"}}},
    }
    defs["Result05"]["then"] = {
        "required": ["artifact_id"],
        "properties": {"artifact_id": {"type": "string"}},
    }
    _pin_schema_version(schema, SCHEMA_VERSION_0_5, required=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:runevidence:manifest:{SCHEMA_VERSION_0_5}"
    return schema


def _generated_schema_0_6() -> dict[str, Any]:
    """The canonical JSON Schema for Manifest06, before version fixes."""
    schema = Manifest06.model_json_schema()
    _canonicalize_schema(schema)
    _add_artifact_type_rules(schema["$defs"]["Artifact"])
    return schema


def _rewrite_descriptions(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "description" and isinstance(child, str):
                value[key] = (
                    child.replace("0.5", "0.6")
                    .replace("0.4", "0.6")
                    .replace("0.3", "0.6")
                )
            else:
                _rewrite_descriptions(child)
    elif isinstance(value, list):
        for child in value:
            _rewrite_descriptions(child)


def json_schema_0_6() -> dict[str, Any]:
    """JSON Schema for the 0.6 manifest (adds ``tensorboard-string``).

    It carries every 0.5 locator form plus the string-tensor object. A non-null
    locator requires ``artifact_id``. The 0.5 file stays byte-frozen.
    """
    schema = _generated_schema_0_6()
    _publish_common_0_2(schema)
    _rewrite_descriptions(schema)
    defs = schema["$defs"]
    jsonl = defs["JsonlLocator"]
    jsonl["description"] = (
        "Structured 0.6 jsonl locator for one physical JSONL line and a JSON "
        "Pointer. 0.6 admits jsonl/csv-cell/tensorboard/tensorboard-string; "
        "``line`` is one-based and uses ``StrictInt`` at runtime."
    )
    jsonl["properties"]["kind"]["description"] = (
        "Locator kind; 0.6 admits jsonl/csv-cell/tensorboard/tensorboard-string."
    )
    jsonl["properties"]["pointer"]["pattern"] = _LOCATOR_POINTER_JSON_PATTERN
    jsonl["properties"]["pointer"]["description"] = (
        "JSON Pointer inside the selected line; empty or /-prefixed, "
        "with only ~0/~1 escapes."
    )
    csv_cell = defs["CsvCellLocator"]
    csv_cell["description"] = (
        "Structured 0.6 csv-cell locator for one CSV data cell. ``row`` is "
        "one-based over data records and ``column`` is one-based over the "
        "parsed header; both use ``StrictInt`` at runtime."
    )
    csv_cell["properties"]["kind"]["description"] = (
        "Locator kind; 0.6 admits csv-cell."
    )
    text = defs["TensorboardStringLocator"]
    text["description"] = (
        "Structured 0.6 tensorboard-string locator for one supported string "
        "tensor. ``tag`` is the exact event tag; ``step`` is a signed 64-bit "
        "integer; ``occurrence`` is the zero-based position among conforming "
        "string entries with that exact (tag, step) in this artifact."
    )
    defs["Result06"]["properties"]["locator"] = {
        "oneOf": [
            {
                "type": "string",
                "pattern": _LOCATOR_POINTER_JSON_PATTERN,
                "description": (
                    "Empty or a JSON Pointer string with only ~0/~1 escapes into "
                    "a .json artifact."
                ),
            },
            {"$ref": "#/$defs/JsonlLocator"},
            {"$ref": "#/$defs/CsvCellLocator"},
            {"$ref": "#/$defs/TensorboardLocator"},
            {"$ref": "#/$defs/TensorboardStringLocator"},
            {"type": "null"},
        ],
        "default": None,
        "title": "Locator",
        "description": (
            "RFC 6901 JSON Pointer string, a structured jsonl, csv-cell, "
            "tensorboard or tensorboard-string locator object, or null. In 0.6 "
            "any non-null locator requires artifact_id."
        ),
    }
    defs["Result06"]["if"] = {
        "required": ["locator"],
        "properties": {"locator": {"not": {"type": "null"}}},
    }
    defs["Result06"]["then"] = {
        "required": ["artifact_id"],
        "properties": {"artifact_id": {"type": "string"}},
    }
    _pin_schema_version(schema, SCHEMA_VERSION_0_6, required=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:runevidence:manifest:{SCHEMA_VERSION_0_6}"
    return schema


def _generated_schema_0_7() -> dict[str, Any]:
    schema = Manifest07.model_json_schema()
    _canonicalize_schema(schema)
    _add_artifact_type_rules(schema["$defs"]["Artifact"])
    return schema


def _rewrite_06_descriptions(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "description" and isinstance(child, str):
                value[key] = child.replace("0.6", "0.7")
            else:
                _rewrite_06_descriptions(child)
    elif isinstance(value, list):
        for child in value:
            _rewrite_06_descriptions(child)


def _generated_schema_0_8() -> dict[str, Any]:
    schema = Manifest08.model_json_schema()
    _canonicalize_schema(schema)
    _add_artifact_type_rules(schema["$defs"]["Artifact"])
    return schema


def _rewrite_media_descriptions(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "description" and isinstance(child, str):
                for older in ("0.7", "0.6", "0.5", "0.4", "0.3"):
                    child = child.replace(older, "0.8")
                value[key] = child
            else:
                _rewrite_media_descriptions(child)
    elif isinstance(value, list):
        for child in value:
            _rewrite_media_descriptions(child)


def json_schema_0_8() -> dict[str, Any]:
    """JSON Schema for the 0.8 manifest (adds image and audio references)."""
    schema = _generated_schema_0_8()
    _publish_common_0_2(schema)
    _rewrite_descriptions(schema)
    _rewrite_06_descriptions(schema)
    _rewrite_media_descriptions(schema)
    defs = schema["$defs"]
    locator_kinds = (
        "jsonl/csv-cell/tensorboard/tensorboard-string/tensorboard-histogram/"
        "tensorboard-image/tensorboard-audio"
    )
    jsonl = defs["JsonlLocator"]
    jsonl["description"] = (
        "Structured 0.8 jsonl locator for one physical JSONL line and a JSON "
        f"Pointer. 0.8 admits {locator_kinds}; ``line`` is one-based and uses "
        "``StrictInt``."
    )
    jsonl["properties"]["kind"]["description"] = f"Locator kind; 0.8 admits {locator_kinds}."
    jsonl["properties"]["pointer"]["pattern"] = _LOCATOR_POINTER_JSON_PATTERN
    csv_cell = defs["CsvCellLocator"]
    csv_cell["description"] = (
        "Structured 0.8 csv-cell locator for one CSV data cell. ``row`` is "
        "one-based over data records and ``column`` one-based over the header."
    )
    csv_cell["properties"]["kind"]["description"] = "Locator kind; 0.8 admits csv-cell."
    for name, label, text in (
        ("TensorboardLocator", "tensorboard", "scalar"),
        ("TensorboardStringLocator", "tensorboard-string", "string tensor"),
        ("TensorboardHistogramLocator", "tensorboard-histogram", "histogram"),
        ("TensorboardImageLocator", "tensorboard-image", "image summary"),
        ("TensorboardAudioLocator", "tensorboard-audio", "audio summary"),
    ):
        definition = defs[name]
        definition["description"] = (
            f"Structured 0.8 {label} locator for one supported TensorBoard {text}. "
            "``tag`` is exact; ``occurrence`` is per artifact."
        )
        definition["properties"]["kind"]["description"] = f"Locator kind; 0.8 admits {label}."
    for name, keys in (
        ("ImageValue", ("kind", "media_type", "sha256", "size_bytes", "width", "height")),
        ("AudioValue", ("kind", "media_type", "sha256", "size_bytes")),
    ):
        definition = defs[name]
        definition["required"] = list(keys)
        definition["additionalProperties"] = False
    image = defs["ImageValue"]
    image["properties"]["kind"] = {"const": "image", "type": "string"}
    image["properties"]["media_type"] = {"const": "image/png", "type": "string"}
    image["properties"]["sha256"]["pattern"] = "^[0-9a-f]{64}$"
    image["properties"]["size_bytes"]["minimum"] = 1
    image["properties"]["size_bytes"]["maximum"] = 16_777_216
    for dim in ("width", "height"):
        image["properties"][dim]["minimum"] = 1
        image["properties"][dim]["maximum"] = 65_535
    audio = defs["AudioValue"]
    audio["properties"]["kind"] = {"const": "audio", "type": "string"}
    audio["properties"]["media_type"] = {"const": "audio/wav", "type": "string"}
    audio["properties"]["sha256"]["pattern"] = "^[0-9a-f]{64}$"
    audio["properties"]["size_bytes"]["minimum"] = 1
    audio["properties"]["size_bytes"]["maximum"] = 16_777_216
    defs["Result08"]["properties"]["locator"] = {
        "oneOf": [
            {
                "type": "string",
                "pattern": _LOCATOR_POINTER_JSON_PATTERN,
                "description": "Empty or a JSON Pointer into a .json artifact.",
            },
            {"$ref": "#/$defs/JsonlLocator"},
            {"$ref": "#/$defs/CsvCellLocator"},
            {"$ref": "#/$defs/TensorboardLocator"},
            {"$ref": "#/$defs/TensorboardStringLocator"},
            {"$ref": "#/$defs/TensorboardHistogramLocator"},
            {"$ref": "#/$defs/TensorboardImageLocator"},
            {"$ref": "#/$defs/TensorboardAudioLocator"},
            {"type": "null"},
        ],
        "default": None,
        "title": "Locator",
        "description": (
            "RFC 6901 pointer, a structured locator object, or null. In 0.8 any "
            "non-null locator requires artifact_id."
        ),
    }
    defs["Result08"]["properties"]["value"] = {
        "anyOf": [
            {"type": "boolean"},
            {"type": "integer"},
            {"type": "number"},
            {"type": "string"},
            {"type": "null"},
            {"$ref": "#/$defs/HistogramValue"},
            {"$ref": "#/$defs/ImageValue"},
            {"$ref": "#/$defs/AudioValue"},
        ],
        "default": None,
        "title": "Value",
        "description": (
            "A scalar, a structured histogram, an image reference, or an audio "
            "reference."
        ),
    }
    defs["Result08"]["allOf"] = [
        {
            "if": {
                "required": ["locator"],
                "properties": {"locator": {"not": {"type": "null"}}},
            },
            "then": {
                "required": ["artifact_id"],
                "properties": {"artifact_id": {"type": "string"}},
            },
        }
    ]
    for kind, locator_kind in (
        ("histogram", "tensorboard-histogram"),
        ("image", "tensorboard-image"),
        ("audio", "tensorboard-audio"),
    ):
        defs["Result08"]["allOf"].append(
            {
                "if": {
                    "required": ["value"],
                    "properties": {
                        "value": {
                            "type": "object",
                            "required": ["kind"],
                            "properties": {"kind": {"const": kind}},
                        }
                    },
                },
                "then": {
                    "properties": {
                        "locator": {
                            "anyOf": [
                                {"type": "null"},
                                {
                                    "type": "object",
                                    "required": ["kind"],
                                    "properties": {"kind": {"const": locator_kind}},
                                },
                            ]
                        }
                    }
                },
            }
        )
    _pin_schema_version(schema, SCHEMA_VERSION_0_8, required=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:runevidence:manifest:{SCHEMA_VERSION_0_8}"
    return schema


def json_schema_0_7() -> dict[str, Any]:
    """JSON Schema for the 0.7 manifest (adds ``tensorboard-histogram``)."""
    schema = _generated_schema_0_7()
    _publish_common_0_2(schema)
    _rewrite_descriptions(schema)
    _rewrite_06_descriptions(schema)
    for definition in schema["$defs"].values():
        if isinstance(definition, dict):
            for key in ("description",):
                if isinstance(definition.get(key), str):
                    definition[key] = (
                        definition[key]
                        .replace("0.6", "0.7")
                        .replace("0.5", "0.7")
                        .replace("0.4", "0.7")
                        .replace("0.3", "0.7")
                    )
    defs = schema["$defs"]
    jsonl = defs["JsonlLocator"]
    jsonl["description"] = (
        "Structured 0.7 jsonl locator for one physical JSONL line and a JSON "
        "Pointer. 0.7 admits jsonl/csv-cell/tensorboard/tensorboard-string/"
        "tensorboard-histogram; ``line`` is one-based and uses ``StrictInt``."
    )
    jsonl["properties"]["kind"]["description"] = (
        "Locator kind; 0.7 admits jsonl/csv-cell/tensorboard/tensorboard-string/"
        "tensorboard-histogram."
    )
    jsonl["properties"]["pointer"]["pattern"] = _LOCATOR_POINTER_JSON_PATTERN
    csv_cell = defs["CsvCellLocator"]
    csv_cell["description"] = (
        "Structured 0.7 csv-cell locator for one CSV data cell. ``row`` is "
        "one-based over data records and ``column`` one-based over the header."
    )
    csv_cell["properties"]["kind"]["description"] = "Locator kind; 0.7 admits csv-cell."
    text = defs["TensorboardStringLocator"]
    text["description"] = (
        "Structured 0.7 tensorboard-string locator for one supported string "
        "tensor. ``tag`` is exact; ``occurrence`` is per artifact."
    )
    hist = defs["TensorboardHistogramLocator"]
    hist["description"] = (
        "Structured 0.7 tensorboard-histogram locator for one supported "
        "histogram. ``occurrence`` is per artifact among conforming histograms."
    )
    value = defs["HistogramValue"]
    value["required"] = [
        "kind",
        "min",
        "max",
        "num",
        "sum",
        "sum_squares",
        "bucket_limit",
        "bucket",
    ]
    value["additionalProperties"] = False
    value["properties"]["kind"] = {"const": "histogram", "type": "string"}
    for name in ("min", "max", "num", "sum", "sum_squares"):
        value["properties"][name]["type"] = "number"
    value["properties"]["num"]["minimum"] = 0
    value["properties"]["bucket_limit"]["type"] = "array"
    value["properties"]["bucket_limit"]["maxItems"] = 1024
    value["properties"]["bucket_limit"]["items"] = {"type": "number"}
    value["properties"]["bucket"]["type"] = "array"
    value["properties"]["bucket"]["maxItems"] = 1024
    value["properties"]["bucket"]["items"] = {"type": "number", "minimum": 0}

    defs["Result07"]["properties"]["locator"] = {
        "oneOf": [
            {
                "type": "string",
                "pattern": _LOCATOR_POINTER_JSON_PATTERN,
                "description": "Empty or a JSON Pointer into a .json artifact.",
            },
            {"$ref": "#/$defs/JsonlLocator"},
            {"$ref": "#/$defs/CsvCellLocator"},
            {"$ref": "#/$defs/TensorboardLocator"},
            {"$ref": "#/$defs/TensorboardStringLocator"},
            {"$ref": "#/$defs/TensorboardHistogramLocator"},
            {"type": "null"},
        ],
        "default": None,
        "title": "Locator",
        "description": (
            "RFC 6901 pointer, a structured jsonl/csv-cell/tensorboard/"
            "tensorboard-string/tensorboard-histogram object, or null. In 0.7 "
            "any non-null locator requires artifact_id."
        ),
    }
    defs["Result07"]["properties"]["value"] = {
        "anyOf": [
            {"type": "boolean"},
            {"type": "integer"},
            {"type": "number"},
            {"type": "string"},
            {"type": "null"},
            {"$ref": "#/$defs/HistogramValue"},
        ],
        "default": None,
        "title": "Value",
        "description": "A scalar or a structured histogram value.",
    }
    defs["Result07"]["allOf"] = [
        {
            "if": {
                "required": ["locator"],
                "properties": {"locator": {"not": {"type": "null"}}},
            },
            "then": {
                "required": ["artifact_id"],
                "properties": {"artifact_id": {"type": "string"}},
            },
        },
        {
            "if": {
                "required": ["value"],
                "properties": {"value": {"type": "object"}},
            },
            "then": {
                "properties": {
                    "locator": {
                        "anyOf": [
                            {"type": "null"},
                            {
                                "type": "object",
                                "required": ["kind"],
                                "properties": {"kind": {"const": "tensorboard-histogram"}},
                            },
                        ]
                    }
                }
            },
        },
    ]
    _pin_schema_version(schema, SCHEMA_VERSION_0_7, required=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:runevidence:manifest:{SCHEMA_VERSION_0_7}"
    return schema


def _generated_schema_0_9() -> dict[str, Any]:
    schema = Manifest09.model_json_schema()
    _canonicalize_schema(schema)
    _add_artifact_type_rules(schema["$defs"]["Artifact"])
    return schema


def json_schema_0_9() -> dict[str, Any]:
    """JSON Schema for the 0.9 manifest (adds canonical tensor digests)."""
    schema = _generated_schema_0_9()
    _publish_common_0_2(schema)
    _rewrite_descriptions(schema)
    _rewrite_06_descriptions(schema)
    _rewrite_media_descriptions(schema)
    defs = schema["$defs"]
    kinds = (
        "jsonl/csv-cell/tensorboard/tensorboard-string/tensorboard-histogram/"
        "tensorboard-image/tensorboard-audio/tensorboard-tensor"
    )
    jsonl = defs["JsonlLocator"]
    jsonl["description"] = (
        "Structured 0.9 jsonl locator for one physical JSONL line and a JSON "
        f"Pointer. 0.9 admits {kinds}; ``line`` is one-based and uses ``StrictInt``."
    )
    jsonl["properties"]["kind"]["description"] = f"Locator kind; 0.9 admits {kinds}."
    jsonl["properties"]["pointer"]["pattern"] = _LOCATOR_POINTER_JSON_PATTERN
    csv_cell = defs["CsvCellLocator"]
    csv_cell["description"] = (
        "Structured 0.9 csv-cell locator for one CSV data cell. ``row`` is "
        "one-based over data records and ``column`` one-based over the header."
    )
    csv_cell["properties"]["kind"]["description"] = "Locator kind; 0.9 admits csv-cell."
    for name, label, text in (
        ("TensorboardLocator", "tensorboard", "scalar"),
        ("TensorboardStringLocator", "tensorboard-string", "string tensor"),
        ("TensorboardHistogramLocator", "tensorboard-histogram", "histogram"),
        ("TensorboardImageLocator", "tensorboard-image", "image summary"),
        ("TensorboardAudioLocator", "tensorboard-audio", "audio summary"),
        ("TensorboardTensorLocator", "tensorboard-tensor", "numeric tensor"),
    ):
        definition = defs[name]
        definition["description"] = (
            f"Structured 0.9 {label} locator for one supported TensorBoard {text}. "
            "``tag`` is exact; ``occurrence`` is per artifact."
        )
        definition["properties"]["kind"]["description"] = f"Locator kind; 0.9 admits {label}."
    for name, keys in (
        ("ImageValue", ("kind", "media_type", "sha256", "size_bytes", "width", "height")),
        ("AudioValue", ("kind", "media_type", "sha256", "size_bytes")),
        ("TensorValue", ("kind", "dtype", "shape", "element_count", "sha256")),
    ):
        definition = defs[name]
        definition["required"] = list(keys)
        definition["additionalProperties"] = False
    image = defs["ImageValue"]
    image["properties"]["kind"] = {"const": "image", "type": "string"}
    image["properties"]["media_type"] = {"const": "image/png", "type": "string"}
    image["properties"]["sha256"]["pattern"] = "^[0-9a-f]{64}$"
    image["properties"]["size_bytes"]["minimum"] = 1
    image["properties"]["size_bytes"]["maximum"] = 16_777_216
    for dim in ("width", "height"):
        image["properties"][dim]["minimum"] = 1
        image["properties"][dim]["maximum"] = 65_535
    audio = defs["AudioValue"]
    audio["properties"]["kind"] = {"const": "audio", "type": "string"}
    audio["properties"]["media_type"] = {"const": "audio/wav", "type": "string"}
    audio["properties"]["sha256"]["pattern"] = "^[0-9a-f]{64}$"
    audio["properties"]["size_bytes"]["minimum"] = 1
    audio["properties"]["size_bytes"]["maximum"] = 16_777_216
    tensor = defs["TensorValue"]
    tensor["properties"]["kind"] = {"const": "tensor", "type": "string"}
    tensor["properties"]["dtype"] = {
        "enum": ["float32", "float64", "int32", "int64"],
        "type": "string",
    }
    tensor["properties"]["shape"] = {
        "type": "array",
        "minItems": 1,
        "maxItems": 8,
        "items": {"type": "integer", "minimum": 1, "maximum": 2**31 - 1},
        "description": "1-8 dimensions, row-major; the product is element_count.",
    }
    tensor["properties"]["element_count"]["minimum"] = 1
    tensor["properties"]["element_count"]["maximum"] = 1_000_000
    tensor["properties"]["sha256"]["pattern"] = "^[0-9a-f]{64}$"
    defs["Result09"]["properties"]["locator"] = {
        "oneOf": [
            {
                "type": "string",
                "pattern": _LOCATOR_POINTER_JSON_PATTERN,
                "description": "Empty or a JSON Pointer into a .json artifact.",
            },
            {"$ref": "#/$defs/JsonlLocator"},
            {"$ref": "#/$defs/CsvCellLocator"},
            {"$ref": "#/$defs/TensorboardLocator"},
            {"$ref": "#/$defs/TensorboardStringLocator"},
            {"$ref": "#/$defs/TensorboardHistogramLocator"},
            {"$ref": "#/$defs/TensorboardImageLocator"},
            {"$ref": "#/$defs/TensorboardAudioLocator"},
            {"$ref": "#/$defs/TensorboardTensorLocator"},
            {"type": "null"},
        ],
        "default": None,
        "title": "Locator",
        "description": (
            "RFC 6901 pointer, a structured locator object, or null. In 0.9 any "
            "non-null locator requires artifact_id."
        ),
    }
    defs["Result09"]["properties"]["value"] = {
        "anyOf": [
            {"type": "boolean"},
            {"type": "integer"},
            {"type": "number"},
            {"type": "string"},
            {"type": "null"},
            {"$ref": "#/$defs/HistogramValue"},
            {"$ref": "#/$defs/ImageValue"},
            {"$ref": "#/$defs/AudioValue"},
            {"$ref": "#/$defs/TensorValue"},
        ],
        "default": None,
        "title": "Value",
        "description": (
            "A scalar, a structured histogram, an image reference, an audio "
            "reference, or a canonical tensor digest."
        ),
    }
    defs["Result09"]["allOf"] = [
        {
            "if": {
                "required": ["locator"],
                "properties": {"locator": {"not": {"type": "null"}}},
            },
            "then": {
                "required": ["artifact_id"],
                "properties": {"artifact_id": {"type": "string"}},
            },
        }
    ]
    for kind, locator_kind in (
        ("histogram", "tensorboard-histogram"),
        ("image", "tensorboard-image"),
        ("audio", "tensorboard-audio"),
        ("tensor", "tensorboard-tensor"),
    ):
        defs["Result09"]["allOf"].append(
            {
                "if": {
                    "required": ["value"],
                    "properties": {
                        "value": {
                            "type": "object",
                            "required": ["kind"],
                            "properties": {"kind": {"const": kind}},
                        }
                    },
                },
                "then": {
                    "properties": {
                        "locator": {
                            "anyOf": [
                                {"type": "null"},
                                {
                                    "type": "object",
                                    "required": ["kind"],
                                    "properties": {"kind": {"const": locator_kind}},
                                },
                            ]
                        }
                    }
                },
            }
        )
    _pin_schema_version(schema, SCHEMA_VERSION_0_9, required=True)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:runevidence:manifest:{SCHEMA_VERSION_0_9}"
    return schema
