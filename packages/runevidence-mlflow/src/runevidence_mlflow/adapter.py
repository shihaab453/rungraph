"""Read a local MLflow file store into a RunEvidence manifest.

This adapter reads only the on-disk file-store layout. That layout is either::

    <store>/<experiment>/meta.yaml
    <store>/<experiment>/<run>/meta.yaml
    <store>/<experiment>/<run>/params/...
    <store>/<experiment>/<run>/metrics/...
    <store>/<experiment>/<run>/tags/...
    <store>/<experiment>/<run>/artifacts/...

or a project folder that contains such a store in ``mlruns/``. No tracking
server is contacted, no URI is resolved, and the MLflow library is not
imported. Experiment and run ``meta.yaml`` files are read with
``yaml.safe_load`` only.

What the manifest gets:

* every ordinary file in the folder as an Artifact with sha256 and size,
  including experiment/run metadata, params, tags, artifacts, and files that
  are unrelated to MLflow;
* one Run per run folder that has valid run metadata and sits directly under
  an experiment folder with valid experiment metadata;
* ``params`` copied as the exact strings stored on disk, with nested names
  such as ``optimizer/name`` preserved;
* one Result per readable metric history. MLflow may write metric rows as
  ``timestamp value step`` or, in older stores, ``timestamp value`` (step 0).
  The recorded value is the row with the greatest ``(step, timestamp, value)``,
  not simply the last physical line;
* ``metadata.mlflow`` with a small whitelist only: experiment id, name and
  lifecycle, run id/name/lifecycle/status, and the paths of metric files that
  could not be summarized.

Deliberately left out: user ids, source names/versions, arbitrary tags,
``artifact_uri`` and ``artifact_location``. Those URIs are never followed,
even when they point at a local folder. Symlinks are never followed either.

A metric file that is empty, malformed, non-finite, or uses an unsupported
row shape is not summarized at all. Its raw bytes are still hashed, and its
path is listed in the run's ``metadata.mlflow.unreadable``. Broken, unsafe or
wrong-shape experiment/run metadata raises ``AdapterError`` instead of being
silently ignored.
"""

from __future__ import annotations

import math
import os
import stat
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Optional

import yaml
from runevidence.adapters import Adapter, AdapterError, Discovery
from runevidence.adapters.generic import slug
from runevidence.checks.files import IGNORED_DIRS
from runevidence.hashing import sha256_file
from runevidence.models import BACKUP_FILENAME, MANIFEST_FILENAME, Artifact, Manifest, Project, Result, Run

MLRUNS_DIR = "mlruns"
META_FILE = "meta.yaml"
TRASH_DIR = ".trash"
# Folders MLflow reserves inside an experiment for things that are not runs.
RESERVED_EXPERIMENT_DIRS = {"tags", "datasets", "traces"}
RUN_NAME_TAG = "mlflow.runName"

_NO_RUNS = (
    "No MLflow runs found. Expected a local MLflow file store: a folder containing "
    "mlruns/<experiment>/meta.yaml and mlruns/<experiment>/<run>/meta.yaml, or the "
    "file-store folder itself with <experiment>/meta.yaml and "
    "<experiment>/<run>/meta.yaml. Point init at the project folder or at its "
    "mlruns folder, or use --adapter generic to hash the folder without MLflow "
    "run detection. Tracking servers and remote or external artifact URIs are not "
    "supported."
)

# MLflow persists RunStatus as an integer enum in run meta.yaml. Map the known
# values to their names for readable output; keep an unknown integer as-is.
_STATUS_NAMES = {1: "RUNNING", 2: "SCHEDULED", 3: "FINISHED", 4: "FAILED", 5: "KILLED"}


