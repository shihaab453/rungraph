"""Strict reading profiles and comparison for verified value locators.

Only the standard library is used. The profiles are deliberately narrower than
the historical JSON reader: JSONL is split on LF bytes, whole-document 0.3
string locators get the same strict value rules, and every refusal maps to a
fixed locator code rather than parser or OS text.
"""

from __future__ import annotations

import json
import math
from typing import Any, NoReturn

MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_JSONL_LINE_BYTES = 1024 * 1024
MAX_NESTING = 64
MAX_INTEGER_DIGITS = 512

_BOM = b"\xef\xbb\xbf"


class ProfileError(ValueError):
    """The bytes do not satisfy the strict locator profile."""


class LocatorMismatch(ValueError):
    """A selected line/pointer is absent or not selectable."""


class _Failure:
    """A cached failure marker, never an exception object.

    A caught exception keeps its ``__traceback__``, which keeps every frame on
    the way out alive, including the reader that holds the snapshot bytes.
    Caching the exception therefore keeps a whole artifact in memory until a
    later cycle collection. Markers are immutable and hold only the failure
    kind; a fresh exception is raised at lookup time instead."""

    __slots__ = ("kind",)

    def __init__(self, kind: str) -> None:
        self.kind = kind


def _raise_failure(kind: str) -> NoReturn:
    """Raise a fresh fixed exception for a cached marker (no traceback reuse)."""
    if kind == "mismatch":
        raise LocatorMismatch("not selectable") from None
    raise ProfileError("strict profile refusal") from None


def _strip_bom(data: bytes) -> bytes:
    return data[len(_BOM):] if data.startswith(_BOM) else data


def _crlf_only(data: bytes) -> bool:
    """True when every CR is immediately followed by LF.

    A CR is legal only as the first half of CRLF, so the number of CR bytes
    equals the number of CRLF pairs exactly when no CR stands alone. Two
    C-level counts replace one Python step per CR, which made a CRLF-heavy
    file cost seconds."""
    return data.count(b"\r") == data.count(b"\r\n")


def _check_file_bytes(data: bytes) -> None:
    """Refuse bad whole-file shapes with bytes-level scans only.

    Every check is C-level: membership for NUL and a pair of counts for CRLF.
    Nothing here enumerates bytes in Python, so a 50 MiB file costs a few
    scans rather than tens of millions of interpreted steps."""
    if len(data) > MAX_FILE_BYTES:
        raise ProfileError("file over cap")
    if b"\x00" in data:
        raise ProfileError("NUL byte")
    if not _crlf_only(data):
        raise ProfileError("bare CR")


def _jsonl_lines_within_cap(data: bytes) -> None:
    """Refuse any physical line over the cap without materialising the lines.

    Each newline is located by a C-level ``find``, but the loop advances once
    per line in Python: the scan is linear and allocation-free (no per-line
    object, no ``bytes.split``, which would be about a gigabyte of overhead on
    a 50 MiB file of very short lines), yet a newline-dense file still costs
    roughly one short Python step per line (about 0.4 s per 5 MiB of bare
    newlines, so around 4 s at the 50 MiB cap). The BOM is not part of the
    first line's content, so it is stripped before lengths are measured."""
    stripped = _strip_bom(data)
    start = 0
    position = stripped.find(b"\n")
    while position != -1:
        end = position
        if end > start and stripped[end - 1] == 0x0D:
            end -= 1
        if end - start > MAX_JSONL_LINE_BYTES:
            raise ProfileError("line over cap")
        start = position + 1
        position = stripped.find(b"\n", position + 1)
    end = len(stripped)
    if end > start and stripped[end - 1] == 0x0D:
        end -= 1
    if end - start > MAX_JSONL_LINE_BYTES:
        raise ProfileError("line over cap")


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise ProfileError("invalid UTF-8") from None


def _depth_ok(text: str) -> bool:
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_NESTING:
                return False
        elif char in "]}":
            if depth:
                depth -= 1
    return True


def _significand_nonzero(token: str) -> bool:
    mantissa = token.split("e", 1)[0].split("E", 1)[0]
    return any(char in "123456789" for char in mantissa)


