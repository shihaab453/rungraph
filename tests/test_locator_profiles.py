"""Strict JSONL / whole-document profiles and the 0.3 scalar comparator."""

from __future__ import annotations

import math
import time
import tracemalloc

import pytest

from runevidence.locator_profiles import (
    MAX_JSONL_LINE_BYTES,
    LocatorMismatch,
    ProfileError,
    compare_values,
    display_locator,
    parse_json_document,
    parse_jsonl_line,
)


def test_jsonl_bom_crlf_and_unterminated_final_line_are_selectable():
    assert parse_jsonl_line(b'\xef\xbb\xbf{"v": 1}\n', 1) == {"v": 1}
    assert parse_jsonl_line(b'{"v": 1}\r\n', 1) == {"v": 1}
    assert parse_jsonl_line(b'{"v": 1}', 1) == {"v": 1}


def test_jsonl_bom_is_stripped_before_the_line_cap():
    # A line at exactly the cap is accepted; the three BOM bytes must not
    # push it over. Before the fix this raised ProfileError("line over cap").
    payload = b'"' + b"x" * (MAX_JSONL_LINE_BYTES - 2) + b'"'
    assert len(payload) == MAX_JSONL_LINE_BYTES
    assert parse_jsonl_line(b"\xef\xbb\xbf" + payload + b"\n", 1) == "x" * (MAX_JSONL_LINE_BYTES - 2)


def test_jsonl_bare_cr_is_refused_even_when_it_is_legal_json_whitespace():
    # A CR between tokens is legal JSON whitespace. Only the file-wide
    # bare-CR refusal makes this unreadable, so removing that refusal would
    # let it parse and this test would fail.
    with pytest.raises(ProfileError):
        parse_jsonl_line(b'{"v":\r1}\n', 1)
    with pytest.raises(ProfileError):
        parse_json_document(b'{"v":\r1}')


def test_jsonl_bare_cr_on_an_unselected_line_is_refused_file_wide():
    # The CR is on line 1, which is not selected, and line 2 alone is valid.
    # File-wide refusal is the invariant; if it is removed, line 2 parses.
    with pytest.raises(ProfileError):
        parse_jsonl_line(b'{"other":\r1}\n{"v": 0.9}\n', 2)


def test_jsonl_escaped_cr_and_crlf_on_unselected_lines_are_accepted():
    assert parse_jsonl_line(b'{"v": "a\\rb"}\n', 1) == {"v": "a\rb"}
    assert parse_jsonl_line(b'{"other": 1}\r\n{"v": 2}\n', 2) == {"v": 2}


def test_jsonl_counts_physical_lines_and_tolerates_other_malformed_lines():
    assert parse_jsonl_line(b'not json\n{"v": 2}\n', 2) == {"v": 2}
    assert parse_jsonl_line(b'\n{"v": 2}\n', 2) == {"v": 2}


@pytest.mark.parametrize("data,line", [(b"", 1), (b"\n", 1), (b"   \n", 1), (b'{"v":1}\n', 2)])
def test_jsonl_blank_or_out_of_range_line_is_mismatch(data, line):
    with pytest.raises(LocatorMismatch):
        parse_jsonl_line(data, line)


@pytest.mark.parametrize(
    "data",
    [
        b'{"a": 1}\x00\n',
        b'{"a": 1}\rX\n',  # bare CR
        b'\xff\n',
        b'{"a": 1, "a": 2}\n',
        b'{"a": 1e999}\n',
        b'{"a": 1e-999}\n',
        b'[' * 65 + b']' * 65 + b'\n',
        b'{"a": "' + b"x" * (MAX_JSONL_LINE_BYTES + 1) + b'"}\n',
    ],
)
def test_jsonl_file_wide_refusals(data):
    with pytest.raises(ProfileError):
        parse_jsonl_line(data, 1)


def test_jsonl_reader_does_not_materialise_every_line():
    """A short-line file must not become one Python string per line.

    The old reader decoded every line into a list, which peaked at 2,003,920 KB
    on a 52,428,744-byte file — the recorded baseline; the incident path is
    never re-measured. 200,000 lines of eight characters already separate the
    designs: eager decoding allocates ~20 MiB of per-line objects, the current
    reader decodes the file text once and slices only the selected line (~1.7
    MiB traced). tracemalloc is safe here because the input is ~1.8 MiB, not
    the 52 MiB reproducer.
    """
    lines = 200_000
    data = b"12345678\n" * lines
    tracemalloc.start()
    try:
        assert parse_jsonl_line(data, 1) == 12345678
        assert parse_jsonl_line(data, lines) == 12345678
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 6 * 1024 * 1024, peak


def test_cr_check_is_two_counts_not_a_per_cr_walk(monkeypatch):
    """A CRLF-heavy file must not cost one Python step per CR.

    The check is a single C-level pair of counts. Reverting to a per-CR loop
    (or dropping the helper) makes this fail even though every refusal still
    behaves the same.
    """
    import runevidence.locator_profiles as profiles

    calls: list[int] = []
    real = profiles._crlf_only

    def counting(data):
        calls.append(len(data))
        return real(data)

    monkeypatch.setattr(profiles, "_crlf_only", counting)
    data = b"0\r\n" * 500_000
    started = time.perf_counter()
    assert parse_jsonl_line(data, 1) == 0
    elapsed = time.perf_counter() - started
    assert calls == [len(data)]
    assert elapsed < 1.0, elapsed

    with pytest.raises(ProfileError):
        parse_jsonl_line(b"0\r0\r\n", 1)
    assert calls == [len(data), len(b"0\r0\r\n")]


