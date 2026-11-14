"""Saving a directory proposal (internal, development only).

Every manifest and folder is a new synthetic one under tmp_path; no test reads
or writes a real experiment. Test names say what kind of evidence each is:

* ``test_native_*``    the native scanner reads a real synthetic folder
* ``test_injected_*``  a fault, a timing or a stub scanner is injected with
                       monkeypatch; these show the apply logic, not native safety
* the rest             refusals before any scan (a stub may make the proposal)
"""

from __future__ import annotations

import ast
import copy
import dataclasses
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

import runevidence
from conftest import make_dir_link, remove_dir_link
from runevidence import directory_scanner
from runevidence.checks import run_checks
from runevidence.directory_apply import DirectoryApplyError, DirectoryApplyResult, apply_directory_proposal
from runevidence.directory_authoring import (
    DirectoryAuthoringError,
    DirectoryProposal,
    propose_add_directory,
    propose_rehash_directory,
)
from runevidence.directory_hashing import DirectoryDigest
from conftest import native_test
from runevidence.directory_scanner import DirectoryScan, DirectoryScanError
from runevidence.edit import EditError
from runevidence.findings import Code
from runevidence.manifest import LoadedManifest, dump_raw, load_manifest, load_raw
from runevidence.models import MANIFEST_FILENAME, Manifest

VECTORS = json.loads(
    (Path(__file__).parent / "data" / "directory_digest_vectors.json").read_bytes().decode("utf-8")
)["vectors"]
NESTED = VECTORS[2]["layout"]  # data/empty/, data/n/, data/n/x.bin

native = native_test

NOTES = b"lab notes\n"


def base_raw(*artifacts, results=(), evidence=()):
    # Keys in a hand-written order, to show that a save keeps them.
    return {
        "project": {"name": "Demo", "id": "demo"},
        "schema_version": "0.1",
        "runs": [{"id": "run", "name": "baseline"}],
        "artifacts": [{"path": "notes.txt", "kind": "doc", "id": "notes",
                       "sha256": hashlib.sha256(NOTES).hexdigest(), "size_bytes": len(NOTES)}, *artifacts],
        "results": list(results),
        "evidence": list(evidence),
    }


def build(root: Path, layout) -> None:
    root.mkdir()
    for entry in layout:
        target = root.joinpath(*entry["path"].split("/"))
        if entry["type"] == "D":
            target.mkdir()
        else:
            target.write_bytes(bytes.fromhex(entry["content_hex"]))


def write_manifest(root: Path, raw: dict) -> None:
    (root / MANIFEST_FILENAME).write_text(dump_raw(raw), encoding="utf-8", newline="\n")


def experiment(root: Path, raw: dict, layout=NESTED) -> LoadedManifest:
    """A synthetic experiment: notes.txt, the folder data/ and a manifest."""
    (root / "notes.txt").write_bytes(NOTES)
    build(root / "data", layout)
    write_manifest(root, raw)
    return load_manifest(root)


def recorded(root: Path, raw: dict, layout=NESTED) -> LoadedManifest:
    """As ``experiment``, with data/ already recorded as the last artifact."""
    loaded = experiment(root, raw, layout)
    write_manifest(root, propose_add_directory(loaded, load_raw(loaded), path="data", artifact_id="data").raw)
    return load_manifest(root)


def add(loaded: LoadedManifest, **kwargs) -> DirectoryProposal:
    kwargs.setdefault("path", "data")
    kwargs.setdefault("artifact_id", "data")
    return propose_add_directory(loaded, load_raw(loaded), **kwargs)


def rehash(loaded: LoadedManifest) -> DirectoryProposal:
    return propose_rehash_directory(loaded, load_raw(loaded), artifact_id="data")


def frozen(value) -> str:
    """Order-sensitive JSON, to compare byte for byte."""
    return json.dumps(value, ensure_ascii=False, default=str)


def state(proposal: DirectoryProposal) -> str:
    return frozen(dict(vars(proposal)))


def snapshot(root: Path):
    """Every path under ``root`` with its bytes and modification time."""
    return sorted(
        (p.relative_to(root).as_posix(), p.read_bytes() if p.is_file() else None, p.stat().st_mtime_ns)
        for p in root.rglob("*")
    )


def staged_files(root: Path) -> list[str]:
    return sorted(p.name for p in root.iterdir() if p.name.startswith(f".{MANIFEST_FILENAME}."))


WRITERS = [
    "runevidence.directory_apply.save_raw", "runevidence.manifest.save_raw", "runevidence.manifest.save_manifest",
    "runevidence.manifest._write", "tempfile.NamedTemporaryFile", "tempfile.mkstemp", "os.replace", "os.rename",
    "os.remove", "os.unlink", "os.mkdir", "os.makedirs", "os.rmdir",
]


@contextmanager
def no_writes(monkeypatch):
    """Every write path fails loudly, and is recorded, while the block runs."""
    used = []

    def forbidden(name):
        def call(*args, **kwargs):
            used.append(name)
            raise AssertionError(f"{name} called")
        return call

    with monkeypatch.context() as m:
        for name in WRITERS:
            m.setattr(name, forbidden(name))
        yield used


