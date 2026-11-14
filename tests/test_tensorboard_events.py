"""Strict TensorBoard event reader: RFC section 12 vectors.

Phase 2 pins the dependency-free reader only: TFRecord framing and CRC32C,
the protobuf subset and merge rules, scalar classification and occurrence
selection, the merged-summary cap, the comparator, a memory-bounded max-summary
vector and a seeded mutation fuzz.  Checker integration is a later phase.
"""

from __future__ import annotations

import json
import math
import os
import platform
import random
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

from runevidence.locator_profiles import compare_tensorboard, compare_values
from runevidence.tensorboard_events import (
    MAX_SUMMARY_BYTES,
    EventFileError,
    ScalarOutcome,
    ScalarRecord,
    iter_supported_scalars,
    masked_crc32c,
    scan_scalars,
)

import tensorboard_builder as tb

REPO = Path(__file__).resolve().parents[1]
REFUSAL = "strict TensorBoard event profile refusal"
MIB = 1024 * 1024


# --- helpers ---------------------------------------------------------------


def _event_with_value(value_bytes: bytes, *, step: int | None = None) -> bytes:
    return tb.records(tb.event(step=step, summary_bytes=tb.summary(value_bytes)))


def _tensor_value(tensor_bytes: bytes, *, tag: bytes = b"x") -> bytes:
    return tb.value(tag=tag, tensor_bytes=tensor_bytes)


def _scan_one(data, tag=b"x", step=0, occurrence=0):
    request = (tag, step, occurrence)
    return scan_scalars(data, [request])[request]


def _assert_refused(data) -> None:
    with pytest.raises(EventFileError):
        list(iter_supported_scalars(data))
    with pytest.raises(EventFileError):
        scan_scalars(data, [(b"x", 0, 0)])


# --- framing and CRC32C ----------------------------------------------------


def test_crc32c_known_vectors_and_mask():
    assert tb.crc32c(b"123456789") == 0xE3069283
    assert masked_crc32c(b"123456789") == 0xC78AB0E5
    assert masked_crc32c(bytearray(b"123456789")) == 0xC78AB0E5
    assert masked_crc32c(b"") == 0xA282EAD8
    # TFRecord length-header vectors.
    assert masked_crc32c(struct.pack("<Q", 0)) == 0x07980329
    assert masked_crc32c(struct.pack("<Q", 3)) == 0x0E4999B0
    assert masked_crc32c(b"abc") == 0x21F1576E


def test_empty_file_and_header_only_record_are_valid_with_zero_records():
    assert list(iter_supported_scalars(b"")) == []
    # TFRecord has no file header: a "header-only" file is one zero-length
    # record, whose empty Event is valid and skipped.
    assert list(iter_supported_scalars(tb.record(b""))) == []
    assert list(iter_supported_scalars(tb.record(b"") + tb.record(b""))) == []
    for data in (b"", tb.record(b"")):
        request = (b"x", 0, 0)
        selection = scan_scalars(data, [request])[request]
        assert selection.outcome is ScalarOutcome.mismatch
        assert selection.value is None


def test_zero_length_record_does_not_hide_later_records():
    data = tb.record(b"") + tb.record(tb.scalar_event(b"x", 0.5))
    assert list(iter_supported_scalars(data)) == [
        ScalarRecord(b"x", 0, 0.5, True)
    ]


def test_file_version_header_event_only_is_valid_with_zero_scalars():
    # TensorBoard writes a file_version event as its file header; a file with
    # only that event has zero scalar records.
    data = tb.records(tb.event(file_version=b"brain.Event:2"))
    assert list(iter_supported_scalars(data)) == []
    assert _scan_one(data).outcome is ScalarOutcome.mismatch


@pytest.mark.parametrize(
    "data",
    [
        b"\x00" * 8,  # length without its CRC
        tb.record(b"abcd")[:11],  # truncated length CRC
        tb.record(b"abcd")[:14],  # truncated data
        tb.record(b"abcd")[:15],  # missing data CRC
        tb.record(b"abcd")[:-1],
    ],
)
def test_truncated_framing_is_refused(data):
    _assert_refused(data)


def test_trailing_bytes_after_the_last_record_are_refused():
    valid = tb.record(tb.scalar_event(b"x", 0.5))
    _assert_refused(valid + b"\x00")
    _assert_refused(valid + b"\x00" * 11)
    _assert_refused(valid + b"\x00" * 12)
    # Two complete records are not trailing bytes.
    assert len(list(iter_supported_scalars(valid + valid))) == 2


def test_length_crc_mismatch_is_refused():
    payload = tb.scalar_event(b"x", 0.5)
    good = tb.record(payload)
    header_crc = struct.unpack_from("<I", good, 8)[0]
    bad = tb.record(payload, length_crc=header_crc ^ 1)
    _assert_refused(bad)


def test_payload_crc_mismatch_is_refused():
    payload = tb.scalar_event(b"x", 0.5)
    good = tb.record(payload)
    data_crc = struct.unpack_from("<I", good, len(good) - 4)[0]
    bad = tb.record(payload, data_crc=data_crc ^ 1)
    _assert_refused(bad)


def test_length_over_remaining_bytes_is_refused():
    _assert_refused(tb.record(b"", length=1000))
    _assert_refused(tb.record(b"", length=(1 << 64) - 1))


def test_zero_scalar_entries_are_valid():
    data = tb.records(
        tb.event(file_version=b"tensorboard"),
        tb.event(graph_def=b"graph"),
        tb.event(step=4),
    )
    assert list(iter_supported_scalars(data)) == []
    selection = _scan_one(data, tag=b"x", step=4)
    assert selection.outcome is ScalarOutcome.mismatch


def test_bytes_and_bytearray_are_equivalent():
    data = tb.records(tb.scalar_event(b"x", 0.5, step=3))
    as_bytes = list(iter_supported_scalars(data))
    as_bytearray = list(iter_supported_scalars(bytearray(data)))
    assert as_bytes == as_bytearray == [ScalarRecord(b"x", 3, 0.5, True)]
    assert _scan_one(bytearray(data), tag=b"x", step=3).value == 0.5


