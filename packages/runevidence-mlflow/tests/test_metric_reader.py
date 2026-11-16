"""ML-1: the bounded MLflow metric-history reader."""

from __future__ import annotations

import math
import random

import pytest

from runevidence_mlflow import adapter as adapter_module
from runevidence_mlflow.adapter import _parse_summary

SEPARATORS = ["\n", "\r", "\r\n", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\u0085", "\u2028", "\u2029"]


def reference(data: bytes):
    """The delivered reader, kept as the oracle for small inputs."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    best = None
    for line in text.splitlines():
        if not line.strip():
            return None
        parts = line.split()
        if len(parts) == 3:
            timestamp_text, value_text, step_text = parts
        elif len(parts) == 2:
            timestamp_text, value_text = parts
            step_text = "0"
        else:
            return None
        try:
            timestamp = int(timestamp_text)
            step = int(step_text)
            value = float(value_text)
        except (ValueError, OverflowError):
            return None
        if not math.isfinite(value):
            return None
        key = (step, timestamp, value)
        if best is None or key > best[0]:
            best = (key, (value, step, timestamp))
    return None if best is None else best[1]


def test_reader_matches_the_delivered_oracle_on_generated_inputs():
    rng = random.Random(20261116)
    atoms = ["1", "2", "0.5", "-1", "nan", "inf", "x", "", " ", "\t", "\u00a0", "\u0661"]
    for _ in range(4000):
        pieces = []
        for _ in range(rng.randint(0, 8)):
            pieces.append(rng.choice(atoms))
            pieces.append(rng.choice(SEPARATORS + [" "]))
        text = "".join(pieces)
        data = text.encode("utf-8")
        assert _parse_summary(data) == reference(data), data


def test_reader_matches_the_oracle_on_invalid_utf8():
    for data in (b"\xff\xfe\n", b"1 0.5 1\n\xff", b"\xc3\n"):
        assert _parse_summary(data) == reference(data)


def test_byte_cap_boundary(monkeypatch):
    data = b"1 0.5 1\n"
    monkeypatch.setattr(adapter_module, "MAX_METRIC_BYTES", len(data))
    assert _parse_summary(data) is not None
    monkeypatch.setattr(adapter_module, "MAX_METRIC_BYTES", len(data) - 1)
    assert _parse_summary(data) is None


def test_row_cap_boundary(monkeypatch):
    data = b"1 0.5 1\n2 0.6 2\n"
    monkeypatch.setattr(adapter_module, "MAX_METRIC_ROWS", 2)
    assert _parse_summary(data) is not None
    monkeypatch.setattr(adapter_module, "MAX_METRIC_ROWS", 1)
    assert _parse_summary(data) is None


def test_line_cap_boundary(monkeypatch):
    data = b"1 0.5\n"
    monkeypatch.setattr(adapter_module, "MAX_METRIC_LINE_BYTES", len(data) - 1)
    assert _parse_summary(data) is not None
    monkeypatch.setattr(adapter_module, "MAX_METRIC_LINE_BYTES", len(data) - 2)
    assert _parse_summary(data) is None


def test_an_unterminated_line_is_refused_at_the_cap(monkeypatch):
    monkeypatch.setattr(adapter_module, "MAX_METRIC_LINE_BYTES", 8)
    assert _parse_summary(b"x" * 9) is None


def test_millions_of_empty_lines_do_not_allocate_a_line_list():
    import tracemalloc

    data = b"\n" * (5 * 1024 * 1024)
    tracemalloc.start()
    try:
        assert _parse_summary(data) is None
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # A splitlines() list would be tens of MiB; the iterator allocates almost nothing.
    assert peak < 1024 * 1024


def test_blank_row_refuses_the_whole_history():
    assert _parse_summary(b"1 0.5 1\n\n2 0.6 2\n") is None


def test_legacy_two_field_rows_and_ties():
    assert _parse_summary(b"2 0.5\n1 0.5\n") == (0.5, 0, 2)
    assert _parse_summary(b"1 0.5 2\n2 0.4 2\n") == (0.4, 2, 2)


def test_crlf_and_lone_cr_are_line_boundaries():
    assert _parse_summary(b"1 0.5 1\r\n2 0.6 2\r\n") == (0.6, 2, 2)
    assert _parse_summary(b"1 0.5 1\r2 0.6 2\r") == (0.6, 2, 2)


def test_empty_input_has_no_summary():
    assert _parse_summary(b"") is None
