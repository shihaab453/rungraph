"""Schema 0.8 media reader: PNG/RIFF validation, identity and the ladder."""

from __future__ import annotations

import struct
import zlib

import pytest

from runevidence.tensorboard_events import (
    AudioRecord,
    EventFileError,
    ImageRecord,
    ScalarOutcome,
    ScalarRecord,
    iter_supported_summaries_08,
    scan_tensorboard_08,
)

import tensorboard_builder as tb


def png(width: int = 64, height: int = 64, *, body: bytes = b"\x00\x01") -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
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


def wav(frames: bytes = b"\x00\x00" * 4, *, pad: bool = True) -> bytes:
    fmt = struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt
    chunks += b"data" + struct.pack("<I", len(frames)) + frames
    if pad and len(frames) % 2:
        chunks += b"\x00"
    return b"RIFF" + struct.pack("<I", len(chunks) + 4) + b"WAVE" + chunks


def image_value(
    tag: bytes, payload: bytes, *, width: int = 64, height: int = 64, wire: bool = True
):
    message = b""
    if wire:
        message += tb.varint_field(1, height) + tb.varint_field(2, width)
    message += tb.bytes_field(4, payload)
    return tb.value(tag=tag, image=message)


def audio_value(
    tag: bytes, payload: bytes, *, content_type: bytes | None = b"audio/wav"
):
    message = b""
    if content_type is not None:
        message += tb.bytes_field(5, content_type)
    message += tb.bytes_field(4, payload)
    return tb.value(tag=tag, audio=message)


def file_with(*values: bytes) -> bytes:
    return tb.records(
        *[
            tb.event(step=index, summary_bytes=tb.summary(value))
            for index, value in enumerate(values)
        ]
    )


def scan(data: bytes, *, images=(), audio=(), scalars=()):
    return scan_tensorboard_08(data, scalars, [], [], images, audio)


def test_image_record_has_digest_and_geometry():
    payload = png()
    records = list(iter_supported_summaries_08(file_with(image_value(b"samples", payload))))
    assert len(records) == 1
    record = records[0]
    assert isinstance(record, ImageRecord)
    assert record.tag == b"samples"
    assert record.step == 0
    assert record.value["width"] == 64
    assert record.value["height"] == 64
    assert record.value["size_bytes"] == len(payload)
    assert record.value["sha256"] == __import__("hashlib").sha256(payload).hexdigest()


def test_audio_record_has_digest_and_size():
    payload = wav()
    records = list(iter_supported_summaries_08(file_with(audio_value(b"clip", payload))))
    assert len(records) == 1
    record = records[0]
    assert isinstance(record, AudioRecord)
    assert record.value["size_bytes"] == len(payload)
    assert set(record.value) == {"kind", "media_type", "sha256", "size_bytes"}


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\x89PNG\r\n\x1a\n",
        b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + b"\x00" * 13,
        png()[:-4],
        png() + b"junk",
        png()[:40],
    ],
)
def test_invalid_images_are_skipped(payload):
    assert list(iter_supported_summaries_08(file_with(image_value(b"samples", payload)))) == []


def test_png_ihdr_crc_is_checked():
    payload = bytearray(png())
    payload[29] ^= 0xFF
    assert list(iter_supported_summaries_08(file_with(image_value(b"s", bytes(payload))))) == []


def test_png_missing_idat_is_skipped():
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 6, 0, 0, 0)
    payload = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")
    assert list(iter_supported_summaries_08(file_with(image_value(b"s", payload)))) == []


def test_png_duplicate_ihdr_is_skipped():
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 6, 0, 0, 0)
    payload = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", b"\x00")
        + chunk(b"IHDR", ihdr)
        + chunk(b"IEND", b"")
    )
    assert list(iter_supported_summaries_08(file_with(image_value(b"s", payload)))) == []


def test_png_wire_dimensions_must_match_ihdr():
    payload = png(width=16, height=16)
    assert list(iter_supported_summaries_08(file_with(image_value(b"s", payload)))) == []
    records = list(
        iter_supported_summaries_08(
            file_with(image_value(b"s", payload, width=16, height=16, wire=False))
        )
    )
    assert records[0].value["width"] == 16


def test_png_negative_wire_dimension_is_skipped():
    message = tb.varint_field(1, -1) + tb.bytes_field(4, png())
    payload = tb.value(tag=b"s", image=message)
    assert list(iter_supported_summaries_08(file_with(payload))) == []


def test_png_int32_truncation_accepts_2_pow_32_plus_64():
    message = (
        tb.varint_field(1, 64)
        + tb.varint_field(2, 2**32 + 64)
        + tb.bytes_field(4, png())
    )
    records = list(iter_supported_summaries_08(file_with(tb.value(tag=b"s", image=message))))
    assert records and records[0].value["width"] == 64


