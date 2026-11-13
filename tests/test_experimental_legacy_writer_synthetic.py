"""Guarded real JSON writer schedules on invented local scratch only."""
import hashlib
import json
import os
import uuid
from pathlib import Path

import pytest

from runevidence import _experimental_staged_reader as reader
from runevidence import _experimental_versioned_manifest as stage
from runevidence.manifest import ManifestLoadError, load_manifest, load_raw, save_manifest, save_raw


@pytest.fixture
def scratch():
    root = Path(os.environ['R5_EVIDENCE_ROOT'])
    assert root.is_absolute() and root.parent == Path(os.environ['LOCALAPPDATA']) / 'Temp'
    path = root / ('runevidence-staging-test-' + uuid.uuid4().hex)
    path.mkdir()
    return path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def setup_source(parent):
    source = parent / 'manifest.json'
    data = {'project': {'id': 'r10.synthetic', 'name': 'Invented'}, 'runs': [], 'artifacts': []}
    before = (json.dumps(data, indent=2) + '\n').encode()
    with source.open('xb') as f:
        f.write(before)
    return load_manifest(source)


def receipt(parent, label, before, after, extra):
    (parent / (label + '-before.json')).write_bytes(before)
    (parent / (label + '-after.json')).write_bytes(after)
    (parent / (label + '-receipt.json')).write_text(json.dumps({'before_sha256': digest(before), 'after_sha256': digest(after), **extra}, indent=2), encoding='utf-8')


def test_writer_makes_source_stale(scratch):
    loaded = setup_source(scratch)
    staged = stage.stage_validated_manifest(loaded, scratch_parent=scratch)
    db_before = staged.database_path.read_bytes()
    raw = load_raw(loaded)
    raw['project']['name'] = 'Writer changed'
    save_raw(raw, loaded.path, expected_bytes=loaded.source_bytes)
    after = loaded.path.read_bytes()
    assert json.loads(after) == raw and staged.database_path.read_bytes() == db_before
    with pytest.raises(reader.StagedReadError) as exc:
        reader.load_staged_for_read(staged)
    assert exc.value.reason == 'stale-source'
    receipt(scratch, 'w1', loaded.source_bytes, after, {'reader': exc.value.reason, 'stage_sha256': digest(db_before), 'writer': 'save_raw'})


def test_stage_cas_does_not_arbitrate_json_writer(scratch):
    loaded = setup_source(scratch)
    staged = stage.stage_validated_manifest(loaded, scratch_parent=scratch)
    result = stage.compare_and_update_stage(staged, expected_revision=1, operation_id='r10-cas', replacement_source_bytes=loaded.source_bytes)
    assert result.status == 'applied'
    raw = load_raw(loaded)
    raw['project']['name'] = 'After stage CAS'
    save_raw(raw, loaded.path, expected_bytes=loaded.source_bytes)
    after = loaded.path.read_bytes()
    assert json.loads(after) == raw
    receipt(scratch, 'w2', loaded.source_bytes, after, {'stage_status': result.status, 'writer': 'save_raw'})


def test_sequential_stale_and_refreshed_expected_bytes(scratch):
    loaded = setup_source(scratch)
    first, second = load_raw(loaded), load_raw(loaded)
    first['project']['name'] = 'First'
    second['project']['name'] = 'Second'
    save_raw(first, loaded.path, expected_bytes=loaded.source_bytes)
    intermediate = loaded.path.read_bytes()
    with pytest.raises(OSError, match='changed since'):
        save_raw(second, loaded.path, expected_bytes=loaded.source_bytes)
    assert loaded.path.read_bytes() == intermediate
    save_raw(second, loaded.path, expected_bytes=intermediate)
    after = loaded.path.read_bytes()
    assert json.loads(after) == second
    receipt(scratch, 'w3', loaded.source_bytes, after, {'intermediate_sha256': digest(intermediate), 'stale_expected': 'rejected', 'refreshed_expected': 'overwrites first candidate', 'schedule': 'single process sequential'})


def test_new_manifest_and_missing_or_invalid(scratch):
    loaded = setup_source(scratch)
    new = scratch / 'new.json'
    save_manifest(loaded.manifest, new)
    after = new.read_bytes()
    assert load_manifest(new).manifest == loaded.manifest
    receipt(scratch, 'new', b'', after, {'writer': 'save_manifest new path'})
    with pytest.raises(ManifestLoadError):
        load_manifest(scratch / 'missing.json')
    invalid = scratch / 'invalid.json'
    invalid.write_bytes(b'{invalid')
    with pytest.raises(ManifestLoadError):
        load_manifest(invalid)
