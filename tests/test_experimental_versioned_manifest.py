"""R5: fresh synthetic Temp fixtures only. No native scans or application saves.

Fixture closure: scratch (local mkdir), monkeypatch (pytest undo only), request
(pytest metadata). No tmp_path, experiment, copytree, subprocess, DACL or links.
Separate connections and injected schedules below are not multiprocess races.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from runevidence import _experimental_versioned_manifest as bridge
from runevidence.manifest import LoadedManifest, dump_manifest, load_manifest
from runevidence.models import Manifest


@pytest.fixture
def scratch():
    evidence = Path(os.environ["R5_EVIDENCE_ROOT"])
    assert evidence.is_absolute() and evidence.parent == Path(os.environ["LOCALAPPDATA"]) / "Temp"
    parent = evidence / ("runevidence-staging-test-" + uuid.uuid4().hex)
    parent.mkdir(exist_ok=False)
    # Intentionally no cleanup: all fixtures/DBs become evidence.
    return parent


def source(*, directory=False, name="Made-up R5", project_id="r5.synthetic"):
    artifact = {"id": "a", "path": "invented", "sha256": "AB" * 32,
                "size_bytes": 7, "volatile": True, "metadata": {"nested": [None, {"x": "y"}]}}
    if directory:
        artifact.update(artifact_type="directory", digest_format="runevidence-directory-v1")
    raw = {
        "project": {"id": project_id, "name": name, "description": "Synthetic only",
                    "tags": ["r5"], "metadata": {"custom": {"keep": True}}},
        "runs": [{"id": "r", "params": {"alpha": 0.1}, "metadata": {"opaque": [1, "x"]}}],
        "artifacts": [{**artifact, "run_id": "r"}],
        "results": [{"id": "res", "run_id": "r", "name": "score", "value": 4,
                     "artifact_id": "a", "metadata": {"keep": "result"}}],
        "evidence": [{"id": "e", "claim": "Invented claim", "supports": ["res"],
                      "contradicts": [], "metadata": {"keep": "claim"}}],
        "relationships": [{"source": "a", "target": "r", "type": "produced_by", "note": "made up"}],
    }
    # Deliberately unlike dump_manifest: CRLF, explicit volatility, uppercase
    # digests, Unicode and omitted default fields/schema_version.
    return (json.dumps(raw, ensure_ascii=False, indent=1).replace("\n", "\r\n") + "\r\n").encode("utf-8")


def loaded(parent, payload=None):
    path = parent / "invented-manifest.json"
    path.write_bytes(source() if payload is None else payload)
    return load_manifest(path)


def stage(parent, payload=None):
    original = loaded(parent, payload)
    return original, bridge.stage_validated_manifest(original, scratch_parent=parent)


def update(receipt, payload, *, revision=1, op="op-1"):
    return bridge.compare_and_update_stage(receipt, expected_revision=revision,
                                           operation_id=op, replacement_source_bytes=payload)


@pytest.mark.parametrize("directory", [False, True], ids=["file", "directory"])
def test_actual_loader_model_and_exact_bytes(scratch, directory):
    payload = source(directory=directory, name="Made-up caf\u00e9")
    original, receipt = stage(scratch, payload)
    result = bridge.read_staged_manifest(receipt)
    assert result.source_bytes == original.source_bytes == payload
    assert result.manifest == original.manifest == Manifest.model_validate(json.loads(payload))
    assert result.sha256 == receipt.source_sha256 == hashlib.sha256(payload).hexdigest()
    assert receipt.source_byte_count == len(payload)
    assert (result.revision, result.generation) == (1, 1)
    assert dump_manifest(result.manifest).encode("utf-8") != payload
    assert result.manifest.artifacts[0].sha256 == "ab" * 32
    assert result.manifest.artifacts[0].volatile is True
    assert original.path.read_bytes() == payload


def test_exclusive_child_and_immutable_receipt(scratch):
    original = loaded(scratch)
    a = bridge.stage_validated_manifest(original, scratch_parent=scratch)
    b = bridge.stage_validated_manifest(original, scratch_parent=scratch)
    assert a.database_path != b.database_path and a.store_id != b.store_id
    assert a.database_path.parent.parent == scratch
    assert set(scratch.iterdir()) == {original.path, a.database_path.parent, b.database_path.parent}
    assert set(a.database_path.parent.iterdir()) == {a.database_path}
    with pytest.raises(FrozenInstanceError):
        a.revision = 7
    assert original.path.read_bytes() == original.source_bytes


def test_existing_stage_target_is_refused(scratch, monkeypatch):
    original, receipt = stage(scratch)
    before = receipt.database_path.read_bytes()
    monkeypatch.setattr(bridge.uuid, "uuid4", lambda: uuid.UUID(receipt.store_id))
    with pytest.raises(bridge.StageError):
        bridge.stage_validated_manifest(original, scratch_parent=scratch)
    assert receipt.database_path.read_bytes() == before


@pytest.mark.parametrize("kind", ["model", "relative", "freshness", "type", "schema"])
def test_loaded_identity_schema_and_source_refusals(scratch, kind):
    original = loaded(scratch)
    if kind == "model":
        original.manifest.project.id = "different"
    elif kind == "relative":
        original = replace(original, path=Path("invented-manifest.json"))
    elif kind == "freshness":
        original.path.write_bytes(source(name="Changed"))
    elif kind == "type":
        original = object()
    else:
        original = replace(original, source_bytes=b'{"schema_version":"9.9","project":{"id":"p","name":"p"}}')
    with pytest.raises(bridge.StageError) as caught:
        bridge.stage_validated_manifest(original, scratch_parent=scratch)
    assert caught.value.reason == "invalid"
    assert not list(scratch.glob("stage-*"))


def test_source_change_after_staging_is_advisory_refusal(scratch, monkeypatch):
    original = loaded(scratch)
    real = bridge._read_source
    calls = []

    def injected(path, payload):
        calls.append(path)
        if len(calls) == 2:
            path.write_bytes(source(name="Injected source change"))
        return real(path, payload)

    monkeypatch.setattr(bridge, "_read_source", injected)
    with pytest.raises(bridge.StageError) as caught:
        bridge.stage_validated_manifest(original, scratch_parent=scratch)
    assert caught.value.reason == "invalid" and len(calls) == 2
    assert len(list(scratch.glob("stage-*/stage.sqlite"))) == 1  # retained, non-authoritative


@pytest.mark.parametrize("kind", ["parent_name", "outside_source", "dotdot", "receipt_path"])
def test_unsafe_path_layouts_refuse_without_alias_creation(scratch, kind):
    original, receipt = stage(scratch)
    if kind == "receipt_path":
        with pytest.raises(bridge.StageError):
            bridge.read_staged_manifest(replace(receipt, database_path=original.path))
        return
    parent = scratch
    if kind == "parent_name":
        parent = scratch / "existing-project"
        parent.mkdir()
    elif kind == "outside_source":
        parent = scratch / ("runevidence-staging-test-" + uuid.uuid4().hex)
        parent.mkdir()
    else:
        parent = scratch / ".." / scratch.name
    with pytest.raises(bridge.StageError):
        bridge.stage_validated_manifest(original, scratch_parent=parent)
    assert bridge.read_staged_manifest(receipt).revision == 1


BAD_PAYLOADS = [b"", b"\xff", b"{", b"[]", b"x" * (bridge.MAX_SOURCE_BYTES + 1),
                b'{"schema_version":"foreign","project":{"id":"p","name":"p"}}',
                b'{"project":{"id":"p","name":"p"},"unknown":1}',
                b'{"project":{"id":"p","name":"p"},"artifacts":[{"id":"a","path":"a","artifact_type":"directory"}]}']


@pytest.mark.parametrize("payload", BAD_PAYLOADS,
                         ids=["empty", "utf8", "json", "array", "oversize", "foreign", "unknown", "directory-format"])
def test_bad_input_refuses_before_connection(scratch, monkeypatch, payload):
    original = loaded(scratch)
    attempted = []

    def forbidden(*args, **kwargs):
        attempted.append(True)
        raise AssertionError("Must validate before connecting")

    monkeypatch.setattr(bridge, "_connect", forbidden)
    with pytest.raises(bridge.StageError):
        bridge.stage_validated_manifest(replace(original, source_bytes=payload), scratch_parent=scratch)
    assert bridge.compare_and_update_stage(object(), expected_revision=1, operation_id="op",
                                          replacement_source_bytes=payload).status == "invalid"
    assert not attempted and not list(scratch.glob("stage-*"))


def test_revision_conflict_replay_collision_and_identical_bytes(scratch):
    original, receipt = stage(scratch)
    assert update(receipt, original.source_bytes).status == "applied"
    assert bridge.read_staged_manifest(receipt).revision == 2
    assert update(receipt, original.source_bytes).status == "already_committed"
    assert update(receipt, original.source_bytes, revision=2).status == "operation_id_collision"
    assert update(receipt, source(name="Different")).status == "operation_id_collision"
    assert update(receipt, original.source_bytes, op="stale").status == "conflict"
    assert update(receipt, source(name="Second"), revision=2, op="second").revision == 3
    historical = update(receipt, original.source_bytes)
    assert (historical.status, historical.revision) == ("already_committed", 2)
    assert bridge.read_staged_manifest(receipt).revision == 3
    assert original.path.read_bytes() == original.source_bytes


def test_separate_connections_one_success_one_conflict(scratch):
    # Two participants loaded revision 1, then serialize via independent
    # connections. This is deterministic stale-token contention, not a live race.
    original, receipt = stage(scratch)
    revision_a = bridge.read_staged_manifest(receipt).revision
    revision_b = bridge.read_staged_manifest(receipt).revision
    a = update(receipt, source(name="Writer A"), revision=revision_a, op="a")
    b = update(receipt, source(name="Writer B"), revision=revision_b, op="b")
    assert [a.status, b.status] == ["applied", "conflict"]
    assert update(receipt, source(name="Writer A"), revision=revision_a, op="a").status == "already_committed"
    with sqlite3.connect(receipt.database_path) as connection:
        assert connection.execute("SELECT operation_id,result_revision FROM receipts").fetchall() == [("a", 2)]
    assert original.path.read_bytes() == original.source_bytes


def test_busy_has_no_automatic_retry(scratch):
    original, receipt = stage(scratch)
    connection = sqlite3.connect(receipt.database_path, isolation_level=None)
    try:
        connection.execute("BEGIN IMMEDIATE")
        assert update(receipt, original.source_bytes).status == "busy"
    finally:
        connection.close()
    assert bridge.read_staged_manifest(receipt).revision == 1


def test_receipt_failure_rolls_back_state_in_same_transaction(scratch, monkeypatch):
    original, receipt = stage(scratch)
    before = receipt.database_path.read_bytes()
    real = bridge._connect

    class InjectedFailure:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, *args):
            if sql.startswith("INSERT INTO receipts"):
                raise sqlite3.IntegrityError("Injected receipt failure after UPDATE")
            return self.connection.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self.connection, name)

    monkeypatch.setattr(bridge, "_connect", lambda *a, **kw: InjectedFailure(real(*a, **kw)))
    assert update(receipt, source(name="Never committed")).status == "fatal"
    after = receipt.database_path.read_bytes()
    (scratch / "rollback-observation.json").write_text(json.dumps({
        "before_sha256": hashlib.sha256(before).hexdigest(),
        "after_sha256": hashlib.sha256(after).hexdigest(),
        "physical_bytes_equal_observed": before == after,
        "limitation": "Observed for this injected failure, not a SQLite physical-byte guarantee.",
    }, indent=2), encoding="utf-8")
    assert after == before
    assert bridge.read_staged_manifest(receipt).source_bytes == original.source_bytes
    with sqlite3.connect(receipt.database_path) as connection:
        assert connection.execute("SELECT count(*) FROM receipts").fetchone() == (0,)


@pytest.mark.parametrize("revision,op", [(True, "op"), (0, "op"), (-1, "op"),
                                       (1 << 63, "op"), (1, ""), (1, "x" * 65), (1, "a';--")])
def test_bounded_revision_and_identifier(scratch, monkeypatch, revision, op):
    original, receipt = stage(scratch)
    monkeypatch.setattr(bridge, "_connect", lambda *a, **kw: pytest.fail("Unexpected connection"))
    assert update(receipt, original.source_bytes, revision=revision, op=op).status == "invalid"


def test_foreign_replacement_identity_refused_before_connection(scratch, monkeypatch):
    _, receipt = stage(scratch)
    monkeypatch.setattr(bridge, "_connect", lambda *a, **kw: pytest.fail("Unexpected connection"))
    assert update(receipt, source(project_id="foreign")).status == "invalid"


def test_revision_overflow_refused(scratch, monkeypatch):
    original, receipt = stage(scratch)
    # Inject only the revision view to reach the overflow guard without making
    # an impossible 2**63-sized receipt history. This is an injected boundary.
    real = bridge._validated_state
    monkeypatch.setattr(bridge, "_validated_state",
                        lambda c, s: replace(real(c, s), revision=bridge.MAX_REVISION))
    assert update(receipt, original.source_bytes, revision=bridge.MAX_REVISION).status == "invalid"


@pytest.mark.parametrize("kind", ["missing", "corrupt", "hash", "model", "identity", "schema",
                                 "oversize", "sidecar", "receipt", "revision"])
def test_reader_and_writer_safe_refusal_on_tampering(scratch, kind):
    original, receipt = stage(scratch)
    path = receipt.database_path
    if kind == "missing":
        path.unlink()
    elif kind == "corrupt":
        path.write_bytes(b"not SQLite")
    elif kind == "oversize":
        with path.open("ab") as fh:
            fh.truncate(bridge.MAX_DB_BYTES + 1)
    elif kind == "sidecar":
        Path(str(path) + "-wal").write_bytes(b"unexpected")
    else:
        with sqlite3.connect(path) as connection:
            if kind == "hash":
                connection.execute("UPDATE state SET sha256=?", ("0" * 64,))
            elif kind == "model":
                payload = b'{"project":{"id":"p","name":"p"},"unknown":true}'
                connection.execute("UPDATE state SET source=?,nbytes=?,sha256=?",
                                   (payload, len(payload), hashlib.sha256(payload).hexdigest()))
            elif kind == "identity":
                connection.execute("UPDATE state SET store_id='different'")
            elif kind == "schema":
                connection.execute("CREATE TABLE foreign_table (x)")
            elif kind == "receipt":
                connection.execute("INSERT INTO receipts VALUES ('fake',1,?,1,'fake',2)", (b"x",))
            else:
                connection.execute("UPDATE state SET revision=2")
    with pytest.raises(bridge.StageError) as caught:
        bridge.read_staged_manifest(receipt)
    assert caught.value.reason in ("missing", "invalid")
    assert update(receipt, original.source_bytes).status in ("missing", "invalid")
    assert original.path.read_bytes() == original.source_bytes


def test_readback_never_falls_back_to_json(scratch):
    original, receipt = stage(scratch)
    original.path.unlink()
    assert bridge.read_staged_manifest(receipt).source_bytes == original.source_bytes


def test_no_public_route_or_production_writer_reference():
    # Fresh-process import topology is also checked by the external driver.
    import ast
    import runevidence.cli as cli

    module = Path(bridge.__file__).read_text(encoding="utf-8")
    tree = ast.parse(module)
    imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
               and node.module == "manifest"]
    assert [[name.name for name in node.names] for node in imports] == [["LoadedManifest", "validate_manifest"]]
    assert not any(isinstance(node, ast.Attribute) and node.attr in ("save_raw", "save_manifest", "replace")
                   for node in ast.walk(tree))
    choices = next(action.choices for action in cli.build_parser()._actions
                   if hasattr(action, "choices") and isinstance(action.choices, dict))
    # HISTORICAL R5 BASELINE (preserved record): the public surface was exactly
    # these seven routes when the staging bridge was qualified.
    historical_seven = {"validate", "inspect", "init", "rehash", "add", "schema", "adapters"}
    # RG-IND11-PC4 (2026-11-09) current reviewed public surface: exactly 23 routes.
    # M9-COMPARABILITY-STAGE2B-R1 (2026-11-10): 24 routes after the reviewed addition.
    # M9-COMPARABILITY-STAGE3B-2C1 (2026-11-11): 25 routes after the explicit migrate addition.
    # M9-COMPARABILITY-STAGE3D3-2 (2026-11-11): 26 routes after the conditions addition.
    # Any addition or removal must fail here until it is re-reviewed.
    current_public = {"validate", "validate-batch", "inspect", "compare", "comparability", "assess", "leaderboard", "matrix", "run",
                      "claim", "lineage", "impact", "coverage", "coverage-gate", "fingerprint",
                      "fingerprint-compare", "coverage-diff", "review-diff", "impact-diff", "review-gate",
                      "result", "evidence", "artifact", "relationship",
                      "init", "migrate", "conditions", "capture", "rehash", "add", "schema", "adapters"}
    assert historical_seven <= current_public and len(current_public) == 32
    assert set(choices) == current_public
    # The protected property: no private, experimental, staging or SQLite route is public.
    assert not [c for c in choices if c.startswith("_")
                or any(m in c.lower() for m in ("experimental", "sqlite", "stage", "shadow", "witness"))]


def test_exact_source_bound_and_maximum_operation_id(scratch):
    payload = source()
    payload += b" " * (bridge.MAX_SOURCE_BYTES - len(payload))
    original, receipt = stage(scratch, payload)
    assert len(original.source_bytes) == 256 * 1024
    assert bridge.read_staged_manifest(receipt).source_bytes == payload
    operation = "a" * 64
    assert update(receipt, payload, op=operation).status == "applied"
    assert update(receipt, payload, op=operation).status == "already_committed"
    assert bridge.read_staged_manifest(receipt).source_bytes == payload
    assert original.path.read_bytes() == payload


def test_receipt_capacity_refuses_without_changing_committed_history(scratch):
    original, receipt = stage(scratch)
    for revision in range(1, bridge.MAX_RECEIPTS + 1):
        result = update(receipt, original.source_bytes, revision=revision, op=f"capacity-{revision}")
        assert (result.status, result.revision) == ("applied", revision + 1)
    before = bridge.read_staged_manifest(receipt)
    assert before.revision == bridge.MAX_RECEIPTS + 1
    assert update(receipt, original.source_bytes, revision=before.revision, op="one-too-many").status == "invalid"
    assert update(receipt, original.source_bytes, op="capacity-1").status == "already_committed"
    assert bridge.read_staged_manifest(receipt) == before
    with sqlite3.connect(receipt.database_path) as connection:
        assert connection.execute("SELECT count(*) FROM receipts").fetchone() == (bridge.MAX_RECEIPTS,)
    assert original.path.read_bytes() == original.source_bytes


@pytest.mark.parametrize("kind", ["operation", "expected", "replacement", "size", "digest", "result", "column"])
def test_committed_receipt_and_column_integrity(scratch, kind):
    original, receipt = stage(scratch)
    assert update(receipt, source(name="Committed replacement")).status == "applied"
    with sqlite3.connect(receipt.database_path) as connection:
        assert connection.execute(
            "SELECT name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall() == [("receipts", bridge._RECEIPT_SQL), ("state", bridge._STATE_SQL)]
        if kind == "column":
            connection.execute("ALTER TABLE receipts RENAME COLUMN sha256 TO changed_digest")
        else:
            column, value = {
                "operation": ("operation_id", "!invalid"),
                "expected": ("expected_revision", 2),
                "replacement": ("replacement", b"not JSON"),
                "size": ("nbytes", 1),
                "digest": ("sha256", "0" * 64),
                "result": ("result_revision", 3),
            }[kind]
            connection.execute(f"UPDATE receipts SET {column}=?", (value,))
    with pytest.raises(bridge.StageError) as caught:
        bridge.read_staged_manifest(receipt)
    assert caught.value.reason == "invalid"
    assert update(receipt, original.source_bytes, revision=2, op="next").status == "invalid"
    assert original.path.read_bytes() == original.source_bytes
