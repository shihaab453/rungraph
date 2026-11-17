"""Compact human rendering for schema-0.7 histogram and 0.8 media values."""

from __future__ import annotations

import json
import math
from typing import Any

from .models import AudioValue, HistogramValue, ImageValue, TensorValue, Tf2HistogramValue


def _tf2_num(buckets: list[dict[str, Any]]) -> str:
    try:
        total = math.fsum(bucket["count"] for bucket in buckets)
    except OverflowError:
        return "overflow"
    return json.dumps(total)


def tf2_histogram_display(value: Any) -> str | None:
    """Return ``tf2-histogram(<k> buckets, num=...)`` or None."""
    if isinstance(value, Tf2HistogramValue):
        data = value.model_dump(mode="json")
    elif isinstance(value, dict) and value.get("kind") == "tf2-histogram":
        data = value
    else:
        return None
    buckets = data.get("buckets")
    if not isinstance(buckets, list):
        return None
    return f"tf2-histogram({len(buckets)} buckets, num={_tf2_num(buckets)})"


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


def tensor_display(value: Any) -> str | None:
    """Return ``tensor(<dtype>[<shape>], <n> element[s])`` for a tensor value."""
    if isinstance(value, TensorValue):
        data = value.model_dump(mode="json")
    elif isinstance(value, dict) and value.get("kind") == "tensor":
        data = value
    else:
        return None
    dtype = data.get("dtype")
    shape = data.get("shape")
    count = data.get("element_count")
    if not isinstance(shape, list):
        return None
    dimensions = "x".join(str(item) for item in shape)
    unit = "element" if count == 1 else "elements"
    return f"tensor({dtype}[{dimensions}], {count} {unit})"


def value_display(value: Any) -> str | None:
    """Compact display for a histogram or media value, in model or dict form."""
    return (
        histogram_display(value)
        or tf2_histogram_display(value)
        or media_display(value)
        or tensor_display(value)
    )


def value_to_json(value: Any) -> Any:
    """Return a JSON-compatible recorded value."""
    if isinstance(
        value,
        (HistogramValue, ImageValue, AudioValue, TensorValue, Tf2HistogramValue),
    ):
        return value.model_dump(mode="json")
    return value
