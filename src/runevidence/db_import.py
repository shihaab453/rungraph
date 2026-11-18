"""Import a derived manifest SQLite cache back into a new JSON manifest."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .manifest import dump_manifest, validate_manifest

FORMAT = "runevidence-db-import-v1"


def _drop_none(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


def import_sqlite(database: Path, output: Path, *, force: bool = False) -> dict[str, Any]:
    database, output = Path(database), Path(output)
    if not database.is_file():
        raise ValueError("database-not-found")
    if output.exists() and not force:
        raise ValueError(f"{output} already exists; use --force to replace it")
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        meta = dict(connection.execute("select key, value from meta"))
        if meta.get("format") != "runevidence-db-export-v1":
            raise ValueError("not a runevidence manifest export")
        project = connection.execute(
            "select id, name, description, metadata from project"
        ).fetchone()
        if project is None:
            raise ValueError("database has no project row")
        document: dict[str, Any] = {
            "schema_version": meta["schema_version"],
            "project": _drop_none({
                "id": project[0], "name": project[1], "description": project[2],
                "metadata": json.loads(project[3]),
            }),
            "runs": [
                _drop_none({
                    "id": row[0], "name": row[1], "started_at": row[2],
                    "params": json.loads(row[3]),
                    "conditions": json.loads(row[4]) if row[4] else None,
                    "metadata": json.loads(row[5]),
                })
                for row in connection.execute(
                    "select id, name, started_at, params, conditions, metadata from runs order by id"
                )
            ],
            "artifacts": [
                _drop_none({
                    "id": row[0], "path": row[1], "sha256": row[2], "size_bytes": row[3],
                    "kind": row[4], "run_id": row[5], "volatile": bool(row[6]),
                    "artifact_type": row[7], "digest_format": row[8], "dataset_id": row[9],
                    "metadata": json.loads(row[10]),
                })
                for row in connection.execute(
                    "select id, path, sha256, size_bytes, kind, run_id, volatile, "
                    "artifact_type, digest_format, dataset_id, metadata from artifacts order by id"
                )
            ],
            "results": [
                _drop_none({
                    "id": row[0], "run_id": row[1], "name": row[2],
                    "value": json.loads(row[3]) if row[3] is not None else None,
                    "artifact_id": row[4],
                    "locator": json.loads(row[5]) if row[5] is not None else None,
                    "metadata": json.loads(row[6]),
                    "metric_definition": json.loads(row[7]) if row[7] else None,
                })
                for row in connection.execute(
                    "select id, run_id, name, value, artifact_id, locator, metadata, "
                    "metric_definition from results order by id"
                )
            ],
            "relationships": [
                {"source": row[0], "target": row[1], "type": row[2], "note": row[3]}
                for row in connection.execute(
                    "select source, target, type, note from relationships"
                )
            ],
        }
        refs: dict[str, dict[str, list[str]]] = {}
        for evidence_id, relation, target in connection.execute(
            "select evidence_id, relation, target from evidence_refs"
        ):
            refs.setdefault(evidence_id, {"supports": [], "contradicts": []})[relation].append(target)
        document["evidence"] = [
            _drop_none({
                "id": row[0], "claim": row[1], "source": row[2],
                "metadata": json.loads(row[3]),
                "supports": refs.get(row[0], {}).get("supports", []),
                "contradicts": refs.get(row[0], {}).get("contradicts", []),
            })
            for row in connection.execute("select id, claim, source, metadata from evidence order by id")
        ]
    finally:
        connection.close()
    manifest = validate_manifest(document)
    data = dump_manifest(manifest).encode("utf-8")
    staging = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    try:
        staging.write_bytes(data + b"\n")
        with staging.open("rb+") as handle:
            os.fsync(handle.fileno())
        os.replace(staging, output)
    finally:
        try:
            staging.unlink()
        except OSError:
            pass
    return {
        "format": FORMAT, "version": "0.1", "path": str(output),
        "schema_version": manifest.schema_version,
        "manifest_sha256": hashlib.sha256(data + b"\n").hexdigest(),
        "counts": {
            "runs": len(manifest.runs), "artifacts": len(manifest.artifacts),
            "results": len(manifest.results), "evidence": len(manifest.evidence),
        },
        "exit_code": 0,
    }
