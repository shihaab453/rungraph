"""Bounded recorded-condition comparison, synthetic integration prototype.

Default evaluate/evaluate_text return an evidence-minimizing public projection.
Private detailed functions are for controlled synthetic acceptance testing only.
prototype remains true for experimental use. integrated_into_rungraph means
application-workflow integration, not physical source-file presence. It stays
false for core-only installation. EXPERIMENTAL_CORE_ONLY describes capability
scope in both external rehearsal and an explicitly authorized installation;
it does not report whether installation has occurred.
No production metadata, privacy, scientific validity or runtime qualification.
"""
from __future__ import annotations

import json
import math
import re
from fractions import Fraction

FORMAT_IN = "pc26-comparability-input-v2"
LEGACY_FORMAT_IN = "pc25-comparability-input-v1"
FORMAT_OUT = "pc26-comparability-result-v2"

COMPARABLE = "COMPARABLE_RECORDED_CONDITIONS"
NOT_COMPARABLE = "NOT_COMPARABLE_RECORDED_CONDITIONS"
INSUFFICIENT = "INSUFFICIENT_EVIDENCE"

KNOWN, MISSING, UNKNOWN, CONFLICTING, AMBIGUOUS = "KNOWN", "MISSING", "UNKNOWN", "CONFLICTING", "AMBIGUOUS"
MATCH, DIFFERENT, INSUFF = "MATCH", "DIFFERENT", "INSUFFICIENT"

# Same token rule as RunEvidence models.py:51 (copied, not imported). fullmatch
# avoids Python's "$ matches before a trailing newline" behaviour.
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")

DERIVED, PROPOSED = "existing_derived", "proposed_extension"
BASES = (DERIVED, PROPOSED)
SIDES = ("baseline", "target")

# Closed v1 dimension table. "current_model_support" summarises the PC25 Phase 1
# inventory (inspection/capability_inventory_v1.md); it is descriptive only.
DIMENSIONS = {
    "dataset_identity": {"default": "mandatory", "allowed_basis": (DERIVED, PROPOSED),
                         "current_model_support": "derivable_with_assumptions: Artifact.kind=='dataset' + input_to edge (models.py:138-142,196)"},
    "dataset_version": {"default": "mandatory", "allowed_basis": (DERIVED, PROPOSED),
                        "current_model_support": "derivable_with_assumptions: Artifact.sha256 as content version (models.py:129); no version label field"},
    "evaluation_split": {"default": "mandatory", "allowed_basis": (PROPOSED,),
                         "current_model_support": "not_represented"},
    "metric_definition": {"default": "mandatory", "allowed_basis": (PROPOSED,),
                          "current_model_support": "not_represented (Result.name is a display label, models.py:164)"},
    "evaluation_protocol": {"default": "mandatory", "allowed_basis": (PROPOSED,),
                            "current_model_support": "not_represented"},
    "preprocessing": {"default": "optional", "allowed_basis": (DERIVED, PROPOSED),
                      "current_model_support": "partially_derivable: derived_from chain (models.py:199)"},
    "source_revision": {"default": "optional", "allowed_basis": (PROPOSED,),
                        "current_model_support": "not_represented"},
    "model_checkpoint": {"default": "optional", "allowed_basis": (DERIVED, PROPOSED),
                         "current_model_support": "derivable_with_assumptions: Artifact.kind=='checkpoint' + sha256 (models.py:129,138-142)"},
    "environment": {"default": "optional", "allowed_basis": (PROPOSED,),
                    "current_model_support": "not_represented"},
}
KNOWN_DIMENSIONS = frozenset(DIMENSIONS)
TOP_KEYS = frozenset(("format", "policy", "baseline", "target"))
RECORD_KEYS = frozenset(("run_id", "result_id", "metric_name", "dimensions"))
OBS_KEYS = frozenset(("value", "field", "basis"))
NAME_ONLY_KEYS = frozenset(("name", "display_name", "label"))
MAX_DEPTH = 16
MAX_INPUT_BYTES = 65536

