"""Compact human rendering for schema-0.7 histogram and 0.8 media values."""

from __future__ import annotations

import json
from typing import Any

from .models import AudioValue, HistogramValue, ImageValue


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


def media_display(value: Any) -> str | None:
    """Return ``image(...)`` or ``audio(...)`` for a media value, else None."""
    if isinstance(value, ImageValue):
        data = value.model_dump(mode="json")
    elif isinstance(value, AudioValue):
        data = value.model_dump(mode="json")
    elif isinstance(value, dict) and value.get("kind") in ("image", "audio"):
        data = value
    else:
        return None
    kind = data.get("kind")
    media_type = data.get("media_type")
    size = data.get("size_bytes")
    if kind == "image":
        width = data.get("width")
        height = data.get("height")
        return f"image({width}x{height} png, {size} bytes)"
    if kind == "audio":
        return f"audio(wav, {size} bytes)"
    return None


def value_display(value: Any) -> str | None:
    """Compact display for a histogram or media value, in model or dict form."""
    return histogram_display(value) or media_display(value)


def value_to_json(value: Any) -> Any:
    """Return a JSON-compatible recorded value."""
    if isinstance(value, (HistogramValue, ImageValue, AudioValue)):
        return value.model_dump(mode="json")
    return value
