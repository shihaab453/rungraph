"""In-memory defensive contract tests; native cases use disposable fixtures.

Fabricated frozen records establish formatting behavior, never authentication
or native scanner security. Native scans occur only before the pure call.
"""

from __future__ import annotations

import builtins
from contextlib import contextmanager
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import runevidence
from runevidence import directory_audit as audit, directory_batch as batch
from runevidence import directory_preview as preview_module
from runevidence.directory_present import (
    DirectoryPresentationError, MAX_RENDER_BYTES, render_directory_review as render,
)
from runevidence.directory_preview import DirectoryPreview, DirectoryPreviewIds
from runevidence.edit import EditError
from test_directory_batch import fixture
from test_directory_preview import VECTORS, native, snapshot


def supplied_preview(**changes):
    """Fabricated data only: does not imply any checks ran."""
    empty = DirectoryPreviewIds((), 0, 0, False)
    seed = DirectoryPreview('rehash', 'unchanged', 'data', 'data', False,
                            'a'*64, 'a'*64, 5, 5, 1, empty, empty, empty,
                            preview_module._CAVEATS)
    return replace(seed, **changes)


def supplied_batch(*previews, **changes):
    report = batch.DirectoryBatchReview(
        'advisory' if previews else 'no-requests', previews, len(previews),
        sum(p.operation == 'add' for p in previews), sum(p.operation == 'rehash' for p in previews),
        sum(p.status == 'would-change' for p in previews), sum(p.status == 'unchanged' for p in previews),
        batch._CAVEATS,
    )
    return replace(report, **changes)


def supplied_audit(*entries, **changes):
    refused = sum(e.status == 'refused' for e in entries)
    report = audit.DirectoryAudit(
        'refused' if refused else 'advisory' if entries else 'no-directory-records', entries,
        sum(e.status == 'unchanged' for e in entries), sum(e.status == 'would-change' for e in entries),
        refused, len(entries), audit._CAVEATS,
    )
    return replace(report, **changes)


def supplied_entry(preview):
    return audit.DirectoryAuditEntry(preview.artifact_id, False, preview.status, preview, None, None, None)


def refused_entry(**changes):
    seed = audit.DirectoryAuditEntry('lost', False, 'refused', None, 'scan-failed', 'scan-failed', 'missing')
    return replace(seed, **changes)


def assert_caveats(text, report):
    for caveat in report.caveats:
        assert 'text='+json.dumps(caveat, ensure_ascii=True) in text
    previews = report.previews if type(report) is batch.DirectoryBatchReview else tuple(
        e.preview for e in report.entries if e.preview is not None)
    for p in previews:
        for caveat in p.caveats:
            assert 'text='+json.dumps(caveat, ensure_ascii=True) in text
    for phrase in ('supplied advisory data', 'not independent verification', 'authentication of origin',
                   'no fresh source/tree check', 'never grants write authorization', 'forged or stale'):
        assert phrase in text


@contextmanager
def pure_call(monkeypatch):
    """Forbid all relevant Python I/O, scan, planning and persistence routes."""
    # Resolve modules before protecting the call, so no lazy import needs I/O.
    from runevidence import directory_apply, directory_authoring, directory_cooperative_apply
    from runevidence import directory_plan, directory_scanner, manifest
    import tempfile

    calls = []

    def forbid(*args, **kwargs):
        calls.append(True)
        raise AssertionError('renderer performed I/O or invoked an upstream operation')

    with monkeypatch.context() as m:
        for module, names in (
            (builtins, ('open', 'print')), (io, ('open',)),
            (os, ('open', 'stat', 'lstat', 'listdir', 'scandir', 'read', 'write', 'readlink',
                  'replace', 'rename', 'unlink', 'remove', 'mkdir', 'makedirs', 'rmdir')),
            (Path, ('open', 'read_bytes', 'read_text', 'write_bytes', 'write_text', 'stat', 'lstat',
                    'exists', 'is_file', 'is_dir', 'iterdir', 'glob', 'rglob', 'unlink', 'rename', 'replace')),
            (tempfile, ('NamedTemporaryFile', 'mkstemp')),
            (manifest, ('load_raw', 'load_manifest', 'save_raw', 'save_manifest', '_write')),
            (directory_scanner, ('scan_directory',)),
            (directory_plan, ('plan_directory_change', 'load_raw')),
            (audit, ('audit_recorded_directories', 'plan_directory_change', 'load_raw')),
            (batch, ('review_directory_changes', 'plan_directory_change', 'load_raw')),
            (preview_module, ('preview_directory_proposal', 'apply_directory_proposal')),
            (directory_authoring, ('propose_add_directory', 'propose_rehash_directory')),
            (directory_apply, ('apply_directory_proposal', 'save_raw')),
            (directory_cooperative_apply, ('apply_directory_proposal_cooperatively', 'lock_path_for')),
        ):
            for name in names:
                m.setattr(module, name, forbid)
        yield
    assert calls == []