def _parse_int(token: str) -> int:
    digits = token[1:] if token.startswith("-") else token
    if len(digits) > MAX_INTEGER_DIGITS:
        raise ProfileError("integer too long")
    try:
        return int(token)
    except (ValueError, RuntimeError):
        # Python's own very-long-integer guard must never escape as a crash.
        raise ProfileError("integer conversion") from None


def _parse_float(token: str) -> float:
    try:
        value = float(token)
    except (ValueError, OverflowError):
        raise ProfileError("number conversion") from None
    if math.isinf(value):
        raise ProfileError("number overflow")
    if value == 0.0 and _significand_nonzero(token):
        raise ProfileError("number underflow")
    return value


def _parse_constant(token: str) -> float:
    if token == "NaN":
        return float("nan")
    if token == "Infinity":
        return float("inf")
    if token == "-Infinity":
        return float("-inf")
    raise ProfileError("number constant")


def _object_no_duplicates(pairs):
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ProfileError("duplicate key")
        out[key] = value
    return out


def _loads(text: str) -> Any:
    if not _depth_ok(text):
        raise ProfileError("nesting depth")
    try:
        return json.loads(
            text,
            parse_int=_parse_int,
            parse_float=_parse_float,
            parse_constant=_parse_constant,
            object_pairs_hook=_object_no_duplicates,
        )
    except ProfileError:
        raise
    except (ValueError, RecursionError, MemoryError):
        raise ProfileError("JSON parse") from None


def parse_json_document(data: bytes) -> Any:
    """Parse a whole 0.3 string-locator JSON document under the strict profile."""
    _check_file_bytes(data)
    text = _decode(_strip_bom(data))
    if not text.strip():
        raise ProfileError("empty document")
    return _loads(text)


def parse_jsonl_line(data: bytes, line: int) -> Any:
    """Parse one one-based physical JSONL line, using the strict profile."""
    return JsonlDocument(data).line(line)


class JsonlDocument:
    """One validated JSONL file: text decoded once, selected lines extracted.

    The file-wide checks and the line-cap scan allocate no per-line objects.
    UTF-8 is validated once for the whole file and the first BOM is stripped;
    only selected physical lines are sliced out of that text and parsed, then
    cached, so many locators over one artifact do not rescan the file and a
    file with millions of short lines is never held as millions of strings.
    Requested line offsets come from one forward scan (``index_lines``); only
    those offsets are kept, never a full line table."""

    __slots__ = ("_text", "_line_count", "_cache", "_offsets")

    def __init__(self, data: bytes) -> None:
        _check_file_bytes(data)
        _jsonl_lines_within_cap(data)
        text = _decode(data)
        if text.startswith("\ufeff"):
            text = text[1:]
        self._text = text
        self._line_count = text.count("\n") + (0 if text.endswith("\n") else 1)
        self._cache: dict[int, Any] = {}
        self._offsets: dict[int, int] = {}

    def __len__(self) -> int:
        return self._line_count

    def index_lines(self, lines) -> None:
        """Record the start offset of every requested physical line in one scan.

        Knowing where line N starts still costs one C-level ``find`` per
        newline up to it, so doing that per distinct selection is
        O(results x file size). The results layer knows the whole selection
        set up front; one forward scan records only the requested offsets and
        stops after the last one. Out-of-range lines are ignored here and keep
        the ordinary ``LocatorMismatch`` from ``line``."""
        wanted = {
            line
            for line in set(lines)
            if 1 <= line <= self._line_count and line not in self._offsets
        }
        if not wanted:
            return
        if 1 in wanted:
            self._offsets[1] = 0
            wanted.discard(1)
            if not wanted:
                return
        position = self._text.find("\n")
        index = 1
        while position != -1 and wanted:
            index += 1
            start = position + 1
            if index in wanted:
                self._offsets[index] = start
                wanted.discard(index)
            position = self._text.find("\n", start)

    def _selected_text(self, line: int) -> str:
        """The physical line's text.

        A line pre-indexed by ``index_lines`` uses its recorded start; any
        other line keeps the bounded single-selection walk from line 1."""
        start = self._offsets.get(line)
        if start is None:
            start = 0
            position = self._text.find("\n")
            index = 1
            while index < line and position != -1:
                start = position + 1
                position = self._text.find("\n", start)
                index += 1
        else:
            position = self._text.find("\n", start)
        end = position if position != -1 else len(self._text)
        return self._text[start:end]

    def line(self, line: int) -> Any:
        if line < 1 or line > self._line_count:
            raise LocatorMismatch("line out of range") from None
        if line in self._cache:
            cached = self._cache[line]
            if isinstance(cached, _Failure):
                _raise_failure(cached.kind)
            return cached
        selected = self._selected_text(line)
        if selected.endswith("\r"):
            selected = selected[:-1]
        if not selected.strip():
            self._cache[line] = _Failure("mismatch")
            _raise_failure("mismatch")
        try:
            value = _loads(selected)
        except (ProfileError, LocatorMismatch):
            self._cache[line] = _Failure("profile")
            _raise_failure("profile")
        self._cache[line] = value
        return value