class MlflowAdapter(Adapter):
    name = "mlflow"
    description = (
        "MLflow file store (mlruns/): one run per run folder, params and latest metric values."
    )

    def discover(self, root: Path) -> Discovery:
        root = Path(root)
        if not root.is_dir():
            raise AdapterError(f"{root} is not a folder")
        store = _store_dir(root)
        base = root / store if store else root
        files = sorted(_walk_files(root))
        experiments = _read_experiments(root, base)
        return Discovery(
            root=root,
            files=files,
            details={"store": store, "experiments": experiments},
        )

    def extract(
        self, root: Path, discovery: Discovery, *, project_id: str, project_name: str
    ) -> Manifest:
        experiments: list[dict[str, Any]] = discovery.details.get("experiments", [])
        if not any(exp["runs"] for exp in experiments):
            raise AdapterError(_NO_RUNS)

        used: set[str] = {project_id}
        run_ids: dict[str, str] = {}
        for exp in experiments:
            for run in exp["runs"]:
                run_ids[run["dir"]] = _unique(f"run-{slug(run['dir'])}", used)

        artifacts: list[Artifact] = []
        artifact_ids: dict[str, str] = {}
        for rel in discovery.files:
            path = root / rel
            try:
                digest = sha256_file(path)
                size = path.stat().st_size
            except OSError as exc:
                raise AdapterError(f"could not read file {rel!r}: {exc}") from None
            run_dir = _owner_run(rel, run_ids)
            art_id = _unique(f"artifact-{slug(rel)}", used)
            artifact_ids[rel] = art_id
            artifacts.append(
                Artifact(
                    id=art_id,
                    path=rel,
                    sha256=digest,
                    size_bytes=size,
                    kind=_kind(rel, run_dir),
                    run_id=run_ids.get(run_dir) if run_dir is not None else None,
                )
            )

        results: list[Result] = []
        unreadable: dict[str, list[str]] = {run_dir: [] for run_dir in run_ids}
        for rel in discovery.files:
            run_dir = _owner_run(rel, run_ids)
            if run_dir is None:
                continue
            name = _metric_name(rel, run_dir)
            if name is None:
                continue
            summary = _read_metric(root / rel)
            if summary is None:
                unreadable[run_dir].append(rel)
                continue
            res_id = _unique(f"metric-{slug(run_dir)}-{slug(name)}", used)
            results.append(
                Result(
                    id=res_id,
                    run_id=run_ids[run_dir],
                    name=name,
                    value=summary.value,
                    artifact_id=artifact_ids[rel],
                    metadata={"step": summary.step, "timestamp_ms": summary.timestamp},
                )
            )

        runs: list[Run] = []
        for exp in experiments:
            for info in exp["runs"]:
                run_dir = info["dir"]
                mlflow: dict[str, Any] = {
                    "experiment_id": exp["experiment_id"],
                    "experiment_name": exp["name"],
                    "run_id": info["run_id"],
                }
                if info["run_name"]:
                    mlflow["run_name"] = info["run_name"]
                if info["lifecycle_stage"]:
                    mlflow["lifecycle_stage"] = info["lifecycle_stage"]
                if info["status"] is not None:
                    mlflow["status"] = info["status"]
                if unreadable[run_dir]:
                    mlflow["unreadable"] = unreadable[run_dir]
                runs.append(
                    Run(
                        id=run_ids[run_dir],
                        name=info["run_name"] or info["run_id"],
                        started_at=info["started_at"],
                        params=_read_params(root, run_dir, discovery.files),
                        metadata={"mlflow": mlflow},
                    )
                )

        project_meta: dict[str, Any] = {"experiments": []}
        for exp in experiments:
            item = {"experiment_id": exp["experiment_id"], "name": exp["name"]}
            if exp["lifecycle_stage"]:
                item["lifecycle_stage"] = exp["lifecycle_stage"]
            project_meta["experiments"].append(item)
        return Manifest(
            project=Project(id=project_id, name=project_name, metadata={"mlflow": project_meta}),
            runs=runs,
            artifacts=artifacts,
            results=results,
        )


class _Summary:
    __slots__ = ("value", "step", "timestamp")

    def __init__(self, value: float, step: int, timestamp: int) -> None:
        self.value = value
        self.step = step
        self.timestamp = timestamp


def _store_dir(root: Path) -> str:
    candidate = root / MLRUNS_DIR
    if candidate.is_dir() and not candidate.is_symlink() and _within(root, os.path.realpath(candidate)):
        return MLRUNS_DIR
    return ""


