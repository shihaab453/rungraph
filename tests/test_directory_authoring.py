"""Internal directory add/rehash proposals (development only).

No command reaches this module. Every folder is a synthetic tree under
tmp_path, and the code under test never writes a manifest or any other file.
"""

from __future__ import annotations

import builtins
import copy
import dataclasses
import hashlib
import io
import json
import os
import stat
from pathlib import Path

import pytest

import runevidence
from conftest import make_dir_link, native_test, remove_dir_link
from runevidence.checks import run_checks
from runevidence.directory_authoring import (
    DirectoryAuthoringError,
    DirectoryProposal,
    propose_add_directory,
    propose_rehash_directory,
)
from runevidence.directory_hashing import DIRECTORY, FILE, DirectoryDigest, DirectoryRecord, directory_digest
from runevidence.directory_scanner import DirectoryScan, DirectoryScanError
from runevidence.edit import EditError
from runevidence.findings import Code
from runevidence.manifest import LoadedManifest, dump_raw, load_manifest, load_raw
from runevidence.models import DIRECTORY_DIGEST_FORMAT, MANIFEST_FILENAME, Artifact, Manifest

VECTORS = json.loads(
    (Path(__file__).parent / "data" / "directory_digest_vectors.json").read_bytes().decode("utf-8")
)["vectors"]

native = native_test


def base_raw(*artifacts, results=(), evidence=()):
    # Keys in a hand-written order, to show that a proposal keeps them.
    return {
        "project": {"name": "Demo", "id": "demo"},
        "schema_version": "0.1",
        "runs": [{"id": "run", "name": "baseline"}],
        "artifacts": [{"path": "notes.txt", "kind": "doc", "id": "notes"}, *artifacts],
        "results": list(results),
        "evidence": list(evidence),
    }


def dir_entry(**changes):
    entry = {"id": "data", "path": "data", "artifact_type": "directory",
             "digest_format": DIRECTORY_DIGEST_FORMAT, "sha256": "a" * 64, "size_bytes": 4}
    entry.update(changes)
    return entry


def loaded_for(root: Path, raw: dict) -> LoadedManifest:
    """An in-memory loaded manifest; nothing is written."""
    return LoadedManifest(Manifest.model_validate(raw), root / MANIFEST_FILENAME, dump_raw(raw).encode("utf-8"))


def frozen(raw) -> str:
    """Order-sensitive canonical JSON, to compare a manifest byte for byte."""
    return json.dumps(raw, ensure_ascii=False)


def add(root, raw, **kwargs):
    kwargs.setdefault("path", "data")
    kwargs.setdefault("artifact_id", "data")
    return propose_add_directory(loaded_for(root, raw), raw, **kwargs)


def rehash(root, raw, artifact_id="data", loaded_raw=None):
    loaded = loaded_for(root, raw if loaded_raw is None else loaded_raw)
    return propose_rehash_directory(loaded, raw, artifact_id=artifact_id)


def refused(reason, call, raw) -> DirectoryAuthoringError:
    before = frozen(raw)
    with pytest.raises(DirectoryAuthoringError) as info:
        call()
    assert info.value.reason == reason, str(info.value)
    assert isinstance(info.value, EditError)
    assert frozen(raw) == before
    return info.value


def build(root: Path, layout) -> None:
    root.mkdir()
    for entry in layout:
        target = root.joinpath(*entry["path"].split("/"))
        if entry["type"] == "D":
            target.mkdir()
        else:
            target.write_bytes(bytes.fromhex(entry["content_hex"]))


@pytest.fixture
def scanner(monkeypatch):
    """Replace the native scanner; records every root it is asked to scan."""
    calls = []
    state = {"outcome": DirectoryScan((), DirectoryDigest("b" * 64, 7, 2)), "calls": calls}

    def scan(root):
        calls.append(Path(root))
        if isinstance(state["outcome"], Exception):
            raise state["outcome"]
        return state["outcome"]

    monkeypatch.setattr("runevidence.directory_scanner.scan_directory", scan)
    return state


# --- root path, before any scan ------------------------------------------------

