"""Strict ``csv-cell`` profile: RFC section 11.1-11.3 vectors.

Phase 2 pins the standalone profile only: dialect accept/refuse vectors,
BOM/newline/quote rules, the structural caps, header metadata, row/column
selection, typed refusals and the RFC section 7 numeric grammar.  Integration
into ``checks/results.py``/``files.py`` is a later phase.
"""

from __future__ import annotations

import math

import pytest

from runevidence.checks.csv import (
    MAX_COLUMNS,
    MAX_DATA_ROWS,
    MAX_FIELD_BYTES,
    MAX_RECORD_BYTES,
    MAX_TOTAL_CELLS,
    PROFILE_REASONS,
    CsvColumnOutOfRange,
    CsvNotNumeric,
    CsvNumberOverflow,
    CsvProfile,
    CsvProfileError,
    CsvRowOutOfRange,
    compare_values,
    parse_number,
    require_number,
)
from runevidence.locator_profiles import MAX_FILE_BYTES

BOM = b"\xef\xbb\xbf"


# --- helpers ---------------------------------------------------------------


def _record(fields: list[bytes]) -> bytes:
    return b",".join(fields)


def _header(names: list[str]) -> bytes:
    return _record([name.encode("utf-8") for name in names])


def _row(cells: list[str]) -> bytes:
    return _record([cell.encode("utf-8") for cell in cells])


def _wide_file(columns: int, rows: int) -> bytes:
    names = [f"c{index}" for index in range(columns)]
    cells = ["1"] * columns
    return _header(names) + b"\n" + (_row(cells) + b"\n") * rows


def _unquoted_record(sizes: list[int]) -> bytes:
    return _record([b"a" * size for size in sizes])


def _quoted_record(sizes: list[int]) -> bytes:
    return _record([b'"' + b"a" * size + b'"' for size in sizes])


def _long_header(sizes: list[int]) -> bytes:
    fields: list[bytes] = []
    for index, size in enumerate(sizes):
        prefix = f"h{index}_".encode("ascii")
        assert size >= len(prefix)
        fields.append(prefix + b"z" * (size - len(prefix)))
    return _record(fields)


# --- RFC 11.1 accept vectors ------------------------------------------------


ACCEPT_VECTORS = [
    pytest.param(b"a,b\n1,2\n", ("a", "b"), (("1", "2"),), id="lf"),
    pytest.param(b"a,b\r\n1,2\r\n", ("a", "b"), (("1", "2"),), id="crlf"),
    pytest.param(b'"a","b"\n"1","2"', ("a", "b"), (("1", "2"),), id="quoted"),
    pytest.param(b'a,b\n"x,y",2\n', ("a", "b"), (("x,y", "2"),), id="comma-in-quotes"),
    pytest.param(b'a,b\n"x\ny",2\n', ("a", "b"), (("x\ny", "2"),), id="lf-in-quotes"),
    pytest.param(
        b'a,b\r\n"x\r\ny",2\r\n',
        ("a", "b"),
        (("x\r\ny", "2"),),
        id="crlf-in-quotes-preserved",
    ),
    pytest.param(b'a\n"x""y"\n', ("a",), (('x"y',),), id="doubled-quote"),
    pytest.param(
        b'a\n"x\n\ny"\n',
        ("a",),
        (("x\n\ny",),),
        id="blank-line-inside-quotes",
    ),
    pytest.param(b"a,b\n1,2", ("a", "b"), (("1", "2"),), id="unterminated-final"),
    pytest.param(b"a,b\n,2\n", ("a", "b"), (("", "2"),), id="empty-field"),
    pytest.param(b"a,b,c\n,,\n", ("a", "b", "c"), (("", "", ""),), id="empty-fields"),
    pytest.param(b"v\n1\n", ("v",), (("1",),), id="single-column"),
    pytest.param(BOM + b"a,b\n1,2\n", ("a", "b"), (("1", "2"),), id="leading-bom"),
    pytest.param(
        BOM + BOM + b"a\n1\n",
        ("\ufeffa",),
        (("1",),),
        id="second-bom-is-data",
    ),
    pytest.param(
        b"a\xef\xbb\xbfb\n1\n",
        ("a\ufeffb",),
        (("1",),),
        id="interior-bom-header",
    ),
    pytest.param(
        b"a\nx\xef\xbb\xbfy\n",
        ("a",),
        (("x\ufeffy",),),
        id="interior-bom-cell",
    ),
    pytest.param(b'a,b\n" 1 ", 2 \n', ("a", "b"), ((" 1 ", " 2 "),), id="whitespace-data"),
    pytest.param(b'a\n""\n', ("a",), (("",),), id="empty-quoted-cell"),
    pytest.param(b'"a,b",c\n1,2\n', ("a,b", "c"), (("1", "2"),), id="quoted-header-comma"),
    pytest.param(b'a\n"1"\n', ("a",), (("1",),), id="quoted-number-transport"),
    pytest.param(b"a,b\n1,\n", ("a", "b"), (("1", ""),), id="trailing-empty-field"),
]


