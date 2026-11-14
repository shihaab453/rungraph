"""Checked previews on disposable synthetic fixtures only.

Native tests use the Windows reader. Injected tests prove control flow only;
they do not certify native security, permissions or hostile-writer behavior.
"""

from __future__ import annotations

import builtins
import copy
import dataclasses
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from contextlib import contextmanager

import pytest

from conftest import make_dir_link, native_test, remove_dir_link
import runevidence
from runevidence import directory_preview, directory_scanner
from runevidence.directory_apply import DirectoryApplyError, DirectoryApplyResult
from runevidence.directory_authoring import DirectoryAuthoringError, propose_add_directory, propose_rehash_directory
from runevidence.directory_preview import preview_directory_proposal
from runevidence.directory_scanner import DirectoryScanError
from runevidence.manifest import dump_raw, load_manifest, load_raw
from runevidence.models import MANIFEST_FILENAME

VECTORS = json.loads((Path(__file__).parent / 'data/directory_digest_vectors.json').read_bytes())['vectors']
native = native_test


def raw_manifest():
    return {'schema_version': '0.1', 'project': {'id': 'demo', 'name': 'Synthetic'},
            'runs': [{'id': 'run'}], 'artifacts': [], 'results': [], 'evidence': []}


def write_manifest(root, raw):
    (root / MANIFEST_FILENAME).write_text(dump_raw(raw), encoding='utf-8', newline='\n')
    return load_manifest(root)


def experiment(root, raw=None, vector=None):
    (root / 'data').mkdir()
    for item in (VECTORS[1] if vector is None else vector)['layout']:
        p = root / 'data' / item['path']
        if item['type'] == 'D': p.mkdir()
        else: p.write_bytes(bytes.fromhex(item['content_hex']))
    return write_manifest(root, raw_manifest() if raw is None else raw)


def add(loaded, **kwargs):
    return propose_add_directory(loaded, load_raw(loaded), path='data', artifact_id=kwargs.pop('artifact_id', 'data'), **kwargs)


def rehash(loaded):
    return propose_rehash_directory(loaded, load_raw(loaded), artifact_id='data')


def recorded(root, raw=None, vector=None):
    loaded = experiment(root, raw, vector)
    return write_manifest(root, add(loaded).raw)


def snapshot(root):
    # Do not follow synthetic junctions. All ordinary contents and mtimes count.
    out = []
    def visit(p):
        st = p.lstat()
        rel = p.relative_to(root).as_posix()
        if getattr(st, 'st_file_attributes', 0) & 0x400:
            out.append((rel, os.readlink(p), st.st_mtime_ns))
        elif p.is_dir():
            out.append((rel, None, st.st_mtime_ns))
            for child in sorted(p.iterdir()): visit(child)
        else: out.append((rel, p.read_bytes(), st.st_mtime_ns))
    visit(root)
    return out


