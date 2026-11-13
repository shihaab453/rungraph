"""Quiet native reads and separately labelled injected boundary observations.

All setup and deliberate race mutations use fresh disposable Temp fixtures.
Injections characterize routing, not hostile-writer or atomic-snapshot safety.
"""

from __future__ import annotations

import ast
import builtins
import dataclasses
import json
import logging
from pathlib import Path

import pytest

from conftest import make_dir_link, remove_dir_link
import runevidence
from runevidence import directory_audit as audit, directory_batch as batch
from runevidence import directory_review as operator, directory_review_path as adapter
from runevidence import directory_scanner as scanner
from runevidence.manifest import ManifestLoadError, load_manifest
from runevidence.models import MANIFEST_FILENAME
from test_directory_batch import fixture
from test_directory_present import pure_call, refused_entry, supplied_audit
from test_directory_preview import VECTORS, native, no_writes, snapshot


def quiet(root, target, requests, monkeypatch, error=None):
    """Observe the real loader and real review under write/output prohibitions."""
    before = snapshot(root)
    loads, reviews, results, errors = [], [], [], []
    real_load, real_review = adapter.load_manifest, adapter.review_directories_for_operator

    def load(arg):
        assert arg is target
        loaded = real_load(arg)
        loads.append(loaded)
        return loaded

    def review(arg, *, requests):
        assert loads == [arg] and requests is supplied
        reviews.append(arg)
        try:
            result = real_review(arg, requests=requests)
        except Exception as exc:
            errors.append(exc)
            raise
        results.append(result)
        return result

    def forbid(*a, **kw):
        raise AssertionError('path review emitted output')

    supplied = requests
    with monkeypatch.context() as m:
        m.setattr(adapter, 'load_manifest', load)
        m.setattr(adapter, 'review_directories_for_operator', review)
        with no_writes(m):
            m.setattr(builtins, 'print', forbid)
            m.setattr(logging.Logger, '_log', forbid)
            if error:
                with pytest.raises(error) as info:
                    adapter.review_existing_directory_manifest(target, requests=requests)
                result = info.value
            else:
                result = adapter.review_existing_directory_manifest(target, requests=requests)
    assert snapshot(root) == before
    assert len(loads) == len(reviews) == 1
    if error:
        assert errors == [result] and not results
    else:
        assert len(results) == 1 and result is results[0]
        assert result == real_review(load_manifest(target), requests=requests)
        assert len(result.text.encode('utf-8')) <= 262_144
        assert result.text.isascii() and not any(c in result.text for c in '\r\t\x1b\x00')
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.text = 'changed'
    return result


@native
@pytest.mark.parametrize('vector', VECTORS, ids=[v['name'] for v in VECTORS])
def test_native_file_and_folder_locators_pinned_audit_and_ordered_batch(tmp_path, monkeypatch, vector):
    loaded = fixture(tmp_path, recorded=('recorded',), added=('new',), vector=vector)
    lock = loaded.path.with_name(loaded.path.name+'.cooperative-writer.lock')
    lock.write_bytes(b'synthetic lock sentinel, never inspect during review')
    audited = quiet(tmp_path, tmp_path, None, monkeypatch)
    p = audited.report.entries[0].preview
    assert p.status == 'unchanged'
    assert (p.new_sha256, p.new_size, p.members) == (vector['sha256'], vector['size_bytes'], len(vector['layout']))
    requests = (batch.DirectoryChangeRequest('add', 'new', 'new'),
                batch.DirectoryChangeRequest('rehash', 'recorded'))
    scans = []
    real_scan = scanner.scan_directory
    def scan(root, *a, **kw):
        scans.append(root)
        return real_scan(root, *a, **kw)
    monkeypatch.setattr(scanner, 'scan_directory', scan)
    result = quiet(tmp_path, loaded.path, requests, monkeypatch)
    assert [p.artifact_id for p in result.report.previews] == ['new', 'recorded']
    assert [p.status for p in result.report.previews] == ['would-change', 'unchanged']
    assert scans == [tmp_path/n for n in ('new', 'new', 'recorded', 'recorded')]*2
    for p in result.report.previews:
        assert (p.new_sha256, p.new_size, p.members) == (vector['sha256'], vector['size_bytes'], len(vector['layout']))


@native
def test_native_none_and_empty_have_distinct_deterministic_display(tmp_path, monkeypatch):
    raw = {'schema_version': '0.1', 'project': {'id': 'demo', 'name': 'Synthetic'},
           'runs': [], 'artifacts': [], 'results': [], 'evidence': []}
    fixture(tmp_path, raw=raw)
    audited = quiet(tmp_path, tmp_path, None, monkeypatch)
    empty = quiet(tmp_path, tmp_path, (), monkeypatch)
    assert audited.mode == 'audit' and audited.report.state == 'no-directory-records'
    assert empty.mode == 'batch' and empty.report.state == 'no-requests'
    assert audited.text != empty.text