@pytest.mark.parametrize("data,header,rows", ACCEPT_VECTORS)
def test_accept_vectors(data, header, rows):
    profile = CsvProfile(data, rows=set(range(1, len(rows) + 1)))
    assert profile.header == header
    assert profile.column_count == len(header)
    assert profile.row_count == len(rows)
    for row_number, expected in enumerate(rows, 1):
        actual = tuple(
            profile.cell(row_number, column)
            for column in range(1, profile.column_count + 1)
        )
        assert actual == expected


def test_bom_rules_and_quoted_crlf_explicitly():
    stripped = CsvProfile(BOM + b"a,b\r\n1,2\r\n", rows={1})
    assert stripped.header == ("a", "b")
    assert stripped.cell(1, 2) == "2"

    second_bom = CsvProfile(BOM + BOM + b"a\n1\n")
    assert second_bom.header == ("\ufeffa",)

    interior = CsvProfile(b"header\nx\xef\xbb\xbfy\n")
    assert interior.cell(1, 1) == "x\ufeffy"

    crlf = CsvProfile(b'a,b\r\n"x\r\ny",2\r\n')
    assert crlf.cell(1, 1) == "x\r\ny"


def test_non_terminating_control_bytes_are_data():
    # TAB, VT, FF, C0 other than CR/LF/NUL, DEL and the UTF-8 forms of C1,
    # NEL, LS and PS are all data.  None of these may split a record.
    payload = b"\x01\t\x0b\x0c\x1f\x7f" + "\u0080\u0085\u009f\u2028\u2029".encode(
        "utf-8"
    )
    profile = CsvProfile(b"h\n" + payload + b"\n", rows={1})
    assert profile.row_count == 1
    assert profile.cell(1, 1) == payload.decode("utf-8")


def test_multibyte_utf8_header_and_cell():
    data = "héader,值\ncafé,😀\n".encode("utf-8")
    profile = CsvProfile(data, rows={1})
    assert profile.header == ("héader", "值")
    assert profile.cell(1, 1) == "café"
    assert profile.cell(1, 2) == "😀"


def test_header_and_data_can_contain_quoted_newlines():
    data = b'"h\n1","h\r\n2"\n"v\n1","v\r\n2"\n'
    profile = CsvProfile(data, rows={1})
    assert profile.header == ("h\n1", "h\r\n2")
    assert profile.cell(1, 1) == "v\n1"
    assert profile.cell(1, 2) == "v\r\n2"


# --- RFC 11.2 refuse vectors ------------------------------------------------