UNSAFE_ROOTS = [
    "../data", "data/..", "./data", "data/.", "/data", "//server/share/data", "C:/data", "C:data",
    "data\\child", "\\\\server\\share", "data//child", "data/", "data.", "data ", "da*ta", "da?ta",
    "da|ta", "da<ta", 'da"ta', "d\u00e4ta", "da\tta", "con", "CON.txt", "data/aux", "lpt1",
    ".git", "data/.venv", "__pycache__", "runevidence.json", "data/runevidence.json",
    "runevidence.json.bak.2", "", None, 7,
]


@pytest.mark.parametrize("path", UNSAFE_ROOTS)
def test_malformed_root_is_refused_before_any_scan(tmp_path, scanner, path):
    raw = base_raw()
    refused("invalid-path", lambda: add(tmp_path, raw, path=path), raw)
    assert scanner["calls"] == []


@pytest.mark.parametrize("path", ["/".join(["d"] * 65), "x" * 1025])
def test_root_beyond_bounds_is_refused_before_any_scan(tmp_path, scanner, path):
    raw = base_raw()
    refused("limit-exceeded", lambda: add(tmp_path, raw, path=path), raw)
    assert scanner["calls"] == []


@pytest.mark.parametrize("path", ["data", "runs/a b/out-1_v.2", "/".join(["d"] * 64)])
def test_valid_root_is_scanned_at_its_exact_folder(tmp_path, scanner, path):
    proposal = add(tmp_path, base_raw(), path=path)
    assert scanner["calls"] == [tmp_path.absolute().joinpath(*path.split("/"))]
    assert proposal.path == path and proposal.entry["path"] == path


@pytest.mark.parametrize("reason", [
    "missing", "not-a-directory", "unsafe", "invalid-root", "unreadable", "unstable", "limit-exceeded",
    "collision", "unsupported-platform", "unsupported-filesystem",
])
def test_every_scan_failure_is_refused(tmp_path, scanner, reason):
    scanner["outcome"] = DirectoryScanError(reason, "synthetic failure")
    raw = base_raw()
    assert refused("scan-failed", lambda: add(tmp_path, raw), raw).scan_reason == reason
    raw = base_raw(dir_entry())
    assert refused("scan-failed", lambda: rehash(tmp_path, raw), raw).scan_reason == reason


def test_unsupported_backend_is_refused_without_fallback(tmp_path, monkeypatch):
    def unsupported():
        raise DirectoryScanError("unsupported-platform", "synthetic: no native backend")

    # The real scan_directory runs; only its backend choice is replaced.
    monkeypatch.setattr("runevidence.directory_scanner._native_backend", unsupported)
    (tmp_path / "data").mkdir()
    raw = base_raw()
    assert refused("scan-failed", lambda: add(tmp_path, raw), raw).scan_reason == "unsupported-platform"
    raw = base_raw(dir_entry())
    assert refused("scan-failed", lambda: rehash(tmp_path, raw), raw).scan_reason == "unsupported-platform"


# --- add: identity, run and overlap ----------------------------------------------


def test_volatile_add_is_refused_before_any_scan(tmp_path, scanner):
    raw = base_raw()
    refused("volatile-unsupported", lambda: add(tmp_path, raw, volatile=True), raw)
    assert scanner["calls"] == []


def with_ids():
    return base_raw(results=[{"id": "acc", "run_id": "run", "name": "accuracy", "value": 0.9}],
                    evidence=[{"id": "claim", "claim": "it works"}])


@pytest.mark.parametrize("taken", ["demo", "run", "notes", "acc", "claim"])
def test_add_needs_an_unused_id(tmp_path, scanner, taken):
    raw = with_ids()
    assert refused("duplicate-id", lambda: add(tmp_path, raw, artifact_id=taken), raw).subjects == (taken,)
    assert scanner["calls"] == []


def test_add_checks_ids_in_the_loaded_manifest_too(tmp_path, scanner):
    raw = base_raw()
    loaded = loaded_for(tmp_path, base_raw({"id": "older", "path": "older.txt"}))
    refused("duplicate-id",
            lambda: propose_add_directory(loaded, raw, path="data", artifact_id="older"), raw)
    assert scanner["calls"] == []


