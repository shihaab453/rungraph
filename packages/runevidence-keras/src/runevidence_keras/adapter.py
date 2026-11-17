"""Read a Keras training-history CSV into a RunEvidence manifest.

``pd.DataFrame(history.history).to_csv("history.csv")`` writes a header of
metric columns and an index column (usually empty or named ``epoch``). This
adapter records every file as a hashed artifact and every numeric metric cell
as a schema 0.4 result with a ``csv-cell`` locator. The CSV is parsed with the
same strict profile the checker uses, so a malformed file is refused rather
than partially imported.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from runevidence.adapters import Adapter, AdapterError, Discovery, GenericAdapter
from runevidence.adapters.generic import valid_slug
from runevidence.checks.csv import CsvProfile, CsvProfileError, parse_number
from runevidence.checks import files as check_files
from runevidence.checks.files import _parts_for, _read_snapshot_parts, assert_tree_readable
from runevidence.models import Artifact, Manifest04, Project, Result04, Run

HISTORY_NAME = "history.csv"
MAX_CSV_BYTES = 50 * 1024 * 1024
MAX_RESULTS = 10_000


class KerasAdapter(Adapter):
    name = "keras"
    description = (
        "Keras history CSV folders: one run, numeric metric cells as csv-cell results."
    )

    def discover(self, root: Path) -> Discovery:
        try:
            assert_tree_readable(root)
        except OSError:
            raise AdapterError(
                "a folder under the selected root cannot be listed; nothing was written"
            ) from None
        return GenericAdapter().discover(root)

    def extract(
        self, root: Path, discovery: Discovery, *, project_id: str, project_name: str
    ) -> Manifest04:
        root = Path(root)
        try:
            assert_tree_readable(root)
        except OSError:
            raise AdapterError(
                "a folder under the selected root cannot be listed; nothing was written"
            ) from None
        if not check_files._DIR_FD:
            raise AdapterError(
                "the Keras adapter needs POSIX directory-descriptor support for protected "
                "reads; use --adapter generic on this host. Nothing was written."
            )
        candidates = [
            rel
            for rel in discovery.files
            if rel.lower().endswith(".csv") and "/" not in rel
        ]
        preferred = [rel for rel in candidates if rel.lower() == HISTORY_NAME]
        if preferred:
            history = preferred[0]
        elif len(candidates) == 1:
            history = candidates[0]
        elif not candidates:
            raise AdapterError(
                "no Keras history CSV found (expected history.csv or one CSV with an epoch header). "
                "Point init at the folder that holds the history file, or use --adapter generic."
            )
        else:
            raise AdapterError(
                "more than one CSV is present and none is named history.csv; "
                "keep one history file or use --adapter generic. Nothing was written."
            )
        read = _read_snapshot_parts(
            root, _parts_for(root, history), cap=MAX_CSV_BYTES
        )
        if read.state != "ok" or read.snapshot is None or read.snapshot.data is None:
            raise AdapterError(
                f"history file {history!r} cannot be read ({read.state}); nothing was written"
            )
        data = read.snapshot.data
        history_snapshot = read.snapshot
        try:
            first = CsvProfile(data)
        except CsvProfileError as exc:
            if getattr(exc, "reason", None) == "header-empty":
                raise AdapterError(
                    "history CSV has an empty header column; save it with "
                    "index_label='epoch' (or add an epoch column). Nothing was written."
                ) from None
            raise AdapterError(f"history CSV is not valid ({exc})") from None
        header = first.header
        row_count = first.row_count
        if row_count > MAX_RESULTS:
            raise AdapterError(
                f"more than {MAX_RESULTS} data rows; split the history first. Nothing was written."
            )
        epoch_index = _epoch_index(header)
        if epoch_index is None:
            raise AdapterError(
                "history CSV has no epoch column or empty index column; nothing was written"
            )
        rows = min(row_count, MAX_RESULTS)
        try:
            profile = CsvProfile(data, rows=range(1, rows + 1))
        except CsvProfileError as exc:
            raise AdapterError(f"history CSV is not valid ({exc})") from None
        used: set[str] = {project_id}
        run_id = _unique("run-1", used)
        results: list[Result04] = []
        names: set[str] = set()
        skipped = 0
        for row in range(1, rows + 1):
            epoch = parse_number(profile.cell(row, epoch_index))
            if not isinstance(epoch, int) or isinstance(epoch, bool):
                epoch = row - 1
            for column, name in enumerate(header, start=1):
                if column == epoch_index or not name:
                    continue
                value = parse_number(profile.cell(row, column))
                if not isinstance(value, int | float) or isinstance(value, bool):
                    skipped += 1
                    continue
                base = f"{name} @ {epoch}"
                result_name = base
                suffix = 2
                while result_name in names:
                    result_name = f"{base} #{suffix}"
                    suffix += 1
                names.add(result_name)
                pointer = hashlib.sha256(
                    f"{run_id}\x00{history}\x00{row}\x00{column}".encode("utf-8")
                ).hexdigest()[:16]
                result_id = f"keras-res-{pointer}"
                if result_id in used:
                    raise AdapterError("a generated id collides with an existing id; nothing was written")
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
        artifacts: list[Artifact] = []
        history_id: str | None = None
        for rel in discovery.files:
            if rel == history:
                digest = history_snapshot.digest
                size = history_snapshot.size
            else:
                artifact_read = _read_snapshot_parts(root, _parts_for(root, rel))
                if (
                    artifact_read.state not in ("ok", "over-cap")
                    or artifact_read.snapshot is None
                ):
                    raise AdapterError(
                        f"artifact {rel!r} cannot be read ({artifact_read.state}); "
                        "nothing was written"
                    )
                digest = artifact_read.snapshot.digest
                size = artifact_read.snapshot.size
            artifact_id = _unique(valid_slug(rel), used)
            if rel == history:
                history_id = artifact_id
            artifacts.append(
                Artifact(
                    id=artifact_id,
                    path=rel,
                    sha256=digest,
                    size_bytes=size,
                    run_id=run_id,
                )
            )
        for result in results:
            result.artifact_id = history_id
        return Manifest04(
            schema_version="0.4",
            project=Project(id=project_id, name=project_name),
            runs=[Run(id=run_id, name=root.name)],
            artifacts=artifacts,
            results=results,
        )


def _epoch_index(header: tuple[str, ...]) -> int | None:
    for index, name in enumerate(header, start=1):
        if name.lower() == "epoch":
            return index
    return None


def _unique(base: str, used: set[str]) -> str:
    candidate, index = base, 2
    while candidate in used:
        candidate = f"{base}-{index}"
        index += 1
    used.add(candidate)
    return candidate
