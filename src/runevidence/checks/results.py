"""Compare recorded result values with the value inside their source file.

Only JSON artifacts are read by the legacy path. Schema 0.3 adds one strict
JSON whole-document profile for string locators and one strict JSONL line
profile for object locators; schema 0.4 adds the strict RFC 4180-derived CSV
cell profile. The bytes come from the single protected snapshot made by the
file check, never from a second read.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path, PurePosixPath
from typing import Any

from ..findings import Code, Finding, Severity
from ..locator_profiles import (
    JsonlDocument,
    LocatorMismatch,
    ProfileError,
    _Failure,
    _raise_failure,
    compare_tensorboard,
    compare_values,
    display_locator,
    parse_json_document,
)
from ..models import (
    SCHEMA_VERSION_0_3,
    SCHEMA_VERSION_0_4,
    SCHEMA_VERSION_0_5,
    SCHEMA_VERSION_0_6,
    SCHEMA_VERSION_0_7,
    SCHEMA_VERSION_0_8,
    SCHEMA_VERSION_0_9,
    SCHEMA_VERSION_0_10,
    CsvCellLocator,
    HistogramValue,
    JsonlLocator,
    Manifest,
    TensorboardAudioLocator,
    TensorboardImageLocator,
    TensorboardLocator,
    TensorboardStringLocator,
    TensorboardHistogramLocator,
    TensorboardTensorLocator,
    TensorboardTf2AudioLocator,
    TensorboardTf2ImageLocator,
    AudioValue,
    ImageValue,
    TensorValue,
)
from ..tensorboard_events import (
    EventFileError,
    ScalarOutcome,
    scan_scalars,
    scan_tensorboard_06,
    scan_tensorboard_07,
    scan_tensorboard_08,
    scan_tensorboard_09,
    scan_tensorboard_10,
)
from .csv import (
    CsvNotNumeric,
    CsvNumberOverflow,
    CsvProfile,
    CsvProfileError,
)
from .files import TENSORBOARD_MAX_FILE_BYTES

MAX_JSON_BYTES = 50 * 1024 * 1024
_MISSING = object()
_INDEX = re.compile(r"0|[1-9][0-9]*")
CSV_CELL_CAP_MESSAGE = (
    "the manifest exceeds the csv-cell verification limit (10,000 entries or "
    "16 MiB of header metadata); no CSV cell was read"
)
CSV_PROFILE_HINT = "the CSV artifact does not satisfy the strict csv-cell profile"
TENSORBOARD_CAP_MESSAGE_06 = (
    "the manifest exceeds the tensorboard verification limit (10,000 entries or "
    "16 MiB of tag metadata); no TensorBoard value was read"
)
TENSORBOARD_CAP_MESSAGE = (
    "the manifest exceeds the tensorboard verification limit (10,000 entries or "
    "16 MiB of tag metadata); no TensorBoard scalar was read"
)
TENSORBOARD_PROFILE_HINT = (
    "the TensorBoard event file does not satisfy the strict profile"
)


def _tensorboard_path_matches(path: str) -> bool:
    """RFC 4.4 TensorBoard predicate: basename contains ".tfevents"."""
    return ".tfevents" in PurePosixPath(path.replace("\\", "/")).name.lower()


def _is_index(token: str, size: int) -> bool:
    """RFC 6901 array index: ASCII digits, no leading zero, inside the list.
    Compared as text, so a huge token never reaches int()."""
    bound = str(size)
    return _INDEX.fullmatch(token) is not None and (len(token), token) < (len(bound), bound)


def resolve_pointer(doc: Any, pointer: str) -> Any:
    """RFC 6901 JSON Pointer lookup. Returns _MISSING if it does not resolve."""
    if pointer == "":
        return doc
    if not pointer.startswith("/"):
        return _MISSING
    current = doc
    for token in pointer[1:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and _is_index(token, len(current)):
            current = current[int(token)]
        else:
            return _MISSING
    return current


def values_match(recorded: Any, actual: Any) -> bool:
    numeric = (int, float)
    if isinstance(recorded, bool) or isinstance(actual, bool):
        return recorded is actual
    if isinstance(recorded, numeric) and isinstance(actual, numeric):
        # A diverged run legitimately records NaN, and NaN != NaN in IEEE
        # floats; recording it faithfully must not look like tampering.
        # Only floats can be NaN, and math.isnan raises on ints too big for one.
        recorded_nan = isinstance(recorded, float) and math.isnan(recorded)
        actual_nan = isinstance(actual, float) and math.isnan(actual)
        if recorded_nan or actual_nan:
            return recorded_nan and actual_nan
        try:
            return math.isclose(recorded, actual, rel_tol=1e-9, abs_tol=1e-12)
        except OverflowError:
            # An int beyond float range has no tolerance to apply: compare exactly.
            return recorded == actual
    return recorded == actual


def _is_object_locator(locator: Any) -> bool:
    return locator is not None and type(locator) is not str


def _extension_matches(artifact_path: str, locator: Any) -> bool:
    lowered = artifact_path.replace("\\", "/").lower()
    if type(locator) is str:
        return lowered.endswith(".json")
    if isinstance(locator, CsvCellLocator):
        return lowered.endswith(".csv")
    if isinstance(
        locator,
        (
            TensorboardLocator,
            TensorboardStringLocator,
            TensorboardHistogramLocator,
            TensorboardImageLocator,
            TensorboardAudioLocator,
            TensorboardTensorLocator,
            TensorboardTf2ImageLocator,
            TensorboardTf2AudioLocator,
        ),
    ):
        return _tensorboard_path_matches(artifact_path)
    return lowered.endswith(".jsonl")


def _expected_extension(locator: Any) -> str:
    if type(locator) is str:
        return ".json"
    if isinstance(locator, CsvCellLocator):
        return ".csv"
    if isinstance(
        locator,
        (
            TensorboardLocator,
            TensorboardStringLocator,
            TensorboardHistogramLocator,
            TensorboardImageLocator,
            TensorboardAudioLocator,
            TensorboardTensorLocator,
            TensorboardTf2ImageLocator,
            TensorboardTf2AudioLocator,
        ),
    ):
        return "TensorBoard event"
    return ".jsonl"


def _locator_finding(
    code: Code,
    *,
    subject: str,
    path: str,
    message: str,
    severity: Severity = Severity.error,
    hint: str | None = None,
) -> Finding:
    return Finding(
        code=code,
        severity=severity,
        subject=subject,
        path=path,
        message=message,
        hint=hint,
    )


def _targeted_results(manifest: Manifest, artifact_id: str):
    return [
        result
        for result in manifest.results
        if result.artifact_id == artifact_id and result.locator is not None
    ]


def _value_error(result, artifact_path: str, locator: Any, code: Code, reason: str) -> Finding:
    return _locator_finding(
        code,
        subject=result.id,
        path=artifact_path,
        message=(
            f"result {result.id!r} locator {display_locator(locator)} "
            f"{reason} in {artifact_path!r}"
        ),
    )


class _LocatorValues:
    """Decode and parse one artifact once, cache per selected line/document.

    Every result on one artifact obtains its value here. Without this cache
    each result would re-decode and re-split the whole file (O(results ×
    file size)). Only parsed values and fixed failure markers are kept, never
    the snapshot bytes and never an exception object (whose traceback would
    keep this instance and its bytes alive until a later gc pass)."""

    __slots__ = ("_data", "_document", "_document_error", "_jsonl", "_jsonl_error", "_lines")

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._document: Any = _MISSING
        self._document_error: _Failure | None = None
        self._jsonl: JsonlDocument | None = None
        self._jsonl_error: _Failure | None = None
        self._lines: dict[int, Any] = {}

    def json_document(self) -> Any:
        if self._document is _MISSING and self._document_error is None:
            try:
                self._document = parse_json_document(self._data)
            except ProfileError:
                self._document_error = _Failure("profile")
        if self._document_error is not None:
            _raise_failure(self._document_error.kind)
        return self._document

    def jsonl_line(self, line: int) -> Any:
        if self._jsonl is None and self._jsonl_error is None:
            try:
                self._jsonl = JsonlDocument(self._data)
            except ProfileError:
                self._jsonl_error = _Failure("profile")
        if self._jsonl_error is not None:
            _raise_failure(self._jsonl_error.kind)
        if line in self._lines:
            cached = self._lines[line]
            if isinstance(cached, _Failure):
                _raise_failure(cached.kind)
            return cached
        try:
            value = self._jsonl.line(line)
        except LocatorMismatch:
            self._lines[line] = _Failure("mismatch")
            raise
        except ProfileError:
            self._lines[line] = _Failure("profile")
            raise
        self._lines[line] = value
        return value

    def request_lines(self, lines) -> None:
        """Record every selected line's offset in one forward scan.

        The results layer knows all object locators before reading any value,
        so the file is scanned once for the requested set instead of once per
        distinct line. Only those offsets are kept; a 17-million-line file does
        not get a 17-million-entry offset list."""
        if self._jsonl is None and self._jsonl_error is None:
            try:
                self._jsonl = JsonlDocument(self._data)
            except ProfileError:
                self._jsonl_error = _Failure("profile")
                return
        if self._jsonl is not None:
            self._jsonl.index_lines(lines)


def _check_one_targeted(
    result, artifact_path: str, locator: Any, values: _LocatorValues
) -> tuple[list[Finding], bool]:
    """Reference/content checks for one result after a good snapshot."""
    findings: list[Finding] = []
    try:
        if type(locator) is str:
            document = values.json_document()
            actual = resolve_pointer(document, locator)
            if actual is _MISSING:
                findings.append(
                    _value_error(
                        result, artifact_path, locator,
                        Code.locator_mismatch, "was not found",
                    )
                )
                return findings, True
        else:
            line_value = values.jsonl_line(locator.line)
            actual = resolve_pointer(line_value, locator.pointer)
            if actual is _MISSING:
                findings.append(
                    _value_error(
                        result, artifact_path, locator,
                        Code.locator_mismatch, "was not found",
                    )
                )
                return findings, True
    except LocatorMismatch:
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.locator_mismatch, "was not selectable",
            )
        )
        return findings, True
    except ProfileError:
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.locator_unreadable, "could not be read under the strict profile",
            )
        )
        return findings, True

    if isinstance(actual, (dict, list)):
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.locator_unsupported_value, "selects an unsupported value",
            )
        )
        return findings, True
    if not compare_values(result.value, actual):
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.result_value_mismatch, "does not match the recorded value",
            )
        )
        return findings, True
    return findings, False


def _check_one_tensorboard_media(
    result,
    artifact_path: str,
    locator,
    selection,
    *,
    kind: str,
    model,
    label: str | None = None,
) -> tuple[list[Finding], bool]:
    """Selection, type and exact comparison for one image or audio result."""
    findings: list[Finding] = []
    outcome = selection.outcome
    if outcome is ScalarOutcome.mismatch:
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.locator_mismatch, "was not found",
            )
        )
        return findings, True
    if outcome is ScalarOutcome.unsupported_scalar:
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.locator_unsupported_value,
                f"selects an unsupported {label or kind} encoding",
            )
        )
        return findings, True
    if outcome is not ScalarOutcome.value or selection.value is None:
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.locator_unsupported_value, "selects an unsupported value",
            )
        )
        return findings, True
    recorded = result.value
    if isinstance(recorded, model):
        recorded = recorded.model_dump(mode="json")
    if not isinstance(recorded, dict) or recorded.get("kind") != kind:
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.locator_unsupported_value, "selects an unsupported value",
            )
        )
        return findings, True
    if recorded != selection.value:
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.result_value_mismatch, "does not match the recorded value",
            )
        )
        return findings, True
    return findings, False


def _check_one_csv_cell(
    result, artifact_path: str, locator: CsvCellLocator, profile: CsvProfile | None
) -> tuple[list[Finding], bool]:
    """Selection, type, numeric parse and comparison for one csv-cell result.

    Selection order is fixed by RFC section 6.2 step 8 (row, then column, then
    header), before the recorded-type and cell-parse stages. ``profile`` is
    ``None`` when the whole file was structurally refused; every csv-cell result
    on that artifact then gets the same fixed finding.
    """
    findings: list[Finding] = []
    if profile is None:
        finding = _value_error(
            result, artifact_path, locator,
            Code.locator_unreadable,
            "could not be read under the strict profile",
        )
        findings.append(finding.model_copy(update={"hint": CSV_PROFILE_HINT}))
        return findings, True

    if locator.row < 1 or locator.row > profile.row_count:
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.locator_mismatch, "was not found",
            )
        )
        return findings, True
    if locator.column < 1 or locator.column > profile.column_count:
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.locator_mismatch, "was not selectable",
            )
        )
        return findings, True
    if profile.header[locator.column - 1] != locator.header:
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.locator_mismatch,
                "selected header does not match the header at this column",
            )
        )
        return findings, True

    if type(result.value) is bool or type(result.value) not in (int, float):
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.locator_unsupported_value, "selects an unsupported value",
            )
        )
        return findings, True

    try:
        actual = profile.number(locator.row, locator.column)
    except (CsvNotNumeric, CsvNumberOverflow):
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.locator_unreadable,
                "selected cell is not a number under the csv-cell profile",
            )
        )
        return findings, True
    except CsvProfileError:
        # The whole-file pass already validated the bytes; this is defense in
        # depth for a cell decode that cannot be represented, never parser text.
        finding = _value_error(
            result, artifact_path, locator,
            Code.locator_unreadable,
            "could not be read under the strict profile",
        )
        findings.append(finding.model_copy(update={"hint": CSV_PROFILE_HINT}))
        return findings, True

    if not compare_values(result.value, actual):
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.result_value_mismatch, "does not match the recorded value",
            )
        )
        return findings, True
    return findings, False


def _check_one_tensorboard(
    result, artifact_path: str, locator: TensorboardLocator, selection
) -> tuple[list[Finding], bool]:
    """Map one already-selected TensorBoard scalar to its fixed outcome."""
    findings: list[Finding] = []
    outcome = selection.outcome
    if outcome is ScalarOutcome.mismatch:
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.locator_mismatch, "was not found",
            )
        )
        return findings, True
    if outcome is ScalarOutcome.unsupported_scalar:
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.locator_unsupported_value,
                "selects an unsupported scalar encoding",
            )
        )
        return findings, True
    if outcome is not ScalarOutcome.value:
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.locator_unsupported_value, "selects an unsupported value",
            )
        )
        return findings, True

    matched = compare_tensorboard(
        result.value, selection.value, float32=selection.float32
    )
    if matched is None:
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.locator_unsupported_value, "selects an unsupported value",
            )
        )
        return findings, True
    if not matched:
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.result_value_mismatch, "does not match the recorded value",
            )
        )
        return findings, True
    return findings, False


def _check_one_tensorboard_string(
    result, artifact_path: str, locator: TensorboardStringLocator, selection
) -> tuple[list[Finding], bool]:
    """Map one selected TensorBoard string to its fixed outcome."""
    findings: list[Finding] = []
    outcome = selection.outcome
    if outcome is ScalarOutcome.mismatch:
        findings.append(
            _value_error(
                result,
                artifact_path,
                locator,
                Code.locator_mismatch,
                "was not found",
            )
        )
        return findings, True
    if outcome is ScalarOutcome.unsupported_scalar:
        findings.append(
            _value_error(
                result,
                artifact_path,
                locator,
                Code.locator_unsupported_value,
                "selects an unsupported scalar encoding",
            )
        )
        return findings, True
    if outcome is not ScalarOutcome.value or selection.value is None:
        findings.append(
            _value_error(
                result,
                artifact_path,
                locator,
                Code.locator_unsupported_value,
                "selects an unsupported value",
            )
        )
        return findings, True
    if type(result.value) is not str:
        findings.append(
            _value_error(
                result,
                artifact_path,
                locator,
                Code.locator_unsupported_value,
                "selects an unsupported value",
            )
        )
        return findings, True
    actual = selection.value.decode("utf-8")
    if result.value != actual:
        findings.append(
            _value_error(
                result,
                artifact_path,
                locator,
                Code.result_value_mismatch,
                "does not match the recorded value",
            )
        )
        return findings, True
    return findings, False


def _check_one_tensorboard_histogram(
    result, artifact_path: str, locator: TensorboardHistogramLocator, selection
) -> tuple[list[Finding], bool]:
    findings: list[Finding] = []
    outcome = selection.outcome
    if outcome is ScalarOutcome.mismatch:
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.locator_mismatch, "was not found",
            )
        )
        return findings, True
    if outcome is ScalarOutcome.unsupported_scalar:
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.locator_unsupported_value,
                "selects an unsupported histogram encoding",
            )
        )
        return findings, True
    if outcome is not ScalarOutcome.value or selection.value is None:
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.locator_unsupported_value, "selects an unsupported value",
            )
        )
        return findings, True
    recorded = result.value
    if isinstance(recorded, HistogramValue):
        recorded = recorded.model_dump(mode="json")
    if not isinstance(recorded, dict) or recorded.get("kind") != "histogram":
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.locator_unsupported_value, "selects an unsupported value",
            )
        )
        return findings, True
    if recorded != selection.value:
        findings.append(
            _value_error(
                result, artifact_path, locator,
                Code.result_value_mismatch, "does not match the recorded value",
            )
        )
        return findings, True
    return findings, False


def _locator_kind(locator: Any) -> str:
    if isinstance(locator, TensorboardTf2ImageLocator):
        return "tensorboard-tf2-image"
    if isinstance(locator, TensorboardTf2AudioLocator):
        return "tensorboard-tf2-audio"
    if isinstance(locator, TensorboardTensorLocator):
        return "tensorboard-tensor"
    if isinstance(locator, TensorboardImageLocator):
        return "tensorboard-image"
    if isinstance(locator, TensorboardAudioLocator):
        return "tensorboard-audio"
    if isinstance(locator, TensorboardHistogramLocator):
        return "tensorboard-histogram"
    if isinstance(locator, TensorboardStringLocator):
        return "tensorboard-string"
    if isinstance(locator, TensorboardLocator):
        return "tensorboard"
    if isinstance(locator, CsvCellLocator):
        return "csv-cell"
    if isinstance(locator, JsonlLocator):
        return "jsonl"
    return "string"


def check_targeted_results(
    manifest: Manifest,
    artifact,
    target_check,
    *,
    csv_cell_capped: bool = False,
    csv_cell_eligible: bool = False,
    tensorboard_capped: bool = False,
    tensorboard_eligible: bool = False,
) -> tuple[list[Finding], set[str], bool]:
    """Validate and verify every 0.3/0.4/0.5 locator on one targeted artifact."""
    findings: list[Finding] = []
    broken: set[str] = set()
    results = _targeted_results(manifest, artifact.id)
    if not results:
        return findings, broken, False

    if target_check.state == "directory":
        if manifest.schema_version in (
            SCHEMA_VERSION_0_5,
            SCHEMA_VERSION_0_6,
            SCHEMA_VERSION_0_7,
            SCHEMA_VERSION_0_8,
            SCHEMA_VERSION_0_9,
            SCHEMA_VERSION_0_10,
        ):
            directory_hint = (
                "Point the result at a single JSON, JSONL, CSV or TensorBoard event "
                "file artifact, or drop the locator."
            )
        elif manifest.schema_version == SCHEMA_VERSION_0_4:
            directory_hint = (
                "Point the result at a single JSON, JSONL or CSV file artifact, or "
                "drop the locator."
            )
        else:
            directory_hint = (
                "Point the result at a single JSON/JSONL file artifact, or drop the locator."
            )
        for result in results:
            findings.append(
                _locator_finding(
                    Code.directory_locator_unsupported,
                    subject=result.id,
                    path=artifact.path,
                    message=(
                        f"result {result.id!r} locator {display_locator(result.locator)} "
                        f"targets a directory artifact {artifact.path!r}"
                    ),
                    hint=directory_hint,
                )
            )
            broken.add(result.id)
        return findings, broken, False

    if target_check.state in ("missing", "reference-failed"):
        return findings, broken, False

    if target_check.state in (
        "unreadable",
        "changed",
        "symlinked-parent",
        "refused-path",
        "refused-alias",
        "escapes-root",
    ):
        # The file check already emitted the artifact-level fixed error. This
        # precedes the extension/kind test: a path the reader refused for its
        # shape is not a kind mismatch, and asking for a ".jsonl artifact"
        # there would name the wrong defect. Nothing was read, so no locator
        # finding may replace the artifact-level refusal.
        return findings, broken, False

    # Reference checks precede digest/volatile state and content reading.
    kind_broken: set[str] = set()
    for result in results:
        if not _extension_matches(artifact.path, result.locator):
            expected = _expected_extension(result.locator)
            findings.append(
                _locator_finding(
                    Code.locator_kind_mismatch,
                    subject=result.id,
                    path=artifact.path,
                    message=(
                        f"result {result.id!r} locator {display_locator(result.locator)} "
                        f"needs a {expected} artifact, not {artifact.path!r}"
                    ),
                )
            )
            broken.add(result.id)
            kind_broken.add(result.id)

    # Capped path (RFC section 7.2): a capped kind on an eligible artifact in
    # state ok/over-cap gets exactly one artifact-level cap finding. Other
    # kinds on the same artifact continue their own path; kind mismatches above
    # stay additive and neither kind's cap suppresses the other.
    capped_kinds: set[str] = set()
    capped_artifact = False
    if target_check.state in ("ok", "over-cap"):
        if tensorboard_capped and tensorboard_eligible:
            findings.append(
                Finding(
                    code=Code.locator_unreadable,
                    severity=Severity.error,
                    subject=artifact.id,
                    path=artifact.path,
                    message=(
                        TENSORBOARD_CAP_MESSAGE_06
                        if manifest.schema_version
                        in (
                            SCHEMA_VERSION_0_6,
                            SCHEMA_VERSION_0_7,
                            SCHEMA_VERSION_0_8,
                            SCHEMA_VERSION_0_9,
                            SCHEMA_VERSION_0_10,
                        )
                        else TENSORBOARD_CAP_MESSAGE
                    ),
                    hint=None,
                )
            )
            capped_kinds.update(
                {
                    "tensorboard",
                    "tensorboard-string",
                    "tensorboard-histogram",
                    "tensorboard-image",
                    "tensorboard-audio",
                    "tensorboard-tensor",
                    "tensorboard-tf2-image",
                    "tensorboard-tf2-audio",
                }
            )
            capped_artifact = True
        if csv_cell_capped and csv_cell_eligible:
            findings.append(
                Finding(
                    code=Code.locator_unreadable,
                    severity=Severity.error,
                    subject=artifact.id,
                    path=artifact.path,
                    message=CSV_CELL_CAP_MESSAGE,
                    hint=None,
                )
            )
            capped_kinds.add("csv-cell")
            capped_artifact = True

    snapshot = target_check.snapshot if target_check.state == "ok" else None
    data = snapshot.data if snapshot is not None else None
    data_size = len(data) if data is not None else None
    # Per-kind file cap: 128 MiB for tensorboard, 50 MiB for every other kind.
    # A shared snapshot up to 128 MiB can still be over the 50 MiB cap for a
    # string/jsonl/csv-cell result on the same artifact.
    file_over_cap_128 = target_check.state == "over-cap" or (
        data_size is not None and data_size > TENSORBOARD_MAX_FILE_BYTES
    )
    file_over_cap_50 = target_check.state == "over-cap" or (
        data_size is not None and data_size > MAX_JSON_BYTES
    )

    usable: list = []
    for result in results:
        if result.id in kind_broken:
            continue
        kind = _locator_kind(result.locator)
        if kind in capped_kinds:
            continue
        if kind in (
            "tensorboard",
            "tensorboard-string",
            "tensorboard-histogram",
            "tensorboard-image",
            "tensorboard-audio",
            "tensorboard-tensor",
            "tensorboard-tf2-image",
            "tensorboard-tf2-audio",
        ):
            if file_over_cap_128:
                findings.append(
                    _value_error(
                        result, artifact.path, result.locator,
                        Code.locator_unreadable,
                        "was not read because the artifact exceeds 128 MiB",
                    )
                )
                broken.add(result.id)
                continue
        elif file_over_cap_50:
            findings.append(
                _value_error(
                    result, artifact.path, result.locator,
                    Code.locator_unreadable,
                    "was not read because the artifact exceeds 50 MiB",
                )
            )
            broken.add(result.id)
            continue
        usable.append(result)

    # ``edit.plan_rehash`` refuses the whole manifest when any object locator
    # exists, not only one on this artifact, so both no-digest branches use the
    # same manifest-wide predicate and wording.
    object_anywhere = any(
        result.locator is not None and _is_object_locator(result.locator)
        for result in manifest.results
    )
    no_digest_hint = (
        "rehash cannot record a digest while any object locator exists in the "
        "manifest; record sha256 manually or regenerate the manifest."
        if object_anywhere
        else "rehash can record the missing digest; only string locators target this artifact."
    )
    if not usable:
        # A kind/extension mismatch is a reference refusal: no locator finding
        # replaces the artifact-level state warning. Preserve the warning the
        # legacy path would have emitted instead of silently dropping it, with
        # the same manifest-wide hint the usable branch uses.
        if target_check.state == "no-digest":
            findings.append(
                Finding(
                    code=Code.no_digest,
                    severity=Severity.warning,
                    subject=artifact.id,
                    path=artifact.path,
                    message=(
                        f"Artifact {artifact.id!r} has no recorded sha256, so changes "
                        "cannot be detected"
                    ),
                    hint=no_digest_hint,
                )
            )
        return findings, broken, capped_artifact

    if target_check.state == "no-digest":
        for result in usable:
            findings.append(
                _value_error(
                    result, artifact.path, result.locator,
                    Code.locator_no_digest, "targets an artifact with no recorded sha256",
                ).model_copy(update={"hint": no_digest_hint})
            )
            broken.add(result.id)
        return findings, broken, capped_artifact

    if target_check.state == "volatile":
        for result in usable:
            findings.append(
                _value_error(
                    result, artifact.path, result.locator,
                    Code.locator_unverified, "targets a volatile artifact",
                ).model_copy(
                    update={"hint": "Remove --volatile or the locator; volatile artifacts are not verified."}
                )
            )
            broken.add(result.id)
        return findings, broken, capped_artifact

    if target_check.state != "ok" or snapshot is None or data is None:
        return findings, broken, capped_artifact

    # TensorBoard routing is before the JSONL line pre-scan: one scan per
    # artifact supplies every tensorboard result. A structural refusal is
    # per-kind and never stops string/jsonl/csv-cell results.
    tensorboard_results = [
        result
        for result in usable
        if isinstance(
            result.locator,
            (
                TensorboardLocator,
                TensorboardStringLocator,
                TensorboardHistogramLocator,
                TensorboardImageLocator,
                TensorboardAudioLocator,
                TensorboardTensorLocator,
                TensorboardTf2ImageLocator,
                TensorboardTf2AudioLocator,
            ),
        )
    ]
    tensorboard_refused: set[str] = set()
    scalar_selections = None
    text_selections = None
    hist_selections = None
    image_selections = None
    audio_selections = None
    tensor_selections = None
    tf2_image_selections = None
    tf2_audio_selections = None
    if tensorboard_results:
        scalar_requests = [
            (
                result.locator.tag.encode("utf-8"),
                result.locator.step,
                result.locator.occurrence,
            )
            for result in tensorboard_results
            if isinstance(result.locator, TensorboardLocator)
        ]
        text_requests = [
            (
                result.locator.tag.encode("utf-8"),
                result.locator.step,
                result.locator.occurrence,
            )
            for result in tensorboard_results
            if isinstance(result.locator, TensorboardStringLocator)
        ]
        hist_requests = [
            (
                result.locator.tag.encode("utf-8"),
                result.locator.step,
                result.locator.occurrence,
            )
            for result in tensorboard_results
            if isinstance(result.locator, TensorboardHistogramLocator)
        ]
        image_requests = [
            (
                result.locator.tag.encode("utf-8"),
                result.locator.step,
                result.locator.occurrence,
            )
            for result in tensorboard_results
            if isinstance(result.locator, TensorboardImageLocator)
        ]
        audio_requests = [
            (
                result.locator.tag.encode("utf-8"),
                result.locator.step,
                result.locator.occurrence,
            )
            for result in tensorboard_results
            if isinstance(result.locator, TensorboardAudioLocator)
        ]
        tensor_requests = [
            (
                result.locator.tag.encode("utf-8"),
                result.locator.step,
                result.locator.occurrence,
            )
            for result in tensorboard_results
            if isinstance(result.locator, TensorboardTensorLocator)
        ]
        tf2_image_requests = [
            (
                result.locator.tag.encode("utf-8"),
                result.locator.step,
                result.locator.occurrence,
                result.locator.element,
            )
            for result in tensorboard_results
            if isinstance(result.locator, TensorboardTf2ImageLocator)
        ]
        tf2_audio_requests = [
            (
                result.locator.tag.encode("utf-8"),
                result.locator.step,
                result.locator.occurrence,
                result.locator.element,
            )
            for result in tensorboard_results
            if isinstance(result.locator, TensorboardTf2AudioLocator)
        ]
        try:
            if manifest.schema_version == SCHEMA_VERSION_0_10:
                (
                    scalar_selections,
                    text_selections,
                    hist_selections,
                    image_selections,
                    audio_selections,
                    tensor_selections,
                    tf2_image_selections,
                    tf2_audio_selections,
                ) = scan_tensorboard_10(
                    data,
                    scalar_requests,
                    text_requests,
                    hist_requests,
                    image_requests,
                    audio_requests,
                    tensor_requests,
                    tf2_image_requests,
                    tf2_audio_requests,
                )
            elif manifest.schema_version == SCHEMA_VERSION_0_9:
                (
                    scalar_selections,
                    text_selections,
                    hist_selections,
                    image_selections,
                    audio_selections,
                    tensor_selections,
                ) = scan_tensorboard_09(
                    data,
                    scalar_requests,
                    text_requests,
                    hist_requests,
                    image_requests,
                    audio_requests,
                    tensor_requests,
                )
            elif manifest.schema_version == SCHEMA_VERSION_0_8:
                (
                    scalar_selections,
                    text_selections,
                    hist_selections,
                    image_selections,
                    audio_selections,
                ) = scan_tensorboard_08(
                    data,
                    scalar_requests,
                    text_requests,
                    hist_requests,
                    image_requests,
                    audio_requests,
                )
            elif manifest.schema_version == SCHEMA_VERSION_0_7:
                (
                    scalar_selections,
                    text_selections,
                    hist_selections,
                ) = scan_tensorboard_07(
                    data, scalar_requests, text_requests, hist_requests
                )
            elif (
                manifest.schema_version == SCHEMA_VERSION_0_6
                or text_requests
            ):
                scalar_selections, text_selections = scan_tensorboard_06(
                    data, scalar_requests, text_requests
                )
            else:
                scalar_selections = scan_scalars(data, scalar_requests)
                text_selections = None
        except EventFileError:
            for result in tensorboard_results:
                finding = _value_error(
                    result, artifact.path, result.locator,
                    Code.locator_unreadable,
                    "could not be read under the strict TensorBoard profile",
                )
                findings.append(
                    finding.model_copy(update={"hint": TENSORBOARD_PROFILE_HINT})
                )
                broken.add(result.id)
                tensorboard_refused.add(result.id)

    values = _LocatorValues(data)
    # The JSONL pre-scan accepts only JsonlLocator entries: a csv-cell or
    # tensorboard result has no ``line`` and is routed to its own pass.
    selected_lines = [
        result.locator.line
        for result in usable
        if isinstance(result.locator, JsonlLocator)
    ]
    if selected_lines:
        # One forward scan records every requested line's offset before any
        # value is read; distinct selections are not O(results x file size).
        values.request_lines(selected_lines)
    csv_results = [
        result for result in usable if isinstance(result.locator, CsvCellLocator)
    ]
    csv_profile: CsvProfile | None = None
    if csv_results:
        try:
            # One strict whole-file pass per artifact, retaining only the
            # requested data-row offsets and the parsed header. The delivered
            # profile takes bytes; a TensorBoard-raised snapshot is a bytearray,
            # and this copy is bounded by the 50 MiB per-kind cap above.
            csv_data = data if type(data) is bytes else bytes(data)
            csv_profile = CsvProfile(
                csv_data, rows=[result.locator.row for result in csv_results]
            )
        except CsvProfileError:
            # Structural refusal affects only csv-cell results; jsonl/string
            # results on the same artifact still compare normally.
            csv_profile = None
    for result in usable:
        if result.id in tensorboard_refused:
            continue
        if isinstance(result.locator, TensorboardLocator):
            assert scalar_selections is not None
            key = (
                result.locator.tag.encode("utf-8"),
                result.locator.step,
                result.locator.occurrence,
            )
            result_findings, failed = _check_one_tensorboard(
                result, artifact.path, result.locator, scalar_selections[key]
            )
        elif isinstance(result.locator, TensorboardStringLocator):
            assert text_selections is not None
            key = (
                result.locator.tag.encode("utf-8"),
                result.locator.step,
                result.locator.occurrence,
            )
            result_findings, failed = _check_one_tensorboard_string(
                result, artifact.path, result.locator, text_selections[key]
            )
        elif isinstance(result.locator, TensorboardHistogramLocator):
            assert hist_selections is not None
            key = (
                result.locator.tag.encode("utf-8"),
                result.locator.step,
                result.locator.occurrence,
            )
            result_findings, failed = _check_one_tensorboard_histogram(
                result, artifact.path, result.locator, hist_selections[key]
            )
        elif isinstance(result.locator, TensorboardImageLocator):
            assert image_selections is not None
            key = (
                result.locator.tag.encode("utf-8"),
                result.locator.step,
                result.locator.occurrence,
            )
            result_findings, failed = _check_one_tensorboard_media(
                result,
                artifact.path,
                result.locator,
                image_selections[key],
                kind="image",
                model=ImageValue,
            )
        elif isinstance(result.locator, TensorboardAudioLocator):
            assert audio_selections is not None
            key = (
                result.locator.tag.encode("utf-8"),
                result.locator.step,
                result.locator.occurrence,
            )
            result_findings, failed = _check_one_tensorboard_media(
                result,
                artifact.path,
                result.locator,
                audio_selections[key],
                kind="audio",
                model=AudioValue,
            )
        elif isinstance(result.locator, TensorboardTf2ImageLocator):
            assert tf2_image_selections is not None
            key = (
                result.locator.tag.encode("utf-8"),
                result.locator.step,
                result.locator.occurrence,
                result.locator.element,
            )
            result_findings, failed = _check_one_tensorboard_media(
                result,
                artifact.path,
                result.locator,
                tf2_image_selections[key],
                kind="image",
                model=ImageValue,
                label="tf2 image",
            )
        elif isinstance(result.locator, TensorboardTf2AudioLocator):
            assert tf2_audio_selections is not None
            key = (
                result.locator.tag.encode("utf-8"),
                result.locator.step,
                result.locator.occurrence,
                result.locator.element,
            )
            result_findings, failed = _check_one_tensorboard_media(
                result,
                artifact.path,
                result.locator,
                tf2_audio_selections[key],
                kind="audio",
                model=AudioValue,
                label="tf2 audio",
            )
        elif isinstance(result.locator, TensorboardTensorLocator):
            assert tensor_selections is not None
            key = (
                result.locator.tag.encode("utf-8"),
                result.locator.step,
                result.locator.occurrence,
            )
            result_findings, failed = _check_one_tensorboard_media(
                result,
                artifact.path,
                result.locator,
                tensor_selections[key],
                kind="tensor",
                model=TensorValue,
            )
        elif isinstance(result.locator, CsvCellLocator):
            result_findings, failed = _check_one_csv_cell(
                result, artifact.path, result.locator, csv_profile
            )
        else:
            result_findings, failed = _check_one_targeted(
                result, artifact.path, result.locator, values
            )
        findings.extend(result_findings)
        if failed:
            broken.add(result.id)
    # Drop the snapshot wrapper before returning so its bytes are released as
    # soon as this artifact's locators are done.
    del csv_profile
    del values
    return findings, broken, capped_artifact


def check_results(
    manifest: Manifest,
    readable_paths: dict[str, Path],
    *,
    enable_directory_artifacts: bool = False,
    skip_result_ids: set[str] | None = None,
) -> tuple[list[Finding], set[str]]:
    """``readable_paths`` includes files whose hash changed: if a metrics
    file was edited, the most useful thing to say is whether the number a
    claim relies on changed with it.

    With the internal ``enable_directory_artifacts`` switch, a locator aimed
    at a directory artifact is an error and its result counts as mismatched,
    since there is no single JSON file to read the value from."""
    findings: list[Finding] = []
    mismatched: set[str] = set()
    artifact_ids = {a.id for a in manifest.artifacts}
    paths_by_id = {a.id: a.path for a in manifest.artifacts}
    directory_ids = (
        {a.id for a in manifest.artifacts if a.artifact_type == "directory"} if enable_directory_artifacts else set()
    )
    skip = skip_result_ids or set()
    has_locators = manifest.schema_version in (
        SCHEMA_VERSION_0_3,
        SCHEMA_VERSION_0_4,
        SCHEMA_VERSION_0_5,
        SCHEMA_VERSION_0_6,
        SCHEMA_VERSION_0_7,
        SCHEMA_VERSION_0_8,
        SCHEMA_VERSION_0_9,
        SCHEMA_VERSION_0_10,
    )

    def unverifiable(res_id: str, msg: str) -> None:
        findings.append(
            Finding(code=Code.result_unverifiable, severity=Severity.warning, subject=res_id, message=msg)
        )

    cache: dict[str, Any] = {}
    for res in manifest.results:
        if res.id in skip:
            continue
        if res.locator is None:
            if has_locators:
                findings.append(
                    Finding(
                        code=Code.result_unverified,
                        severity=Severity.warning,
                        subject=res.id,
                        message=(
                            f"result {res.id!r} has no locator in schema "
                            f"{manifest.schema_version}; its recorded value was not checked"
                        ),
                    )
                )
            continue
        if has_locators:
            # Any 0.3/0.4 locator is handled through check_targeted_results so
            # it shares the file check's snapshot. Direct callers that do not
            # skip it still get a total, fixed outcome rather than a crash.
            findings.append(
                _value_error(
                    res, paths_by_id.get(res.artifact_id, ""), res.locator,
                    Code.locator_unreadable, "was not checked without a shared snapshot",
                )
            )
            mismatched.add(res.id)
            continue
        if res.artifact_id is None:
            unverifiable(res.id, f"Result {res.id!r} has a locator but no artifact_id to read it from")
            continue
        if res.artifact_id in directory_ids:
            mismatched.add(res.id)
            findings.append(
                Finding(
                    code=Code.directory_locator_unsupported,
                    severity=Severity.error,
                    subject=res.id,
                    path=paths_by_id[res.artifact_id],
                    message=(
                        f"Result {res.id!r} has locator {res.locator!r} but {res.artifact_id!r} "
                        "is a directory artifact; locators need a single JSON file"
                    ),
                    hint="Point the result at a JSON file artifact, or drop the locator.",
                )
            )
            continue
        path = readable_paths.get(res.artifact_id)
        if res.artifact_id not in artifact_ids or path is None:
            continue  # already reported as a dangling reference or missing file
        if path.suffix.lower() != ".json":
            unverifiable(res.id, f"Result {res.id!r}: locators are only supported for .json files ({path.name})")
            continue
        if res.artifact_id not in cache:
            try:
                if path.stat().st_size > MAX_JSON_BYTES:
                    raise ValueError(f"file larger than {MAX_JSON_BYTES} bytes")
                cache[res.artifact_id] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, RecursionError) as exc:
                cache[res.artifact_id] = exc
        doc = cache[res.artifact_id]
        if isinstance(doc, Exception):
            unverifiable(res.id, f"Result {res.id!r}: could not parse {path.name} as JSON: {doc}")
            continue
        actual = resolve_pointer(doc, res.locator)
        if actual is _MISSING:
            unverifiable(res.id, f"Result {res.id!r}: locator {res.locator!r} not found in {path.name}")
            continue
        if not values_match(res.value, actual):
            mismatched.add(res.id)
            findings.append(
                Finding(
                    code=Code.result_value_mismatch,
                    severity=Severity.error,
                    subject=res.id,
                    message=(
                        f"Result {res.id!r} ({res.name}) records {res.value!r} but "
                        f"{paths_by_id[res.artifact_id]} at {res.locator} contains {actual!r}"
                    ),
                )
            )
    return findings, mismatched