def test_refusal_message_is_fixed_and_has_no_parser_text():
    with pytest.raises(EventFileError) as raised:
        list(iter_supported_scalars(tb.record(b"abc")[:-1]))
    message = str(raised.value)
    assert message == REFUSAL
    lowered = message.lower()
    for token in ("crc", "protobuf", "varint", "offset", "errno"):
        assert token not in lowered


# --- protobuf: keys and varints -------------------------------------------


def test_field_number_zero_is_refused():
    _assert_refused(tb.records(tb.varint(0)))
    _assert_refused(tb.records(tb.key(0, 2)))


@pytest.mark.parametrize("wire", [3, 4, 6, 7])
def test_groups_and_unknown_wire_types_are_refused(wire):
    _assert_refused(tb.records(tb.varint((10 << 3) | wire)))


def test_field_number_must_fit_29_bits():
    # The largest legal field number is a valid unknown field.
    legal = tb.varint((((1 << 29) - 1) << 3) | 0) + tb.varint(0)
    assert list(iter_supported_scalars(tb.records(legal))) == []
    illegal = tb.varint(((1 << 29) << 3) | 0) + tb.varint(0)
    _assert_refused(tb.records(illegal))


@pytest.mark.parametrize(
    "varint_bytes",
    [
        b"\x80",  # truncated
        b"\x80" * 10,  # tenth byte has the continuation bit
        b"\x80" * 9 + b"\x02",  # tenth byte above 0x01
        b"\x80" * 9 + b"\xff",  # tenth byte above 0x01
    ],
)
def test_varint_bounds_are_refused(varint_bytes):
    _assert_refused(tb.records(tb.key(2, 0) + varint_bytes))


def test_ten_byte_varint_with_high_bit_is_accepted():
    # 0x80 * 9 then 0x01 is 2**63, the int64 minimum.
    step_bytes = b"\x80" * 9 + b"\x01"
    data = tb.records(
        tb.key(2, 0)
        + step_bytes
        + tb.bytes_field(5, tb.summary(tb.value(tag=b"x", simple_value=0.5)))
    )
    assert _scan_one(data, tag=b"x", step=-(1 << 63)).value == 0.5


def test_non_minimal_varints_are_accepted_if_they_fit():
    data = tb.records(
        tb.key(2, 0)
        + tb.varint(5, extra=2)
        + tb.bytes_field(5, tb.summary(tb.value(tag=b"x", simple_value=0.5)))
    )
    assert _scan_one(data, tag=b"x", step=5).value == 0.5


# --- protobuf: wire types, unknown fields and merge rules -----------------


@pytest.mark.parametrize(
    "payload",
    [
        tb.key(1, 0) + tb.varint(0),  # wall_time wire 0
        tb.key(2, 1) + b"\x00" * 8,  # step wire 1
        tb.key(5, 0) + tb.varint(0),  # summary wire 0
        tb.key(9, 0) + tb.varint(0),  # meta_graph_def wire 0
    ],
)
def test_event_known_fields_with_wrong_wire_type_are_refused(payload):
    _assert_refused(tb.records(payload))


@pytest.mark.parametrize(
    "value_payload",
    [
        tb.key(1, 0) + tb.varint(0),  # tag wire 0
        tb.bytes_field(2, b"\x00" * 4),  # simple_value wire 2
        tb.fixed32_field(8, 0),  # tensor wire 5
        tb.key(3, 0) + tb.varint(0),  # histogram wire 0
        tb.key(9, 0) + tb.varint(0),  # metadata wire 0
    ],
)
def test_value_known_fields_with_wrong_wire_type_are_refused(value_payload):
    _assert_refused(_event_with_value(value_payload))


def test_summary_value_field_with_wrong_wire_type_is_refused():
    summary_payload = tb.key(1, 0) + tb.varint(0)
    _assert_refused(tb.records(tb.event(summary_bytes=summary_payload)))


@pytest.mark.parametrize(
    "tensor_payload",
    [
        tb.bytes_field(1, b""),  # dtype wire 2
        tb.key(2, 0) + tb.varint(0),  # tensor_shape wire 0
        tb.key(4, 0) + tb.varint(0),  # tensor_content wire 0
        tb.key(5, 0) + tb.varint(0),  # float_val wire 0
        tb.fixed32_field(6, 0),  # double_val wire 5
        tb.key(4, 5) + b"\x00" * 4,  # tensor_content wire 5
    ],
)
def test_tensor_known_fields_with_wrong_wire_type_are_refused(tensor_payload):
    _assert_refused(_event_with_value(_tensor_value(tensor_payload)))


@pytest.mark.parametrize(
    "shape_payload",
    [
        tb.key(2, 0) + tb.varint(0),  # dim wire 0
        tb.key(3, 2) + b"",  # unknown_rank wire 2
    ],
)
def test_shape_known_fields_with_wrong_wire_type_are_refused(shape_payload):
    tensor_bytes = tb.tensor(dtype=1, shape_bytes=shape_payload)
    _assert_refused(_event_with_value(_tensor_value(tensor_bytes)))


@pytest.mark.parametrize(
    "dim_payload",
    [
        tb.bytes_field(1, b""),  # size wire 2
        tb.key(2, 0) + tb.varint(0),  # name wire 0
    ],
)
def test_dim_known_fields_with_wrong_wire_type_are_refused(dim_payload):
    tensor_bytes = tb.tensor(
        dtype=1, shape_bytes=tb.bytes_field(2, dim_payload)
    )
    _assert_refused(_event_with_value(_tensor_value(tensor_bytes)))


def test_known_field_wrong_wire_type_is_refused_even_when_losing():
    data = tb.records(
        tb.bytes_field(5, tb.summary(tb.value(tag=b"x", simple_value=0.5)))
        + tb.key(4, 0)
        + tb.varint(0)
    )
    _assert_refused(data)


def test_unknown_fields_are_skipped_by_wire_type():
    unknown = (
        tb.varint_field(100, 7)
        + tb.fixed64_field(101, 1)
        + tb.bytes_field(102, b"payload")
        + tb.fixed32_field(103, 1)
    )
    tensor_bytes = (
        tb.tensor(
            dtype=1,
            shape_bytes=tb.shape([]),
            content=struct.pack("<f", 0.5),
        )
        + tb.varint_field(200, 1)
        + tb.bytes_field(201, b"x")
    )
    value_payload = (
        tb.bytes_field(1, b"x")
        + tb.varint_field(150, 1)
        + tb.bytes_field(8, tensor_bytes)
        + tb.fixed32_field(151, 1)
    )
    data = tb.records(
        unknown
        + tb.varint_field(2, 0)
        + tb.bytes_field(5, tb.summary(value_payload))
        + unknown
    )
    assert _scan_one(data).value == 0.5