@pytest.fixture
def scans(monkeypatch):
    """The real scanner, with every root it is asked to scan recorded."""
    calls = []
    real = directory_scanner.scan_directory

    def scan(root, *args, **kwargs):
        calls.append(Path(root))
        return real(root, *args, **kwargs)

    monkeypatch.setattr("runevidence.directory_scanner.scan_directory", scan)
    return calls


@pytest.fixture
def stub(monkeypatch):
    """Replace the scanner with a fixed answer (not native evidence)."""
    answer = {"scan": DirectoryScan((), DirectoryDigest("b" * 64, 7, 2))}

    def scan(root, *args, **kwargs):
        if isinstance(answer["scan"], Exception):
            raise answer["scan"]
        return answer["scan"]

    monkeypatch.setattr("runevidence.directory_scanner.scan_directory", scan)
    return answer


def refused(reason, loaded, proposal, root, monkeypatch, *, dry_run=False) -> DirectoryApplyError:
    """Apply must refuse with ``reason``, touching no write path, no file and
    not the proposal itself."""
    before, proposal_before = snapshot(root), state(proposal)
    with no_writes(monkeypatch) as used, pytest.raises(DirectoryApplyError) as info:
        apply_directory_proposal(loaded, proposal, dry_run=dry_run)
    assert info.value.reason == reason, str(info.value)
    assert isinstance(info.value, EditError)
    assert used == []
    assert snapshot(root) == before
    assert state(proposal) == proposal_before
    return info.value


def clean(root: Path) -> list:
    """Findings of the internal opt-in check of the manifest on disk."""
    return run_checks(load_manifest(root), enable_directory_artifacts=True).findings


# --- written changes, native ---------------------------------------------------------


@native
@pytest.mark.parametrize("vector", VECTORS, ids=[v["name"] for v in VECTORS])
def test_native_add_writes_exactly_one_entry_and_checks_clean(tmp_path, vector):
    loaded = experiment(tmp_path, base_raw(), vector["layout"])
    pristine = load_raw(loaded)
    proposal = add(loaded, run_id="run", kind="dataset")
    before = state(proposal)

    result = apply_directory_proposal(loaded, proposal, dry_run=False)

    assert type(result) is DirectoryApplyResult and result.written and result.status == "written"
    assert (result.operation, result.artifact_id, result.path, result.manifest_path) == (
        "add", "data", "data", loaded.path)
    assert (result.new_sha256, result.new_size, result.members) == (
        vector["sha256"], vector["size_bytes"], len(vector["layout"]))
    assert (result.old_sha256, result.old_size) == (None, None)
    expected = copy.deepcopy(pristine)
    expected["artifacts"].append(proposal.entry)
    written = (tmp_path / MANIFEST_FILENAME).read_bytes()
    assert written == dump_raw(expected).encode("utf-8") == result.text.encode("utf-8")
    assert state(proposal) == before  # the proposal itself is never changed
    assert staged_files(tmp_path) == []
    assert clean(tmp_path) == []


@native
def test_native_rehash_writes_only_sha256_and_size_bytes(tmp_path):
    loaded = recorded(tmp_path, base_raw())
    (tmp_path / "data" / "n" / "x.bin").write_bytes(b"\x00\x01\x02\xff\x10")
    loaded = load_manifest(tmp_path)
    assert {f.code for f in clean(tmp_path) if f.subject == "data"} == {
        Code.directory_hash_mismatch, Code.directory_size_mismatch}
    pristine = load_raw(loaded)
    proposal = rehash(loaded)
    assert proposal.changed

    result = apply_directory_proposal(loaded, proposal, dry_run=False)

    assert result.status == "written" and (result.old_size, result.new_size) == (4, 5)
    after = json.loads((tmp_path / MANIFEST_FILENAME).read_bytes())
    old_entry, new_entry = pristine["artifacts"][-1], after["artifacts"][-1]
    assert list(new_entry) == list(old_entry)
    assert [k for k in old_entry if old_entry[k] != new_entry[k]] == ["sha256", "size_bytes"]
    assert (new_entry["sha256"], new_entry["size_bytes"]) == (proposal.new_sha256, 5)
    expected = copy.deepcopy(pristine)
    expected["artifacts"][-1].update(sha256=proposal.new_sha256, size_bytes=5)
    assert (tmp_path / MANIFEST_FILENAME).read_bytes() == dump_raw(expected).encode("utf-8")
    assert staged_files(tmp_path) == [] and clean(tmp_path) == []