def _walk_files(root: Path) -> Iterator[str]:
    """Regular files under root, never following a symlink or leaving root."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        kept: list[str] = []
        for name in sorted(dirnames):
            if name in IGNORED_DIRS:
                continue
            full = Path(dirpath) / name
            if full.is_symlink() or not _within(root, os.path.realpath(full)):
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            # Only the generated manifest and its backup at the selected root
            # are omitted. A nested file with the same name is ordinary content.
            if rel in (MANIFEST_FILENAME, BACKUP_FILENAME):
                continue
            if full.is_symlink():
                continue
            try:
                st = full.stat(follow_symlinks=False)
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            if not _within(root, os.path.realpath(full)):
                continue
            yield rel


def _read_experiments(root: Path, base: Path) -> list[dict[str, Any]]:
    experiments: list[dict[str, Any]] = []
    for exp_dir in _safe_subdirs(base, root):
        if exp_dir.name == TRASH_DIR:
            continue
        exp_meta = exp_dir / META_FILE
        if not _safe_file(exp_meta, root):
            continue
        exp_rel = exp_meta.relative_to(root).as_posix()
        doc = _read_yaml_mapping(exp_meta, exp_rel, "experiment")
        exp_id = _id_value(doc, "experiment_id", exp_rel, "experiment")
        exp_name = _required_str(doc, "name", exp_rel, "experiment")
        exp_lifecycle = _optional_str(doc, "lifecycle_stage", exp_rel, "experiment")

        runs: list[dict[str, Any]] = []
        for run_dir in _safe_subdirs(exp_dir, root):
            run_meta = run_dir / META_FILE
            if not _safe_file(run_meta, root):
                continue
            run_rel = run_meta.relative_to(root).as_posix()
            run_doc = _read_yaml_mapping(run_meta, run_rel, "run")
            run_id = _run_id(run_doc, run_rel)
            exp_ref = _id_value(run_doc, "experiment_id", run_rel, "run")
            if exp_ref != exp_id:
                raise AdapterError(
                    f"run metadata {run_rel!r} has experiment_id {exp_ref!r}, but it is inside "
                    f"experiment {exp_id!r}. Fix the metadata or move the run folder."
                )
            runs.append(
                {
                    "dir": run_dir.relative_to(root).as_posix(),
                    "run_id": run_id,
                    "run_name": _run_name(run_doc, run_rel),
                    "lifecycle_stage": _optional_str(run_doc, "lifecycle_stage", run_rel, "run"),
                    "status": _status_value(run_doc, run_rel),
                    "started_at": _start_time(run_doc, run_rel),
                }
            )
        experiments.append(
            {
                "dir": exp_dir.relative_to(root).as_posix(),
                "experiment_id": exp_id,
                "name": exp_name,
                "lifecycle_stage": exp_lifecycle,
                "runs": runs,
            }
        )
    return experiments


def _safe_subdirs(folder: Path, root: Path) -> list[Path]:
    try:
        entries = sorted(folder.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        raise AdapterError(f"could not list {folder}: {exc}") from None
    out: list[Path] = []
    for entry in entries:
        if (
            entry.name in IGNORED_DIRS
            or entry.name == TRASH_DIR
            or entry.name in RESERVED_EXPERIMENT_DIRS
        ):
            continue
        if not entry.is_dir() or entry.is_symlink():
            continue
        if not _within(root, os.path.realpath(entry)):
            continue
        out.append(entry)
    return out


def _safe_file(path: Path, root: Path) -> bool:
    if path.is_symlink():
        return False
    try:
        st = path.stat(follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and _within(root, os.path.realpath(path))


def _read_yaml_mapping(path: Path, rel: str, kind: str) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise AdapterError(f"could not read {kind} metadata {rel!r}: {exc}") from None
    try:
        doc = yaml.safe_load(text)
    except (yaml.YAMLError, RecursionError) as exc:
        raise AdapterError(
            f"{kind} metadata {rel!r} is not valid YAML under safe loading: {exc}. "
            "RunEvidence reads metadata with yaml.safe_load only and never executes tags."
        ) from None
    if not isinstance(doc, dict):
        raise AdapterError(
            f"{kind} metadata {rel!r} must be a YAML mapping, not {_type_name(doc)}."
        )
    return doc


def _id_value(doc: dict[str, Any], key: str, rel: str, kind: str) -> str:
    value = doc.get(key)
    if isinstance(value, bool) or not isinstance(value, (str, int)) or str(value) == "":
        raise AdapterError(
            f"{kind} metadata {rel!r} must contain a non-empty string {key!r}; "
            f"found {_type_name(value)}."
        )
    return str(value)


def _run_id(doc: dict[str, Any], rel: str) -> str:
    """Newer MLflow writes run_id; older stores also wrote run_uuid."""
    for key in ("run_id", "run_uuid"):
        if doc.get(key) is not None:
            return _id_value(doc, key, rel, "run")
    raise AdapterError(
        f"run metadata {rel!r} must contain a non-empty string 'run_id' "
        "(or the older 'run_uuid')."
    )


def _status_value(doc: dict[str, Any], rel: str) -> Optional[Any]:
    value = doc.get("status")
    if value is None:
        return None
    if isinstance(value, bool):
        raise AdapterError(f"run metadata {rel!r} has a boolean 'status'.")
    if isinstance(value, int):
        return _STATUS_NAMES.get(value, value)
    if isinstance(value, str):
        return value or None
    raise AdapterError(
        f"run metadata {rel!r} has a 'status' that is neither a string nor an "
        f"integer enum: {_type_name(value)}."
    )


def _required_str(doc: dict[str, Any], key: str, rel: str, kind: str) -> str:
    value = doc.get(key)
    if not isinstance(value, str) or not value:
        raise AdapterError(
            f"{kind} metadata {rel!r} must contain a non-empty string {key!r}; "
            f"found {_type_name(value)}."
        )
    return value


def _optional_str(doc: dict[str, Any], key: str, rel: str, kind: str) -> Optional[str]:
    value = doc.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise AdapterError(
            f"{kind} metadata {rel!r} has a non-string {key!r}: {_type_name(value)}."
        )
    return value or None


def _run_name(doc: dict[str, Any], rel: str) -> Optional[str]:
    name: Optional[str] = None
    raw = doc.get("run_name")
    if raw is not None:
        if not isinstance(raw, str):
            raise AdapterError(
                f"run metadata {rel!r} has a non-string 'run_name': {_type_name(raw)}."
            )
        name = raw or None
    tags = doc.get("tags")
    if tags is not None:
        if not isinstance(tags, list):
            raise AdapterError(
                f"run metadata {rel!r} has a 'tags' value that is not a list."
            )
        for item in tags:
            if not isinstance(item, dict) or not isinstance(item.get("key"), str):
                raise AdapterError(
                    f"run metadata {rel!r} has a tag entry without a string 'key'."
                )
            if item["key"] != RUN_NAME_TAG:
                continue
            value = item.get("value")
            if not isinstance(value, str):
                raise AdapterError(
                    f"run metadata {rel!r} has a non-string {RUN_NAME_TAG!r} tag value."
                )
            if name is None and value:
                name = value
    return name


def _start_time(doc: dict[str, Any], rel: str) -> Optional[datetime]:
    value = doc.get("start_time")
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise AdapterError(
            f"run metadata {rel!r} has a non-integer 'start_time' (milliseconds): "
            f"{_type_name(value)}."
        )
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OSError, OverflowError, ValueError) as exc:
        raise AdapterError(f"run metadata {rel!r} has an unusable start_time: {exc}") from None


def _read_params(root: Path, run_dir: str, files: list[str]) -> dict[str, str]:
    prefix = f"{run_dir}/params/"
    params: dict[str, str] = {}
    for rel in files:
        if not rel.startswith(prefix):
            continue
        key = rel[len(prefix) :]
        if not key:
            continue
        try:
            params[key] = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise AdapterError(f"could not read param file {rel!r}: {exc}") from None
    return params


def _read_metric(path: Path) -> Optional[_Summary]:
    """The best row in an MLflow metric history, or None if the log is bad."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    best: Optional[tuple[tuple[int, int, float], _Summary]] = None
    for line in text.splitlines():
        if not line.strip():
            # A blank line is not a metric row; refuse the whole history
            # rather than silently dropping a corrupt row.
            return None
        parts = line.split()
        if len(parts) == 3:
            timestamp_text, value_text, step_text = parts
        elif len(parts) == 2:
            # Legacy MLflow histories have no step column.
            timestamp_text, value_text = parts
            step_text = "0"
        else:
            return None
        try:
            timestamp = int(timestamp_text)
            step = int(step_text)
            value = float(value_text)
        except ValueError:
            return None
        if not math.isfinite(value):
            return None
        key = (step, timestamp, value)
        if best is None or key > best[0]:
            best = (key, _Summary(value=value, step=step, timestamp=timestamp))
    if best is None:
        return None
    return best[1]


