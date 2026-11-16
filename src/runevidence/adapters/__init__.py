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


def summary_claimants(manifest: Manifest, result: Result) -> tuple[list[Adapter], str]:
    """Adapters eligible by provenance that claim ``result``, plus a reason.

    Only installed adapter names present in the result's run metadata or the
    project metadata are asked, so an unrelated plugin cannot suppress the
    absent-verifier warning or create false ambiguity. A candidate that raises
    from ``claims_summary`` reports ``broken``; it is never silently treated as
    absent. Returns ``([], "absent")`` when no eligible adapter claims it."""
    installed = available_adapters()
    run = next((item for item in manifest.runs if item.id == result.run_id), None)
    names: list[str] = []
    for metadata in (manifest.project.metadata, run.metadata if run is not None else {}):
        if not isinstance(metadata, dict):
            continue
        for key in metadata:
            if isinstance(key, str) and key in installed and key not in names:
                names.append(key)
    if not names:
        return [], "absent"
    found: list[Adapter] = []
    for name in names:
        try:
            adapter = installed[name]()
            claimed = adapter.claims_summary(manifest, result)
        except Exception:
            return [], "broken"
        if claimed:
            found.append(adapter)
    if not found:
        return [], "absent"
    if len(found) > 1:
        return found, "ambiguous"
    return found, "ok"
