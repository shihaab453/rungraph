"""Synthetic fixtures only; native scans stay real unless explicitly injected.

Injected boundary/exception/capacity tests establish control flow, not native
security. Fixture writes and deliberate timing mutations are test-only work.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from conftest import make_dir_link, remove_dir_link
import runevidence
from runevidence import directory_batch, directory_plan, directory_scanner
from runevidence.directory_apply import DirectoryApplyError
from runevidence.directory_authoring import DirectoryAuthoringError
from runevidence.directory_batch import DirectoryBatchError, DirectoryChangeRequest as Request, review_directory_changes
from runevidence.directory_scanner import DirectoryScanError
from runevidence.edit import EditError
from runevidence.manifest import load_raw
from test_directory_preview import VECTORS, native, no_writes, raw_manifest, snapshot, write_manifest


def fixture(root, recorded=(), added=(), vector=None, raw=None):
    vector = VECTORS[1] if vector is None else vector
    raw = raw_manifest() if raw is None else copy.deepcopy(raw)
    for name in (*recorded, *added):
        folder = root / name
        folder.mkdir(parents=True, exist_ok=True)
        for item in vector['layout']:
            p = folder / item['path']
            if item['type'] == 'D': p.mkdir(exist_ok=True)
            else: p.write_bytes(bytes.fromhex(item['content_hex']))
        if name in recorded:
            raw['artifacts'].append(dict(id=name, path=name, artifact_type='directory',
                                        digest_format='runevidence-directory-v1',
                                        sha256=vector['sha256'], size_bytes=vector['size_bytes']))
    return write_manifest(root, raw)


def checked(root, loaded, requests, monkeypatch, error=None):
    lock = loaded.path.with_name(loaded.path.name + '.cooperative-writer.lock')
    if not lock.exists(): lock.write_bytes(b'disposable synthetic lock: do not inspect')
    before = snapshot(root)
    model = copy.deepcopy(loaded.manifest.model_dump())
    with no_writes(monkeypatch):
        if error:
            with pytest.raises(error) as info: review_directory_changes(loaded, requests)
            result = info.value
        else:
            result = review_directory_changes(loaded, requests)
    assert snapshot(root) == before
    assert loaded.manifest.model_dump() == model
    if not error:
        assert result.total == len(requests) == len(result.previews) == result.adds + result.rehashes
        assert result.total == result.would_change + result.unchanged
        assert result.adds == sum(p.operation == 'add' for p in result.previews)
        assert result.would_change == sum(p.status == 'would-change' for p in result.previews)
        assert len(json.dumps(dataclasses.asdict(result)).encode()) <= directory_batch.MAX_BATCH_JSON_BYTES
    return result


def observe(monkeypatch, loaded):
    calls, scans, previews = [], [], []
    real_plan, real_scan = directory_batch.plan_directory_change, directory_scanner.scan_directory
    def plan(arg, **kw):
        assert arg is loaded
        calls.append(kw)
        result = real_plan(arg, **kw)
        previews.append(result)
        return result
    def scan(root, *a, **kw):
        scans.append(Path(root))
        return real_scan(root, *a, **kw)
    monkeypatch.setattr(directory_batch, 'plan_directory_change', plan)
    monkeypatch.setattr(directory_scanner, 'scan_directory', scan)
    return calls, scans, previews


def test_empty_has_two_source_model_checks_no_planner_or_scan(tmp_path, monkeypatch):
    loaded = fixture(tmp_path)
    reads, models = [], []
    real_read, real_raw = Path.read_bytes, directory_batch.load_raw
    def read(p):
        if p == loaded.path: reads.append(p)
        return real_read(p)
    def raw(arg):
        models.append(arg)
        return real_raw(arg)
    def forbid(*a, **kw): raise AssertionError('empty review reached planner or scanner')
    with monkeypatch.context() as m:
        m.setattr(Path, 'read_bytes', read)
        m.setattr(directory_batch, 'load_raw', raw)
        m.setattr(directory_batch, 'plan_directory_change', forbid)
        m.setattr(directory_scanner, 'scan_directory', forbid)
        result = review_directory_changes(loaded, ())
    assert reads == [loaded.path] * 2 and models == [loaded] * 2
    assert (result.state, result.previews, result.total) == ('no-requests', (), 0)
    assert result == checked(tmp_path, loaded, (), monkeypatch)
    text = ' '.join(result.caveats)
    for phrase in ('Read-only advisory', 'independent per-request', 'same loaded baseline',
                   'Non-atomic', 'quiet-tree', 'revalidate', 'CAS', 'custom-ACL',
                   'hostile-writer', 'ABA', 'No persisted write authorization', 'No requests'):
        assert phrase in text


@native
@pytest.mark.parametrize('vector', VECTORS, ids=[v['name'] for v in VECTORS])
@pytest.mark.parametrize('operation', ['add', 'rehash'])
def test_native_vectors_identity_exact_planner_and_two_scans(tmp_path, monkeypatch, vector, operation):
    loaded = fixture(tmp_path, recorded=('data',) if operation == 'rehash' else (),
                     added=('data',) if operation == 'add' else (), vector=vector)
    request = Request(operation, 'data', 'data' if operation == 'add' else None)
    calls, scans, previews = observe(monkeypatch, loaded)
    result = checked(tmp_path, loaded, (request,), monkeypatch)
    assert calls == [dataclasses.asdict(request)]
    assert scans == [tmp_path / 'data'] * 2
    p = result.previews[0]
    assert p is previews[0]
    assert (p.new_sha256, p.new_size, p.members) == (vector['sha256'], vector['size_bytes'], len(vector['layout']))
    assert p.status == ('would-change' if operation == 'add' else 'unchanged')
    assert 'Preview only, not a write authorization.' in p.caveats
    assert result == checked(tmp_path, loaded, (request,), monkeypatch)
    assert scans == [tmp_path / 'data'] * 4 and len(calls) == 2
    for obj, field in ((request, 'operation'), (result, 'total'), (p, 'status')):
        with pytest.raises(dataclasses.FrozenInstanceError): setattr(obj, field, None)


@native
def test_native_mixed_order_independent_baseline_and_changed_rehash(tmp_path, monkeypatch):
    loaded = fixture(tmp_path, recorded=('z', 'a'), added=('m',))
    (tmp_path / 'a/a').write_bytes(b'changed bytes')
    requests = (Request('rehash', 'z'), Request('add', 'm', 'm', 'run', 'dataset'), Request('rehash', 'a'))
    calls, scans, previews = observe(monkeypatch, loaded)
    result = checked(tmp_path, loaded, requests, monkeypatch)
    assert calls == [dataclasses.asdict(r) for r in requests]
    assert scans == [tmp_path / name for name in ('z', 'z', 'm', 'm', 'a', 'a')]
    assert all(p is original for p, original in zip(result.previews, previews))
    assert [p.artifact_id for p in result.previews] == ['z', 'm', 'a']
    assert (result.total, result.adds, result.rehashes, result.would_change, result.unchanged) == (3, 1, 2, 2, 1)
    assert result.previews[2].old_sha256 != result.previews[2].new_sha256
    assert result.previews[2].new_size == len(b'changed bytes')
    assert [a.id for a in loaded.manifest.artifacts] == ['z', 'a']


@native
@pytest.mark.parametrize('paths,overlap', [
    (('data', 'data'), True), (('data', 'data/n'), True),
    (('data/n', 'data'), True), (('data', 'DATA'), True), (('data/a', 'data/ab'), False),
])
def test_native_cross_add_checked_path_collisions(tmp_path, monkeypatch, paths, overlap):
    loaded = fixture(tmp_path, added=paths, vector=VECTORS[0])
    requests = tuple(Request('add', 'id'+str(i), p) for i, p in enumerate(paths))
    calls, scans, previews = observe(monkeypatch, loaded)
    alias = os.name == 'nt' and paths == ('data', 'DATA')
    posix_case_pair = os.name != 'nt' and paths == ('data', 'DATA')
    error = DirectoryAuthoringError if alias else DirectoryBatchError if (overlap and not posix_case_pair) else None
    result = checked(tmp_path, loaded, requests, monkeypatch, error)
    assert len(calls) == 2 and len(scans) == (3 if alias else 4) and len(previews) == (1 if alias else 2)
    if alias: assert (result.reason, result.scan_reason) == ('scan-failed', 'unsafe')
    elif posix_case_pair:
        # POSIX is case-sensitive: 'data' and 'DATA' are distinct folders and
        # the two adds do not overlap. This asserts the actual POSIX behavior;
        # it does not assume Windows case-insensitive semantics.
        assert result.total == result.adds == 2
        assert [p.status for p in result.previews] == ['would-change', 'would-change']
    elif overlap: assert result.reason == 'batch-overlap'
    else: assert result.total == result.adds == 2


class RequestSubclass(Request):
    pass


@pytest.mark.parametrize('requests,reason', [
    ([], 'invalid-request'), (({},), 'invalid-request'), ((object(),), 'invalid-request'),
    ((RequestSubclass('add', 'x', 'data'),), 'invalid-request'),
    ((Request('bad', 'x', 'data'),), 'invalid-request'), ((Request(1, 'x'),), 'invalid-request'),
    ((Request('add', [], 'data'),), 'invalid-request'), ((Request('add', 'x'),), 'invalid-request'),
    ((Request('add', 'x', 1),), 'invalid-request'), ((Request('add', 'x', 'data', 1),), 'invalid-request'),
    ((Request('add', 'x', 'data', kind=[]),), 'invalid-request'),
    ((Request('rehash', 'x', 'data'),), 'invalid-request'),
    ((Request('rehash', 'x', run_id='run'),), 'invalid-request'),
    ((Request('rehash', 'x', kind='data'),), 'invalid-request'),
    (tuple(Request('add', 'x'+str(i), 'data') for i in range(9)), 'capacity-exceeded'),
    ((Request('add', 'x', 'data'), Request('rehash', 'x')), 'duplicate-id'),
    ((Request('add', 'x', 'data'), Request('add', 'y', 'other'), Request('add', 'x', 'third')), 'duplicate-id'),
])
def test_shape_count_duplicate_refusal_before_any_load_or_scan(monkeypatch, requests, reason):
    def forbid(*a, **kw): raise AssertionError('invalid request reached manifest or scan')
    monkeypatch.setattr(directory_batch, 'load_raw', forbid)
    monkeypatch.setattr(Path, 'read_bytes', forbid)
    monkeypatch.setattr(directory_scanner, 'scan_directory', forbid)
    monkeypatch.setattr(directory_batch, 'plan_directory_change', forbid)
    with pytest.raises(DirectoryBatchError) as info: review_directory_changes(object(), requests)
    assert isinstance(info.value, EditError) and info.value.reason == reason


def test_signature_refuses_unknown_keywords_before_work(monkeypatch):
    def forbid(*a, **kw): raise AssertionError('signature error reached load')
    monkeypatch.setattr(directory_batch, 'load_raw', forbid)
    with pytest.raises(TypeError): Request('add', 'x', 'data', volatile=False)
    with pytest.raises(TypeError): review_directory_changes(object(), (), raw={})
    with pytest.raises(TypeError): Request('add')


@native
def test_native_eight_distinct_adds_bounded_and_exact(tmp_path, monkeypatch):
    names = tuple('d'+str(i) for i in range(8))
    loaded = fixture(tmp_path, added=names, vector=VECTORS[0])
    calls, scans, previews = observe(monkeypatch, loaded)
    result = checked(tmp_path, loaded, tuple(Request('add', n, n) for n in names), monkeypatch)
    assert (result.total, result.adds, result.would_change) == (8, 8, 8)
    assert len(calls) == 8 and len(scans) == 16
    assert all(p is original for p, original in zip(result.previews, previews))


@pytest.mark.parametrize('case,reason', [
    ('id', 'invalid-artifact'), ('namespace', 'duplicate-id'), ('run', 'unknown-run'),
    ('path', 'invalid-path'), ('missing', 'not-found'), ('file', 'not-a-directory-artifact'),
    ('volatile', 'volatile-unsupported'), ('locator', 'locator-unsupported'),
    ('equal', 'path-collision'), ('nested', 'overlap'),
])
def test_existing_planner_refusals_before_scan(tmp_path, monkeypatch, case, reason):
    raw = raw_manifest()
    request = Request('add', 'data', 'data')
    if case == 'id': request = dataclasses.replace(request, artifact_id='bad id')
    elif case == 'namespace': request = dataclasses.replace(request, artifact_id='run')
    elif case == 'run': request = dataclasses.replace(request, run_id='unknown')
    elif case == 'path': request = dataclasses.replace(request, path='../outside')
    elif case in ('missing', 'file', 'volatile'):
        request = Request('rehash', 'data')
        if case != 'missing':
            entry = dict(id='data', path='data')
            if case == 'volatile': entry.update(artifact_type='directory', digest_format='runevidence-directory-v1', volatile=True)
            raw['artifacts'].append(entry)
    elif case == 'locator':
        raw['results'] = [dict(id='metric', run_id='run', name='secret', artifact_id='data', locator='/x')]
    else:
        raw['artifacts'] = [dict(id='other', path='data' if case == 'equal' else 'data/n',
                                 artifact_type='directory', digest_format='runevidence-directory-v1')]
    loaded = fixture(tmp_path, raw=raw)
    def forbid(*a, **kw): raise AssertionError('excluded request reached scanner')
    monkeypatch.setattr(directory_scanner, 'scan_directory', forbid)
    calls, errors = [], []
    real = directory_batch.plan_directory_change
    def plan(*a, **kw):
        calls.append(kw)
        try: return real(*a, **kw)
        except EditError as exc:
            errors.append(exc)
            raise
    monkeypatch.setattr(directory_batch, 'plan_directory_change', plan)
    error = checked(tmp_path, loaded, (request,), monkeypatch, DirectoryAuthoringError)
    assert error is errors[0] and error.reason == reason and len(calls) == 1


@pytest.mark.parametrize('empty', [False, True])
@pytest.mark.parametrize('case', ['object', 'source', 'relative', 'model', 'dict', 'invalid-json'])
def test_loaded_identity_model_source_guard_even_empty(tmp_path, monkeypatch, empty, case):
    loaded = fixture(tmp_path)
    if case == 'object': loaded = object()
    elif case == 'source': loaded = dataclasses.replace(loaded, source_bytes=bytearray(loaded.source_bytes))
    elif case == 'relative': loaded = dataclasses.replace(loaded, path=Path('relative.json'))
    elif case == 'model': loaded.manifest.project.name = 'mutated'
    elif case == 'dict': loaded = dataclasses.replace(loaded, manifest=loaded.manifest.model_dump())
    else: loaded = dataclasses.replace(loaded, source_bytes=b'{')
    def forbid(*a, **kw): raise AssertionError('invalid loaded state reached planner')
    monkeypatch.setattr(directory_batch, 'plan_directory_change', forbid)
    with no_writes(monkeypatch), pytest.raises(DirectoryBatchError) as info:
        review_directory_changes(loaded, () if empty else (Request('add', 'x', 'data'),))
    assert info.value.reason == 'loaded-mismatch'


@pytest.mark.parametrize('empty', [False, True])
@pytest.mark.parametrize('when', ['before', 'missing', 'final-read', 'final-model'])
def test_injected_final_guards_and_initial_staleness(tmp_path, monkeypatch, empty, when):
    loaded = fixture(tmp_path)
    if when == 'before': loaded.path.write_bytes(loaded.source_bytes+b' ')
    elif when == 'missing': loaded.path.rename(tmp_path / 'moved.json')
    calls, reads, models = [], [], []
    # A nonempty fake result is only for end-guard control flow.
    from runevidence.directory_preview import DirectoryPreview, DirectoryPreviewIds
    ids = DirectoryPreviewIds((), 0, 0, False)
    preview = DirectoryPreview('add', 'would-change', 'x', 'data', False, None, 'a'*64, None, 0, 0, ids, ids, ids, ())
    def plan(*a, **kw):
        calls.append(1)
        return preview
    original_source, original_model = directory_batch._source, directory_batch._model
    def source(arg):
        reads.append(1)
        if when == 'final-read' and len(reads) == 2: loaded.path.write_bytes(loaded.source_bytes+b' ')
        return original_source(arg)
    def model(arg):
        models.append(1)
        if when == 'final-model' and len(models) == 2: loaded.manifest.project.name = 'mutated'
        return original_model(arg)
    monkeypatch.setattr(directory_batch, 'plan_directory_change', plan)
    monkeypatch.setattr(directory_batch, '_source', source)
    monkeypatch.setattr(directory_batch, '_model', model)
    # Deliberate mutations are outside the no-write guard, at test-only boundaries.
    with pytest.raises(DirectoryBatchError) as info:
        review_directory_changes(loaded, () if empty else (Request('add', 'x', 'data'),))
    assert info.value.reason == ('loaded-mismatch' if when == 'final-model' else 'stale-source')
    assert len(calls) == int(not empty and when.startswith('final'))


@native
@pytest.mark.parametrize('change', ['source-between-scans', 'tree-between-scans', 'source-between-requests', 'source-last', 'model-last'])
def test_native_scans_with_injected_timing_mutations_are_fatal(tmp_path, monkeypatch, change):
    loaded = fixture(tmp_path, added=('x', 'y'))
    calls, errors = [], []
    real_preview, real_plan = directory_plan.preview_directory_proposal, directory_batch.plan_directory_change
    def preview(*a):
        if change == 'source-between-scans': loaded.path.write_bytes(loaded.source_bytes+b' ')
        if change == 'tree-between-scans': (tmp_path / 'x/a').write_bytes(b'boundary change')
        return real_preview(*a)
    def plan(*a, **kw):
        calls.append(1)
        try: result = real_plan(*a, **kw)
        except EditError as exc:
            errors.append(exc)
            raise
        if change == 'source-between-requests' or (change == 'source-last' and len(calls) == 2):
            loaded.path.write_bytes(loaded.source_bytes+b' ')
        if change == 'model-last' and len(calls) == 2: loaded.manifest.project.name = 'mutated'
        return result
    monkeypatch.setattr(directory_plan, 'preview_directory_proposal', preview)
    monkeypatch.setattr(directory_batch, 'plan_directory_change', plan)
    with pytest.raises((DirectoryApplyError, DirectoryBatchError)) as info:
        review_directory_changes(loaded, (Request('add', 'x', 'x'), Request('add', 'y', 'y')))
    reason = 'stale-tree' if change == 'tree-between-scans' else 'loaded-mismatch' if change == 'model-last' else 'stale-source'
    assert info.value.reason == reason
    assert len(calls) == (1 if change.endswith('between-scans') else 2)
    if errors: assert info.value is errors[0]


@native
@pytest.mark.parametrize('phase', ['proposal', 'preview'])
def test_injected_backend_refusal_preserves_nested_error_object(tmp_path, monkeypatch, phase):
    loaded = fixture(tmp_path, added=('x', 'y'))
    def unsupported(): raise DirectoryScanError('unsupported-platform', 'SECRET exception')
    if phase == 'proposal': monkeypatch.setattr(directory_scanner, '_native_backend', unsupported)
    else:
        real = directory_plan.preview_directory_proposal
        def preview(*a):
            monkeypatch.setattr(directory_scanner, '_native_backend', unsupported)
            return real(*a)
        monkeypatch.setattr(directory_plan, 'preview_directory_proposal', preview)
    calls, errors = [], []
    real_plan = directory_batch.plan_directory_change
    def plan(*a, **kw):
        calls.append(1)
        try: return real_plan(*a, **kw)
        except EditError as exc:
            errors.append(exc)
            raise
    monkeypatch.setattr(directory_batch, 'plan_directory_change', plan)
    error = checked(tmp_path, loaded, (Request('add', 'x', 'x'), Request('add', 'y', 'y')), monkeypatch,
                    DirectoryAuthoringError if phase == 'proposal' else DirectoryApplyError)
    assert error is errors[0] and calls == [1]
    assert error.reason == ('scan-failed' if phase == 'proposal' else 'recheck-failed')
    assert error.scan_reason == 'unsupported-platform'
    if phase == 'preview': assert error.authoring_reason == 'scan-failed'


@native
@pytest.mark.parametrize('location', ['root', 'member'])
def test_native_junction_and_linked_root_refusal(tmp_path, monkeypatch, location):
    loaded = fixture(tmp_path, added=('data',))
    (tmp_path / 'outside').mkdir()
    (tmp_path / 'outside/untouched').write_bytes(b'outside')
    link = tmp_path / 'data' if location == 'root' else tmp_path / 'data/linked'
    if location == 'root': (tmp_path / 'data').rename(tmp_path / 'original')
    if not make_dir_link(link, tmp_path / 'outside'): pytest.skip('platform refused disposable directory link')
    try:
        error = checked(tmp_path, loaded, (Request('add', 'data', 'data'),), monkeypatch, DirectoryAuthoringError)
        assert (error.reason, error.scan_reason) == ('scan-failed', 'unsafe')
    finally: remove_dir_link(link)


@native
def test_native_clipped_impacts_secrets_and_child_records_preserved(tmp_path, monkeypatch):
    raw = raw_manifest()
    directory_id = 'd'*200
    raw['project']['metadata'] = {'secret': 'SECRET METADATA'}
    for i in range(20):
        suffix = str(i).zfill(2)+'x'*180
        raw['artifacts'].append(dict(id='child'+suffix, path='data/child'+str(i), sha256='a'*64, size_bytes=999))
        raw['results'].append(dict(id='result'+suffix, run_id='run', name='SECRET NAME', value='SECRET VALUE', artifact_id=directory_id))
        raw['evidence'].append(dict(id='claim'+suffix, claim='SECRET CLAIM', supports=['result'+suffix]))
    loaded = fixture(tmp_path, recorded=('data',), raw=raw)
    raw = load_raw(loaded)
    raw['artifacts'][-1]['id'] = directory_id
    loaded = write_manifest(tmp_path, raw)
    (tmp_path / 'data/a').write_bytes(b'new content')
    result = checked(tmp_path, loaded, (Request('rehash', directory_id),), monkeypatch)
    p = result.previews[0]
    assert p.display_truncated and len(p.artifact_id) == 160 and result.would_change == 1
    for impact in (p.linked_results, p.claims, p.overlapping_files):
        assert (impact.total, impact.omitted, impact.truncated, len(impact.ids)) == (20, 4, True, 16)
        assert all(len(v) == 160 and v.endswith('...') for v in impact.ids)
    encoded = json.dumps(dataclasses.asdict(result))
    assert all(s not in encoded for s in ('SECRET', 'schema_version', 'proposal', 'source_bytes', directory_id))
    assert load_raw(loaded) == raw
    assert isinstance(result.previews, tuple) and isinstance(result.caveats, tuple)
    assert 'Child file artifacts keep their own checks and were not rehashed.' in p.caveats


@native
def test_injected_checked_paths_not_request_strings_and_capacity(tmp_path, monkeypatch):
    loaded = fixture(tmp_path, added=('data',))
    seed = directory_plan.plan_directory_change(loaded, operation='add', artifact_id='data', path='data')
    requests = (Request('add', 'x', 'unverified/x'), Request('add', 'y', 'unverified/y'))
    monkeypatch.setattr(directory_batch, 'plan_directory_change', lambda *a, **kw: seed)
    error = checked(tmp_path, loaded, requests, monkeypatch, DirectoryBatchError)
    assert error.reason == 'batch-overlap'
    # Native scanning rejects case-mismatched spelling earlier. This injection
    # independently covers the batch's Windows casefold comparison only.
    if os.name == 'nt':
        aliases = iter((seed, dataclasses.replace(seed, path='DATA')))
        monkeypatch.setattr(directory_batch, 'plan_directory_change', lambda *a, **kw: next(aliases))
        error = checked(tmp_path, loaded, requests, monkeypatch, DirectoryBatchError)
        assert error.reason == 'batch-overlap'
    for length in (1024, 1025):
        clipped = dataclasses.replace(seed, path='x'*length, display_truncated=True)
        monkeypatch.setattr(directory_batch, 'plan_directory_change', lambda *a, **kw: clipped)
        error = checked(tmp_path, loaded, requests, monkeypatch, DirectoryBatchError)
        assert error.reason == 'path-summary-capacity-exceeded'
    # Unusually large but immutable previews exercise only the report guard.
    impact = dataclasses.replace(seed.claims, ids=('\uffff'*157+'...',)*16, total=100, omitted=84, truncated=True)
    large = dataclasses.replace(seed, operation='rehash', status='unchanged', artifact_id='x'*160,
                                path='\uffff'*1024, claims=impact, linked_results=impact,
                                overlapping_files=impact, old_size=10**4000)
    monkeypatch.setattr(directory_batch, 'plan_directory_change', lambda *a, **kw: large)
    rehashes = tuple(Request('rehash', 'id'+str(i)) for i in range(8))
    result = checked(tmp_path, loaded, rehashes, monkeypatch)
    assert result.unchanged == 8 and all(p is large for p in result.previews)
    assert 400_000 < len(json.dumps(dataclasses.asdict(result)).encode()) <= directory_batch.MAX_BATCH_JSON_BYTES
    excessive = dataclasses.replace(large, caveats=('x'*directory_batch.MAX_BATCH_JSON_BYTES,))
    monkeypatch.setattr(directory_batch, 'plan_directory_change', lambda *a, **kw: excessive)
    error = checked(tmp_path, loaded, rehashes, monkeypatch, DirectoryBatchError)
    assert error.reason == 'report-capacity-exceeded' and 'x'*100 not in str(error)


@native
@pytest.mark.parametrize('error', [DirectoryAuthoringError('scan-failed', 'SECRET', scan_reason='unsafe'),
                                 DirectoryApplyError('proposal-mismatch', 'SECRET'), RuntimeError('SECRET')])
def test_injected_second_request_errors_propagate_without_partial_report_or_retry(tmp_path, monkeypatch, error):
    loaded = fixture(tmp_path, added=('x', 'y', 'z'))
    calls = []
    real = directory_batch.plan_directory_change
    def plan(*a, **kw):
        calls.append(kw['artifact_id'])
        if len(calls) == 2: raise error
        return real(*a, **kw)
    monkeypatch.setattr(directory_batch, 'plan_directory_change', plan)
    actual = checked(tmp_path, loaded, tuple(Request('add', n, n) for n in ('x', 'y', 'z')), monkeypatch, type(error))
    assert actual is error and calls == ['x', 'y']


def test_recursive_import_isolation_and_fresh_public_startup(tmp_path):
    src = Path(runevidence.__file__).parent
    users = [p.relative_to(src).as_posix() for p in src.rglob('*.py')
             if p.name != 'directory_batch.py' and 'directory_batch' in p.read_text(encoding='utf-8')]
    assert sorted(users) == ['directory_present.py', 'directory_review.py']
    script = '''import sys
import runevidence, runevidence.cli, runevidence.checks, runevidence.edit
import runevidence_hydra, runevidence_mlflow
assert not any('runevidence.' + n in sys.modules for n in (
    'directory_batch', 'directory_audit', 'directory_plan', 'directory_preview',
    'directory_authoring', 'directory_apply', 'directory_cooperative_apply'))
'''
    p = subprocess.run([sys.executable, '-B', '-c', script], cwd=tmp_path, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