def test_wav_odd_chunk_requires_padding():
    frames = b"\x00" * 5
    assert list(
        iter_supported_summaries_08(file_with(audio_value(b"a", wav(frames, pad=False))))
    ) == []
    records = list(
        iter_supported_summaries_08(file_with(audio_value(b"a", wav(frames, pad=True))))
    )
    assert records
    assert records[0].value["size_bytes"] == len(wav(frames, pad=True))


@pytest.mark.parametrize(
    "payload",
    [
        b"RIFF\x04\x00\x00\x00WAVE",
        b"RIFF\x00\x00\x00\x00WAVEfmt \x10\x00\x00\x00" + b"\x00" * 16,
        b"OGG!" + wav()[4:],
    ],
)
def test_invalid_audio_is_skipped(payload):
    assert list(iter_supported_summaries_08(file_with(audio_value(b"a", payload)))) == []


def test_audio_content_type_must_be_wav():
    payload = wav()
    assert list(
        iter_supported_summaries_08(
            file_with(audio_value(b"a", payload, content_type=b"audio/mpeg"))
        )
    ) == []
    records = list(
        iter_supported_summaries_08(file_with(audio_value(b"a", payload, content_type=b"")))
    )
    assert records


def test_bad_media_is_entry_local_and_scalars_still_load():
    data = tb.records(
        tb.event(step=0, summary_bytes=tb.summary(tb.value(tag=b"loss", simple_value=0.5))),
        tb.event(step=1, summary_bytes=tb.summary(image_value(b"s", b"not a png"))),
        tb.event(step=2, summary_bytes=tb.summary(tb.value(tag=b"loss2", simple_value=1.5))),
    )
    records = list(iter_supported_summaries_08(data))
    assert [type(record).__name__ for record in records] == ["ScalarRecord", "ScalarRecord"]


def test_media_occurrences_are_per_kind():
    data = tb.records(
        tb.event(step=0, summary_bytes=tb.summary(image_value(b"t", png()))),
        tb.event(step=0, summary_bytes=tb.summary(tb.value(tag=b"t", simple_value=1.0))),
        tb.event(step=0, summary_bytes=tb.summary(image_value(b"t", png(body=b"\x00\x02")))),
    )
    scalars, _, _, images, _ = scan(
        data,
        scalars=[(b"t", 0, 0)],
        images=[(b"t", 0, 0), (b"t", 0, 1), (b"t", 0, 2)],
    )
    assert scalars[(b"t", 0, 0)].outcome is ScalarOutcome.value
    assert images[(b"t", 0, 0)].outcome is ScalarOutcome.value
    assert images[(b"t", 0, 1)].outcome is ScalarOutcome.value
    assert images[(b"t", 0, 1)].value["sha256"] != images[(b"t", 0, 0)].value["sha256"]
    assert images[(b"t", 0, 2)].outcome is ScalarOutcome.mismatch


def test_ladder_failed_image_is_unsupported_encoding():
    data = file_with(image_value(b"t", b"bad"))
    _, _, _, images, _ = scan(data, images=[(b"t", 0, 0)])
    assert images[(b"t", 0, 0)].outcome is ScalarOutcome.unsupported_scalar


def test_ladder_other_kind_is_unsupported_value():
    data = file_with(tb.value(tag=b"t", simple_value=1.0))
    _, _, _, images, _ = scan(data, images=[(b"t", 0, 0)])
    assert images[(b"t", 0, 0)].outcome is ScalarOutcome.unsupported_value


def test_ladder_tag_only_value_is_unsupported_value():
    data = file_with(tb.value(tag=b"t"))
    _, _, _, images, _ = scan(data, images=[(b"t", 0, 0)])
    assert images[(b"t", 0, 0)].outcome is ScalarOutcome.unsupported_value


def test_ladder_absent_tag_is_missing():
    data = file_with(image_value(b"other", png()))
    _, _, _, images, _ = scan(data, images=[(b"t", 0, 0)])
    assert images[(b"t", 0, 0)].outcome is ScalarOutcome.mismatch


def test_merged_summary_image_and_scalar_both_load():
    data = tb.records(
        tb.event(
            step=3,
            summary_bytes=tb.summary(
                tb.value(tag=b"loss", simple_value=0.5), image_value(b"s", png())
            ),
        )
    )
    records = list(iter_supported_summaries_08(data))
    assert {type(record).__name__ for record in records} == {"ScalarRecord", "ImageRecord"}


def test_summary_cap_refuses_the_file_at_16_mib_plus_one():
    payload = png(body=b"\x00" * (16 * 1024 * 1024))
    data = file_with(image_value(b"s", payload))
    with pytest.raises(EventFileError):
        list(iter_supported_summaries_08(data))