REFUSE_VECTORS = [
    pytest.param(b"a\n1\x00\n", "nul-byte", id="nul-data"),
    pytest.param(b"a\n\x00\n", "nul-byte", id="nul-cell"),
    pytest.param(b'a\n"x\x00y"\n', "nul-byte", id="nul-quoted"),
    pytest.param(b"a\rb\n", "bare-cr", id="bare-cr-data"),
    pytest.param(b"a\r\n1\r2\r\n", "bare-cr", id="bare-cr-second-record"),
    pytest.param(b'a\n"x\ry"\n', "bare-cr", id="bare-cr-quoted"),
    pytest.param(b"a\n1\n\n", "blank-record", id="blank-lf"),
    pytest.param(b"a\r\n1\r\n\r\n", "blank-record", id="blank-crlf"),
    pytest.param(b"a\n\n", "blank-record", id="blank-after-header"),
    pytest.param(b"\n", "blank-record", id="blank-only"),
    pytest.param(b"a\n1\n\r\n", "blank-record", id="blank-crlf-after-data"),
    pytest.param(
        b'a,b\n1,x"y\n',
        "quote-in-unquoted-field",
        id="quote-in-unquoted",
    ),
    pytest.param(b'a\n"x\n', "unclosed-quote", id="unclosed-quote"),
    pytest.param(
        b'a\n"x"y\n',
        "data-after-closing-quote",
        id="data-after-closing-quote",
    ),
    pytest.param(
        b'a\n"x" \n',
        "data-after-closing-quote",
        id="space-after-closing-quote",
    ),
    pytest.param(b"a,b\n1\n", "ragged-record", id="ragged-short"),
    pytest.param(b"a,b\n1,2,3\n", "ragged-record", id="ragged-long"),
    pytest.param(b"a,b\n", "header-only", id="header-only-terminated"),
    pytest.param(b"a,b", "header-only", id="header-only-unterminated"),
    pytest.param(b"", "empty-file", id="empty-file"),
    pytest.param(BOM, "empty-file", id="bom-only"),
    pytest.param(b'"",b\n1,2\n', "header-empty", id="header-empty-quoted"),
    pytest.param(b"a,,b\n1,2,3\n", "header-empty", id="header-empty-unquoted"),
    pytest.param(b'a,"a"\n1,2\n', "header-duplicate", id="header-duplicate-parsed"),
    pytest.param(b"a,a\n1,2\n", "header-duplicate", id="header-duplicate-plain"),
    pytest.param(b"\xff\n1\n", "invalid-utf8", id="invalid-utf8-header"),
    pytest.param(b"a\n1\n\xff\n", "invalid-utf8", id="invalid-utf8-data"),
    pytest.param(b'a\n"\xff"\n', "invalid-utf8", id="invalid-utf8-quoted"),
]


@pytest.mark.parametrize("data,reason", REFUSE_VECTORS)
def test_refuse_vectors(data, reason):
    with pytest.raises(CsvProfileError) as caught:
        CsvProfile(data)
    assert caught.value.reason == reason
    assert reason in PROFILE_REASONS
    assert caught.value.reason in str(caught.value)


# --- BOM / newline interactions --------------------------------------------


def test_invalid_utf8_is_file_wide_even_when_the_row_is_not_selected():
    with pytest.raises(CsvProfileError) as caught:
        CsvProfile(b"a\n1\n\xff\n", rows={1})
    assert caught.value.reason == "invalid-utf8"


def test_decode_helper_accepts_at_most_one_record_of_bytes():
    from runevidence.checks.csv import _decode_bytes

    assert _decode_bytes(b"value") == "value"
    with pytest.raises(CsvProfileError) as caught:
        _decode_bytes(b"x" * (MAX_RECORD_BYTES + 1))
    assert caught.value.reason == "record-over-cap"


def test_incremental_utf8_validation_handles_a_character_split_at_1_mib():
    """A naive per-chunk decode would refuse this: the 3-byte character's
    first byte is at offset 1,048,575 and its continuation bytes are in the
    next chunk.  The incremental decoder must carry the partial sequence."""
    data = bytearray(b"h\n")
    data += (b"x" * MAX_FIELD_BYTES + b"\n") * 15
    assert len(data) == 983_057
    filler = 1_048_575 - len(data)
    assert filler == 65_518
    data += b"x" * filler + "é".encode("utf-8") + b"\n"
    assert len(data) == 1_048_578

    profile = CsvProfile(bytes(data), rows={16})
    assert profile.row_count == 16
    assert profile.cell(16, 1) == "x" * filler + "é"


# --- structural caps --------------------------------------------------------


def test_columns_at_cap_and_one_over():
    at_cap = _wide_file(MAX_COLUMNS, 1)
    profile = CsvProfile(at_cap, rows={1})
    assert profile.column_count == 1024
    assert profile.header[-1] == "c1023"
    assert profile.cell(1, 1024) == "1"

    with pytest.raises(CsvProfileError) as caught:
        CsvProfile(_wide_file(MAX_COLUMNS + 1, 1))
    assert caught.value.reason == "columns-over-cap"


