"""Adapter registry: built-ins plus anything installed under the
``runevidence.adapters`` entry point group."""

from __future__ import annotations

from importlib.metadata import entry_points

from .base import Adapter, Discovery
from .generic import GenericAdapter

__all__ = ["Adapter", "Discovery", "GenericAdapter", "available_adapters", "get_adapter"]

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
