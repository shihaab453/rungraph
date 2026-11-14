"""Strict, RFC 4180-derived CSV profile for ``csv-cell`` locators.

The profile is deliberately narrower than RFC 4180 and than the standard
library ``csv`` module (see RFC section 13).  It validates one in-memory byte
snapshot and keeps only the parsed header and, optionally, the byte offset of
each requested data record.  It never builds a cell table and never decodes the
whole file into one ``str``.

Pass 1 (``CsvProfile.__init__``) scans the snapshot once:

* the 50 MiB file cap, NUL refusal, incremental UTF-8 validation (<= 1 MiB
  chunks), bare-CR refusal and one leading BOM strip happen before parsing;
* LF/CRLF records, quoted fields, ``""`` escapes, blank-record, ragged-record,
  header and cap rules are checked for the whole file;
* only the header is decoded and retained, plus the absolute byte offset of
  each requested data record (``rows=``).

Pass 2 (``cell``) decodes and unescapes exactly the selected field.  An
unrequested row is located by a forward scan and is not cached, so retention
stays bounded by the caller's explicit row set.

The module also owns the RFC section 7 numeric grammar.  ``compare_values`` is
re-exported from :mod:`runevidence.locator_profiles` so the integration layer
uses the delivered comparator rather than a second implementation.
"""

from __future__ import annotations

import codecs
import math
import re
from typing import Iterable

from ..locator_profiles import (
    MAX_FILE_BYTES,
    MAX_INTEGER_DIGITS,
    _significand_nonzero,
    compare_values,
)

__all__ = [
    "MAX_COLUMNS",
    "MAX_DATA_ROWS",
    "MAX_FIELD_BYTES",
    "MAX_FILE_BYTES",
    "MAX_RECORD_BYTES",
    "MAX_TOTAL_CELLS",
    "PROFILE_REASONS",
    "NUMBER_REASONS",
    "CsvProfile",
    "CsvTable",
    "CsvError",
    "CsvProfileError",
    "CsvSelectionError",
    "CsvRowOutOfRange",
    "CsvColumnOutOfRange",
    "CsvNumberError",
    "CsvNotNumeric",
    "CsvNumberOverflow",
    "parse_number",
    "require_number",
    "compare_values",
]

# Per-artifact structural caps (RFC section 5.3).  The 50 MiB file cap is
# ``locator_profiles.MAX_FILE_BYTES``; a snapshot above it is refused here as a
# defensive boundary, although the file check supplies ``over_cap`` first.
MAX_RECORD_BYTES = 1024 * 1024
MAX_FIELD_BYTES = 64 * 1024
MAX_COLUMNS = 1024
MAX_DATA_ROWS = 1_000_000
MAX_TOTAL_CELLS = 10_000_000

# UTF-8 is validated through one incremental decoder; no chunk exceeds 1 MiB.
_UTF8_CHUNK_BYTES = 1024 * 1024

_BOM = b"\xef\xbb\xbf"
_COMMA = 0x2C
_QUOTE = 0x22
_LF = 0x0A
_CR = 0x0D

# An unquoted field ends at the first comma, quote, or record terminator.  CR
# is included because a bare CR is refused file-wide and a CRLF terminates.
_UNQUOTED_STOP = re.compile(rb'[",\r\n]')

# RFC section 7 numeric grammar.  ``re.fullmatch`` is the normative match.
_NUMBER_PATTERN = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_NAN = float("nan")
_INFINITY = float("inf")
_NEGATIVE_INFINITY = float("-inf")

PROFILE_REASONS = frozenset(
    {
        "file-over-cap",
        "nul-byte",
        "invalid-utf8",
        "bare-cr",
        "empty-file",
        "blank-record",
        "quote-in-unquoted-field",
        "unclosed-quote",
        "data-after-closing-quote",
        "ragged-record",
        "header-only",
        "header-empty",
        "header-duplicate",
        "columns-over-cap",
        "record-over-cap",
        "field-over-cap",
        "rows-over-cap",
        "cells-over-cap",
    }
)

NUMBER_REASONS = frozenset(
    {
        "not-numeric",
        "integer-too-long",
        "number-overflow",
        "number-underflow",
        "number-conversion",
    }
)


class CsvError(ValueError):
    """Base class for every fixed CSV-profile refusal."""


class CsvProfileError(CsvError):
    """The bytes do not satisfy the strict csv-cell profile.

    ``reason`` is a short, stable token from :data:`PROFILE_REASONS`.  The
    exception message and arguments never contain cell text, header text, raw
    rows, parser detail or OS text, so the integration layer can render the
    fixed structural finding without leaking artifact content.
    """

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        if reason not in PROFILE_REASONS:
            raise ValueError("unknown csv profile reason") from None
        self.reason = reason
        super().__init__(f"csv profile refusal: {reason}")