def test_file_version_is_not_validated_and_wall_time_is_ignored():
    data = tb.records(
        tb.event(
            wall_time=float("nan"),
            file_version=b"\xff\xfe not utf8",
            summary_bytes=tb.summary(tb.value(tag=b"x", simple_value=0.5)),
        )
    )
    assert _scan_one(data).value == 0.5


def test_metadata_is_ignored():
    data = _event_with_value(
        tb.value(tag=b"x", simple_value=0.5, metadata=b"\xff\xfe")
    )
    assert _scan_one(data).value == 0.5


def test_node_name_is_ignored_but_wire_checked():
    value_payload = (
        tb.bytes_field(7, b"node/op")
        + tb.bytes_field(1, b"x")
        + tb.float_field(2, 0.5)
    )
    assert _scan_one(_event_with_value(value_payload)).value == 0.5
    bad = (
        tb.bytes_field(1, b"x")
        + tb.key(7, 0)
        + tb.varint(0)
    )
    _assert_refused(_event_with_value(bad))


def test_duplicate_step_is_last_wins():
    data = tb.records(
        tb.event(
            summary_bytes=tb.summary(tb.value(tag=b"x", simple_value=0.5)),
            extra=tb.varint_field(2, 4),
        )
    )
    assert _scan_one(data, step=4).value == 0.5
    assert _scan_one(data, step=0).outcome is ScalarOutcome.mismatch


def test_duplicate_tag_is_last_wins():
    value_payload = (
        tb.bytes_field(1, b"old")
        + tb.bytes_field(1, b"new")
        + tb.float_field(2, 0.5)
    )
    data = _event_with_value(value_payload)
    assert _scan_one(data, tag=b"new").value == 0.5
    assert _scan_one(data, tag=b"old").outcome is ScalarOutcome.mismatch


def test_duplicate_dtype_is_last_wins():
    tensor_bytes = (
        tb.varint_field(1, 1)
        + tb.bytes_field(4, struct.pack("<d", 2.5))
        + tb.varint_field(1, 2)
    )
    data = _event_with_value(_tensor_value(tensor_bytes))
    selection = _scan_one(data)
    assert selection.outcome is ScalarOutcome.value
    assert selection.value == 2.5
    assert selection.float32 is False


def test_duplicate_tensor_content_is_last_wins():
    tensor_bytes = (
        tb.varint_field(1, 1)
        + tb.bytes_field(4, struct.pack("<f", 1.0))
        + tb.bytes_field(4, struct.pack("<f", 2.0))
    )
    data = _event_with_value(_tensor_value(tensor_bytes))
    assert _scan_one(data).value == 2.0


def test_tensor_content_is_little_endian():
    big_endian = struct.pack(">f", 1.0)
    tensor_bytes = tb.tensor(
        dtype=1, shape_bytes=tb.shape([]), content=big_endian
    )
    data = _event_with_value(_tensor_value(tensor_bytes))
    selection = _scan_one(data)
    assert selection.value == struct.unpack("<f", big_endian)[0]
    assert selection.value != 1.0


@pytest.mark.parametrize(
    "dtype,field,width",
    [(1, 5, 4), (2, 6, 8)],
    ids=["packed-float", "packed-double"],
)
def test_packed_value_length_must_be_a_multiple_of_its_width(dtype, field, width):
    tensor_bytes = tb.tensor(
        dtype=dtype,
        shape_bytes=tb.shape([]),
        extra=tb.bytes_field(field, b"\x00" * (width - 1)),
    )
    _assert_refused(_event_with_value(_tensor_value(tensor_bytes)))


def test_tensor_content_last_wins_across_merged_tensors():
    t1 = tb.tensor(dtype=1, content=struct.pack("<f", 1.0))
    t2 = tb.tensor(
        shape_bytes=tb.shape([]), content=struct.pack("<f", 2.0)
    )
    value_payload = (
        tb.bytes_field(1, b"x") + tb.bytes_field(8, t1) + tb.bytes_field(8, t2)
    )
    assert _scan_one(_event_with_value(value_payload)).value == 2.0


def test_explicit_defaults_count_as_present():
    # step: 5 then an explicit 0; the later explicit default wins.
    data = tb.records(
        tb.varint_field(2, 5)
        + tb.varint_field(2, 0)
        + tb.bytes_field(5, tb.summary(tb.value(tag=b"x", simple_value=0.5)))
    )
    assert _scan_one(data, step=0).value == 0.5
    assert _scan_one(data, step=5).outcome is ScalarOutcome.mismatch

    # tag: b"x" then an explicit empty tag; the empty tag wins.
    value_payload = (
        tb.bytes_field(1, b"x")
        + tb.bytes_field(1, b"")
        + tb.float_field(2, 0.5)
    )
    data = _event_with_value(value_payload)
    assert _scan_one(data, tag=b"").value == 0.5
    assert _scan_one(data, tag=b"x").outcome is ScalarOutcome.mismatch

    # dtype: DT_FLOAT then an explicit DT_INVALID (0); the default wins.
    tensor_bytes = tb.tensor(
        dtype=1,
        shape_bytes=tb.shape([]),
        content=struct.pack("<f", 0.5),
        extra=tb.varint_field(1, 0),
    )
    data = _event_with_value(_tensor_value(tensor_bytes))
    assert _scan_one(data).outcome is ScalarOutcome.unsupported_scalar

    # tensor_content: 4 bytes then an explicit empty value; empty wins.
    tensor_bytes = tb.tensor(
        dtype=1,
        shape_bytes=tb.shape([]),
        content=struct.pack("<f", 0.5),
        extra=tb.bytes_field(4, b""),
    )
    data = _event_with_value(_tensor_value(tensor_bytes))
    assert _scan_one(data).outcome is ScalarOutcome.unsupported_scalar

    # Dim.size: 1 then an explicit 0; the dimension becomes size 0.
    dim_bytes = tb.varint_field(1, 1) + tb.varint_field(1, 0)
    tensor_bytes = tb.tensor(
        dtype=1,
        shape_bytes=tb.bytes_field(2, dim_bytes),
        content=struct.pack("<f", 0.5),
    )
    data = _event_with_value(_tensor_value(tensor_bytes))
    assert _scan_one(data).outcome is ScalarOutcome.unsupported_value


