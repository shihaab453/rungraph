"""Synthetic-only persistence characterizations, not a write-safety proof.

The one replace hook controls a local interleaving and delegates to the real
filesystem operation. No test here establishes live multi-process safety or
custom-DACL preservation. All writer entry points are fixture-guarded.
"""

from __future__ import annotations

import ast
import ctypes
import hashlib
import inspect
import json
import os
import tempfile
from pathlib import Path

import pytest

from conftest import native_test
from runevidence import directory_apply as checked
from runevidence import directory_cooperative_apply as cooperative
from runevidence import directory_scanner, manifest
from runevidence.directory_authoring import propose_add_directory, propose_rehash_directory
from runevidence.models import MANIFEST_FILENAME, Manifest

native = native_test


def fingerprint(value):
    return {"hex": value.hex(), "sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}


class Synthetic:
    def __init__(self, root):
        self.root = root
        self.path = root / MANIFEST_FILENAME
        self.raw = {"schema_version": "0.1", "project": {"id": "synthetic", "name": "A"}, "artifacts": []}
        Manifest.model_validate(self.raw)
        self.a = manifest.dump_raw(self.raw).encode("utf-8")
        self.b = manifest.dump_raw(dict(self.raw, project=dict(self.raw["project"], name="B competitor"))).encode("utf-8")
        self.c_raw = dict(self.raw, project=dict(self.raw["project"], name="C proposed"))
        self.c = manifest.dump_raw(self.c_raw).encode("utf-8")
        self.events = []

    def guard(self, path):
        path = Path(path)
        assert path == self.path and path.parent == self.root
        assert self.root.is_dir() and not self.root.is_symlink()

    def event(self, name, **details):
        self.events.append({"order": len(self.events), "event": name, **details})

    def competitor(self, content, *, replace=False):
        """Deliberately no lock call. Only this fresh registered target is used."""
        self.guard(self.path)
        if replace:
            incoming = self.root / "competitor.incoming"
            with incoming.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(incoming, self.path)
        else:
            self.path.write_bytes(content)
        assert self.path.read_bytes() == content
        self.event("competitor-real-replacement" if replace else "competitor-real-write",
                   observed=fingerprint(self.path.read_bytes()), cooperative_lock_acquired=False)

    def receipt(self, outcome, **details):
        record = {"target": str(self.path), "A": fingerprint(self.a), "B": fingerprint(self.b),
                  "C": fingerprint(self.c), "events": self.events, "outcome": outcome,
                  "final": fingerprint(self.path.read_bytes()) if self.path.exists() else None, **details}
        (self.root / "receipt.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        print("S3P3P7 " + json.dumps(record, sort_keys=True))


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    user_temp = Path(tempfile.gettempdir()).resolve()
    assert tmp_path.resolve().is_relative_to(user_temp)
    assert not tmp_path.is_symlink()
    root = tmp_path / "s"
    root.mkdir(exist_ok=False)
    fixture = Synthetic(root.resolve())
    fixture.path.write_bytes(fixture.a)
    (fixture.root / "data").mkdir()
    (fixture.root / "data" / "member.bin").write_bytes(b"synthetic member\x00\xff")
    (fixture.root / "sentinel.bin").write_bytes(b"untouched sentinel")
    untouched = {p: p.read_bytes() for p in (fixture.root / "data" / "member.bin", fixture.root / "sentinel.bin")}
    real_raw, real_manifest = manifest.save_raw, manifest.save_manifest

    def guarded_raw(data, path, *, expected_bytes):
        fixture.guard(path)
        return real_raw(data, path, expected_bytes=expected_bytes)

    def guarded_manifest(data, path):
        fixture.guard(path)
        return real_manifest(data, path)

    monkeypatch.setattr(manifest, "save_raw", guarded_raw)
    monkeypatch.setattr(checked, "save_raw", guarded_raw)
    monkeypatch.setattr(manifest, "save_manifest", guarded_manifest)
    yield fixture
    assert {p: p.read_bytes() for p in untouched} == untouched
    assert not list(fixture.root.glob(f".{MANIFEST_FILENAME}.*.tmp"))
    assert not (fixture.root / "competitor.incoming").exists()


def test_controlled_save_raw_loses_nonparticipating_write_after_compare(synthetic, monkeypatch):
    s = synthetic
    original_replace = os.replace

    def interleave(source, destination):
        s.guard(destination)
        staged = Path(source)
        assert staged.parent == s.root and staged.name.startswith(f".{MANIFEST_FILENAME}.")
        assert staged.read_bytes() == s.c
        assert s.path.read_bytes() == s.a
        s.event("replace-entry-after-genuine-expected-byte-comparison", staged=fingerprint(staged.read_bytes()))
        s.competitor(s.b)
        s.event("delegate-to-original-os.replace")
        result = original_replace(source, destination)
        s.event("genuine-replace-returned", result=repr(result), observed=fingerprint(s.path.read_bytes()))
        return result

    # Only this test hooks replacement. The real compare and all reads remain intact.
    with monkeypatch.context() as patch:
        patch.setattr(manifest.os, "replace", interleave)
        result = manifest.save_raw(s.c_raw, s.path, expected_bytes=s.a)
    assert result is None
    assert s.path.read_bytes() == s.c and s.path.read_bytes() != s.b
    assert [e["event"] for e in s.events] == [
        "replace-entry-after-genuine-expected-byte-comparison", "competitor-real-write",
        "delegate-to-original-os.replace", "genuine-replace-returned",
    ]
    s.receipt("returned None; competing B lost; final C", classification="controlled-local-real-filesystem")


def test_native_stale_before_compare_refuses_and_cleans_staging(synthetic):
    s = synthetic
    s.competitor(s.b)
    with pytest.raises(OSError, match="Manifest changed since it was loaded") as error:
        manifest.save_raw(s.c_raw, s.path, expected_bytes=s.a)
    assert s.path.read_bytes() == s.b
    s.receipt("stale refused; B retained", exception=repr(error.value))


def test_native_missing_source_refuses_and_cleans_staging(synthetic):
    s = synthetic
    s.path.unlink()
    with pytest.raises(FileNotFoundError) as error:
        manifest.save_raw(s.c_raw, s.path, expected_bytes=s.a)
    assert not s.path.exists()
    s.receipt("missing source refused; no manifest created", exception=repr(error.value))


def test_native_real_aba_is_invisible_to_byte_equality(synthetic):
    s = synthetic
    assert s.path.read_bytes() == s.a
    s.event("initial-A", observed=fingerprint(s.path.read_bytes()))
    s.competitor(s.b)
    s.competitor(s.a)
    assert manifest.save_raw(s.c_raw, s.path, expected_bytes=s.a) is None
    assert s.path.read_bytes() == s.c
    s.receipt("actual A to B to A accepted; final C", limitation="byte equality has no history/version information")


@native
@pytest.mark.parametrize("operation", ["add", "rehash"])
@pytest.mark.parametrize("route", ["checked", "cooperative"])
def test_controlled_native_rescan_then_competitor_replacement_is_stale(synthetic, monkeypatch, operation, route):
    s = synthetic
    # The public release policy promotes ext4 since S3a; the conftest seam
    # runs this test under the candidate-only qualification policy and gate.
    assert directory_scanner.RELEASE_QUALIFIED_FSTYPES == frozenset({"ext4"})
    loaded = manifest.load_manifest(s.path)
    if operation == "rehash":
        initial = propose_add_directory(loaded, manifest.load_raw(loaded), path="data", artifact_id="data")
        # Seed this separate synthetic fixture, never a checkout manifest.
        s.guard(s.path)
        s.path.write_bytes(manifest.dump_raw(initial.raw).encode("utf-8"))
        loaded = manifest.load_manifest(s.path)
        (s.root / "data" / "added.bin").write_bytes(b"rehash change")
        proposal = propose_rehash_directory(loaded, manifest.load_raw(loaded), artifact_id="data")
    else:
        proposal = propose_add_directory(loaded, manifest.load_raw(loaded), path="data", artifact_id="data")
    s.a = loaded.source_bytes
    s.b = manifest.dump_raw(dict(manifest.load_raw(loaded), project=dict(s.raw["project"], name="B competitor"))).encode("utf-8")
    s.c_raw, s.c = proposal.raw, manifest.dump_raw(proposal.raw).encode("utf-8")
    original_scan, guarded_raw = directory_scanner.scan_directory, checked.save_raw
    scans = []

    def scan(root, *args, **kwargs):
        assert Path(root) == s.root / "data"
        result = original_scan(root, *args, **kwargs)
        scans.append(result)
        s.event("fresh-native-rescan-returned", digest=result.digest.sha256)
        return result

    def compete_then_save(data, path, *, expected_bytes):
        s.guard(path)
        assert expected_bytes == s.a and manifest.dump_raw(data).encode("utf-8") == s.c
        assert len(scans) == (2 if route == "cooperative" else 1)
        lock = cooperative.lock_path_for(s.path)
        if route == "cooperative":
            assert lock.is_file()
            content = lock.read_bytes()
            # Preserve the actual held lock's content before normal production release.
            (s.root / "held-lock.bin").write_bytes(content)
            st = lock.lstat()
            s.event("cooperative-lock-observed-held", content=fingerprint(content), ino=st.st_ino)
        else:
            assert not lock.exists()
        s.event("checked-save-entry-after-native-rescan")
        s.competitor(s.b, replace=True)
        return guarded_raw(data, path, expected_bytes=expected_bytes)

    monkeypatch.setattr(directory_scanner, "scan_directory", scan)
    monkeypatch.setattr(checked, "save_raw", compete_then_save)
    apply = checked.apply_directory_proposal if route == "checked" else cooperative.apply_directory_proposal_cooperatively
    with pytest.raises(checked.DirectoryApplyError) as error:
        apply(loaded, proposal, dry_run=False)
    assert error.value.reason == "stale-source"
    assert s.path.read_bytes() == s.b
    assert not cooperative.lock_path_for(s.path).exists()
    s.receipt("stale-source refused after fresh native scan; B retained", exception=repr(error.value),
              route=route, operation=operation, native_rescans=len(scans),
              limitation="competitor bypasses lock; final guard catches only changes before comparison")


def read_dacl(path):
    """Read DACL information only; no security descriptor setter is called."""
    from ctypes import wintypes

    api = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get = api.GetFileSecurityW
    get.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    get.restype = wintypes.BOOL
    convert = api.ConvertSecurityDescriptorToStringSecurityDescriptorW
    convert.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                        ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD)]
    convert.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    size = wintypes.DWORD()
    get(str(path), 4, None, 0, ctypes.byref(size))  # DACL_SECURITY_INFORMATION
    if not size.value:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_string_buffer(size.value)
    if not get(str(path), 4, buffer, size.value, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    text = wintypes.LPWSTR()
    if not convert(buffer, 1, 4, ctypes.byref(text), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return text.value
    finally:
        kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))