@native
def test_native_apply_saves_once_through_save_raw_with_the_loaded_guard(tmp_path, monkeypatch):
    import runevidence.directory_apply as module

    loaded = experiment(tmp_path, base_raw())
    proposal = add(loaded)
    calls = []
    real = module.save_raw

    def spy(data, path, *, expected_bytes):
        calls.append((frozen(data), path, expected_bytes))
        return real(data, path, expected_bytes=expected_bytes)

    def forbidden(*args, **kwargs):
        raise AssertionError("old file-only edit path used")

    monkeypatch.setattr(module, "save_raw", spy)
    for name in ("runevidence.edit.plan_add", "runevidence.edit.plan_rehash",
                 "runevidence.manifest.save_manifest", "runevidence.manifest._write"):
        monkeypatch.setattr(name, forbidden)
    apply_directory_proposal(loaded, proposal, dry_run=False)
    assert calls == [(frozen(proposal.raw), loaded.path, loaded.source_bytes)]
    assert calls[0][2] is loaded.source_bytes


@native
def test_native_child_files_and_linked_results_are_left_alone(tmp_path):
    child = {"id": "child", "path": "data/a", "sha256": hashlib.sha256(b"hello").hexdigest(), "size_bytes": 5}
    raw = base_raw(
        child,
        results=[{"id": "count", "run_id": "run", "name": "files", "value": 1, "artifact_id": "data"}],
        evidence=[{"id": "claim", "claim": "one file", "supports": ["count"]},
                  {"id": "direct", "claim": "the data", "supports": ["data"]}],
    )
    loaded = experiment(tmp_path, raw, VECTORS[1]["layout"])  # data/a holds b"hello"
    proposal = add(loaded)
    result = apply_directory_proposal(loaded, proposal, dry_run=False)

    assert result.written and any("child" in n for n in result.notes) and any("claim" in n for n in result.notes)
    after = json.loads((tmp_path / MANIFEST_FILENAME).read_bytes())
    assert after["artifacts"][:-1] == raw["artifacts"]  # the child record is not refreshed
    assert (after["results"], after["evidence"]) == (raw["results"], raw["evidence"])  # nor results or claims
    assert clean(tmp_path) == []
    wrong = copy.deepcopy(after)
    wrong["artifacts"][1]["sha256"] = "0" * 64
    write_manifest(tmp_path, wrong)
    assert [(f.subject, f.code) for f in clean(tmp_path) if f.subject in ("data", "child")] == [
        ("child", Code.hash_mismatch)]


# --- nothing written, native ---------------------------------------------------------


@native
@pytest.mark.parametrize("dry_run", [False, True])
def test_native_unchanged_rehash_writes_nothing(tmp_path, monkeypatch, dry_run):
    loaded = recorded(tmp_path, base_raw())
    proposal = rehash(loaded)
    assert not proposal.changed
    before = snapshot(tmp_path)
    with no_writes(monkeypatch) as used:
        result = apply_directory_proposal(loaded, proposal, dry_run=dry_run)
    assert (result.status, result.written, result.text) == ("unchanged", False, None)
    assert result.new_sha256 == result.old_sha256 == proposal.new_sha256
    assert used == [] and snapshot(tmp_path) == before


@native
@pytest.mark.parametrize("operation", ["add", "rehash"])
def test_native_dry_run_checks_everything_and_writes_nothing(tmp_path, monkeypatch, operation):
    if operation == "add":
        loaded = experiment(tmp_path, base_raw())
        proposal = add(loaded)
    else:
        loaded = recorded(tmp_path, base_raw())
        (tmp_path / "data" / "n" / "x.bin").write_bytes(b"changed")
        loaded = load_manifest(tmp_path)
        proposal = rehash(loaded)
    before = snapshot(tmp_path)
    with no_writes(monkeypatch) as used:
        result = apply_directory_proposal(loaded, proposal, dry_run=True)
    assert (result.status, result.written) == ("dry-run", False)
    assert result.text == dump_raw(proposal.raw)
    assert (result.new_sha256, result.new_size) == (proposal.new_sha256, proposal.new_size)
    assert used == [] and snapshot(tmp_path) == before

    # A dry run is checked like a real one: a stale folder is refused too.
    (tmp_path / "data" / "late.txt").write_bytes(b"late")
    refused("stale-tree", loaded, proposal, tmp_path, monkeypatch, dry_run=True)


# --- the proposal must be the one this manifest and folder give ------------------------


def _target(p):
    return p.raw["artifacts"][-1]


def _both(change):
    """The same change to the entry in ``raw`` and to ``entry``."""
    def mutate(p):
        change(_target(p))
        change(p.entry)
    return mutate


def _set(name, value):
    return lambda p: object.__setattr__(p, name, value)


def _swap_top_keys(p):
    items = list(p.raw.items())
    items[0], items[1] = items[1], items[0]
    p.raw.clear()
    p.raw.update(items)


def _reorder_entry(d):
    items = list(d.items())[::-1]
    d.clear()
    d.update(items)