def test_duplicate_dim_size_is_last_wins():
    dim_bytes = tb.varint_field(1, 5) + tb.varint_field(1, 1)
    tensor_bytes = tb.tensor(
        dtype=1,
        shape_bytes=tb.bytes_field(2, dim_bytes),
        content=struct.pack("<f", 0.5),
    )
    data = _event_with_value(_tensor_value(tensor_bytes))
    assert _scan_one(data).value == 0.5


def test_repeated_summary_fields_merge_in_order():
    first = tb.summary(tb.value(tag=b"x", simple_value=1.0))
    second = tb.summary(tb.value(tag=b"x", simple_value=2.0))
    data = tb.records(
        tb.varint_field(2, 0) + tb.bytes_field(5, first) + tb.bytes_field(5, second)
    )
    assert _scan_one(data, occurrence=0).value == 1.0
    assert _scan_one(data, occurrence=1).value == 2.0
    assert _scan_one(data, occurrence=2).outcome is ScalarOutcome.mismatch


def test_repeated_tensor_fields_merge_dtype_and_content():
    # RFC vector: T1 has dtype and content, T2 only a rank-0 shape.  The
    # merged tensor keeps T1's dtype and 4 content bytes.
    t1 = tb.tensor(dtype=1, content=struct.pack("<f", 1.25))
    t2 = tb.tensor(shape_bytes=tb.shape([]))
    value_payload = (
        tb.bytes_field(1, b"x") + tb.bytes_field(8, t1) + tb.bytes_field(8, t2)
    )
    data = _event_with_value(value_payload)
    assert _scan_one(data).value == 1.25


def test_repeated_shape_fields_merge_dimension_lists():
    tensor_bytes = tb.tensor(
        dtype=1,
        shape_bytes=tb.shape([1]),
        content=struct.pack("<f", 0.5),
        extra=tb.bytes_field(2, tb.shape([1])),
    )
    data = _event_with_value(_tensor_value(tensor_bytes))
    # Two dimensions of size 1 merge into rank 2, so the tensor is non-scalar.
    assert _scan_one(data).outcome is ScalarOutcome.unsupported_value


def test_merged_shape_with_no_new_dimension_keeps_the_existing_dims():
    tensor_bytes = tb.tensor(
        dtype=1,
        shape_bytes=tb.shape([1]),
        content=struct.pack("<f", 0.5),
        extra=tb.bytes_field(2, tb.shape([])),
    )
    data = _event_with_value(_tensor_value(tensor_bytes))
    assert _scan_one(data).value == 0.5


def test_dim_size_absent_means_zero():
    tensor_bytes = tb.tensor(
        dtype=1,
        shape_bytes=tb.bytes_field(2, tb.dim(name=b"d")),
        content=struct.pack("<f", 0.5),
    )
    data = _event_with_value(_tensor_value(tensor_bytes))
    assert _scan_one(data).outcome is ScalarOutcome.unsupported_value


def test_event_summary_merge_starts_after_last_different_what_member():
    a = tb.summary(tb.value(tag=b"x", simple_value=1.0))
    b = tb.summary(tb.value(tag=b"x", simple_value=2.0))
    c = tb.summary(tb.value(tag=b"x", simple_value=3.0))
    data = tb.records(
        tb.varint_field(2, 0)
        + tb.bytes_field(5, a)
        + tb.bytes_field(3, b"file_version")
        + tb.bytes_field(5, b)
        + tb.bytes_field(5, c)
    )
    assert _scan_one(data, occurrence=0).value == 2.0
    assert _scan_one(data, occurrence=1).value == 3.0
    assert _scan_one(data, occurrence=2).outcome is ScalarOutcome.mismatch


def test_tensor_after_simple_value_replaces_it():
    value_payload = (
        tb.bytes_field(1, b"x")
        + tb.float_field(2, 1.0)
        + tb.bytes_field(
            8,
            tb.tensor(
                dtype=1,
                shape_bytes=tb.shape([]),
                content=struct.pack("<f", 2.0),
            ),
        )
    )
    data = _event_with_value(value_payload)
    selection = _scan_one(data)
    assert selection.value == 2.0
    assert selection.float32 is True


def test_value_oneof_interleaving_merges_only_after_last_different_member():
    value_payload = (
        tb.bytes_field(1, b"x")
        + tb.float_field(2, 1.0)
        + tb.bytes_field(
            8,
            tb.tensor(
                dtype=1,
                shape_bytes=tb.shape([]),
                content=struct.pack("<f", 2.0),
            ),
        )
        + tb.float_field(2, 3.0)
        + tb.bytes_field(
            8,
            tb.tensor(
                dtype=1,
                shape_bytes=tb.shape([]),
                content=struct.pack("<f", 4.0),
            ),
        )
    )
    data = _event_with_value(value_payload)
    selection = _scan_one(data)
    assert selection.value == 4.0
    assert _scan_one(data, occurrence=1).outcome is ScalarOutcome.mismatch


def test_value_oneof_interleaving_does_not_merge_the_losing_tensor():
    t1 = tb.tensor(dtype=1, content=struct.pack("<f", 1.0))
    t2 = tb.tensor(shape_bytes=tb.shape([]))
    value_payload = (
        tb.bytes_field(1, b"x")
        + tb.bytes_field(8, t1)
        + tb.bytes_field(3, b"")  # histogram replaces the tensor
        + tb.bytes_field(8, t2)
    )
    data = _event_with_value(value_payload)
    # T1 lost; T2 alone has no dtype or content, so it is an unsupported scalar.
    assert _scan_one(data).outcome is ScalarOutcome.unsupported_scalar


def test_losing_tensor_payload_is_never_decoded():
    value_payload = (
        tb.bytes_field(1, b"x")
        + tb.bytes_field(8, b"\xff\xff")
        + tb.float_field(2, 0.5)
    )
    data = _event_with_value(value_payload)
    assert _scan_one(data).value == 0.5


