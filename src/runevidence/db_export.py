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
CREATE TABLE project (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT, tags TEXT NOT NULL, metadata TEXT NOT NULL);
CREATE TABLE runs (id TEXT PRIMARY KEY, name TEXT, started_at TEXT, params TEXT NOT NULL, conditions TEXT, metadata TEXT NOT NULL);
CREATE TABLE artifacts (id TEXT PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT, size_bytes INTEGER, kind TEXT, run_id TEXT, volatile INTEGER NOT NULL, artifact_type TEXT, digest_format TEXT, dataset_id TEXT, metadata TEXT NOT NULL);
CREATE TABLE results (id TEXT PRIMARY KEY, run_id TEXT NOT NULL, name TEXT NOT NULL, value TEXT, artifact_id TEXT, locator TEXT, metadata TEXT NOT NULL, metric_definition TEXT);
CREATE TABLE evidence (id TEXT PRIMARY KEY, claim TEXT NOT NULL, source TEXT, metadata TEXT NOT NULL);
CREATE TABLE evidence_refs (evidence_id TEXT NOT NULL, relation TEXT NOT NULL, target TEXT NOT NULL);
CREATE TABLE relationships (source TEXT NOT NULL, target TEXT NOT NULL, type TEXT NOT NULL, note TEXT);
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=True, sort_keys=True)


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
                "INSERT INTO project(id, name, description, tags, metadata) VALUES (?, ?, ?, ?, ?)",
                (project.id, project.name, project.description, _json(project.tags), _json(project.metadata)),
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
                payload = result.model_dump(mode="json")
                connection.execute(
                    "INSERT INTO results VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        result.id,
                        result.run_id,
                        result.name,
                        _json(payload["value"]),
                        result.artifact_id,
                        _json(payload["locator"]) if payload["locator"] is not None else None,
                        _json(result.metadata),
                        _json(payload["metric_definition"]) if payload.get("metric_definition") else None,
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
        with staging.open("rb+") as handle:
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


def check_sqlite(loaded: LoadedManifest, database: Path) -> dict[str, Any]:
    """Compare a derived SQLite cache with the current manifest snapshot."""
    database = Path(database)
    if not database.is_file():
        return {"format": "runevidence-db-check-v1", "ok": False,
                "error": "database-not-found", "exit_code": 2}
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    except sqlite3.Error:
        return {"format": "runevidence-db-check-v1", "ok": False,
                "error": "database-unreadable", "exit_code": 2}
    manifest = loaded.manifest
    digest = hashlib.sha256(loaded.source_bytes).hexdigest()
    expected = {
        "runs": {run.id for run in manifest.runs},
        "artifacts": {artifact.id for artifact in manifest.artifacts},
        "results": {result.id for result in manifest.results},
        "evidence": {evidence.id for evidence in manifest.evidence},
    }
    mismatches: list[dict[str, Any]] = []
    try:
        row = connection.execute(
            "select value from meta where key='manifest_sha256'"
        ).fetchone()
        database_digest = row[0] if row else None
        if database_digest != digest:
            mismatches.append({"table": "meta", "reason": "manifest-changed"})
        for table, ids in expected.items():
            try:
                actual = {item[0] for item in connection.execute(f"select id from {table}")}
            except sqlite3.Error:
                mismatches.append({"table": table, "reason": "table-missing"})
                continue
            if actual != ids:
                mismatches.append({
                    "table": table,
                    "missing": sorted(ids - actual),
                    "extra": sorted(actual - ids),
                })
        expected_relationships = {
            (item.source, item.target, item.type.value)
            for item in manifest.relationships
        }
        expected_refs = {
            (evidence.id, relation, target)
            for evidence in manifest.evidence
            for relation, refs in (
                ("supports", evidence.supports),
                ("contradicts", evidence.contradicts),
            )
            for target in refs
        }
        expected_rows = {
            "project": {
                (manifest.project.id, manifest.project.name, manifest.project.description,
                 _json(manifest.project.tags), _json(manifest.project.metadata))
            },
            "runs": {
                (run.id, run.name, run.started_at.isoformat() if run.started_at else None,
                 _json(run.params),
                 _json(run.conditions.model_dump(mode="json")) if run.conditions else None,
                 _json(run.metadata))
                for run in manifest.runs
            },
            "artifacts": {
                (artifact.id, artifact.path, artifact.sha256, artifact.size_bytes,
                 artifact.kind, artifact.run_id, int(artifact.volatile),
                 artifact.artifact_type, artifact.digest_format,
                 getattr(artifact, "dataset_id", None), _json(artifact.metadata))
                for artifact in manifest.artifacts
            },
            "results": {
                (result.id, result.run_id, result.name, _json(result.value),
                 result.artifact_id,
                 _json(result.locator) if result.locator is not None else None,
                 _json(result.metadata),
                 _json(result.metric_definition) if getattr(result, "metric_definition", None) else None)
                for result in manifest.results
            },
            "evidence": {
                (evidence.id, evidence.claim, evidence.source, _json(evidence.metadata))
                for evidence in manifest.evidence
            },
            "relationships": expected_relationships,
            "evidence_refs": expected_refs,
        }
        queries = {
            "project": "select id, name, description, tags, metadata from project",
            "runs": "select id, name, started_at, params, conditions, metadata from runs",
            "artifacts": "select id, path, sha256, size_bytes, kind, run_id, volatile, artifact_type, digest_format, dataset_id, metadata from artifacts",
            "results": "select id, run_id, name, value, artifact_id, locator, metadata, metric_definition from results",
            "evidence": "select id, claim, source, metadata from evidence",
            "relationships": "select source, target, type from relationships",
            "evidence_refs": "select evidence_id, relation, target from evidence_refs",
        }
        for table, expected_table_rows in expected_rows.items():
            try:
                actual_rows = {
                    tuple(row) for row in connection.execute(queries[table])
                }
            except sqlite3.Error:
                mismatches.append({"table": table, "reason": "table-missing"})
                continue
            if actual_rows != expected_table_rows:
                mismatches.append({
                    "table": table,
                    "missing": sorted(expected_table_rows - actual_rows),
                    "extra": sorted(actual_rows - expected_table_rows),
                })
    finally:
        connection.close()
    return {
        "format": "runevidence-db-check-v1",
        "ok": not mismatches,
        "manifest_sha256": digest,
        "database_sha256": database_digest,
        "mismatches": mismatches,
        "exit_code": 0 if not mismatches else 1,
    }


def query_sqlite(database: Path, sql: str, *, limit: int = 1000) -> dict[str, Any]:
    """Run one read-only query against a derived cache."""
    statement = sql.strip()
    first = statement.split(None, 1)[0].upper() if statement else ""
    if first not in ("SELECT", "WITH"):
        return {"format": "runevidence-db-query-v1", "ok": False,
                "error": "only SELECT/WITH queries are allowed", "exit_code": 2}
    database = Path(database)
    if not database.is_file():
        return {"format": "runevidence-db-query-v1", "ok": False,
                "error": "database-not-found", "exit_code": 2}
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
        try:
            cursor = connection.execute(statement)
            columns = [item[0] for item in cursor.description or ()]
            rows = cursor.fetchmany(limit + 1)
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return {"format": "runevidence-db-query-v1", "ok": False,
                "error": str(exc), "exit_code": 2}
    truncated = len(rows) > limit
    return {
        "format": "runevidence-db-query-v1",
        "ok": True,
        "columns": columns,
        "rows": [list(row) for row in rows[:limit]],
        "truncated": truncated,
        "exit_code": 0,
    }