@pytest.mark.parametrize("changes", [
    {"artifact_id": "-bad"}, {"artifact_id": "a b"}, {"artifact_id": ""}, {"artifact_id": None},
    {"artifact_id": ["data"]}, {"kind": 3}, {"run_id": 5},
])
def test_add_refuses_an_invalid_entry(tmp_path, scanner, changes):
    raw = base_raw()
    refused("invalid-artifact", lambda: add(tmp_path, raw, **changes), raw)
    assert scanner["calls"] == []


@pytest.mark.parametrize("run_id", ["nope", "notes", "demo", ""])
def test_add_needs_an_existing_run(tmp_path, scanner, run_id):
    raw = base_raw()
    refused("unknown-run", lambda: add(tmp_path, raw, run_id=run_id), raw)
    assert scanner["calls"] == []


@pytest.mark.parametrize("kind,run_id", [(None, None), ("", None), ("dataset", "run"), ("測定", "run")])
def test_add_captures_separate_frozen_intent(tmp_path, scanner, kind, run_id):
    proposal = add(tmp_path, base_raw(), kind=kind, run_id=run_id)
    assert (proposal.add_kind, proposal.add_run_id) == (kind, run_id)
    proposal.entry.update(kind="other", run_id=None)
    proposal.raw["artifacts"][-1].update(kind="other", run_id=None)
    assert (proposal.add_kind, proposal.add_run_id) == (kind, run_id)
    for field in ("add_kind", "add_run_id"):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(proposal, field, "other")


@pytest.mark.parametrize("field", ["add_kind", "add_run_id"])
def test_intent_constructor_fields_have_no_compatibility_defaults(tmp_path, scanner, field):
    proposal = add(tmp_path, base_raw())
    arguments = dict(vars(proposal))
    del arguments[field]
    with pytest.raises(TypeError, match=field):
        DirectoryProposal(**arguments)


@pytest.mark.parametrize("existing", [
    {"id": "old", "path": "data"}, {"id": "old", "path": "DATA"}, {"id": "old", "path": "./Data"},
    {"id": "old", "path": "data/"}, {"id": "old", "path": "data\\"}, dir_entry(id="old", path="Data"),
])
def test_add_refuses_a_root_that_is_already_tracked(tmp_path, scanner, existing):
    raw = base_raw(existing)
    assert refused("path-collision", lambda: add(tmp_path, raw), raw).subjects == ("old",)
    assert scanner["calls"] == []


def test_add_refuses_an_absolute_record_of_the_same_root(tmp_path, scanner):
    raw = base_raw({"id": "old", "path": str(tmp_path / "DATA")})
    refused("path-collision", lambda: add(tmp_path, raw), raw)
    assert scanner["calls"] == []


@pytest.mark.parametrize("path,existing", [
    ("data", dir_entry(id="old", path="data/sub")),
    ("data", dir_entry(id="old", path="DATA/Sub/deeper")),
    ("data/sub", dir_entry(id="old", path="data")),
    ("data/sub", {"id": "old", "path": "data"}),  # a file cannot hold a folder
    ("data/sub", {"id": "old", "path": "."}),  # the experiment folder itself
])
def test_add_refuses_overlap(tmp_path, scanner, path, existing):
    raw = base_raw(existing)
    assert refused("overlap", lambda: add(tmp_path, raw, path=path), raw).subjects == ("old",)
    assert scanner["calls"] == []


def test_file_artifacts_inside_the_root_are_kept_and_listed(tmp_path, scanner):
    raw = base_raw({"id": "child", "path": "data/a.txt", "sha256": "0" * 64},
                   {"id": "deep", "path": "data\\sub\\b.txt"},
                   {"id": "beside", "path": "data-extra.txt"})
    proposal = add(tmp_path, raw)
    assert proposal.overlapping_files == ("child", "deep")
    assert frozen(proposal.raw["artifacts"][:-1]) == frozen(raw["artifacts"])
    assert any("child, deep" in note for note in proposal.notes)


@pytest.mark.parametrize("bad", [
    None, [], "{}", {"project": {"id": "demo"}}, {**base_raw(), "extra": 1}, base_raw({"id": "x"}),
])
def test_invalid_raw_is_refused_before_any_scan(tmp_path, scanner, bad):
    loaded = loaded_for(tmp_path, base_raw(dir_entry()))
    refused("invalid-manifest", lambda: propose_add_directory(loaded, bad, path="new", artifact_id="new"), bad)
    refused("invalid-manifest", lambda: propose_rehash_directory(loaded, bad, artifact_id="data"), bad)
    assert scanner["calls"] == []


