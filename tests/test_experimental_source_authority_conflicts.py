"""Controlled synthetic interleavings; not hostile concurrency or crash tests."""
import hashlib
import json
import os
import uuid
from pathlib import Path

import pytest

from runevidence import _experimental_staged_reader as reader
from runevidence import _experimental_staged_validation as facade
from runevidence import _experimental_versioned_manifest as stage
from runevidence.checks import run_checks
from runevidence.manifest import load_manifest
from runevidence.report import format_findings, format_overview, to_json


@pytest.fixture
def scratch():
    root = Path(os.environ['R5_EVIDENCE_ROOT'])
    assert root.is_absolute() and root.parent == Path(os.environ['LOCALAPPDATA']) / 'Temp'
    path = root / ('runevidence-staging-test-' + uuid.uuid4().hex)
    path.mkdir()
    return path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def setup_stage(parent):
    artifact = parent / 'artifact.txt'
    artifact.write_bytes(b'original')
    payload = {'project': {'id': 'r9.synthetic', 'name': 'Invented'}, 'runs': [{'id': 'r'}],
               'artifacts': [{'id': 'a', 'path': 'artifact.txt', 'sha256': digest(artifact),
                              'size_bytes': 8, 'run_id': 'r'}]}
    source = parent / 'manifest.json'
    source.write_bytes((json.dumps(payload, indent=2) + '\n').encode())
    loaded = load_manifest(source)
    receipt = stage.stage_validated_manifest(loaded, scratch_parent=parent)
    return loaded, receipt, artifact


def trace(parent, loaded, receipt, artifact, rows, function, revision, status, expected):
    rows.append({'step': len(rows) + 1, 'function': function, 'source_sha256': digest(loaded.path),
                 'stage_sha256': digest(receipt.database_path), 'artifact_sha256': digest(artifact),
                 'revision': revision, 'status': status, 'expected': expected,
                 'unplanned_writers': 'none observed in controlled process',
                 'schedule': 'synthetic deterministic'})
    (parent / 'trace.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')


def update(receipt, loaded, operation, replacement=None):
    return stage.compare_and_update_stage(receipt, expected_revision=1, operation_id=operation,
        replacement_source_bytes=loaded.source_bytes if replacement is None else replacement)


def test_baseline_and_root(scratch):
    loaded, receipt, artifact = setup_stage(scratch)
    rows = []
    ordinary = run_checks(loaded)
    view = reader.load_staged_for_read(receipt)
    result = facade.validate_staged_readonly(receipt)
    assert view.loaded.source_bytes == loaded.source_bytes == loaded.path.read_bytes()
    assert view.loaded.path == loaded.path and view.loaded.root == scratch
    assert receipt.database_path != loaded.path
    assert result.report == ordinary
    assert result.findings_text == format_findings(ordinary, False)
    assert result.findings_json == to_json(ordinary, False)
    assert result.overview == format_overview(loaded.manifest)
    assert result.exit_code == ordinary.exit_code(False)
    trace(scratch, loaded, receipt, artifact, rows, 'reader and facade', 1, 'accepted', 'accept')


@pytest.mark.parametrize('kind', ['cas-first', 'json-first', 'whitespace', 'interposed-json', 'interposed-cas'])
def test_source_and_stage_divergence(scratch, monkeypatch, kind):
    loaded, receipt, artifact = setup_stage(scratch)
    rows = []
    trace(scratch, loaded, receipt, artifact, rows, 'stage_validated_manifest', 1, 'accepted', 'accept')
    def intervene():
        if kind in ('cas-first', 'interposed-cas'):
            assert update(receipt, loaded, 'r9-cas').status == 'applied'
            trace(scratch, loaded, receipt, artifact, rows, 'compare_and_update_stage', 2, 'applied', 'refuse')
        else:
            # Emulated legacy edit on an exclusively owned synthetic file.
            changed = (loaded.source_bytes.replace(b'Invented', b'Modified')
                       if kind == 'json-first' else loaded.source_bytes + b' ')
            with loaded.path.open('wb') as f:
                f.write(changed)
            assert (json.loads(changed) == json.loads(loaded.source_bytes)) == (kind != 'json-first')
            trace(scratch, loaded, receipt, artifact, rows, 'emulated legacy edit', 1, 'written', 'refuse')
    if kind.startswith('interposed'):
        original = facade.run_checks
        def barrier(view):
            report = original(view)
            intervene()
            return report
        monkeypatch.setattr(facade, 'run_checks', barrier)
    else:
        intervene()
    with pytest.raises(reader.StagedReadError) as caught:
        facade.validate_staged_readonly(receipt)
    assert caught.value.reason == 'stale-source'
    trace(scratch, loaded, receipt, artifact, rows, 'validate_staged_readonly',
          2 if 'cas' in kind else 1, caught.value.reason, 'refuse')
    if kind == 'cas-first':
        assert loaded.path.read_bytes() == loaded.source_bytes
    if kind in ('cas-first', 'json-first', 'whitespace'):
        with pytest.raises(reader.StagedReadError):
            reader.load_staged_for_read(receipt)


