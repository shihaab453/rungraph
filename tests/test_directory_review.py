"""Disposable synthetic integration; injected observations are not OS assurance.

Native cases retain the real Windows scanner. Timing injections deliberately
mutate only disposable fixtures, outside no-write observations of quiet calls.
"""

from __future__ import annotations

import ast
import builtins
import copy
import dataclasses
import json
import logging
from pathlib import Path
import subprocess
import sys

import pytest

from conftest import make_dir_link, remove_dir_link
import runevidence
from runevidence import directory_audit as audit, directory_batch as batch
from runevidence import directory_plan as plan, directory_present as present
from runevidence import directory_review as operator, directory_scanner as scanner
from runevidence.directory_apply import DirectoryApplyError
from runevidence.directory_authoring import DirectoryAuthoringError
from runevidence.directory_batch import DirectoryChangeRequest as Request
from runevidence.directory_scanner import DirectoryScanError
from runevidence.edit import EditError
from test_directory_batch import fixture, observe
from test_directory_present import (
    assert_caveats, pure_call, refused_entry, supplied_audit, supplied_batch,
    supplied_preview,
)
from test_directory_preview import VECTORS, native, no_writes, snapshot


def assert_result(result, mode, report):
    assert type(result) is operator.DirectoryOperatorReview
    assert result.mode == mode and result.report is report
    assert result.text == present.render_directory_review(report)
    assert len(result.text.encode('utf-8')) <= 262_144
    assert_caveats(result.text, report)
    assert result.caveats is operator._CAVEATS
    assert type(result.caveats) is tuple and all(type(s) is str for s in result.caveats)
    text = ' '.join(result.caveats)
    for phrase in ('Internal advisory read-only', 'Text merely displays supplied data',
                   'only by the delegated checked service', 'Separate member scans',
                   'directory checks are non-atomic', 'preview may already be stale',
                   'Neither unchanged nor would-change means authorized to apply',
                   'later action requires fresh validation'):
        assert phrase in text
    assert [f.name for f in dataclasses.fields(result)] == ['mode', 'report', 'text', 'caveats']
    for name in ('mode', 'report', 'text', 'caveats'):
        with pytest.raises(dataclasses.FrozenInstanceError): setattr(result, name, None)


def checked(root, loaded, requests, monkeypatch, error=None):
    """Observe real delegated services, identity and no writes on a quiet tree."""
    lock = loaded.path.with_name(loaded.path.name + '.cooperative-writer.lock')
    if not lock.exists(): lock.write_bytes(b'disposable sentinel; must not be inspected')
    before = snapshot(root)
    model = copy.deepcopy((loaded.manifest.model_dump(), loaded.source_bytes))
    calls, reports, renders, errors = [], [], [], []
    real_audit, real_batch = operator.audit_recorded_directories, operator.review_directory_changes
    real_render = operator.render_directory_review

    def delegated(mode, fn, arg, *args):
        assert arg is loaded
        if args: assert args[0] is requests
        calls.append(mode)
        try: report = fn(arg, *args)
        except Exception as exc:
            errors.append(exc)
            raise
        reports.append(report)
        return report

    def render(report):
        renders.append(report)
        return real_render(report)

    def forbid(*a, **kw): raise AssertionError('operator path emitted output')

    with monkeypatch.context() as m:
        m.setattr(operator, 'audit_recorded_directories', lambda arg: delegated('audit', real_audit, arg))
        m.setattr(operator, 'review_directory_changes', lambda arg, req: delegated('batch', real_batch, arg, req))
        m.setattr(operator, 'render_directory_review', render)
        with no_writes(m):
            m.setattr(builtins, 'print', forbid)
            m.setattr(logging.Logger, '_log', forbid)
            if error:
                with pytest.raises(error) as info:
                    operator.review_directories_for_operator(loaded, requests=requests)
                result = info.value
            else:
                result = operator.review_directories_for_operator(loaded, requests=requests)
    assert snapshot(root) == before
    assert (loaded.manifest.model_dump(), loaded.source_bytes) == model
    mode = 'audit' if requests is None else 'batch'
    assert calls == [mode]
    if error:
        assert errors == [result] and result is errors[0]
        assert renders == reports == []
    else:
        assert len(reports) == len(renders) == 1 and renders[0] is reports[0]
        assert_result(result, mode, reports[0])
    return result