@pytest.mark.parametrize('report', [supplied_audit(), supplied_batch(),
                                    supplied_audit(refused_entry()), supplied_batch(supplied_preview())])
def test_in_memory_empty_refused_and_advisory_pure_calls(report, monkeypatch, capsys):
    with pure_call(monkeypatch):
        text = render(report)
        again = render(report)
    assert type(text) is str and text == again
    assert len(text.encode('utf-8')) <= MAX_RENDER_BYTES
    assert text.isascii() and '\r' not in text
    assert_caveats(text, report)
    assert report.state in text
    assert capsys.readouterr() == ('', '')


def test_in_memory_mixed_audit_preserves_refusal_and_order():
    same = supplied_preview(artifact_id='z')
    changed = supplied_preview(artifact_id='a', status='would-change', new_sha256='b'*64, new_size=7)
    report = supplied_audit(supplied_entry(same), refused_entry(), supplied_entry(changed))
    text = render(report)
    assert 'audit state="refused" total=3 unchanged=1 would_change=1 refused=1' in text
    assert text.index('artifact_id="z"') < text.index('artifact_id="lost"') < text.index('artifact_id="a"')
    assert 'refusal reason="scan-failed" authoring_reason="scan-failed" scan_reason="missing"' in text
    assert 'status="unchanged"' in text and 'status="would-change"' in text
    assert_caveats(text, report)
    assert 'all-clear inference' in text


def test_in_memory_ordered_batch_details_and_separate_identifier_groups():
    groups = [DirectoryPreviewIds((name,), 1, 0, False) for name in ('result-id', 'claim-id', 'file-id')]
    added = supplied_preview(operation='add', status='would-change', artifact_id='m', old_sha256=None,
                             old_size=None, linked_results=groups[0], claims=groups[1], overlapping_files=groups[2])
    report = supplied_batch(supplied_preview(artifact_id='z'), added,
                            supplied_preview(artifact_id='a', status='would-change', new_sha256='b'*64))
    text = render(report)
    assert 'batch state="advisory" total=3 adds=1 rehashes=2 would_change=2 unchanged=1' in text
    assert text.index('artifact_id="z"') < text.index('artifact_id="m"') < text.index('artifact_id="a"')
    assert 'digest old=null new="'+'a'*64+'"' in text
    assert 'logical-size old=null new=5 members=1' in text
    for name, group in zip(('linked-results', 'claims', 'child-file-overlaps'), groups):
        assert 'impact group='+json.dumps(name)+' ids='+json.dumps(group.ids)+' shown=1 total=1 omitted=0 truncated=false' in text
    assert 'identifiers only' in text and 'no cumulative manifest or transaction' in text
    assert_caveats(text, report)