def test_injected_exact_load_review_request_and_result_identity(monkeypatch):
    target, loaded = object(), object()
    attack = 'label\r\n"\\\t\x1b[2J\u2028'
    report = supplied_audit(refused_entry(artifact_id=attack))
    with monkeypatch.context() as m:
        m.setattr(operator, 'audit_recorded_directories', lambda arg: report)
        result = operator.review_directories_for_operator(loaded)
    assert json.dumps(attack, ensure_ascii=True) in result.text
    for requests in (None, (), (batch.DirectoryChangeRequest('rehash', 'z'), batch.DirectoryChangeRequest('rehash', 'a')), []):
        calls = []
        def load(arg):
            assert arg is target
            calls.append('load')
            return loaded
        def review(arg, *, requests):
            assert arg is loaded and requests is supplied
            calls.append('review')
            return result
        supplied = requests
        with pure_call(monkeypatch), monkeypatch.context() as m:
            m.setattr(adapter, 'load_manifest', load)
            m.setattr(adapter, 'review_directories_for_operator', review)
            assert adapter.review_existing_directory_manifest(target, requests=requests) is result
        assert calls == ['load', 'review']


def test_injected_errors_propagate_identically_without_retry_or_fallback(monkeypatch):
    for stage, error in (('load', OSError('private I/O body')),
                         ('review', audit.DirectoryAuditError('stale-source')),
                         ('review', batch.DirectoryBatchError('loaded-mismatch')),
                         ('review', RuntimeError('private unexpected body'))):
        calls = []
        def load(arg):
            calls.append('load')
            if stage == 'load': raise error
            return loaded
        def review(arg, *, requests):
            assert arg is loaded
            calls.append('review')
            raise error
        loaded = object()
        with pure_call(monkeypatch), monkeypatch.context() as m:
            m.setattr(adapter, 'load_manifest', load)
            m.setattr(adapter, 'review_directories_for_operator', review)
            with pytest.raises(type(error)) as info:
                adapter.review_existing_directory_manifest(object())
        assert info.value is error
        assert calls == (['load'] if stage == 'load' else ['load', 'review'])


def test_caller_contract_bad_keywords_and_optional_shape(tmp_path, monkeypatch):
    fixture(tmp_path)
    calls = []
    real = adapter.load_manifest
    monkeypatch.setattr(adapter, 'load_manifest', lambda arg: calls.append(arg) or real(arg))
    with pytest.raises(TypeError): adapter.review_existing_directory_manifest(tmp_path, ())
    with pytest.raises(TypeError): adapter.review_existing_directory_manifest(tmp_path, raw={})
    assert calls == []
    result = quiet(tmp_path, tmp_path, [], monkeypatch, batch.DirectoryBatchError)
    assert result.reason == 'invalid-request'
    assert str(result) == 'Directory batch review refused: invalid-request'
    result = quiet(tmp_path, tmp_path, (batch.DirectoryChangeRequest('rehash', 'x'),
                   batch.DirectoryChangeRequest('rehash', 'x')), monkeypatch, batch.DirectoryBatchError)
    assert result.reason == 'duplicate-id'


@pytest.mark.parametrize('case,code', [('missing', 'manifest_not_found'), ('wrong-folder', 'manifest_not_found'),
    ('malformed', 'manifest_unreadable'), ('schema', 'schema_invalid'), ('unreadable-injected', 'manifest_unreadable')])
def test_loader_existing_classifications_and_single_attempt(tmp_path, monkeypatch, case, code):
    fixture(tmp_path)
    target = tmp_path/MANIFEST_FILENAME
    if case == 'missing': target = tmp_path/'missing.json'
    if case == 'wrong-folder':
        target = tmp_path/'child'
        target.mkdir()
    if case == 'malformed': target.write_bytes(b'not JSON')
    if case == 'schema': target.write_bytes(b'{}')
    real_read = Path.read_bytes
    reads = []
    def read(p):
        reads.append(p)
        if case == 'unreadable-injected' and p == target: raise PermissionError('synthetic denied read')
        return real_read(p)
    def forbid(*a, **kw): raise AssertionError('review after loader failure')
    before = snapshot(tmp_path)
    with monkeypatch.context() as m:
        m.setattr(Path, 'read_bytes', read)
        m.setattr(adapter, 'review_directories_for_operator', forbid)
        with no_writes(m):
            with pytest.raises(ManifestLoadError) as direct: load_manifest(target)
            direct_reads = list(reads)
            reads.clear()
            with pytest.raises(ManifestLoadError) as wrapped: adapter.review_existing_directory_manifest(target)
    assert reads == direct_reads
    assert wrapped.value.findings == direct.value.findings
    assert all(f.code.name == code for f in wrapped.value.findings)
    assert snapshot(tmp_path) == before