@pytest.mark.parametrize(
    "cell,quoted,accepted",
    [
        pytest.param(b"x" * MAX_FIELD_BYTES, False, True, id="ascii-unquoted-at-cap"),
        pytest.param(b"x" * (MAX_FIELD_BYTES + 1), False, False, id="ascii-unquoted-over"),
        pytest.param(b"x" * MAX_FIELD_BYTES, True, True, id="ascii-quoted-at-cap"),
        pytest.param(b"x" * (MAX_FIELD_BYTES + 1), True, False, id="ascii-quoted-over"),
        pytest.param(
            ("é" * 32_768).encode("utf-8"),
            True,
            True,
            id="multibyte-quoted-at-cap",
        ),
        pytest.param(
            ("é" * 32_768 + "a").encode("utf-8"),
            True,
            False,
            id="multibyte-quoted-over",
        ),
    ],
)
def test_field_cap_at_and_over(cell, quoted, accepted):
    raw = b'"' + cell + b'"' if quoted else cell
    document = b"h\n" + raw + b"\n"
    if accepted:
        profile = CsvProfile(document, rows={1})
        assert profile.cell(1, 1) == cell.decode("utf-8")
    else:
        with pytest.raises(CsvProfileError) as caught:
            CsvProfile(document)
        assert caught.value.reason == "field-over-cap"


def _cap_record_sizes(over: bool) -> list[int]:
    # 15 full 64 KiB fields prove the field cap is not what refuses an
    # over-cap record; the sixteenth field absorbs the difference.
    adjustment = 1 if over else 0
    return [65_521 + adjustment] + [MAX_FIELD_BYTES] * 15


def test_record_at_cap_is_accepted():
    sizes = _cap_record_sizes(over=False)
    record = _unquoted_record(sizes)
    assert len(record) == MAX_RECORD_BYTES
    header = _header([f"c{index}" for index in range(16)])
    profile = CsvProfile(header + b"\n" + record + b"\n", rows={1})
    assert profile.cell(1, 1) == "a" * sizes[0]
    assert profile.cell(1, 16) == "a" * sizes[-1]


def test_record_with_bom_is_capped_after_bom_strip():
    sizes = _cap_record_sizes(over=False)
    record = _unquoted_record(sizes)
    header = _header([f"c{index}" for index in range(16)])
    profile = CsvProfile(BOM + header + b"\n" + record + b"\n", rows={1})
    assert profile.cell(1, 1) == "a" * sizes[0]


def test_quoted_record_at_cap_is_accepted():
    sizes = [MAX_FIELD_BYTES] * 15 + [65_489]
    record = _quoted_record(sizes)
    assert len(record) == MAX_RECORD_BYTES
    header = _header([f"c{index}" for index in range(16)])
    profile = CsvProfile(header + b"\n" + record + b"\n", rows={1})
    assert profile.cell(1, 16) == "a" * 65_489


def test_record_one_byte_over_cap_is_refused():
    sizes = _cap_record_sizes(over=True)
    record = _unquoted_record(sizes)
    assert len(record) == MAX_RECORD_BYTES + 1
    header = _header([f"c{index}" for index in range(16)])
    with pytest.raises(CsvProfileError) as caught:
        CsvProfile(header + b"\n" + record + b"\n")
    assert caught.value.reason == "record-over-cap"


def test_header_record_at_cap_is_accepted():
    sizes = _cap_record_sizes(over=False)
    header = _long_header(sizes)
    assert len(header) == MAX_RECORD_BYTES
    profile = CsvProfile(header + b"\n" + _row(["1"] * 16) + b"\n", rows={1})
    assert len(profile.header[0]) == sizes[0]


def test_header_record_one_byte_over_cap_is_refused():
    sizes = _cap_record_sizes(over=True)
    header = _long_header(sizes)
    assert len(header) == MAX_RECORD_BYTES + 1
    with pytest.raises(CsvProfileError) as caught:
        CsvProfile(header + b"\n" + _row(["1"] * 16) + b"\n")
    assert caught.value.reason == "record-over-cap"


def test_rows_at_cap_and_one_over():
    at_cap = b"v\n" + b"1\n" * MAX_DATA_ROWS
    profile = CsvProfile(at_cap, rows={MAX_DATA_ROWS})
    assert profile.row_count == MAX_DATA_ROWS
    assert profile.cell(MAX_DATA_ROWS, 1) == "1"

    over_cap = b"v\n" + b"1\n" * (MAX_DATA_ROWS + 1)
    with pytest.raises(CsvProfileError) as caught:
        CsvProfile(over_cap)
    assert caught.value.reason == "rows-over-cap"


def test_cells_at_cap_and_one_over():
    # 500,000 x 20 is exactly 10,000,000 data cells; 909,091 x 11 is
    # 10,000,001.  Both stay under the 1,000,000-row cap.
    at_cap = _wide_file(20, 500_000)
    profile = CsvProfile(at_cap, rows={500_000})
    assert profile.row_count == 500_000
    assert profile.column_count == 20
    assert profile.cell(500_000, 20) == "1"

    over_cap = _wide_file(11, 909_091)
    with pytest.raises(CsvProfileError) as caught:
        CsvProfile(over_cap)
    assert caught.value.reason == "cells-over-cap"
    assert MAX_TOTAL_CELLS == 10_000_000


