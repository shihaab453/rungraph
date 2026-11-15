"""Dependency-free strict reader for TensorBoard event files.

The profile is the one specified by the slice D design RFC (sections 5.1-5.4):
TFRecord framing with masked CRC32C, a bounded protobuf subset, and scalar
classification for rank-0 or rank-1-with-one-dimension-1 float/double tensors
plus the legacy ``simple_value``.  Only the standard library is used.

The parser is deliberately strict.  Every malformed framing or protobuf shape
raises :class:`EventFileError` with one fixed message; parser internals (CRC,
protobuf, offsets, OS text) never leak into a finding.  A structural refusal
applies to the whole file, and the caller maps it to one locator outcome per
kind.

Public API (stable for the checker and the optional adapter):

``masked_crc32c(data)``
    The TFRecord masked CRC32C of ``data``.
``iter_supported_scalars(data)``
    Yield every supported scalar in file order (record order, then
    ``Summary.Value`` order) as :class:`ScalarRecord`.
``scan_scalars(data, requests)``
    One pass over the file selecting requested ``(tag_bytes, step,
    occurrence)`` triples.  Retained state is O(requested): one counter per
    requested ``(tag, step)`` and one value per requested occurrence.
``compare_tensorboard``
    Lives next to ``compare_values`` in :mod:`runevidence.locator_profiles`
    because that module owns the delivered scalar comparators.

All byte-taking functions accept ``bytes`` or ``bytearray``.  They never
require a ``memoryview``, never copy the whole input, and never retain slices
of it.  The merged ``summary`` cap uses O(1) state only.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Iterator, NoReturn

__all__ = [
    "EventFileError",
    "MAX_SUMMARY_BYTES",
    "ScalarOutcome",
    "ScalarRecord",
    "ScalarSelection",
    "iter_supported_scalars",
    "masked_crc32c",
    "scan_scalars",
]

#: The merged ``Summary`` payload cap for one Event: the winning ``summary``
#: occurrence payload bytes after the last other ``what`` member.
MAX_SUMMARY_BYTES = 16 * 1024 * 1024

#: Castagnoli polynomial, reflected, as used by CRC32C.
_CRC32C_POLYNOMIAL = 0x82F63B78
_CRC32C_MASK_DELTA = 0xA282EAD8
_U32 = struct.Struct("<I")
_U64 = struct.Struct("<Q")
_F32 = struct.Struct("<f")
_F64 = struct.Struct("<d")

_FIELD_NUMBER_LIMIT = (1 << 29) - 1
_MAX_VARINT_BYTES = 10

# Refusal categories are internal constants; ``_classify`` returns one of
# them.  Only the public outcome enum below is visible to callers.
_SUPPORTED = 1
_UNSUPPORTED_SCALAR = 2
_NON_SCALAR = 3


class EventFileError(ValueError):
    """The bytes do not satisfy the strict TensorBoard event profile."""


def _refuse() -> NoReturn:
    """Raise the fixed structural refusal, never parser or CRC text."""
    raise EventFileError("strict TensorBoard event profile refusal") from None


def _build_crc32c_table() -> tuple[int, ...]:
    table = []
    for index in range(256):
        crc = index
        for _ in range(8):
            crc = (crc >> 1) ^ _CRC32C_POLYNOMIAL if crc & 1 else crc >> 1
        table.append(crc)
    return tuple(table)


def _build_crc32c_tables(base: tuple[int, ...]) -> tuple[tuple[int, ...], ...]:
    """Derive slicing-by-8 tables once at import time.

    The byte-at-a-time loop is correct but slow for a 128 MiB file.  Slicing by
    eight consumes eight bytes per Python step with the same table-driven
    algorithm, so the cost of the mandatory CRC does not approach the RFC's
    wall-clock ceiling.
    """
    tables = [base]
    for _ in range(7):
        previous = tables[-1]
        tables.append(
            tuple((value >> 8) ^ base[value & 0xFF] for value in previous)
        )
    return tuple(tables)


_CRC32C_TABLES = _build_crc32c_tables(_build_crc32c_table())


def _crc32c(data: bytes | bytearray, start: int, end: int) -> int:
    """CRC32C (Castagnoli) of ``data[start:end]``, no slice copy."""
    tables = _CRC32C_TABLES
    table0 = tables[0]
    crc = 0xFFFFFFFF
    position = start
    unpack = _U32.unpack_from
    while position + 8 <= end:
        crc ^= unpack(data, position)[0]
        crc = (
            tables[7][crc & 0xFF]
            ^ tables[6][(crc >> 8) & 0xFF]
            ^ tables[5][(crc >> 16) & 0xFF]
            ^ tables[4][(crc >> 24) & 0xFF]
            ^ tables[3][data[position + 4]]
            ^ tables[2][data[position + 5]]
            ^ tables[1][data[position + 6]]
            ^ table0[data[position + 7]]
        )
        position += 8
    while position < end:
        crc = table0[(crc ^ data[position]) & 0xFF] ^ (crc >> 8)
        position += 1
    return crc ^ 0xFFFFFFFF


def _mask_crc(crc: int) -> int:
    return (((crc >> 15) | (crc << 17)) + _CRC32C_MASK_DELTA) & 0xFFFFFFFF


def masked_crc32c(data: bytes | bytearray) -> int:
    """Return the TFRecord masked CRC32C of ``data``."""
    return _mask_crc(_crc32c(data, 0, len(data)))


class ScalarOutcome(str, Enum):
    """The selection result for one requested ``(tag, step, occurrence)``.

    ``value`` is a supported scalar at the requested occurrence; ``mismatch``
    means no such occurrence was found; ``unsupported_scalar`` and
    ``unsupported_value`` are the two section 5.3 unsupported reasons.
    """

    value = "value"
    mismatch = "mismatch"
    unsupported_scalar = "unsupported-scalar"
    unsupported_value = "unsupported-value"


@dataclass(frozen=True, slots=True)
class ScalarRecord:
    """One supported TensorBoard scalar.

    ``tag`` is the exact event tag bytes (never decoded or UTF-8 validated),
    ``step`` is the signed Event step (an absent step is 0), and ``float32`` is
    true when the value originated from a float32 encoding (``simple_value`` or
    ``DT_FLOAT``) rather than ``DT_DOUBLE``.
    """

    tag: bytes
    step: int
    value: float
    float32: bool


@dataclass(frozen=True, slots=True)
class ScalarSelection:
    """The outcome of one request, with a value only for ``value``."""

    outcome: ScalarOutcome
    value: float | None = None
    float32: bool = False


# --- protobuf primitives ----------------------------------------------------


def _read_varint(data: bytes | bytearray, position: int, end: int) -> tuple[int, int]:
    """Read one varint bounded by ``end``.

    At most ten bytes; the tenth byte must be ``<= 0x01`` so a 64-bit value
    fits.  Non-minimal encodings are accepted when they fit.  This is also the
    whole key varint: the field-number and wire-type checks happen in
    ``_read_key``.
    """
    result = 0
    shift = 0
    for index in range(_MAX_VARINT_BYTES):
        if position >= end:
            _refuse()
        byte = data[position]
        position += 1
        if index == 9:
            if byte > 0x01:
                _refuse()
            return result | (byte << 63), position
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            return result, position
        shift += 7
    _refuse()  # pragma: no cover - the tenth byte always terminates or refuses


def _read_key(
    data: bytes | bytearray, position: int, end: int
) -> tuple[int, int, int]:
    """Read and validate a field key: 29-bit field number and wire type."""
    key, position = _read_varint(data, position, end)
    field_number = key >> 3
    wire_type = key & 0x07
    if field_number == 0 or field_number > _FIELD_NUMBER_LIMIT:
        _refuse()
    if wire_type not in (0, 1, 2, 5):
        _refuse()
    return field_number, wire_type, position


def _read_length(
    data: bytes | bytearray, position: int, end: int
) -> tuple[int, int]:
    """Read a length delimiter and require the payload to fit ``end``."""
    length, position = _read_varint(data, position, end)
    if length > end - position:
        _refuse()
    return position, position + length


def _skip_field(
    data: bytes | bytearray, position: int, end: int, wire_type: int
) -> int:
    """Skip an unknown field payload by its already-validated wire type."""
    if wire_type == 0:
        _, position = _read_varint(data, position, end)
        return position
    if wire_type == 1:
        if end - position < 8:
            _refuse()
        return position + 8
    if wire_type == 5:
        if end - position < 4:
            _refuse()
        return position + 4
    # wire_type == 2; _read_key already refused groups and reserved types.
    _, payload_end = _read_length(data, position, end)
    return payload_end


def _signed_int64(value: int) -> int:
    return value - (1 << 64) if value >= (1 << 63) else value


# --- TFRecord framing -------------------------------------------------------


def _iter_records(
    data: bytes | bytearray,
) -> Iterator[tuple[int, int]]:
    """Yield ``(payload_start, payload_end)`` for every non-empty record.

    Zero-length records are valid empty Events and are skipped.  Empty files
    and files whose only record is zero-length are valid with zero records.
    There is no per-record byte cap; the caller owns the file-level cap.
    """
    total = len(data)
    position = 0
    while position < total:
        if total - position < 12:
            _refuse()
        length = _U64.unpack_from(data, position)[0]
        length_crc = _U32.unpack_from(data, position + 8)[0]
        if length > total - position - 12:
            _refuse()
        payload_start = position + 12
        payload_end = payload_start + length
        if total - payload_end < 4:
            _refuse()
        if _mask_crc(_crc32c(data, position, position + 8)) != length_crc:
            _refuse()
        if _mask_crc(_crc32c(data, payload_start, payload_end)) != _U32.unpack_from(
            data, payload_end
        )[0]:
            _refuse()
        position = payload_end + 4
        if length:
            yield payload_start, payload_end


# --- TensorProto / TensorShapeProto ----------------------------------------


class _TensorState:
    """Bounded TensorProto accumulator.

    Only what scalar classification needs is retained: dtype, a dimension
    count plus the first dimension's size, unknown_rank, the last
    ``tensor_content`` offsets, and the float/double value count plus the first
    value.  A huge tensor never allocates a value or dimension list.
    """

    __slots__ = (
        "dtype",
        "dim_count",
        "first_dim_size",
        "unknown_rank",
        "content_start",
        "content_end",
        "float_count",
        "float_value",
        "double_count",
        "double_value",
        "string_count",
        "string_start",
        "string_end",
        "string_wrong_wire",
        "other_typed",
    )

    def __init__(self) -> None:
        self.dtype = 0
        self.dim_count = 0
        self.first_dim_size = 0
        self.unknown_rank = False
        self.content_start = -1
        self.content_end = -1
        self.float_count = 0
        self.float_value = 0.0
        self.double_count = 0
        self.double_value = 0.0
        self.string_count = 0
        self.string_start = -1
        self.string_end = -1
        self.string_wrong_wire = False
        self.other_typed = 0


def _parse_dimension(data: bytes | bytearray, start: int, end: int) -> int:
    """Parse one ``TensorShapeProto.Dim``; return its last-wins size."""
    size = 0
    position = start
    while position < end:
        field_number, wire_type, position = _read_key(data, position, end)
        if field_number == 1:  # size
            if wire_type != 0:
                _refuse()
            value, position = _read_varint(data, position, end)
            size = _signed_int64(value)
        elif field_number == 2:  # name; ignored but validated
            if wire_type != 2:
                _refuse()
            _, position = _read_length(data, position, end)
        else:
            position = _skip_field(data, position, end, wire_type)
    return size


def _parse_shape(
    data: bytes | bytearray, start: int, end: int, state: _TensorState
) -> None:
    """Merge one ``TensorShapeProto`` into ``state`` (dim lists append)."""
    position = start
    while position < end:
        field_number, wire_type, position = _read_key(data, position, end)
        if field_number == 2:  # dim
            if wire_type != 2:
                _refuse()
            dim_start, dim_end = _read_length(data, position, end)
            size = _parse_dimension(data, dim_start, dim_end)
            state.dim_count += 1
            if state.dim_count == 1:
                state.first_dim_size = size
            position = dim_end
        elif field_number == 3:  # unknown_rank
            if wire_type != 0:
                _refuse()
            value, position = _read_varint(data, position, end)
            state.unknown_rank = value != 0
        else:
            position = _skip_field(data, position, end, wire_type)


def _parse_tensor(
    data: bytes | bytearray,
    start: int,
    end: int,
    state: _TensorState,
    *,
    strings: bool = False,
) -> None:
    """Merge one ``TensorProto`` into ``state`` with the RFC's last-wins rules."""
    position = start
    while position < end:
        field_number, wire_type, position = _read_key(data, position, end)
        if field_number == 1:  # dtype, last-wins
            if wire_type != 0:
                _refuse()
            state.dtype, position = _read_varint(data, position, end)
        elif field_number == 2:  # tensor_shape, message merge
            if wire_type != 2:
                _refuse()
            shape_start, shape_end = _read_length(data, position, end)
            _parse_shape(data, shape_start, shape_end, state)
            position = shape_end
        elif field_number == 4:  # tensor_content, singular bytes, last-wins
            if wire_type != 2:
                _refuse()
            content_start, content_end = _read_length(data, position, end)
            state.content_start = content_start
            state.content_end = content_end
            position = content_end
        elif field_number == 5:  # float_val, packed wire 2 or unpacked wire 5
            if wire_type == 2:
                values_start, values_end = _read_length(data, position, end)
                values_length = values_end - values_start
                if values_length % 4:
                    _refuse()
                count = values_length // 4
                if count and state.float_count == 0:
                    state.float_value = _F32.unpack_from(data, values_start)[0]
                state.float_count += count
                position = values_end
            elif wire_type == 5:
                if end - position < 4:
                    _refuse()
                if state.float_count == 0:
                    state.float_value = _F32.unpack_from(data, position)[0]
                state.float_count += 1
                position += 4
            else:
                _refuse()
        elif field_number == 6:  # double_val, packed wire 2 or unpacked wire 1
            if wire_type == 2:
                values_start, values_end = _read_length(data, position, end)
                values_length = values_end - values_start
                if values_length % 8:
                    _refuse()
                count = values_length // 8
                if count and state.double_count == 0:
                    state.double_value = _F64.unpack_from(data, values_start)[0]
                state.double_count += count
                position = values_end
            elif wire_type == 1:
                if end - position < 8:
                    _refuse()
                if state.double_count == 0:
                    state.double_value = _F64.unpack_from(data, position)[0]
                state.double_count += 1
                position += 8
            else:
                _refuse()
        elif field_number == 8 and strings:  # string_val, repeated bytes
            if wire_type == 2:
                value_start, value_end = _read_length(data, position, end)
                if state.string_count == 0:
                    state.string_start = value_start
                    state.string_end = value_end
                state.string_count += 1
                position = value_end
            elif wire_type in (0, 1, 5):
                state.string_wrong_wire = True
                position = _skip_field(data, position, end, wire_type)
            else:
                _refuse()
        elif strings and field_number in (
            7,
            9,
            10,
            11,
            12,
            13,
            14,
            15,
            16,
            17,
            18,
        ):  # typed values that are not part of the string profile
            if wire_type == 2:
                value_start, value_end = _read_length(data, position, end)
                if value_end > value_start:
                    state.other_typed += 1
                position = value_end
            elif wire_type in (0, 1, 5):
                state.other_typed += 1
                position = _skip_field(data, position, end, wire_type)
            else:
                _refuse()
        else:
            position = _skip_field(data, position, end, wire_type)

