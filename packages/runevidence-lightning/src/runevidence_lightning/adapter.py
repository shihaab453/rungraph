"""Read PyTorch Lightning CSVLogger runs into a RunEvidence manifest.

A CSVLogger base directory contains ``version_N/metrics.csv`` files. The
adapter records one run per version folder, hashes every file, and turns every
numeric CSV cell into a schema 0.4 result with a ``csv-cell`` locator. Sparse
and empty cells are skipped. The CSV is parsed with the same strict profile
verification uses, and every read uses the protected descriptor walk.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from runevidence.adapters import Adapter, AdapterError, Discovery, GenericAdapter
from runevidence.adapters.generic import valid_slug
from runevidence.checks import files as check_files
from runevidence.checks.csv import CsvProfile, CsvProfileError, parse_number
from runevidence.checks.files import _parts_for, _read_snapshot_parts
from runevidence.models import Artifact, Manifest04, Project, Result04, Run

METRICS_NAME = "metrics.csv"
MAX_CSV_BYTES = 50 * 1024 * 1024
MAX_RESULTS = 10_000


class LightningAdapter(Adapter):
    name = "lightning"
    description = (
        "PyTorch Lightning CSVLogger folders: one run per version_* folder, "
        "numeric metric cells as csv-cell results."
    )

    def discover(self, root: Path) -> Discovery:
        discovery = GenericAdapter().discover(root)
        runs = sorted(
            {
                _parent(rel)
                for rel in discovery.files
                if _basename(rel) == METRICS_NAME
            }
        )
        return Discovery(root=root, files=discovery.files, details={"runs": runs})

    def extract(
        self, root: Path, discovery: Discovery, *, project_id: str, project_name: str
    ) -> Manifest04:
        root = Path(root)
        if not check_files._DIR_FD:
            raise AdapterError(
                "the Lightning adapter needs POSIX directory-descriptor support for protected "
                "reads; use --adapter generic on this host. Nothing was written."
            )
        run_dirs = sorted(set(discovery.details.get("runs", [])))
        if not run_dirs:
            raise AdapterError(
                "no Lightning metrics.csv found (expected version_*/metrics.csv). "
                "Point init at the CSVLogger base directory, or use --adapter generic."
            )
        used: set[str] = {project_id}
        run_ids: dict[str, str] = {}
        names_by_run: dict[str, set[str]] = {}
        runs: list[Run] = []
        results: list[Result04] = []
        metrics_ids: dict[str, str] = {}
        metrics_snapshots: dict[str, object] = {}
        for run_dir in run_dirs:
            label = run_dir or root.name
            run_id = _unique(f"run-{valid_slug(label)}", used)
            run_ids[run_dir] = run_id
            rel = _join(run_dir, METRICS_NAME)
            read = _read_snapshot_parts(root, _parts_for(root, rel), cap=MAX_CSV_BYTES)
            if read.state != "ok" or read.snapshot is None or read.snapshot.data is None:
                raise AdapterError(
                    f"metrics file {rel!r} cannot be read ({read.state}); nothing was written"
                )
            metrics_snapshots[rel] = read.snapshot
            try:
                first = CsvProfile(read.snapshot.data)
            except CsvProfileError as exc:
                raise AdapterError(f"metrics CSV is not valid ({exc})") from None
            header = first.header
            row_count = first.row_count
            if row_count > MAX_RESULTS:
                raise AdapterError(
                    f"more than {MAX_RESULTS} data rows in {rel!r}; nothing was written"
                )
            step_index = _named_column(header, "step")
            epoch_index = _named_column(header, "epoch")
            try:
                profile = CsvProfile(
                    read.snapshot.data, rows=range(1, row_count + 1)
                )
            except CsvProfileError as exc:
                raise AdapterError(f"metrics CSV is not valid ({exc})") from None
            names = names_by_run.setdefault(run_id, set())
            for row in range(1, row_count + 1):
                step = _row_label(profile, row, step_index, epoch_index)
                for column, name in enumerate(header, start=1):
                    if column in (step_index, epoch_index) or not name:
                        continue
                    value = parse_number(profile.cell(row, column))
                    if not isinstance(value, int | float) or isinstance(value, bool):
                        continue
                    base = f"{name} @ {step}"
                    result_name = base
                    suffix = 2
                    while result_name in names:
                        result_name = f"{base} #{suffix}"
                        suffix += 1
                    names.add(result_name)
                    digest = hashlib.sha256(
                        f"{run_id}\x00{rel}\x00{row}\x00{column}".encode("utf-8")
                    ).hexdigest()[:16]
                    result_id = f"lightning-res-{digest}"
                    if result_id in used:
                        raise AdapterError(
                            "a generated id collides with an existing id; nothing was written"
                        )
                    used.add(result_id)
                    results.append(
                        Result04(
                            id=result_id,
                            run_id=run_id,
                            name=result_name,
                            value=value,
                            artifact_id=None,
                            locator={
                                "kind": "csv-cell",
                                "row": row,
                                "column": column,
                                "header": name,
                            },
                        )
                    )
                    if len(results) > MAX_RESULTS:
                        raise AdapterError(
                            f"more than {MAX_RESULTS} numeric cells; nothing was written"
                        )
            runs.append(Run(id=run_id, name=label))
            metrics_ids[rel] = None  # filled after artifacts
        by_depth = sorted(run_dirs, key=lambda d: len(d.split("/")) if d else 0, reverse=True)
        artifacts: list[Artifact] = []
        for rel in discovery.files:
            snapshot = metrics_snapshots.get(rel)
            if snapshot is None:
                read = _read_snapshot_parts(root, _parts_for(root, rel))
                if read.state not in ("ok", "over-cap") or read.snapshot is None:
                    raise AdapterError(
                        f"artifact {rel!r} cannot be read ({read.state}); nothing was written"
                    )
                snapshot = read.snapshot
            owner = next((d for d in by_depth if _is_under(rel, d)), None)
            artifact_id = _unique(valid_slug(rel), used)
            if rel in metrics_ids:
                metrics_ids[rel] = artifact_id
            artifacts.append(
                Artifact(
                    id=artifact_id,
                    path=rel,
                    sha256=snapshot.digest,
                    size_bytes=snapshot.size,
                    run_id=run_ids[owner] if owner is not None else None,
                )
            )
        for result in results:
            run_dir = next((d for d, ident in run_ids.items() if ident == result.run_id), "")
            result.artifact_id = metrics_ids.get(_join(run_dir, METRICS_NAME))
        return Manifest04(
            schema_version="0.4",
            project=Project(id=project_id, name=project_name),
            runs=runs,
            artifacts=artifacts,
            results=results,
        )


def _named_column(header: tuple[str, ...], name: str) -> int | None:
    for index, value in enumerate(header, start=1):
        if value.lower() == name:
            return index
    return None


def _row_label(profile: CsvProfile, row: int, step: int | None, epoch: int | None) -> object:
    for index in (step, epoch):
        if index is None:
            continue
        value = parse_number(profile.cell(row, index))
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
    return row - 1


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


def _unique(base: str, used: set[str]) -> str:
    candidate, index = base, 2
    while candidate in used:
        candidate = f"{base}-{index}"
        index += 1
    used.add(candidate)
    return candidate