MUTATIONS = {
    "raw-project": (lambda p: p.raw["project"].update(name="Other"), "proposal-mismatch"),
    "raw-other-artifact": (lambda p: p.raw["artifacts"][0].update(kind="other"), "proposal-mismatch"),
    "raw-top-key-order": (_swap_top_keys, "proposal-mismatch"),
    "raw-artifact-order": (lambda p: p.raw["artifacts"].insert(0, p.raw["artifacts"].pop()), "proposal-mismatch"),
    "raw-extra-top-key": (lambda p: p.raw.update(extra=1), "proposal-mismatch"),
    "raw-removed-result": (lambda p: p.raw["results"].pop(), "proposal-mismatch"),
    "raw-removed-claim": (lambda p: p.raw["evidence"].pop(), "proposal-mismatch"),
    "raw-extra-artifact": (lambda p: p.raw["artifacts"].append({"id": "x", "path": "x.txt"}), "proposal-mismatch"),
    "raw-entry-sha256": (lambda p: _target(p).update(sha256="0" * 64), "proposal-mismatch"),
    "raw-entry-size-as-float": (lambda p: _target(p).update(size_bytes=float(_target(p)["size_bytes"])),
                                "proposal-mismatch"),
    "entry-sha256": (lambda p: p.entry.update(sha256="0" * 64), "proposal-mismatch"),
    "entry-path": (lambda p: p.entry.update(path="data2"), "proposal-mismatch"),
    "entry-extra-field": (lambda p: p.entry.update(extra=1), "proposal-mismatch"),
    "entry-key-order": (lambda p: _reorder_entry(p.entry), "proposal-mismatch"),
    "both-sha256": (_both(lambda e: e.update(sha256="0" * 64)), "proposal-mismatch"),
    "both-size": (_both(lambda e: e.update(size_bytes=e["size_bytes"] + 1)), "proposal-mismatch"),
    "both-path": (_both(lambda e: e.update(path="data2")), "proposal-mismatch"),
    "both-id": (_both(lambda e: e.update(id="data2")), "proposal-mismatch"),
    "both-extra-field": (_both(lambda e: e.update(extra=1)), "proposal-mismatch"),
    "both-metadata": (_both(lambda e: e.update(metadata={"note": "x"})), "proposal-mismatch"),
    # add: allowed shape, then refused as volatile; rehash: a key other than the two
    "both-volatile": (_both(lambda e: e.update(volatile=True)), {"add": "recheck-failed",
                                                                 "rehash": "proposal-mismatch"}),
    "attr-artifact-id": (_set("artifact_id", "data2"), "proposal-mismatch"),
    "attr-path": (_set("path", "data2"), "proposal-mismatch"),
    "attr-new-sha256": (_set("new_sha256", "0" * 64), "proposal-mismatch"),
    "attr-new-size": (_set("new_size", 999), "proposal-mismatch"),
    "attr-old-sha256": (_set("old_sha256", "0" * 64), "proposal-mismatch"),
    "attr-changed": (lambda p: object.__setattr__(p, "changed", not p.changed), "proposal-mismatch"),
    "attr-claims": (_set("claims", ()), "proposal-mismatch"),
    "attr-linked-results": (_set("linked_results", ()), "proposal-mismatch"),
    "attr-notes": (_set("notes", ()), "proposal-mismatch"),
    "attr-members": (_set("members", 999), "stale-tree"),  # not visible in the entry
    "attr-extra": (_set("extra", 1), "proposal-mismatch"),
    "attr-raw-replaced": (lambda p: object.__setattr__(p, "raw", dict(p.raw)) or p.raw.update(project={}),
                          "proposal-mismatch"),
    "attr-entry-not-dict": (_set("entry", ["data"]), "proposal-mismatch"),
    "attr-operation-swapped": (lambda p: object.__setattr__(p, "operation", "rehash" if p.operation == "add" else "add"),
                               "proposal-mismatch"),
    "attr-operation-unknown": (_set("operation", "delete"), "invalid-request"),
}


def _linked_raw(*artifacts):
    return base_raw(
        *artifacts,
        results=[{"id": "count", "run_id": "run", "name": "files", "value": 2, "artifact_id": "data"}],
        evidence=[{"id": "claim", "claim": "two files", "supports": ["count"]}],
    )


@native
@pytest.mark.parametrize("operation", ["add", "rehash"])
@pytest.mark.parametrize("name", list(MUTATIONS))
def test_native_changed_proposal_is_refused(tmp_path, monkeypatch, operation, name):
    mutate, reason = MUTATIONS[name]
    if operation == "add":
        loaded = experiment(tmp_path, _linked_raw())
        proposal = add(loaded)
    else:
        loaded = recorded(tmp_path, _linked_raw())
        (tmp_path / "data" / "n" / "x.bin").write_bytes(b"changed")
        loaded = load_manifest(tmp_path)
        proposal = rehash(loaded)
    assert proposal.changed and proposal.linked_results and proposal.claims
    mutate(proposal)
    refused(reason if isinstance(reason, str) else reason[operation], loaded, proposal, tmp_path, monkeypatch)


@native
@pytest.mark.parametrize("change", [{"kind": "other"}, {"run_id": None}])
def test_native_consistent_change_to_kind_or_run_is_refused(tmp_path, monkeypatch, scans, change):
    loaded = experiment(tmp_path, base_raw())
    proposal = add(loaded, run_id="run", kind="dataset")
    _both(lambda e: e.update(change))(proposal)
    scans.clear()
    refused("proposal-mismatch", loaded, proposal, tmp_path, monkeypatch)
    assert scans == []