def compare_values(recorded: Any, actual: Any) -> bool:
    """0.3 scalar comparator: exact int/int, approximate mixed numbers."""
    if isinstance(recorded, bool) or isinstance(actual, bool):
        return recorded is actual
    if recorded is None or actual is None:
        return recorded is None and actual is None
    if type(recorded) is int and type(actual) is int:
        return recorded == actual
    if isinstance(recorded, (int, float)) and isinstance(actual, (int, float)):
        recorded_nan = isinstance(recorded, float) and math.isnan(recorded)
        actual_nan = isinstance(actual, float) and math.isnan(actual)
        if recorded_nan or actual_nan:
            return recorded_nan and actual_nan
        if isinstance(recorded, float) and math.isinf(recorded):
            return isinstance(actual, float) and recorded == actual
        if isinstance(actual, float) and math.isinf(actual):
            return False
        try:
            return math.isclose(recorded, actual, rel_tol=1e-9, abs_tol=1e-12)
        except OverflowError:
            return recorded == actual
    return recorded == actual


def compare_tensorboard(recorded: Any, actual: Any, *, float32: bool) -> bool | None:
    """TensorBoard scalar comparator (slice D RFC section 5.4).

    ``actual`` is a decoded Event scalar: always a float.  float32-origin
    values (``simple_value`` and ``DT_FLOAT``) use ``rel_tol=1e-6`` and
    float64-origin values (``DT_DOUBLE``) use ``rel_tol=1e-9``; both use
    ``abs_tol=1e-12``.  An integer recorded value uses the same tolerance as
    the actual value's origin and falls back to exact comparison when
    ``math.isclose`` cannot convert it (``OverflowError``).  NaN equals NaN;
    infinities equal only themselves; finite versus NaN/infinity is a
    mismatch.

    Returns ``True`` for a match, ``False`` for a mismatch, and ``None`` when
    the recorded value's type is not representable as a TensorBoard scalar
    (``str``, ``bool``, ``None`` or any other non-number), which the caller
    maps to ``locator-unsupported-value``.  The delivered ``compare_values``
    stays at its 0.3/0.4 ``1e-9`` tolerance.
    """
    if isinstance(recorded, bool) or not isinstance(recorded, (int, float)):
        return None
    recorded_nan = isinstance(recorded, float) and math.isnan(recorded)
    actual_nan = isinstance(actual, float) and math.isnan(actual)
    if recorded_nan or actual_nan:
        return recorded_nan and actual_nan
    recorded_infinite = isinstance(recorded, float) and math.isinf(recorded)
    actual_infinite = isinstance(actual, float) and math.isinf(actual)
    if recorded_infinite or actual_infinite:
        return (
            isinstance(recorded, float)
            and isinstance(actual, float)
            and recorded == actual
        )
    try:
        return math.isclose(
            recorded,
            actual,
            rel_tol=1e-6 if float32 else 1e-9,
            abs_tol=1e-12,
        )
    except OverflowError:
        return recorded == actual


