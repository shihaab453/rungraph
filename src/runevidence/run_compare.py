"""Read-only comparison of recorded parameters and result values for two runs.

This module does not check referenced artifact bytes, manifest authority or result
locators. Comparisons are purely between values recorded in one manifest.
"""

from __future__ import annotations

import json
import math
from fractions import Fraction
from typing import Any

from .histogram_display import value_display, value_to_json
from .models import Manifest


class ComparisonError(ValueError):
    """An ambiguous or unsupported recorded comparison, never a file error."""


def _run(manifest: Manifest, identifier: str):
    matches = [r for r in manifest.runs if r.id == identifier]
    if len(matches) != 1:
        kind = "not found" if not matches else "ambiguous (duplicate run IDs)"
        raise ComparisonError(f"run {identifier!r} {kind}")
    return matches[0]


def _results(manifest: Manifest, run_id: str) -> tuple[dict[str, Any], set[str]]:
    result_map: dict[str, Any] = {}
    text_names: set[str] = set()
    for r in manifest.results:
        if r.run_id != run_id:
            continue
        if r.name in result_map:
            raise ComparisonError(f"run {run_id!r} has multiple results named {r.name!r}; refusing ambiguous comparison")
        result_map[r.name] = value_to_json(r.value)
        if getattr(getattr(r, "locator", None), "kind", None) == "tensorboard-string":
            text_names.add(r.name)
    return result_map, text_names


def _numeric(v: object) -> bool:
    return type(v) in (int, float)


def _same(a: object, b: object) -> bool:
    """Preserve nested JSON type distinctions, especially bool versus number."""
    if _numeric(a) and _numeric(b):
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    return a == b


def _delta(base: int | float, target: int | float) -> int | float | None:
    """Return a representable difference, never a spurious zero from lossy casts.

    Integers are exact. Fractions preserve the exact values of finite floats,
    including the distinction between 2**53+1 and float(2**53).
    Omit an unrepresentable floating result rather than emitting infinity/zero.
    """
    if type(base) is int and type(target) is int:
        return target - base
    if any(type(v) is float and not math.isfinite(v) for v in (base, target)):
        return None
    difference = Fraction(target) - Fraction(base)
    try:
        rounded = float(difference)
    except OverflowError:
        return None
    if not math.isfinite(rounded) or (rounded == 0.0 and difference != 0):
        return None
    # Preserve the JSON numeric type of the existing public output: only two
    # integer operands produce an integer delta. Any float operand yields a
    # float delta, including exact integral and near-cancellation differences.
    return rounded


def _kind(value: Any) -> str | None:
    return value.get("kind") if isinstance(value, dict) else None


def _structured_flag(row: dict[str, Any], side: str, value: Any, *, allow: bool) -> None:
    if not allow:
        return
    kind = _kind(value)
    if kind in ("histogram", "image", "audio", "tensor"):
        row[f"{side}_{kind}"] = True


def _items(
    base: dict[str, Any],
    target: dict[str, Any],
    *,
    numeric_deltas: bool,
    base_text: set[str] | frozenset[str] = frozenset(),
    target_text: set[str] | frozenset[str] = frozenset(),
    allow_histograms: bool = False,
) -> list[dict[str, Any]]:
    items = []
    for name in sorted(base.keys() | target.keys()):
        if name not in base:
            row = {"name": name, "target": target[name], "status": "target_only"}
            if name in target_text:
                row["target_text"] = True
            _structured_flag(row, "target", target[name], allow=allow_histograms)
            items.append(row)
            continue
        if name not in target:
            row = {"name": name, "baseline": base[name], "status": "baseline_only"}
            if name in base_text:
                row["baseline_text"] = True
            _structured_flag(row, "baseline", base[name], allow=allow_histograms)
            items.append(row)
            continue
        b, t = base[name], target[name]
        row: dict[str, Any] = {
            "name": name,
            "baseline": b,
            "target": t,
            "status": "same" if _same(b, t) else "changed",
        }
        if name in base_text:
            row["baseline_text"] = True
        if name in target_text:
            row["target_text"] = True
        _structured_flag(row, "baseline", b, allow=allow_histograms)
        _structured_flag(row, "target", t, allow=allow_histograms)
        if numeric_deltas and _numeric(b) and _numeric(t):
            delta = _delta(b, t)
            if delta is not None:
                row["delta"] = delta
        items.append(row)
    return items


