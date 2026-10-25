"""The adapter contract.

An adapter turns an existing experiment folder, written by some tool or by
hand, into a RunEvidence manifest. It works in two read-only steps:

1. ``discover(root)`` looks at the folder and returns what it found: which
   files matter and any structure it recognized. It must be cheap and must
   not write anything.
2. ``extract(root, discovery)`` turns that discovery into a ``Manifest``
   (hashing files, filling in runs, results and relationships).

Rules every adapter must follow:

* Read-only. Never write into ``root``; the CLI decides where the manifest goes.
* Offline. No network calls.
* Never import, unpickle or execute experiment files (no ``torch.load``,
  ``pickle.load``, ``yaml.load`` without a safe loader, ``exec``). Hash raw
  bytes; parse only plain data formats such as JSON or CSV.
* Paths in the returned manifest are relative to ``root`` with forward slashes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from ..models import Manifest


@dataclass
class Discovery:
    """What ``discover`` found. ``files`` are POSIX paths relative to root."""

    root: Path
    files: list[str] = field(default_factory=list)
    # Free-form notes for ``extract``: detected run folders, tool versions, ...
    details: dict[str, Any] = field(default_factory=dict)


class Adapter(ABC):
    #: Short unique name, used as ``runevidence init --adapter <name>``.
    name: ClassVar[str]
    #: One line shown in ``runevidence adapters``.
    description: ClassVar[str] = ""

    @abstractmethod
    def discover(self, root: Path) -> Discovery:
        """Inspect ``root`` read-only and report what this adapter recognizes."""

    @abstractmethod
    def extract(self, root: Path, discovery: Discovery, *, project_id: str, project_name: str) -> Manifest:
        """Build a manifest from a previous ``discover`` result."""
