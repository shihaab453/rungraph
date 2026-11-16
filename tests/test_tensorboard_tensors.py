"""Schema 0.9 tensor digests: canonical forms, wire boundary and identity."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from runevidence.tensorboard_events import (
    EventFileError,
    ScalarOutcome,
    TensorRecord,
    iter_supported_summaries_09,
    scan_tensorboard_09,
)

import tensorboard_builder as tb


def shape(*dims):
    return b"".join(tb.bytes_field(2, tb.varint_field(1, d)) for d in dims)


def tensor(dtype, dims, *, content=None, floats=None, doubles=None, ints=None,
           int64s=None, packed_ints=None, packed_int64s=None, unknown_rank=False):
    shape_bytes = shape(*dims)
    if unknown_rank:
        shape_bytes += tb.varint_field(3, 1)
    parts = [tb.varint_field(1, dtype), tb.bytes_field(2, shape_bytes)]
    if content is not None:
        parts.append(tb.bytes_field(4, content))
    if floats is not None:
        parts.append(tb.bytes_field(5, b"".join(struct.pack("<f", x) for x in floats)))
    if doubles is not None:
        parts.append(tb.bytes_field(6, b"".join(struct.pack("<d", x) for x in doubles)))
    if ints is not None:
        parts.append(b"".join(tb.varint_field(7, x) for x in ints))
    if int64s is not None:
        parts.append(b"".join(tb.varint_field(10, x) for x in int64s))
    if packed_ints is not None:
        parts.append(tb.bytes_field(7, packed_ints))
    if packed_int64s is not None:
        parts.append(tb.bytes_field(10, packed_int64s))
    return b"".join(parts)


def events(*payloads, tag=b"t"):
    return tb.records(
        *[
            tb.event(step=index, summary_bytes=tb.summary(tb.value(tag=tag, tensor_bytes=p)))
            for index, p in enumerate(payloads)
        ]
    )


def digest(payload):
    records = list(iter_supported_summaries_09(events(payload)))
    return records[0].value if records else None


def scan(data, *, tensors=(), scalars=()):
    return scan_tensorboard_09(data, scalars, [], [], [], [], tensors)


def test_float32_content_and_typed_forms_hash_equally():
    content = struct.pack("<6f", 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    a = digest(tensor(1, [2, 3], content=content))
    b = digest(tensor(1, [2, 3], floats=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0]))
    assert a == b
    assert a["shape"] == [2, 3] and a["element_count"] == 6


def test_float64_and_integer_forms_hash_equally():
    assert digest(tensor(2, [2], doubles=[1.5, 2.5])) == digest(
        tensor(2, [2], content=struct.pack("<2d", 1.5, 2.5))
    )
    assert digest(tensor(3, [2], ints=[-1, 5])) == digest(
        tensor(3, [2], content=struct.pack("<2i", -1, 5))
    )
    assert digest(tensor(9, [2], int64s=[-1, 2])) == digest(
        tensor(9, [2], content=struct.pack("<2q", -1, 2))
    )


def test_int32_low_bits_make_minus_one_and_max_agree():
    assert digest(tensor(3, [1], ints=[-1])) == digest(
        tensor(3, [1], content=struct.pack("<I", 0xFFFFFFFF))
    )


def test_non_minimal_varint_is_accepted():
    content = struct.pack("<1i", 7)
    packed = b"\x87\x80\x00"
    assert digest(tensor(3, [1], content=content)) == digest(
        tensor(3, [1], packed_ints=packed)
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (tensor(1, [2], content=struct.pack("<2f", 1.0, 2.0)), "value"),
        (tensor(1, [2], content=struct.pack("<f", 1.0)), None),
        (tensor(1, [2], floats=[1.0, 2.0, 3.0]), None),
        (tensor(1, [] , content=b""), None),
        (tensor(1, [1] * 9, content=struct.pack("<f", 1.0)), None),
        (tensor(1, [0], content=b""), None),
        (tensor(1, [-2], content=b"\x00" * 8), None),
        (tensor(1, [1], content=struct.pack("<f", 1.0), unknown_rank=True), None),
        (tensor(7, [2], ints=[1, 2]), None),
        (tensor(101, [2], ints=[1, 2]), None),
    ],
)
def test_profile_failures_are_entry_local(payload, expected):
    assert (digest(payload) is not None) == (expected == "value")


def test_content_wins_and_typed_values_are_ignored():
    content = struct.pack("<2f", 1.0, 2.0)
    with_ignored = tensor(1, [2], content=content, doubles=[9.0, 9.0, 9.0])
    assert digest(with_ignored) == digest(tensor(1, [2], content=content))


def test_foreign_typed_list_fails_without_content():
    assert digest(tensor(1, [2], floats=[1.0, 2.0], doubles=[1.0, 2.0])) is None
    assert digest(tensor(1, [2], floats=[1.0, 2.0], ints=[1, 2])) is None


def test_overflow_beside_content_is_ignored():
    content = struct.pack("<1f", 1.0)
    overflow = tb.bytes_field(5, struct.pack("<f", 1.0) * 1_000_001)
    payload = tb.varint_field(1, 1) + tb.bytes_field(2, shape(1)) + tb.bytes_field(4, content) + overflow
    assert digest(payload) is not None


def test_zero_length_content_counts_as_absent():
    payload = tb.varint_field(1, 1) + tb.bytes_field(2, shape(1)) + tb.bytes_field(4, b"")
    assert digest(payload) is None
    payload = tb.varint_field(1, 2) + tb.bytes_field(2, shape(2)) + tb.bytes_field(4, b"\x00" * 16)
    assert digest(payload) is not None


def test_unpacked_damage_refuses_the_file_but_packed_damage_does_not():
    good = tb.records(
        tb.event(step=9, summary_bytes=tb.summary(tb.value(tag=b"good", simple_value=1.5)))
    )
    unpacked = tb.varint_field(1, 3) + tb.bytes_field(2, shape(1)) + tb.key(7, 0) + b"\x80"
    packed = tb.varint_field(1, 3) + tb.bytes_field(2, shape(1)) + tb.bytes_field(7, b"\x80")
    with pytest.raises(EventFileError):
        list(iter_supported_summaries_09(events(unpacked) + good))
    records = list(iter_supported_summaries_09(events(packed) + good))
    assert [type(record).__name__ for record in records] == ["ScalarRecord"]


def test_wrong_wire_on_float_refuses_the_file():
    payload = tb.varint_field(1, 1) + tb.bytes_field(2, shape(1)) + tb.varint_field(5, 1)
    with pytest.raises(EventFileError):
        list(iter_supported_summaries_09(events(payload)))


def test_tensor_occurrences_are_independent_and_scalars_are_not_consumed():
    data = tb.records(
        tb.event(step=0, summary_bytes=tb.summary(tb.value(tag=b"t", simple_value=1.0))),
        tb.event(step=0, summary_bytes=tb.summary(tb.value(tag=b"t", tensor_bytes=tensor(1, [2], content=struct.pack("<2f", 1.0, 2.0))))),
        tb.event(step=0, summary_bytes=tb.summary(tb.value(tag=b"t", tensor_bytes=tensor(1, [2], content=struct.pack("<2f", 3.0, 4.0))))),
    )
    scalar, _, _, _, _, tensors = scan(
        data, scalars=[(b"t", 0, 0)], tensors=[(b"t", 0, 0), (b"t", 0, 1), (b"t", 0, 2)]
    )
    assert scalar[(b"t", 0, 0)].outcome is ScalarOutcome.value
    assert tensors[(b"t", 0, 0)].outcome is ScalarOutcome.value
    assert tensors[(b"t", 0, 1)].outcome is ScalarOutcome.value
    assert tensors[(b"t", 0, 2)].outcome is ScalarOutcome.mismatch


def test_ladder_failed_tensor_is_unsupported_encoding():
    _, _, _, _, _, tensors = scan(events(tensor(7, [2], ints=[1, 2])), tensors=[(b"t", 0, 0)])
    assert tensors[(b"t", 0, 0)].outcome is ScalarOutcome.unsupported_scalar


def test_ladder_scalar_entry_is_unsupported_value():
    data = tb.records(tb.event(step=0, summary_bytes=tb.summary(tb.value(tag=b"t", simple_value=1.0))))
    _, _, _, _, _, tensors = scan(data, tensors=[(b"t", 0, 0)])
    assert tensors[(b"t", 0, 0)].outcome is ScalarOutcome.unsupported_value


def test_two_outcome_asymmetry_for_a_rank_two_tensor():
    data = tb.records(
        tb.event(step=0, summary_bytes=tb.summary(tb.value(tag=b"t", tensor_bytes=tensor(1, [2, 2], content=struct.pack("<4f", 1, 2, 3, 4)))))
    )
    scalar, _, _, _, _, tensors = scan(data, scalars=[(b"t", 0, 0)], tensors=[(b"t", 0, 0)])
    assert scalar[(b"t", 0, 0)].outcome is ScalarOutcome.unsupported_value
    assert tensors[(b"t", 0, 0)].outcome is ScalarOutcome.value


def test_one_element_float_with_content_and_float_value_is_unsupported_scalar():
    payload = tb.varint_field(1, 1) + tb.bytes_field(2, shape(1)) + tb.bytes_field(4, struct.pack("<f", 1.0)) + tb.bytes_field(5, struct.pack("<f", 2.0))
    data = tb.records(tb.event(step=0, summary_bytes=tb.summary(tb.value(tag=b"t", tensor_bytes=payload))))
    scalar, _, _, _, _, tensors = scan(data, scalars=[(b"t", 0, 0)], tensors=[(b"t", 0, 0)])
    assert scalar[(b"t", 0, 0)].outcome is ScalarOutcome.unsupported_scalar
    assert tensors[(b"t", 0, 0)].outcome is ScalarOutcome.value


def test_rank_zero_int_fails_the_profile():
    payload = tb.varint_field(1, 3) + tb.bytes_field(2, b"") + tb.bytes_field(4, struct.pack("<i", 1))
    _, _, _, _, _, tensors = scan(events(payload), tensors=[(b"t", 0, 0)])
    assert tensors[(b"t", 0, 0)].outcome is ScalarOutcome.unsupported_scalar


def test_one_million_elements_is_the_boundary():
    payload = struct.pack("<4f", 1.0, 2.0, 3.0, 4.0)
    accepted = tb.varint_field(1, 1) + tb.bytes_field(2, shape(1_000_000)) + tb.bytes_field(
        4, payload * 250_000
    )
    value = digest(accepted)
    assert value is not None
    assert value["element_count"] == 1_000_000

    rejected = tb.varint_field(1, 1) + tb.bytes_field(2, shape(1_000_001)) + tb.bytes_field(
        4, payload * 250_000 + payload[:4]
    )
    assert digest(rejected) is None


def test_rank_eight_is_the_boundary():
    payload = struct.pack("<2f", 1.0, 2.0)
    accepted = tb.varint_field(1, 1) + tb.bytes_field(2, shape(1, 1, 1, 1, 1, 1, 1, 2)) + tb.bytes_field(4, payload)
    assert digest(accepted) is not None
    rejected = tb.varint_field(1, 1) + tb.bytes_field(2, shape(*([1] * 8 + [2]))) + tb.bytes_field(4, payload)
    assert digest(rejected) is None


def test_seeded_mutations_only_ever_refuse_the_profile():
    import random

    base = events(
        tensor(1, [2, 2], content=struct.pack("<4f", 1, 2, 3, 4)),
        tensor(3, [2], ints=[-1, 5]),
    )
    rng = random.Random(0x0907)
    for _ in range(400):
        mutated = bytearray(base)
        for _ in range(rng.randint(1, 4)):
            mutated[rng.randrange(len(mutated))] ^= 1 << rng.randrange(8)
        try:
            list(iter_supported_summaries_09(bytes(mutated)))
        except EventFileError:
            continue
        except Exception as exc:  # noqa: BLE001 - the fuzz asserts the exception class
            raise AssertionError(f"unexpected {type(exc).__name__}: {exc}") from exc


_LINUX = __import__("platform").system() == "Linux"

_MEASURE_SCRIPT = r"""
import json, resource, sys
from runevidence.tensorboard_events import scan_tensorboard_09