def test_in_memory_terminal_escaping_clipping_and_no_synthetic_status_lines():
    attack = 'label\r\nbatch state="applied"\t\x1b[2J\u2028\u2029"\\\x00\x7f'
    ids = DirectoryPreviewIds((attack, *tuple('x'*157+'...' for _ in range(15))), 27, 11, True)
    p = supplied_preview(artifact_id=attack, path='p'*1021+'...', display_truncated=True,
                         linked_results=ids, claims=ids, overlapping_files=ids)
    report = supplied_batch(p)
    text = render(report)
    assert json.dumps(attack, ensure_ascii=True) in text
    assert text.isascii() and not any(c in text for c in '\r\t\x1b\x00\x7f')
    assert len([s for s in text.splitlines() if s.startswith('batch ')]) == 1
    assert 'display_truncated=true' in text
    assert text.count('shown=16 total=27 omitted=11 truncated=true') == 3
    assert 'Display strings may be truncated' in text
    assert_caveats(text, report)


class Hostile:
    def __str__(self):
        raise AssertionError('arbitrary __str__ called')

    def __repr__(self):
        raise AssertionError('arbitrary __repr__ called')

    def __eq__(self, other):
        raise AssertionError('arbitrary comparison called')


def invalid(report, monkeypatch, reason='invalid-report'):
    with pure_call(monkeypatch), pytest.raises(DirectoryPresentationError) as info:
        render(report)
    assert isinstance(info.value, EditError)
    assert info.value.reason == reason
    assert str(info.value) == 'Directory presentation refused: '+reason
    assert info.value.__cause__ is None


@pytest.mark.parametrize('change', [
    {'operation': Hostile()}, {'status': 'written'}, {'artifact_id': Hostile()}, {'path': Hostile()},
    {'artifact_id': ''}, {'path': 'x'*1025}, {'display_truncated': 1}, {'display_truncated': True},
    {'old_sha256': Hostile()}, {'new_sha256': 'SECRET'}, {'new_sha256': 'A'*64}, {'new_sha256': Hostile()},
    {'old_size': -1}, {'new_size': True}, {'new_size': Hostile()}, {'members': -1},
    {'new_size': 6}, {'members': 0}, {'status': 'would-change'},
    {'caveats': ()}, {'caveats': (Hostile(),)},
    {'linked_results': Hostile()}, {'claims': None}, {'overlapping_files': ()},
    {'operation': 'add'},
])
def test_in_memory_malformed_preview_refuses_without_echo(change, monkeypatch):
    p = supplied_preview(**change)
    # Keep report counts valid so rejection exercises the nested record.
    invalid(replace(supplied_batch(supplied_preview()), previews=(p,)), monkeypatch)


@pytest.mark.parametrize('change', [
    {'ids': []}, {'ids': (Hostile(),)}, {'ids': ('x'*161,)}, {'ids': ('',)},
    {'ids': ('z', 'a'), 'total': 2}, {'total': -1}, {'total': True}, {'omitted': -1},
    {'omitted': Hostile()}, {'total': 2}, {'total': 18, 'omitted': 17}, {'truncated': 1},
    {'truncated': True}, {'ids': ('same', 'same'), 'total': 2},
])
def test_in_memory_impossible_impact_shape_refuses(change, monkeypatch):
    group = replace(DirectoryPreviewIds(('x',), 1, 0, False), **change)
    invalid(supplied_batch(supplied_preview(claims=group)), monkeypatch)


@pytest.mark.parametrize('kind,change', [
    ('batch', {'total': -1}), ('batch', {'total': True}), ('batch', {'adds': 1}),
    ('batch', {'rehashes': 1}), ('batch', {'unchanged': 1}), ('batch', {'would_change': 1}),
    ('batch', {'state': 'advisory'}), ('batch', {'previews': []}), ('batch', {'caveats': ()}),
    ('audit', {'total': 1}), ('audit', {'unchanged': -1}), ('audit', {'would_change': True}),
    ('audit', {'refused': 1}), ('audit', {'state': 'refused'}), ('audit', {'entries': []}),
    ('audit', {'caveats': ()}), ('audit', {'caveats': Hostile()}),
])
def test_in_memory_inconsistent_report_refuses(kind, change, monkeypatch):
    report = supplied_batch() if kind == 'batch' else supplied_audit()
    invalid(replace(report, **change), monkeypatch)