@native
@pytest.mark.parametrize("where", ["raw", "entry", "both", "intent"])
@pytest.mark.parametrize("field,initial,replacement", [
    ("kind", "dataset", "other"), ("kind", "dataset", None), ("kind", None, "dataset"),
    ("kind", "", None), ("kind", None, ""), ("kind", "測定", "dataset"),
    ("run_id", "run", "other"), ("run_id", "run", None), ("run_id", None, "run"),
    ("run_id", "run", ""), ("run_id", "run", "notes"), ("run_id", "run", "missing"),
])
def test_native_intent_mutations_are_refused_before_scan(tmp_path, monkeypatch, scans,
                                                       field, initial, replacement, where):
    raw = _linked_raw()
    raw["runs"].append({"id": "other", "name": "another valid run"})
    loaded = experiment(tmp_path, raw)
    proposal = add(loaded, **{field: initial})
    if where in ("raw", "both"):
        _target(proposal)[field] = replacement
    if where in ("entry", "both"):
        proposal.entry[field] = replacement
    if where == "intent":
        # A single corrupted scalar must also fail, even with untouched dicts.
        proposal = dataclasses.replace(proposal, **{"add_" + field: replacement})
    scans.clear()
    refused("proposal-mismatch", loaded, proposal, tmp_path, monkeypatch)
    assert scans == []


@pytest.mark.parametrize("field", ["add_kind", "add_run_id"])
@pytest.mark.parametrize("value", [0, False, [], {}])
def test_non_scalar_intent_is_refused_before_scan(tmp_path, stub, monkeypatch, field, value):
    loaded = experiment(tmp_path, base_raw())
    proposal = dataclasses.replace(add(loaded), **{field: value})

    def forbidden(*args, **kwargs):
        raise AssertionError("scanner called with invalid intent")

    monkeypatch.setattr(directory_scanner, "scan_directory", forbidden)
    refused("proposal-mismatch", loaded, proposal, tmp_path, monkeypatch)


@pytest.mark.parametrize("field", ["add_kind", "add_run_id"])
def test_proposal_missing_mandatory_intent_is_refused_before_scan(tmp_path, stub, monkeypatch, field):
    loaded = experiment(tmp_path, base_raw())
    proposal = add(loaded)
    object.__delattr__(proposal, field)  # simulate an older in-memory proposal

    def forbidden(*args, **kwargs):
        raise AssertionError("scanner called with missing intent")

    monkeypatch.setattr(directory_scanner, "scan_directory", forbidden)
    refused("proposal-mismatch", loaded, proposal, tmp_path, monkeypatch)


@native
@pytest.mark.parametrize("kwargs", [{}, {"kind": None, "run_id": None}, {"kind": ""},
                                  {"kind": "dataset", "run_id": "run"}, {"kind": "測定", "run_id": "run"}],
                         ids=["omitted", "explicit-none", "empty-kind", "assigned", "unicode-kind"])
def test_native_add_preserves_intent_and_exact_serialization(tmp_path, kwargs):
    raw = _linked_raw()
    raw["project"]["metadata"] = {"custom": [1, None, "kept"]}
    raw["runs"][0]["params"] = {"epochs": 0}
    raw["artifacts"][0]["metadata"] = {"custom": {"order": True}}
    loaded = experiment(tmp_path, raw)
    proposal = add(loaded, **kwargs)
    assert (proposal.add_kind, proposal.add_run_id) == (kwargs.get("kind"), kwargs.get("run_id"))
    expected = copy.deepcopy(raw)
    expected["artifacts"].append({
        "id": "data", "path": "data", "artifact_type": "directory",
        "digest_format": "runevidence-directory-v1", "sha256": VECTORS[2]["sha256"],
        "size_bytes": VECTORS[2]["size_bytes"], "kind": kwargs.get("kind"),
        "run_id": kwargs.get("run_id"), "metadata": {},
    })
    result = apply_directory_proposal(loaded, proposal, dry_run=False)
    assert result.written and result.text == dump_raw(expected)
    assert loaded.path.read_bytes() == result.text.encode("utf-8")
    assert clean(tmp_path) == []


