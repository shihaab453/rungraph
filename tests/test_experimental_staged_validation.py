"""Genuine private facade calls using invented disposable staging stores only."""
import hashlib
import json
import os
import sqlite3
import uuid
from dataclasses import FrozenInstanceError, replace
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


def staged(parent):
    payload = {'project': {'id': 'r8.synthetic', 'name': 'Made up'}, 'runs': [{'id': 'r'}],
               'artifacts': [{'id': 'stale', 'path': 'stale.txt', 'sha256': hashlib.sha256(b'old').hexdigest(),
                              'size_bytes': 3, 'run_id': 'r'},
                             {'id': 'missing', 'path': 'missing.txt', 'sha256': '0' * 64,
                              'size_bytes': 1, 'run_id': 'r'}]}
    source = (json.dumps(payload, indent=1).replace('\n', '\r\n') + '\r\n').encode()
    with (parent / 'stale.txt').open('xb') as f:
        f.write(b'new bytes')
    path = parent / 'manifest.json'
    with path.open('xb') as f:
        f.write(source)
    loaded = load_manifest(path)
    return loaded, stage.stage_validated_manifest(loaded, scratch_parent=parent)


def state(parent):
    entries = {}
    for base, dirs, files in os.walk(parent):
        for name in dirs + files:
            p = Path(base, name)
            st = p.lstat()
            entries[p.relative_to(parent).as_posix()] = (st.st_size, st.st_mtime_ns, st.st_ino,
                                                         hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
    return entries


@pytest.mark.parametrize('kind', ['normal', 'strict'])
def test_real_checks_match_json_and_leave_tree_unchanged(scratch, monkeypatch, kind):
    original, receipt = staged(scratch)
    strict = kind == 'strict'
    baseline = run_checks(original)
    before = state(scratch)
    def forbidden(*args, **kwargs):
        pytest.fail('writer reached during validation')
    import runevidence.manifest as manifest
    for module, name in ((manifest, 'save_raw'), (manifest, 'save_manifest'),
                         (stage, 'stage_validated_manifest'), (stage, 'compare_and_update_stage'), (os, 'replace')):
        monkeypatch.setattr(module, name, forbidden)
    result = facade.validate_staged_readonly(receipt, strict=strict)
    assert result.staged.loaded.source_bytes == original.source_bytes == original.path.read_bytes()
    assert result.staged.loaded.path == original.path and result.staged.loaded.root == scratch
    assert result.report == baseline and result.report.findings
    assert result.findings_text == format_findings(baseline, strict)
    assert result.findings_json == to_json(baseline, strict)
    assert result.overview == format_overview(original.manifest)
    assert result.exit_code == baseline.exit_code(strict)
    assert state(scratch) == before
    with pytest.raises(FrozenInstanceError):
        result.exit_code = 3


@pytest.mark.parametrize('kind', ['missing', 'corrupt', 'sidecar', 'source', 'cas', 'forged', 'foreign', 'busy'])
def test_refusals_never_fall_back(scratch, kind):
    original, receipt = staged(scratch)
    db = receipt.database_path
    holder = None
    if kind == 'missing': db.unlink()
    elif kind == 'corrupt': db.write_bytes(b'not sqlite')
    elif kind == 'sidecar': Path(str(db) + '-journal').write_bytes(b'unexpected')
    elif kind == 'source': original.path.write_bytes(original.source_bytes + b' ')
    elif kind == 'cas':
        assert stage.compare_and_update_stage(receipt, expected_revision=1, operation_id='r8-test',
                                              replacement_source_bytes=original.source_bytes).status == 'applied'
    elif kind == 'forged': receipt = replace(receipt, store_id=uuid.uuid4().hex)
    elif kind == 'foreign': receipt = object()
    elif kind == 'busy':
        holder = sqlite3.connect(db, isolation_level=None)
    try:
        if holder is not None: holder.execute('BEGIN EXCLUSIVE')
        with pytest.raises(reader.StagedReadError) as caught:
            facade.validate_staged_readonly(receipt)
        assert str(scratch) not in str(caught.value)
        assert caught.value.reason == {'missing': 'missing', 'corrupt': 'invalid-stage',
            'sidecar': 'invalid-stage', 'source': 'stale-source', 'cas': 'stale-source',
            'forged': 'invalid-stage', 'foreign': 'invalid-stage', 'busy': 'busy'}[kind]
    finally:
        if holder is not None: holder.close()


@pytest.mark.parametrize('kind', ['source', 'stage'])
def test_changed_between_observations_refuses(scratch, monkeypatch, kind):
    original, receipt = staged(scratch)
    real = facade.run_checks
    def change_after_checks(loaded):
        report = real(loaded)
        if kind == 'source': original.path.write_bytes(original.source_bytes + b' ')
        else:
            assert stage.compare_and_update_stage(receipt, expected_revision=1, operation_id='r8-race',
                                                   replacement_source_bytes=original.source_bytes).status == 'applied'
        return report
    monkeypatch.setattr(facade, 'run_checks', change_after_checks)
    with pytest.raises(reader.StagedReadError):
        facade.validate_staged_readonly(receipt)
