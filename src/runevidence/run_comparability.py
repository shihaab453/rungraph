"""Experimental read-only comparability command module (Stage 2a).

Status: experimental; read-only. This is the Stage 2a command module; the
Stage 2b CLI route is registered in ``cli.py``. It provides the privacy-safe
command layer over the frozen PC26 core and the accepted Stage 1 bridge. It
implements the Stage 2a contract in
``docs/comparability-stage2-privacy-cli-rfc.md`` (v0.3): a closed result
envelope, closed application-error documents, a fixed text renderer and a fixed
failure-precedence boundary.

Trusted local use only. The caller must be authorized to read both whole
manifests and to disclose the comparison result to every recipient. Ordinary
unmodified ``load_manifest`` snapshots are assumed. This module does not cover
hostile Python objects, subclasses with hooks, ``model_construct`` substitutes,
monkeypatched models, concurrent mutation of snapshots, a shared or remotely
callable comparison service, cross-tenant use, attacker-controlled adaptive
querying, debuggers or traceback locals, process memory or crash dumps, OS or
timing observations, resource usage, command history, terminal capture, CI
command echoes or host audit records.

Processing disclosure: both operands are loaded through ``load_manifest`` as
complete manifests, including all metadata, names, paths, locators, artifact
IDs and source bytes; field selection applies only to comparison mapping, not
file acquisition. Comparison then reads the documented condition fields,
selected result names, manifest-wide IDs and relevant relationships, and scans
metadata on the selected result, selected run and attached artifacts for
nonfinite numbers. Artifact paths, locators and URLs are never dereferenced.

Output omits raw evidence values, IDs, names, paths, hashes, metadata keys and
exception text. It reveals condition presence, states, equality or difference,
counts, display-name difference and fixed rejection categories. Repeated
comparisons can reveal sensitive information; output is not anonymous or safe
for unrestricted publication.

INSUFFICIENT_EVIDENCE is not a summary of side states: for a top-level string
difference, a case/whitespace-only change can be insufficient while both side
states are KNOWN. Dict/list dimensions cannot take that path. The public
projection does not expose the values needed to distinguish the cause. A
CONFLICTING side likewise requires at least two non-null observations.

Stage 1 numeric limitation: exact JSON numbers whose magnitude exceeds the
core's NUMBER_LIMIT are not rejected by the bridge; they reach the core's
sanitised rejected-input projection (``input_rejected``/``NUMBER_LIMIT``) and
are returned in a result envelope with exit code 2. That is intentionally a
core rejection, not a ``MAPPING_FAILED`` application error.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import _experimental_comparability_bridge as bridge
from . import comparability
from . import manifest
from .manifest import ManifestLoadError
from .models import (
    SCHEMA_VERSION_0_3,
    SCHEMA_VERSION_0_4,
    SCHEMA_VERSION_0_5,
    SCHEMA_VERSION_0_6,
    SCHEMA_VERSION_0_7,
)

# Comparability is defined on the 0.1/0.2 recorded-condition projections.
# Every later schema refuses rather than compare silently.
_UNSUPPORTED_COMPARABILITY_SCHEMAS = (
    SCHEMA_VERSION_0_3,
    SCHEMA_VERSION_0_4,
    SCHEMA_VERSION_0_5,
    SCHEMA_VERSION_0_6,
)

__all__ = [
    "APPLICATION_STATUS",
    "CommandOutcome",
    "ERROR_FORMAT",
    "EXIT_COMPARABLE",
    "EXIT_NOT_COMPARABLE",
    "EXIT_UNAVAILABLE",
    "NOTICE",
    "PREFLIGHT_JSON_REFUSAL",
    "PREFLIGHT_INVALID_ARGUMENTS",
    "PreflightOutcome",
    "RESULT_FORMAT",
    "UNSUPPORTED_VERSION",
    "compare_paths",
    "invalid_arguments",
    "preflight_paths",
]

PREFLIGHT_JSON_REFUSAL = (
    "ERROR: comparability --preflight is text only; --json is not available (exit 2)"
)
PREFLIGHT_INVALID_ARGUMENTS = "ERROR INVALID_ARGUMENTS (exit 2)"
PREFLIGHT_DISPLAY_CAP = 20
PREFLIGHT_ID_PLACEHOLDER = "<id longer than 1024 characters>"
PREFLIGHT_ID_UNAVAILABLE = "<result id unavailable>"
PREFLIGHT_FOOTER = (
    "Readiness does not establish matching values, artifact integrity, or a "
    "successful comparison.",
    "Omitted candidates and pairwise compatibility were not assessed.",
)
PREFLIGHT_READY_EXCEPTION = (
    "A ready pair can still be INSUFFICIENT_EVIDENCE when a text dimension "
    "differs only by case or surrounding whitespace."
)

RESULT_FORMAT = "runevidence-comparability-result-v1"
ERROR_FORMAT = "runevidence-comparability-error-v1"
APPLICATION_STATUS = "EXPERIMENTAL_READ_ONLY_CLI"
NOTICE = (
    "Embedded prototype and integration flags describe the frozen PC26 core, "
    "not this CLI."
)

EXIT_COMPARABLE = 0
EXIT_NOT_COMPARABLE = 1
EXIT_UNAVAILABLE = 2

DISCLAIMER = (
    "COMPARABLE_RECORDED_CONDITIONS means only that five mandatory recorded "
    "dimensions matched. It does not establish artifact integrity, causal "
    "equivalence, reproducibility, statistical equivalence or correct "
    "recording. Missing evidence, identical run-ID strings across manifests, "
    "unsupported shapes and bounds may prevent comparison. This is not "
    "production qualification."
)

# Closed application-error vocabulary.
MANIFEST_LOAD_FAILED = "MANIFEST_LOAD_FAILED"
MAPPING_FAILED = "MAPPING_FAILED"
INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
INTERNAL_ERROR = "INTERNAL_ERROR"
ERROR_CODES = frozenset(
    (
        MANIFEST_LOAD_FAILED,
        MAPPING_FAILED,
        INVALID_ARGUMENTS,
        UNSUPPORTED_VERSION,
        INTERNAL_ERROR,
    )
)
ERROR_SIDES = frozenset(("baseline", "target"))

# The Stage 1 bridge catalogue, copied as an explicit closed set. Validation is
# against this catalogue, never against arbitrary exception text.
MAPPING_CODES = frozenset(
    (
        bridge.INVALID_SELECTOR,
        bridge.RESULT_NOT_FOUND,
        bridge.RESULT_SELECTION_AMBIGUOUS,
        bridge.RUN_NOT_FOUND,
        bridge.RUN_SELECTION_AMBIGUOUS,
        bridge.DUPLICATE_ID,
        bridge.DANGLING_REFERENCE,
        bridge.CONFLICTING_OWNERSHIP,
        bridge.INPUT_SHAPE_UNSUPPORTED,
        bridge.INPUT_BOUND_EXCEEDED,
        bridge.INTERNAL_MAPPING_ERROR,
    )
)

# Exact core public-projection key set, shape and closed vocabularies.
CORE_KEYS = frozenset(
    (
        "format",
        "prototype",
        "integrated_into_rungraph",
        "integration_status",
        "decision",
        "reason_codes",
        "dimensions",
        "unknown_dimension_count",
        "unknown_dimensions",
        "input_rejected",
        "input_error_codes",
    )
)
DECISIONS = frozenset(
    (comparability.COMPARABLE, comparability.NOT_COMPARABLE, comparability.INSUFFICIENT)
)
DIMENSION_KEYS = frozenset(
    ("dimension", "designation", "status", "baseline", "target")
)
UNKNOWN_DIMENSION_KEYS = frozenset(
    ("reference", "status", "present_on", "baseline", "target")
)
SIDE_KEYS = frozenset(
    ("present", "observation_count", "null_observation_count", "state")
)
UNKNOWN_SIDE_KEYS = frozenset(
    ("present", "observation_count", "null_observation_count")
)
DESIGNATIONS = frozenset(("mandatory", "optional"))
STATUSES = frozenset(
    (comparability.MATCH, comparability.DIFFERENT, comparability.INSUFF)
)
STATES = frozenset(
    (
        comparability.KNOWN,
        comparability.MISSING,
        comparability.UNKNOWN,
        comparability.CONFLICTING,
        comparability.AMBIGUOUS,
    )
)
PRESENT_ON = (["baseline"], ["target"], ["baseline", "target"])
INTEGRATION_STATUS = "EXPERIMENTAL_CORE_ONLY"

# The core's canonical dimension table order and default designations.
DIMENSION_ORDER = tuple(sorted(comparability.KNOWN_DIMENSIONS))
DESIGNATION_BY_DIMENSION = {
    name: spec["default"] for name, spec in comparability.DIMENSIONS.items()
}
MANDATORY_ORDER = tuple(
    name for name in DIMENSION_ORDER
    if DESIGNATION_BY_DIMENSION[name] == "mandatory"
)
# Bridge values that can be a top-level string. Only these can produce the
# core's both-sides-KNOWN INSUFFICIENT result for a case/whitespace-only
# difference; the remaining dimensions are dict/list shapes.
TOP_LEVEL_STRING_DIMENSIONS = frozenset(
    (
        "dataset_identity",
        "dataset_version",
        "evaluation_split",
        "source_revision",
        "model_checkpoint",
    )
)


@dataclass(frozen=True)
class CommandOutcome:
    """A complete command result: exit code, JSON document and fixed text."""

    exit_code: int
    document: dict
    text: str


@dataclass(frozen=True)
class PreflightOutcome:
    """A complete text-only preflight result: exit code and fixed text."""

    exit_code: int
    text: str


# --------------------------------------------------------------- validation


def _valid_codes(values, catalogue) -> bool:
    if type(values) is not list:
        return False
    if any(type(value) is not str or value not in catalogue for value in values):
        return False
    return values == sorted(set(values))


def _valid_side(side, *, unknown: bool) -> bool:
    keys = UNKNOWN_SIDE_KEYS if unknown else SIDE_KEYS
    if type(side) is not dict or set(side) != keys:
        return False
    if type(side["present"]) is not bool:
        return False
    for name in ("observation_count", "null_observation_count"):
        value = side[name]
        if type(value) is not int or not 0 <= value <= 16:
            return False
    observations = side["observation_count"]
    nulls = side["null_observation_count"]
    if nulls > observations:
        return False

    if unknown:
        # Unknown rows have no state. Absence must carry no observation counts.
        return side["present"] or (observations == 0 and nulls == 0)

    if type(side["state"]) is not str or side["state"] not in STATES:
        return False
    state = side["state"]
    if side["present"] is False:
        return state == comparability.MISSING and observations == 0 and nulls == 0
    if state == comparability.MISSING:
        return observations == 0 and nulls == 0
    if state == comparability.UNKNOWN:
        return observations > 0 and nulls == observations
    if state == comparability.CONFLICTING:
        # A conflict needs at least two distinct non-null observations.
        return observations - nulls >= 2
    # KNOWN and AMBIGUOUS require at least one known value.
    return nulls < observations


def _valid_row(row) -> bool:
    if type(row) is not dict or set(row) != DIMENSION_KEYS:
        return False
    if type(row["dimension"]) is not str:
        return False
    if row["dimension"] not in comparability.KNOWN_DIMENSIONS:
        return False
    if row["designation"] != DESIGNATION_BY_DIMENSION.get(row["dimension"]):
        return False
    if type(row["status"]) is not str or row["status"] not in STATUSES:
        return False
    if not _valid_side(row["baseline"], unknown=False):
        return False
    if not _valid_side(row["target"], unknown=False):
        return False
    baseline_state = row["baseline"]["state"]
    target_state = row["target"]["state"]
    if row["status"] in (comparability.MATCH, comparability.DIFFERENT):
        return (
            baseline_state == comparability.KNOWN
            and target_state == comparability.KNOWN
        )
    # INSUFFICIENT may have any side state. Both-KNOWN INSUFF is possible only
    # for top-level string values, where the core treats a case/whitespace-only
    # difference as insufficient. Dict/list dimensions cannot take that path.
    if (
        baseline_state == comparability.KNOWN
        and target_state == comparability.KNOWN
    ):
        return row["dimension"] in TOP_LEVEL_STRING_DIMENSIONS
    return True


def _valid_unknown(row, index: int) -> bool:
    if type(row) is not dict or set(row) != UNKNOWN_DIMENSION_KEYS:
        return False
    if row["reference"] != "unknown_%04d" % index:
        return False
    if row["status"] != "UNRECOGNIZED":
        return False
    if type(row["present_on"]) is not list or row["present_on"] not in PRESENT_ON:
        return False
    if not _valid_side(row["baseline"], unknown=True):
        return False
    if not _valid_side(row["target"], unknown=True):
        return False
    expected_present_on = [
        side for side in ("baseline", "target") if row[side]["present"]
    ]
    return row["present_on"] == expected_present_on


def _dimension_reason(row, suffix: str) -> str:
    prefix = "MANDATORY" if row["designation"] == "mandatory" else "OPTIONAL"
    return f"{prefix}_DIMENSION_{suffix}:{row['dimension']}"


def _valid_projection(core) -> bool:
    """Structural and decision-invariant check for the core public projection.

    This validates the closed key sets, canonical dimension coverage and order,
    per-side count/state consistency, default designations, the
    MATCH/DIFFERENT side-state requirement, decision/reason consistency and
    unknown-row shape. INSUFFICIENT may have either side state; when both sides
    are KNOWN the dimension must be one whose bridge value is a top-level
    string, because only there can the core's case/whitespace-only difference
    produce that shape. The public counts and states still cannot distinguish
    that cause from a fabricated projection with the same shape. This does not
    re-verify the underlying recorded values and is not a cryptographic proof
    that the projection was produced by ``comparability.evaluate``.
    """
    if type(core) is not dict or set(core) != CORE_KEYS:
        return False
    if core["format"] != comparability.PUBLIC_FORMAT:
        return False
    if core["prototype"] is not True or core["integrated_into_rungraph"] is not False:
        return False
    if core["integration_status"] != INTEGRATION_STATUS:
        return False
    if type(core["decision"]) is not str or core["decision"] not in DECISIONS:
        return False
    if not _valid_codes(core["reason_codes"], comparability.PUBLIC_REASON_CODES):
        return False
    if not _valid_codes(core["input_error_codes"], comparability.PUBLIC_ERROR_CODES):
        return False
    if type(core["input_rejected"]) is not bool:
        return False

    count = core["unknown_dimension_count"]
    if type(count) is not int or not 0 <= count <= 64:
        return False
    dimensions, unknown = core["dimensions"], core["unknown_dimensions"]
    if type(dimensions) is not list or type(unknown) is not list:
        return False
    if len(unknown) != count or len(dimensions) > len(DIMENSION_ORDER):
        return False
    if not all(_valid_row(row) for row in dimensions):
        return False
    if not all(_valid_unknown(row, index) for index, row in enumerate(unknown, 1)):
        return False

    reasons = set(core["reason_codes"])
    if core["input_rejected"]:
        return (
            core["decision"] == comparability.INSUFFICIENT
            and core["reason_codes"] == ["MALFORMED_INPUT"]
            and dimensions == []
            and unknown == []
            and count == 0
            and bool(core["input_error_codes"])
        )

    if core["input_error_codes"]:
        return False
    if len(dimensions) != len(DIMENSION_ORDER):
        return False
    if [row["dimension"] for row in dimensions] != list(DIMENSION_ORDER):
        return False
    if "MALFORMED_INPUT" in reasons:
        return False
    if "ALL_MANDATORY_DIMENSIONS_MATCH" in reasons and (
        core["decision"] != comparability.COMPARABLE
    ):
        return False
    if count == 0 and "UNRECOGNIZED_DIMENSION_PRESENT" in reasons:
        return False
    if count > 0 and "UNRECOGNIZED_DIMENSION_PRESENT" not in reasons:
        return False

    for row in dimensions:
        if row["status"] == comparability.DIFFERENT:
            if _dimension_reason(row, "DIFFERENT") not in reasons:
                return False
        elif row["status"] == comparability.INSUFF:
            if _dimension_reason(row, "INSUFFICIENT") not in reasons:
                return False

    mandatory_rows = [
        row for row in dimensions if row["designation"] == "mandatory"
    ]
    mandatory_different = any(
        row["status"] == comparability.DIFFERENT for row in mandatory_rows
    )
    mandatory_insufficient = any(
        row["status"] == comparability.INSUFF for row in mandatory_rows
    )
    decision = core["decision"]
    run_missing = any(
        reason in ("RUN_IDENTITY_MISSING:baseline", "RUN_IDENTITY_MISSING:target")
        for reason in reasons
    )

    if mandatory_different and decision != comparability.NOT_COMPARABLE:
        return False
    if decision == comparability.NOT_COMPARABLE:
        return mandatory_different
    if decision == comparability.COMPARABLE:
        return (
            not mandatory_different
            and not mandatory_insufficient
            and not run_missing
            and count == 0
            and "ALL_MANDATORY_DIMENSIONS_MATCH" in reasons
        )
    # INSUFFICIENT_EVIDENCE: no mandatory difference, and at least one of the
    # core's three insufficient triggers must be present.
    return (
        not mandatory_different
        and (mandatory_insufficient or count > 0 or run_missing)
    )


# ------------------------------------------------------------------ outcome


def _error_outcome(
    code: str,
    side: str | None,
    mapping_code: str | None = None,
) -> CommandOutcome:
    """Build one complete closed application-error document and fixed text."""
    # Fixed local validation messages only; this is a programmer-error guard.
    if type(code) is not str or code not in ERROR_CODES:
        raise ValueError("INTERNAL_ERROR_CODE")
    if side is not None and (type(side) is not str or side not in ERROR_SIDES):
        raise ValueError("INTERNAL_ERROR_SIDE")
    if code == MAPPING_FAILED:
        if type(mapping_code) is not str or mapping_code not in MAPPING_CODES:
            raise ValueError("INTERNAL_MAPPING_CODE")
    elif mapping_code is not None:
        raise ValueError("INTERNAL_MAPPING_CODE")

    error: dict = {"code": code, "side": side}
    if mapping_code is not None:
        error["mapping_code"] = mapping_code
    document = {
        "format": ERROR_FORMAT,
        "ok": False,
        "exit_code": EXIT_UNAVAILABLE,
        "error": error,
    }
    if code == INVALID_ARGUMENTS:
        text = "ERROR INVALID_ARGUMENTS (exit 2)"
    elif code == MANIFEST_LOAD_FAILED:
        text = f"ERROR MANIFEST_LOAD_FAILED ({side}) (exit 2)"
    elif code == UNSUPPORTED_VERSION:
        text = f"ERROR UNSUPPORTED_VERSION ({side}) (exit 2)"
    elif code == MAPPING_FAILED:
        if side is None:
            text = f"ERROR MAPPING_FAILED ({mapping_code}) (exit 2)"
        else:
            text = f"ERROR MAPPING_FAILED ({side}, {mapping_code}) (exit 2)"
    else:
        text = "ERROR INTERNAL_ERROR (exit 2)"
    return CommandOutcome(exit_code=EXIT_UNAVAILABLE, document=document, text=text)


def _internal_error() -> CommandOutcome:
    return _error_outcome(INTERNAL_ERROR, None)


def _render(core: dict) -> str:
    """Render only the validated projection vocabulary. No values or names."""
    lines = [
        f"Comparability of recorded conditions: {core['decision']}",
        f"Application status: {APPLICATION_STATUS}",
    ]
    for designation, heading in (
        ("mandatory", "Mandatory dimensions:"),
        ("optional", "Optional dimensions:"),
    ):
        lines.append(heading)
        for row in core["dimensions"]:
            if row["designation"] != designation:
                continue
            baseline, target = row["baseline"], row["target"]
            lines.append(
                "  %s: %s (baseline observations %d, nulls %d; "
                "target observations %d, nulls %d)"
                % (
                    row["dimension"],
                    row["status"],
                    baseline["observation_count"],
                    baseline["null_observation_count"],
                    target["observation_count"],
                    target["null_observation_count"],
                )
            )
    reason_codes = core["reason_codes"]
    lines.append(
        "Reason codes: " + (", ".join(reason_codes) if reason_codes else "none")
    )
    if core["input_error_codes"]:
        lines.append("Core input errors: " + ", ".join(core["input_error_codes"]))
    lines.append(f"Unknown dimensions: {core['unknown_dimension_count']}")
    lines.append(NOTICE)
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def _result_outcome(core: dict) -> CommandOutcome:
    if not _valid_projection(core):
        return _internal_error()
    document = {
        "format": RESULT_FORMAT,
        "application_status": APPLICATION_STATUS,
        "core": core,
        "notice": NOTICE,
    }
    if core["input_rejected"]:
        exit_code = EXIT_UNAVAILABLE
    elif core["decision"] == comparability.COMPARABLE:
        exit_code = EXIT_COMPARABLE
    elif core["decision"] == comparability.NOT_COMPARABLE:
        exit_code = EXIT_NOT_COMPARABLE
    else:
        exit_code = EXIT_UNAVAILABLE
    return CommandOutcome(exit_code=exit_code, document=document, text=_render(core))


# ------------------------------------------------------------------- public


def compare_paths(
    baseline: Path,
    target: Path,
    *,
    baseline_result_id: str,
    target_result_id: str,
) -> CommandOutcome:
    """Compare recorded conditions in two manifests, without artifact I/O.

    Only the two ``load_manifest`` calls perform I/O. Failures are sanitised to
    the closed error vocabulary; no loader finding, path, ID, value or exception
    text leaves this boundary. Precedence is baseline load, target load,
    schema-version refusal (baseline then target), mapping, then unexpected
    error.
    """
    try:
        baseline_loaded = manifest.load_manifest(baseline)
    except ManifestLoadError:
        return _error_outcome(MANIFEST_LOAD_FAILED, "baseline")
    except Exception:
        return _internal_error()

    try:
        target_loaded = manifest.load_manifest(target)
    except ManifestLoadError:
        return _error_outcome(MANIFEST_LOAD_FAILED, "target")
    except Exception:
        return _internal_error()

    if baseline_loaded.manifest.schema_version in _UNSUPPORTED_COMPARABILITY_SCHEMAS:
        return _error_outcome(UNSUPPORTED_VERSION, "baseline")
    if target_loaded.manifest.schema_version in _UNSUPPORTED_COMPARABILITY_SCHEMAS:
        return _error_outcome(UNSUPPORTED_VERSION, "target")

    try:
        projection = bridge.compare_recorded_conditions(
            baseline_loaded,
            target_loaded,
            baseline_result_id=baseline_result_id,
            target_result_id=target_result_id,
        )
    except bridge.ComparabilityMappingError as error:
        try:
            code = error.code
            side = error.side
        except Exception:
            return _internal_error()
        if type(code) is not str or code not in MAPPING_CODES:
            return _internal_error()
        if side is not None and (type(side) is not str or side not in ERROR_SIDES):
            return _internal_error()
        return _error_outcome(MAPPING_FAILED, side, mapping_code=code)
    except Exception:
        return _internal_error()

    try:
        return _result_outcome(projection)
    except Exception:
        return _internal_error()


def invalid_arguments() -> CommandOutcome:
    """Return the fixed invalid-arguments document and text."""
    return _error_outcome(INVALID_ARGUMENTS, None)


# --------------------------------------------------------------- preflight

def _preflight_text(lines) -> str:
    return "\n".join(lines)


def _preflight_failure(exit_code: int, code: str, side=None) -> PreflightOutcome:
    if side is None:
        text = f"ERROR {code} (exit {exit_code})"
    else:
        text = f"ERROR {code} ({side}) (exit {exit_code})"
    return PreflightOutcome(exit_code, text)


def _load_for_preflight(path, side: str):
    try:
        return manifest.load_manifest(path)
    except ManifestLoadError:
        return _preflight_failure(EXIT_UNAVAILABLE, MANIFEST_LOAD_FAILED, side)
    except Exception:
        return _preflight_failure(EXIT_UNAVAILABLE, MANIFEST_LOAD_FAILED, side)


def _display_id(value) -> str:
    if type(value) is not str:
        return PREFLIGHT_ID_UNAVAILABLE
    if len(value) > 1024:
        return PREFLIGHT_ID_PLACEHOLDER
    return value


def _manifest_result_id(loaded, selector) -> str | None:
    matches = [result.id for result in loaded.manifest.results if result.id == selector]
    if len(matches) == 1:
        return matches[0]
    return None


def _recording_label(state) -> str:
    """The RFC's three recording states only.

    A present-but-ambiguous or present-but-conflicting value is still a
    recorded value, so it is ``present`` here; usability is reported
    separately in the fixed not-ready reason.
    """
    if state in (
        comparability.KNOWN,
        comparability.AMBIGUOUS,
        comparability.CONFLICTING,
    ):
        return "present"
    if state == comparability.UNKNOWN:
        return "recorded-unknown"
    if state == comparability.MISSING:
        return "missing"
    return "missing"


def _usability_label(state) -> str:
    return {
        comparability.KNOWN: "present",
        comparability.MISSING: "missing",
        comparability.UNKNOWN: "recorded-unknown",
        comparability.AMBIGUOUS: "ambiguous",
        comparability.CONFLICTING: "conflicting",
    }.get(state, "not-usable")


def _preflight_footer(*, pair: bool):
    lines = list(PREFLIGHT_FOOTER)
    if pair:
        lines.append(PREFLIGHT_READY_EXCEPTION)
    return lines


def _preflight_list_outcome(baseline, target) -> PreflightOutcome:
    lines = ["comparability preflight: list"]
    for side, loaded in (("baseline", baseline), ("target", target)):
        lines.append(f"{side} candidates:")
        try:
            bridge._require_unique_ids(loaded.manifest, side)
        except bridge.ComparabilityMappingError as error:
            code = error.code if error.code in MAPPING_CODES else bridge.INTERNAL_MAPPING_ERROR
            lines.append(f"  unavailable: {code}")
            continue
        except Exception:
            lines.append(f"  unavailable: {bridge.INTERNAL_MAPPING_ERROR}")
            continue
        results = list(loaded.manifest.results)
        if not results:
            lines.append("  (none)")
            continue
        for result in results[:PREFLIGHT_DISPLAY_CAP]:
            lines.append(f"  {_display_id(result.id)}")
            summary = bridge.side_recording_summary(loaded, result.id, side)
            if summary.get("code") is not None:
                lines.append(f"    unavailable: {summary['code']}")
                continue
            states = summary.get("states") or {}
            parts = [
                f"{name}={states.get(name, 'missing')}"
                for name in MANDATORY_ORDER
            ]
            attachment = (
                "mappable" if summary.get("attachment") else "not-mappable"
            )
            lines.append(
                "    " + ", ".join(parts) + f"; attachment={attachment}"
            )
        if len(results) > PREFLIGHT_DISPLAY_CAP:
            lines.append(f"  {len(results) - PREFLIGHT_DISPLAY_CAP} more not shown")
    lines.extend(_preflight_footer(pair=False))
    return PreflightOutcome(EXIT_COMPARABLE, _preflight_text(lines))


def _preflight_pair_outcome(baseline, target, baseline_result_id, target_result_id) -> PreflightOutcome:
    try:
        core = bridge.compare_recorded_conditions(
            baseline,
            target,
            baseline_result_id=baseline_result_id,
            target_result_id=target_result_id,
        )
    except bridge.ComparabilityMappingError as error:
        code = error.code
        side = error.side
        if type(code) is not str or code not in MAPPING_CODES:
            return _preflight_failure(EXIT_UNAVAILABLE, INTERNAL_ERROR)
        if side is not None and (type(side) is not str or side not in ERROR_SIDES):
            return _preflight_failure(EXIT_UNAVAILABLE, INTERNAL_ERROR)
        if code == bridge.RESULT_NOT_FOUND:
            return _preflight_failure(EXIT_UNAVAILABLE, "RESULT_NOT_FOUND", side)
        if code == bridge.INVALID_SELECTOR:
            return PreflightOutcome(EXIT_UNAVAILABLE, PREFLIGHT_INVALID_ARGUMENTS)
        lines = ["comparability preflight: not ready"]
        if side is None:
            lines.append(f"reason: {code}")
        else:
            lines.append(f"reason: {side}: {code}")
        lines.extend(_preflight_footer(pair=True))
        return PreflightOutcome(EXIT_NOT_COMPARABLE, _preflight_text(lines))
    except Exception:
        return _preflight_failure(EXIT_UNAVAILABLE, INTERNAL_ERROR)

    try:
        if not _valid_projection(core):
            return _preflight_failure(EXIT_UNAVAILABLE, INTERNAL_ERROR)
    except Exception:
        return _preflight_failure(EXIT_UNAVAILABLE, INTERNAL_ERROR)

    baseline_id = _manifest_result_id(baseline, baseline_result_id)
    target_id = _manifest_result_id(target, target_result_id)
    lines = ["comparability preflight: not ready"]
    lines.append(f"baseline result: {_display_id(baseline_id)}")
    lines.append(f"target result: {_display_id(target_id)}")

    if core["input_rejected"]:
        codes = ",".join(core["input_error_codes"])
        lines.append(f"reason: CORE_INPUT_REJECTED:{codes}")
        lines.extend(_preflight_footer(pair=True))
        return PreflightOutcome(EXIT_NOT_COMPARABLE, _preflight_text(lines))

    rows = {row["dimension"]: row for row in core["dimensions"]}
    mandatory = [
        rows[name]
        for name in DIMENSION_ORDER
        if name in rows and rows[name]["designation"] == "mandatory"
    ]
    if len(mandatory) != 5:
        return _preflight_failure(EXIT_UNAVAILABLE, INTERNAL_ERROR)

    recording = {}
    usability = {}
    for row in mandatory:
        name = row["dimension"]
        recording[name] = (
            _recording_label(row["baseline"]["state"]),
            _recording_label(row["target"]["state"]),
        )
        usability[name] = (
            _usability_label(row["baseline"]["state"]),
            _usability_label(row["target"]["state"]),
        )
        lines.append(
            f"  {name}: baseline={recording[name][0]}, target={recording[name][1]}"
        )

    def mappable(side: str) -> bool:
        for name in ("dataset_identity", "dataset_version"):
            row = rows.get(name)
            if row is None or row[side]["state"] != comparability.KNOWN:
                return False
        return True

    attachment = {"baseline": mappable("baseline"), "target": mappable("target")}
    lines.append(
        "dataset attachment: baseline=%s, target=%s"
        % (
            "mappable" if attachment["baseline"] else "not-mappable",
            "mappable" if attachment["target"] else "not-mappable",
        )
    )

    not_ready = [
        row for row in mandatory
        if row["baseline"]["state"] != comparability.KNOWN
        or row["target"]["state"] != comparability.KNOWN
    ]
    ready = not not_ready and attachment["baseline"] and attachment["target"]
    if ready:
        lines[0] = "comparability preflight: ready"
    else:
        if not_ready:
            row = not_ready[0]
            name = row["dimension"]
            lines.append(
                "reason: DIMENSION_NOT_USABLE:%s:baseline=%s,target=%s"
                % (name, usability[name][0], usability[name][1])
            )
        else:
            failed = [
                side for side in ("baseline", "target") if not attachment[side]
            ]
            lines.append("reason: ATTACHMENT_NOT_MAPPABLE:" + ",".join(failed))
    lines.extend(_preflight_footer(pair=True))
    return PreflightOutcome(
        EXIT_COMPARABLE if ready else EXIT_NOT_COMPARABLE, _preflight_text(lines)
    )


def preflight_paths(
    baseline: Path,
    target: Path,
    *,
    baseline_result_id: str | None,
    target_result_id: str | None,
) -> PreflightOutcome:
    """Read-only readiness preflight for recorded comparability.

    Zero selectors list both sides; exactly one is invalid arguments; both
    select a pair. Readiness uses the bridge's own mapping/bounds and the
    core's own validation/projection, never a copy. Text only; nothing is
    written.
    """
    if (baseline_result_id is None) != (target_result_id is None):
        return PreflightOutcome(EXIT_UNAVAILABLE, PREFLIGHT_INVALID_ARGUMENTS)

    baseline_loaded = _load_for_preflight(baseline, "baseline")
    if isinstance(baseline_loaded, PreflightOutcome):
        return baseline_loaded
    target_loaded = _load_for_preflight(target, "target")
    if isinstance(target_loaded, PreflightOutcome):
        return target_loaded

    if baseline_loaded.manifest.schema_version in _UNSUPPORTED_COMPARABILITY_SCHEMAS:
        return _preflight_failure(EXIT_UNAVAILABLE, UNSUPPORTED_VERSION, "baseline")
    if target_loaded.manifest.schema_version in _UNSUPPORTED_COMPARABILITY_SCHEMAS:
        return _preflight_failure(EXIT_UNAVAILABLE, UNSUPPORTED_VERSION, "target")

    if baseline_result_id is None:
        return _preflight_list_outcome(baseline_loaded, target_loaded)
    return _preflight_pair_outcome(
        baseline_loaded,
        target_loaded,
        baseline_result_id,
        target_result_id,
    )
