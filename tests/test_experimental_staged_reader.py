"""R7: private staged read seam against actual readers. Fresh synthetic Temp fixtures only.

Fixture closure: scratch (local mkdir), monkeypatch (pytest undo only). No tmp_path,
experiment, copytree, subprocess, native scanner, DACL, links or permission setters.
Writes here are labelled setup (exclusive synthetic JSON, staging, CAS) or negative
fixture (edit, unlink, corrupt, sidecar) and stay inside the fresh scratch parent.
Monkeypatching cli._load_or_report is TEST-ONLY INJECTION, never a public route.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import io
import json
import os
import sqlite3
import uuid
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

import runevidence
import runevidence.cli as cli
import runevidence.directory_apply as directory_apply
import runevidence.manifest as manifest_module
from runevidence import _experimental_staged_reader as reader
from runevidence import _experimental_versioned_manifest as stage
from runevidence.checks import run_checks
from runevidence.checks.graph import build_index, check_graph, collect_edges
from runevidence.directory_review import review_directories_for_operator
from runevidence.edit import plan_add, plan_rehash, select_artifacts
from runevidence.findings import Code
from runevidence.manifest import LoadedManifest, dump_manifest, load_manifest, load_raw
from runevidence.models import Manifest
from runevidence.report import format_findings, format_overview, to_json

KNOWN = b"known synthetic bytes\n"


@pytest.fixture
def scratch():
    evidence = Path(os.environ["R5_EVIDENCE_ROOT"])
    assert evidence.is_absolute() and evidence.parent == Path(os.environ["LOCALAPPDATA"]) / "Temp"
    parent = evidence / ("runevidence-staging-test-" + uuid.uuid4().hex)
    parent.mkdir(exist_ok=False)
    # Intentionally no cleanup: all fixtures/DBs become evidence.
    return parent


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _project(name="Made-up R7"):
    return {"id": "r7.synthetic", "name": name, "description": "Synthetic only",
            "tags": ["r7"], "metadata": {"custom": {"keep": True}}}


def source(style="crlf", name="Made-up R7"):
    raw = {
        "project": _project(name),
        "runs": [{"id": "r", "params": {"alpha": 0.1}, "metadata": {"opaque": [1, "x"]}}],
        "artifacts": [{"id": "a", "path": "invented", "sha256": "AB" * 32, "size_bytes": 7, "volatile": True,
                       "run_id": "r", "metadata": {"nested": [None, {"x": "y"}]}}],
        "results": [{"id": "res", "run_id": "r", "name": "score", "value": 4, "artifact_id": "a"}],
        "evidence": [{"id": "e", "claim": "Invented claim", "supports": ["res"], "contradicts": []}],
    }
    if style == "crlf":  # CRLF, Unicode, uppercase digest, omitted schema_version/defaults
        raw["project"]["name"] += " café"
        return (json.dumps(raw, ensure_ascii=False, indent=1).replace("\n", "\r\n") + "\r\n").encode("utf-8")
    if style == "whitespace":  # three-space indent, LF, no final newline
        return json.dumps(raw, indent=3).encode("utf-8")
    return dump_manifest(Manifest.model_validate(raw)).encode("utf-8")  # writer's own default form


def tree(parent):
    """Setup: known file, stale file, untracked file and a recorded-but-missing file."""
    data = parent / "data"
    data.mkdir()
    for name, payload in (("known.txt", KNOWN), ("stale.txt", b"changed synthetic bytes\n"),
                          ("extra.txt", b"untracked synthetic bytes\n")):
        with (data / name).open("xb") as fh:
            fh.write(payload)
    raw = {
        "project": _project(),
        "runs": [{"id": "r"}],
        "artifacts": [
            {"id": "known", "path": "data/known.txt", "sha256": _digest(KNOWN), "size_bytes": len(KNOWN),
             "run_id": "r"},
            {"id": "stale", "path": "data/stale.txt", "sha256": _digest(b"old\n"), "size_bytes": 4, "run_id": "r"},
            {"id": "missing", "path": "data/missing.txt", "sha256": "0" * 64, "size_bytes": 1, "run_id": "r"},
        ],
        "results": [{"id": "res", "run_id": "r", "name": "score", "value": 1, "artifact_id": "known"}],
        "evidence": [{"id": "e", "claim": "Rests on a known file", "supports": ["res"]},
                     {"id": "m", "claim": "Rests on a missing file", "supports": ["missing"]}],
    }
    return (json.dumps(raw, indent=1).replace("\n", "\r\n") + "\r\n").encode("utf-8")


def staged(parent, payload=None):
    """Setup: exclusive synthetic JSON, actual loader, actual R5 staging in fresh scratch."""
    path = parent / "invented-manifest.json"
    with path.open("xb") as fh:
        fh.write(source() if payload is None else payload)
    original = load_manifest(path)
    return original, stage.stage_validated_manifest(original, scratch_parent=parent)


class WriterReached(AssertionError):
    pass


def no_writers(monkeypatch):
    """Sentinels on every production/stage writer route; installed after setup."""
    hits = []
    routes = [(manifest_module, "save_raw"), (manifest_module, "save_manifest"), (cli, "save_raw"),
              (cli, "save_manifest"), (directory_apply, "save_raw"), (directory_apply, "apply_directory_proposal"), (os, "replace"),
              (stage, "stage_validated_manifest"), (stage, "compare_and_update_stage")]
    for module, name in routes:
        label = f"{module.__name__}.{name}"

        def sentinel(*args, _label=label, **kwargs):
            hits.append(_label)
            raise WriterReached(_label)

        monkeypatch.setattr(module, name, sentinel)
    return hits


def connections(monkeypatch):
    modes = []
    real = stage._connect

    def spy(path, *, writable):
        modes.append(writable)
        return real(path, writable=writable)

    monkeypatch.setattr(stage, "_connect", spy)
    return modes


def opened(monkeypatch):
    seen = []
    real = Path.open

    def spy(self, mode="r", *args, **kwargs):
        seen.append((str(self), mode))
        return real(self, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy)
    return seen


def tree_state(parent):
    state = {}
    for folder, dirs, files in os.walk(parent):
        for name in dirs + files:
            p = Path(folder, name)
            s = p.lstat()
            state[p.relative_to(parent).as_posix()] = (
                s.st_size, s.st_mtime_ns, s.st_ino, _digest(p.read_bytes()) if p.is_file() else None)
    return state


def findings(report):
    return [f.model_dump(mode="json") for f in report.findings]


def run_cli(argv):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli.main(argv)
    return code, buffer.getvalue()


def refused(receipt):
    with pytest.raises(reader.StagedReadError) as caught:
        reader.load_staged_for_read(receipt)
    return caught.value


@pytest.mark.parametrize("kind", ["crlf", "whitespace", "default"])
def test_view_is_actual_loaded_manifest_with_exact_bytes(scratch, monkeypatch, kind):
    original, receipt = staged(scratch, source(kind))
    hits, modes = no_writers(monkeypatch), connections(monkeypatch)
    view = reader.load_staged_for_read(receipt)
    assert type(view) is reader.StagedLoadedRead and type(view.loaded) is LoadedManifest
    assert view.loaded.source_bytes == original.source_bytes == original.path.read_bytes() == source(kind)
    assert view.loaded.manifest == original.manifest
    assert view.loaded.path == original.path == view.source_path == receipt.source_path
    assert view.loaded.path != receipt.database_path and view.loaded.root == scratch
    assert load_raw(view.loaded) == load_raw(original)
    assert (view.stage_revision, view.stage_generation) == (1, 1)
    assert view.stage_sha256 == receipt.source_sha256 == _digest(source(kind))
    assert view.parity == "verified-at-observation-only"
    assert view.loaded.manifest.runs[0].metadata == {"opaque": [1, "x"]}
    with pytest.raises(FrozenInstanceError):
        view.stage_revision = 2
    assert modes == [False] and hits == []


def test_run_checks_and_graph_equal_with_relative_root(scratch, monkeypatch):
    original, receipt = staged(scratch, tree(scratch))
    hits = no_writers(monkeypatch)
    view = reader.load_staged_for_read(receipt).loaded
    for untracked in (False, True):
        baseline, viewed = run_checks(original, include_untracked=untracked), run_checks(view, include_untracked=untracked)
        assert findings(baseline) == findings(viewed) and baseline.exit_code() == viewed.exit_code() == 1
        assert baseline.manifest_path == viewed.manifest_path == original.path
    report = run_checks(view)
    assert [f.subject for f in report.findings if f.code is Code.missing_file] == ["missing"]
    assert [f.subject for f in report.findings if f.code is Code.hash_mismatch] == ["stale"]
    assert not [f for f in report.findings if f.subject == "known" and f.code in (Code.missing_file, Code.hash_mismatch)]
    assert build_index(original.manifest) == build_index(view.manifest)
    assert collect_edges(original.manifest) == collect_edges(view.manifest)
    assert check_graph(original.manifest, {"missing"}) == check_graph(view.manifest, {"missing"})
    assert hits == []


def test_report_text_equal(scratch, monkeypatch):
    original, receipt = staged(scratch, tree(scratch))
    hits = no_writers(monkeypatch)
    view = reader.load_staged_for_read(receipt).loaded
    texts = []
    for loaded in (original, view):
        report = run_checks(loaded, include_untracked=True)
        texts.append([format_findings(report), format_findings(report, strict=True),
                      format_findings(report, show_info=False), format_overview(loaded.manifest),
                      to_json(report), to_json(report, manifest=loaded.manifest)])
    assert texts[0] == texts[1] and "missing-file" in texts[1][0] and "caf" not in texts[1][3]
    assert hits == []


def test_in_memory_edit_planners_equal_without_saving(scratch, monkeypatch):
    original, receipt = staged(scratch, tree(scratch))
    before = tree_state(scratch)
    hits = no_writers(monkeypatch)
    view = reader.load_staged_for_read(receipt).loaded
    results = []
    for loaded in (original, view):
        raw = load_raw(loaded)
        indexes = select_artifacts(loaded, ["id:stale"], cwd=scratch)
        plan = plan_rehash(loaded, raw, indexes)
        raw_add = load_raw(loaded)
        added = plan_add(loaded, raw_add, [scratch / "data" / "extra.txt"], cwd=scratch)
        results.append((indexes, plan, raw, added, raw_add))
    assert results[0] == results[1]
    assert [f.id for f in results[1][1].changed_files] == ["stale"]
    assert [a["path"] for a in results[1][3].added] == ["data/extra.txt"]
    assert tree_state(scratch) == before and hits == []


def test_directory_review_empty_batch_rereads_json(scratch, monkeypatch):
    original, receipt = staged(scratch, tree(scratch))
    hits = no_writers(monkeypatch)
    view = reader.load_staged_for_read(receipt).loaded
    seen = opened(monkeypatch)
    baseline = review_directories_for_operator(original, requests=())
    baseline_reads = [x for x in seen if x[0] == str(original.path)]
    seen.clear()
    viewed = review_directories_for_operator(view, requests=())
    viewed_reads = [x for x in seen if x[0] == str(original.path)]
    assert (viewed.mode, viewed.report.state) == (baseline.mode, baseline.report.state) == ("batch", "no-requests")
    assert viewed.report == baseline.report and viewed.text == baseline.text and viewed.caveats == baseline.caveats
    # Known gap: directory_batch._source re-reads the JSON file even for the staged view.
    assert viewed_reads == baseline_reads and viewed_reads and all(mode == "rb" for _, mode in viewed_reads)
    assert hits == []


@pytest.mark.parametrize("kind", ["same-size", "longer"])
def test_json_edit_after_staging_refused(scratch, monkeypatch, kind):
    original, receipt = staged(scratch)
    payload = original.source_bytes
    edited = payload.replace(b"Made-up R7", b"Made-up R8") if kind == "same-size" else payload + b" "
    assert edited != payload and (len(edited) == len(payload)) == (kind == "same-size")
    original.path.write_bytes(edited)  # negative fixture: legacy direct JSON edit
    hits = no_writers(monkeypatch)
    error = refused(receipt)
    assert (error.reason, error.subject) == ("stale-source", "source")
    assert error.check in ("identity-before", "bytes-at-read")
    assert stage.read_staged_manifest(receipt).source_bytes == payload  # the stage itself never notices
    assert hits == []


@pytest.mark.parametrize("kind", ["different-bytes", "identical-bytes"])
def test_stage_cas_without_json_update_refused(scratch, monkeypatch, kind):
    original, receipt = staged(scratch)
    replacement = source(name="Advanced only in stage") if kind == "different-bytes" else original.source_bytes
    outcome = stage.compare_and_update_stage(receipt, expected_revision=1, operation_id="r7-setup",
                                             replacement_source_bytes=replacement)  # setup: stage-only CAS
    assert (outcome.status, outcome.revision) == ("applied", 2)
    hits, seen = no_writers(monkeypatch), opened(monkeypatch)
    assert stage.read_staged_manifest(receipt).revision == 2
    error = refused(receipt)
    assert error.diagnostics == {"reason": "stale-source", "subject": "stage",
                                 "check": "revision-or-digest-divergence", "stage_reason": None}
    assert not [x for x in seen if x[0] == str(original.path)]
    assert original.path.read_bytes() == original.source_bytes and hits == []


@pytest.mark.parametrize("kind", ["missing", "corrupt", "sidecar"])
def test_unusable_stage_refused_without_json_fallback(scratch, monkeypatch, kind):
    original, receipt = staged(scratch)
    database = receipt.database_path
    if kind == "missing":
        database.unlink()  # negative fixture
    elif kind == "corrupt":
        database.write_bytes(b"not SQLite")  # negative fixture, disposable synthetic store
    else:
        with Path(str(database) + "-journal").open("xb") as fh:  # negative fixture
            fh.write(b"unexpected")
    hits, seen = no_writers(monkeypatch), opened(monkeypatch)
    loads = []
    monkeypatch.setattr(manifest_module, "load_manifest", lambda *a, **kw: loads.append(a) or pytest.fail("fallback"))
    error = refused(receipt)
    expected = {"missing": ("missing", "stage", None), "corrupt": ("invalid-stage", "stage", "invalid"),
                "sidecar": ("invalid-stage", "stage", None)}[kind]
    assert (error.reason, error.subject, error.stage_reason) == expected
    assert not [x for x in seen if x[0] == str(original.path)] and not loads and hits == []
    assert str(original.path) not in str(error) and str(scratch) not in str(error)
    assert original.path.read_bytes() == original.source_bytes


def test_busy_stage_refused_without_retry_or_fallback(scratch, monkeypatch):
    original, receipt = staged(scratch)
    before = receipt.database_path.read_bytes()
    holder = sqlite3.connect(receipt.database_path, isolation_level=None)  # setup: separate participant
    try:
        holder.execute("BEGIN EXCLUSIVE")
        seen = opened(monkeypatch)
        error = refused(receipt)
        assert (error.reason, error.subject, error.stage_reason) == ("busy", "stage", "busy")
        assert not [x for x in seen if x[0] == str(original.path)]
    finally:
        holder.close()
    assert reader.load_staged_for_read(receipt).loaded.source_bytes == original.source_bytes
    assert receipt.database_path.read_bytes() == before


FORGED = ["not-a-receipt", "relative-source", "database-as-source", "outside-scratch", "dotdot-spelling",
          "same-bytes-copy", "forged-identity", "forged-store", "foreign-project", "forged-revision"]


@pytest.mark.parametrize("kind", FORGED)
def test_forged_alias_and_foreign_receipts_refused(scratch, monkeypatch, kind):
    original, receipt = staged(scratch)
    before = receipt.database_path.read_bytes()
    if kind == "same-bytes-copy":
        alias = scratch / "copy-manifest.json"
    elif kind == "outside-scratch":
        sibling = scratch.parent / ("runevidence-staging-test-" + uuid.uuid4().hex)
        sibling.mkdir()
        alias = sibling / original.path.name
    if kind in ("same-bytes-copy", "outside-scratch"):
        with alias.open("xb") as fh:  # negative fixture: identical bytes at another path
            fh.write(original.source_bytes)
    forged = {
        "not-a-receipt": lambda: object(),
        "relative-source": lambda: replace(receipt, source_path=Path(original.path.name)),
        "database-as-source": lambda: replace(receipt, source_path=receipt.database_path),
        "outside-scratch": lambda: replace(receipt, source_path=alias),
        "dotdot-spelling": lambda: replace(receipt, source_path=scratch / ".." / scratch.name / original.path.name),
        "same-bytes-copy": lambda: replace(receipt, source_path=alias),
        "forged-identity": lambda: replace(receipt, source_identity=(0, 0, 1, 0, 0)),
        "forged-store": lambda: replace(receipt, store_id=uuid.uuid4().hex),
        "foreign-project": lambda: replace(receipt, project_id="foreign"),
        "forged-revision": lambda: replace(receipt, revision=2),
    }[kind]()
    hits = no_writers(monkeypatch)
    error = refused(forged)
    expected = {"same-bytes-copy": ("stale-source", "source"), "forged-identity": ("stale-source", "source"),
                "forged-store": ("invalid-stage", "stage"), "foreign-project": ("invalid-stage", "stage"),
                "forged-revision": ("invalid-stage", "stage")}.get(kind, ("invalid-stage", "receipt"))
    assert (error.reason, error.subject) == expected
    assert stage.read_staged_manifest(receipt).revision == 1
    assert receipt.database_path.read_bytes() == before and hits == []


def test_missing_source_refused_although_stage_is_readable(scratch, monkeypatch):
    original, receipt = staged(scratch)
    original.path.unlink()  # negative fixture
    hits = no_writers(monkeypatch)
    error = refused(receipt)
    assert (error.reason, error.subject) == ("missing", "source")
    assert stage.read_staged_manifest(receipt).source_bytes == original.source_bytes  # R5 stage alone never reads JSON
    assert hits == []


def test_reader_only_calls_leave_db_source_and_tree_unchanged(scratch, monkeypatch):
    original, receipt = staged(scratch, tree(scratch))
    before = tree_state(scratch)
    hits, modes = no_writers(monkeypatch), connections(monkeypatch)
    views = [reader.load_staged_for_read(receipt) for _ in range(2)]
    assert views[0] == views[1]
    assert tree_state(scratch) == before
    assert sorted(os.listdir(receipt.database_path.parent)) == ["stage.sqlite"]
    assert modes == [False, False] and hits == []


def test_public_cli_stays_json_only_and_refuses_stage_paths(scratch, monkeypatch):
    original, receipt = staged(scratch)
    before = tree_state(scratch)
    hits = no_writers(monkeypatch)
    for command in ("validate", "inspect"):
        code, out = run_cli([command, str(receipt.database_path)])
        assert code == 2 and "manifest-unreadable" in out
        code, out = run_cli([command, str(receipt.database_path.parent)])
        assert code == 2 and "manifest-not-found" in out
        code, out = run_cli([command, str(original.path)])
        assert code == 1 and "missing-file" in out
    text = Path(cli.__file__).read_text(encoding="utf-8")
    assert "sqlite" not in text.lower() and "_experimental" not in text
    assert tree_state(scratch) == before and hits == []


def test_cli_with_test_only_injection_matches_unmodified_json(scratch, monkeypatch):
    # TEST-ONLY INJECTION: cli._load_or_report is replaced inside this process only.
    original, receipt = staged(scratch, tree(scratch))
    before = tree_state(scratch)
    hits = no_writers(monkeypatch)
    view = reader.load_staged_for_read(receipt).loaded
    p = str(original.path)
    # R13 scope: only the non-editing CLI reader commands are admitted.
    # Historical R11 route-execution deviations remain in the evidence record.
    argvs = [["validate", p], ["validate", p, "--json"], ["validate", p, "--strict"],
             ["inspect", p], ["inspect", p, "--json"]]
    def admitted_cli(argv):
        assert argv and argv[0] in {"validate", "inspect"}, "non-reader CLI route forbidden"
        return run_cli(argv)
    baseline = [admitted_cli(argv) for argv in argvs]
    calls = []
    forwarded = []
    def injected_load(target, as_json, redact_manifest_path=False, summary_only=False):
        # Mirrors the current cli._load_or_report signature; records the target and returns view.
        calls.append(target)
        forwarded.append((as_json, redact_manifest_path, summary_only))
        return view
    monkeypatch.setattr(cli, "_load_or_report", injected_load)
    injected = [admitted_cli(argv) for argv in argvs]
    assert injected == baseline and len(calls) == len(argvs) == 5
    assert calls == [Path(p)] * 5
    assert forwarded == [(False, False, False), (True, False, False), (False, False, False),
                         (False, False, False), (True, False, False)]
    assert [code for code, _ in baseline] == [1, 1, 1, 1, 1]
    assert tree_state(scratch) == before and hits == []


def test_reader_is_private_read_only_and_unreferenced():
    text = Path(reader.__file__).read_text(encoding="utf-8")
    tree_ = ast.parse(text)
    imports = sorted((node.module or "", tuple(a.name for a in node.names)) for node in ast.walk(tree_)
                     if isinstance(node, ast.ImportFrom) and node.level == 1)
    assert imports == [("", ("_experimental_versioned_manifest",)),
                       ("manifest", ("LoadedManifest", "validate_manifest")),
                       ("models", ("is_manifest",))]
    names = ({n.attr for n in ast.walk(tree_) if isinstance(n, ast.Attribute)}
             | {n.id for n in ast.walk(tree_) if isinstance(n, ast.Name)})
    assert "read_staged_manifest" in names
    assert not names & {"save_raw", "save_manifest", "replace", "rename", "stage_validated_manifest",
                        "compare_and_update_stage", "connect", "_connect", "load_manifest", "write_bytes",
                        "write_text", "unlink", "remove", "rmdir", "mkdir", "chmod", "utime", "copy"}
    package = Path(runevidence.__file__).parent
    references = [p.name for p in package.rglob("*.py")
                  if p.name != "_experimental_staged_reader.py"
                  and "_experimental_staged_reader" in p.read_text(encoding="utf-8")]
    # R8's sole approved private consumer; no public or other package reader references.
    assert references == ["_experimental_staged_validation.py"]
    facade_path = package / references[0]
    facade_tree = ast.parse(facade_path.read_text(encoding="utf-8"))
    reader_imports = [node for node in ast.walk(facade_tree)
                      if isinstance(node, ast.ImportFrom) and node.level == 1
                      and node.module is None
                      and [(alias.name, alias.asname) for alias in node.names]
                      == [("_experimental_staged_reader", "_reader")]]
    assert len(reader_imports) == 1
    # Public CLI, package exports and all other package modules stay isolated from R8.
    facade_references = sorted(p.name for p in package.rglob("*.py")
                               if p.name != "_experimental_staged_validation.py"
                               and "_experimental_staged_validation" in p.read_text(encoding="utf-8"))
    assert facade_references == []
    choices = next(action.choices for action in cli.build_parser()._actions
                   if hasattr(action, "choices") and isinstance(action.choices, dict))
    # HISTORICAL R7/R8 BASELINE (preserved record): the public surface was exactly
    # these seven routes when this reader seam was qualified.
    historical_seven = {"validate", "inspect", "init", "rehash", "add", "schema", "adapters"}
    # RG-IND11-PC4 (2026-11-09) current reviewed public surface: exactly 23 routes.
    # RG-IND11-PC4 (2026-11-09) current reviewed public surface: exactly 23 routes.
    # M9-COMPARABILITY-STAGE2B-R1 (2026-11-10): 24 routes after the reviewed addition.
    # M9-COMPARABILITY-STAGE3B-2C1 (2026-11-11): 25 routes after the explicit migrate addition.
    # M9-COMPARABILITY-STAGE3D3-2 (2026-11-11): 26 routes after the conditions addition.
    # Any addition or removal must fail here until it is re-reviewed.
    current_public = {"validate", "validate-batch", "inspect", "compare", "comparability", "assess", "leaderboard", "stats", "matrix", "run", "db-export", "db-check", "db-query", "runs",
                      "claim", "lineage", "impact", "coverage", "coverage-gate", "fingerprint",
                      "fingerprint-compare", "coverage-diff", "review-diff", "impact-diff", "review-gate",
                      "result", "evidence", "artifact", "relationship",
                      "init", "migrate", "conditions", "capture", "rehash", "add", "schema", "adapters"}
    assert historical_seven <= current_public and len(current_public) == 37
    assert set(choices) == current_public
    # The protected property: no private, experimental, staging or SQLite route is public.
    assert not [c for c in choices if c.startswith("_")
                or any(m in c.lower() for m in ("experimental", "sqlite", "stage", "shadow", "witness"))]


def test_writer_sentinels_are_live(scratch, monkeypatch):
    hits = no_writers(monkeypatch)
    with pytest.raises(WriterReached):
        manifest_module.save_raw({}, scratch / "never.json", expected_bytes=b"")
    with pytest.raises(WriterReached):
        os.replace(scratch / "never-a", scratch / "never-b")
    with pytest.raises(WriterReached):
        directory_apply.apply_directory_proposal(object(), object())
    with pytest.raises(WriterReached):
        stage.compare_and_update_stage(object(), expected_revision=1, operation_id="x", replacement_source_bytes=b"")
    assert hits == ["runevidence.manifest.save_raw", "os.replace", "runevidence.directory_apply.apply_directory_proposal",
                    "runevidence._experimental_versioned_manifest.compare_and_update_stage"]
    assert sorted(os.listdir(scratch)) == []


def test_source_changes_during_stage_read_refused(scratch, monkeypatch):
    original, receipt = staged(scratch)
    real_read = stage.read_staged_manifest
    def change_source(stage_receipt):
        result = real_read(stage_receipt)
        original.path.write_bytes(original.source_bytes + b" ")
        return result
    monkeypatch.setattr(stage, "read_staged_manifest", change_source)
    hits = no_writers(monkeypatch)
    error = refused(receipt)
    assert (error.reason, error.subject) == ("stale-source", "source")
    assert hits == []


def test_schema_and_readback_model_mismatch_refused(scratch, monkeypatch):
    original, receipt = staged(scratch)
    hits = no_writers(monkeypatch)
    schema_error = refused(replace(receipt, schema_version="foreign"))
    assert (schema_error.reason, schema_error.subject) == ("invalid-stage", "stage")
    real_read = stage.read_staged_manifest
    def mismatched_model(stage_receipt):
        result = real_read(stage_receipt)
        return replace(result, manifest=result.manifest.model_copy(update={"schema_version": "foreign"}))
    monkeypatch.setattr(stage, "read_staged_manifest", mismatched_model)
    model_error = refused(receipt)
    assert (model_error.reason, model_error.subject, model_error.check) == ("invalid-stage", "stage", "readback-model")
    assert original.path.read_bytes() == original.source_bytes and hits == []