# --- Summary.Value / Event --------------------------------------------------


def _scan_tensor_region(
    data: bytes | bytearray,
    start: int,
    end: int,
    state: _TensorState,
    *,
    strings: bool = False,
) -> None:
    """Decode winning ``tensor`` occurrences after the last different member.

    Losing oneof payloads are never decoded.  The first pass checks only their
    key, wire type and outer bounds, exactly as the RFC requires.
    """
    position = start
    while position < end:
        field_number, wire_type, position = _read_key(data, position, end)
        if field_number == 8:
            if wire_type != 2:
                _refuse()
            tensor_start, tensor_end = _read_length(data, position, end)
            _parse_tensor(data, tensor_start, tensor_end, state, strings=strings)
            position = tensor_end
        else:
            position = _skip_field(data, position, end, wire_type)


def _parse_value(
    data: bytes | bytearray,
    start: int,
    end: int,
    step: int,
    *,
    strings: bool = False,
):
    """Parse one ``Summary.Value`` and return its final oneof state.

    The return is ``(tag_start, tag_end, step, kind, simple_value, tensor)``.
    ``tag_start``/``tag_end`` are ``-1`` when no tag field was encoded; the
    protobuf default is then the empty tag.  ``kind`` is the numeric field
    number of the winning oneof member (0 when absent), ``simple_value`` is the
    last encoded fixed32 and ``tensor`` is a parsed ``_TensorState`` for a
    winning tensor and ``None`` otherwise.
    """
    tag_start = -1
    tag_end = -1
    kind = 0
    simple_value = 0.0
    # Every winning tensor occurrence is at or after the end of the last
    # different oneof member (tag/metadata/unknown fields do not reset it).
    tensor_restart = start
    position = start
    while position < end:
        field_number, wire_type, position = _read_key(data, position, end)
        if field_number == 1:  # tag, singular bytes, last-wins
            if wire_type != 2:
                _refuse()
            tag_start, tag_end = _read_length(data, position, end)
            position = tag_end
        elif field_number == 2:  # simple_value, oneof member, last-wins
            if wire_type != 5:
                _refuse()
            if end - position < 4:
                _refuse()
            simple_value = _F32.unpack_from(data, position)[0]
            kind = 2
            tensor_restart = position + 4
            position += 4
        elif field_number in (3, 4, 5, 6):  # histogram/image/histo/audio
            if wire_type != 2:
                _refuse()
            _, payload_end = _read_length(data, position, end)
            kind = field_number
            tensor_restart = payload_end
            position = payload_end
        elif field_number == 7:  # node_name, ignored but wire-checked
            if wire_type != 2:
                _refuse()
            _, position = _read_length(data, position, end)
        elif field_number == 8:  # tensor, oneof member, message merge
            if wire_type != 2:
                _refuse()
            _, payload_end = _read_length(data, position, end)
            # The payload is decoded later only if this member wins.
            kind = 8
            position = payload_end
        elif field_number == 9:  # metadata, ignored
            if wire_type != 2:
                _refuse()
            _, position = _read_length(data, position, end)
        else:
            position = _skip_field(data, position, end, wire_type)

    tensor = None
    if kind == 8:
        tensor = _TensorState()
        _scan_tensor_region(data, tensor_restart, end, tensor, strings=strings)
    return tag_start, tag_end, step, kind, simple_value, tensor


