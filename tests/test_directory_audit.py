"""Disposable native Windows audits; injected cases prove control flow only."""

from __future__ import annotations

import copy
import dataclasses
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import runevidence
from runevidence import directory_audit, directory_plan, directory_scanner
from runevidence.directory_apply import DirectoryApplyError
from runevidence.directory_authoring import DirectoryAuthoringError
from runevidence.directory_audit import DirectoryAuditError, audit_recorded_directories
from runevidence.directory_scanner import DirectoryScanError
from runevidence.edit import EditError
from runevidence.manifest import LoadedManifest, load_raw
from test_directory_preview import VECTORS, native, no_writes, raw_manifest, snapshot, write_manifest


def fixture(root, names=('data',), vector=None, raw=None):
    vector = VECTORS[1] if vector is None else vector
    raw = raw_manifest() if raw is None else copy.deepcopy(raw)
    for name in names:
        folder = root / name
        folder.mkdir()
        for item in vector['layout']:
            p = folder / item['path']
            if item['type'] == 'D': p.mkdir()
            else: p.write_bytes(bytes.fromhex(item['content_hex']))
        raw['artifacts'].append(dict(id=name, path=name, artifact_type='directory',
                                    digest_format='runevidence-directory-v1',
                                    sha256=vector['sha256'], size_bytes=vector['size_bytes']))
    return write_manifest(root, raw)


def checked(root, loaded, monkeypatch):
    lock = loaded.path.with_name(loaded.path.name + '.cooperative-writer.lock')
    if not lock.exists(): lock.write_bytes(b'existing synthetic lock: do not inspect')
    before = snapshot(root)
    model = copy.deepcopy(loaded.manifest.model_dump())
    with no_writes(monkeypatch):
        result = audit_recorded_directories(loaded)
    assert snapshot(root) == before
    assert loaded.manifest.model_dump() == model
    assert result.total == len(result.entries) == result.unchanged + result.would_change + result.refused
    assert len(json.dumps(dataclasses.asdict(result)).encode()) <= directory_audit.MAX_AUDIT_JSON_BYTES
    return result


def test_zero_only_explicit_directory_candidates_and_two_source_reads(tmp_path, monkeypatch):
    raw = raw_manifest()
    raw['artifacts'] = [{'id': 'ordinary', 'path': 'folder'}]
    (tmp_path / 'folder').mkdir()
    (tmp_path / 'unrecorded').mkdir()
    loaded = write_manifest(tmp_path, raw)
    reads = []
    original = Path.read_bytes
    def read(p):
        if p == loaded.path: reads.append(p)
        return original(p)
    def forbid(*a, **kw): raise AssertionError('no directory candidates may scan or plan')
    with monkeypatch.context() as m:
        m.setattr(Path, 'read_bytes', read)
        m.setattr(directory_audit, 'plan_directory_change', forbid)
        m.setattr(directory_scanner, 'scan_directory', forbid)
        result = audit_recorded_directories(loaded)
    assert len(reads) == 2
    assert (result.state, result.entries, result.total) == ('no-directory-records', (), 0)
    assert 'no records is not an integrity assertion' in ' '.join(result.caveats)
    assert checked(tmp_path, loaded, monkeypatch) == result


@native
@pytest.mark.parametrize('vector', VECTORS, ids=[v['name'] for v in VECTORS])
def test_native_vectors_original_preview_two_scans_and_determinism(tmp_path, monkeypatch, vector):
    loaded = fixture(tmp_path, vector=vector)
    (tmp_path / 'unrecorded').mkdir()
    scans, calls, previews = [], [], []
    real_scan = directory_scanner.scan_directory
    real_plan = directory_audit.plan_directory_change
    def scan(root, *a, **kw):
        scans.append(Path(root))
        return real_scan(root, *a, **kw)
    def plan(loaded_arg, **kw):
        assert loaded_arg is loaded
        calls.append(kw)
        value = real_plan(loaded_arg, **kw)
        previews.append(value)
        return value
    monkeypatch.setattr(directory_scanner, 'scan_directory', scan)
    monkeypatch.setattr(directory_audit, 'plan_directory_change', plan)
    result = checked(tmp_path, loaded, monkeypatch)
    assert calls == [dict(operation='rehash', artifact_id='data')]
    assert scans == [tmp_path / 'data'] * 2
    entry = result.entries[0]
    assert entry.preview is previews[0]
    assert (result.state, result.unchanged, result.would_change, result.refused) == ('advisory', 1, 0, 0)
    p = entry.preview
    assert (p.old_sha256, p.new_sha256, p.old_size, p.new_size, p.members) == (
        vector['sha256'], vector['sha256'], vector['size_bytes'], vector['size_bytes'], len(vector['layout']))
    assert p.status == entry.status == 'unchanged'
    assert 'Preview only, not a write authorization.' in p.caveats
    assert result == checked(tmp_path, loaded, monkeypatch)
    assert len(calls) == 2 and scans == [tmp_path / 'data'] * 4
    for obj, field in ((result, 'total'), (entry, 'status'), (p, 'status')):
        with pytest.raises(dataclasses.FrozenInstanceError): setattr(obj, field, None)


