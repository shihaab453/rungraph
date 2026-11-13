"""Single-call planning on disposable Windows fixtures; injections are control flow only."""

from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path
import subprocess
import sys

import pytest

import runevidence
from runevidence import directory_plan, directory_preview, directory_scanner
from runevidence.directory_apply import DirectoryApplyError
from runevidence.directory_authoring import DirectoryAuthoringError
from runevidence.directory_plan import plan_directory_change
from runevidence.directory_preview import DirectoryPreview
from runevidence.directory_scanner import DirectoryScanError
from runevidence.manifest import load_raw
from conftest import make_dir_link, remove_dir_link
from test_directory_preview import VECTORS, experiment, native, no_writes, raw_manifest, recorded, snapshot, write_manifest


def plan(loaded, operation='add', **kwargs):
    return plan_directory_change(loaded, operation=operation, artifact_id=kwargs.pop('artifact_id', 'data'),
                                 **({'path': 'data'} if operation == 'add' else {}), **kwargs)


def checked_plan(root, loaded, monkeypatch, operation='add', **kwargs):
    before = snapshot(root)
    state = copy.deepcopy((loaded.manifest.model_dump(), loaded.source_bytes))
    raws = []
    original = directory_plan.load_raw
    def raw(loaded):
        value = original(loaded)
        raws.append((value, copy.deepcopy(value)))
        return value
    with monkeypatch.context() as m:
        m.setattr(directory_plan, 'load_raw', raw)
        with no_writes(m):
            result = plan(loaded, operation, **kwargs)
    assert snapshot(root) == before
    assert (loaded.manifest.model_dump(), loaded.source_bytes) == state
    assert len(raws) == 1 and raws[0][0] == raws[0][1]
    assert type(result) is DirectoryPreview
    assert not any(hasattr(result, field) for field in ('raw', 'text', 'proposal', 'entry', 'manifest', 'save'))
    return result


@native
@pytest.mark.parametrize('vector', VECTORS, ids=[v['name'] for v in VECTORS])
@pytest.mark.parametrize('operation', ['add', 'rehash'])
def test_native_vectors_determinism_two_scans_and_no_writes(tmp_path, monkeypatch, vector, operation):
    loaded = experiment(tmp_path, vector=vector) if operation == 'add' else recorded(tmp_path, vector=vector)
    loaded.path.with_name(loaded.path.name + '.cooperative-writer.lock').write_bytes(b'disposable lock')
    scans, checks = [], []
    scan, apply = directory_scanner.scan_directory, directory_preview.apply_directory_proposal
    def observed_scan(root, *a, **kw):
        scans.append(Path(root))
        return scan(root, *a, **kw)
    def observed_apply(*a, **kw):
        checks.append(kw)
        return apply(*a, **kw)
    monkeypatch.setattr(directory_scanner, 'scan_directory', observed_scan)
    monkeypatch.setattr(directory_preview, 'apply_directory_proposal', observed_apply)
    kwargs = {'run_id': 'run', 'kind': 'dataset'} if operation == 'add' else {}
    result = checked_plan(tmp_path, loaded, monkeypatch, operation, **kwargs)
    assert scans == [tmp_path / 'data'] * 2 and checks == [{'dry_run': True}]
    assert result == checked_plan(tmp_path, loaded, monkeypatch, operation, **kwargs)
    assert scans == [tmp_path / 'data'] * 4 and checks == [{'dry_run': True}] * 2
    assert (result.new_sha256, result.new_size, result.members) == (
        vector['sha256'], vector['size_bytes'], len(vector['layout']))
    assert result.status == ('would-change' if operation == 'add' else 'unchanged')
    assert 'Preview only, not a write authorization.' in result.caveats
    with pytest.raises(dataclasses.FrozenInstanceError): result.status = 'written'