def _iter_summary_values(
    data: bytes | bytearray,
    start: int,
    end: int,
    step: int,
    *,
    strings: bool = False,
):
    """Yield parsed values from one winning ``Summary`` message."""
    position = start
    while position < end:
        field_number, wire_type, position = _read_key(data, position, end)
        if field_number == 1:  # value, repeated
            if wire_type != 2:
                _refuse()
            value_start, value_end = _read_length(data, position, end)
            yield _parse_value(
                data, value_start, value_end, step, strings=strings
            )
            position = value_end
        else:
            position = _skip_field(data, position, end, wire_type)


def _iter_winning_summaries(
    data: bytes | bytearray,
    start: int,
    end: int,
    step: int,
    *,
    strings: bool = False,
):
    """Find winning ``summary`` members in the Event tail and yield their values.

    ``start`` is the end of the last non-``summary`` ``what`` member (or the
    Event payload start), so every ``summary`` field found here is a winning
    occurrence and their payloads merge in wire order.
    """
    position = start
    while position < end:
        field_number, wire_type, position = _read_key(data, position, end)
        if field_number == 5:  # summary
            if wire_type != 2:
                _refuse()
            summary_start, summary_end = _read_length(data, position, end)
            yield from _iter_summary_values(
                data, summary_start, summary_end, step, strings=strings
            )
            position = summary_end
        else:
            position = _skip_field(data, position, end, wire_type)