@pytest.mark.skipif(os.name != "nt", reason="Windows read-only DACL APIs")
@pytest.mark.parametrize("writer", ["save_raw", "save_manifest"])
def test_native_default_dacl_staging_observation_only(synthetic, monkeypatch, writer):
    s = synthetic
    try:
        before = read_dacl(s.path)
    except (OSError, AttributeError) as error:
        s.receipt("DACL read unavailable", exception=repr(error))
        pytest.skip(f"read-only DACL API unavailable: {error}")
    real_temporary = manifest.tempfile.NamedTemporaryFile
    staged = []

    def observe_temporary(*args, **kwargs):
        assert Path(kwargs["dir"]) == s.root
        stream = real_temporary(*args, **kwargs)
        try:
            staged.append({"path": stream.name, "dacl": read_dacl(Path(stream.name))})
        except BaseException:
            stream.close()
            Path(stream.name).unlink(missing_ok=True)
            raise
        return stream

    monkeypatch.setattr(manifest.tempfile, "NamedTemporaryFile", observe_temporary)
    if writer == "save_raw":
        manifest.save_raw(s.c_raw, s.path, expected_bytes=s.a)
    else:
        model = Manifest.model_validate(s.c_raw)
        s.c = manifest.dump_manifest(model).encode("utf-8")
        manifest.save_manifest(model, s.path)
    after = read_dacl(s.path)
    assert len(staged) == 1 and after == staged[0]["dacl"]
    s.receipt("default-only DACL observation; custom preservation unproven", writer=writer,
              original_dacl=before, staged=staged, final_dacl=after,
              limitation="no custom DACL created or changed; defaults cannot prove custom-DACL preservation")


def test_source_writer_has_no_windows_security_descriptor_transfer():
    # This is source evidence about these exact writers, not an OS guarantee.
    for writer in (manifest.save_raw, manifest.save_manifest):
        source = inspect.getsource(writer)
        tree = ast.parse(source)
        calls = [ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)]
        assert "tempfile.NamedTemporaryFile" in calls and "os.replace" in calls
        assert not any("security" in name.lower() or "acl" in name.lower() for name in calls)
        modes = [n for n in ast.walk(tree) if isinstance(n, ast.If) and ast.unparse(n.test) == "os.name != 'nt'"]
        assert len(modes) == 1 and "os.chmod" in ast.unparse(modes[0])
    assert "expected_bytes=loaded.source_bytes" in inspect.getsource(checked.apply_directory_proposal)
