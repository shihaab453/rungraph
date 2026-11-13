"""Existing-path writer characterization, invented guarded Temp fixtures only."""
import hashlib
import json
import os
import uuid
from pathlib import Path

import pytest

from runevidence import _experimental_staged_reader as reader
from runevidence import _experimental_versioned_manifest as stage
from runevidence import manifest as writer


@pytest.fixture
def scratch():
    root = Path(os.environ['R5_EVIDENCE_ROOT'])
    assert root.is_absolute() and root.parent == Path(os.environ['LOCALAPPDATA']) / 'Temp'
    p = root / ('runevidence-staging-test-' + uuid.uuid4().hex)
    p.mkdir()
    return p


def source(parent):
    p = parent / 'manifest.json'
    p.write_bytes((json.dumps({'project': {'id': 'r11.synthetic', 'name': 'Before'},
                               'runs': [], 'artifacts': []}, indent=2) + '\n').encode())
    return writer.load_manifest(p)


def meta(p):
    if not p.exists():
        return None
    s = p.stat()
    b = p.read_bytes()
    return {'size': s.st_size, 'mtime_ns': s.st_mtime_ns, 'inode': s.st_ino,
            'mode': s.st_mode, 'attrs': getattr(s, 'st_file_attributes', None),
            'nlink': s.st_nlink, 'reparse': getattr(s, 'st_reparse_tag', None),
            'sha256': hashlib.sha256(b).hexdigest()}


def traced_save(monkeypatch, model, target, parent):
    assert os.name == 'nt' and target.parent == parent and target.is_file()
    events = []
    original_temp = writer.tempfile.NamedTemporaryFile
    original_fsync, original_replace, original_unlink = os.fsync, os.replace, Path.unlink
    temp = []
    def checked(p):
        p = Path(p)
        assert p.is_absolute() and p.parent == parent and (p == target or p in temp)
        return p
    class Wrapped:
        def __init__(self, fh): self.fh = fh
        def __enter__(self): self.fh.__enter__(); events.append({'event': 'enter'}); return self
        def __exit__(self, *args):
            result = self.fh.__exit__(*args)
            events.append({'event': 'close', 'temp': meta(temp[0])})
            return result
        @property
        def name(self): return self.fh.name
        def write(self, value):
            events.append({'event': 'write-enter', 'chars': len(value)})
            result = self.fh.write(value)
            events.append({'event': 'write-exit', 'result': result})
            return result
        def flush(self):
            events.append({'event': 'flush-enter'})
            result = self.fh.flush()
            events.append({'event': 'flush-exit', 'temp': meta(temp[0])})
            return result
        def fileno(self): return self.fh.fileno()
    def temporary(*args, **kwargs):
        assert not args and kwargs == {'mode': 'w', 'encoding': 'utf-8', 'newline': '\n',
                                      'dir': parent, 'prefix': '.manifest.json.',
                                      'suffix': '.tmp', 'delete': False}
        events.append({'event': 'temporary-enter'})
        fh = original_temp(*args, **kwargs)
        p = Path(fh.name)
        assert p.is_absolute() and p.parent == parent and p.name.startswith('.manifest.json.') and p.suffix == '.tmp'
        temp.append(p)
        events.append({'event': 'temporary-exit', 'path': str(p), 'temp': meta(p)})
        return Wrapped(fh)
    def sync(fd):
        assert len(temp) == 1 and fd == active[0].fileno()
        events.append({'event': 'fsync-enter'})
        result = original_fsync(fd)
        events.append({'event': 'fsync-exit'})
        return result
    # Retain the one actual file object solely to validate the descriptor.
    active = []
    def make(*args, **kwargs):
        wrapped = temporary(*args, **kwargs)
        active.append(wrapped)
        return wrapped
    def replace(src, dst):
        assert checked(src) == temp[0] and checked(dst) == target
        events.append({'event': 'replace-enter', 'temp': meta(temp[0]), 'target': meta(target)})
        result = original_replace(src, dst)
        events.append({'event': 'replace-exit', 'target': meta(target), 'temp': meta(temp[0])})
        return result
    def unlink(p, *args, **kwargs):
        assert checked(p) == temp[0] and not args and kwargs == {'missing_ok': True}
        events.append({'event': 'unlink-enter', 'temp': meta(p)})
        result = original_unlink(p, *args, **kwargs)
        events.append({'event': 'unlink-exit', 'temp': meta(p)})
        return result
    with monkeypatch.context() as m:
        m.setattr(writer.tempfile, 'NamedTemporaryFile', make)
        m.setattr(os, 'fsync', sync)
        m.setattr(os, 'replace', replace)
        m.setattr(Path, 'unlink', unlink)
        writer.save_manifest(model, target)
    return events, temp


def test_existing_path_stage_refusal(scratch, monkeypatch):
    loaded = source(scratch)
    staged = stage.stage_validated_manifest(loaded, scratch_parent=scratch)
    before = loaded.path.read_bytes(); db = staged.database_path.read_bytes()
    start = {'source': meta(loaded.path), 'db': meta(staged.database_path)}
    model = loaded.manifest.model_copy(deep=True)
    model.project.name = 'After'
    events, temps = traced_save(monkeypatch, model, loaded.path, scratch)
    after = loaded.path.read_bytes()
    assert before != after and json.loads(after)['project']['name'] == 'After'
    assert staged.database_path.read_bytes() == db
    with pytest.raises(reader.StagedReadError) as exc:
        reader.load_staged_for_read(staged)
    assert exc.value.reason == 'stale-source'
    assert all(not p.exists() for p in temps)
    (scratch / 'r11-trace.json').write_text(json.dumps({'before_utf8': before.decode(),
        'after_utf8': after.decode(), 'start': start, 'end': {'source': meta(loaded.path),
        'db': meta(staged.database_path)}, 'events': events, 'refusal': exc.value.diagnostics,
        'allowlist': [str(loaded.path), *map(str, temps)],
        'observer_reads': 'metadata hashes and before/after byte reads are observer reads'}, indent=2), encoding='utf-8')


def test_existing_roundtrip_separate_stage(scratch):
    clean = scratch / 'clean'; clean.mkdir()
    separate = scratch / 'separate'; separate.mkdir()
    loaded = source(clean)
    staged_source = source(separate)
    staged = stage.stage_validated_manifest(staged_source, scratch_parent=scratch)
    before = staged.database_path.read_bytes()
    model = loaded.manifest.model_copy(deep=True); model.project.name = 'Round trip'
    writer.save_manifest(model, loaded.path)
    assert writer.load_manifest(loaded.path).manifest == model
    assert staged.database_path.read_bytes() == before
    assert reader.load_staged_for_read(staged).loaded.source_bytes == staged_source.source_bytes
    staged_model = staged_source.manifest.model_copy(deep=True)
    staged_model.project.name = 'Independent overwrite'
    writer.save_manifest(staged_model, staged_source.path)
    assert staged.database_path.read_bytes() == before
    with pytest.raises(reader.StagedReadError) as exc:
        reader.load_staged_for_read(staged)
    assert exc.value.reason == 'stale-source'


def test_invalid_model_before_temporary_file(scratch):
    loaded = source(scratch)
    before = loaded.path.read_bytes(); entries = set(scratch.iterdir())
    with pytest.raises(AttributeError):
        writer.save_manifest(object(), loaded.path)
    assert loaded.path.read_bytes() == before and set(scratch.iterdir()) == entries