# --- linked results and claims ----------------------------------------------------


def test_add_lists_results_and_claims_that_already_name_the_id(tmp_path, scanner):
    raw = base_raw(
        results=[{"id": "count", "run_id": "run", "name": "files", "value": 3, "artifact_id": "data"}],
        evidence=[{"id": "claim", "claim": "three files", "supports": ["count"]},
                  {"id": "direct", "claim": "the data", "contradicts": ["data"]},
                  {"id": "other", "claim": "notes", "supports": ["notes"]}],
    )
    proposal = add(tmp_path, raw)
    assert proposal.linked_results == ("count",)
    assert proposal.claims == ("claim", "direct")
    assert proposal.raw["results"] == raw["results"] and proposal.raw["evidence"] == raw["evidence"]


def test_locator_linkage_is_refused_for_add_and_rehash(tmp_path, scanner):
    locator = {"id": "acc", "run_id": "run", "name": "accuracy", "value": 0.9,
               "artifact_id": "data", "locator": "/acc"}
    raw = base_raw(results=[locator])
    err = refused("locator-unsupported", lambda: add(tmp_path, raw), raw)
    assert err.subjects == ("acc",) and "acc" in str(err)
    raw = base_raw(dir_entry(), results=[locator])
    assert refused("locator-unsupported", lambda: rehash(tmp_path, raw), raw).subjects == ("acc",)
    assert scanner["calls"] == []


# --- rehash -------------------------------------------------------------------------


@pytest.mark.parametrize("raw,loaded_raw,artifact_id,reason", [
    (base_raw(), None, "data", "not-found"),
    (base_raw(dir_entry()), None, "notes", "not-a-directory-artifact"),
    (base_raw(dir_entry(volatile=True)), None, "data", "volatile-unsupported"),
    (base_raw(dir_entry(), dir_entry()), None, "data", "duplicate-id"),
    (base_raw(dir_entry(), results=[{"id": "data", "run_id": "run", "name": "n"}]), None, "data", "duplicate-id"),
    (base_raw(dir_entry(path="data2")), base_raw(dir_entry()), "data", "stale"),
    (base_raw(dir_entry()), base_raw(), "data", "stale"),
    (base_raw(dir_entry()), base_raw({"id": "data", "path": "data"}), "data", "stale"),
    (base_raw(dir_entry(path="./data")), None, "data", "invalid-path"),
    (base_raw(dir_entry(path="data\\sub")), None, "data", "invalid-path"),
    (base_raw(dir_entry(), dir_entry(id="inner", path="data/inner")), None, "data", "overlap"),
    (base_raw(dir_entry(), {"id": "twin", "path": "DATA"}), None, "data", "path-collision"),
], ids=["missing", "file", "volatile", "duplicate-artifact", "duplicate-namespace", "stale-path",
        "stale-missing", "stale-type", "non-canonical", "backslash", "nested-directory", "same-root"])
def test_rehash_refusals_happen_before_any_scan(tmp_path, scanner, raw, loaded_raw, artifact_id, reason):
    refused(reason, lambda: rehash(tmp_path, raw, artifact_id, loaded_raw), raw)
    assert scanner["calls"] == []


def test_rehash_changes_only_digest_and_size(tmp_path, scanner):
    entry = {"kind": "dataset", **dir_entry(), "run_id": "run", "metadata": {"source": "lab"}}
    raw = base_raw(
        entry, {"id": "after", "path": "after.txt", "sha256": "c" * 64},
        results=[{"id": "count", "run_id": "run", "name": "files", "value": 3, "artifact_id": "data"}],
        evidence=[{"id": "claim", "claim": "three files", "supports": ["count"]}],
    )
    proposal = rehash(tmp_path, raw)
    assert proposal.operation == "rehash" and proposal.changed
    assert proposal.add_kind is None and proposal.add_run_id is None
    assert (proposal.old_sha256, proposal.old_size) == ("a" * 64, 4)
    assert (proposal.new_sha256, proposal.new_size, proposal.members) == ("b" * 64, 7, 2)
    expected = copy.deepcopy(raw)
    expected["artifacts"][1]["sha256"] = "b" * 64
    expected["artifacts"][1]["size_bytes"] = 7
    assert frozen(proposal.raw) == frozen(expected)
    assert proposal.entry == proposal.raw["artifacts"][1] and proposal.entry is not proposal.raw["artifacts"][1]
    assert proposal.linked_results == ("count",) and proposal.claims == ("claim",)
    assert scanner["calls"] == [tmp_path.absolute() / "data"]