def test_caller_contract_invalid_locator_matches_loader_without_reads(monkeypatch):
    def forbid(*a, **kw): raise AssertionError('invalid locator reached read or review')
    with monkeypatch.context() as m:
        m.setattr(Path, 'read_bytes', forbid)
        m.setattr(adapter, 'review_directories_for_operator', forbid)
        for target in (None, object(), 123):
            with pytest.raises(TypeError) as direct: load_manifest(target)
            with pytest.raises(TypeError) as wrapped: adapter.review_existing_directory_manifest(target)
            assert str(wrapped.value) == str(direct.value)


def test_injected_post_load_mutation_reaches_real_stale_source_refusal(tmp_path, monkeypatch):
    loaded = fixture(tmp_path)
    real = adapter.load_manifest
    for requests, error in ((None, audit.DirectoryAuditError), ((), batch.DirectoryBatchError)):
        loaded.path.write_bytes(loaded.source_bytes)
        calls, errors = [], []
        def load(target):
            value = real(target)
            calls.append(value)
            value.path.write_bytes(value.source_bytes+b' ')
            return value
        real_review = adapter.review_directories_for_operator
        def review(arg, *, requests):
            assert calls == [arg]
            before = snapshot(tmp_path)
            with no_writes(monkeypatch):
                try: return real_review(arg, requests=requests)
                except error as exc:
                    errors.append(exc)
                    raise
                finally: assert snapshot(tmp_path) == before
        with monkeypatch.context() as m:
            m.setattr(adapter, 'load_manifest', load)
            m.setattr(adapter, 'review_directories_for_operator', review)
            with pytest.raises(error) as info: adapter.review_existing_directory_manifest(tmp_path, requests=requests)
        assert info.value is errors[0] and info.value.reason == 'stale-source'


def test_injected_mutation_before_loader_return_uses_loader_snapshot(tmp_path, monkeypatch):
    loaded = fixture(tmp_path)
    real_read = Path.read_bytes
    def read(p):
        value = real_read(p)
        if p == loaded.path:
            p.write_bytes(value+b' ')
        return value
    def review(arg, *, requests):
        assert arg.source_bytes == loaded.source_bytes
        with pytest.raises(audit.DirectoryAuditError) as info:
            operator.review_directories_for_operator(arg)
        assert info.value.reason == 'stale-source'
        raise info.value
    with monkeypatch.context() as m:
        m.setattr(Path, 'read_bytes', read)
        m.setattr(adapter, 'review_directories_for_operator', review)
        with pytest.raises(audit.DirectoryAuditError): adapter.review_existing_directory_manifest(tmp_path)


def test_injected_loaded_model_drift_is_not_a_clean_report(tmp_path, monkeypatch):
    fixture(tmp_path)
    real = adapter.load_manifest
    def load(target):
        loaded = real(target)
        loaded.manifest.project.name = 'injected drift'
        return loaded
    monkeypatch.setattr(adapter, 'load_manifest', load)
    result = quiet(tmp_path, tmp_path, None, monkeypatch, audit.DirectoryAuditError)
    assert result.reason == 'loaded-mismatch'


@native
def test_native_linked_locator_inherits_loader_resolution(tmp_path, monkeypatch):
    real_root = tmp_path/'real'
    real_root.mkdir()
    fixture(real_root)
    link = tmp_path/'link'
    if not make_dir_link(link, real_root):
        pytest.skip('platform refused disposable directory link')
    try:
        # The existing loader resolves this supported locator to its real folder.
        assert load_manifest(link).path == load_manifest(real_root).path
        assert quiet(tmp_path, link, None, monkeypatch).report.state == 'no-directory-records'
    finally:
        remove_dir_link(link)


def test_adapter_ast_has_only_loader_then_review_and_no_source_importers():
    src = Path(runevidence.__file__).parent
    tree = ast.parse((src/'directory_review_path.py').read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef))
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
    assert [n.func.id for n in calls] == ['load_manifest', 'review_directories_for_operator']
    assert [n.module for n in tree.body if isinstance(n, ast.ImportFrom)] == [
        '__future__', 'pathlib', 'directory_review', 'manifest']
    importers = []
    for p in src.rglob('*.py'):
        if p.name == 'directory_review_path.py': continue
        nodes = ast.walk(ast.parse(p.read_text(encoding='utf-8')))
        if any((isinstance(n, ast.Import) and any(a.name.split('.')[-1] == 'directory_review_path' for a in n.names))
               or (isinstance(n, ast.ImportFrom) and ((n.module or '').split('.')[-1] == 'directory_review_path'
                   or any(a.name == 'directory_review_path' for a in n.names))) for n in nodes):
            importers.append(p.relative_to(src).as_posix())
    assert importers == []
