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
import re
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, ClassVar, Literal, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = "0.1"
# First-class comparability fields require an explicit 0.2 declaration. An
# omitted schema_version still means 0.1.
SCHEMA_VERSION_0_2 = "0.2"
# 0.3 adds structured locators. It is an explicit opt-in; omitted still means 0.1.
SCHEMA_VERSION_0_3 = "0.3"
SCHEMA_VERSIONS = (SCHEMA_VERSION, SCHEMA_VERSION_0_2, SCHEMA_VERSION_0_3)
MAX_JSONL_LINE = 2**63 - 1
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
        if key < 0:
            candidate = length + key
            return candidate if 0 <= candidate < length else None
        return key

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


def _selection_excludes_key(caller: Any, key: Any) -> bool:
    if caller is True or caller is Ellipsis:
        return True
    if isinstance(caller, (set, frozenset)):
        return key in caller
    if isinstance(caller, dict):
        direct = caller.get(key, _MISSING)
        if direct is True or direct is Ellipsis:
            return True
        if direct is not _MISSING:
            # An explicit partial spec retains the entry even with __all__ whole.
            return False
        every = caller.get("__all__", _MISSING)
        return every is True or every is Ellipsis
    return False


def _nested_caller_spec(caller: Any, key: Any) -> Any:
    """The caller exclusion applying to one retained field/entry."""
    if not isinstance(caller, dict):
        return _MISSING
    direct = caller.get(key, _MISSING)
    every = caller.get("__all__", _MISSING)
    if direct is not _MISSING:
        if every is True or every is Ellipsis or every is _MISSING:
            # __all__ whole is overridden by an explicit partial retention.
            return direct
        return _merge_exclude_values(direct, every)
    return every


def _prune_presence(presence: Any, caller: Any) -> Any:
    """Drop presence entries the caller already excludes wholly."""
    if presence is None or caller is None:
        return presence
    if caller is True or caller is Ellipsis:
        return None
    if not isinstance(presence, dict):
        return presence
    out: dict[Any, Any] = {}
    for key, value in presence.items():
        if _selection_excludes_key(caller, key):
            continue
        nested = _nested_caller_spec(caller, key)
        if isinstance(value, dict) and nested is not _MISSING:
            pruned = _prune_presence(value, nested)
            if pruned is not None:
                out[key] = pruned
        else:
            out[key] = value
    return out or None


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
            if isinstance(item, BaseModel):
                nested = _presence_exclude(item)
                if nested is not None:
                    indices[index] = nested
        return indices or None
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


def _merge_exclude_values(left: Any, right: Any) -> Any:
    if left is True or right is True or left is Ellipsis or right is Ellipsis:
        return True
    # A caller may pass False to keep a field. It selects nothing, so the
    # other side is the whole answer.
    if left is None or left is False:
        return right
    if right is None or right is False:
        return left
    if isinstance(left, dict) and isinstance(right, dict):
        merged = dict(left)
        for key, value in right.items():
            merged[key] = (
                _merge_exclude_values(merged[key], value)
                if key in merged
                else value
            )
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

        presence = _presence_exclude(self)
        if presence is not None and exclude is not None:
            presence = _prune_presence(presence, exclude)
        merged = _merge_exclude(exclude, presence)
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
        if self.schema_version in (SCHEMA_VERSION_0_2, SCHEMA_VERSION_0_3):
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
        else:
            for result in self.results:
                if isinstance(result.locator, JsonlLocator):
                    raise ValueError("object locators require schema_version 0.3")
        return self


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


def _publish_schema_0_2(schema: dict[str, Any]) -> None:
    _publish_common_0_2(schema)
    _restore_locator_0_1(schema)
    _pin_schema_version(schema, SCHEMA_VERSION_0_2, required=True)


def json_schema() -> dict[str, Any]:
    """JSON Schema for the 0.1 manifest, generated from the Pydantic models."""
    schema = Manifest.model_json_schema()
    _restrict_schema_to_0_1(schema)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = f"urn:runevidence:manifest:{SCHEMA_VERSION}"
    return schema


def json_schema_0_2() -> dict[str, Any]:
    """JSON Schema for the 0.2 manifest, generated from the Pydantic models.

    Distinct ``$id`` and an explicit ``schema_version`` const 0.2. Omitted
    schema_version means 0.1, so the 0.2 schema requires the declaration.
    """
    schema = Manifest.model_json_schema()
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
    schema = Manifest.model_json_schema()
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