@pytest.mark.parametrize('requests', [None, (), (Request('rehash', 'z'), Request('add', 'a', 'a')), []])
def test_injected_dispatch_identity_exactly_once_and_no_wrapper_io(requests, monkeypatch, capsys):
    loaded = object()
    report = supplied_audit() if requests is None else supplied_batch()
    text = present.render_directory_review(report)
    calls = []

    def audited(arg):
        assert arg is loaded and requests is None
        calls.append('audit')
        return report

    def batched(arg, req):
        assert arg is loaded and req is requests and requests is not None
        calls.append('batch')
        return report

    def rendered(arg):
        assert arg is report
        calls.append('render')
        return text

    # All Python I/O/scan/planning/persistence paths are blocked here. The
    # service stubs show routing only, including unchanged invalid input.
    with pure_call(monkeypatch), monkeypatch.context() as m:
        m.setattr(operator, 'audit_recorded_directories', audited)
        m.setattr(operator, 'review_directory_changes', batched)
        m.setattr(operator, 'render_directory_review', rendered)
        result = operator.review_directories_for_operator(loaded, requests=requests)
    assert calls == [('audit' if requests is None else 'batch'), 'render']
    assert result.text is text
    assert_result(result, 'audit' if requests is None else 'batch', report)
    assert capsys.readouterr() == ('', '')


def test_signature_is_keyword_only_and_default_is_audit(monkeypatch):
    calls = []
    report = supplied_audit()
    monkeypatch.setattr(operator, 'audit_recorded_directories', lambda arg: calls.append(arg) or report)
    loaded = object()
    assert operator.review_directories_for_operator(loaded).report is report
    assert calls == [loaded]
    with pytest.raises(TypeError): operator.review_directories_for_operator(loaded, ())
    with pytest.raises(TypeError): operator.review_directories_for_operator(loaded, raw={})
    assert calls == [loaded]


@pytest.mark.parametrize('requests,error', [
    (None, audit.DirectoryAuditError('stale-source')),
    ((), batch.DirectoryBatchError('loaded-mismatch')),
    ((), DirectoryAuthoringError('scan-failed', 'private body', scan_reason='unsafe')),
    ((), DirectoryApplyError('recheck-failed', 'private body', authoring_reason='scan-failed', scan_reason='unsupported-platform')),
    (None, RuntimeError('private unexpected body')),
    ((), RuntimeError('private unexpected body')),
])
def test_injected_workflow_errors_propagate_identically_without_render_or_partial(requests, error, monkeypatch):
    calls = []

    def fail(*args):
        calls.append('service')
        raise error

    def forbid(*args): raise AssertionError('fallback, render or result after failure')

    with pure_call(monkeypatch), monkeypatch.context() as m:
        m.setattr(operator, 'audit_recorded_directories', fail if requests is None else forbid)
        m.setattr(operator, 'review_directory_changes', fail if requests is not None else forbid)
        m.setattr(operator, 'render_directory_review', forbid)
        m.setattr(operator, 'DirectoryOperatorReview', forbid)
        with pytest.raises(type(error)) as info:
            operator.review_directories_for_operator(object(), requests=requests)
    assert info.value is error and calls == ['service']


@pytest.mark.parametrize('case', ['invalid-report', 'render-capacity-exceeded', 'unexpected'])
def test_actual_renderer_refusal_propagates_without_constructing_partial_result(case, monkeypatch):
    report = dataclasses.replace(supplied_audit(), total=-1) if case == 'invalid-report' else supplied_audit()
    calls, errors = [], []
    real = present.render_directory_review
    unexpected = RuntimeError('private rendering body')

    def render(arg):
        calls.append(arg)
        try:
            if case == 'unexpected': raise unexpected
            return real(arg)
        except Exception as exc:
            errors.append(exc)
            raise

    def forbid(*a, **kw): raise AssertionError('constructed partial operator record')

    with pure_call(monkeypatch), monkeypatch.context() as m:
        m.setattr(operator, 'audit_recorded_directories', lambda arg: report)
        m.setattr(operator, 'render_directory_review', render)
        m.setattr(operator, 'DirectoryOperatorReview', forbid)
        if case == 'render-capacity-exceeded': m.setattr(present, 'MAX_RENDER_BYTES', 1)
        with pytest.raises(RuntimeError if case == 'unexpected' else present.DirectoryPresentationError) as info:
            operator.review_directories_for_operator(object())
    assert calls == [report] and info.value is errors[0]
    if case == 'unexpected': assert info.value is unexpected
    else:
        assert info.value.reason == case
        assert str(info.value) == 'Directory presentation refused: '+case


