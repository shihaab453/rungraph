"""Bounded tag patterns shared by the CLI and capable adapters.

The grammar is a small, full-string glob: ``*`` matches zero or more code
points, ``?`` matches exactly one, ``\\`` escapes the next code point, and
every other code point is literal. Matching is case-sensitive and has no path
semantics.

Work is metered in cost units so a hostile pattern cannot monopolise a run.
Every evaluated ``(tag, pattern)`` pair is charged at most once; later calls
read a cache. When the shared budget is exceeded the filter raises
:class:`TagPatternTooExpensive` and refuses further work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

MAX_PATTERN_POINTS = 1024
MAX_TAG_BYTES = 4096
MAX_PATTERNS = 256
MAX_CACHE_ENTRIES = 65_536
MATCH_BUDGET = 50_000_000

__all__ = [
    "MATCH_BUDGET",
    "MAX_PATTERN_POINTS",
    "MAX_TAG_BYTES",
    "InvalidTagPattern",
    "TagFilter",
    "TagPatternTooExpensive",
]


class InvalidTagPattern(ValueError):
    """A pattern is empty, ends in a backslash, or is over the cap."""


class TagPatternTooExpensive(Exception):
    """The shared matching-work budget is exhausted."""


@dataclass(frozen=True)
class _Segment:
    text: str
    wildcards: tuple[bool, ...]

    @property
    def has_wildcard(self) -> bool:
        return any(self.wildcards)


@dataclass(frozen=True)
class _Pattern:
    source: str
    segments: tuple[_Segment, ...]


def _compile(source: str) -> _Pattern:
    if source == "":
        raise InvalidTagPattern("an empty pattern is not valid")

    segments: list[_Segment] = []
    chars: list[str] = []
    wildcards: list[bool] = []
    escaped = False
    last_was_star = False
    consumed = 0

    def flush() -> None:
        segments.append(_Segment("".join(chars), tuple(wildcards)))
        chars.clear()
        wildcards.clear()

    for ch in source:
        if escaped:
            consumed += 1
            if consumed > MAX_PATTERN_POINTS:
                raise InvalidTagPattern("pattern over the code-point cap")
            chars.append(ch)
            wildcards.append(False)
            escaped = False
            last_was_star = False
        elif ch == "\\":
            escaped = True
            last_was_star = False
        elif ch == "*":
            consumed += 1
            if consumed > MAX_PATTERN_POINTS:
                raise InvalidTagPattern("pattern over the code-point cap")
            if not last_was_star:
                flush()
            last_was_star = True
        elif ch == "?":
            consumed += 1
            if consumed > MAX_PATTERN_POINTS:
                raise InvalidTagPattern("pattern over the code-point cap")
            chars.append(ch)
            wildcards.append(True)
            last_was_star = False
        else:
            consumed += 1
            if consumed > MAX_PATTERN_POINTS:
                raise InvalidTagPattern("pattern over the code-point cap")
            chars.append(ch)
            wildcards.append(False)
            last_was_star = False

    if escaped:
        raise InvalidTagPattern("a trailing backslash is not valid")
    flush()
    return _Pattern(source, tuple(segments))


def _matches_at(segment: _Segment, text: str, start: int) -> bool:
    if start < 0 or start + len(segment.text) > len(text):
        return False
    for offset, (ch, wildcard) in enumerate(zip(segment.text, segment.wildcards)):
        if not wildcard and text[start + offset] != ch:
            return False
    return True


def _find(segment: _Segment, text: str, start: int, positions: int) -> int:
    """Return the first matching start in ``[start, start + positions)``."""

    limit = start + positions
    if not segment.has_wildcard:
        found = text.find(segment.text, start, limit - 1 + len(segment.text))
        return found if found < limit else -1
    for candidate in range(start, limit):
        if _matches_at(segment, text, candidate):
            return candidate
    return -1


def _evaluate(pattern: _Pattern, tag: str) -> tuple[int, bool]:
    """Return ``(cost, matched)``; stop charging at the first failing segment."""

    segments = pattern.segments
    count = len(segments)
    if count == 1:
        segment = segments[0]
        if len(tag) != len(segment.text):
            return len(segment.text), False
        return len(segment.text), _matches_at(segment, tag, 0)

    suffix = [0] * (count + 1)
    for index in range(count - 1, -1, -1):
        suffix[index] = suffix[index + 1] + len(segments[index].text)

    first = segments[0]
    cost = len(first.text)
    if not _matches_at(first, tag, 0):
        return cost, False
    position = len(first.text)

    for index in range(1, count - 1):
        segment = segments[index]
        later = suffix[index + 1]
        window = len(tag) - later - position
        positions = max(0, window - len(segment.text) + 1)
        cost += positions * len(segment.text)
        if positions == 0:
            return cost, False
        found = _find(segment, tag, position, positions)
        if found < 0:
            return cost, False
        position = found + len(segment.text)

    last = segments[-1]
    cost += len(last.text)
    start = len(tag) - len(last.text)
    if start < position:
        return cost, False
    return cost, _matches_at(last, tag, start)


class TagFilter:
    """Evaluate patterns against tags with one shared cache and budget."""

    def __init__(self, patterns: Iterable[str], *, budget: int = MATCH_BUDGET) -> None:
        if budget < 0:
            raise ValueError("budget must not be negative")
        self._sources = tuple(patterns)
        if len(self._sources) > MAX_PATTERNS:
            raise InvalidTagPattern("too many patterns")
        self._patterns = tuple(_compile(pattern) for pattern in self._sources)
        self._budget = budget
        self._spent = 0
        self._cache: dict[tuple[str, str], bool] = {}
        self._matched = [False] * len(self._patterns)
        self._exhausted = False

    @property
    def patterns(self) -> tuple[str, ...]:
        return self._sources

    @property
    def spent(self) -> int:
        return self._spent

    @property
    def remaining(self) -> int:
        return self._budget - self._spent

    def matches(self, tag: str) -> bool:
        """Return whether any pattern matches ``tag``, charging every pair.

        Every pattern is evaluated even after one matches: the warning pass
        must be able to report each pattern independently, and the cache is
        keyed by pattern position.
        """

        if self._exhausted:
            raise TagPatternTooExpensive("matching budget exhausted")
        try:
            tag_bytes = len(tag.encode("utf-8"))
        except UnicodeEncodeError:
            return False
        if not 1 <= tag_bytes <= MAX_TAG_BYTES:
            return False

        selected = False
        for index, pattern in enumerate(self._patterns):
            key = (tag, pattern.source)
            cached = self._cache.get(key)
            if cached is None:
                cost, hit = _evaluate(pattern, tag)
                # A pair with no literal work still consumes one unit, so an
                # all-wildcard pattern cannot be evaluated for free.
                self._spent += cost or 1
                if self._spent > self._budget:
                    self._exhausted = True
                    raise TagPatternTooExpensive("matching budget exhausted")
                if len(self._cache) < MAX_CACHE_ENTRIES:
                    self._cache[key] = hit
            else:
                hit = cached
            if hit:
                selected = True
                self._matched[index] = True
        return selected

    def unmatched_patterns(self) -> tuple[str, ...]:
        """Return patterns that matched no tag, in input order."""

        return tuple(
            source
            for index, source in enumerate(self._sources)
            if not self._matched[index]
        )
