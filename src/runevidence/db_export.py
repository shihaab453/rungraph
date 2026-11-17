"""Derived read-only SQLite export of one validated manifest.

JSON remains the authority. The database is a disposable query cache: it is
built from the manifest snapshot, never read back by validation, and never
changes a manifest or artifact.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .manifest import LoadedManifest

FORMAT = "runevidence-db-export-v1"
SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE project (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT, metadata TEXT NOT NULL);
CREATE TABLE runs (id TEXT PRIMARY KEY, name TEXT, started_at TEXT, params TEXT NOT NULL, conditions TEXT, metadata TEXT NOT NULL);
CREATE TABLE artifacts (id TEXT PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT, size_bytes INTEGER, kind TEXT, run_id TEXT, volatile INTEGER NOT NULL, artifact_type TEXT, digest_format TEXT, dataset_id TEXT, metadata TEXT NOT NULL);
CREATE TABLE results (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, name TEXT NOT NULL, value TEXT, artifact_id TEXT, locator TEXT, metadata TEXT NOT NULL, metric_definition TEXT);
CREATE TABLE evidence (id TEXT PRIMARY KEY, claim TEXT NOT NULL, source TEXT, metadata TEXT NOT NULL);
CREATE TABLE evidence_refs (evidence_id TEXT NOT NULL, relation TEXT NOT NULL, target TEXT NOT NULL);
CREATE TABLE relationships (source TEXT NOT NULL, target TEXT NOT NULL, type TEXT NOT NULL, note TEXT);
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True)


def export_sqlite(loaded: LoadedManifest, output: Path, *, force: bool = False) -> dict[str, Any]:
    output = Path(output)
    if output.exists() and not force:
        raise ValueError(f"{output} already exists; use --force to replace it")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    try:
        if staging.exists():
            staging.unlink()
        connection = sqlite3.connect(staging)
        try:
            connection.executescript(SCHEMA)
            manifest = loaded.manifest
            digest = hashlib.sha256(loaded.source_bytes).hexdigest()
            connection.executemany(
                "INSERT INTO meta(key, value) VALUES (?, ?)",
                [
                    ("format", FORMAT),
                    ("version", "0.1"),
                    ("schema_version", manifest.schema_version),
                    ("manifest_sha256", digest),
                ],
            )
            project = manifest.project
            connection.execute(
                "INSERT INTO project(id, name, description, metadata) VALUES (?, ?, ?, ?)",
                (project.id, project.name, project.description, _json(project.metadata)),
            )
            for run in manifest.runs:
                connection.execute(
                    "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        run.id,
                        run.name,
                        run.started_at.isoformat() if run.started_at else None,
                        _json(run.params),
                        _json(run.conditions.model_dump(mode="json")) if run.conditions else None,
                        _json(run.metadata),
                    ),
                )
            for artifact in manifest.artifacts:
                connection.execute(
                    "INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        artifact.id,
                        artifact.path,
                        artifact.sha256,
                        artifact.size_bytes,
                        artifact.kind,
                        artifact.run_id,
                        int(artifact.volatile),
                        artifact.artifact_type,
                        artifact.digest_format,
                        getattr(artifact, "dataset_id", None),
                        _json(artifact.metadata),
                    ),
                )
            for result in manifest.results:
                connection.execute(
                    "INSERT INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        result.id,
                        result.run_id,
                        result.name,
                        _json(result.value),
                        result.artifact_id,
                        _json(result.locator) if result.locator is not None else None,
                        _json(result.metadata),
                        _json(result.metric_definition) if getattr(result, "metric_definition", None) else None,
                    ),
                )
            for evidence in manifest.evidence:
                connection.execute(
                    "INSERT INTO evidence VALUES (?, ?, ?, ?)",
                    (evidence.id, evidence.claim, evidence.source, _json(evidence.metadata)),
                )
                for relation, refs in (("supports", evidence.supports), ("contradicts", evidence.contradicts)):
                    connection.executemany(
                        "INSERT INTO evidence_refs VALUES (?, ?, ?)",
                        [(evidence.id, relation, ref) for ref in refs],
                    )
            for relationship in manifest.relationships:
                connection.execute(
                    "INSERT INTO relationships VALUES (?, ?, ?, ?)",
                    (relationship.source, relationship.target, relationship.type.value, relationship.note),
                )
            connection.commit()
        finally:
            connection.close()
        with staging.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(staging, output)
    finally:
        try:
            staging.unlink()
        except OSError:
            pass
    return {
        "format": FORMAT,
        "version": "0.1",
        "path": str(output),
        "manifest_sha256": digest,
        "counts": {
            "runs": len(manifest.runs),
            "artifacts": len(manifest.artifacts),
            "results": len(manifest.results),
            "evidence": len(manifest.evidence),
            "relationships": len(manifest.relationships),
        },
        "exit_code": 0,
    }