def test_artifact_mutation_and_aba(scratch, monkeypatch):
    loaded, receipt, artifact = setup_stage(scratch)
    rows = []
    baseline = run_checks(loaded)
    initial_db = digest(receipt.database_path)
    original = facade.run_checks
    def barrier(view):
        before = original(view)
        artifact.write_bytes(b'changed!')
        after = original(view)
        assert before != after
        trace(scratch, loaded, receipt, artifact, rows, 'artifact-only mutation', 1, 'changed findings', 'accept parity')
        artifact.write_bytes(b'original')
        assert original(view) == before
        trace(scratch, loaded, receipt, artifact, rows, 'artifact ABA', 1, 'restored', 'accept parity')
        return after
    monkeypatch.setattr(facade, 'run_checks', barrier)
    result = facade.validate_staged_readonly(receipt)
    assert result.report != baseline and digest(receipt.database_path) == initial_db
    assert loaded.path.read_bytes() == loaded.source_bytes
    trace(scratch, loaded, receipt, artifact, rows, 'validate_staged_readonly', 1, 'accepted', 'accept parity')


def test_stale_stage_writer_does_not_arbitrate_json(scratch):
    loaded, receipt, artifact = setup_stage(scratch)
    rows = []
    candidate_a = loaded.source_bytes + b' '
    candidate_b = loaded.source_bytes + b'\n'
    assert update(receipt, loaded, 'candidate-a', candidate_a).status == 'applied'
    trace(scratch, loaded, receipt, artifact, rows, 'candidate-a CAS', 2, 'applied', 'accept')
    assert update(receipt, loaded, 'candidate-b', candidate_b).status == 'conflict'
    trace(scratch, loaded, receipt, artifact, rows, 'candidate-b CAS', 2, 'conflict', 'conflict')
    with loaded.path.open('wb') as f:
        f.write(candidate_b)
    assert loaded.path.read_bytes() == candidate_b
    assert stage.read_staged_manifest(receipt).source_bytes == candidate_a
    trace(scratch, loaded, receipt, artifact, rows, 'emulated legacy edit', 2, 'written', 'not arbitrated')

@pytest.mark.parametrize('kind', ['missing', 'corrupt', 'sidecar', 'forged', 'busy'])
def test_fail_closed(scratch, kind):
    import sqlite3
    from dataclasses import replace
    loaded, receipt, artifact = setup_stage(scratch)
    rows = []
    original_receipt = receipt
    holder = None
    if kind == 'missing':
        receipt.database_path.unlink()
    elif kind == 'corrupt':
        receipt.database_path.write_bytes(b'not sqlite')
    elif kind == 'sidecar':
        Path(str(receipt.database_path) + '-journal').write_bytes(b'unexpected')
    elif kind == 'forged':
        receipt = replace(receipt, store_id=uuid.uuid4().hex)
    elif kind == 'busy':
        holder = sqlite3.connect(receipt.database_path, isolation_level=None)
    try:
        if holder is not None: holder.execute('BEGIN EXCLUSIVE')
        with pytest.raises(reader.StagedReadError) as caught:
            facade.validate_staged_readonly(receipt)
        assert caught.value.reason == {'missing': 'missing', 'corrupt': 'invalid-stage',
            'sidecar': 'invalid-stage', 'forged': 'invalid-stage', 'busy': 'busy'}[kind]
    finally:
        if holder is not None:
            holder.close()
    trace(scratch, loaded, original_receipt, artifact, rows, 'validate_staged_readonly',
          1, caught.value.reason, 'refuse without fallback')