def test_invalid_utf8_on_an_unselected_line_is_refused_file_wide():
    # Validation is one decode of the whole file, so a bad byte on another
    # line still refuses the file; decoding only the selected line would not.
    with pytest.raises(ProfileError):
        parse_jsonl_line(b'{"v": 1}\n\xff\n', 1)


def test_file_scan_never_enumerates_every_byte(monkeypatch):
    """NUL/CR scans are bytes-level ``find`` calls, not a Python byte loop.

    Reverting ``_check_file_bytes`` to ``for index, byte in enumerate(data)``
    makes this test fail even though every refusal still works, because the
    loop is the 50M-iteration cost this guards against.
    """
    import runevidence.locator_profiles as profiles

    def boom(*args, **kwargs):
        raise AssertionError("the file scan iterated over every byte in Python")

    monkeypatch.setattr(profiles, "enumerate", boom, raising=False)
    assert profiles.parse_jsonl_line(b'{"v": 1}\n{"v": 2}\n', 2) == {"v": 2}
    assert profiles.parse_json_document(b'{"v": 1}') == {"v": 1}
    with pytest.raises(ProfileError):
        profiles.parse_jsonl_line(b'{"v": 1}\rX\n', 1)


@pytest.mark.parametrize("token", [b"NaN", b"Infinity", b"-Infinity"])
def test_jsonl_accepts_closed_nonfinite_tokens(token):
    value = parse_jsonl_line(b'{"v": ' + token + b'}\n', 1)["v"]
    assert not math.isfinite(value)


@pytest.mark.parametrize("digits", [512, 513])
def test_jsonl_integer_digit_cap(digits):
    data = b'{"v": ' + b"9" * digits + b'}\n'
    if digits == 512:
        assert parse_jsonl_line(data, 1)["v"] == int("9" * digits)
    else:
        with pytest.raises(ProfileError):
            parse_jsonl_line(data, 1)


def test_distinct_jsonl_lines_are_found_in_one_scan(monkeypatch):
    """Selecting lines 991-1000 must not rescan from line 1 for each one.

    The per-selection walk costs 9,955 newline searches for this 1,000-line
    document; the one-pass index costs one per line. Reverting
    ``index_lines`` (or its use) makes this count fail.
    """
    import runevidence.locator_profiles as profiles

    searches: list[tuple] = []
    real_decode = profiles._decode

    class CountingText(str):
        def find(self, *args):
            searches.append(args)
            return super().find(*args)

    monkeypatch.setattr(profiles, "_decode", lambda data: CountingText(real_decode(data)))
    data = b"".join(b'{"v": %d}\n' % i for i in range(1, 1001))
    document = profiles.JsonlDocument(data)

    before = len(searches)
    document.index_lines(range(991, 1001))
    assert len(searches) - before <= 1005, len(searches) - before
    assert [document.line(line) for line in range(991, 1001)] == [
        {"v": line} for line in range(991, 1001)
    ]
    assert len(searches) - before <= 1015, len(searches) - before


def test_jsonl_index_keeps_only_requested_offsets():
    """A million-line file must not get a million-entry offset table."""
    import runevidence.locator_profiles as profiles

    document = profiles.JsonlDocument(b'{"v": 1}\n' * 1_000_000)
    assert document._offsets == {}
    document.index_lines([999_995, 1_000_000])
    assert set(document._offsets) == {999_995, 1_000_000}
    assert document.line(1_000_000) == {"v": 1}


def test_whole_document_profile_rules():
    assert parse_json_document(b"\xef\xbb\xbf 1 ") == 1
    assert parse_json_document(b'{"v": NaN}')["v"] != parse_json_document(b'{"v": NaN}')["v"]
    for data in (
        b'{"a": 1, "a": 2}',
        b'{"a": 1}\x00',
        b'{"a": 1}\rX',
        b'{"a": 1e999}',
        b'{"a": 1e-999}',
        b'[' * 65 + b']' * 65,
    ):
        with pytest.raises(ProfileError):
            parse_json_document(data)


def test_whole_document_512_digit_integer_and_4300_digit_refusal():
    assert parse_json_document(b"9" * 512) == int("9" * 512)
    with pytest.raises(ProfileError):
        parse_json_document(b"9" * 4300)


def test_comparator_rules():
    assert compare_values(9007199254740993, 9007199254740993)
    assert not compare_values(9007199254740993, 9007199254740992)
    assert compare_values(1, 1.0000000005)
    assert not compare_values(True, 1)
    assert not compare_values(False, 0)
    assert compare_values(float("nan"), float("nan"))
    assert compare_values(float("inf"), float("inf"))
    assert not compare_values(float("inf"), float("-inf"))
    assert compare_values(None, None)
    assert not compare_values(None, 0)


def test_display_locator_is_escaped_and_bounded():
    class Locator:
        line = 41
        pointer = "/val_rmse\n"

    text = display_locator(Locator())
    assert text.startswith("jsonl:41#/val_rmse\\n")
    assert len(display_locator(Locator(), limit=12)) == 12


def test_display_locator_escapes_prefix_delimiters_in_the_payload():
    class Locator:
        line = 1
        pointer = "/a#b,c@d"

    text = display_locator(Locator())
    assert text.startswith("jsonl:1#")
    assert "\\u0023" in text and "\\u002c" in text and "\\u0040" in text
    assert display_locator("/x#y").startswith("json#")