class CsvSelectionError(CsvError):
    """A selected cell is absent or not selectable (``locator-mismatch``)."""


class CsvRowOutOfRange(CsvSelectionError):
    """The selected data row is beyond the last data record."""

    def __init__(self) -> None:
        super().__init__("csv row was not found")


class CsvColumnOutOfRange(CsvSelectionError):
    """The selected column is beyond the parsed header (not selectable)."""

    def __init__(self) -> None:
        super().__init__("csv column was not selectable")


class CsvNumberError(CsvError):
    """The selected cell text is not a number under the RFC section 7 grammar.

    ``reason`` is one of :data:`NUMBER_REASONS`; the message never includes the
    cell text.
    """

    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        if reason not in NUMBER_REASONS:
            raise ValueError("unknown csv number reason") from None
        self.reason = reason
        super().__init__(f"csv cell number refusal: {reason}")


class CsvNotNumeric(CsvNumberError):
    """The text does not match the ASCII number grammar."""


class CsvNumberOverflow(CsvNumberError):
    """The text matches the grammar but exceeds exact/representable limits."""


def _validate_utf8(data: bytes, start: int) -> None:
    """Validate whole-file UTF-8 incrementally in ``<= 1 MiB`` chunks.

    One leading BOM is caller-stripped through ``start``; an interior BOM is
    ordinary data.  Chunk boundaries may split a multibyte sequence; the
    incremental decoder carries the partial sequence and is finalised once.
    The decoded chunk is discarded immediately, so no full-file ``str`` is
    built.
    """
    end = len(data)
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    position = start
    while True:
        chunk = data[position:position + _UTF8_CHUNK_BYTES]
        position += len(chunk)
        final = position >= end
        try:
            decoder.decode(chunk, final=final)
        except UnicodeDecodeError:
            raise CsvProfileError("invalid-utf8") from None
        if final:
            return


def _scan_record(data: bytes, start: int) -> tuple[int, int, int]:
    """Validate one record and return ``(next_position, field_count, record_end)``.

    ``record_end`` excludes the LF/CRLF terminator.  The scan walks structural
    tokens with C-level ``find``/regex calls, not one Python step per byte.
    Record- and field-cap violations are recorded during the walk and raised
    only after the record boundary is known, so a record over 1 MiB reports
    ``record-over-cap`` before a field in the same record reports
    ``field-over-cap``.
    """
    data_length = len(data)
    position = start
    fields = 0
    widest_field = 0

    while True:
        fields += 1
        if position < data_length and data[position] == _QUOTE:
            content_start = position + 1
            cursor = content_start
            unescaped = 0
            while True:
                closing = data.find(b'"', cursor)
                if closing < 0:
                    raise CsvProfileError("unclosed-quote")
                unescaped += closing - cursor
                if closing + 1 < data_length and data[closing + 1] == _QUOTE:
                    # A doubled quote is one literal quote in the parsed text.
                    unescaped += 1
                    if unescaped > widest_field:
                        widest_field = unescaped
                    cursor = closing + 2
                    continue
                position = closing + 1
                break
            if unescaped > widest_field:
                widest_field = unescaped
            if position >= data_length:
                record_end = data_length
                next_position = data_length
                break
            following = data[position]
            if following == _COMMA:
                position += 1
                continue
            if following == _LF:
                record_end = position
                next_position = position + 1
                break
            if following == _CR:
                if position + 1 >= data_length or data[position + 1] != _LF:
                    raise CsvProfileError("bare-cr")
                record_end = position
                next_position = position + 2
                break
            raise CsvProfileError("data-after-closing-quote")
        else:
            match = _UNQUOTED_STOP.search(data, position)
            if match is None:
                field_end = data_length
                field_length = field_end - position
                if field_length > widest_field:
                    widest_field = field_length
                record_end = data_length
                next_position = data_length
                break
            field_end = match.start()
            field_length = field_end - position
            if field_length > widest_field:
                widest_field = field_length
            following = data[field_end]
            if following == _COMMA:
                position = field_end + 1
                continue
            if following == _QUOTE:
                raise CsvProfileError("quote-in-unquoted-field")
            if following == _LF:
                record_end = field_end
                next_position = field_end + 1
                break
            # A bare CR was refused before parsing; this is a CRLF terminator.
            if field_end + 1 >= data_length or data[field_end + 1] != _LF:
                raise CsvProfileError("bare-cr")
            record_end = field_end
            next_position = field_end + 2
            break

    if record_end == start:
        raise CsvProfileError("blank-record")
    if record_end - start > MAX_RECORD_BYTES:
        raise CsvProfileError("record-over-cap")
    if widest_field > MAX_FIELD_BYTES:
        raise CsvProfileError("field-over-cap")
    return next_position, fields, record_end


