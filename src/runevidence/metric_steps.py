"""Recognize the delivered ``<metric> @ <step>`` result-name convention.

Adapters for TensorBoard, Lightning, Keras and Sacred append a step or epoch to
the recorded result name. Comparison and ranking normally use the exact name;
the public ``--latest`` option can instead select the highest step for each
base metric without changing the default output.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

STEP_NAME = re.compile(r"^(?P<base>.+) @ (?P<step>-?\d+)(?: #\d+)?$")


def split_step_name(name: str) -> tuple[str, Optional[int]]:
    match = STEP_NAME.fullmatch(name)
    if match is None:
        return name, None
    return match.group("base"), int(match.group("step"))


def latest_names(names: Iterable[str]) -> set[str]:
    """Names to keep: one highest-step name per base, or all exact names."""
    grouped: dict[str, list[tuple[Optional[int], str]]] = {}
    for name in names:
        base, step = split_step_name(name)
        grouped.setdefault(base, []).append((step, name))
    kept: set[str] = set()
    for items in grouped.values():
        steps = [step for step, _ in items if step is not None]
        if steps:
            highest = max(steps)
            kept.update(name for step, name in items if step == highest)
        else:
            kept.update(name for _, name in items)
    return kept