LIMITATIONS = (
    "Prototype only: experimental core, not integrated into RunGraph/RunEvidence application workflows; decision labels are not a production API.",
    "Recorded conditions only: no artifact bytes, files, URLs or result locators are read or revalidated.",
    "COMPARABLE_RECORDED_CONDITIONS is not proof of causal equivalence, reproducibility, equal statistical uncertainty or correct recording.",
    "Dimensions with basis proposed_extension are not fields of the current RunEvidence manifest model (schema 0.1).",
    "existing_derived values rely on conventions the current model does not enforce: Artifact.kind labels, complete input_to/derived_from edges, recorded non-volatile sha256.",
    "Unrecognised dimensions retain bounded synthetic observations and never support equality or difference.",
    "Raw metadata output is synthetic-only; real metadata needs a separate privacy review.",
)


# ---------------------------------------------------------------- parsing

class _DuplicateKey(Exception):
    pass


class _NonFinite(Exception):
    pass


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise _DuplicateKey(key)
        out[key] = value
    return out


def _reject_constant(name):
    raise _NonFinite(name)


def _finite_float(text):
    number = float(text)
    if not math.isfinite(number):
        raise _NonFinite(text)
    return number


def _err(code, path, message):
    return {"code": code, "path": path, "message": code}


def _ptr(path, token):
    return path + "/" + str(token).replace("~", "~0").replace("/", "~1")


def _short(value):
    text = json.dumps(value, ensure_ascii=True)
    return text if len(text) <= 80 else text[:77] + "..."


def _parse_unbounded_text(text):
    """Return (doc, errors). Duplicate keys and non-finite numbers are errors."""
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=_reject_constant,
                          parse_float=_finite_float), []
    except _DuplicateKey as exc:
        return None, [_err("DUPLICATE_KEY", "", f"duplicate object key {_short(exc.args[0])}")]
    except _NonFinite as exc:
        return None, [_err("NONFINITE_NUMBER", "", f"non-finite number {_short(exc.args[0])}")]
    except (ValueError, RecursionError) as exc:
        return None, [_err("INVALID_JSON", "", type(exc).__name__)]


# ------------------------------------------------------------- validation