def test_unchanged_rehash_proposes_the_input_exactly(tmp_path, scanner):
    raw = base_raw(dir_entry(sha256="B" * 64, size_bytes=7))  # uppercase is stored lowercase
    proposal = rehash(tmp_path, raw)
    assert not proposal.changed and proposal.old_sha256 == "b" * 64
    assert frozen(proposal.raw) == frozen(raw) and proposal.raw is not raw
    assert any("unchanged" in note for note in proposal.notes)


def test_proposal_is_a_deep_copy(tmp_path, scanner):
    raw = base_raw(dir_entry())
    for proposal in (rehash(tmp_path, raw), add(tmp_path, raw, path="other", artifact_id="other")):
        proposal.raw["artifacts"][0]["path"] = "changed"
        proposal.raw["project"]["name"] = "changed"
        proposal.entry["path"] = "changed"
    assert frozen(raw) == frozen(base_raw(dir_entry()))


def test_no_command_or_other_module_uses_the_authoring_module():
    # directory_apply is the one internal writer; the S4-1 publication core
    # reuses its checked rebuild. S4-2b authorizes the CLI to compose the
    # authoring proposals; no other command reaches them (the apply and
    # publication guards are checked in their own test modules).
    src = Path(runevidence.__file__).parent
    users = [
        p.relative_to(src).as_posix() for p in src.rglob("*.py")
        if p.name != "directory_authoring.py" and "directory_authoring" in p.read_text(encoding="utf-8")
    ]
    assert sorted(users) == [
        "cli.py", "directory_apply.py", "directory_plan.py",
        "directory_preview.py", "directory_publication.py",
    ]


# --- native scanner, synthetic folders ------------------------------------------------


@native
@pytest.mark.parametrize("vector", VECTORS, ids=[v["name"] for v in VECTORS])
def test_native_add_matches_pinned_vectors(tmp_path, vector):
    build(tmp_path / "data", vector["layout"])
    raw = base_raw()
    proposal = add(tmp_path, raw, run_id="run", kind="dataset")
    assert (proposal.new_sha256, proposal.new_size, proposal.members) == (
        vector["sha256"], vector["size_bytes"], len(vector["layout"]))
    assert proposal.operation == "add" and proposal.changed
    assert (proposal.old_sha256, proposal.old_size) == (None, None)
    assert list(proposal.entry.items()) == [
        ("id", "data"), ("path", "data"), ("artifact_type", "directory"),
        ("digest_format", DIRECTORY_DIGEST_FORMAT), ("sha256", vector["sha256"]),
        ("size_bytes", vector["size_bytes"]), ("kind", "dataset"), ("run_id", "run"), ("metadata", {}),
    ]
    art = Artifact.model_validate(proposal.entry)
    assert (art.artifact_type, art.digest_format, art.volatile) == ("directory", DIRECTORY_DIGEST_FORMAT, False)
    # Only one entry is appended; every other byte of the manifest is as it was.
    assert proposal.raw["artifacts"][-1] == proposal.entry
    rest = {**proposal.raw, "artifacts": proposal.raw["artifacts"][:-1]}
    assert frozen(rest) == frozen(raw) == frozen(base_raw())
    Manifest.model_validate(proposal.raw)
    assert dump_raw(add(tmp_path, raw, run_id="run", kind="dataset").raw) == dump_raw(proposal.raw)
    report = run_checks(loaded_for(tmp_path, proposal.raw), enable_directory_artifacts=True)
    assert [f for f in report.findings if f.subject == "data"] == []