@pytest.mark.parametrize('change', [
    {'preview': supplied_preview()}, {'reason': None}, {'reason': 'SECRET'},
    {'authoring_reason': Hostile()}, {'scan_reason': Hostile()}, {'scan_reason': 'SECRET'},
    {'display_truncated': 1}, {'display_truncated': True}, {'status': 'unchanged'},
    {'artifact_id': Hostile()},
])
def test_in_memory_inconsistent_refusal_refuses(change, monkeypatch):
    invalid(supplied_audit(refused_entry(**change)), monkeypatch)


def test_in_memory_wrong_types_missing_fields_subclasses_and_entry_mismatch(monkeypatch):
    class Subclass(batch.DirectoryBatchReview):
        pass

    missing = object.__new__(batch.DirectoryBatchReview)
    p = supplied_preview()
    records = [Hostile(), None, {}, missing,
               Subclass('no-requests', (), 0, 0, 0, 0, 0, batch._CAVEATS),
               supplied_audit(replace(supplied_entry(p), artifact_id='other')),
               supplied_audit(replace(supplied_entry(p), reason='refused')),
               supplied_audit(replace(supplied_entry(p), status='would-change')),
               supplied_batch(*(p for _ in range(9))),
               supplied_audit(*(refused_entry() for _ in range(17)))]
    for report in records:
        invalid(report, monkeypatch)


def test_in_memory_complete_utf8_capacity_exact_boundary_and_large_integer(monkeypatch):
    report = supplied_batch()
    length = len(render(report).encode('utf-8'))
    # Additional supplied caveats are retained and escaped, never dropped.
    extra_overhead = len(render(replace(report, caveats=report.caveats+('x',))).encode())-length-1
    padding = MAX_RENDER_BYTES-length-extra_overhead
    boundary = replace(report, caveats=report.caveats+('x'*padding,))
    assert len(render(boundary).encode()) == MAX_RENDER_BYTES
    invalid(replace(report, caveats=report.caveats+('x'*(padding+1),)), monkeypatch, 'render-capacity-exceeded')
    invalid(replace(report, caveats=report.caveats+('\uffff'*MAX_RENDER_BYTES,)), monkeypatch, 'render-capacity-exceeded')
    # Numeric conversion never depends on the process decimal-string limit.
    huge = supplied_preview(status='would-change', new_size=10**5000)
    text = render(supplied_batch(huge))
    assert 'new=1'+'0'*5000+' members=1' in text
    invalid(supplied_batch(replace(huge, new_size=1 << (MAX_RENDER_BYTES*4+1))),
            monkeypatch, 'render-capacity-exceeded')


@native
@pytest.mark.parametrize('vector', VECTORS, ids=[v['name'] for v in VECTORS])
def test_native_quiet_tree_vectors_add_rehash_audit_then_strict_pure_display(tmp_path, monkeypatch, vector):
    loaded = fixture(tmp_path, recorded=('recorded',), added=('added',), vector=vector)
    # Native checked report construction is fixture setup, outside pure_call.
    reviewed = batch.review_directory_changes(loaded, (batch.DirectoryChangeRequest('add', 'added', 'added'),
                                                      batch.DirectoryChangeRequest('rehash', 'recorded')))
    audited = audit.audit_recorded_directories(loaded)
    before = snapshot(tmp_path)
    with pure_call(monkeypatch):
        batch_text, audit_text = render(reviewed), render(audited)
        assert batch_text == render(reviewed) and audit_text == render(audited)
    assert snapshot(tmp_path) == before
    for report, text in ((reviewed, batch_text), (audited, audit_text)):
        assert vector['sha256'] in text
        assert 'new='+str(vector['size_bytes'])+' members='+str(len(vector['layout'])) in text
        assert 'shown=0 total=0 omitted=0 truncated=false' in text
        assert_caveats(text, report)