@pytest.mark.parametrize('spec', [
    {'operation': x, 'artifact_id': 'data', 'path': 'data'} for x in ('ADD', '', 'remove', None, 1, [], {})
] + [
    {'operation': 'add', 'artifact_id': x, 'path': 'data'} for x in (None, 1, True, [], {})
] + [
    {'operation': 'add', 'artifact_id': 'data'},
    *[dict(operation='add', artifact_id='data', path=1)],
    *[dict(operation='add', artifact_id='data', path='data', **{key: value})
      for key in ('run_id', 'kind') for value in (1, False, [])],
    *[dict(operation='rehash', artifact_id='data', **{key: value})
      for key in ('path', 'run_id', 'kind') for value in ('', 'data', 1)],
])
def test_invalid_requests_rejected_before_raw_or_scan(monkeypatch, spec):
    def forbid(*a, **kw): raise AssertionError('invalid spec reached raw loading or scanner')
    monkeypatch.setattr(directory_plan, 'load_raw', forbid)
    monkeypatch.setattr(directory_scanner, 'scan_directory', forbid)
    with pytest.raises(DirectoryAuthoringError) as info:
        plan_directory_change(object(), **spec)
    assert info.value.reason == 'invalid-request'


@pytest.mark.parametrize('spec', [
    {'operation': 'add', 'path': 'data'},
    {'artifact_id': 'data', 'path': 'data'},
    {'operation': 'add', 'artifact_id': 'data', 'path': 'data', 'volatile': False},
    {'operation': 'add', 'artifact_id': 'data', 'path': 'data', 'raw': {}},
    {'operation': 'rehash', 'artifact_id': 'data', 'selector': 'data'},
])
def test_missing_or_extra_selectors_are_signature_errors(monkeypatch, spec):
    def forbid(*a, **kw): raise AssertionError('signature error reached raw loading')
    monkeypatch.setattr(directory_plan, 'load_raw', forbid)
    with pytest.raises(TypeError): plan_directory_change(object(), **spec)


@pytest.mark.parametrize('case,reason', [
    ('empty-id', 'invalid-artifact'), ('bad-id', 'invalid-artifact'), ('duplicate', 'duplicate-id'),
    ('run', 'unknown-run'), ('path', 'invalid-path'), ('missing', 'not-found'),
    ('file', 'not-a-directory-artifact'), ('volatile', 'volatile-unsupported'),
    ('locator', 'locator-unsupported'), ('overlap', 'overlap'),
])
def test_existing_authoring_exclusions_precede_scan(tmp_path, monkeypatch, case, reason):
    raw = raw_manifest()
    spec = dict(operation='add', artifact_id='data', path='data')
    if case == 'empty-id': spec['artifact_id'] = ''
    elif case == 'bad-id': spec['artifact_id'] = 'bad id'
    elif case == 'duplicate': spec['artifact_id'] = 'run'
    elif case == 'run': spec['run_id'] = 'unknown'
    elif case == 'path': spec['path'] = '../outside'
    elif case in ('missing', 'file', 'volatile'):
        spec = dict(operation='rehash', artifact_id='data')
        if case != 'missing':
            entry = {'id': 'data', 'path': 'data'}
            if case == 'volatile': entry.update(artifact_type='directory', digest_format='runevidence-directory-v1', volatile=True)
            raw['artifacts'].append(entry)
    elif case == 'locator':
        raw['results'] = [{'id': 'metric', 'run_id': 'run', 'name': 'metric', 'artifact_id': 'data', 'locator': '/x'}]
    else:
        raw['artifacts'] = [{'id': 'inner', 'path': 'data/n', 'artifact_type': 'directory', 'digest_format': 'runevidence-directory-v1'}]
    loaded = experiment(tmp_path, raw)
    def forbid(*a, **kw): raise AssertionError('excluded authoring spec scanned')
    monkeypatch.setattr(directory_scanner, 'scan_directory', forbid)
    before = snapshot(tmp_path)
    with no_writes(monkeypatch), pytest.raises(DirectoryAuthoringError) as info:
        plan_directory_change(loaded, **spec)
    assert info.value.reason == reason and snapshot(tmp_path) == before