@native
@pytest.mark.parametrize("changed", [False, True])
@pytest.mark.parametrize("dry_run", [False, True])
def test_native_rehash_preserves_existing_kind_run_and_raw_keys(tmp_path, monkeypatch, changed, dry_run):
    loaded = experiment(tmp_path, _linked_raw())
    raw = add(loaded, kind="dataset", run_id="run").raw
    raw["artifacts"][-1]["metadata"] = {"custom": ["kept", None]}
    raw["artifacts"].append({"id": "after", "path": "notes.txt", "kind": "doc"})
    write_manifest(tmp_path, raw)
    if changed:
        (tmp_path / "data" / "n" / "x.bin").write_bytes(b"changed")
    loaded = load_manifest(tmp_path)
    proposal = rehash(loaded)
    assert proposal.add_kind is None and proposal.add_run_id is None
    assert (proposal.entry["kind"], proposal.entry["run_id"]) == ("dataset", "run")
    expected = copy.deepcopy(raw)
    if changed:
        expected["artifacts"][-2].update(sha256=proposal.new_sha256, size_bytes=proposal.new_size)
    if dry_run or not changed:
        with no_writes(monkeypatch) as used:
            result = apply_directory_proposal(loaded, proposal, dry_run=dry_run)
        assert used == [] and loaded.path.read_bytes() == loaded.source_bytes
    else:
        result = apply_directory_proposal(loaded, proposal, dry_run=False)
        assert result.written and loaded.path.read_bytes() == dump_raw(expected).encode("utf-8")
    assert result.status == ("unchanged" if not changed else "dry-run" if dry_run else "written")
    assert result.text == (dump_raw(expected) if changed else None)


@pytest.mark.parametrize("field,value", [("add_kind", "dataset"), ("add_run_id", "run")])
def test_rehash_cannot_carry_new_intent(tmp_path, stub, monkeypatch, field, value):
    loaded = recorded(tmp_path, base_raw())
    proposal = dataclasses.replace(rehash(loaded), **{field: value})

    def forbidden(*args, **kwargs):
        raise AssertionError("scanner called with new rehash intent")

    monkeypatch.setattr(directory_scanner, "scan_directory", forbidden)
    refused("proposal-mismatch", loaded, proposal, tmp_path, monkeypatch)


@native
def test_native_proposal_from_another_manifest_is_refused(tmp_path, monkeypatch):
    loaded = experiment(tmp_path, base_raw())
    proposal = add(loaded)
    other = base_raw()
    other["project"]["name"] = "Edited since"
    write_manifest(tmp_path, other)
    refused("proposal-mismatch", load_manifest(tmp_path), proposal, tmp_path, monkeypatch)


# --- stale manifest source --------------------------------------------------------------


@native
@pytest.mark.parametrize("operation", ["add", "rehash"])
@pytest.mark.parametrize("change", ["edited", "reformatted", "missing"])
def test_native_manifest_changed_after_proposal_is_stale_before_any_scan(tmp_path, monkeypatch, scans,
                                                                          operation, change):
    if operation == "add":
        loaded = experiment(tmp_path, base_raw())
        proposal = add(loaded)
    else:
        loaded = recorded(tmp_path, base_raw())
        (tmp_path / "data" / "n" / "x.bin").write_bytes(b"changed")
        loaded = load_manifest(tmp_path)
        proposal = rehash(loaded)
    manifest = tmp_path / MANIFEST_FILENAME
    if change == "edited":
        write_manifest(tmp_path, {**load_raw(loaded), "project": {"name": "Other", "id": "demo"}})
    elif change == "reformatted":  # same JSON, other bytes
        manifest.write_bytes(loaded.source_bytes.replace(b"\n", b"\r\n"))
    else:
        manifest.unlink()
    scans.clear()
    err = refused("stale-source", loaded, proposal, tmp_path, monkeypatch)
    assert ("missing" if change == "missing" else "changed since") in str(err)
    assert scans == []  # refused before the folder was measured
    assert manifest.exists() is (change != "missing")


def test_loaded_manifest_must_match_its_own_source(tmp_path, stub, monkeypatch):
    (tmp_path / "data").mkdir()
    write_manifest(tmp_path, base_raw())
    loaded = load_manifest(tmp_path)
    proposal = add(loaded)
    other = Manifest.model_validate({**base_raw(), "project": {"name": "Other", "id": "demo"}})
    for bad in (
        dataclasses.replace(loaded, manifest=other),
        dataclasses.replace(loaded, source_bytes=b"not json"),
        dataclasses.replace(loaded, source_bytes=loaded.source_bytes.decode("utf-8")),
        dataclasses.replace(loaded, path=Path(MANIFEST_FILENAME)),
        dataclasses.replace(loaded, path=str(loaded.path)),
    ):
        refused("loaded-mismatch", bad, proposal, tmp_path, monkeypatch)


@pytest.mark.parametrize("args", [
    ({}, "proposal", False), ("loaded", {}, False), ("loaded", "subclass", False),
    ("loaded", "proposal", None), ("loaded", "proposal", 1), ("loaded", "proposal", "yes"),
])
def test_wrong_argument_types_are_refused(tmp_path, stub, monkeypatch, args):
    (tmp_path / "data").mkdir()
    write_manifest(tmp_path, base_raw())
    loaded = load_manifest(tmp_path)
    proposal = add(loaded)

    @dataclasses.dataclass(frozen=True)
    class Subclass(DirectoryProposal):
        pass

    values = {"loaded": loaded, "proposal": proposal, "subclass": Subclass(**vars(proposal))}
    got_loaded, got_proposal, dry_run = (values.get(a, a) if isinstance(a, str) else a for a in args)
    before = snapshot(tmp_path)
    with no_writes(monkeypatch) as used, pytest.raises(DirectoryApplyError) as info:
        apply_directory_proposal(got_loaded, got_proposal, dry_run=dry_run)
    assert info.value.reason == "invalid-request" and used == [] and snapshot(tmp_path) == before