def test_file_cap_is_refused_before_parsing():
    # The file cap is raw on-disk bytes.  The class refuses defensively even
    # though the phase-3 file check supplies Snapshot.over_cap first.
    data = b"x" * (51 * 1024 * 1024)
    assert len(data) > MAX_FILE_BYTES
    with pytest.raises(CsvProfileError) as caught:
        CsvProfile(data)
    assert caught.value.reason == "file-over-cap"


# --- selection and header metadata -----------------------------------------


def test_row_and_column_indexing_and_selection_order():
    profile = CsvProfile(
        b"a,b\nr1c1,r1c2\nr2c1,r2c2\nr3c1,r3c2\n",
        rows={1, 3},
    )
    assert profile.row_count == 3
    assert profile.cell(1, 1) == "r1c1"
    assert profile.cell(1, 2) == "r1c2"
    assert profile.cell(3, 2) == "r3c2"

    # A row outside the requested set is located by the bounded fallback.
    assert profile.cell(2, 1) == "r2c1"
    assert profile._offsets is not None
    assert set(profile._offsets) == {1, 3}

    with pytest.raises(CsvRowOutOfRange):
        profile.cell(0, 1)  # the header is never selectable
    with pytest.raises(CsvRowOutOfRange):
        profile.cell(4, 1)
    with pytest.raises(CsvColumnOutOfRange):
        profile.cell(1, 0)
    with pytest.raises(CsvColumnOutOfRange):
        profile.cell(1, 3)
    # Fixed order row -> column: a bad row wins over a bad column.
    with pytest.raises(CsvRowOutOfRange):
        profile.cell(4, 3)


def test_rows_none_retains_no_offsets_but_still_reads():
    profile = CsvProfile(b"a\n1\n2\n")
    assert profile._offsets is None
    assert profile.cell(2, 1) == "2"
    assert profile._offsets is None


def test_only_requested_rows_store_offsets():
    profile = CsvProfile(b"a\n1\n2\n3\n", rows={1, 3})
    assert profile._offsets == {1: 2, 3: 6}
    assert profile.cell(2, 1) == "2"
    assert profile._offsets == {1: 2, 3: 6}


def test_header_metadata_is_exact_and_immutable():
    profile = CsvProfile(b'a,"b,c"\n1,2\n', rows={1})
    assert profile.header == ("a", "b,c")
    assert profile.column_count == 2
    with pytest.raises(TypeError):
        profile.header[0] = "changed"  # type: ignore[index]


def test_profile_refusal_never_leaks_cell_or_header_text():
    with pytest.raises(CsvProfileError) as duplicate:
        CsvProfile(b'SECRET,"SECRET"\n1,2\n')
    assert duplicate.value.reason == "header-duplicate"
    for rendered in (str(duplicate.value), repr(duplicate.value), *map(str, duplicate.value.args)):
        assert "SECRET" not in rendered

    with pytest.raises(CsvProfileError) as ragged:
        CsvProfile(b"a,b\nSECRET\n")
    assert ragged.value.reason == "ragged-record"
    for rendered in (str(ragged.value), repr(ragged.value), *map(str, ragged.value.args)):
        assert "SECRET" not in rendered

    with pytest.raises(CsvNotNumeric) as numeric:
        require_number("SECRET")
    for rendered in (str(numeric.value), repr(numeric.value), *map(str, numeric.value.args)):
        assert "SECRET" not in rendered


# --- RFC section 7 numbers --------------------------------------------------


NUMERIC_ACCEPT = [
    pytest.param("0", 0, id="zero"),
    pytest.param("-1", -1, id="negative-int"),
    pytest.param("1.5", 1.5, id="fraction"),
    pytest.param("1e10", 1e10, id="exponent"),
    pytest.param("1E-3", 0.001, id="upper-exponent"),
    pytest.param("9" * 512, int("9" * 512), id="512-digit-int"),
    pytest.param("-0", 0, id="negative-zero-int"),
]