@native
@pytest.mark.parametrize('operation', ['add', 'rehash'])
@pytest.mark.parametrize('change', ['manifest-before', 'manifest-between', 'tree-between'])
def test_native_stale_source_and_between_scan_changes_fail_closed(tmp_path, monkeypatch, operation, change):
    loaded = experiment(tmp_path) if operation == 'add' else recorded(tmp_path)
    if change == 'manifest-before': loaded.path.write_bytes(loaded.source_bytes + b' ')
    scans = []
    real_scan = directory_scanner.scan_directory
    def scan(*a, **kw):
        scans.append(1)
        return real_scan(*a, **kw)
    monkeypatch.setattr(directory_scanner, 'scan_directory', scan)
    real_preview = directory_plan.preview_directory_proposal
    def boundary(loaded, proposal):
        if change == 'manifest-between': loaded.path.write_bytes(loaded.source_bytes + b' ')
        if change == 'tree-between': (tmp_path / 'data/a').write_bytes(b'changed at boundary')
        return real_preview(loaded, proposal)
    monkeypatch.setattr(directory_plan, 'preview_directory_proposal', boundary)
    # The boundary write is deliberate fixture mutation; actual scans/checker stay real.
    with pytest.raises(DirectoryApplyError) as info: plan(loaded, operation)
    assert info.value.reason == ('stale-tree' if change == 'tree-between' else 'stale-source')
    assert len(scans) == (2 if change == 'tree-between' else 1)


@native
def test_native_changed_rehash_bounded_honest_impact_and_later_mutation(tmp_path, monkeypatch):
    raw = raw_manifest()
    raw['project']['metadata'] = {'secret': 'SECRET METADATA'}
    for i in range(20):
        raw['artifacts'].append({'id': f'child{i:02}', 'path': f'data/child{i:02}', 'sha256': 'a'*64, 'size_bytes': 999})
        raw['results'].append({'id': f'result{i:02}', 'run_id': 'run', 'name': 'private', 'value': 'SECRET VALUE', 'artifact_id': 'data'})
        raw['evidence'].append({'id': f'claim{i:02}', 'claim': 'SECRET CLAIM', 'supports': [f'result{i:02}']})
    loaded = recorded(tmp_path, raw)
    (tmp_path / 'data/a').write_bytes(b'new content')
    result = checked_plan(tmp_path, loaded, monkeypatch, 'rehash', path=None, run_id=None, kind=None)
    assert result.status == 'would-change' and result.old_sha256 != result.new_sha256
    for impact in (result.linked_results, result.claims, result.overlapping_files):
        assert impact.total == 20 and len(impact.ids) == 16 and impact.omitted == 4
    assert 'SECRET' not in json.dumps(dataclasses.asdict(result))
    assert len(json.dumps(dataclasses.asdict(result))) < 12000
    for key in ('artifacts', 'results', 'evidence'):
        assert load_raw(loaded)[key][:20] == raw[key]
    (tmp_path / 'data/a').write_bytes(b'later mutation')
    later = checked_plan(tmp_path, loaded, monkeypatch, 'rehash')
    assert later.new_sha256 != result.new_sha256
    assert 'Later operations must independently revalidate the manifest and directory.' in result.caveats
    assert 'Child file artifacts keep their own checks and were not rehashed.' in result.caveats


