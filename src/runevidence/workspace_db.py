"""Derived SQLite export of a workspace document (JSON remains authority)."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from .workspace import LoadedWorkspace

FORMAT = "rungraph-sqlite-export-v1"
SCHEMA = """
CREATE TABLE workspace (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT, metadata TEXT NOT NULL);
CREATE TABLE members (id TEXT PRIMARY KEY, path TEXT NOT NULL, manifest TEXT NOT NULL, role TEXT);
CREATE TABLE claims (id TEXT PRIMARY KEY, text TEXT NOT NULL, source TEXT, metadata TEXT NOT NULL);
CREATE TABLE claim_refs (claim_id TEXT NOT NULL, relation TEXT NOT NULL, member TEXT, target TEXT NOT NULL);
CREATE TABLE relationships (id TEXT PRIMARY KEY, type TEXT NOT NULL, source_member TEXT, source_id TEXT, target_member TEXT, target_id TEXT, note TEXT);
"""


def export_workspace(loaded: LoadedWorkspace, output: Path, *, force: bool = False) -> dict[str, Any]:
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
            digest = hashlib.sha256(loaded.path.read_bytes()).hexdigest()
            connection.execute("insert into workspace values (?, ?, ?, ?)", (
                loaded.document.id, loaded.document.name, loaded.document.description,
                json.dumps(loaded.document.metadata, sort_keys=True),
            ))
            connection.executemany(
                "insert into members values (?, ?, ?, ?)",
                [(m.id, m.path, m.manifest, m.role) for m in loaded.document.members],
            )
            for claim in loaded.document.claims:
                connection.execute("insert into claims values (?, ?, ?, ?)", (
                    claim.id, claim.claim, claim.source,
                    json.dumps(claim.metadata, sort_keys=True),
                ))
                for relation, refs in (("supports", claim.supports), ("contradicts", claim.contradicts)):
                    connection.executemany(
                        "insert into claim_refs values (?, ?, ?, ?)",
                        [(claim.id, relation, ref.member, ref.id or ref.claim or "") for ref in refs],
                    )
            connection.executemany(
                "insert into relationships values (?, ?, ?, ?, ?, ?, ?)",
                [
                    (r.id, r.type.value, r.source.member, r.source.id or r.source.claim,
                     r.target.member, r.target.id or r.target.claim, r.note)
                    for r in loaded.document.relationships
                ],
            )
            connection.execute("create table meta (key text primary key, value text)")
            connection.executemany("insert into meta values (?, ?)", [
                ("format", FORMAT), ("version", "0.1"), ("workspace_sha256", digest),
            ])
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
        "format": FORMAT, "version": "0.1", "path": str(output),
        "workspace_sha256": digest,
        "counts": {
            "members": len(loaded.document.members),
            "claims": len(loaded.document.claims),
            "relationships": len(loaded.document.relationships),
        },
        "exit_code": 0,
    }