data = sys.stdin.buffer.read()
limit = 2_500_000 * 1024
resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
with open("/proc/self/status") as status:
    before = next(int(line.split()[1]) for line in status if line.startswith("VmHWM"))
results = scan_tensorboard_09(data, [], [], [], [], [], [(b"x", 0, 1_500_000)])
with open("/proc/self/status") as status:
    after = next(int(line.split()[1]) for line in status if line.startswith("VmHWM"))
selection = results[5][(b"x", 0, 1_500_000)]
print(json.dumps({"delta_mb": (after - before) / 1024, "outcome": selection.outcome.value}))
"""


@pytest.mark.skipif(not _LINUX, reason="VmHWM measurement is Linux-only")
def test_sixteen_mib_summary_of_millions_of_empty_tensor_spans_stays_bounded():
    import json
    import os
    import subprocess
    import sys

    entry = tb.bytes_field(1, tb.value(tag=b"x", tensor_bytes=tb.bytes_field(7, b"")))
    payload = entry * (16 * 1024 * 1024 // len(entry))
    assert len(payload) <= 16 * 1024 * 1024
    data = tb.records(tb.event(summary_bytes=payload))
    repo = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(repo / "src") + os.pathsep + environment.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "-c", _MEASURE_SCRIPT],
        input=data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=300,
        check=False,
        cwd=repo,
        env=environment,
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    measured = json.loads(completed.stdout)
    assert measured["delta_mb"] <= 300, measured
    assert measured["outcome"] == "unsupported-scalar"