@native
@pytest.mark.parametrize('refuse', [False, True])
def test_native_sorted_independent_mixed_totals(tmp_path, monkeypatch, refuse):
    loaded = fixture(tmp_path, names=('z', 'b', 'a'))
    (tmp_path / 'b/a').write_bytes(b'changed bytes')
    if refuse:
        raw = load_raw(loaded)
        raw['results'] = [dict(id='metric', run_id='run', name='private', artifact_id='z', locator='/x')]
        loaded = write_manifest(tmp_path, raw)
    calls = []
    real = directory_audit.plan_directory_change
    def plan(*a, **kw):
        calls.append(kw['artifact_id'])
        return real(*a, **kw)
    monkeypatch.setattr(directory_audit, 'plan_directory_change', plan)
    result = checked(tmp_path, loaded, monkeypatch)
    assert calls == ['a', 'b', 'z']
    assert [e.artifact_id for e in result.entries] == calls
    assert (result.unchanged, result.would_change, result.refused, result.total) == (1 if refuse else 2, 1, int(refuse), 3)
    assert result.state == ('refused' if refuse else 'advisory')
    assert result.entries[1].preview.old_sha256 != result.entries[1].preview.new_sha256
    if refuse:
        assert result.entries[2].preview is None
        assert result.entries[2].reason == 'locator-unsupported'
    assert 'Non-atomic across directories' in ' '.join(result.caveats)
    assert 'not accepting a digest' in ' '.join(result.caveats)


@native
def test_native_one_changed_and_impact_secrecy_clipping(tmp_path, monkeypatch):
    raw = raw_manifest()
    long_id = 'd' * 200
    for i in range(20):
        suffix = str(i).zfill(2) + 'x' * 180
        raw['artifacts'].append(dict(id='child'+suffix, path=long_id+'/child'+str(i)))
        raw['results'].append(dict(id='result'+suffix, run_id='run', name='SECRET NAME', value='SECRET VALUE', artifact_id=long_id))
        raw['evidence'].append(dict(id='claim'+suffix, claim='SECRET CLAIM', supports=['result'+suffix]))
    # A long ID need not be a long filesystem path on Windows.
    loaded = fixture(tmp_path, raw=raw)
    raw = load_raw(loaded)
    raw['artifacts'][-1]['id'] = long_id
    for a in raw['artifacts'][:-1]: a['path'] = 'data/' + a['path'].split('/')[-1]
    loaded = write_manifest(tmp_path, raw)
    (tmp_path / 'data/a').write_bytes(b'new content')
    result = checked(tmp_path, loaded, monkeypatch)
    e = result.entries[0]
    assert result.would_change == 1 and e.display_truncated and len(e.artifact_id) == 160
    assert e.artifact_id.endswith('...') and e.preview.display_truncated
    for impact in (e.preview.linked_results, e.preview.claims, e.preview.overlapping_files):
        assert (impact.total, impact.omitted, impact.truncated, len(impact.ids)) == (20, 4, True, 16)
        assert all(len(v) == 160 and v.endswith('...') for v in impact.ids)
    encoded = json.dumps(dataclasses.asdict(result))
    assert 'SECRET' not in encoded and 'schema_version' not in encoded


