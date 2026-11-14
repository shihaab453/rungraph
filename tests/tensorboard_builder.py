"""Test-only TensorBoard event writer: framing, varints and message bytes.

This module deliberately does not import the reader under test.  Its CRC32C
table, varint encoder and message builders are an independent implementation,
so tests compare two implementations rather than one implementation with
itself.  It is not a TensorBoard replacement and is not shipped.
"""

from __future__ import annotations

import struct

_MASK_DELTA = 0xA282EAD8
_UNSET = object()


def _build_crc32c_table() -> tuple[int, ...]:
    table = []
    polynomial = 0x82F63B78
    for index in range(256):
        crc = index
        for _ in range(8):
            crc = (crc >> 1) ^ polynomial if crc & 1 else crc >> 1
        table.append(crc)
    return tuple(table)


_CRC32C_TABLE = _build_crc32c_table()


def crc32c(data: bytes) -> int:
    """Unmasked CRC32C (Castagnoli), independent reference implementation."""
    crc = 0xFFFFFFFF
    table = _CRC32C_TABLE
    for byte in data:
        crc = table[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def masked_crc32c(data: bytes) -> int:
    """TFRecord's masked CRC32C."""
    crc = crc32c(data)
    return (((crc >> 15) | (crc << 17)) + _MASK_DELTA) & 0xFFFFFFFF


def varint(value: int, *, extra: int = 0) -> bytes:
    """Encode an unsigned or two's-complement-64 varint.

    ``extra`` appends that many redundant zero continuation bytes, producing a
    non-minimal encoding that a protobuf reader must still accept.
    """
    if value < 0:
        value += 1 << 64
    elif value >= 1 << 64:
        raise ValueError("varint does not fit 64 bits")
    encoded = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            encoded.append(byte | 0x80)
        else:
            encoded.append(byte)
            break
    if extra:
        encoded[-1] |= 0x80
        encoded.extend(b"\x80" * (extra - 1))
        encoded.append(0)
    return bytes(encoded)


def key(field: int, wire: int) -> bytes:
    return varint((field << 3) | wire)


def varint_field(field: int, value: int) -> bytes:
    return key(field, 0) + varint(value)


def bytes_field(field: int, payload: bytes) -> bytes:
    return key(field, 2) + varint(len(payload)) + payload


def fixed32_field(field: int, value: int) -> bytes:
    return key(field, 5) + struct.pack("<I", value)


def fixed64_field(field: int, value: int) -> bytes:
    return key(field, 1) + struct.pack("<Q", value)


def float_field(field: int, value: float) -> bytes:
    return key(field, 5) + struct.pack("<f", value)


def double_field(field: int, value: float) -> bytes:
    return key(field, 1) + struct.pack("<d", value)


def packed_floats(field: int, values) -> bytes:
    return bytes_field(field, b"".join(struct.pack("<f", value) for value in values))


def packed_doubles(field: int, values) -> bytes:
    return bytes_field(field, b"".join(struct.pack("<d", value) for value in values))


def record(
    payload: bytes,
    *,
    length: int | None = None,
    length_crc: int | None = None,
    data_crc: int | None = None,
) -> bytes:
    """One TFRecord: length, masked length CRC, payload, masked data CRC."""
    declared = len(payload) if length is None else length
    header = struct.pack("<Q", declared)
    header_crc = (
        masked_crc32c(header) if length_crc is None else length_crc
    )
    payload_crc = masked_crc32c(payload) if data_crc is None else data_crc
    return header + struct.pack("<I", header_crc) + payload + struct.pack("<I", payload_crc)


def records(*payloads: bytes) -> bytes:
    return b"".join(record(payload) for payload in payloads)


def dim(size: int | None = None, name: bytes | None = None, *, extra: bytes = b"") -> bytes:
    parts = []
    if size is not None:
        parts.append(varint_field(1, size))
    if name is not None:
        parts.append(bytes_field(2, name))
    return b"".join(parts) + extra


def shape(
    dims=(),
    unknown_rank: bool | None = None,
    *,
    extra: bytes = b"",
) -> bytes:
    parts = [
        bytes_field(2, item if isinstance(item, bytes) else dim(size=item))
        for item in dims
    ]
    if unknown_rank is not None:
        parts.append(varint_field(3, 1 if unknown_rank else 0))
    return b"".join(parts) + extra


def tensor(
    *,
    dtype: int | None = None,
    shape_bytes: bytes | None = None,
    content: bytes | None = None,
    float_values=None,
    double_values=None,
    packed_float_values=None,
    packed_double_values=None,
    extra: bytes = b"",
) -> bytes:
    parts = []
    if dtype is not None:
        parts.append(varint_field(1, dtype))
    if shape_bytes is not None:
        parts.append(bytes_field(2, shape_bytes))
    if content is not None:
        parts.append(bytes_field(4, content))
    for value in float_values or ():
        parts.append(float_field(5, value))
    for value in double_values or ():
        parts.append(double_field(6, value))
    if packed_float_values is not None:
        parts.append(packed_floats(5, packed_float_values))
    if packed_double_values is not None:
        parts.append(packed_doubles(6, packed_double_values))
    return b"".join(parts) + extra


def value(
    *,
    tag: bytes | None = None,
    simple_value=_UNSET,
    tensor_bytes: bytes | None = None,
    histogram: bytes | None = None,
    image: bytes | None = None,
    histo: bytes | None = None,
    audio: bytes | None = None,
    metadata: bytes | None = None,
    extra: bytes = b"",
) -> bytes:
    parts = []
    if tag is not None:
        parts.append(bytes_field(1, tag))
    if simple_value is not _UNSET:
        parts.append(float_field(2, simple_value))
    if histogram is not None:
        parts.append(bytes_field(3, histogram))
    if image is not None:
        parts.append(bytes_field(4, image))
    if histo is not None:
        parts.append(bytes_field(5, histo))
    if audio is not None:
        parts.append(bytes_field(6, audio))
    if tensor_bytes is not None:
        parts.append(bytes_field(8, tensor_bytes))
    if metadata is not None:
        parts.append(bytes_field(9, metadata))
    return b"".join(parts) + extra


def summary(*values: bytes, extra: bytes = b"") -> bytes:
    return b"".join(bytes_field(1, item) for item in values) + extra


def event(
    *,
    wall_time: float | None = None,
    step: int | None = None,
    file_version: bytes | None = None,
    graph_def: bytes | None = None,
    summary_bytes: bytes | None = None,
    log_message: bytes | None = None,
    session_log: bytes | None = None,
    tagged_run_metadata: bytes | None = None,
    meta_graph_def: bytes | None = None,
    extra: bytes = b"",
) -> bytes:
    parts = []
    if wall_time is not None:
        parts.append(double_field(1, wall_time))
    if step is not None:
        parts.append(varint_field(2, step))
    if file_version is not None:
        parts.append(bytes_field(3, file_version))
    if graph_def is not None:
        parts.append(bytes_field(4, graph_def))
    if summary_bytes is not None:
        parts.append(bytes_field(5, summary_bytes))
    if log_message is not None:
        parts.append(bytes_field(6, log_message))
    if session_log is not None:
        parts.append(bytes_field(7, session_log))
    if tagged_run_metadata is not None:
        parts.append(bytes_field(8, tagged_run_metadata))
    if meta_graph_def is not None:
        parts.append(bytes_field(9, meta_graph_def))
    return b"".join(parts) + extra


def scalar_event(tag: bytes, number: float, *, step: int | None = None) -> bytes:
    return event(step=step, summary_bytes=summary(value(tag=tag, simple_value=number)))


def padding_summary(total: int, field: int = 15) -> bytes:
    """A valid Summary payload of exactly ``total`` bytes using an unknown field."""
    if total <= 0:
        return b""
    for payload_length in range(total, max(total - 12, -1), -1):
        payload = bytes(payload_length)
        candidate = bytes_field(field, payload)
        if len(candidate) == total:
            return candidate
    raise ValueError(f"cannot build a {total}-byte summary")