def compare_runs(manifest: Manifest, baseline_id: str, target_id: str, *, manifest_path: str) -> dict[str, Any]:
    """Compare recorded values only. Never open artifact paths or run checks."""
    if baseline_id == target_id:
        raise ComparisonError("baseline and target must be different run IDs")
    b = _run(manifest, baseline_id)
    t = _run(manifest, target_id)
    parameters = _items(b.params, t.params, numeric_deltas=False)
    baseline_results, baseline_text = _results(manifest, baseline_id)
    target_results, target_text = _results(manifest, target_id)
    results = _items(
        baseline_results,
        target_results,
        numeric_deltas=True,
        base_text=baseline_text,
        target_text=target_text,
        allow_histograms=True,
    )
    data = {
        "manifest": manifest_path,
        "baseline": {"id": b.id, "name": b.name},
        "target": {"id": t.id, "name": t.name},
        "parameters": parameters,
        "results": results,
        "summary": {
            "parameters_changed": sum(row["status"] != "same" for row in parameters),
            "results_changed": sum(row["status"] != "same" for row in results),
            "results_baseline_only": sum(row["status"] == "baseline_only" for row in results),
            "results_target_only": sum(row["status"] == "target_only" for row in results),
        },
        "note": "Recorded manifest values only. Artifact contents and result locators were not revalidated.",
    }
    try:
        json.dumps(data, allow_nan=False)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ComparisonError("comparison contains non-JSON/non-finite recorded values") from exc
    return data


def _visible(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True)


def _display_value(
    value: Any, *, text: bool, histogram: bool = False
) -> str:
    if histogram:
        shown = value_display(value)
        if shown is not None:
            return shown
    if text and isinstance(value, str) and len(value) > 120:
        return json.dumps(value[:120], ensure_ascii=True, allow_nan=False) + "..."
    return _visible(value)


def _side_flag(row: dict[str, Any], side: str) -> bool:
    return any(
        row.get(f"{side}_{kind}")
        for kind in ("histogram", "image", "audio", "tensor")
    )


def format_comparison(data: dict[str, Any]) -> str:
    """Deterministic, terminal-safe readable comparison."""
    # Unlike run IDs, a user-controlled filesystem path can contain newlines.
    manifest_path = data["manifest"]
    shown_path = manifest_path if manifest_path.isprintable() else _visible(manifest_path)
    lines = [
        f"Manifest: {shown_path}",
        f"Baseline: {data['baseline']['id']} ({_visible(data['baseline']['name'])})",
        f"Target: {data['target']['id']} ({_visible(data['target']['name'])})",
        "Recorded values only, not validated against artifact files.",
    ]
    for field, heading in (("parameters", "Parameters"), ("results", "Results")):
        items = data[field]
        lines.append(f"\n{heading} ({len(items)}):")
        for row in items:
            name = _visible(row["name"])
            status = row["status"]
            if status == "baseline_only":
                msg = (
                    "baseline only "
                    f"{_display_value(row['baseline'], text=row.get('baseline_text', False), histogram=_side_flag(row, 'baseline'))}"
                )
            elif status == "target_only":
                msg = (
                    "target only "
                    f"{_display_value(row['target'], text=row.get('target_text', False), histogram=_side_flag(row, 'target'))}"
                )
            else:
                msg = (
                    f"{_display_value(row['baseline'], text=row.get('baseline_text', False), histogram=_side_flag(row, 'baseline'))}"
                    " -> "
                    f"{_display_value(row['target'], text=row.get('target_text', False), histogram=_side_flag(row, 'target'))}"
                    f" [{status}]"
                )
                if "delta" in row:
                    delta = row["delta"]
                    readable_delta = f"{delta:+.12g}" if isinstance(delta, float) else f"{delta:+d}"
                    msg += f" (target minus baseline: {readable_delta})"
            lines.append(f"  {name}: {msg}")
    lines.append("\nNo artifact files were opened or checked. Run 'runevidence validate' separately before trusting recorded values.")
    return "\n".join(lines)