@native
def test_native_sixteen_capacity_and_seventeen_before_plan_or_scan(tmp_path, monkeypatch):
    names = tuple('d'+str(i).zfill(2) for i in range(16))
    loaded = fixture(tmp_path, names=names, vector=VECTORS[0])
    scans = []
    real = directory_scanner.scan_directory
    def scan(root, *a, **kw):
        scans.append(Path(root))
        return real(root, *a, **kw)
    monkeypatch.setattr(directory_scanner, 'scan_directory', scan)
    result = checked(tmp_path, loaded, monkeypatch)
    assert result.total == result.unchanged == 16 and len(scans) == 32
    raw = load_raw(loaded)
    raw['artifacts'].append(dict(raw['artifacts'][0], id='d16', path='unopened'))
    loaded = write_manifest(tmp_path, raw)
    def forbid(*a, **kw): raise AssertionError('capacity refusal must precede planner and scans')
    monkeypatch.setattr(directory_audit, 'plan_directory_change', forbid)
    with no_writes(monkeypatch), pytest.raises(DirectoryAuditError) as info:
        audit_recorded_directories(loaded)
    assert info.value.reason == 'capacity-exceeded' and len(scans) == 32


@pytest.mark.parametrize('case,reason', [
    ('object', 'loaded-mismatch'), ('source', 'loaded-mismatch'), ('relative', 'loaded-mismatch'),
    ('mutated-id', 'loaded-mismatch'), ('raw-dict', 'loaded-mismatch'),
    ('duplicate', 'duplicate-id'), ('cross-duplicate', 'duplicate-id'), ('bad-source-id', 'loaded-mismatch'),
])
def test_invalid_invariants_and_duplicate_ids_are_fatal_before_plan(tmp_path, monkeypatch, case, reason):
    loaded = fixture(tmp_path)
    if case == 'object': loaded = object()
    elif case == 'source': loaded = dataclasses.replace(loaded, source_bytes=b'{}')
    elif case == 'relative': loaded = dataclasses.replace(loaded, path=Path('relative.json'))
    elif case == 'mutated-id': loaded.manifest.artifacts[0].id = 'bad id'
    elif case == 'raw-dict': loaded.manifest.artifacts[0] = loaded.manifest.artifacts[0].model_dump()
    else:
        raw = load_raw(loaded)
        if case == 'duplicate': raw['artifacts'].append(copy.deepcopy(raw['artifacts'][0]))
        elif case == 'cross-duplicate': raw['artifacts'][0]['id'] = 'run'
        else:
            source = loaded.source_bytes.replace(b'"data"', b'"bad id"', 1)
            loaded = dataclasses.replace(loaded, source_bytes=source)
        if case != 'bad-source-id': loaded = write_manifest(tmp_path, raw)
    def forbid(*a, **kw): raise AssertionError('invalid loaded input reached planner')
    monkeypatch.setattr(directory_audit, 'plan_directory_change', forbid)
    with no_writes(monkeypatch), pytest.raises(DirectoryAuditError) as info:
        audit_recorded_directories(loaded)
    assert info.value.reason == reason


def test_recorded_path_validation_is_planners_refusal_without_scan(tmp_path, monkeypatch):
    loaded = fixture(tmp_path)
    raw = load_raw(loaded)
    raw['artifacts'][0]['path'] = '../outside'
    loaded = write_manifest(tmp_path, raw)
    def forbid(*a, **kw): raise AssertionError('unsafe path reached scanner')
    monkeypatch.setattr(directory_scanner, 'scan_directory', forbid)
    result = checked(tmp_path, loaded, monkeypatch)
    assert (result.state, result.refused, result.entries[0].reason) == ('refused', 1, 'invalid-path')


def test_injected_mutable_model_change_during_audit_is_fatal(tmp_path, monkeypatch):
    loaded = fixture(tmp_path)
    def refused(*a, **kw):
        loaded.manifest.artifacts.clear()
        raise DirectoryAuthoringError('scan-failed', 'refused')
    monkeypatch.setattr(directory_audit, 'plan_directory_change', refused)
    with no_writes(monkeypatch), pytest.raises(DirectoryAuditError) as info: audit_recorded_directories(loaded)
    assert info.value.reason == 'loaded-mismatch'


