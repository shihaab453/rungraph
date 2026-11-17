"""Reader and selection tests for schema-0.11 TF2 [k,3] histograms."""

from __future__ import annotations

import struct

import pytest

from runevidence.tensorboard_events import (
    EventFileError,
    iter_supported_tf2_histograms,
    scan_tf2_histograms,
)
from tensorboard_builder import (
    bytes_field,
    double_field,
    event,
    float_field,
    records,
    shape,
    summary,
    tensor,
    value,
)


def hist_tensor(buckets, **kwargs):
    flat = [item for bucket in buckets for item in bucket]
    if not any(key in kwargs for key in ("content", "double_values", "packed_double_values")):
        kwargs["double_values"] = flat
    return tensor(
        dtype=2,
        shape_bytes=shape([len(buckets), 3]),
        **kwargs,
    )


def hist_event(tag, buckets, *, step=0, **kwargs):
    return event(
        step=step,
        summary_bytes=summary(value(tag=tag, tensor_bytes=hist_tensor(buckets, **kwargs))),
    )


def one(buckets, **kwargs):
    return list(iter_supported_tf2_histograms(records(hist_event(b"t", buckets, **kwargs))))


def test_double_values_are_recorded_in_row_order():
    data = records(hist_event(b"t", [(0.0, 1.0, 2.0), (1.0, 2.0, 1.0)]))
    records_out = list(iter_supported_tf2_histograms(data))
    assert len(records_out) == 1
    assert records_out[0].tag == b"t"
    assert records_out[0].step == 0
    assert records_out[0].buckets == (
        {"left": 0.0, "right": 1.0, "count": 2.0},
        {"left": 1.0, "right": 2.0, "count": 1.0},
    )


def test_tensor_content_and_packed_values_are_accepted():
    buckets = [(0.0, 1.0, 2.0)]
    content = struct.pack("<3d", 0.0, 1.0, 2.0)
    assert one(buckets, content=content)[0].buckets[0]["count"] == 2.0
    assert one(buckets, packed_double_values=[0.0, 1.0, 2.0])[0].buckets


def test_content_wins_over_double_values():
    content = struct.pack("<3d", 0.0, 1.0, 2.0)
    data = records(
        event(
            step=0,
            summary_bytes=summary(
                value(
                    tag=b"t",
                    tensor_bytes=tensor(
                        dtype=2,
                        shape_bytes=shape([1, 3]),
                        content=content,
                        double_values=[9.0, 9.0, 9.0],
                    ),
                )
            ),
        )
    )
    assert list(iter_supported_tf2_histograms(data))[0].buckets[0]["left"] == 0.0


def test_empty_histogram_is_empty_buckets():
    data = records(
        event(
            step=0,
            summary_bytes=summary(
                value(
                    tag=b"t",
                    tensor_bytes=tensor(dtype=2, shape_bytes=shape([0, 3])),
                )
            ),
        )
    )
    assert list(iter_supported_tf2_histograms(data))[0].buckets == ()


def test_single_value_equal_edges_are_accepted():
    assert one([(3.0, 3.0, 0.0), (3.0, 3.0, 5.0)])[0].buckets[-1]["count"] == 5.0


@pytest.mark.parametrize(
    "buckets",
    [
        [(0.0, 1.0, -1.0)],
        [(1.0, 0.0, 1.0)],
        [(float("nan"), 1.0, 1.0)],
        [(0.0, float("inf"), 1.0)],
    ],
)
def test_nonconforming_claims_are_skipped(buckets):
    assert one(buckets) == []


def test_wrong_value_count_is_skipped():
    data = records(
        event(
            step=0,
            summary_bytes=summary(
                value(
                    tag=b"t",
                    tensor_bytes=tensor(
                        dtype=2,
                        shape_bytes=shape([1, 3]),
                        double_values=[0.0, 1.0],
                    ),
                )
            ),
        )
    )
    assert list(iter_supported_tf2_histograms(data)) == []


def test_float32_is_not_a_histogram():
    data = records(
        event(
            step=0,
            summary_bytes=summary(
                value(
                    tag=b"t",
                    tensor_bytes=tensor(
                        dtype=1,
                        shape_bytes=shape([1, 3]),
                        float_values=[0.0, 1.0, 2.0],
                    ),
                )
            ),
        )
    )
    assert list(iter_supported_tf2_histograms(data)) == []


