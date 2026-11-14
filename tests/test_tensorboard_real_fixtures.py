"""Oracle test for real TensorBoard-written event files.

The bytes in ``tests/data/tensorboard`` were written by TensorBoard 2.20.0's
own ``EventFileWriter`` (see that directory's README). TensorBoard produced
the protobufs, TFRecord framing and CRC32C, so this test checks the reader
against an implementation it does not share code with.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
from pathlib import Path

import pytest

from runevidence.checks import run_checks
from runevidence.manifest import load_manifest
from runevidence.tensorboard_events import (
    ScalarOutcome,
    ScalarRecord,
    iter_supported_scalars,
    scan_scalars,
)

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "data" / "tensorboard"

PINS = {
    "legacy-simple-value.tfevents": (
        283,
        "21b6528ac0132b313c6bb506d9b3cd56bfe4992bee493c424132582c1dbf1ffe",
    ),
    "tensor-content.tfevents": (
        380,
        "dfaf40263c49a35dd9f30ed81a57ac1821d9881bd5cc773bbb4aa58a6e249a07",
    ),
    "graph-record.tfevents": (
        225,
        "35aa71290a9258e1b0d0ee7804e937da8f20820f5d7ddb909de294f804cbd06a",
    ),
}


@pytest.mark.parametrize("name", sorted(PINS))
def test_fixture_bytes_are_pinned(name):
    data = (FIXTURES / name).read_bytes()
    size, digest = PINS[name]
    assert len(data) == size
    assert hashlib.sha256(data).hexdigest() == digest


def _same(record: ScalarRecord, tag: bytes, step: int, value: float, float32: bool) -> bool:
    if record.tag != tag or record.step != step or record.float32 is not float32:
        return False
    if math.isnan(value):
        return math.isnan(record.value)
    return record.value == value


def test_legacy_simple_value_records():
    records = list(iter_supported_scalars((FIXTURES / "legacy-simple-value.tfevents").read_bytes()))
    assert len(records) == 6
    expected = [
        (b"loss", 1, 0.5, True),
        (b"accuracy", 1, 0.75, True),
        (b"loss", 2, 0.25, True),
        (b"loss", 3, math.nan, True),
        (b"inf", 4, math.inf, True),
        (b"neg_inf", 4, -math.inf, True),
    ]
    assert all(_same(record, *want) for record, want in zip(records, expected))


def test_tensor_content_and_packed_records():
    records = list(iter_supported_scalars((FIXTURES / "tensor-content.tfevents").read_bytes()))
    assert records == [
        ScalarRecord(b"f32_content", 10, struct.unpack("<f", struct.pack("<f", 0.25))[0], True),
        ScalarRecord(b"f64_content", 10, 0.1, False),
        ScalarRecord(b"f32_rank1", 10, 1.5, True),
        ScalarRecord(b"f64_packed", 10, 0.2, False),
        ScalarRecord(b"f32_packed", 10, struct.unpack("<f", struct.pack("<f", 0.3))[0], True),
    ]
    # The rank-2, int and half tensors are not supported scalars.
    assert {r.tag for r in records} == {
        b"f32_content",
        b"f64_content",
        b"f32_rank1",
        b"f64_packed",
        b"f32_packed",
    }


def test_graph_record_is_ignored_and_later_scalars_still_parse():
    data = (FIXTURES / "graph-record.tfevents").read_bytes()
    assert list(iter_supported_scalars(data)) == [
        ScalarRecord(b"loss", 7, struct.unpack("<f", struct.pack("<f", 0.9))[0], True),
        ScalarRecord(b"loss", 8, struct.unpack("<f", struct.pack("<f", 0.95))[0], True),
    ]


def test_selection_outcomes_on_the_legacy_fixture():
    data = (FIXTURES / "legacy-simple-value.tfevents").read_bytes()
    results = scan_scalars(
        data,
        [
            (b"loss", 1, 0),
            (b"loss", 1, 1),
            (b"loss", 3, 0),
            (b"accuracy", 1, 0),
            (b"absent", 0, 0),
        ],
    )
    assert results[(b"loss", 1, 0)].outcome is ScalarOutcome.value
    assert results[(b"loss", 1, 0)].value == 0.5
    assert results[(b"loss", 1, 0)].float32 is True
    assert results[(b"loss", 1, 1)].outcome is ScalarOutcome.mismatch
    assert results[(b"loss", 3, 0)].outcome is ScalarOutcome.value
    assert math.isnan(results[(b"loss", 3, 0)].value)
    assert results[(b"accuracy", 1, 0)].outcome is ScalarOutcome.value
    assert results[(b"absent", 0, 0)].outcome is ScalarOutcome.mismatch


def test_unsupported_scalar_and_non_scalar_selection_on_real_tensors():
    data = (FIXTURES / "tensor-content.tfevents").read_bytes()
    results = scan_scalars(
        data,
        [
            (b"int_scalar", 11, 0),
            (b"half_scalar", 11, 0),
            (b"f32_2d", 11, 0),
        ],
    )
    assert results[(b"int_scalar", 11, 0)].outcome is ScalarOutcome.unsupported_scalar
    assert results[(b"half_scalar", 11, 0)].outcome is ScalarOutcome.unsupported_scalar
    assert results[(b"f32_2d", 11, 0)].outcome is ScalarOutcome.unsupported_value


def test_a_0_5_manifest_over_a_real_fixture_verifies(tmp_path):
    data = (FIXTURES / "legacy-simple-value.tfevents").read_bytes()
    (tmp_path / "events.tfevents.1").write_bytes(data)
    document = {
        "schema_version": "0.5",
        "project": {"id": "p", "name": "P"},
        "runs": [{"id": "run", "name": "R"}],
        "artifacts": [
            {
                "id": "a",
                "path": "events.tfevents.1",
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
        ],
        "results": [
            {
                "id": "acc",
                "run_id": "run",
                "name": "accuracy",
                "value": 0.75,
                "artifact_id": "a",
                "locator": {"kind": "tensorboard", "tag": "accuracy", "step": 1, "occurrence": 0},
            },
            {
                "id": "zero",
                "run_id": "run",
                "name": "loss",
                "value": 0.5,
                "artifact_id": "a",
                "locator": {"kind": "tensorboard", "tag": "loss", "step": 1, "occurrence": 0},
            },
        ],
        "evidence": [{"id": "claim", "claim": "real file", "supports": ["acc", "zero"]}],
    }
    (tmp_path / "runevidence.json").write_text(json.dumps(document), encoding="utf-8")
    report = run_checks(load_manifest(tmp_path / "runevidence.json"))
    assert report.findings == []
    assert report.exit_code() == 0