def _iter_event_values(
    data: bytes | bytearray,
    start: int,
    end: int,
    *,
    strings: bool = False,
):
    """Yield every winning value from one Event record.

    The first pass tracks, in O(1) state, the offset after the last non-summary
    ``what`` member and the sum of summary payload lengths after it.  A losing
    summary is never decoded and is never checked against the 16 MiB cap; the
    cap is enforced only for the final winning summary region.
    """
    step = 0
    what = 0
    last_other_end = start
    summary_bytes = 0
    position = start
    while position < end:
        field_number, wire_type, position = _read_key(data, position, end)
        if field_number == 1:  # wall_time, ignored
            if wire_type != 1:
                _refuse()
            if end - position < 8:
                _refuse()
            position += 8
        elif field_number == 2:  # step, int64, last-wins
            if wire_type != 0:
                _refuse()
            raw, position = _read_varint(data, position, end)
            step = _signed_int64(raw)
        elif 3 <= field_number <= 9:  # what oneof
            if wire_type != 2:
                _refuse()
            payload_start, payload_end = _read_length(data, position, end)
            if field_number == 5:
                what = 5
                summary_bytes += payload_end - payload_start
            else:
                what = field_number
                last_other_end = payload_end
                summary_bytes = 0
            position = payload_end
        else:
            position = _skip_field(data, position, end, wire_type)

    if what != 5:
        return
    if summary_bytes > MAX_SUMMARY_BYTES:
        _refuse()
    yield from _iter_winning_summaries(
        data, last_other_end, end, step, strings=strings
    )


def _iter_winning_values(data: bytes | bytearray, *, strings: bool = False):
    """Yield ``(tag_start, tag_end, step, kind, simple_value, tensor)``."""
    for payload_start, payload_end in _iter_records(data):
        yield from _iter_event_values(
            data, payload_start, payload_end, strings=strings
        )


# --- classification ---------------------------------------------------------


def _content_length(state: _TensorState) -> int:
    if state.content_start < 0:
        return 0
    return state.content_end - state.content_start


def _classify(
    data: bytes | bytearray, kind: int, simple_value: float, tensor: _TensorState | None
) -> tuple[int, float, bool]:
    """Classify one value: supported, unsupported scalar, or non-scalar.

    Shape is checked before dtype or encoding.  A scalar-shaped tensor must be
    ``unknown_rank == false`` and rank-0, or rank-1 with exactly one dimension
    of size 1.
    """
    if kind == 2:
        return _SUPPORTED, simple_value, True
    if kind != 8 or tensor is None:
        return _NON_SCALAR, 0.0, False

    if tensor.unknown_rank:
        return _NON_SCALAR, 0.0, False
    if tensor.dim_count > 1 or (
        tensor.dim_count == 1 and tensor.first_dim_size != 1
    ):
        return _NON_SCALAR, 0.0, False

    dtype = tensor.dtype
    content_length = _content_length(tensor)
    if dtype == 1:  # DT_FLOAT
        if content_length == 4:
            if tensor.float_count or tensor.double_count:
                return _UNSUPPORTED_SCALAR, 0.0, False
            return _SUPPORTED, _F32.unpack_from(data, tensor.content_start)[0], True
        if content_length:
            return _UNSUPPORTED_SCALAR, 0.0, False
        if tensor.float_count == 1 and tensor.double_count == 0:
            return _SUPPORTED, tensor.float_value, True
        return _UNSUPPORTED_SCALAR, 0.0, False
    if dtype == 2:  # DT_DOUBLE
        if content_length == 8:
            if tensor.float_count or tensor.double_count:
                return _UNSUPPORTED_SCALAR, 0.0, False
            return _SUPPORTED, _F64.unpack_from(data, tensor.content_start)[0], False
        if content_length:
            return _UNSUPPORTED_SCALAR, 0.0, False
        if tensor.double_count == 1 and tensor.float_count == 0:
            return _SUPPORTED, tensor.double_value, False
        return _UNSUPPORTED_SCALAR, 0.0, False
    return _UNSUPPORTED_SCALAR, 0.0, False


def _tag_bytes(data: bytes | bytearray, start: int, end: int) -> bytes:
    if start < 0:
        return b""
    tag = data[start:end]
    return tag if type(tag) is bytes else bytes(tag)


def iter_supported_scalars(
    data: bytes | bytearray,
) -> Iterator[ScalarRecord]:
    """Yield every supported scalar in file order.

    A structural refusal raises :class:`EventFileError`; unsupported scalars
    and non-scalar values are simply not yielded.  Tags are returned as exact
    bytes, never decoded or UTF-8 validated.
    """
    for tag_start, tag_end, step, kind, simple_value, tensor in _iter_winning_values(
        data
    ):
        category, value, float32 = _classify(data, kind, simple_value, tensor)
        if category == _SUPPORTED:
            yield ScalarRecord(
                tag=_tag_bytes(data, tag_start, tag_end),
                step=step,
                value=value,
                float32=float32,
            )