@pytest.mark.parametrize("text,expected", NUMERIC_ACCEPT)
def test_numeric_accept(text, expected):
    value = parse_number(text)
    assert type(value) is type(expected)
    assert value == expected
    required = require_number(text)
    assert type(required) is type(expected)
    assert required == expected


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("NaN", id="nan"),
        pytest.param("Infinity", id="infinity"),
        pytest.param("-Infinity", id="negative-infinity"),
    ],
)
def test_numeric_non_finite_accept(text):
    value = parse_number(text)
    assert type(value) is float
    required = require_number(text)
    if text == "NaN":
        assert math.isnan(value)
        assert math.isnan(required)
    elif text == "Infinity":
        assert value == float("inf")
        assert required == float("inf")
    else:
        assert value == float("-inf")
        assert required == float("-inf")


NUMERIC_REFUSE = [
    pytest.param("1abc", CsvNotNumeric, "not-numeric", id="trailing-text"),
    pytest.param("1_000", CsvNotNumeric, "not-numeric", id="underscore"),
    pytest.param("+1", CsvNotNumeric, "not-numeric", id="leading-plus"),
    pytest.param(".5", CsvNotNumeric, "not-numeric", id="no-integer-part"),
    pytest.param("1.", CsvNotNumeric, "not-numeric", id="no-fraction-digits"),
    pytest.param(" 1", CsvNotNumeric, "not-numeric", id="leading-space"),
    pytest.param("1 ", CsvNotNumeric, "not-numeric", id="trailing-space"),
    pytest.param("1,5", CsvNotNumeric, "not-numeric", id="decimal-comma"),
    pytest.param("", CsvNotNumeric, "not-numeric", id="empty"),
    pytest.param("0x1", CsvNotNumeric, "not-numeric", id="hex"),
    pytest.param("true", CsvNotNumeric, "not-numeric", id="true"),
    pytest.param("null", CsvNotNumeric, "not-numeric", id="null"),
    pytest.param("inf", CsvNotNumeric, "not-numeric", id="stdlib-inf"),
    pytest.param("nan", CsvNotNumeric, "not-numeric", id="stdlib-nan"),
    pytest.param("001", CsvNotNumeric, "not-numeric", id="leading-zero"),
    pytest.param("1e-400", CsvNumberOverflow, "number-underflow", id="underflow"),
    pytest.param("1e999", CsvNumberOverflow, "number-overflow", id="overflow"),
    pytest.param("9" * 513, CsvNumberOverflow, "integer-too-long", id="513-digit-int"),
]


@pytest.mark.parametrize("text,error,reason", NUMERIC_REFUSE)
def test_numeric_refuse(text, error, reason):
    assert parse_number(text) is None
    with pytest.raises(error) as caught:
        require_number(text)
    assert caught.value.reason == reason
    if text:
        assert text not in str(caught.value)


def test_non_text_values_are_not_numbers():
    for value in (None, True, False, 0, 1.0, b"1", ["1"]):
        assert parse_number(value) is None
        with pytest.raises(CsvNotNumeric):
            require_number(value)


def test_number_method_and_quoted_transport():
    profile = CsvProfile(b'a,b\n"1",2.5\n', rows={1})
    assert profile.cell(1, 1) == "1"
    assert profile.number(1, 1) == 1
    assert type(profile.number(1, 1)) is int
    assert profile.number(1, 2) == 2.5

    with pytest.raises(CsvNotNumeric):
        CsvProfile(b'a\n"x"\n', rows={1}).number(1, 1)


def test_compare_values_reuses_delivered_comparator():
    assert compare_values(1, 1.0)
    assert compare_values(2**53, float(2**53))
    assert not compare_values(2**53, 2**53 + 1)
    assert compare_values(1.0 + 1e-10, 1.0)
    assert not compare_values(float("nan"), 1.0)
    assert compare_values(float("nan"), float("nan"))
    assert compare_values(float("inf"), float("inf"))
    assert not compare_values(float("inf"), float("-inf"))
    assert not compare_values(True, 1)
    assert not compare_values(1, True)
    assert compare_values(True, True)
    assert not compare_values(1, "001")
    assert not compare_values("001", 1)


def test_numeric_cell_overflow_and_underflow_are_typed():
    profile = CsvProfile(b"v\n1e999\n1e-400\n", rows={1, 2})
    with pytest.raises(CsvNumberOverflow) as overflow:
        profile.number(1, 1)
    assert overflow.value.reason == "number-overflow"
    with pytest.raises(CsvNumberOverflow) as underflow:
        profile.number(2, 1)
    assert underflow.value.reason == "number-underflow"