def test_supplied_refusal_and_malicious_display_stay_escaped(monkeypatch):
    attack = 'label\r\nbatch state="applied"\t\x1b[2J\u2028"\\\x00'
    reports = (supplied_audit(refused_entry(artifact_id=attack)),
               supplied_batch(supplied_preview(artifact_id=attack)))
    for requests, report in zip((None, ()), reports):
        with pure_call(monkeypatch), monkeypatch.context() as m:
            m.setattr(operator, 'audit_recorded_directories', lambda arg: report)
            m.setattr(operator, 'review_directory_changes', lambda arg, req: report)
            result = operator.review_directories_for_operator(object(), requests=requests)
        assert_result(result, 'audit' if requests is None else 'batch', report)
        assert json.dumps(attack, ensure_ascii=True) in result.text
        assert result.text.isascii() and not any(c in result.text for c in '\r\t\x1b\x00')
        if requests is None:
            assert result.report.state == 'refused' and 'status="refused"' in result.text
            assert 'reason="scan-failed"' in result.text and 'scan_reason="missing"' in result.text


@native
def test_native_no_records_and_explicit_empty_batch_are_distinct(tmp_path, monkeypatch):
    loaded = fixture(tmp_path)
    audited = checked(tmp_path, loaded, None, monkeypatch)
    batched = checked(tmp_path, loaded, (), monkeypatch)
    assert audited.report.state == 'no-directory-records'
    assert batched.report.state == 'no-requests'
    assert audited.text != batched.text


@native
@pytest.mark.parametrize('vector', VECTORS, ids=[v['name'] for v in VECTORS])
def test_native_vector_outputs_audit_and_batch_inherit_checked_previews(tmp_path, monkeypatch, vector):
    loaded = fixture(tmp_path, recorded=('recorded',), added=('new',), vector=vector)
    audit_previews = []
    real = audit.plan_directory_change

    def audited(*a, **kw):
        p = real(*a, **kw)
        audit_previews.append(p)
        return p

    monkeypatch.setattr(audit, 'plan_directory_change', audited)
    calls, scans, previews = observe(monkeypatch, loaded)
    result = checked(tmp_path, loaded, None, monkeypatch)
    p = result.report.entries[0].preview
    assert p is audit_previews[0] and p.status == 'unchanged'
    assert scans == [tmp_path/'recorded']*2 and calls == []
    assert (p.new_sha256, p.new_size, p.members) == (vector['sha256'], vector['size_bytes'], len(vector['layout']))
    requests = (Request('add', 'new', 'new'), Request('rehash', 'recorded'))
    result = checked(tmp_path, loaded, requests, monkeypatch)
    assert [p.artifact_id for p in result.report.previews] == ['new', 'recorded']
    assert len(calls) == 2
    assert scans == [tmp_path/n for n in ('recorded', 'recorded', 'new', 'new', 'recorded', 'recorded')]
    assert all(p is original for p, original in zip(result.report.previews, previews))
    assert [p.status for p in result.report.previews] == ['would-change', 'unchanged']
    for p in result.report.previews:
        assert (p.new_sha256, p.new_size, p.members) == (vector['sha256'], vector['size_bytes'], len(vector['layout']))


@native
def test_native_audit_changed_unchanged_and_safe_refused_are_visible(tmp_path, monkeypatch):
    loaded = fixture(tmp_path, recorded=('z', 'a', 'missing'))
    (tmp_path/'a/a').write_bytes(b'changed bytes')
    (tmp_path/'missing').rename(tmp_path/'moved')
    result = checked(tmp_path, loaded, None, monkeypatch)
    report = result.report
    assert (report.state, report.total, report.unchanged, report.would_change, report.refused) == ('refused', 3, 1, 1, 1)
    assert [e.artifact_id for e in report.entries] == ['a', 'missing', 'z']
    assert report.entries[0].preview.new_size == len(b'changed bytes')
    assert (report.entries[1].reason, report.entries[1].scan_reason) == ('scan-failed', 'missing')
    assert 'status="refused"' in result.text and 'all-clear inference' in result.text
    assert checked(tmp_path, loaded, None, monkeypatch) == result