def test_losing_summary_payload_is_never_decoded():
    data = tb.records(
        tb.bytes_field(5, b"\xff\xff\xff")
        + tb.bytes_field(3, b"file_version")
        + tb.bytes_field(6, b"log")
    )
    assert list(iter_supported_scalars(data)) == []


# --- classification --------------------------------------------------------


@pytest.mark.parametrize(
    "shape_bytes",
    [tb.shape([]), tb.shape([1])],
    ids=["rank-0", "rank-1-dim-1"],
)
@pytest.mark.parametrize(
    "dtype,encoding,float32",
    [
        (1, "content", True),
        (1, "unpacked", True),
        (1, "packed", True),
        (2, "content", False),
        (2, "unpacked", False),
        (2, "packed", False),
    ],
    ids=[
        "float-content",
        "float-unpacked",
        "float-packed",
        "double-content",
        "double-unpacked",
        "double-packed",
    ],
)
def test_supported_tensor_encodings(shape_bytes, dtype, encoding, float32):
    number = 1.5
    kwargs = {"dtype": dtype, "shape_bytes": shape_bytes}
    if dtype == 1:
        if encoding == "content":
            kwargs["content"] = struct.pack("<f", number)
        elif encoding == "unpacked":
            kwargs["float_values"] = [number]
        else:
            kwargs["packed_float_values"] = [number]
    else:
        if encoding == "content":
            kwargs["content"] = struct.pack("<d", number)
        elif encoding == "unpacked":
            kwargs["double_values"] = [number]
        else:
            kwargs["packed_double_values"] = [number]
    data = _event_with_value(_tensor_value(tb.tensor(**kwargs)))
    selection = _scan_one(data)
    assert selection.outcome is ScalarOutcome.value
    assert selection.value == number
    assert selection.float32 is float32


@pytest.mark.parametrize(
    "shape_bytes",
    [tb.shape([2]), tb.shape([1, 1]), tb.shape([1, 1, 1])],
    ids=["rank-1-dim-2", "rank-2", "rank-3"],
)
def test_non_scalar_shapes_are_unsupported_values(shape_bytes):
    tensor_bytes = tb.tensor(
        dtype=1, shape_bytes=shape_bytes, content=struct.pack("<f", 0.5)
    )
    data = _event_with_value(_tensor_value(tensor_bytes))
    assert _scan_one(data).outcome is ScalarOutcome.unsupported_value
    assert list(iter_supported_scalars(data)) == []


@pytest.mark.parametrize(
    "shape_bytes",
    [tb.shape([1], unknown_rank=True), tb.shape([], unknown_rank=True)],
    ids=["known-dims", "rank-0"],
)
def test_unknown_rank_is_non_scalar_shape_first(shape_bytes):
    tensor_bytes = tb.tensor(
        dtype=1, shape_bytes=shape_bytes, content=struct.pack("<f", 0.5)
    )
    data = _event_with_value(_tensor_value(tensor_bytes))
    assert _scan_one(data).outcome is ScalarOutcome.unsupported_value


@pytest.mark.parametrize(
    "encoded_rank",
    [2, 255, 1 << 63, (1 << 64) - 1],
    ids=["two", "255", "2**63", "2**64-1"],
)
def test_unknown_rank_is_true_for_any_nonzero_varint(encoded_rank):
    shape_bytes = tb.varint_field(3, encoded_rank)
    tensor_bytes = tb.tensor(
        dtype=1, shape_bytes=shape_bytes, content=struct.pack("<f", 0.5)
    )
    data = _event_with_value(_tensor_value(tensor_bytes))
    assert _scan_one(data).outcome is ScalarOutcome.unsupported_value


def test_explicit_zero_unknown_rank_stays_scalar_shaped():
    shape_bytes = tb.shape([1], unknown_rank=False)
    tensor_bytes = tb.tensor(
        dtype=1, shape_bytes=shape_bytes, content=struct.pack("<f", 0.5)
    )
    data = _event_with_value(_tensor_value(tensor_bytes))
    assert _scan_one(data).value == 0.5


def test_merged_shape_keeps_unknown_rank_when_the_second_shape_omits_it():
    tensor_bytes = tb.tensor(
        dtype=1,
        shape_bytes=tb.shape([], unknown_rank=True),
        content=struct.pack("<f", 0.5),
        extra=tb.bytes_field(2, tb.shape([])),
    )
    data = _event_with_value(_tensor_value(tensor_bytes))
    assert _scan_one(data).outcome is ScalarOutcome.unsupported_value


def test_no_winning_value_member_is_non_scalar():
    data = _event_with_value(tb.bytes_field(1, b"x"))
    assert _scan_one(data).outcome is ScalarOutcome.unsupported_value


def test_empty_content_with_no_star_val_is_unsupported_scalar():
    tensor_bytes = tb.tensor(
        dtype=1, shape_bytes=tb.shape([]), content=b""
    )
    data = _event_with_value(_tensor_value(tensor_bytes))
    assert _scan_one(data).outcome is ScalarOutcome.unsupported_scalar


def test_content_plus_value_is_unsupported_scalar():
    tensor_bytes = tb.tensor(
        dtype=1,
        shape_bytes=tb.shape([]),
        content=struct.pack("<f", 0.5),
        float_values=[0.5],
    )
    data = _event_with_value(_tensor_value(tensor_bytes))
    assert _scan_one(data).outcome is ScalarOutcome.unsupported_scalar


def test_float_val_on_double_is_unsupported_scalar():
    tensor_bytes = tb.tensor(
        dtype=2, shape_bytes=tb.shape([]), float_values=[0.5]
    )
    data = _event_with_value(_tensor_value(tensor_bytes))
    assert _scan_one(data).outcome is ScalarOutcome.unsupported_scalar


def test_double_val_on_float_is_unsupported_scalar():
    tensor_bytes = tb.tensor(
        dtype=1, shape_bytes=tb.shape([]), double_values=[0.5]
    )
    data = _event_with_value(_tensor_value(tensor_bytes))
    assert _scan_one(data).outcome is ScalarOutcome.unsupported_scalar


