"""Schema 0.10 TF2 media: layouts, records and element identity."""

from __future__ import annotations

import struct
import zlib

import pytest

from runevidence.tensorboard_events import (
    EventFileError,
    ScalarOutcome,
    Tf2MediaRecord,
    iter_supported_summaries_10,
    scan_tensorboard_10,
)

import tensorboard_builder as tb


def shape(*dims):
    return b"".join(tb.bytes_field(2, tb.varint_field(1, d)) for d in dims)


def png(width=8, height=8, body=b"\x01"):
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", body)
        + chunk(b"IEND", b"")
    )


def wav(frames=b"\x00\x00" * 4):
    fmt = struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    chunks += b"data" + struct.pack("<I", len(frames)) + frames
    return b"RIFF" + struct.pack("<I", len(chunks) + 4) + b"WAVE" + chunks


def image_tensor(width="8", height="8", payloads=()):
    parts = [tb.varint_field(1, 7), tb.bytes_field(2, shape(2 + len(payloads)))]
    parts.append(tb.bytes_field(8, width.encode()))
    parts.append(tb.bytes_field(8, height.encode()))
    parts.extend(tb.bytes_field(8, payload) for payload in payloads)
    return b"".join(parts)


def audio_tensor(rows=()):
    parts = [tb.varint_field(1, 7), tb.bytes_field(2, shape(len(rows), 2))]
    for payload, label in rows:
        parts.append(tb.bytes_field(8, payload))
        parts.append(tb.bytes_field(8, label))
    return b"".join(parts)


def events(*tensors, tags=(b"samples",), steps=(0,)):
    return tb.records(
        *[
            tb.event(step=steps[i], summary_bytes=tb.summary(tb.value(tag=tags[i], tensor_bytes=t)))
            for i, t in enumerate(tensors)
        ]
    )


def scan(data, *, images=(), audio=()):
    return scan_tensorboard_10(data, [], [], [], [], [], [], images, audio)


def test_image_batch_records_each_payload():
    data = events(image_tensor(payloads=(png(), png(body=b"\x02"))))
    records = list(iter_supported_summaries_10(data))
    assert len(records) == 1
    record = records[0]
    assert isinstance(record, Tf2MediaRecord)
    assert record.kind == "tf2-image"
    assert [element for element, _ in record.elements] == [0, 1]
    assert all(value["width"] == 8 for _, value in record.elements)


def test_audio_batch_records_each_row_and_ignores_label():
    data = events(audio_tensor(rows=((wav(), b"clip"), (wav(b"\x00\x00" * 2), b""))))
    record = list(iter_supported_summaries_10(data))[0]
    assert record.kind == "tf2-audio"
    assert [element for element, _ in record.elements] == [0, 1]


def test_scan_element_selection_and_precedence():
    data = events(image_tensor(payloads=(png(),)))
    out = scan(data, images=[(b"samples", 0, 0, 0), (b"samples", 0, 0, 1), (b"samples", 0, 1, 0)])
    assert out[6][(b"samples", 0, 0, 0)].outcome is ScalarOutcome.value
    assert out[6][(b"samples", 0, 0, 1)].outcome is ScalarOutcome.mismatch
    assert out[6][(b"samples", 0, 1, 0)].outcome is ScalarOutcome.mismatch


def test_opposite_kind_is_unsupported_encoding():
    data = events(image_tensor(payloads=(png(),)))
    out = scan(data, audio=[(b"samples", 0, 0, 0)])
    assert out[7][(b"samples", 0, 0, 0)].outcome is ScalarOutcome.unsupported_scalar


def test_width_mismatch_and_bad_dims_fail_the_entry():
    assert not list(iter_supported_summaries_10(events(image_tensor(width="9", payloads=(png(),)))))
    assert not list(iter_supported_summaries_10(events(image_tensor(width="0", payloads=(png(),)))))
    assert not list(
        iter_supported_summaries_10(events(image_tensor(width="0" * 5000 + "8", payloads=(png(),))))
    )


@pytest.mark.parametrize("payload", [png()[:20], b"", b"not a png"])
def test_invalid_payload_fails_the_entry(payload):
    assert not list(iter_supported_summaries_10(events(image_tensor(payloads=(payload,)))))


def test_tensor_content_is_rejected():
    tensor = image_tensor(payloads=(png(),))
    tensor += tb.bytes_field(4, b"\x00")
    assert not list(iter_supported_summaries_10(events(tensor)))


def test_shape_count_mismatch_is_rejected():
    tensor = tb.varint_field(1, 7) + tb.bytes_field(2, shape(4))
    tensor += tb.bytes_field(8, b"8") + tb.bytes_field(8, b"8") + tb.bytes_field(8, png())
    assert not list(iter_supported_summaries_10(events(tensor)))


def test_sixty_five_payloads_is_rejected():
    tensor = image_tensor(payloads=tuple(png(body=bytes([i % 255])) for i in range(65)))
    assert not list(iter_supported_summaries_10(events(tensor)))


def test_wrong_wire_string_val_fails_the_entry_not_the_file():
    good = tb.records(tb.event(step=9, summary_bytes=tb.summary(tb.value(tag=b"good", simple_value=1.5))))
    tensor = tb.varint_field(1, 7) + tb.bytes_field(2, shape(3))
    tensor += tb.bytes_field(8, b"8") + tb.bytes_field(8, b"8")
    tensor += tb.key(8, 0) + b"\x05"
    records = list(iter_supported_summaries_10(events(tensor) + good))
    assert [type(record).__name__ for record in records] == ["ScalarRecord"]


def test_tf2_shaped_entries_keep_lower_kind_outcomes():
    data = events(image_tensor(payloads=(png(),)))
    out = scan_tensorboard_10(
        data,
        [(b"samples", 0, 0)],
        [(b"samples", 0, 0)],
        [(b"samples", 0, 0)],
        [(b"samples", 0, 0)],
        [(b"samples", 0, 0)],
        [(b"samples", 0, 0)],
        [],
        [],
    )
    assert out[0][(b"samples", 0, 0)].outcome is ScalarOutcome.unsupported_value
    assert out[1][(b"samples", 0, 0)].outcome is ScalarOutcome.unsupported_value
    assert out[2][(b"samples", 0, 0)].outcome is ScalarOutcome.unsupported_value
    assert out[3][(b"samples", 0, 0)].outcome is ScalarOutcome.unsupported_value
    assert out[4][(b"samples", 0, 0)].outcome is ScalarOutcome.unsupported_value
    assert out[5][(b"samples", 0, 0)].outcome is ScalarOutcome.unsupported_scalar


def test_summary_cap_still_refuses_the_file():
    tensor = image_tensor(payloads=(png(),))
    payload = tb.bytes_field(1, tb.value(tag=b"s", tensor_bytes=tensor)) * (16 * 1024 * 1024 // 20)
    with pytest.raises(EventFileError):
        list(iter_supported_summaries_10(tb.records(tb.event(summary_bytes=payload))))