def _field_slices(data: bytes, start: int, end: int):
    """Yield ``(raw_start, raw_end, quoted)`` for each field of one record.

    ``end`` excludes the terminator.  The record has already passed pass 1;
    the small state machine here only separates fields so one selected field
    can be decoded without materialising the row or a cell table.
    """
    position = start
    while True:
        if position < end and data[position] == _QUOTE:
            cursor = position + 1
            while True:
                closing = data.find(b'"', cursor, end)
                if closing < 0:
                    raise CsvProfileError("unclosed-quote")
                if closing + 1 < end and data[closing + 1] == _QUOTE:
                    cursor = closing + 2
                    continue
                yield position, closing + 1, True
                position = closing + 1
                break
            if position >= end:
                return
            if data[position] == _COMMA:
                position += 1
                continue
            raise CsvProfileError("data-after-closing-quote")
        closing = data.find(b",", position, end)
        if closing < 0:
            yield position, end, False
            return
        yield position, closing, False
        position = closing + 1


def _decode_bytes(raw: bytes) -> str:
    """The one bounded decode helper: at most one record (1 MiB) of bytes.

    Whole-file UTF-8 validation is incremental in ``_validate_utf8``; every
    field/header decode goes through here.  The length guard is a defensive
    invariant (callers pass slices of an already-capped record), not a profile
    cap, and keeps the helper's promise locally checkable.
    """
    if len(raw) > MAX_RECORD_BYTES:
        raise CsvProfileError("record-over-cap")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise CsvProfileError("invalid-utf8") from None


def _decode_field(raw: bytes, quoted: bool) -> str:
    """Decode and unescape exactly one field."""
    if quoted:
        inner = raw[1:-1]
        text = _decode_bytes(inner)
        # CSV escapes a literal quote as two quotes.  Replacing pairs
        # left-to-right gives the same result as a per-pair unescape and is
        # correct for any even-length run.
        return text.replace('""', '"')
    return _decode_bytes(raw)


def _decode_record(data: bytes, start: int, end: int) -> tuple[str, ...]:
    return tuple(
        _decode_field(data[field_start:field_end], quoted)
        for field_start, field_end, quoted in _field_slices(data, start, end)
    )


def _number(text: object) -> tuple[int | float | None, str | None]:
    """Classify ``text`` under RFC section 7; return ``(value, reason)``."""
    if type(text) is not str:
        return None, "not-numeric"
    if text == "NaN":
        return _NAN, None
    if text == "Infinity":
        return _INFINITY, None
    if text == "-Infinity":
        return _NEGATIVE_INFINITY, None
    if _NUMBER_PATTERN.fullmatch(text) is None:
        return None, "not-numeric"
    if "." not in text and "e" not in text and "E" not in text:
        digits = text[1:] if text.startswith("-") else text
        if len(digits) > MAX_INTEGER_DIGITS:
            return None, "integer-too-long"
        try:
            return int(text), None
        except (ValueError, RuntimeError):
            return None, "number-conversion"
    try:
        value = float(text)
    except (ValueError, OverflowError):
        return None, "number-conversion"
    if math.isinf(value):
        return None, "number-overflow"
    if value == 0.0 and _significand_nonzero(text):
        return None, "number-underflow"
    return value, None


def parse_number(text: object) -> int | float | None:
    """Return the RFC section 7 numeric value of ``text`` or ``None``.

    ``None`` covers non-text values, grammar refusals, the 513-digit integer
    limit, float overflow, underflow (a nonzero significand that becomes
    ``0.0``) and conversion refusals.  It never raises for cell text.
    """
    value, _ = _number(text)
    return value


def require_number(text: object) -> int | float:
    """Return the numeric value of ``text`` or raise a typed refusal.

    ``CsvNotNumeric`` means the text does not match the ASCII grammar;
    ``CsvNumberOverflow`` covers the digit limit and float over/underflow.
    Both carry a stable ``reason`` and never include the cell text.
    """
    value, reason = _number(text)
    if value is None:
        if reason == "not-numeric":
            raise CsvNotNumeric("not-numeric") from None
        raise CsvNumberOverflow(reason or "number-conversion") from None
    return value