@native
def test_native_rehash_after_a_real_change(tmp_path):
    vector = VECTORS[2]
    build(tmp_path / "data", vector["layout"])
    recorded = add(tmp_path, base_raw()).raw
    new_bytes = b"\x00\x01\x02\xff\x10"
    (tmp_path / "data" / "n" / "x.bin").write_bytes(new_bytes)
    stale = run_checks(loaded_for(tmp_path, recorded), enable_directory_artifacts=True)
    assert {f.code for f in stale.findings if f.subject == "data"} == {
        Code.directory_hash_mismatch, Code.directory_size_mismatch}

    before = frozen(recorded)
    proposal = rehash(tmp_path, recorded)
    expected_digest = directory_digest([
        DirectoryRecord(DIRECTORY, "empty"), DirectoryRecord(DIRECTORY, "n"),
        DirectoryRecord(FILE, "n/x.bin", hashlib.sha256(new_bytes).digest(), len(new_bytes)),
    ])
    assert proposal.changed and (proposal.old_sha256, proposal.old_size) == (vector["sha256"], 4)
    assert (proposal.new_sha256, proposal.new_size, proposal.members) == (expected_digest.sha256, 5, 3)
    expected = copy.deepcopy(recorded)
    expected["artifacts"][-1].update(sha256=expected_digest.sha256, size_bytes=5)
    assert frozen(proposal.raw) == frozen(expected)
    assert frozen(recorded) == before
    report = run_checks(loaded_for(tmp_path, proposal.raw), enable_directory_artifacts=True)
    assert [f for f in report.findings if f.subject == "data"] == []

    again = rehash(tmp_path, proposal.raw)
    assert not again.changed and frozen(again.raw) == frozen(proposal.raw)


@native
def test_native_missing_or_file_root_is_refused(tmp_path):
    raw = base_raw()
    assert refused("scan-failed", lambda: add(tmp_path, raw), raw).scan_reason == "missing"
    (tmp_path / "data").write_bytes(b"not a folder")
    assert refused("scan-failed", lambda: add(tmp_path, raw), raw).scan_reason == "not-a-directory"


@native
def test_native_junction_root_or_ancestor_is_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    build(real / "inner", VECTORS[1]["layout"])
    link = tmp_path / "data"
    if not make_dir_link(link, real):
        pytest.skip("this platform refused to create a directory link")
    try:
        raw = base_raw()
        assert refused("scan-failed", lambda: add(tmp_path, raw), raw).scan_reason == "unsafe"
        assert refused("scan-failed", lambda: add(tmp_path, raw, path="data/inner"), raw).scan_reason == "unsafe"
        raw = base_raw(dir_entry())
        assert refused("scan-failed", lambda: rehash(tmp_path, raw), raw).scan_reason == "unsafe"
    finally:
        remove_dir_link(link)


@native
def test_native_junction_member_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"not part of the artifact")
    build(tmp_path / "data", VECTORS[1]["layout"])
    link = tmp_path / "data" / "linked"
    if not make_dir_link(link, outside):
        pytest.skip("this platform refused to create a directory link")
    try:
        raw = base_raw()
        assert refused("scan-failed", lambda: add(tmp_path, raw), raw).scan_reason == "unsafe"
    finally:
        remove_dir_link(link)


@native
def test_native_hardlinked_member_is_refused(tmp_path):
    build(tmp_path / "data", VECTORS[1]["layout"])
    outside = tmp_path / "outside"
    outside.write_bytes(b"shared bytes")
    try:
        os.link(outside, tmp_path / "data" / "alias")
    except OSError as exc:
        pytest.skip(f"cannot create a hard link here: {exc}")
    raw = base_raw()
    assert refused("scan-failed", lambda: add(tmp_path, raw), raw).scan_reason == "unsafe"


@native
@pytest.mark.parametrize("target_is_directory", [False, True], ids=["file", "directory"])
def test_native_symbolic_link_member_is_refused(tmp_path, target_is_directory):
    target = tmp_path / "outside"
    if target_is_directory:
        target.mkdir()
    else:
        target.write_bytes(b"outside")
    build(tmp_path / "data", VECTORS[1]["layout"])
    try:
        os.symlink(target, tmp_path / "data" / "link", target_is_directory=target_is_directory)
    except OSError as exc:  # WinError 1314 without Developer Mode or elevation
        pytest.skip(f"cannot create a symbolic link here: {exc}")
    raw = base_raw()
    assert refused("scan-failed", lambda: add(tmp_path, raw), raw).scan_reason == "unsafe"


