"""Compact human rendering for schema-0.7 histogram values."""

from __future__ import annotations

import json
from typing import Any

from .models import HistogramValue


def histogram_display(value: Any) -> str | None:
    """Return ``histogram(<k> buckets, num=<num>)`` or None."""
    if isinstance(value, HistogramValue):
        data = value.model_dump(mode="json")
    elif isinstance(value, dict) and value.get("kind") == "histogram":
        data = value
    else:
        return None
    bucket = data.get("bucket")
    num = data.get("num")
    if not isinstance(bucket, list):
        return None
    return f"histogram({len(bucket)} buckets, num={json.dumps(num)})"


def value_to_json(value: Any) -> Any:
    """Return a JSON-compatible recorded value."""
    if isinstance(value, HistogramValue):
        return value.model_dump(mode="json")
    return value