@pytest.mark.parametrize(
    "tensor_bytes",
    [
        tb.tensor(
            dtype=1,
            shape_bytes=tb.shape([]),
            float_values=[1.0, 2.0],
        ),
        tb.tensor(
            dtype=1,
            shape_bytes=tb.shape([]),
            packed_float_values=[1.0, 2.0],
        ),
        tb.tensor(
            dtype=2,
            shape_bytes=tb.shape([]),
            double_values=[1.0, 2.0],
        ),
        tb.tensor(
            dtype=2,
            shape_bytes=tb.shape([]),
            packed_double_values=[1.0, 2.0],
        ),
    ],
    ids=["float-unpacked", "float-packed", "double-unpacked", "double-packed"],
)
def test_wrong_value_count_is_unsupported_scalar(tensor_bytes):
    data = _event_with_value(_tensor_value(tensor_bytes))
    assert _scan_one(data).outcome is ScalarOutcome.unsupported_scalar


@pytest.mark.parametrize("dtype", [19, 14, 3, 7, 2**40], ids=["half", "bfloat16", "int32", "string", "out-of-int32"])
def test_unsupported_dtypes_are_unsupported_scalars(dtype):
    tensor_bytes = tb.tensor(
        dtype=dtype, shape_bytes=tb.shape([]), content=b"\x00"
    )
    data = _event_with_value(_tensor_value(tensor_bytes))
    assert _scan_one(data).outcome is ScalarOutcome.unsupported_scalar


@pytest.mark.parametrize(
    "value_payload",
    [
        tb.value(tag=b"x", histogram=b""),
        tb.value(tag=b"x", image=b""),
        tb.value(tag=b"x", histo=b""),
        tb.value(tag=b"x", audio=b""),
    ],
    ids=["histogram", "image", "histo", "audio"],
)
def test_histogram_image_histo_and_audio_are_non_scalar(value_payload):
    data = _event_with_value(value_payload)
    assert _scan_one(data).outcome is ScalarOutcome.unsupported_value


def test_mixed_unsupported_and_non_scalar_uses_unsupported_scalar_reason():
    unsupported = tb.tensor(
        dtype=19, shape_bytes=tb.shape([]), content=b"\x00"
    )
    data = _event_with_value(tb.value(tag=b"x", histogram=b""))
    data += _event_with_value(_tensor_value(unsupported))
    assert _scan_one(data).outcome is ScalarOutcome.unsupported_scalar

    data = _event_with_value(_tensor_value(unsupported))
    data += _event_with_value(tb.value(tag=b"x", histogram=b""))
    assert _scan_one(data).outcome is ScalarOutcome.unsupported_scalar


def test_only_supported_scalars_increment_occurrence():
    unsupported = tb.tensor(
        dtype=19, shape_bytes=tb.shape([]), content=b"\x00"
    )
    data = tb.records(
        tb.scalar_event(b"x", 1.0),
        tb.event(summary_bytes=tb.summary(tb.value(tag=b"x", histogram=b""))),
        tb.event(summary_bytes=tb.summary(_tensor_value(unsupported))),
        tb.scalar_event(b"x", 2.0),
    )
    assert _scan_one(data, occurrence=0).value == 1.0
    assert _scan_one(data, occurrence=1).value == 2.0
    assert _scan_one(data, occurrence=2).outcome is ScalarOutcome.mismatch


def test_iter_supported_scalars_filters_and_keeps_file_order():
    unsupported = tb.tensor(
        dtype=19, shape_bytes=tb.shape([]), content=b"\x00"
    )
    data = tb.records(
        tb.event(summary_bytes=tb.summary(tb.value(tag=b"a", histogram=b""))),
        tb.scalar_event(b"a", 1.0, step=2),
        tb.event(summary_bytes=tb.summary(_tensor_value(unsupported, tag=b"a"))),
        tb.scalar_event(b"b", 2.0, step=3),
    )
    assert list(iter_supported_scalars(data)) == [
        ScalarRecord(b"a", 2, 1.0, True),
        ScalarRecord(b"b", 3, 2.0, True),
    ]


# --- selection -------------------------------------------------------------


def test_absent_step_is_zero():
    data = tb.records(
        tb.event(summary_bytes=tb.summary(tb.value(tag=b"x", simple_value=0.5)))
    )
    assert _scan_one(data, step=0).value == 0.5


def test_negative_step_is_matched_exactly():
    data = tb.records(tb.scalar_event(b"x", 0.5, step=-5))
    assert _scan_one(data, step=-5).value == 0.5
    assert _scan_one(data, step=5).outcome is ScalarOutcome.mismatch


def test_duplicate_tag_step_occurrences_zero_and_one():
    data = tb.records(
        tb.scalar_event(b"x", 1.0, step=7),
        tb.scalar_event(b"x", 2.0, step=7),
        tb.scalar_event(b"x", 3.0, step=8),
    )
    request0 = (b"x", 7, 0)
    request1 = (b"x", 7, 1)
    results = scan_scalars(data, [request0, request1])
    assert results[request0].value == 1.0
    assert results[request1].value == 2.0


def test_occurrence_beyond_supported_count_is_mismatch():
    data = tb.records(
        tb.scalar_event(b"x", 1.0),
        tb.scalar_event(b"x", 2.0),
    )
    assert _scan_one(data, occurrence=2).outcome is ScalarOutcome.mismatch
    assert _scan_one(data, occurrence=2).value is None


def test_only_a_later_occurrence_requested_still_counts_earlier_ones():
    data = tb.records(
        tb.scalar_event(b"x", 1.0),
        tb.scalar_event(b"x", 2.0),
        tb.scalar_event(b"x", 3.0),
    )
    assert _scan_one(data, occurrence=1).value == 2.0
    assert _scan_one(data, occurrence=2).value == 3.0


def test_multiple_tags_and_steps_do_not_cross_contaminate():
    data = tb.records(
        tb.scalar_event(b"a", 1.0, step=0),
        tb.scalar_event(b"a", 2.0, step=1),
        tb.scalar_event(b"b", 3.0, step=0),
    )
    requests = [(b"a", 0, 0), (b"a", 1, 0), (b"b", 0, 0), (b"b", 1, 0)]
    results = scan_scalars(data, requests)
    assert results[(b"a", 0, 0)].value == 1.0
    assert results[(b"a", 1, 0)].value == 2.0
    assert results[(b"b", 0, 0)].value == 3.0
    assert results[(b"b", 1, 0)].outcome is ScalarOutcome.mismatch


