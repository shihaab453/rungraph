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

If the folder is not something the adapter can read (for example, none of
the run folders it looks for exist), raise ``AdapterError`` with a message
saying what was expected. ``runevidence init`` prints it and exits 2 without
writing anything.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from ..models import Manifest, Result


class AdapterError(Exception):
    """The folder is not one this adapter can turn into a manifest."""


@dataclass(frozen=True)
class Summary:
    """A metric summary read from bytes the caller already hashed.

    The caller owns the bytes and their digest, so the returned triple is bound
    to exactly the bytes that were read; an adapter never opens a path itself."""

    value: float
    step: int
    timestamp: int


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
    #: True when ``extract`` accepts a keyword-only ``tag_filter``.
    supports_tag_filters: ClassVar[bool] = False

    @abstractmethod
    def discover(self, root: Path) -> Discovery:
        """Inspect ``root`` read-only and report what this adapter recognizes."""

    @abstractmethod
    def extract(self, root: Path, discovery: Discovery, *, project_id: str, project_name: str) -> Manifest:
        """Build a manifest from a previous ``discover`` result."""

    # --- optional verified-summary protocol -------------------------------
    # An adapter whose results summarize a file it owns can verify and refresh
    # those summaries. The defaults mean "not claimed": core then emits an
    # unverified warning for a recognized summary shape, never a silent pass.

    def claims_summary(self, manifest: Manifest, result: Result) -> bool:
        """True when this adapter can re-read ``result`` from its source bytes."""
        return False

    def read_summary(self, data: bytes) -> Optional[Summary]:
        """Parse one summary from ``data``.

        Return None for any malformed, non-finite, blank-row, over-cap or
        non-UTF-8 input. Never return a partial summary."""
        return None
