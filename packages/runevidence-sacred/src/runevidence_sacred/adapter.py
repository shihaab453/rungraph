"""Read Sacred FileStorageObserver folders into a RunEvidence manifest.

Sacred's file observer writes one folder per run under a base directory::

    <basedir>/<run id>/run.json       experiment, status, command, times
    <basedir>/<run id>/config.json    the flattened run configuration
    <basedir>/<run id>/metrics.json   {metric: {values, steps, timestamps}}
    <basedir>/<run id>/cout.txt       captured output
    <basedir>/<run id>/<artifact>     files saved by the run

The adapter records every file as a hashed artifact, one run per folder that
contains ``run.json``, the configuration as run params, and every scalar metric
value as a schema 0.3 result with a JSON Pointer locator into ``metrics.json``.
Sacred itself is not needed; only the JSON files are read, and they are bounded.

The result name is ``<metric> @ <step>``; when Sacred recorded no step for that
value, the zero-based array index is used instead. Non-scalar and non-finite
values are skipped. A malformed ``metrics.json`` still leaves its hashed
artifact and is reported in ``metadata["sacred"]["unreadable"]``.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from runevidence.adapters import Adapter, AdapterError, Discovery, GenericAdapter
from runevidence.adapters.generic import valid_slug
from runevidence.checks.files import assert_tree_readable
from runevidence.hashing import sha256_file
from runevidence.locator_profiles import ProfileError, parse_json_document
from runevidence.models import Artifact, Manifest, Project, Result, Run

RUN_FILE = "run.json"
CONFIG_FILE = "config.json"
METRICS_FILE = "metrics.json"
MAX_METRICS_BYTES = 50 * 1024 * 1024
MAX_RESULTS = 10_000


class SacredAdapter(Adapter):
    name = "sacred"
    description = (
        "Sacred FileStorageObserver folders: one run per folder containing run.json, "
        "with config params and scalar metric values."
    )

    def discover(self, root: Path) -> Discovery:
        try:
            assert_tree_readable(root)
        except OSError:
            raise AdapterError(
                "a folder under the selected root cannot be listed; nothing was written"
            ) from None
        discovery = GenericAdapter().discover(root)
        runs = sorted(
            {
                _parent(rel)
                for rel in discovery.files
                if _basename(rel) == RUN_FILE
            }
        )
        return Discovery(root=root, files=discovery.files, details={"runs": runs})

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
        run_dirs: list[str] = [
            item
            for item in sorted(set(discovery.details.get("runs", [])))
            if not _has_symlink_component(root, item)
        ]
        if not run_dirs:
            raise AdapterError(
                "no Sacred run folders found (a run folder contains run.json). "
                "Point init at the FileStorageObserver base directory, or use --adapter generic."
            )
        used: set[str] = {project_id}
        run_ids: dict[str, str] = {}
        names_by_run: dict[str, set[str]] = {}
        runs: list[Run] = []
        results: list[Result] = []
        for run_dir in run_dirs:
            label = run_dir or root.name
            run_id = _unique(f"run-{valid_slug(label)}", used)
            run_ids[run_dir] = run_id
            run_json, config, metrics, info = _read_run(root, run_dir)
            experiment = _mapping(run_json.get("experiment")) if run_json else {}
            name = experiment.get("name")
            runs.append(
                Run(
                    id=run_id,
                    name=name if isinstance(name, str) and name else label,
                    started_at=_started_at(run_json),
                    params=config,
                    metadata={"sacred": info},
                )
            )
            metrics_path = _join(run_dir, METRICS_FILE)
            for metric_name in sorted(metrics):
                try:
                    metric_name.encode("utf-8")
                except UnicodeEncodeError:
                    continue
                entry = metrics[metric_name]
                if not isinstance(entry, dict):
                    continue
                values = entry.get("values")
                if not isinstance(values, list):
                    continue
                steps = entry.get("steps")
                steps = steps if isinstance(steps, list) else []
                for index, value in enumerate(values):
                    if not _scalar(value):
                        continue
                    step = steps[index] if index < len(steps) else index
                    if isinstance(step, bool) or not isinstance(step, (int, float, str)):
                        step = index
                    elif isinstance(step, float) and not math.isfinite(step):
                        step = index
                    pointer = (
                        "/"
                        + _escape(metric_name)
                        + "/values/"
                        + str(index)
                    )
                    result_id = _result_id(run_id, pointer)
                    _reserve(result_id, used)
                    base_name = f"{metric_name} @ {step}"
                    name = base_name
                    suffix = 2
                    while name in names_by_run.setdefault(run_id, set()):
                        name = f"{base_name} #{suffix}"
                        suffix += 1
                    names_by_run[run_id].add(name)
                    results.append(
                        Result(
                            id=result_id,
                            run_id=run_id,
                            name=name,
                            value=value,
                            artifact_id=None,
                            locator=pointer,
                        )
                    )
                    if len(results) > MAX_RESULTS:
                        raise AdapterError(
                            f"more than {MAX_RESULTS} scalar metric values; "
                            "split the study or prune the metrics first. Nothing was written."
                        )
            info["result_count"] = sum(1 for item in results if item.run_id == run_id)

        by_depth = sorted(run_dirs, key=lambda d: len(d.split("/")) if d else 0, reverse=True)
        artifacts: list[Artifact] = []
        for rel in discovery.files:
            owner = next((d for d in by_depth if _is_under(rel, d)), None)
            path = root / rel
            target = _safe_target(root, path)
            if target is None:
                raise AdapterError(
                    f"artifact {rel!r} resolves outside the selected root; nothing was written"
                )
            try:
                digest = sha256_file(target)
                size = target.stat().st_size
            except OSError:
                raise AdapterError(
                    f"artifact {rel!r} cannot be read; nothing was written"
                ) from None
            artifacts.append(
                Artifact(
                    id=_unique(valid_slug(rel), used),
                    path=rel,
                    sha256=digest,
                    size_bytes=size,
                    run_id=run_ids[owner] if owner is not None else None,
                )
            )
        # Metrics results need the metrics artifact id; locate it by path.
        metrics_ids = {
            artifact.path: artifact.id
            for artifact in artifacts
            if _basename(artifact.path) == METRICS_FILE
        }
        for result in results:
            run_dir = next(
                (d for d, ident in run_ids.items() if ident == result.run_id), ""
            )
            result.artifact_id = metrics_ids.get(_join(run_dir, METRICS_FILE))
        return Manifest(
            schema_version="0.3",
            project=Project(id=project_id, name=project_name),
            runs=runs,
            artifacts=artifacts,
            results=results,
        )


def _read_run(root: Path, run_dir: str) -> tuple[dict, dict, dict, dict]:
    info: dict[str, Any] = {"run_dir": run_dir or "."}
    unreadable: list[str] = []
    reasons: dict[str, str] = {}

    def load(
        name: str, cap: Optional[int] = None, *, allow_nonfinite: bool = False
    ) -> Any:
        rel = _join(run_dir, name)
        path = root / rel
        if not path.is_file():
            return None
        target = _safe_target(root, path)
        if target is None:
            unreadable.append(rel)
            reasons[rel] = "resolves outside the selected root"
            return None
        try:
            size = target.stat().st_size
            if cap is not None and size > cap:
                unreadable.append(rel)
                reasons[rel] = f"larger than {cap} bytes"
                return None
            data = target.read_bytes()
        except (OSError, UnicodeDecodeError) as exc:
            unreadable.append(rel)
            reasons[rel] = f"cannot be read as UTF-8 text ({type(exc).__name__})"
            return None
        try:
            parsed = parse_json_document(data)
        except ProfileError as exc:
            unreadable.append(rel)
            reasons[rel] = f"is not valid under the strict JSON profile ({exc})"
            return None
        if not isinstance(parsed, dict):
            unreadable.append(rel)
            reasons[rel] = "is not a JSON object"
            return None
        if not allow_nonfinite and not _finite_json(parsed):
            unreadable.append(rel)
            reasons[rel] = "contains a non-finite number"
            return None
        return parsed

    run_json = load(RUN_FILE) or {}
    config = load(CONFIG_FILE) or {}
    metrics = load(METRICS_FILE, MAX_METRICS_BYTES, allow_nonfinite=True) or {}
    experiment = _mapping(run_json.get("experiment"))
    for key in ("name", "id", "status", "command", "start_time", "stop_time"):
        value = experiment.get(key, run_json.get(key))
        if value not in (None, "", []):
            info[key] = value
    info["artifacts"] = run_json.get("artifacts") if isinstance(run_json.get("artifacts"), list) else []
    if unreadable:
        info["unreadable"] = unreadable
        info["unreadable_reasons"] = reasons
    return run_json, config, metrics, info

def _has_symlink_component(root: Path, rel: str) -> bool:
    current = root
    for part in rel.split("/") if rel else ():
        current = current / part
        if current.is_symlink():
            return True
    return False


def _safe_target(root: Path, path: Path) -> Optional[Path]:
    try:
        base = root.resolve()
        target = path.resolve()
    except OSError:
        return None
    if target != base and base not in target.parents:
        return None
    return target


def _finite_json(value: Any) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_finite_json(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _finite_json(item) for key, item in value.items())
    return True


def _started_at(run_json: dict) -> Optional[datetime]:
    value = run_json.get("start_time")
    if not isinstance(value, str):
        experiment = _mapping(run_json.get("experiment"))
        value = experiment.get("start_time")
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _scalar(value: Any) -> bool:
    if isinstance(value, bool) or isinstance(value, str):
        return True
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _result_id(run_id: str, pointer: str) -> str:
    payload = run_id.encode("utf-8") + b"\x00" + pointer.encode("utf-8")
    return f"sacred-res-{hashlib.sha256(payload).hexdigest()[:16]}"


def _unique(base: str, used: set[str]) -> str:
    candidate, index = base, 2
    while candidate in used:
        candidate = f"{base}-{index}"
        index += 1
    used.add(candidate)
    return candidate


def _reserve(value: str, used: set[str]) -> None:
    if value in used:
        raise AdapterError("a generated id collides with an existing id; nothing was written")
    used.add(value)


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _parent(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _join(prefix: str, name: str) -> str:
    return f"{prefix}/{name}" if prefix else name


def _is_under(path: str, folder: str) -> bool:
    if not folder:
        return True
    return path.startswith(folder + "/")