def test_never_seen_tag_or_step_is_mismatch():
    data = tb.records(tb.scalar_event(b"x", 0.5))
    assert _scan_one(data, tag=b"y").outcome is ScalarOutcome.mismatch
    assert _scan_one(data, tag=b"x", step=1).outcome is ScalarOutcome.mismatch


def test_tags_are_exact_bytes_not_case_folded_or_trimmed():
    data = tb.records(
        tb.scalar_event(b"loss", 1.0),
        tb.scalar_event(b"Loss", 2.0),
        tb.scalar_event(b"loss\x00", 3.0),
    )
    assert _scan_one(data, tag=b"loss").value == 1.0
    assert _scan_one(data, tag=b"Loss").value == 2.0
    assert _scan_one(data, tag=b"loss\x00").value == 3.0
    assert _scan_one(data, tag=b"LOSS").outcome is ScalarOutcome.mismatch


def test_long_and_invalid_utf8_event_tags_are_unmatchable():
    data = tb.records(
        tb.scalar_event(b"loss" + b"\xff", 1.0),
        tb.scalar_event(b"loss" + b"\x00", 2.0),
        tb.scalar_event(b"loss" * 2000, 3.0),
        tb.scalar_event(b"\xff\xfe", 4.0),
    )
    assert _scan_one(data, tag=b"loss").outcome is ScalarOutcome.mismatch
    assert list(iter_supported_scalars(data)) == [
        ScalarRecord(b"loss\xff", 0, 1.0, True),
        ScalarRecord(b"loss\x00", 0, 2.0, True),
        ScalarRecord(b"loss" * 2000, 0, 3.0, True),
        ScalarRecord(b"\xff\xfe", 0, 4.0, True),
    ]


def test_empty_request_set_returns_empty_mapping():
    assert scan_scalars(tb.records(tb.scalar_event(b"x", 0.5)), []) == {}


def test_request_tag_must_be_bytes():
    with pytest.raises(TypeError):
        scan_scalars(tb.records(tb.scalar_event(b"x", 0.5)), [("x", 0, 0)])


# --- numbers ---------------------------------------------------------------


def test_compare_tensorboard_float32_tolerance():
    assert compare_tensorboard(1.0, 1.0 + 1e-6, float32=True) is True
    assert compare_tensorboard(1.0, 1.0 + 1.1e-6, float32=True) is False
    assert compare_tensorboard(1.0, 1.0 + 5e-7, float32=True) is True
    # The absolute tolerance applies near zero for both origins.
    assert compare_tensorboard(0.0, 0.5e-12, float32=True) is True
    assert compare_tensorboard(0.0, 1.5e-12, float32=True) is False


def test_compare_tensorboard_float64_tolerance_and_origin_difference():
    assert compare_tensorboard(2.0, 2.0 + 1e-9, float32=False) is True
    assert compare_tensorboard(2.0, 2.0 + 2e-9, float32=False) is False
    assert compare_tensorboard(0.0, 0.5e-12, float32=False) is True
    assert compare_tensorboard(0.0, 1.5e-12, float32=False) is False
    # 1e-7 matches only the float32-origin tolerance.
    assert compare_tensorboard(1.0, 1.0 + 1e-7, float32=True) is True
    assert compare_tensorboard(1.0, 1.0 + 1e-7, float32=False) is False


def test_compare_tensorboard_integer_recorded_values():
    assert compare_tensorboard(1, 1.0, float32=False) is True
    assert compare_tensorboard(1, 1.0000005, float32=True) is True
    assert compare_tensorboard(1, 1.000002, float32=True) is False
    assert compare_tensorboard(2**53, float(2**53), float32=True) is True
    assert compare_tensorboard(2**53, float(2**53) + 1e10, float32=True) is False
    assert compare_tensorboard(10**400, 1.0, float32=True) is False
    assert compare_tensorboard(10**400, 1e300, float32=False) is False
    assert compare_tensorboard(10**400, float("inf"), float32=True) is False
    # The OverflowError fallback can also match exactly.
    assert compare_tensorboard(10**400, 10**400, float32=False) is True


def test_compare_tensorboard_nan_and_infinities():
    nan = float("nan")
    assert compare_tensorboard(nan, nan, float32=True) is True
    assert compare_tensorboard(nan, 1.0, float32=True) is False
    assert compare_tensorboard(1.0, nan, float32=False) is False
    assert compare_tensorboard(float("inf"), float("inf"), float32=True) is True
    assert compare_tensorboard(float("-inf"), float("-inf"), float32=True) is True
    assert compare_tensorboard(float("inf"), float("-inf"), float32=True) is False
    assert compare_tensorboard(float("inf"), 1e308, float32=False) is False
    assert compare_tensorboard(1.0, float("inf"), float32=False) is False
    assert compare_tensorboard(0, float("inf"), float32=True) is False


@pytest.mark.parametrize("recorded", ["1.0", True, False, None, [1.0], {"v": 1.0}])
def test_compare_tensorboard_unsupported_recorded_types(recorded):
    assert compare_tensorboard(recorded, 1.0, float32=True) is None


def test_compare_values_stays_at_one_e_minus_nine():
    assert compare_values(2.0, 2.0 + 1e-9) is True
    assert compare_values(2.0, 2.0 + 2e-9) is False
    assert compare_values(1.0, 1.0 + 1e-7) is False
    assert compare_values(1.0, 1.0 + 1e-10) is True
    assert compare_values(0.0, 0.5e-12) is True
    assert compare_values(0.0, 1.5e-12) is False


# --- merged summary cap and losing members --------------------------------


def test_summary_cap_boundary_is_inclusive():
    accepted = tb.records(tb.event(summary_bytes=tb.padding_summary(MAX_SUMMARY_BYTES)))
    assert list(iter_supported_scalars(accepted)) == []
    refused = tb.records(
        tb.event(summary_bytes=tb.padding_summary(MAX_SUMMARY_BYTES + 1))
    )
    _assert_refused(refused)