class _KeyState:
    """O(1) selection state for one requested ``(tag, step)`` pair."""

    __slots__ = ("supported", "unsupported", "non_scalar", "wanted", "values")

    def __init__(self) -> None:
        self.supported = 0
        self.unsupported = False
        self.non_scalar = False
        self.wanted: set[int] = set()
        self.values: dict[int, tuple[float, bool]] = {}


def _normalise_requests(
    requests: Iterable[tuple[bytes, int, int]],
) -> tuple[
    dict[tuple[bytes, int, int], _KeyState],
    dict[bytes, dict[int, _KeyState]],
    set[int],
]:
    """Build the per-key state without retaining a request or value list."""
    states: dict[tuple[bytes, int, int], _KeyState] = {}
    by_tag: dict[bytes, dict[int, _KeyState]] = {}
    lengths: set[int] = set()
    for tag, step, occurrence in requests:
        if isinstance(tag, bytearray):
            tag = bytes(tag)
        elif not isinstance(tag, bytes):
            raise TypeError("a TensorBoard request tag must be bytes")
        by_step = by_tag.get(tag)
        if by_step is None:
            by_step = {}
            by_tag[tag] = by_step
            lengths.add(len(tag))
        state = by_step.get(step)
        if state is None:
            state = _KeyState()
            by_step[step] = state
        state.wanted.add(occurrence)
        states[(tag, step, occurrence)] = state
    return states, by_tag, lengths


def scan_scalars(
    data: bytes | bytearray,
    requests: Iterable[tuple[bytes, int, int]],
) -> dict[tuple[bytes, int, int], ScalarSelection]:
    """Select requested ``(tag_bytes, step, occurrence)`` scalars in one pass.

    The return maps every distinct request to a :class:`ScalarSelection`.
    Request tags are exact bytes; the caller supplies the UTF-8 encoding of a
    locator tag.  Only supported scalars increment occurrence; occurrence
    numbers beyond the supported count are a mismatch.  A key with no supported
    scalar reports an unsupported scalar before an unsupported non-scalar
    value, as section 5.3 requires.  Requests are never retained beyond the
    per-key counters and the requested-occurrence values.  An empty request set
    returns an empty mapping without parsing the file.
    """
    states, by_tag, lengths = _normalise_requests(requests)
    if not states:
        return {}

    for tag_start, tag_end, step, kind, simple_value, tensor in _iter_winning_values(
        data
    ):
        if tag_start < 0:
            tag_length = 0
        else:
            tag_length = tag_end - tag_start
        if tag_length not in lengths:
            continue
        if tag_start < 0:
            tag = b""
        else:
            tag = data[tag_start:tag_end]
            if type(tag) is not bytes:
                tag = bytes(tag)
        by_step = by_tag.get(tag)
        if by_step is None:
            continue
        state = by_step.get(step)
        if state is None:
            continue

        category, value, float32 = _classify(data, kind, simple_value, tensor)
        if category == _SUPPORTED:
            occurrence = state.supported
            state.supported += 1
            if occurrence in state.wanted:
                state.values[occurrence] = (value, float32)
        elif category == _UNSUPPORTED_SCALAR:
            state.unsupported = True
        else:
            state.non_scalar = True

    results: dict[tuple[bytes, int, int], ScalarSelection] = {}
    for key, state in states.items():
        occurrence = key[2]
        if occurrence >= 0 and state.supported > occurrence:
            value, float32 = state.values[occurrence]
            results[key] = ScalarSelection(ScalarOutcome.value, value, float32)
        elif state.supported:
            results[key] = ScalarSelection(ScalarOutcome.mismatch)
        elif state.unsupported:
            results[key] = ScalarSelection(ScalarOutcome.unsupported_scalar)
        elif state.non_scalar:
            results[key] = ScalarSelection(ScalarOutcome.unsupported_value)
        else:
            results[key] = ScalarSelection(ScalarOutcome.mismatch)
    return results


# --- string tensors (schema 0.6) --------------------------------------------

MAX_STRING_VALUE_BYTES = 65_536

_STRING_SUPPORTED = 0
_STRING_FAILING = 1
_STRING_OTHER = 2


@dataclass(frozen=True, slots=True)
class StringRecord:
    """One conforming TensorBoard string tensor.

    ``tag`` is the exact event tag bytes, ``step`` the signed Event step, and
    ``value`` the raw UTF-8 bytes (validated, at most 64 KiB).
    """

    tag: bytes
    step: int
    value: bytes


@dataclass(frozen=True, slots=True)
class StringSelection:
    """The outcome of one string request, with bytes only for ``value``."""

    outcome: ScalarOutcome
    value: bytes | None = None


def _classify_string(
    data: bytes | bytearray, kind: int, tensor: _TensorState | None
) -> tuple[int, bytes | None]:
    if kind != 8 or tensor is None:
        return _STRING_OTHER, None
    if tensor.unknown_rank:
        return _STRING_OTHER, None
    if tensor.dim_count > 1 or (
        tensor.dim_count == 1 and tensor.first_dim_size != 1
    ):
        return _STRING_OTHER, None
    if tensor.dtype != 7:
        return _STRING_OTHER, None
    if (
        _content_length(tensor)
        or tensor.float_count
        or tensor.double_count
        or tensor.other_typed
        or tensor.string_count != 1
        or tensor.string_wrong_wire
    ):
        return _STRING_FAILING, None
    raw = data[tensor.string_start : tensor.string_end]
    if type(raw) is not bytes:
        raw = bytes(raw)
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        return _STRING_FAILING, None
    if len(raw) > MAX_STRING_VALUE_BYTES:
        return _STRING_FAILING, None
    return _STRING_SUPPORTED, raw


def iter_supported_strings(data: bytes | bytearray) -> Iterator[StringRecord]:
    """Yield every conforming string tensor in file order."""
    for tag_start, tag_end, step, kind, _simple, tensor in _iter_winning_values(
        data, strings=True
    ):
        category, value = _classify_string(data, kind, tensor)
        if category == _STRING_SUPPORTED:
            yield StringRecord(
                tag=_tag_bytes(data, tag_start, tag_end), step=step, value=value
            )