def _owner_run(rel: str, run_ids: dict[str, str]) -> Optional[str]:
    for run_dir in run_ids:
        if rel == run_dir or rel.startswith(run_dir + "/"):
            return run_dir
    return None


def _metric_name(rel: str, run_dir: str) -> Optional[str]:
    prefix = f"{run_dir}/metrics/"
    if not rel.startswith(prefix):
        return None
    name = rel[len(prefix) :]
    return name or None


def _kind(rel: str, run_dir: Optional[str]) -> Optional[str]:
    name = PurePosixPath(rel).name
    if name == META_FILE:
        return "metadata"
    if run_dir is not None:
        if rel.startswith(f"{run_dir}/metrics/"):
            return "metrics"
        if rel.startswith(f"{run_dir}/params/"):
            return "param"
        if rel.startswith(f"{run_dir}/tags/"):
            return "tag"
    return None


def _within(root: Path, path: Any) -> bool:
    root_text = os.path.normcase(os.path.abspath(root))
    path_text = os.path.normcase(os.path.abspath(str(path)))
    return path_text == root_text or path_text.startswith(root_text.rstrip(os.sep) + os.sep)


def _type_name(value: Any) -> str:
    if value is None:
        return "nothing"
    return {
        bool: "a boolean",
        int: "an integer",
        float: "a number",
        str: "a string",
        list: "a list",
        dict: "a mapping",
    }.get(type(value), type(value).__name__)


def _unique(base: str, used: set[str]) -> str:
    candidate, n = base, 2
    while candidate in used:
        candidate, n = f"{base}-{n}", n + 1
    used.add(candidate)
    return candidate