def test_fifteen_mib_then_file_version_then_fifteen_mib_is_accepted():
    first = b"\xff" * (15 * MIB)  # losing and deliberately not valid protobuf
    winning_value = tb.summary(tb.value(tag=b"x", simple_value=0.5))
    winning = winning_value + tb.padding_summary(15 * MIB - len(winning_value))
    assert len(winning) == 15 * MIB
    # All three fields are in one Event; file_version is a different `what`
    # member that discards the first summary from the merged region.
    data = tb.records(
        tb.varint_field(2, 2)
        + tb.bytes_field(5, first)
        + tb.bytes_field(3, b"tensorboard")
        + tb.bytes_field(5, winning)
    )
    assert list(iter_supported_scalars(data)) == [
        ScalarRecord(b"x", 2, 0.5, True)
    ]


def test_adjacent_fifteen_plus_fifteen_mib_is_refused():
    one = tb.padding_summary(15 * MIB)
    data = tb.records(
        tb.varint_field(2, 0) + tb.bytes_field(5, one) + tb.bytes_field(5, one)
    )
    _assert_refused(data)


def test_seventeen_mib_losing_summary_then_graph_def_is_accepted():
    data = tb.records(
        tb.bytes_field(5, b"\xff" * (17 * MIB)) + tb.bytes_field(4, b"graph")
    )
    assert list(iter_supported_scalars(data)) == []
    # A later winning summary is still parsed normally.
    data += tb.record(tb.scalar_event(b"x", 0.5))
    assert list(iter_supported_scalars(data)) == [ScalarRecord(b"x", 0, 0.5, True)]


# --- memory vectors and fuzz ----------------------------------------------


_MEASURE_SCRIPT = r"""
import json
import sys
import time

from runevidence.tensorboard_events import scan_scalars

TAG = %r
STEP = %d
OCCURRENCE = %d


def field_kb(name):
    with open("/proc/self/status", "r", encoding="ascii") as handle:
        for line in handle:
            if line.startswith(name + ":"):
                return int(line.split()[1])
    raise SystemExit("missing " + name)


baseline = field_kb("VmRSS")
data = sys.stdin.buffer.read()
started = time.perf_counter()
request = (TAG, STEP, OCCURRENCE)
selection = scan_scalars(data, [request])[request]
elapsed = time.perf_counter() - started
peak = field_kb("VmHWM")
print(
    json.dumps(
        {
            "baseline_kb": baseline,
            "peak_kb": peak,
            "delta_mb": (peak - baseline) / 1024.0,
            "seconds": elapsed,
            "outcome": selection.outcome.value,
            "value": selection.value,
        }
    )
)
"""


def _limit_address_space() -> None:
    import resource

    limit = 2_500_000 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))


def _measure_scan(data: bytes, tag: bytes, step: int, occurrence: int) -> dict:
    script = _MEASURE_SCRIPT % (tag, step, occurrence)
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPO / "src") + os.pathsep + environment.get(
        "PYTHONPATH", ""
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        input=data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=300,
        check=False,
        cwd=REPO,
        env=environment,
        preexec_fn=_limit_address_space,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    return json.loads(completed.stdout)


linux_only = pytest.mark.skipif(
    platform.system() != "Linux",
    reason="VmHWM measurement and the 300 MB budget assertion are Linux-only",
)


@linux_only
def test_sixteen_mib_summary_of_84m_empty_values_is_within_budget():
    summary_payload = tb.bytes_field(1, b"") * (MAX_SUMMARY_BYTES // 2)
    assert len(summary_payload) == MAX_SUMMARY_BYTES
    data = tb.records(tb.event(summary_bytes=summary_payload))
    measured = _measure_scan(data, b"absent", 0, 0)
    assert measured["delta_mb"] <= 300, measured
    assert measured["outcome"] == "mismatch"


@linux_only
def test_sixteen_mib_summary_of_16m_tagged_scalars_is_within_budget():
    entry = tb.bytes_field(1, tb.value(tag=b"x", simple_value=0.5))
    assert len(entry) == 10
    summary_payload = entry * (MAX_SUMMARY_BYTES // len(entry))
    assert len(summary_payload) <= MAX_SUMMARY_BYTES
    data = tb.records(tb.event(summary_bytes=summary_payload))
    occurrence = 1_500_000
    measured = _measure_scan(data, b"x", 0, occurrence)
    assert measured["delta_mb"] <= 300, measured
    assert measured["outcome"] == "value"
    assert measured["value"] == 0.5


def _fuzz_seed() -> bytes:
    content = tb.tensor(
        dtype=1,
        shape_bytes=tb.shape([1]),
        content=struct.pack("<f", 1.5),
    )
    return tb.records(
        tb.scalar_event(b"loss", 0.5, step=0),
        tb.event(
            wall_time=1.0,
            step=3,
            summary_bytes=tb.summary(
                tb.value(tag=b"loss", simple_value=0.75),
                tb.value(tag=b"tensor", tensor_bytes=content),
                tb.value(tag=b"hist", histogram=b"\x00\x01"),
            ),
        ),
        tb.event(file_version=b"tensorboard"),
    )


def test_seeded_mutation_fuzz_only_refuses_or_returns_valid_results():
    rng = random.Random(0)
    seed = _fuzz_seed()
    requests = [
        (b"loss", 0, 0),
        (b"loss", 3, 0),
        (b"tensor", 3, 0),
        (b"hist", 3, 0),
        (b"absent", 0, 0),
    ]
    started = time.perf_counter()
    for _ in range(2000):
        data = bytearray(seed)
        operation = rng.randrange(5)
        if operation == 0:
            index = rng.randrange(len(data))
            data[index] ^= 1 << rng.randrange(8)
        elif operation == 1:
            index = rng.randrange(len(data))
            data[index] = rng.randrange(256)
        elif operation == 2 and len(data) > 1:
            del data[rng.randrange(len(data))]
        elif operation == 3:
            data.insert(rng.randrange(len(data) + 1), rng.randrange(256))
        else:
            index = rng.randrange(len(data))
            data[index : index + 2] = bytes(
                (rng.randrange(256), rng.randrange(256))
            )
        try:
            records = list(iter_supported_scalars(data))
            assert all(isinstance(record, ScalarRecord) for record in records)
            results = scan_scalars(data, requests)
            assert set(results) == set(requests)
            assert all(
                selection.outcome in set(ScalarOutcome) for selection in results.values()
            )
        except EventFileError as error:
            assert str(error) == REFUSAL
    elapsed = time.perf_counter() - started
    assert elapsed < 30, elapsed