@native
@pytest.mark.parametrize("name", [".git", "__pycache__", "runevidence.json"])
def test_native_disallowed_member_name_is_refused(tmp_path, name):
    build(tmp_path / "data", VECTORS[1]["layout"])
    target = tmp_path / "data" / name
    if name == "runevidence.json":
        target.write_bytes(b"{}")
    else:
        target.mkdir()
    raw = base_raw()
    assert refused("scan-failed", lambda: add(tmp_path, raw), raw).scan_reason == "unsafe"


@native
def test_native_child_file_artifact_stays_independently_checked(tmp_path):
    build(tmp_path / "data", VECTORS[1]["layout"])  # data/a holds b"hello"
    child = {"id": "child", "path": "data/a", "sha256": hashlib.sha256(b"hello").hexdigest(), "size_bytes": 5}
    proposal = add(tmp_path, base_raw(child))
    assert proposal.overlapping_files == ("child",) and proposal.new_sha256 == VECTORS[1]["sha256"]

    def findings(raw):
        report = run_checks(loaded_for(tmp_path, raw), enable_directory_artifacts=True)
        return [(f.subject, f.code) for f in report.findings if f.subject in ("data", "child")]

    assert findings(proposal.raw) == []
    wrong = copy.deepcopy(proposal.raw)
    wrong["artifacts"][1]["sha256"] = "0" * 64
    assert findings(wrong) == [("child", Code.hash_mismatch)]


FORBIDDEN = [
    "os.replace",
    "os.rename", "os.remove", "os.unlink", "os.mkdir", "os.makedirs", "os.rmdir",
    "os.chmod", "os.link", "os.symlink", "os.truncate", "os.utime",
    "tempfile.NamedTemporaryFile", "tempfile.mkstemp",
    "runevidence.manifest.save_raw", "runevidence.manifest.save_manifest", "runevidence.manifest._write",
    "runevidence.edit.plan_add", "runevidence.edit.plan_rehash", "runevidence.edit.iter_files",
    "runevidence.edit.sha256_file", "runevidence.checks.files.iter_files", "runevidence.hashing.sha256_file",
]
# The POSIX backend reads through os.open/os.scandir. The Windows backend reads
# through NtCreateFile, so the original strict authoring list still forbids
# ordinary and path-based reads there; the read-only scanner allowance is
# POSIX-only.
READ_ONLY_SCANNER_CALLS = [
    "builtins.open", "io.open", "os.open", "os.scandir", "os.listdir", "os.walk",
]
if os.name == "posix":
    # The scanner may read with os.open/os.scandir; advisory locks, chmod,
    # chown, fchmod, mkfifo and xattr writes are never part of a proposal.
    FORBIDDEN += ["fcntl.flock", "fcntl.lockf", "fcntl.fcntl", "os.chown"]
    FORBIDDEN += [
        name for name in ("os.fchmod", "os.mkfifo", "os.setxattr")
        if hasattr(os, name.split(".", 1)[1])
    ]
else:
    FORBIDDEN += READ_ONLY_SCANNER_CALLS

# Read-only scanner opens are allowed; a write/create/truncate mode is not.
# O_TMPFILE is deliberately absent: on Linux its value includes O_DIRECTORY,
# so it would false-positive on the scanner's O_PATH|O_DIRECTORY opens; it
# still requires a write access mode, which this mask catches.
WRITE_OPEN_FLAGS = (
    os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    | getattr(os, "O_EXCL", 0)
)


def _xattrs(path: Path) -> tuple:
    """Read-only extended-attribute snapshot; an unavailable API is empty."""
    lister = getattr(os, "listxattr", None)
    reader = getattr(os, "getxattr", None)
    if lister is None or reader is None:
        return ()
    try:
        names = lister(path)
    except OSError:
        return ()
    out = []
    for name in sorted(names):
        try:
            out.append((name, reader(path, name)))
        except OSError:
            out.append((name, None))
    return tuple(out)


