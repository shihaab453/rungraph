"""Local Weights & Biases summary-folder adapter.

These folders are written by W&B in offline or synced mode. The adapter reads
only local files: ``wandb-summary.json``, ``config.yaml``, ``wandb-metadata.json``
and the other files in the run folder. It never contacts W&B or resolves a URI.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml

import runevidence.checks.files as check_files
from runevidence.adapters import Adapter, AdapterError, Discovery, GenericAdapter
from runevidence.adapters.generic import valid_slug
from runevidence.checks.files import (
    _digest_protected,
    _parts_for,
    _read_snapshot_parts,
    assert_tree_readable,
)
from runevidence.locator_profiles import MAX_FILE_BYTES, ProfileError, parse_json_document
from runevidence.models import Artifact, Manifest, Project, Result, Run

SUMMARY_FILE = "wandb-summary.json"
CONFIG_FILE = "config.yaml"
METADATA_FILE = "wandb-metadata.json"
MAX_RESULTS = 10_000


class WandbAdapter(Adapter):
    name = "wandb"
    description = (
        "Weights & Biases local run folders: one run per folder with wandb-summary.json, "
        "config as params and numeric summary values as results."
    )

    def discover(self, root: Path) -> Discovery:
        root = Path(root)
        try:
            assert_tree_readable(root)
        except OSError:
            raise AdapterError(
                "a folder under the selected root cannot be listed; nothing was written"
            ) from None
        discovery = GenericAdapter().discover(root)
        files = set(discovery.files)
        runs: set[str] = set()
        for rel in files:
            suffix = "/" + SUMMARY_FILE
            if rel == SUMMARY_FILE:
                runs.add("")
            elif rel.endswith(suffix):
                prefix = rel[: -len(suffix)]
                if prefix.endswith("/files"):
                    runs.add(prefix[: -len("/files")])
                else:
                    runs.add(prefix)
        if not runs:
            raise AdapterError(
                "no W&B run folders found (a run contains wandb-summary.json or "
                "files/wandb-summary.json). Point init at a wandb/ folder, or use "
                "--adapter generic."
            )
        return Discovery(root=root, files=discovery.files, details={"runs": sorted(runs)})

    def extract(
        self, root: Path, discovery: Discovery, *, project_id: str, project_name: str
    ) -> Manifest:
        root = Path(root)
        try:
            assert_tree_readable(root)
        except OSError:
            raise AdapterError(
                "a folder under the selected root cannot be listed; nothing was written"
            ) from None
        if not check_files._DIR_FD:
            raise AdapterError(
                "the W&B adapter needs POSIX directory-descriptor support for protected "
                "reads; use --adapter generic on this host. Nothing was written."
            )
        run_dirs = sorted(set(discovery.details.get("runs", [])))
        if not run_dirs:
            raise AdapterError(
                "no W&B run folders found. Point init at a wandb/ folder, or use "
                "--adapter generic."
            )
        by_depth = sorted(run_dirs, key=lambda item: len(item.split("/")) if item else 0, reverse=True)
        used: set[str] = {project_id}
        run_ids: dict[str, str] = {}
        runs: list[Run] = []
        results: list[Result] = []
        artifacts: list[Artifact] = []
        artifact_ids: dict[str, str] = {}

        for run_dir in run_dirs:
            label = run_dir or root.name
            summary_rel = _summary_rel(root, discovery.files, run_dir)
            if summary_rel is None:
                continue
            summary = _load_json(root, summary_rel, "summary")
            metadata = _load_json(
                root, _join(run_dir, "files", METADATA_FILE), "metadata", optional=True
            ) or {}
            config = _load_config(root, run_dir)
            run_name = _string(metadata.get("display_name")) or _string(metadata.get("name")) or label
            run_id_value = _string(metadata.get("run_id")) or _string(metadata.get("id"))
            base = f"run-{valid_slug(run_id_value or label)}"
            run_id = _unique(base, used)
            run_ids[run_dir] = run_id
            info: dict[str, Any] = {"run_dir": run_dir or "."}
            info["run_id"] = run_id_value or None
            if summary_rel:
                info["summary"] = summary_rel
            unreadable = []
            reasons = {}
            if summary is None:
                unreadable.append(summary_rel)
                reasons[summary_rel] = "is not valid under the strict JSON profile"
            if config is None:
                config = {}
            runs.append(
                Run(
                    id=run_id,
                    name=run_name,
                    started_at=_started_at(metadata),
                    params=config,
                    metadata={"wandb": info},
                )
            )
            summary_id = None
            for rel in discovery.files:
                if _owner(rel, by_depth) != run_dir:
                    continue
                parts = _parts_for(root, rel)
                hashed = _digest_protected(root, parts) if parts is not None else None
                if hashed is None:
                    raise AdapterError(
                        f"artifact {rel!r} cannot be read inside the selected root; nothing was written"
                    )
                digest, size = hashed
                artifact_id = _unique(valid_slug(rel), used)
                artifacts.append(
                    Artifact(
                        id=artifact_id,
                        path=rel,
                        sha256=digest,
                        size_bytes=size,
                        run_id=run_id,
                    )
                )
                artifact_ids[rel] = artifact_id
                if rel == summary_rel:
                    summary_id = artifact_id
            if summary and summary_id:
                for key in sorted(summary):
                    if key.startswith("_") or not isinstance(key, str):
                        continue
                    value = summary[key]
                    if not _numeric(value):
                        continue
                    pointer = "/" + key.replace("~", "~0").replace("/", "~1")
                    payload = (run_id + "\0" + key).encode("utf-8")
                    result_id = _unique(
                        f"wandb-res-{hashlib.sha256(payload).hexdigest()[:16]}", used
                    )
                    results.append(
                        Result(
                            id=result_id,
                            run_id=run_id,
                            name=key,
                            value=value,
                            artifact_id=summary_id,
                            locator=pointer,
                        )
                    )
                    if len(results) > MAX_RESULTS:
                        raise AdapterError(
                            f"more than {MAX_RESULTS} numeric summary values; split the run "
                            "or prune the summary. Nothing was written."
                        )
            if unreadable:
                info["unreadable"] = unreadable
                info["unreadable_reasons"] = reasons

        return Manifest(
            schema_version="0.3",
            project=Project(id=project_id, name=project_name),
            runs=runs,
            artifacts=artifacts,
            results=results,
        )


def _join(prefix: str, *parts: str) -> str:
    return "/".join(part for part in (prefix, *parts) if part)


def _parent(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _summary_rel(root: Path, files: list[str], run_dir: str) -> Optional[str]:
    candidates = [_join(run_dir, SUMMARY_FILE), _join(run_dir, "files", SUMMARY_FILE)]
    present = set(files)
    for rel in candidates:
        if rel in present:
            return rel
    return None


def _owner(path: str, run_dirs: list[str]) -> Optional[str]:
    for run_dir in run_dirs:
        if run_dir == "" or path == run_dir or path.startswith(run_dir + "/"):
            return run_dir
    return None


def _read(root: Path, rel: str):
    parts = _parts_for(root, rel)
    read = _read_snapshot_parts(root, parts, cap=MAX_FILE_BYTES)
    if read.state == "over-cap":
        return None, "larger than the 50 MiB cap"
    if read.state != "ok" or read.snapshot is None or read.snapshot.data is None:
        return None, f"cannot be read safely ({read.state})"
    return bytes(read.snapshot.data), None


def _load_json(root: Path, rel: str, kind: str, *, optional: bool = False) -> Optional[dict]:
    data, reason = _read(root, rel)
    if data is None:
        if optional:
            return None
        return None
    try:
        parsed = parse_json_document(data)
    except ProfileError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _load_config(root: Path, run_dir: str) -> dict:
    for rel in (_join(run_dir, "files", CONFIG_FILE), _join(run_dir, CONFIG_FILE)):
        data, _ = _read(root, rel)
        if data is None:
            continue
        try:
            parsed = yaml.safe_load(data.decode("utf-8"))
        except (UnicodeDecodeError, yaml.YAMLError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _numeric(value: Any) -> bool:
    return (
        type(value) is int
        or (type(value) is float and math.isfinite(value))
    )


def _string(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def _started_at(metadata: dict) -> Optional[datetime]:
    value = metadata.get("started_at") or metadata.get("start_time")
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _unique(base: str, used: set[str]) -> str:
    candidate, index = base, 2
    while candidate in used:
        candidate = f"{base}-{index}"
        index += 1
    used.add(candidate)
    return candidate