@pytest.mark.parametrize('when', ['before', 'after', 'missing'])
@pytest.mark.parametrize('empty', [False, True])
def test_stale_source_fatal_even_zero_and_after_last_refusal(tmp_path, monkeypatch, when, empty):
    loaded = fixture(tmp_path, names=() if empty else ('data',))
    if when == 'before': loaded.path.write_bytes(loaded.source_bytes+b' ')
    if when == 'missing': loaded.path.rename(tmp_path / 'fixture-moved.json')
    calls = []
    def refused(*a, **kw):
        calls.append(1)
        raise DirectoryAuthoringError('scan-failed', 'secret', scan_reason='unsafe')
    monkeypatch.setattr(directory_audit, 'plan_directory_change', refused)
    original = directory_audit._source
    reads = []
    def boundary(arg):
        reads.append(1)
        if when == 'after' and len(reads) == 2:
            loaded.path.write_bytes(loaded.source_bytes+b' ')
        return original(arg)
    monkeypatch.setattr(directory_audit, '_source', boundary)
    # Deliberate source mutation is outside the audit's protected operations.
    with pytest.raises(DirectoryAuditError) as info: audit_recorded_directories(loaded)
    assert info.value.reason == 'stale-source'
    assert len(calls) == int(when == 'after' and not empty)


@native
@pytest.mark.parametrize('change', ['manifest-between', 'manifest-last', 'tree-between'])
def test_native_source_and_tree_boundary_mutations(tmp_path, monkeypatch, change):
    loaded = fixture(tmp_path)
    real = directory_plan.preview_directory_proposal
    def boundary(*a):
        if change == 'manifest-between': loaded.path.write_bytes(loaded.source_bytes+b' ')
        if change == 'tree-between': (tmp_path / 'data/a').write_bytes(b'boundary mutation')
        p = real(*a)
        if change == 'manifest-last': loaded.path.write_bytes(loaded.source_bytes+b' ')
        return p
    monkeypatch.setattr(directory_plan, 'preview_directory_proposal', boundary)
    if change.startswith('manifest'):
        with pytest.raises((DirectoryApplyError, DirectoryAuditError)) as info: audit_recorded_directories(loaded)
        assert info.value.reason == 'stale-source'
    else:
        result = audit_recorded_directories(loaded)
        assert (result.state, result.refused, result.unchanged, result.would_change) == ('refused', 1, 0, 0)
        assert result.entries[0].reason == 'stale-tree'


@native
def test_native_unsafe_hardlink_tree_refusal(tmp_path, monkeypatch):
    loaded = fixture(tmp_path)
    os.link(tmp_path / 'data/a', tmp_path / 'outside-hardlink')
    result = checked(tmp_path, loaded, monkeypatch)
    assert result.state == 'refused' and result.refused == 1
    assert (result.entries[0].reason, result.entries[0].scan_reason) == ('scan-failed', 'unsafe')


@native
@pytest.mark.parametrize('phase', ['proposal', 'preview'])
def test_injected_backend_refusal_existing_error_at_planner_boundary(tmp_path, monkeypatch, phase):
    loaded = fixture(tmp_path)
    def unsupported(): raise DirectoryScanError('unsupported-platform', 'SECRET exception')
    if phase == 'proposal': monkeypatch.setattr(directory_scanner, '_native_backend', unsupported)
    else:
        real = directory_plan.preview_directory_proposal
        def boundary(*a):
            monkeypatch.setattr(directory_scanner, '_native_backend', unsupported)
            return real(*a)
        monkeypatch.setattr(directory_plan, 'preview_directory_proposal', boundary)
    errors = []
    real_plan = directory_audit.plan_directory_change
    def observe(*a, **kw):
        try: return real_plan(*a, **kw)
        except EditError as exc:
            errors.append(exc)
            raise
    monkeypatch.setattr(directory_audit, 'plan_directory_change', observe)
    result = checked(tmp_path, loaded, monkeypatch)
    e = result.entries[0]
    assert len(errors) == 1 and type(errors[0]) is (DirectoryAuthoringError if phase == 'proposal' else DirectoryApplyError)
    assert errors[0].reason == e.reason == ('scan-failed' if phase == 'proposal' else 'recheck-failed')
    assert e.scan_reason == errors[0].scan_reason == 'unsupported-platform'
    assert e.authoring_reason == (None if phase == 'proposal' else 'scan-failed')
    assert result.state == 'refused' and result.refused == 1