def iter_supported_summaries(
    data: bytes | bytearray,
) -> Iterator[ScalarRecord | StringRecord]:
    """Yield supported scalars and strings in one pass, in record order."""
    for tag_start, tag_end, step, kind, simple_value, tensor in _iter_winning_values(data, strings=True):
        category, value, float32 = _classify(data, kind, simple_value, tensor)
        if category == _SUPPORTED:
            yield ScalarRecord(
                tag=_tag_bytes(data, tag_start, tag_end),
                step=step,
                value=value,
                float32=float32,
            )
        string_category, raw = _classify_string(data, kind, tensor)
        if string_category == _STRING_SUPPORTED:
            yield StringRecord(
                tag=_tag_bytes(data, tag_start, tag_end), step=step, value=raw
            )


def scan_tensorboard_06(
    data: bytes | bytearray,
    scalar_requests: Iterable[tuple[bytes, int, int]],
    string_requests: Iterable[tuple[bytes, int, int]],
) -> tuple[
    dict[tuple[bytes, int, int], ScalarSelection],
    dict[tuple[bytes, int, int], StringSelection],
]:
    """Select scalar and string requests in one traversal."""
    scalar_states, scalar_by_tag, scalar_lengths = _normalise_requests(
        scalar_requests
    )
    string_states, string_by_tag, string_lengths = _normalise_requests(
        string_requests
    )
    if not scalar_states and not string_states:
        return {}, {}

    for tag_start, tag_end, step, kind, simple_value, tensor in _iter_winning_values(data, strings=True):
        tag_length = 0 if tag_start < 0 else tag_end - tag_start
        tag = b"" if tag_start < 0 else data[tag_start:tag_end]
        if type(tag) is not bytes:
            tag = bytes(tag)

        scalar_state = None
        if tag_length in scalar_lengths:
            by_step = scalar_by_tag.get(tag)
            if by_step is not None:
                scalar_state = by_step.get(step)
        if scalar_state is not None:
            category, value, float32 = _classify(
                data, kind, simple_value, tensor
            )
            if category == _SUPPORTED:
                occurrence = scalar_state.supported
                scalar_state.supported += 1
                if occurrence in scalar_state.wanted:
                    scalar_state.values[occurrence] = (value, float32)
            elif category == _UNSUPPORTED_SCALAR:
                scalar_state.unsupported = True
            else:
                scalar_state.non_scalar = True

        string_state = None
        if tag_length in string_lengths:
            by_step = string_by_tag.get(tag)
            if by_step is not None:
                string_state = by_step.get(step)
        if string_state is not None:
            category, value = _classify_string(data, kind, tensor)
            if category == _STRING_SUPPORTED:
                occurrence = string_state.supported
                string_state.supported += 1
                if occurrence in string_state.wanted:
                    string_state.values[occurrence] = value
            elif category == _STRING_FAILING:
                string_state.unsupported = True
            else:
                string_state.non_scalar = True

    scalar_results: dict[tuple[bytes, int, int], ScalarSelection] = {}
    for key, state in scalar_states.items():
        occurrence = key[2]
        if occurrence >= 0 and state.supported > occurrence:
            value, float32 = state.values[occurrence]
            scalar_results[key] = ScalarSelection(
                ScalarOutcome.value, value, float32
            )
        elif state.supported:
            scalar_results[key] = ScalarSelection(ScalarOutcome.mismatch)
        elif state.unsupported:
            scalar_results[key] = ScalarSelection(
                ScalarOutcome.unsupported_scalar
            )
        elif state.non_scalar:
            scalar_results[key] = ScalarSelection(
                ScalarOutcome.unsupported_value
            )
        else:
            scalar_results[key] = ScalarSelection(ScalarOutcome.mismatch)

    string_results: dict[tuple[bytes, int, int], StringSelection] = {}
    for key, state in string_states.items():
        occurrence = key[2]
        if occurrence >= 0 and state.supported > occurrence:
            string_results[key] = StringSelection(
                ScalarOutcome.value, state.values[occurrence]
            )
        elif state.supported:
            string_results[key] = StringSelection(ScalarOutcome.mismatch)
        elif state.unsupported:
            string_results[key] = StringSelection(
                ScalarOutcome.unsupported_scalar
            )
        elif state.non_scalar:
            string_results[key] = StringSelection(
                ScalarOutcome.unsupported_value
            )
        else:
            string_results[key] = StringSelection(ScalarOutcome.mismatch)
    return scalar_results, string_results


def scan_strings(
    data: bytes | bytearray,
    requests: Iterable[tuple[bytes, int, int]],
) -> dict[tuple[bytes, int, int], StringSelection]:
    """Select requested string tensors in one pass."""
    return scan_tensorboard_06(data, (), requests)[1]


# --- histograms (schema 0.7) ------------------------------------------------

MAX_HISTOGRAM_BUCKETS = 1024
_HISTOGRAM_RETAIN = MAX_HISTOGRAM_BUCKETS + 1


def _read_key_lenient(data, position, end):
    """Read a key without raising; return (field, wire, new_position) or None."""
    try:
        key, position = _read_varint(data, position, end)
    except EventFileError:
        return None
    field = key >> 3
    wire = key & 7
    if field == 0 or field > (2**29 - 1) or wire in (3, 4, 6, 7):
        return None
    return field, wire, position


@dataclass(frozen=True, slots=True)
class _HistoResult:
    present: bool
    value: dict | None


def _finalize_histogram(values, limits, counts, failed):
    if (
        failed
        or len(limits) > MAX_HISTOGRAM_BUCKETS
        or len(counts) > MAX_HISTOGRAM_BUCKETS
        or len(limits) != len(counts)
        or any(not math.isfinite(v) for v in values)
        or values[2] < 0
        or any(not math.isfinite(v) or v < 0 for v in counts)
        or any(not math.isfinite(v) for v in limits)
        or any(a >= b for a, b in zip(limits, limits[1:]))
    ):
        return None
    return {
        "kind": "histogram",
        "min": 0.0 if values[0] == 0.0 else values[0],
        "max": 0.0 if values[1] == 0.0 else values[1],
        "num": 0.0 if values[2] == 0.0 else values[2],
        "sum": 0.0 if values[3] == 0.0 else values[3],
        "sum_squares": 0.0 if values[4] == 0.0 else values[4],
        "bucket_limit": [0.0 if v == 0.0 else v for v in limits],
        "bucket": [0.0 if v == 0.0 else v for v in counts],
    }