@native
def test_native_mixed_batch_preserves_order_and_each_checked_preview(tmp_path, monkeypatch):
    loaded = fixture(tmp_path, recorded=('z', 'a'), added=('m',))
    (tmp_path/'a/a').write_bytes(b'changed bytes')
    requests = (Request('rehash', 'z'), Request('add', 'm', 'm'), Request('rehash', 'a'))
    calls, scans, previews = observe(monkeypatch, loaded)
    result = checked(tmp_path, loaded, requests, monkeypatch)
    assert (result.report.total, result.report.would_change, result.report.unchanged) == (3, 2, 1)
    assert calls == [dataclasses.asdict(r) for r in requests]
    assert scans == [tmp_path/n for n in ('z', 'z', 'm', 'm', 'a', 'a')]
    assert all(p is original for p, original in zip(result.report.previews, previews))
    assert [p.artifact_id for p in result.report.previews] == ['z', 'm', 'a']
    assert result.report.previews[-1].old_sha256 != result.report.previews[-1].new_sha256
    assert checked(tmp_path, loaded, requests, monkeypatch) == result


@pytest.mark.parametrize('requests,reason', [
    ([], 'invalid-request'), (({},), 'invalid-request'),
    ((Request('rehash', 'x', 'extra'),), 'invalid-request'),
    ((Request('add', 'x', 'x'), Request('rehash', 'x')), 'duplicate-id'),
    (tuple(Request('add', str(i), 'x') for i in range(9)), 'capacity-exceeded'),
])
def test_actual_batch_rejects_shape_before_any_io(requests, reason, monkeypatch):
    with pure_call(monkeypatch), pytest.raises(batch.DirectoryBatchError) as info:
        operator.review_directories_for_operator(object(), requests=requests)
    assert info.value.reason == reason


@native
@pytest.mark.parametrize('paths', [('data', 'data'), ('data', 'data/n')])
def test_native_batch_overlap_retains_existing_classification(tmp_path, monkeypatch, paths):
    loaded = fixture(tmp_path, added=paths, vector=VECTORS[0])
    requests = tuple(Request('add', 'id'+str(i), path) for i, path in enumerate(paths))
    error = checked(tmp_path, loaded, requests, monkeypatch, batch.DirectoryBatchError)
    assert error.reason == 'batch-overlap'


def test_existing_path_collision_is_not_reclassified(tmp_path, monkeypatch):
    loaded = fixture(tmp_path, recorded=('data',))
    error = checked(tmp_path, loaded, (Request('add', 'other', 'data'),), monkeypatch, DirectoryAuthoringError)
    assert error.reason == 'path-collision'


@pytest.mark.parametrize('requests', [None, ()])
@pytest.mark.parametrize('change', ['stale-source', 'loaded-mismatch'])
def test_actual_fatal_source_model_drift_in_both_modes(tmp_path, monkeypatch, requests, change):
    loaded = fixture(tmp_path)
    if change == 'stale-source': loaded.path.write_bytes(loaded.source_bytes+b' ')
    else: loaded.manifest.project.name = 'mutated'
    error = checked(tmp_path, loaded, requests, monkeypatch,
                    audit.DirectoryAuditError if requests is None else batch.DirectoryBatchError)
    assert error.reason == change


@native
@pytest.mark.parametrize('mode', ['audit', 'batch'])
def test_native_linked_root_refusal_stays_refusal(tmp_path, monkeypatch, mode):
    loaded = fixture(tmp_path, recorded=('data',))
    (tmp_path/'data').rename(tmp_path/'original')
    if not make_dir_link(tmp_path/'data', tmp_path/'original'):
        pytest.skip('platform refused disposable directory link')
    try:
        requests = None if mode == 'audit' else (Request('rehash', 'data'),)
        result = checked(tmp_path, loaded, requests, monkeypatch,
                         DirectoryAuthoringError if mode == 'batch' else None)
        if mode == 'batch':
            assert (result.reason, result.scan_reason) == ('scan-failed', 'unsafe')
        else:
            entry = result.report.entries[0]
            assert (entry.status, entry.reason, entry.scan_reason) == ('refused', 'scan-failed', 'unsafe')
    finally: remove_dir_link(tmp_path/'data')


@native
@pytest.mark.parametrize('mode', ['audit', 'batch'])
@pytest.mark.parametrize('backend', ['unsupported', 'unexpected'])
def test_injected_backend_outcomes_preserve_native_workflow_semantics(tmp_path, monkeypatch, mode, backend):
    loaded = fixture(tmp_path, recorded=('data',))
    unexpected = RuntimeError('private backend body')

    def fail():
        if backend == 'unexpected': raise unexpected
        raise DirectoryScanError('unsupported-platform', 'private backend body')

    monkeypatch.setattr(scanner, '_native_backend', fail)
    requests = None if mode == 'audit' else (Request('rehash', 'data'),)
    error = RuntimeError if backend == 'unexpected' else DirectoryAuthoringError if mode == 'batch' else None
    result = checked(tmp_path, loaded, requests, monkeypatch, error)
    if backend == 'unexpected': assert result is unexpected
    elif mode == 'batch': assert (result.reason, result.scan_reason) == ('scan-failed', 'unsupported-platform')
    else:
        entry = result.report.entries[0]
        assert (entry.status, entry.scan_reason) == ('refused', 'unsupported-platform')
        assert 'status="refused"' in result.text