@pytest.mark.parametrize('reason', ['stale-source', 'loaded-mismatch', 'invalid-manifest', 'duplicate-id', 'unexpected'])
def test_injected_fatal_and_unexpected_errors_propagate_identically(tmp_path, monkeypatch, reason):
    loaded = fixture(tmp_path)
    error = RuntimeError('unexpected') if reason == 'unexpected' else DirectoryApplyError(reason, 'SECRET')
    calls = []
    def fail(*a, **kw):
        calls.append(1)
        raise error
    monkeypatch.setattr(directory_audit, 'plan_directory_change', fail)
    with no_writes(monkeypatch), pytest.raises(type(error)) as info: audit_recorded_directories(loaded)
    assert info.value is error and calls == [1]


@pytest.mark.parametrize('hostile', ['SECRET/private/path', 'x'*10000, 'unsafe\nSECRET', 'secret', '\u2603', [], None])
def test_injected_reason_sanitation_and_no_retry(tmp_path, monkeypatch, hostile):
    loaded = fixture(tmp_path)
    error = DirectoryApplyError(hostile, 'SECRET RAW JSON', authoring_reason=hostile, scan_reason=hostile)
    calls = []
    def fail(*a, **kw):
        calls.append(1)
        raise error
    monkeypatch.setattr(directory_audit, 'plan_directory_change', fail)
    result = checked(tmp_path, loaded, monkeypatch)
    e = result.entries[0]
    assert e.reason == 'refused' and e.preview is None and calls == [1]
    for reason in (e.reason, e.authoring_reason, e.scan_reason):
        assert reason is None or (reason.isascii() and len(reason) <= 80)
    assert 'SECRET' not in json.dumps(dataclasses.asdict(result))


@native
def test_injected_maximum_serialization_and_capacity_refusal(tmp_path, monkeypatch):
    loaded = fixture(tmp_path, names=tuple('d'+str(i) for i in range(16)))
    seed = directory_plan.plan_directory_change(loaded, operation='rehash', artifact_id='d0')
    impact = dataclasses.replace(seed.claims, ids=('\uffff'*157+'...',)*16, total=100, omitted=84, truncated=True)
    preview = dataclasses.replace(seed, artifact_id='x'*160, path='\uffff'*1024,
                                  linked_results=impact, claims=impact, overlapping_files=impact,
                                  old_size=10**4000)
    monkeypatch.setattr(directory_audit, 'plan_directory_change', lambda *a, **kw: preview)
    result = checked(tmp_path, loaded, monkeypatch)
    assert result.total == 16 and all(e.preview is preview for e in result.entries)
    size = len(json.dumps(dataclasses.asdict(result)).encode())
    assert 800_000 < size <= directory_audit.MAX_AUDIT_JSON_BYTES
    excessive = dataclasses.replace(preview, caveats=('x'*directory_audit.MAX_AUDIT_JSON_BYTES,))
    monkeypatch.setattr(directory_audit, 'plan_directory_change', lambda *a, **kw: excessive)
    with no_writes(monkeypatch), pytest.raises(DirectoryAuditError) as info: audit_recorded_directories(loaded)
    assert info.value.reason == 'report-capacity-exceeded'


def test_recursive_audit_import_isolation_and_public_startup(tmp_path):
    src = Path(runevidence.__file__).parent
    users = [p.relative_to(src).as_posix() for p in src.rglob('*.py')
             if p.name != 'directory_audit.py' and 'directory_audit' in p.read_text(encoding='utf-8')]
    assert sorted(users) == ['directory_present.py', 'directory_review.py']
    script = '''import sys
import runevidence, runevidence.cli, runevidence.checks, runevidence.edit
import runevidence_hydra, runevidence_mlflow
assert not any('runevidence.' + n in sys.modules for n in (
    'directory_audit', 'directory_plan', 'directory_preview', 'directory_authoring',
    'directory_apply', 'directory_cooperative_apply'))
'''
    p = subprocess.run([sys.executable, '-B', '-c', script], cwd=tmp_path, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