def display_locator(locator: Any, *, limit: int = 200) -> str:
    """A bounded, escaped display form; never used for parsing or identity.

    The ``#`` separator is part of the fixed prefix. Delimiter characters
    inside the user-supplied pointer are escaped so the rendered form cannot
    be confused with that prefix. A ``csv-cell`` locator is rendered as
    ``csv-cell:<row>,<column>``: numbers only, never the recorded header. A
    ``tensorboard`` locator is rendered as
    ``tensorboard:step=<step>,occurrence=<n>``: never the recorded tag."""
    if type(locator) is str:
        prefix, payload = "json#", locator
    elif isinstance(locator, dict):
        if locator.get("kind") == "csv-cell":
            return f"csv-cell:{locator.get('row')},{locator.get('column')}"
        if locator.get("kind") == "tensorboard":
            return (
                f"tensorboard:step={locator.get('step')},"
                f"occurrence={locator.get('occurrence')}"
            )
        if locator.get("kind") == "tensorboard-string":
            return (
                f"tensorboard-string:step={locator.get('step')},"
                f"occurrence={locator.get('occurrence')}"
            )
        if locator.get("kind") == "tensorboard-histogram":
            return (
                f"tensorboard-histogram:step={locator.get('step')},"
                f"occurrence={locator.get('occurrence')}"
            )
        if locator.get("kind") == "tensorboard-image":
            return (
                f"tensorboard-image:step={locator.get('step')},"
                f"occurrence={locator.get('occurrence')}"
            )
        if locator.get("kind") == "tensorboard-audio":
            return (
                f"tensorboard-audio:step={locator.get('step')},"
                f"occurrence={locator.get('occurrence')}"
            )
        if locator.get("kind") == "tensorboard-tensor":
            return (
                f"tensorboard-tensor:step={locator.get('step')},"
                f"occurrence={locator.get('occurrence')}"
            )
        if locator.get("kind") in ("tensorboard-tf2-image", "tensorboard-tf2-audio"):
            return (
                f"{locator.get('kind')}:step={locator.get('step')},"
                f"occurrence={locator.get('occurrence')},element={locator.get('element')}"
            )
        if locator.get("kind") == "tensorboard-tf2-histogram":
            return (
                f"tensorboard-tf2-histogram:step={locator.get('step')},"
                f"occurrence={locator.get('occurrence')}"
            )
        prefix, payload = f"jsonl:{locator.get('line')}#", str(locator.get('pointer', ''))
    elif getattr(locator, "kind", None) == "csv-cell":
        return f"csv-cell:{locator.row},{locator.column}"
    elif getattr(locator, "kind", None) == "tensorboard":
        return f"tensorboard:step={locator.step},occurrence={locator.occurrence}"
    elif getattr(locator, "kind", None) == "tensorboard-string":
        return (
            f"tensorboard-string:step={locator.step},"
            f"occurrence={locator.occurrence}"
        )
    elif getattr(locator, "kind", None) == "tensorboard-histogram":
        return (
            f"tensorboard-histogram:step={locator.step},"
            f"occurrence={locator.occurrence}"
        )
    elif getattr(locator, "kind", None) == "tensorboard-image":
        return (
            f"tensorboard-image:step={locator.step},"
            f"occurrence={locator.occurrence}"
        )
    elif getattr(locator, "kind", None) == "tensorboard-audio":
        return (
            f"tensorboard-audio:step={locator.step},"
            f"occurrence={locator.occurrence}"
        )
    elif getattr(locator, "kind", None) == "tensorboard-tensor":
        return (
            f"tensorboard-tensor:step={locator.step},"
            f"occurrence={locator.occurrence}"
        )
    elif getattr(locator, "kind", None) in (
        "tensorboard-tf2-image",
        "tensorboard-tf2-audio",
    ):
        return (
            f"{locator.kind}:step={locator.step},"
            f"occurrence={locator.occurrence},element={locator.element}"
        )
    elif getattr(locator, "kind", None) == "tensorboard-tf2-histogram":
        return (
            f"tensorboard-tf2-histogram:step={locator.step},"
            f"occurrence={locator.occurrence}"
        )
    else:
        prefix, payload = f"jsonl:{locator.line}#", str(locator.pointer)
    escaped = json.dumps(payload, ensure_ascii=True)[1:-1]
    escaped = (
        escaped.replace("#", "\\u0023")
        .replace(",", "\\u002c")
        .replace("@", "\\u0040")
    )
    text = prefix + escaped
    return text if len(text) <= limit else text[: limit - 3] + "..."