def _check_json_value(value, path, errors, depth=0):
    if depth > MAX_DEPTH:
        errors.append(_err("TYPE", path, f"nesting deeper than {MAX_DEPTH}"))
        return
    if value is None or type(value) in (bool, int, str):
        return
    if type(value) is float:
        if not math.isfinite(value):
            errors.append(_err("NONFINITE_NUMBER", path, "non-finite number"))
        return
    if type(value) is list:
        for i, item in enumerate(value):
            _check_json_value(item, _ptr(path, i), errors, depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                errors.append(_err("NOT_JSON_VALUE", path, "object key is not a string"))
                continue
            _check_json_value(item, _ptr(path, key), errors, depth + 1)
        return
    errors.append(_err("NOT_JSON_VALUE", path, f"unsupported type {type(value).__name__}"))


def _keys(obj, path, allowed, required, errors):
    for key in obj:
        if key not in allowed:
            errors.append(_err("UNKNOWN_KEY", _ptr(path, key), f"unknown key {_short(key)}"))
    for key in sorted(required):
        if key not in obj:
            errors.append(_err("MISSING_KEY", _ptr(path, key), f"missing key {_short(key)}"))


def _validate_policy(policy, errors):
    path = "/policy"
    if type(policy) is not dict:
        errors.append(_err("TYPE", path, "policy must be an object"))
        return
    _keys(policy, path, {"mandatory", "optional"}, {"mandatory", "optional"}, errors)
    seen = {}
    for name in ("mandatory", "optional"):
        items = policy.get(name)
        if name not in policy:
            continue
        if type(items) is not list:
            errors.append(_err("TYPE", _ptr(path, name), "must be a list"))
            continue
        local = set()
        for i, dim in enumerate(items):
            p = _ptr(_ptr(path, name), i)
            if type(dim) is not str:
                errors.append(_err("TYPE", p, "dimension name must be a string"))
                continue
            if dim not in KNOWN_DIMENSIONS:
                errors.append(_err("POLICY_UNKNOWN_DIMENSION", p, f"unknown dimension {_short(dim)}"))
                continue
            if dim in local:
                errors.append(_err("POLICY_DUPLICATE", p, f"{_short(dim)} listed twice"))
                continue
            local.add(dim)
            if dim in seen and seen[dim] != name:
                errors.append(_err("POLICY_OVERLAP", p, f"{_short(dim)} is both mandatory and optional"))
            seen.setdefault(dim, name)
        if name == "mandatory" and not items:
            errors.append(_err("POLICY_EMPTY_MANDATORY", _ptr(path, name), "mandatory list is empty"))
    if type(policy.get("mandatory")) is list and type(policy.get("optional")) is list:
        if set(seen) != KNOWN_DIMENSIONS:
            missing = sorted(KNOWN_DIMENSIONS - set(seen))
            errors.append(_err("POLICY_INCOMPLETE", path, f"dimensions not designated: {missing}"))


def _validate_record(rec, side, errors):
    path = "/" + side
    if type(rec) is not dict:
        errors.append(_err("TYPE", path, "record must be an object"))
        return
    _keys(rec, path, RECORD_KEYS, {"dimensions"}, errors)
    run_id = rec.get("run_id")
    if run_id is not None and (type(run_id) is not str or not _ID_RE.fullmatch(run_id)):
        errors.append(_err("BAD_RUN_ID", _ptr(path, "run_id"), "run_id must match the RunEvidence ID pattern"))
    for key in ("result_id", "metric_name"):
        if rec.get(key) is not None and type(rec.get(key)) is not str:
            errors.append(_err("TYPE", _ptr(path, key), f"{key} must be a string or null"))
    if "dimensions" not in rec:
        return
    dims = rec["dimensions"]
    dpath = _ptr(path, "dimensions")
    if type(dims) is not dict:
        errors.append(_err("TYPE", dpath, "dimensions must be an object"))
        return
    for dim, ent in dims.items():
        epath = _ptr(dpath, dim)
        if dim == "":
            errors.append(_err("TYPE", epath, "dimension name must be non-empty"))
            continue
        if type(ent) is not dict:
            errors.append(_err("TYPE", epath, "dimension entry must be an object"))
            continue
        _keys(ent, epath, {"observations"}, {"observations"}, errors)
        observations = ent.get("observations")
        if "observations" in ent and type(observations) is not list:
            errors.append(_err("TYPE", _ptr(epath, "observations"), "observations must be a list"))
            continue
        for i, ob in enumerate(observations or ()):
            opath = _ptr(_ptr(epath, "observations"), i)
            if type(ob) is not dict:
                errors.append(_err("TYPE", opath, "observation must be an object"))
                continue
            _keys(ob, opath, OBS_KEYS, OBS_KEYS, errors)
            if "field" in ob and (type(ob["field"]) is not str or not ob["field"]):
                errors.append(_err("BAD_FIELD", _ptr(opath, "field"), "field must be a non-empty string"))
            if "basis" in ob:
                basis = ob["basis"]
                if basis not in BASES:
                    errors.append(_err("BAD_BASIS", _ptr(opath, "basis"), f"unknown basis {_short(basis)}"))
                elif dim in DIMENSIONS and basis not in DIMENSIONS[dim]["allowed_basis"]:
                    errors.append(_err("BASIS_EXCEEDS_CURRENT_MODEL", _ptr(opath, "basis"),
                                       f"{dim} is not derivable from the current model; only {list(DIMENSIONS[dim]['allowed_basis'])} allowed"))


def _validate_structure(doc):
    errors = []
    try:
        _check_json_value(doc, "", errors)
    except RecursionError:
        errors.append(_err("TYPE", "", "nesting too deep"))
    if errors:
        return errors
    if type(doc) is not dict:
        return [_err("TYPE", "", "top level must be an object")]
    _keys(doc, "", TOP_KEYS, {"format", "baseline", "target"}, errors)
    if "format" in doc and doc["format"] not in (FORMAT_IN, LEGACY_FORMAT_IN):
        errors.append(_err("BAD_FORMAT", "/format", f"expected {FORMAT_IN}"))
    if "policy" in doc:
        _validate_policy(doc["policy"], errors)
    for side in SIDES:
        if side in doc:
            _validate_record(doc[side], side, errors)
    ids = [doc.get(s).get("run_id") if type(doc.get(s)) is dict else None for s in SIDES]
    if type(ids[0]) is str and ids[0] == ids[1]:
        errors.append(_err("SAME_RUN_ID", "/target/run_id", "baseline and target must be different run IDs"))
    return errors


# ------------------------------------------------------------- comparison

def _canon(value):
    """Canonical form: key order irrelevant, list order significant, bool != number,
    int/float equal when their exact values are equal."""
    if value is None:
        return ("null",)
    if type(value) is bool:
        return ("bool", value)
    if type(value) in (int, float):
        return ("num", Fraction(value))
    if type(value) is str:
        return ("str", value)
    if type(value) is list:
        return ("list", tuple(_canon(v) for v in value))
    return ("dict", tuple(sorted((k, _canon(v)) for k, v in value.items())))


def _text(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def _ambiguous(dim, value):
    if type(value) is str and value.strip() == "":
        return True
    if type(value) in (list, dict) and not value:
        return True
    if dim == "metric_definition":
        if type(value) is not dict:
            return True
        if set(value) <= NAME_ONLY_KEYS:
            return True
    return False


def _side_state(dim, entry):
    observations = [] if entry is None else list(entry["observations"])
    shown = sorted(({"value": o["value"], "field": o["field"], "basis": o["basis"]} for o in observations),
                   key=lambda o: (_text(o["value"]), o["field"], o["basis"]))
    known = [o["value"] for o in observations if o["value"] is not None]
    state = {"observations": shown, "unknown_observations": len(observations) - len(known),
             "values": [], "value": None}
    if not observations:
        state["state"] = MISSING
        return state
    if not known:
        state["state"] = UNKNOWN
        return state
    groups = {}
    for v in known:
        groups.setdefault(_canon(v), []).append(v)
    reps = sorted((min(vs, key=_text) for vs in groups.values()), key=_text)
    state["values"] = reps
    if len(groups) > 1:
        state["state"] = CONFLICTING
        return state
    state["value"] = reps[0]
    state["state"] = AMBIGUOUS if _ambiguous(dim, reps[0]) else KNOWN
    return state


def _pair_status(dim, b, t, ctx):
    if b["state"] == KNOWN and t["state"] == KNOWN:
        bv, tv = b["value"], t["value"]
        if _canon(bv) == _canon(tv):
            return MATCH, ["values:EQUAL"]
        if type(bv) is str and type(tv) is str and bv.strip().casefold() == tv.strip().casefold():
            return INSUFF, ["values:CASE_OR_WHITESPACE_ONLY_DIFFERENCE"]
        return DIFFERENT, ["values:DIFFERENT"]
    reasons = sorted(f"{side}:{s['state']}" for side, s in (("baseline", b), ("target", t)) if s["state"] != KNOWN)
    return INSUFF, reasons


def _dimension_order(b_dims, t_dims):
    return sorted(KNOWN_DIMENSIONS)


def _unrecognized(b_dims, t_dims):
    names = sorted((set(b_dims) | set(t_dims)) - KNOWN_DIMENSIONS)
    rows = []
    for name in names:
        row = {"name": name, "present_on": [], "status": "UNRECOGNIZED",
               "reasons": ["dimension:UNRECOGNIZED"]}
        for side, dims in (("baseline", b_dims), ("target", t_dims)):
            evidence = {"present": name in dims}
            if name in dims:
                row["present_on"].append(side)
                observations = sorted(dims[name]["observations"],
                    key=lambda o: (_text(o["value"]), o["field"], o["basis"]))
                # JSON round-trip copies only already validated bounded JSON.
                evidence["entry"] = json.loads(_text({"observations": observations}))
            row[side] = evidence
        rows.append(row)
    return rows


def _side_out(state):
    return {"present": state["present"], "state": state["state"], "values": state["values"], "observations": state["observations"],
            "unknown_observations": state["unknown_observations"]}


def _identity(rec):
    summary = {k: rec.get(k) for k in ("run_id", "result_id", "metric_name")}
    summary["present_fields"] = sorted(k for k in summary if k in rec)
    return summary


def _malformed(errors):
    return {
        "format": FORMAT_OUT, "prototype": True, "integrated_into_rungraph": False,
        "decision": INSUFFICIENT, "decision_reasons": ["MALFORMED_INPUT"],
        "evidence_status": "REJECTED_INPUT", "input_valid": False,
        "input_errors": sorted(errors, key=lambda e: (e["path"], e["code"], e["message"])),
        "baseline": None, "target": None, "policy": None, "dimensions": [],
        "unrecognized_dimensions": [], "missing_information": [],
        "relies_on_proposed_extensions": [], "relies_on_derived_assumptions": [],
        "limitations": list(LIMITATIONS),
    }


def _evaluate_detailed(doc):
    """Deterministically evaluate one synthetic comparability-input document."""
    errors = validate(doc)
    if errors:
        return _malformed(errors)
    if "policy" in doc:
        mandatory = set(doc["policy"]["mandatory"])
        policy_source = "input"
    else:
        mandatory = {d for d, spec in DIMENSIONS.items() if spec["default"] == "mandatory"}
        policy_source = "default"
    recs = {s: doc[s] for s in SIDES}
    b_dims, t_dims = recs["baseline"]["dimensions"], recs["target"]["dimensions"]
    ctx = {"metric_name": {s: recs[s].get("metric_name") for s in SIDES}}

    rows, missing_info = [], []
    reasons = set()
    proposed, derived = set(), set()
    mand_diff = mand_insuff = False
    for dim in _dimension_order(b_dims, t_dims):
        b = _side_state(dim, b_dims.get(dim))
        t = _side_state(dim, t_dims.get(dim))
        b["present"] = dim in b_dims
        t["present"] = dim in t_dims
        status, row_reasons = _pair_status(dim, b, t, ctx)
        designation = "mandatory" if dim in mandatory else "optional"
        rows.append({"dimension": dim, "designation": designation, "status": status, "reasons": row_reasons,
                     "current_model_support": DIMENSIONS[dim]["current_model_support"],
                     "baseline": _side_out(b), "target": _side_out(t)})
        for side, s in (("baseline", b), ("target", t)):
            if s["state"] != KNOWN:
                missing_info.append({"subject": dim, "side": side, "state": s["state"]})
            for o in s["observations"]:
                (proposed if o["basis"] == PROPOSED else derived).add(dim)
        prefix = "MANDATORY" if designation == "mandatory" else "OPTIONAL"
        if status == DIFFERENT:
            reasons.add(f"{prefix}_DIMENSION_DIFFERENT:{dim}")
            mand_diff = mand_diff or designation == "mandatory"
        elif status == INSUFF:
            reasons.add(f"{prefix}_DIMENSION_INSUFFICIENT:{dim}")
            mand_insuff = mand_insuff or designation == "mandatory"

    run_missing = False
    for side in SIDES:
        if recs[side].get("run_id") is None:
            run_missing = True
            reasons.add(f"RUN_IDENTITY_MISSING:{side}")
            missing_info.append({"subject": "run_id", "side": side, "state": MISSING})
        if recs[side].get("metric_name") is None:
            reasons.add(f"METRIC_NAME_MISSING:{side}")
            missing_info.append({"subject": "metric_name", "side": side, "state": MISSING})
    names = ctx["metric_name"]
    if names["baseline"] is not None and names["target"] is not None and names["baseline"] != names["target"]:
        reasons.add("METRIC_NAME_DIFFERS")

    unrecognized = _unrecognized(b_dims, t_dims)
    for u in unrecognized:
        reasons.add(f"UNRECOGNIZED_DIMENSION_PRESENT:{u['name']}")

    if mand_diff:
        decision = NOT_COMPARABLE
    elif run_missing or mand_insuff or unrecognized:
        decision = INSUFFICIENT
    else:
        decision = COMPARABLE
        reasons.add("ALL_MANDATORY_DIMENSIONS_MATCH")

    return {
        "format": FORMAT_OUT, "prototype": True, "integrated_into_rungraph": False,
        "decision": decision, "decision_reasons": sorted(reasons),
        "evidence_status": "EVALUATED", "input_valid": True, "input_errors": [],
        "baseline": _identity(recs["baseline"]), "target": _identity(recs["target"]),
        "policy": {"mandatory": sorted(mandatory), "optional": sorted(KNOWN_DIMENSIONS - mandatory),
                   "source": policy_source},
        "dimensions": rows,
        "unrecognized_dimensions": unrecognized,
        "missing_information": sorted(missing_info, key=lambda m: (m["subject"], m["side"], m["state"])),
        "relies_on_proposed_extensions": sorted(proposed),
        "relies_on_derived_assumptions": sorted(derived),
        "limitations": list(LIMITATIONS),
    }


def _evaluate_text_detailed(text):
    doc, errors = parse_text(text)
    if errors:
        return _malformed(errors)
    return _evaluate_detailed(doc)


def _render_detailed(result):
    return json.dumps(result, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n"



# Bounds run before structural validation, equality or JSON serialization.
class _BoundViolation(Exception):
    pass

def _bounded(doc):
    nodes = 0
    def visit(value, depth):
        nonlocal nodes
        nodes += 1
        if nodes > 4096:
            raise _BoundViolation("NODE_LIMIT")
        if depth > MAX_DEPTH:
            raise _BoundViolation("DEPTH_LIMIT")
        kind = type(value)
        if value is None or kind is bool:
            return
        if kind is int:
            if abs(value) > 9007199254740991:
                raise _BoundViolation("NUMBER_LIMIT")
            return
        if kind is float:
            if not math.isfinite(value):
                raise _BoundViolation("NONFINITE_NUMBER")
            if abs(value) > 1e100:
                raise _BoundViolation("NUMBER_LIMIT")
            return
        if kind is str:
            if len(value) > 1024:
                raise _BoundViolation("STRING_LIMIT")
            try:
                value.encode("utf-8")
            except UnicodeEncodeError:
                raise _BoundViolation("UTF8") from None
            return
        if kind not in (dict, list):
            raise _BoundViolation("NOT_JSON_VALUE")
        if len(value) > 128:
            raise _BoundViolation("CONTAINER_LIMIT")
        if kind is dict:
            if any(type(k) is not str for k in value):
                raise _BoundViolation("NOT_JSON_VALUE")
            for key in sorted(value):
                # Keys obey string limits but are not value nodes.
                if len(key) > 1024:
                    raise _BoundViolation("STRING_LIMIT")
                try:
                    key.encode("utf-8")
                except UnicodeEncodeError:
                    raise _BoundViolation("UTF8") from None
                visit(value[key], depth + 1)
        else:
            for item in value:
                visit(item, depth + 1)
    try:
        visit(doc, 0)
        if len(_text(doc).encode("ascii")) > MAX_INPUT_BYTES:
            raise _BoundViolation("INPUT_SIZE")
        if type(doc) is dict:
            for side in SIDES:
                rec = doc.get(side)
                dims = rec.get("dimensions") if type(rec) is dict else None
                if type(dims) is not dict:
                    continue
                if len(dims) > 32:
                    raise _BoundViolation("DIMENSION_LIMIT")
                for name in sorted(dims):
                    entry = dims[name]
                    obs = entry.get("observations") if type(entry) is dict else None
                    if type(obs) is list and len(obs) > 16:
                        raise _BoundViolation("OBSERVATION_LIMIT")
    except _BoundViolation as exc:
        code = exc.args[0]
        return [_err(code, "", code)]
    return []

def validate(doc):
    return _bounded(doc) or _validate_structure(doc)

def parse_text(text):
    if type(text) is not str:
        return None, [_err("NOT_JSON_VALUE", "", "NOT_JSON_VALUE")]
    # Codepoint count first avoids allocating an unbounded UTF-8 buffer.
    if len(text) > MAX_INPUT_BYTES:
        return None, [_err("INPUT_SIZE", "", "INPUT_SIZE")]
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return None, [_err("UTF8", "", "UTF8")]
    if size > MAX_INPUT_BYTES:
        return None, [_err("INPUT_SIZE", "", "INPUT_SIZE")]
    return _parse_unbounded_text(text)


# Closed public vocabulary. Only these literals can leave the public boundary.
PUBLIC_FORMAT = "pc27-comparability-public-v1"
PUBLIC_REASON_CODES = frozenset(
    ["MALFORMED_INPUT", "ALL_MANDATORY_DIMENSIONS_MATCH", "METRIC_NAME_DIFFERS",
     "UNRECOGNIZED_DIMENSION_PRESENT"]
    + [prefix + ":" + dim for prefix in (
        "MANDATORY_DIMENSION_DIFFERENT", "MANDATORY_DIMENSION_INSUFFICIENT",
        "OPTIONAL_DIMENSION_DIFFERENT", "OPTIONAL_DIMENSION_INSUFFICIENT")
       for dim in sorted(KNOWN_DIMENSIONS)]
    + [prefix + ":" + side for prefix in (
        "RUN_IDENTITY_MISSING", "METRIC_NAME_MISSING") for side in SIDES]
)
PUBLIC_ERROR_CODES = frozenset((
    "NOT_JSON_VALUE", "NONFINITE_NUMBER", "NUMBER_LIMIT", "STRING_LIMIT", "UTF8",
    "CONTAINER_LIMIT", "NODE_LIMIT", "DEPTH_LIMIT", "INPUT_SIZE", "DIMENSION_LIMIT",
    "OBSERVATION_LIMIT", "INVALID_JSON", "DUPLICATE_KEY", "TYPE", "UNKNOWN_KEY",
    "MISSING_KEY", "POLICY_UNKNOWN_DIMENSION", "POLICY_DUPLICATE", "POLICY_OVERLAP",
    "POLICY_EMPTY_MANDATORY", "POLICY_INCOMPLETE", "BAD_RUN_ID", "BAD_FIELD",
    "BAD_BASIS", "BASIS_EXCEEDS_CURRENT_MODEL", "BAD_FORMAT", "SAME_RUN_ID",
    "INTERNAL_EVALUATION_ERROR",
))
__all__ = ["evaluate", "evaluate_text", "render"]


def _public_side(side, unknown=False):
    observations = (side.get("entry", {}).get("observations", []) if unknown
                    else side["observations"])
    result = {"present": side["present"], "observation_count": len(observations),
              "null_observation_count": sum(o["value"] is None for o in observations)}
    if not unknown:
        result["state"] = side["state"]
    return result


def _project_public(detail):
    codes = set()
    for reason in detail["decision_reasons"]:
        if reason.startswith("UNRECOGNIZED_DIMENSION_PRESENT:"):
            codes.add("UNRECOGNIZED_DIMENSION_PRESENT")
        elif reason in PUBLIC_REASON_CODES:
            codes.add(reason)
        else:
            raise ValueError("INTERNAL_PUBLIC_REASON")
    errors = sorted({error["code"] for error in detail["input_errors"]})
    if not set(errors) <= PUBLIC_ERROR_CODES:
        raise ValueError("INTERNAL_PUBLIC_ERROR")
    dimensions = []
    for row in detail["dimensions"]:
        dimensions.append({"dimension": row["dimension"], "designation": row["designation"],
                           "status": row["status"],
                           "baseline": _public_side(row["baseline"]),
                           "target": _public_side(row["target"])})
    unknown = []
    for index, row in enumerate(detail["unrecognized_dimensions"], 1):
        unknown.append({"reference": "unknown_%04d" % index, "status": "UNRECOGNIZED",
                        "present_on": list(row["present_on"]),
                        "baseline": _public_side(row["baseline"], True),
                        "target": _public_side(row["target"], True)})
    return {"format": PUBLIC_FORMAT, "prototype": True, "integrated_into_rungraph": False,
            "integration_status": "EXPERIMENTAL_CORE_ONLY", "decision": detail["decision"],
            "reason_codes": sorted(codes), "dimensions": dimensions,
            "unknown_dimension_count": len(unknown), "unknown_dimensions": unknown,
            "input_rejected": not detail["input_valid"], "input_error_codes": errors}


def _public_evaluation(call, value):
    try:
        return _project_public(call(value))
    except Exception:
        # Never expose input-derived exception text, paths, objects or tracebacks.
        return _project_public(_malformed([
            {"code": "INTERNAL_EVALUATION_ERROR", "path": "", "message": "INTERNAL_EVALUATION_ERROR"}
        ]))


def evaluate(document):
    """Return only the proposed public projection for bounded JSON input.

    This interface is synthetic-only pending a separate real-metadata review.
    Unexpected evaluation exceptions become a fixed rejected-input result.
    """
    return _public_evaluation(_evaluate_detailed, document)


def evaluate_text(text):
    """Parse bounded JSON text and return only the proposed public projection."""
    return _public_evaluation(_evaluate_text_detailed, text)


def _public_shape(result):
    # A strict render boundary prevents accidental rendering of detailed evidence.
    if type(result) is not dict or set(result) != {
        "format", "prototype", "integrated_into_rungraph", "integration_status", "decision",
        "reason_codes", "dimensions", "unknown_dimension_count", "unknown_dimensions",
        "input_rejected", "input_error_codes",
    }:
        return False
    if result["format"] != PUBLIC_FORMAT or result["prototype"] is not True:
        return False
    if result["integrated_into_rungraph"] is not False or result["integration_status"] != "EXPERIMENTAL_CORE_ONLY":
        return False
    if type(result["decision"]) is not str or result["decision"] not in (COMPARABLE, NOT_COMPARABLE, INSUFFICIENT):
        return False
    for key, allowed in (("reason_codes", PUBLIC_REASON_CODES), ("input_error_codes", PUBLIC_ERROR_CODES)):
        values = result[key]
        if type(values) is not list or any(type(v) is not str or v not in allowed for v in values):
            return False
        if values != sorted(set(values)):
            return False
    if type(result["input_rejected"]) is not bool:
        return False
    if type(result["unknown_dimension_count"]) is not int or not 0 <= result["unknown_dimension_count"] <= 64:
        return False
    dimensions, unknown = result["dimensions"], result["unknown_dimensions"]
    if type(dimensions) is not list or type(unknown) is not list:
        return False
    if len(unknown) != result["unknown_dimension_count"] or len(dimensions) > 9:
        return False
    for is_unknown, rows in ((False, dimensions), (True, unknown)):
        for index, row in enumerate(rows, 1):
            keys = {"reference", "status", "present_on", "baseline", "target"} if is_unknown else {
                "dimension", "designation", "status", "baseline", "target"}
            if type(row) is not dict or set(row) != keys:
                return False
            if is_unknown:
                if row["reference"] != "unknown_%04d" % index or row["status"] != "UNRECOGNIZED":
                    return False
                if row["present_on"] not in (["baseline"], ["target"], ["baseline", "target"]):
                    return False
            elif (type(row["dimension"]) is not str or row["dimension"] not in KNOWN_DIMENSIONS
                  or row["designation"] not in ("mandatory", "optional")
                  or row["status"] not in (MATCH, DIFFERENT, INSUFF)):
                return False
            for side in SIDES:
                item = row[side]
                keys = {"present", "observation_count", "null_observation_count"}
                if not is_unknown:
                    keys.add("state")
                if type(item) is not dict or set(item) != keys or type(item["present"]) is not bool:
                    return False
                if any(type(item[k]) is not int or not 0 <= item[k] <= 16
                       for k in ("observation_count", "null_observation_count")):
                    return False
                if item["null_observation_count"] > item["observation_count"]:
                    return False
                if not is_unknown and item["state"] not in (KNOWN, MISSING, UNKNOWN, CONFLICTING, AMBIGUOUS):
                    return False
    return True


def render(result):
    """Render a public result only; reject raw detailed evidence with fixed text."""
    try:
        valid = _bounded(result) == [] and _public_shape(result)
    except Exception:
        valid = False
    if not valid:
        raise ValueError("INVALID_PUBLIC_RESULT")
    return json.dumps(result, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n"