def _decode_histogram_part(data, start, end, values, limits, counts):
    position = start
    while position < end:
        key = _read_key_lenient(data, position, end)
        if key is None:
            return values, limits, counts, False
        field, wire, position = key
        if 1 <= field <= 5:
            if wire != 1 or end - position < 8:
                return values, limits, counts, False
            values[field - 1] = _F64.unpack_from(data, position)[0]
            position += 8
        elif field in (6, 7):
            target = limits if field == 6 else counts
            if wire == 1:
                if end - position < 8:
                    return values, limits, counts, False
                if len(target) < _HISTOGRAM_RETAIN:
                    target.append(_F64.unpack_from(data, position)[0])
                position += 8
            elif wire == 2:
                try:
                    packed_start, packed_end = _read_length(data, position, end)
                except EventFileError:
                    return values, limits, counts, False
                length = packed_end - packed_start
                if length % 8:
                    return values, limits, counts, False
                for offset in range(packed_start, packed_end, 8):
                    if len(target) < _HISTOGRAM_RETAIN:
                        target.append(_F64.unpack_from(data, offset)[0])
                position = packed_end
            else:
                return values, limits, counts, False
        else:
            try:
                position = _skip_field(data, position, end, wire)
            except EventFileError:
                return values, limits, counts, False
    return values, limits, counts, True


def _parse_value_07(data, start, end, step):
    tag_start = -1
    tag_end = -1
    kind = 0
    simple_value = 0.0
    tensor_restart = start
    hist_values = [0.0, 0.0, 0.0, 0.0, 0.0]
    hist_limits: list[float] = []
    hist_counts: list[float] = []
    hist_present = False
    hist_failed = False
    position = start
    while position < end:
        field_number, wire_type, position = _read_key(data, position, end)
        if field_number == 1:
            if wire_type != 2:
                _refuse()
            tag_start, tag_end = _read_length(data, position, end)
            position = tag_end
        elif field_number == 2:
            if wire_type != 5 or end - position < 4:
                _refuse()
            simple_value = _F32.unpack_from(data, position)[0]
            kind = 2
            tensor_restart = position + 4
            hist_values = [0.0, 0.0, 0.0, 0.0, 0.0]
            hist_limits = []
            hist_counts = []
            hist_present = False
            hist_failed = False
            position += 4
        elif field_number in (3, 4, 6):
            if wire_type != 2:
                _refuse()
            _, payload_end = _read_length(data, position, end)
            kind = field_number
            tensor_restart = payload_end
            hist_values = [0.0, 0.0, 0.0, 0.0, 0.0]
            hist_limits = []
            hist_counts = []
            hist_present = False
            hist_failed = False
            position = payload_end
        elif field_number == 5:
            if wire_type != 2:
                _refuse()
            histo_start, histo_end = _read_length(data, position, end)
            kind = 5
            tensor_restart = histo_end
            hist_present = True
            if not hist_failed:
                hist_values, hist_limits, hist_counts, ok = (
                    _decode_histogram_part(
                        data,
                        histo_start,
                        histo_end,
                        hist_values,
                        hist_limits,
                        hist_counts,
                    )
                )
                hist_failed = not ok
            position = histo_end
        elif field_number == 7:
            if wire_type != 2:
                _refuse()
            _, position = _read_length(data, position, end)
        elif field_number == 8:
            if wire_type != 2:
                _refuse()
            _, payload_end = _read_length(data, position, end)
            kind = 8
            hist_values = [0.0, 0.0, 0.0, 0.0, 0.0]
            hist_limits = []
            hist_counts = []
            hist_present = False
            hist_failed = False
            position = payload_end
        elif field_number == 9:
            if wire_type != 2:
                _refuse()
            _, position = _read_length(data, position, end)
        else:
            position = _skip_field(data, position, end, wire_type)
    tensor = None
    if kind == 8:
        tensor = _TensorState()
        _scan_tensor_region(data, tensor_restart, end, tensor, strings=True)
    hist = _HistoResult(
        hist_present,
        _finalize_histogram(
            hist_values, hist_limits, hist_counts, hist_failed
        )
        if hist_present
        else None,
    )
    return tag_start, tag_end, step, kind, simple_value, tensor, hist


def _iter_summary_values_07(data, start, end, step):
    position = start
    while position < end:
        field_number, wire_type, position = _read_key(data, position, end)
        if field_number == 1:
            if wire_type != 2:
                _refuse()
            value_start, value_end = _read_length(data, position, end)
            yield _parse_value_07(data, value_start, value_end, step)
            position = value_end
        else:
            position = _skip_field(data, position, end, wire_type)


def _iter_winning_summaries_07(data, start, end, step):
    position = start
    while position < end:
        field_number, wire_type, position = _read_key(data, position, end)
        if field_number == 5:
            if wire_type != 2:
                _refuse()
            summary_start, summary_end = _read_length(data, position, end)
            yield from _iter_summary_values_07(
                data, summary_start, summary_end, step
            )
            position = summary_end
        else:
            position = _skip_field(data, position, end, wire_type)


def _iter_event_values_07(data, start, end):
    step = 0
    what = 0
    last_other_end = start
    summary_bytes = 0
    position = start
    while position < end:
        field_number, wire_type, position = _read_key(data, position, end)
        if field_number == 1:  # wall_time
            if wire_type != 1 or end - position < 8:
                _refuse()
            position += 8
        elif field_number == 2:  # step
            if wire_type != 0:
                _refuse()
            raw, position = _read_varint(data, position, end)
            step = _signed_int64(raw)
        elif 3 <= field_number <= 9:
            if wire_type != 2:
                _refuse()
            payload_start, payload_end = _read_length(data, position, end)
            if field_number == 5:
                what = 5
                summary_bytes += payload_end - payload_start
            else:
                what = field_number
                last_other_end = payload_end
                summary_bytes = 0
            position = payload_end
        else:
            position = _skip_field(data, position, end, wire_type)
    if what != 5:
        return
    if summary_bytes > MAX_SUMMARY_BYTES:
        _refuse()
    yield from _iter_winning_summaries_07(
        data, last_other_end, end, step
    )


@dataclass(frozen=True, slots=True)
class HistogramRecord:
    tag: bytes
    step: int
    value: dict