@native
def test_native_proposals_read_only_through_the_scanner_and_write_nothing(tmp_path, monkeypatch):
    build(tmp_path / "data", VECTORS[2]["layout"])
    (tmp_path / MANIFEST_FILENAME).write_text(dump_raw(base_raw()), encoding="utf-8", newline="\n")
    loaded = load_manifest(tmp_path)
    raw = load_raw(loaded)
    root_stat = (tmp_path / "data").stat()

    def snapshot():
        return sorted(
            (
                p.relative_to(tmp_path).as_posix(),
                p.read_bytes() if p.is_file() else None,
                p.lstat().st_mtime_ns,
                stat.S_IMODE(p.lstat().st_mode),
                p.lstat().st_uid,
                p.lstat().st_gid,
                _xattrs(p),
            )
            for p in tmp_path.rglob("*")
        )

    before = snapshot()
    first = propose_add_directory(loaded, raw, path="data", artifact_id="data")  # warm any lazy imports
    recorded = loaded_for(tmp_path, first.raw)
    used = []
    read_only_opens = []

    def forbidden(name):
        def call(*args, **kwargs):
            used.append(name)
            raise AssertionError(f"{name} called while proposing")
        return call

    real_open = os.open
    real_builtin_open = builtins.open
    real_io_open = io.open

    def read_only_open(path, flags, *args, **kwargs):
        if flags & WRITE_OPEN_FLAGS:
            used.append("os.open")
            raise AssertionError(f"os.open called with write flags {flags!r} while proposing")
        fd = real_open(path, flags, *args, **kwargs)
        try:
            st = os.fstat(fd)
        except OSError:
            identity = None
        else:
            identity = (st.st_dev, st.st_ino)
        read_only_opens.append(
            {"path": os.fspath(path), "flags": flags, "dir_fd": kwargs.get("dir_fd"),
             "identity": identity}
        )
        return fd

    def read_only_builtin_open(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wax+"):
            used.append("builtins.open")
            raise AssertionError(f"builtins.open called with write mode {mode!r} while proposing")
        return real_builtin_open(file, mode, *args, **kwargs)

    def read_only_io_open(file, mode="r", *args, **kwargs):
        if any(flag in mode for flag in "wax+"):
            used.append("io.open")
            raise AssertionError(f"io.open called with write mode {mode!r} while proposing")
        return real_io_open(file, mode, *args, **kwargs)

    with monkeypatch.context() as m:
        for name in FORBIDDEN:
            m.setattr(name, forbidden(name))
        if os.name == "posix":
            m.setattr("os.open", read_only_open)
            m.setattr("builtins.open", read_only_builtin_open)
            m.setattr("io.open", read_only_io_open)
        added = propose_add_directory(loaded, raw, path="data", artifact_id="data")
        rehashed = propose_rehash_directory(recorded, first.raw, artifact_id="data")
    assert used == []
    if os.name == "posix":
        # Prove the scanner opened the root itself read-only: the O_PATH probe
        # of the last component and its O_RDONLY|O_DIRECTORY pinned reopen both
        # resolve to the root's inode and carry no write/create/truncate flag.
        root_identity = (root_stat.st_dev, root_stat.st_ino)
        root_opens = [record for record in read_only_opens if record["identity"] == root_identity]
        assert root_opens, "the native scanner should have opened the root itself read-only"
        assert any(
            record["dir_fd"] is not None and record["flags"] & os.O_PATH
            and not record["flags"] & WRITE_OPEN_FLAGS
            for record in root_opens
        ), "the root should have a component-relative O_PATH probe"
        assert any(
            record["path"].startswith("/proc/self/fd/") and record["flags"] & os.O_DIRECTORY
            and not record["flags"] & WRITE_OPEN_FLAGS
            for record in root_opens
        ), "the root should have an O_RDONLY|O_DIRECTORY pinned reopen"
        assert all(not record["flags"] & WRITE_OPEN_FLAGS for record in read_only_opens)
    assert frozen(added.raw) == frozen(first.raw) and not rehashed.changed
    assert snapshot() == before
    assert (tmp_path / MANIFEST_FILENAME).read_bytes() == loaded.source_bytes
    assert frozen(raw) == frozen(load_raw(loaded))
