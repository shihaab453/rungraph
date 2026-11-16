"""Adapter registry: built-ins plus anything installed under the
``runevidence.adapters`` entry point group."""

from __future__ import annotations

from importlib.metadata import entry_points

from ..models import Manifest, Result
from .base import Adapter, AdapterError, Discovery, Summary
from .generic import GenericAdapter

__all__ = [
    "Adapter",
    "AdapterError",
    "Discovery",
    "GenericAdapter",
    "Summary",
    "available_adapters",
    "get_adapter",
    "summary_claimants",
]

ENTRY_POINT_GROUP = "runevidence.adapters"
BUILTIN: dict[str, type[Adapter]] = {GenericAdapter.name: GenericAdapter}


def available_adapters() -> dict[str, type[Adapter]]:
    found = dict(BUILTIN)
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        if ep.name in found:
            continue  # built-ins win; a plugin cannot shadow them
        try:
            cls = ep.load()
        except Exception:  # a broken plugin must not break the CLI
            continue
        if isinstance(cls, type) and issubclass(cls, Adapter):
            found[ep.name] = cls
    return found


def get_adapter(name: str) -> Adapter:
    adapters = available_adapters()
    if name not in adapters:
        raise KeyError(f"Unknown adapter {name!r}. Available: {', '.join(sorted(adapters))}")
    return adapters[name]()


def summary_claimants(manifest: Manifest, result: Result) -> list[Adapter]:
    """Every installed adapter that claims ``result`` as a re-readable summary.

    A plugin that fails to construct or raises from ``claims_summary`` is
    skipped, exactly as ``available_adapters`` skips a broken entry point. An
    empty or plural result is not silence: callers emit an unverified warning;
    ownership is never guessed."""
    found: list[Adapter] = []
    for cls in available_adapters().values():
        try:
            adapter = cls()
            if adapter.claims_summary(manifest, result):
                found.append(adapter)
        except Exception:
            continue
    return found