@native
@pytest.mark.parametrize('change', ['source-between-scans', 'tree-between-scans', 'source-between-requests', 'source-last', 'model-last'])
def test_native_scans_with_injected_cross_phase_mutation_abort(tmp_path, monkeypatch, change):
    loaded = fixture(tmp_path, added=('x', 'y'))
    real_preview, real_plan = plan.preview_directory_proposal, batch.plan_directory_change
    calls, errors = [], []

    def preview(*a):
        if change == 'source-between-scans': loaded.path.write_bytes(loaded.source_bytes+b' ')
        if change == 'tree-between-scans': (tmp_path/'x/a').write_bytes(b'boundary mutation')
        return real_preview(*a)

    def planned(*a, **kw):
        calls.append(kw['artifact_id'])
        try: result = real_plan(*a, **kw)
        except EditError as exc:
            errors.append(exc)
            raise
        if change == 'source-between-requests' or (change == 'source-last' and len(calls) == 2):
            loaded.path.write_bytes(loaded.source_bytes+b' ')
        if change == 'model-last' and len(calls) == 2: loaded.manifest.project.name = 'mutated'
        return result

    def forbid(*a): raise AssertionError('rendered partial mutation result')

    monkeypatch.setattr(plan, 'preview_directory_proposal', preview)
    monkeypatch.setattr(batch, 'plan_directory_change', planned)
    monkeypatch.setattr(operator, 'render_directory_review', forbid)
    with pytest.raises((DirectoryApplyError, batch.DirectoryBatchError)) as info:
        operator.review_directories_for_operator(loaded, requests=(Request('add', 'x', 'x'), Request('add', 'y', 'y')))
    expected = 'stale-tree' if change == 'tree-between-scans' else 'loaded-mismatch' if change == 'model-last' else 'stale-source'
    assert info.value.reason == expected
    assert calls == (['x'] if change.endswith('between-scans') else ['x', 'y'])
    if errors: assert info.value is errors[0]


def test_recursive_exact_import_topology_and_fresh_public_startup(tmp_path):
    src = Path(runevidence.__file__).parent
    expected = {
        'directory_review': ['directory_review_path.py'],
        'directory_present': ['directory_review.py'],
        'directory_audit': ['directory_present.py', 'directory_review.py'],
        'directory_batch': ['directory_present.py', 'directory_review.py'],
    }
    for module, users in expected.items():
        actual = []
        for p in src.rglob('*.py'):
            if p.name == module+'.py': continue
            source = p.read_text(encoding='utf-8')
            if module == 'directory_review':
                # The existing render_directory_review function is not an
                # import of this module. Inspect all actual import forms.
                nodes = ast.walk(ast.parse(source))
                used = any(
                    (isinstance(n, ast.Import) and any(
                        a.name == module or a.name.endswith('.'+module) for a in n.names))
                    or (isinstance(n, ast.ImportFrom) and (
                        n.module == module or (n.module or '').endswith('.'+module)
                        or any(a.name == module for a in n.names)))
                    for n in nodes)
            else:
                used = module in source
            if used: actual.append(p.relative_to(src).as_posix())
        assert sorted(actual) == users
    tree = ast.parse((src/'directory_review.py').read_text(encoding='utf-8'))
    imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert imports == ['__future__', 'dataclasses', 'typing', 'directory_audit', 'directory_batch', 'directory_present', 'manifest']
    assert not any(isinstance(n, (ast.Import, ast.Try)) for n in ast.walk(tree))
    script = '''import sys
import runevidence, runevidence.cli, runevidence.checks, runevidence.edit
import runevidence_hydra, runevidence_mlflow
assert not any('runevidence.' + n in sys.modules for n in (
    'directory_review_path', 'directory_review', 'directory_present', 'directory_batch', 'directory_audit',
    'directory_plan', 'directory_preview', 'directory_authoring', 'directory_apply',
    'directory_cooperative_apply', 'directory_scanner', 'directory_hashing'))
'''
    p = subprocess.run([sys.executable, '-B', '-c', script], cwd=tmp_path, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout+p.stderr