# --- stale folder or linkage --------------------------------------------------------------


@native
@pytest.mark.parametrize("operation", ["add", "rehash"])
@pytest.mark.parametrize("change", ["content", "new-member", "removed-member", "renamed-member"])
def test_native_folder_changed_after_proposal_is_stale(tmp_path, monkeypatch, operation, change):
    if operation == "add":
        loaded = experiment(tmp_path, base_raw())
        proposal = add(loaded)
    else:
        loaded = recorded(tmp_path, base_raw())
        (tmp_path / "data" / "n" / "x.bin").write_bytes(b"changed")
        loaded = load_manifest(tmp_path)
        proposal = rehash(loaded)
    data = tmp_path / "data"
    if change == "content":
        (data / "n" / "x.bin").write_bytes(b"other bytes")
    elif change == "new-member":
        (data / "late.txt").write_bytes(b"late")
    elif change == "removed-member":
        (data / "empty").rmdir()
    else:
        (data / "n" / "x.bin").rename(data / "n" / "y.bin")
    err = refused("stale-tree", loaded, proposal, tmp_path, monkeypatch)
    assert err.subjects == ("data",) and proposal.new_sha256 in str(err)


@native
def test_native_folder_replaced_by_a_junction_is_refused(tmp_path, monkeypatch):
    loaded = experiment(tmp_path, base_raw())
    proposal = add(loaded)
    (tmp_path / "data").rename(tmp_path / "real")  # same bytes behind the link
    if not make_dir_link(tmp_path / "data", tmp_path / "real"):
        pytest.skip("this platform refused to create a directory link")
    try:
        err = refused("recheck-failed", loaded, proposal, tmp_path, monkeypatch)
        assert (err.authoring_reason, err.scan_reason) == ("scan-failed", "unsafe")
    finally:
        remove_dir_link(tmp_path / "data")


@native
def test_native_removed_folder_is_refused(tmp_path, monkeypatch):
    loaded = experiment(tmp_path, base_raw(), [])
    proposal = add(loaded)
    (tmp_path / "data").rmdir()
    err = refused("recheck-failed", loaded, proposal, tmp_path, monkeypatch)
    assert (err.authoring_reason, err.scan_reason) == ("scan-failed", "missing")


LOCATOR = {"id": "acc", "run_id": "run", "name": "accuracy", "value": 0.9, "artifact_id": "data", "locator": "/acc"}


@native
@pytest.mark.parametrize("operation", ["add", "rehash"])
@pytest.mark.parametrize("change,authoring_reason", [
    ("locator", "locator-unsupported"),
    ("nested-directory", "overlap"),
    ("file-parent", "overlap"),
])
def test_native_linkage_or_overlap_added_after_proposal_is_refused(tmp_path, monkeypatch, operation, change,
                                                                    authoring_reason):
    if operation == "add":
        loaded = experiment(tmp_path, base_raw())
        proposal = add(loaded)
    else:
        loaded = recorded(tmp_path, base_raw())
        (tmp_path / "data" / "n" / "x.bin").write_bytes(b"changed")
        loaded = load_manifest(tmp_path)
        proposal = rehash(loaded)
    edited = load_raw(loaded)
    if change == "locator":
        edited["results"].append(LOCATOR)
    elif change == "nested-directory":
        edited["artifacts"].insert(1, {"id": "inner", "path": "data/n", "artifact_type": "directory",
                                       "digest_format": "runevidence-directory-v1", "sha256": "a" * 64,
                                       "size_bytes": 1})
    else:  # a file record of the experiment folder itself, above data/
        edited["artifacts"].insert(1, {"id": "parent", "path": ".", "sha256": "a" * 64})
    write_manifest(tmp_path, edited)

    # With the snapshot the proposal came from, the file is now stale.
    refused("stale-source", loaded, proposal, tmp_path, monkeypatch)
    # Loaded again, the old proposal is not this manifest's ...
    reloaded = load_manifest(tmp_path)
    refused("proposal-mismatch", reloaded, proposal, tmp_path, monkeypatch)
    # ... and a new one cannot be made.
    with pytest.raises(DirectoryAuthoringError) as info:
        add(reloaded) if operation == "add" else rehash(reloaded)
    assert info.value.reason == authoring_reason


# --- injected faults and timings (not native safety evidence) -----------------------------


@native
def test_injected_manifest_written_during_apply_is_not_overwritten(tmp_path, monkeypatch):
    loaded = experiment(tmp_path, base_raw())
    proposal = add(loaded)
    real = directory_scanner.scan_directory
    other = {**base_raw(), "project": {"name": "Another writer", "id": "demo"}}

    def scan_then_other_writer(root, *args, **kwargs):
        out = real(root, *args, **kwargs)
        write_manifest(tmp_path, other)  # after apply's first check, before save_raw's
        return out

    monkeypatch.setattr("runevidence.directory_scanner.scan_directory", scan_then_other_writer)
    before = state(proposal)
    with pytest.raises(DirectoryApplyError) as info:
        apply_directory_proposal(loaded, proposal, dry_run=False)
    assert info.value.reason == "stale-source"
    assert (tmp_path / MANIFEST_FILENAME).read_bytes() == dump_raw(other).encode("utf-8")
    assert staged_files(tmp_path) == [] and state(proposal) == before