class CsvProfile:
    """One validated CSV snapshot with lazy, per-cell pass-2 decoding.

    ``rows`` is the set of one-based data rows the caller will read.  When it
    is supplied, pass 1 retains only the absolute start offset of those rows
    (a dict), so a 1,000,000-row file with ten selected rows costs ten offsets.
    ``None`` retains no row offsets; a ``cell`` call then locates its row with
    a forward scan and retains nothing, which keeps a default construction
    bounded.  Callers that read many rows should pass ``rows``.

    The parsed header is always retained (at most 1 MiB of record bytes) and is
    exposed as an immutable tuple for the phase-3 exact-match check.  Row
    numbers are one-based over data records: row 1 is the first data record;
    the header is never selectable.
    """

    __slots__ = ("_data", "_header", "_header_end", "_row_count", "_offsets")

    def __init__(self, data: bytes, rows: Iterable[int] | None = None) -> None:
        if type(data) is not bytes:
            raise TypeError("CsvProfile requires a bytes snapshot")

        # RFC section 4 order: file cap, NUL, UTF-8, then newline rules.
        if len(data) > MAX_FILE_BYTES:
            raise CsvProfileError("file-over-cap")
        if b"\x00" in data:
            raise CsvProfileError("nul-byte")
        start = len(_BOM) if data.startswith(_BOM) else 0
        _validate_utf8(data, start)
        if data.count(b"\r") != data.count(b"\r\n"):
            raise CsvProfileError("bare-cr")
        if start >= len(data):
            raise CsvProfileError("empty-file")

        if rows is None:
            wanted: set[int] | None = None
            offsets: dict[int, int] | None = None
        else:
            wanted = {
                value for value in rows if type(value) is int and value >= 1
            }
            offsets = {}

        header_next, column_count, header_end = _scan_record(data, start)
        if column_count > MAX_COLUMNS:
            raise CsvProfileError("columns-over-cap")
        header = _decode_record(data, start, header_end)
        if any(cell == "" for cell in header):
            raise CsvProfileError("header-empty")
        if len(set(header)) != column_count:
            raise CsvProfileError("header-duplicate")

        row_count = 0
        position = header_next
        data_length = len(data)
        while position < data_length:
            record_start = position
            next_position, field_count, record_end = _scan_record(data, position)
            if field_count != column_count:
                raise CsvProfileError("ragged-record")
            row_count += 1
            if row_count > MAX_DATA_ROWS:
                raise CsvProfileError("rows-over-cap")
            if row_count * column_count > MAX_TOTAL_CELLS:
                raise CsvProfileError("cells-over-cap")
            if offsets is not None and wanted is not None and row_count in wanted:
                offsets[row_count] = record_start
            position = next_position

        if row_count == 0:
            raise CsvProfileError("header-only")

        self._data = data
        self._header = header
        self._header_end = header_next
        self._row_count = row_count
        self._offsets = offsets

    @property
    def header(self) -> tuple[str, ...]:
        """The exact parsed header cells, one per column."""
        return self._header

    @property
    def column_count(self) -> int:
        return len(self._header)

    @property
    def row_count(self) -> int:
        return self._row_count

    def _record_bounds(self, row: int) -> tuple[int, int]:
        """Return ``(start, end)`` for one already-range-checked data row."""
        start: int | None = None
        if self._offsets is not None:
            start = self._offsets.get(row)
        if start is None:
            # The caller did not request this row (or passed rows=None).
            # Locate it with a forward scan and retain nothing.
            position = self._header_end
            index = 0
            data_length = len(self._data)
            while position < data_length:
                index += 1
                if index == row:
                    start = position
                    break
                next_position, _, _ = _scan_record(self._data, position)
                position = next_position
            if start is None:
                raise CsvRowOutOfRange() from None
        _, _, end = _scan_record(self._data, start)
        return start, end

    def cell(self, row: int, column: int) -> str:
        """Return the exact unescaped text of a one-based data cell.

        Selection order is row then column (RFC section 6.2 step 8): a row
        beyond the data is :class:`CsvRowOutOfRange` even when the column is
        also out of range; otherwise a column beyond the header is
        :class:`CsvColumnOutOfRange`.  Row 0 (the header) is never selectable.
        """
        if type(row) is not int or type(column) is not int:
            raise TypeError("row and column must be integers")
        if row < 1 or row > self._row_count:
            raise CsvRowOutOfRange() from None
        if column < 1 or column > len(self._header):
            raise CsvColumnOutOfRange() from None

        start, end = self._record_bounds(row)
        index = 1
        for field_start, field_end, quoted in _field_slices(self._data, start, end):
            if index == column:
                return _decode_field(self._data[field_start:field_end], quoted)
            index += 1
        # The record was validated against the header, so a missing field is
        # an internal inconsistency; report the fixed selection failure.
        raise CsvColumnOutOfRange() from None

    def number(self, row: int, column: int) -> int | float:
        """Return ``cell(row, column)`` parsed by :func:`require_number`."""
        return require_number(self.cell(row, column))


# The RFC discusses the profile as a parsed table; the alias keeps that
# reading local expressive without a second implementation.
CsvTable = CsvProfile