@dataclass(frozen=True, slots=True)
class HistogramSelection:
    outcome: ScalarOutcome
    value: dict | None = None


def _iter_winning_values_07(data):
    for payload_start, payload_end in _iter_records(data):
        yield from _iter_event_values_07(data, payload_start, payload_end)


def iter_supported_histograms(data):
    for tag_start, tag_end, step, kind, _simple, _tensor, ranges in _iter_winning_values_07(
        data
    ):
        if kind != 5 or not ranges.present or ranges.value is None:
            continue
        yield HistogramRecord(
            tag=_tag_bytes(data, tag_start, tag_end), step=step, value=ranges.value
        )


def iter_supported_summaries_07(data):
    for tag_start, tag_end, step, kind, simple_value, tensor, ranges in _iter_winning_values_07(
        data
    ):
        category, value, float32 = _classify(data, kind, simple_value, tensor)
        if category == _SUPPORTED:
            yield ScalarRecord(
                tag=_tag_bytes(data, tag_start, tag_end),
                step=step,
                value=value,
                float32=float32,
            )
        string_category, raw = _classify_string(data, kind, tensor)
        if string_category == _STRING_SUPPORTED:
            yield StringRecord(
                tag=_tag_bytes(data, tag_start, tag_end), step=step, value=raw
            )
        if kind == 5 and ranges.present and ranges.value is not None:
            yield HistogramRecord(
                tag=_tag_bytes(data, tag_start, tag_end),
                step=step,
                value=ranges.value,
            )


def scan_tensorboard_07(data, scalar_requests, string_requests, histogram_requests):
    """Select scalar, string and histogram requests in one traversal."""
    scalar_states, scalar_by_tag, scalar_lengths = _normalise_requests(scalar_requests)
    string_states, string_by_tag, string_lengths = _normalise_requests(string_requests)
    hist_states, hist_by_tag, hist_lengths = _normalise_requests(histogram_requests)
    if not scalar_states and not string_states and not hist_states:
        return {}, {}, {}

    for tag_start, tag_end, step, kind, simple_value, tensor, ranges in _iter_winning_values_07(
        data
    ):
        tag_length = 0 if tag_start < 0 else tag_end - tag_start
        tag = b"" if tag_start < 0 else data[tag_start:tag_end]
        if type(tag) is not bytes:
            tag = bytes(tag)

        scalar_state = None
        if tag_length in scalar_lengths:
            by_step = scalar_by_tag.get(tag)
            if by_step is not None:
                scalar_state = by_step.get(step)
        if scalar_state is not None:
            category, value, float32 = _classify(data, kind, simple_value, tensor)
            if category == _SUPPORTED:
                occurrence = scalar_state.supported
                scalar_state.supported += 1
                if occurrence in scalar_state.wanted:
                    scalar_state.values[occurrence] = (value, float32)
            elif category == _UNSUPPORTED_SCALAR:
                scalar_state.unsupported = True
            else:
                scalar_state.non_scalar = True

        string_state = None
        if tag_length in string_lengths:
            by_step = string_by_tag.get(tag)
            if by_step is not None:
                string_state = by_step.get(step)
        if string_state is not None:
            category, raw = _classify_string(data, kind, tensor)
            if category == _STRING_SUPPORTED:
                occurrence = string_state.supported
                string_state.supported += 1
                if occurrence in string_state.wanted:
                    string_state.values[occurrence] = raw
            elif category == _STRING_FAILING:
                string_state.unsupported = True
            else:
                string_state.non_scalar = True

        hist_state = None
        if tag_length in hist_lengths:
            by_step = hist_by_tag.get(tag)
            if by_step is not None:
                hist_state = by_step.get(step)
        if hist_state is not None:
            if kind == 5 and ranges.present:
                if ranges.value is not None:
                    occurrence = hist_state.supported
                    hist_state.supported += 1
                    if occurrence in hist_state.wanted:
                        hist_state.values[occurrence] = ranges.value
                else:
                    hist_state.unsupported = True
            elif kind == 5:
                hist_state.non_scalar = True
            else:
                hist_state.non_scalar = True

    scalar_results = _scalar_results(scalar_states)
    string_results = _string_results(string_states)
    hist_results: dict[tuple[bytes, int, int], HistogramSelection] = {}
    for key, state in hist_states.items():
        occurrence = key[2]
        if occurrence >= 0 and state.supported > occurrence:
            hist_results[key] = HistogramSelection(
                ScalarOutcome.value, state.values[occurrence]
            )
        elif state.supported:
            hist_results[key] = HistogramSelection(ScalarOutcome.mismatch)
        elif state.unsupported:
            hist_results[key] = HistogramSelection(
                ScalarOutcome.unsupported_scalar
            )
        elif state.non_scalar:
            hist_results[key] = HistogramSelection(
                ScalarOutcome.unsupported_value
            )
        else:
            hist_results[key] = HistogramSelection(ScalarOutcome.mismatch)
    return scalar_results, string_results, hist_results


def _scalar_results(states):
    results = {}
    for key, state in states.items():
        occurrence = key[2]
        if occurrence >= 0 and state.supported > occurrence:
            value, float32 = state.values[occurrence]
            results[key] = ScalarSelection(ScalarOutcome.value, value, float32)
        elif state.supported:
            results[key] = ScalarSelection(ScalarOutcome.mismatch)
        elif state.unsupported:
            results[key] = ScalarSelection(ScalarOutcome.unsupported_scalar)
        elif state.non_scalar:
            results[key] = ScalarSelection(ScalarOutcome.unsupported_value)
        else:
            results[key] = ScalarSelection(ScalarOutcome.mismatch)
    return results


def _string_results(states):
    results = {}
    for key, state in states.items():
        occurrence = key[2]
        if occurrence >= 0 and state.supported > occurrence:
            results[key] = StringSelection(
                ScalarOutcome.value, state.values[occurrence]
            )
        elif state.supported:
            results[key] = StringSelection(ScalarOutcome.mismatch)
        elif state.unsupported:
            results[key] = StringSelection(ScalarOutcome.unsupported_scalar)
        elif state.non_scalar:
            results[key] = StringSelection(ScalarOutcome.unsupported_value)
        else:
            results[key] = StringSelection(ScalarOutcome.mismatch)
    return results
