"""Per-metric counts and distribution statistics over recorded result rows.

Shared by the single-manifest ``runevidence stats`` route and the workspace
``rungraph stats`` route. Rows are plain dictionaries with ``name``, ``value``,
``numeric``, ``member`` and ``run`` fields. Calculations use exact rational
arithmetic for every finite recorded value, so values at the float limits do
not overflow or lose small differences.
"""

from __future__ import annotations

import math
import sys
from decimal import Decimal, localcontext
from fractions import Fraction
from typing import Any, Optional

from pydantic import BaseModel


def json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def _json_number(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _safe_mean(values: list[Any]) -> Any:
    return _json_number(float(sum(Fraction(value) for value in values) / len(values)))


def _safe_median(values: list[Any]) -> Any:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return _json_number(
        float((Fraction(ordered[middle - 1]) + Fraction(ordered[middle])) / 2)
    )


def _safe_pstdev(values: list[Any]) -> Any:
    exact = [Fraction(value) for value in values]
    mean = sum(exact) / len(exact)
    variance = sum((item - mean) ** 2 for item in exact) / len(exact)
    if variance == 0:
        return 0.0
    with localcontext() as context:
        context.prec = 80
        root = (
            Decimal(variance.numerator) / Decimal(variance.denominator)
        ).sqrt()
    if not root.is_finite():
        value = sys.float_info.max
    else:
        try:
            value = float(root)
        except OverflowError:
            value = sys.float_info.max
    return _json_number(value)


def stats_from_rows(
    rows: list[dict[str, Any]], metric: Optional[str] = None
) -> list[dict[str, Any]]:
    """Summarize rows already filtered to the selected metric, if any."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["name"], []).append(row)
    metrics: list[dict[str, Any]] = []
    for name in sorted(grouped):
        items = grouped[name]
        numeric_values = [row["value"] for row in items if row["numeric"]]
        cells: dict[tuple[str, str], int] = {}
        for row in items:
            key = (row["member"], row["run"])
            cells[key] = cells.get(key, 0) + 1
        entry: dict[str, Any] = {
            "name": name,
            "rows": len(items),
            "numeric": len(numeric_values),
            "missing": sum(1 for row in items if row["value"] is None),
            "non_numeric": sum(
                1 for row in items if row["value"] is not None and not row["numeric"]
            ),
            "ambiguous": sum(1 for count in cells.values() if count > 1),
            "min": None,
            "max": None,
            "mean": None,
            "median": None,
            "stddev": None,
        }
        if numeric_values:
            entry["min"] = min(numeric_values)
            entry["max"] = max(numeric_values)
            entry["mean"] = _safe_mean(numeric_values)
            entry["median"] = _safe_median(numeric_values)
            entry["stddev"] = _safe_pstdev(numeric_values)
        metrics.append(entry)
    return metrics