def test_foreign_field_skips_the_entry():
    data = records(
        event(
            step=0,
            summary_bytes=summary(
                value(
                    tag=b"t",
                    tensor_bytes=tensor(
                        dtype=2,
                        shape_bytes=shape([1, 3]),
                        double_values=[0.0, 1.0, 2.0],
                        extra=bytes_field(8, b"label"),
                    ),
                )
            ),
        )
    )
    assert list(iter_supported_tf2_histograms(data)) == []


def test_structural_double_span_refuses_the_file():
    data = records(
        event(
            step=0,
            summary_bytes=summary(
                value(
                    tag=b"t",
                    tensor_bytes=tensor(
                        dtype=2,
                        shape_bytes=shape([1, 3]),
                        extra=bytes_field(6, b"\x00"),
                    ),
                )
            ),
        )
    )
    with pytest.raises(EventFileError):
        list(iter_supported_tf2_histograms(data))


def test_losing_tensor_occurrence_is_still_structurally_checked():
    payload = (
        bytes_field(1, b"t")
        + bytes_field(
            8,
            tensor(dtype=2, shape_bytes=shape([1, 3]), extra=bytes_field(6, b"\x00")),
        )
        + float_field(2, 1.0)
    )
    data = records(event(step=0, summary_bytes=summary(payload)))
    with pytest.raises(EventFileError):
        list(iter_supported_tf2_histograms(data))


def test_shape_fragments_and_value_fields_merge():
    first = tensor(
        dtype=2,
        shape_bytes=shape([1]),
        packed_double_values=[0.0],
    )
    second = tensor(shape_bytes=shape([3]), double_values=[1.0, 2.0])
    payload = (
        bytes_field(1, b"t")
        + bytes_field(8, first)
        + bytes_field(8, second)
    )
    data = records(event(step=0, summary_bytes=summary(payload)))
    result = list(iter_supported_tf2_histograms(data))
    assert result[0].buckets[0] == {"left": 0.0, "right": 1.0, "count": 2.0}


def test_scan_ladder_missing_beats_unsupported_encoding():
    malformed = tensor(
        dtype=2,
        shape_bytes=shape([1, 3]),
        double_values=[0.0, 1.0, -1.0],
    )
    good = hist_tensor([(0.0, 1.0, 2.0)])
    data = records(
        event(
            step=0,
            summary_bytes=summary(
                value(tag=b"t", tensor_bytes=good),
                value(tag=b"t", tensor_bytes=malformed),
            ),
        )
    )
    selection = scan_tf2_histograms(data, [(b"t", 0, 1)])
    assert selection[(b"t", 0, 1)].outcome == "missing"
    assert scan_tf2_histograms(data, [(b"t", 0, 0)])[(b"t", 0, 0)].outcome == "value"


def test_scan_ladder_only_malformed_is_unsupported_encoding():
    malformed = tensor(
        dtype=2,
        shape_bytes=shape([1, 3]),
        double_values=[0.0, 1.0, -1.0],
    )
    data = records(
        event(step=0, summary_bytes=summary(value(tag=b"t", tensor_bytes=malformed)))
    )
    assert scan_tf2_histograms(data, [(b"t", 0, 0)])[(b"t", 0, 0)].outcome == (
        "unsupported_encoding"
    )


def test_scan_ladder_other_tensor_is_unsupported_value():
    data = records(
        event(
            step=0,
            summary_bytes=summary(
                value(
                    tag=b"t",
                    tensor_bytes=tensor(
                        dtype=1, shape_bytes=shape([4]), float_values=[1.0]
                    ),
                )
            ),
        )
    )
    assert scan_tf2_histograms(data, [(b"t", 0, 0)])[(b"t", 0, 0)].outcome == (
        "unsupported_value"
    )


def test_scan_ladder_nothing_is_missing():
    data = records(event(step=0, summary_bytes=summary(value(tag=b"other", simple_value=1.0))))
    assert scan_tf2_histograms(data, [(b"t", 0, 0)])[(b"t", 0, 0)].outcome == "missing"