@native
def test_native_changed_unchanged_refused_and_linked_impact_without_values(tmp_path, monkeypatch):
    from test_directory_preview import raw_manifest
    raw = raw_manifest()
    raw['artifacts'] = [dict(id='child-file-id', path='same/a', sha256='0'*64)]
    raw['results'] = [dict(id='linked-result-id', run_id='run', name='metric',
                           artifact_id='same', value=12345.6789)]
    raw['evidence'] = [dict(id='claim-id', claim='SECRET CLAIM TEXT', supports=['linked-result-id'])]
    loaded = fixture(tmp_path, recorded=('same', 'changed', 'lost'), added=('added',), raw=raw)
    (tmp_path/'changed/a').write_bytes(b'new disposable content')
    # Missing fixture member/root produces an ordinary read-only audit refusal.
    (tmp_path/'lost/a').unlink()
    (tmp_path/'lost').rmdir()
    reviewed = batch.review_directory_changes(loaded, tuple(batch.DirectoryChangeRequest('rehash', n)
                                                            for n in ('same', 'changed')) +
                                             (batch.DirectoryChangeRequest('add', 'added', 'added'),))
    audited = audit.audit_recorded_directories(loaded)
    before = snapshot(tmp_path)
    with pure_call(monkeypatch):
        batch_text, audit_text = render(reviewed), render(audited)
    assert snapshot(tmp_path) == before
    assert 'total=3 adds=1 rehashes=2 would_change=2 unchanged=1' in batch_text
    assert 'total=3 unchanged=1 would_change=1 refused=1' in audit_text
    assert 'scan_reason="missing"' in audit_text
    for text in (batch_text, audit_text):
        for identifier in ('linked-result-id', 'claim-id', 'child-file-id'):
            assert identifier in text
        assert 'SECRET CLAIM TEXT' not in text and '12345.6789' not in text
        assert 'same/a' not in text and 'new disposable content' not in text
        assert 'shown=1 total=1 omitted=0 truncated=false' in text
    for report, text in ((reviewed, batch_text), (audited, audit_text)):
        assert_caveats(text, report)


@native
@pytest.mark.parametrize('missing', [('sha256',), ('size_bytes',), ('sha256', 'size_bytes')])
def test_native_missing_old_measurements_display_absent_values(tmp_path, monkeypatch, missing):
    from runevidence.manifest import load_raw
    from test_directory_preview import write_manifest
    loaded = fixture(tmp_path, recorded=('data',))
    raw = load_raw(loaded)
    for field in missing:
        raw['artifacts'][0].pop(field)
    loaded = write_manifest(tmp_path, raw)
    reviewed = batch.review_directory_changes(loaded, (batch.DirectoryChangeRequest('rehash', 'data'),))
    audited = audit.audit_recorded_directories(loaded)
    before = snapshot(tmp_path)
    with pure_call(monkeypatch):
        texts = (render(reviewed), render(audited))
    assert snapshot(tmp_path) == before
    for text in texts:
        assert 'status="would-change"' in text
        if 'sha256' in missing:
            assert 'digest old=null' in text
        if 'size_bytes' in missing:
            assert 'logical-size old=null' in text


def test_recursive_exact_importers_and_fresh_public_startup(tmp_path):
    src = Path(runevidence.__file__).parent
    for module in ('directory_audit', 'directory_batch'):
        users = [p.relative_to(src).as_posix() for p in src.rglob('*.py')
                 if p.name != module+'.py' and module in p.read_text(encoding='utf-8')]
        assert sorted(users) == ['directory_present.py', 'directory_review.py']
    users = [p.relative_to(src).as_posix() for p in src.rglob('*.py')
             if p.name != 'directory_present.py' and 'directory_present' in p.read_text(encoding='utf-8')]
    assert users == ['directory_review.py']
    script = '''import sys
import runevidence, runevidence.cli, runevidence.checks, runevidence.edit
import runevidence_hydra, runevidence_mlflow
assert not any('runevidence.' + n in sys.modules for n in (
    'directory_present', 'directory_batch', 'directory_audit', 'directory_plan',
    'directory_preview', 'directory_authoring', 'directory_apply',
    'directory_cooperative_apply', 'directory_scanner', 'directory_hashing'))
'''
    result = subprocess.run([sys.executable, '-B', '-c', script], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout+result.stderr