@native
@pytest.mark.parametrize('location', ['root', 'member'])
def test_native_junction_refusal(tmp_path, monkeypatch, location):
    loaded = experiment(tmp_path)
    (tmp_path / 'outside').mkdir()
    (tmp_path / 'outside/untouched').write_bytes(b'outside')
    link = tmp_path / 'data' if location == 'root' else tmp_path / 'data/linked'
    if location == 'root': (tmp_path / 'data').rename(tmp_path / 'original-data')
    if not make_dir_link(link, tmp_path / 'outside'): pytest.skip('platform refused disposable directory link')
    try:
        before = snapshot(tmp_path)
        with no_writes(monkeypatch), pytest.raises(DirectoryAuthoringError) as info: plan(loaded)
        assert (info.value.reason, info.value.scan_reason) == ('scan-failed', 'unsafe')
        assert snapshot(tmp_path) == before
    finally: remove_dir_link(link)


@native
@pytest.mark.parametrize('phase', ['proposal', 'preview'])
def test_injected_backend_refusal_preserves_nested_reason(tmp_path, monkeypatch, phase):
    loaded = experiment(tmp_path)
    def unsupported(): raise DirectoryScanError('unsupported-platform', 'injected refusal')
    if phase == 'proposal': monkeypatch.setattr(directory_scanner, '_native_backend', unsupported)
    else:
        real = directory_plan.preview_directory_proposal
        def boundary(*a):
            monkeypatch.setattr(directory_scanner, '_native_backend', unsupported)
            return real(*a)
        monkeypatch.setattr(directory_plan, 'preview_directory_proposal', boundary)
    error = DirectoryAuthoringError if phase == 'proposal' else DirectoryApplyError
    with no_writes(monkeypatch), pytest.raises(error) as info: plan(loaded)
    assert info.value.scan_reason == 'unsupported-platform'
    assert info.value.reason == ('scan-failed' if phase == 'proposal' else 'recheck-failed')
    if phase == 'preview': assert info.value.authoring_reason == 'scan-failed'


@native
def test_injected_forged_intent_refused_by_real_checked_preview(tmp_path, monkeypatch):
    loaded = experiment(tmp_path)
    real = directory_plan.propose_add_directory
    def forged(*a, **kw): return dataclasses.replace(real(*a, **kw), add_kind='forged')
    monkeypatch.setattr(directory_plan, 'propose_add_directory', forged)
    with no_writes(monkeypatch), pytest.raises(DirectoryApplyError) as info: plan(loaded)
    assert info.value.reason == 'proposal-mismatch'


@pytest.mark.parametrize('phase', ['proposal', 'preview'])
def test_injected_errors_propagate_without_retry(monkeypatch, phase):
    error = DirectoryAuthoringError('scan-failed', 'refused', scan_reason='unsafe') if phase == 'proposal' else DirectoryApplyError('stale-tree', 'refused')
    calls = []
    monkeypatch.setattr(directory_plan, 'load_raw', lambda _: {})
    def proposal(*a, **kw):
        calls.append('proposal')
        if phase == 'proposal': raise error
        return object()
    def preview(*a):
        calls.append('preview')
        raise error
    monkeypatch.setattr(directory_plan, 'propose_add_directory', proposal)
    monkeypatch.setattr(directory_plan, 'preview_directory_proposal', preview)
    with pytest.raises(type(error)) as info: plan(object())
    assert info.value is error and calls == (['proposal'] if phase == 'proposal' else ['proposal', 'preview'])


def test_recursive_exact_importers_and_fresh_public_startup(tmp_path):
    src = Path(runevidence.__file__).parent
    users = [p.relative_to(src).as_posix() for p in src.rglob('*.py')
             if p.name != 'directory_plan.py' and 'directory_plan' in p.read_text(encoding='utf-8')]
    assert sorted(users) == ['directory_audit.py', 'directory_batch.py']
    script = '''import sys
import runevidence, runevidence.cli, runevidence.checks, runevidence.edit
import runevidence_hydra, runevidence_mlflow
for name in ('directory_plan', 'directory_preview', 'directory_authoring', 'directory_apply', 'directory_cooperative_apply'):
    assert 'runevidence.' + name not in sys.modules, name
'''
    result = subprocess.run([sys.executable, '-B', '-c', script], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