@native
@pytest.mark.parametrize("fault", ["os.replace", "tempfile.NamedTemporaryFile"])
def test_injected_failed_save_keeps_the_manifest(tmp_path, monkeypatch, fault):
    loaded = experiment(tmp_path, base_raw())
    proposal = add(loaded)
    before = snapshot(tmp_path)

    def fail(*args, **kwargs):
        raise PermissionError(13, "injected failure")

    with monkeypatch.context() as m, pytest.raises(DirectoryApplyError) as info:
        m.setattr(fault, fail)
        apply_directory_proposal(loaded, proposal, dry_run=False)
    assert info.value.reason == "write-failed" and "injected failure" in str(info.value)
    assert (tmp_path / MANIFEST_FILENAME).read_bytes() == loaded.source_bytes
    assert staged_files(tmp_path) == [] and snapshot(tmp_path) == before


@native
def test_native_unencodable_text_fails_without_a_change(tmp_path, monkeypatch):
    # JSON can carry a lone surrogate that UTF-8 cannot; save_raw then fails.
    raw = base_raw()
    raw["project"]["description"] = "\ud800"
    (tmp_path / "notes.txt").write_bytes(NOTES)
    build(tmp_path / "data", NESTED)
    (tmp_path / MANIFEST_FILENAME).write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8", newline="\n")
    loaded = load_manifest(tmp_path)
    proposal = add(loaded)
    before = snapshot(tmp_path)
    with pytest.raises(DirectoryApplyError) as info:
        apply_directory_proposal(loaded, proposal, dry_run=False)
    assert info.value.reason == "write-failed"
    assert snapshot(tmp_path) == before and staged_files(tmp_path) == []


def test_injected_unsupported_backend_is_refused(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    write_manifest(tmp_path, base_raw())
    loaded = load_manifest(tmp_path)
    with monkeypatch.context() as m:
        m.setattr("runevidence.directory_scanner.scan_directory",
                  lambda root, *a, **k: DirectoryScan((), DirectoryDigest("b" * 64, 7, 2)))
        proposal = add(loaded)

    def unsupported():
        raise DirectoryScanError("unsupported-platform", "synthetic: no native backend")

    # The real scan_directory runs; only its backend choice is replaced.
    monkeypatch.setattr("runevidence.directory_scanner._native_backend", unsupported)
    err = refused("recheck-failed", loaded, proposal, tmp_path, monkeypatch)
    assert (err.authoring_reason, err.scan_reason) == ("scan-failed", "unsupported-platform")


def test_injected_scan_result_is_compared_not_trusted(tmp_path, stub, monkeypatch):
    (tmp_path / "data").mkdir()
    write_manifest(tmp_path, base_raw())
    loaded = load_manifest(tmp_path)
    proposal = add(loaded)
    stub["scan"] = DirectoryScan((), DirectoryDigest("c" * 64, 7, 2))
    refused("stale-tree", loaded, proposal, tmp_path, monkeypatch)
    stub["scan"] = DirectoryScanError("unstable", "synthetic: changed while read")
    assert refused("recheck-failed", loaded, proposal, tmp_path, monkeypatch).scan_reason == "unstable"


# --- not wired into anything ------------------------------------------------------------


def test_no_command_or_other_module_uses_the_apply_module():
    src = Path(runevidence.__file__).parent
    users = [
        p.relative_to(src).as_posix() for p in src.rglob("*.py")
        if p.name != "directory_apply.py" and "directory_apply" in p.read_text(encoding="utf-8")
    ]
    assert sorted(users) == ["directory_cooperative_apply.py", "directory_preview.py", "directory_publication.py"]


def test_apply_module_imports_only_what_it_needs():
    source = (Path(runevidence.__file__).parent / "directory_apply.py").read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported |= {f"{'.' * node.level}{node.module}.{a.name}" for a in node.names}
    assert imported == {
        "__future__.annotations", "copy", "dataclasses", "dataclasses.dataclass", "pathlib.Path",
        "typing.Callable", "typing.Literal", "typing.Optional", "pydantic.ValidationError",
        ".directory_authoring.DirectoryAuthoringError", ".directory_authoring.DirectoryProposal",
        ".directory_authoring.propose_add_directory", ".directory_authoring.propose_rehash_directory",
        ".edit.EditError", ".manifest.LoadedManifest", ".manifest.dump_raw", ".manifest.load_raw",
        ".manifest.save_raw", ".manifest.validate_manifest",
        ".models.DIRECTORY_DIGEST_FORMAT", ".models.Artifact",
    }