@contextmanager
def no_writes(monkeypatch):
    """Fail on persistence, any lock access, or Python file creation.

    The native scanner's necessary read handles remain real and unmocked.
    Snapshots independently compare contents/mtimes outside this guard.
    """
    calls = []
    def forbid(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError('preview attempted persistence or lock I/O')
    def is_lock(path):
        return not isinstance(path, int) and str(path).endswith('.cooperative-writer.lock')
    with monkeypatch.context() as m:
        for name in ('runevidence.directory_apply.save_raw', 'runevidence.manifest.save_raw',
                     'runevidence.manifest.save_manifest', 'runevidence.manifest._write',
                     'runevidence.directory_cooperative_apply.apply_directory_proposal_cooperatively',
                     'runevidence.directory_cooperative_apply.lock_path_for',
                     'tempfile.NamedTemporaryFile', 'tempfile.mkstemp',
                     'os.replace', 'os.rename', 'os.unlink', 'os.remove', 'os.mkdir', 'os.makedirs', 'os.rmdir'):
            m.setattr(name, forbid)
        for module in (builtins, io):
            real = module.open
            def opened(file, mode='r', *args, _real=real, **kwargs):
                if is_lock(file) or any(c in mode for c in 'wax+'): return forbid(file, mode)
                return _real(file, mode, *args, **kwargs)
            m.setattr(module, 'open', opened)
        real_open = os.open
        def os_open(path, flags, *args, **kwargs):
            if is_lock(path) or flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
                return forbid(path, flags)
            return real_open(path, flags, *args, **kwargs)
        m.setattr(os, 'open', os_open)
        for name in ('stat', 'lstat'):
            real = getattr(os, name)
            def stat(path, *args, _real=real, **kwargs):
                if is_lock(path): return forbid(path)
                return _real(path, *args, **kwargs)
            m.setattr(os, name, stat)
        yield calls
    assert calls == []


def checked_preview(root, loaded, proposal, monkeypatch):
    before = snapshot(root)
    state = copy.deepcopy((vars(proposal), loaded.manifest.model_dump(), loaded.source_bytes))
    with no_writes(monkeypatch):
        preview = preview_directory_proposal(loaded, proposal)
    assert snapshot(root) == before
    assert (vars(proposal), loaded.manifest.model_dump(), loaded.source_bytes) == state
    assert not hasattr(preview, 'raw') and not hasattr(preview, 'text') and not hasattr(preview, 'proposal')
    return preview


@native
@pytest.mark.parametrize('vector', VECTORS, ids=[v['name'] for v in VECTORS])
@pytest.mark.parametrize('operation', ['add', 'rehash'])
def test_native_pinned_vectors_and_unchanged_are_read_only(tmp_path, monkeypatch, vector, operation):
    loaded = experiment(tmp_path, vector=vector) if operation == 'add' else recorded(tmp_path, vector=vector)
    proposal = add(loaded) if operation == 'add' else rehash(loaded)
    # An existing synthetic lock is ignored, never inspected, opened or removed.
    lock = loaded.path.with_name(loaded.path.name + '.cooperative-writer.lock')
    lock.write_bytes(b'existing disposable lock')
    scans = []
    real = directory_scanner.scan_directory
    def scan(root, *args, **kwargs):
        scans.append(Path(root))
        return real(root, *args, **kwargs)
    monkeypatch.setattr(directory_scanner, 'scan_directory', scan)
    p = checked_preview(tmp_path, loaded, proposal, monkeypatch)
    assert p == checked_preview(tmp_path, loaded, proposal, monkeypatch)
    assert scans == [tmp_path / 'data', tmp_path / 'data']
    assert (p.new_sha256, p.new_size, p.members) == (vector['sha256'], vector['size_bytes'], len(vector['layout']))
    assert (p.operation, p.artifact_id, p.path) == (operation, 'data', 'data')
    assert p.status == ('would-change' if operation == 'add' else 'unchanged')
    assert (p.old_sha256, p.old_size) == ((None, None) if operation == 'add' else (p.new_sha256, p.new_size))
    assert not p.display_truncated
    assert 'Preview only, not a write authorization.' in p.caveats
    assert 'Cooperative quiet-tree, no CAS/ACL guarantee.' in p.caveats
    with pytest.raises(dataclasses.FrozenInstanceError): p.status = 'written'


@native
def test_native_changed_rehash_has_new_digest_without_updating_child_or_values(tmp_path, monkeypatch):
    raw = raw_manifest()
    raw['artifacts'] = [{'id': 'child', 'path': 'data/a', 'sha256': 'a' * 64, 'size_bytes': 999}]
    raw['results'] = [{'id': 'count', 'run_id': 'run', 'name': 'count', 'value': 'UNREAD-RESULT-VALUE', 'artifact_id': 'data'}]
    raw['evidence'] = [{'id': 'direct', 'claim': 'PRIVATE CLAIM', 'supports': ['data']},
                       {'id': 'indirect', 'claim': 'PRIVATE CLAIM', 'contradicts': ['count']}]
    loaded = experiment(tmp_path, raw)
    added = checked_preview(tmp_path, loaded, add(loaded), monkeypatch)
    assert added.status == 'would-change'
    # Fixture authoring only, never the preview, records the synthetic directory.
    loaded = write_manifest(tmp_path, add(loaded).raw)
    (tmp_path / 'data/a').write_bytes(b'changed content')
    proposal = rehash(loaded)
    p = checked_preview(tmp_path, loaded, proposal, monkeypatch)
    assert p.status == 'would-change' and p.new_sha256 != p.old_sha256
    assert p.old_sha256 == added.new_sha256 and p.new_size == len(b'changed content')
    for preview in (added, p):
        assert preview.linked_results.ids == ('count',)
        assert preview.claims.ids == ('direct', 'indirect')
        assert preview.overlapping_files.ids == ('child',)
        assert all(x.total == len(x.ids) and x.omitted == 0 for x in
                   (preview.linked_results, preview.claims, preview.overlapping_files))
        assert 'PRIVATE CLAIM' not in repr(preview) and 'UNREAD-RESULT-VALUE' not in repr(preview)
    assert load_raw(loaded)['artifacts'][0] == raw['artifacts'][0]
    assert load_raw(loaded)['results'] == raw['results']
    assert load_raw(loaded)['evidence'] == raw['evidence']


@native
@pytest.mark.parametrize('operation', ['add', 'rehash'])
@pytest.mark.parametrize('change', ['manifest', 'content', 'new-member', 'removed-member'])
def test_native_stale_state_fails_closed(tmp_path, monkeypatch, operation, change):
    loaded = experiment(tmp_path) if operation == 'add' else recorded(tmp_path)
    proposal = add(loaded) if operation == 'add' else rehash(loaded)
    if change == 'manifest': loaded.path.write_bytes(loaded.source_bytes + b' ')
    elif change == 'content': (tmp_path / 'data/a').write_bytes(b'changed')
    elif change == 'new-member': (tmp_path / 'data/new').write_bytes(b'new')
    else: (tmp_path / 'data/a').unlink()
    before = snapshot(tmp_path)
    with no_writes(monkeypatch), pytest.raises(DirectoryApplyError) as info:
        preview_directory_proposal(loaded, proposal)
    assert info.value.reason == ('stale-source' if change == 'manifest' else 'stale-tree')
    assert snapshot(tmp_path) == before


@native
@pytest.mark.parametrize('field,value', [
    ('changed', False), ('members', 900), ('new_sha256', '0' * 64),
    ('claims', ('forged-approval',)), ('linked_results', ('forged',)),
    ('overlapping_files', ('forged',)), ('notes', ('Approved to write',)),
    ('add_kind', 'forged-kind'), ('path', '../outside'),
])
def test_native_forged_proposal_fields_are_refused(tmp_path, monkeypatch, field, value):
    loaded = experiment(tmp_path)
    proposal = dataclasses.replace(add(loaded), **{field: value})
    before = snapshot(tmp_path)
    with no_writes(monkeypatch), pytest.raises(DirectoryApplyError) as info:
        preview_directory_proposal(loaded, proposal)
    assert info.value.reason in ('proposal-mismatch', 'stale-tree')
    assert snapshot(tmp_path) == before


@native
def test_native_mutable_entry_cannot_change_creation_intent(tmp_path, monkeypatch):
    loaded = experiment(tmp_path)
    proposal = add(loaded, kind='dataset', run_id='run')
    proposal.entry['kind'] = proposal.raw['artifacts'][-1]['kind'] = 'other'
    with no_writes(monkeypatch), pytest.raises(DirectoryApplyError) as info:
        preview_directory_proposal(loaded, proposal)
    assert info.value.reason == 'proposal-mismatch'


@native
@pytest.mark.parametrize('location', ['root', 'member'])
def test_native_junction_and_linked_root_are_refused(tmp_path, monkeypatch, location):
    loaded = experiment(tmp_path)
    proposal = add(loaded)
    (tmp_path / 'outside').mkdir()
    (tmp_path / 'outside/untouched').write_bytes(b'outside')
    link = tmp_path / 'data' if location == 'root' else tmp_path / 'data/linked'
    if location == 'root': (tmp_path / 'data').rename(tmp_path / 'original-data')
    if not make_dir_link(link, tmp_path / 'outside'):
        pytest.skip('platform refused disposable directory link')
    try:
        before = snapshot(tmp_path)
        with no_writes(monkeypatch), pytest.raises(DirectoryApplyError) as info:
            preview_directory_proposal(loaded, proposal)
        assert (info.value.reason, info.value.authoring_reason, info.value.scan_reason) == (
            'recheck-failed', 'scan-failed', 'unsafe')
        assert snapshot(tmp_path) == before
    finally:
        remove_dir_link(link)


@native
@pytest.mark.parametrize('refusal', ['locator', 'overlap'])
def test_native_changed_locator_or_overlap_never_becomes_success(tmp_path, monkeypatch, refusal):
    loaded = experiment(tmp_path)
    proposal = add(loaded)
    raw = load_raw(loaded)
    if refusal == 'locator':
        raw['results'] = [{'id': 'metric', 'run_id': 'run', 'name': 'metric', 'artifact_id': 'data', 'locator': '/x'}]
    else:
        raw['artifacts'] = [{'id': 'inner', 'path': 'data/n', 'artifact_type': 'directory',
                             'digest_format': 'runevidence-directory-v1'}]
    reloaded = write_manifest(tmp_path, raw)
    with no_writes(monkeypatch), pytest.raises(DirectoryApplyError) as info:
        preview_directory_proposal(loaded, proposal)
    assert info.value.reason == 'stale-source'
    with no_writes(monkeypatch), pytest.raises(DirectoryApplyError) as info:
        preview_directory_proposal(reloaded, proposal)
    assert info.value.reason == 'proposal-mismatch'
    with no_writes(monkeypatch), pytest.raises(DirectoryAuthoringError) as info:
        add(reloaded)
    assert info.value.reason == ('locator-unsupported' if refusal == 'locator' else 'overlap')


@native
def test_injected_unsupported_backend_retains_classification(tmp_path, monkeypatch):
    loaded = experiment(tmp_path)
    proposal = add(loaded)
    def unsupported(): raise DirectoryScanError('unsupported-platform', 'injected backend refusal')
    monkeypatch.setattr(directory_scanner, '_native_backend', unsupported)
    with no_writes(monkeypatch), pytest.raises(DirectoryApplyError) as info:
        preview_directory_proposal(loaded, proposal)
    assert (info.value.reason, info.value.authoring_reason, info.value.scan_reason) == (
        'recheck-failed', 'scan-failed', 'unsupported-platform')


@pytest.mark.parametrize('error', [DirectoryApplyError('stale-source', 'refused'),
                                 DirectoryAuthoringError('locator-unsupported', 'refused')])
def test_injected_errors_propagate_as_original_objects(monkeypatch, error):
    calls = []
    def fail(loaded, proposal, *, dry_run):
        calls.append((loaded, proposal, dry_run))
        raise error
    monkeypatch.setattr(directory_preview, 'apply_directory_proposal', fail)
    loaded, proposal = object(), object()
    with pytest.raises(type(error)) as info:
        preview_directory_proposal(loaded, proposal)
    assert info.value is error and calls == [(loaded, proposal, True)]


@native
def test_native_bounded_impact_ids_and_no_secret_echo(tmp_path, monkeypatch):
    raw = raw_manifest()
    raw['project']['metadata'] = {'secret': 'DO NOT ECHO THIS MANIFEST'}
    for i in range(40):
        raw['artifacts'].append({'id': f'child{i:02}', 'path': f'data/child{i:02}'})
        raw['results'].append({'id': f'result{i:02}', 'run_id': 'run', 'name': 'private',
                               'value': 'SECRET VALUE', 'artifact_id': 'data'})
        raw['evidence'].append({'id': f'claim{i:02}', 'claim': 'SECRET CLAIM', 'supports': [f'result{i:02}']})
    loaded = experiment(tmp_path, raw)
    p = checked_preview(tmp_path, loaded, add(loaded), monkeypatch)
    for impact in (p.linked_results, p.claims, p.overlapping_files):
        assert impact.total == 40 and impact.omitted == 24 and len(impact.ids) == 16
        assert impact.ids == tuple(sorted(impact.ids)) and not impact.truncated
        assert all(len(v) <= 160 for v in impact.ids)
    text = json.dumps(dataclasses.asdict(p))
    assert len(text) < 12000
    assert 'SECRET' not in text and 'DO NOT ECHO' not in text


@native
def test_native_long_display_labels_are_explicitly_truncated(tmp_path, monkeypatch):
    raw = raw_manifest()
    long_id = 'x' * 10000
    raw['results'] = [{'id': long_id, 'run_id': 'run', 'name': 'private', 'artifact_id': long_id + 'a'}]
    loaded = experiment(tmp_path, raw)
    p = checked_preview(tmp_path, loaded, add(loaded, artifact_id=long_id + 'a'), monkeypatch)
    assert p.display_truncated and len(p.artifact_id) == 160 and p.artifact_id.endswith('...')
    assert p.linked_results.total == 1 and p.linked_results.truncated
    assert len(p.linked_results.ids[0]) == 160
    assert long_id not in repr(p)


@native
def test_injected_unexpected_write_result_is_refused(tmp_path, monkeypatch):
    loaded = experiment(tmp_path)
    proposal = add(loaded)
    result = DirectoryApplyResult('written', 'add', 'data', 'data', loaded.path,
                                  None, proposal.new_sha256, None, proposal.new_size, proposal.members, 'SECRET', ())
    monkeypatch.setattr(directory_preview, 'apply_directory_proposal', lambda *a, **kw: result)
    with pytest.raises(DirectoryApplyError) as info: preview_directory_proposal(loaded, proposal)
    assert info.value.reason == 'invalid-request' and 'SECRET' not in str(info.value)


def test_only_precise_internal_source_importers_and_no_public_imports(tmp_path):
    src = Path(runevidence.__file__).parent
    users = [p.relative_to(src).as_posix() for p in src.rglob('*.py')
             if p.name != 'directory_preview.py' and 'directory_preview' in p.read_text(encoding='utf-8')]
    assert sorted(users) == ['directory_audit.py', 'directory_batch.py', 'directory_plan.py']
    script = """import sys
import runevidence, runevidence.cli, runevidence.checks, runevidence.edit
import runevidence_hydra, runevidence_mlflow
assert 'runevidence.directory_preview' not in sys.modules
assert 'runevidence.directory_apply' not in sys.modules
assert 'runevidence.directory_authoring' not in sys.modules
"""
    result = subprocess.run([sys.executable, '-B', '-c', script], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
