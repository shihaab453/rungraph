"""Command line interface.

Exit codes (stable, safe to use in CI):
  0  no errors (warnings are allowed unless --strict)
  1  the evidence has problems: errors found, or warnings with --strict
  2  could not check at all: manifest missing, unreadable or schema-invalid,
     bad arguments, or init refused to overwrite, could not read the folder,
     or could not write its backup or the new manifest (nothing is replaced);
     for rehash and add: the change could not be made, and nothing was written;
     for a built-in writer on a POSIX-flock host: another cooperating writer
     holds the folder lock (BUSY), or the mandatory folder lock could not be
     taken (LOCK_UNAVAILABLE); in both cases nothing was written

validate, validate-batch, inspect and adapters never write. schema -o writes a schema file.
init, migrate, conditions, rehash and add write the manifest; init --force, migrate
and conditions keep a backup generation and never overwrite or delete an earlier one.
On hosts with POSIX directory flock (``fcntl.flock``), writers resolve, take one
non-blocking folder admission lock, then load and write. A second cooperating writer
refuses with exit 2 rather than waiting, and a lock that cannot be taken is a refusal
(LOCK_UNAVAILABLE), never an unlocked write. Hosts without directory flock (for example
Windows) keep the legacy unlocked writer behavior and are outside that guarantee; no
Windows parity is claimed. --dry-run takes no lock. Only this installed version's CLI
participates; other writers are not controlled.
"""

from __future__ import annotations

import argparse
import inspect
import json
import hmac
import re
import io
import os
import signal
import sqlite3
import stat
import sys
from collections import Counter
from contextlib import contextmanager, redirect_stderr
from pathlib import Path
from typing import Optional, Sequence

from pydantic import ValidationError

from . import __version__
from . import run_comparability
from .adapters import AdapterError, available_adapters, get_adapter
from .tag_filter import InvalidTagPattern, TagFilter, TagPatternTooExpensive
from .adapters.generic import valid_slug
from .checks import EXIT_FINDINGS, EXIT_INVALID, EXIT_OK, Report, run_checks
from .checks.results import _tensorboard_path_matches
from .edit import EditError, glob_match, plan_add, plan_refresh, plan_rehash, select_artifacts
from .manifest import (LoadedManifest, ManifestLoadError, load_manifest, load_raw,
                       parse_manifest_source, resolve_manifest_path, save_manifest,
                       save_raw)
from . import authoring, capture
from .conditions_authoring import FIELDS as CONDITIONS_FIELDS, ConditionsError, author_conditions
from .migrate import TARGET_VERSIONS, MigrationError, migrate_manifest
from .models import (
    BACKUP_FILENAME,
    MANIFEST_FILENAME,
    Manifest,
    Manifest05,
    Manifest06,
    Manifest07,
    Manifest08,
    Manifest09,
    Manifest10,
    backup_generation,
    json_schema,
)
from .report import format_findings, format_markdown, format_overview, to_json
from .run_compare import ComparisonError, compare_runs, format_comparison
from .run_leaderboard import LeaderboardError, rank_runs, format_leaderboard
from .run_matrix import MatrixError, metric_matrix, format_metric_matrix
from .run_stats import format_stats, stats_manifest
from .db_export import check_sqlite, export_sqlite, query_sqlite
from .db_import import import_sqlite
from .run_inventory import format_runs, runs_manifest
from .run_details import RunDetailsError, run_details, format_run_details
from .run_claim import ClaimDetailsError, claim_details, format_claim_details
from .run_lineage import LineageError, claim_lineage, format_claim_lineage
from .run_impact import ImpactError, claim_impact, format_claim_impact
from .run_impact_diff import ImpactDiffError, compare_claim_impact, format_impact_diff
from .run_coverage import CoverageError, claim_coverage, format_claim_coverage
from .run_coverage_diff import CoverageDiffError, compare_claim_coverage, format_coverage_diff
from .run_review_diff import ReviewDiffError, compare_claim_review, format_claim_review
from .run_review_gate import ReviewGateError, evaluate_review_gate, format_review_gate
from .run_coverage_gate import CoverageGateError, evaluate_coverage_gate, format_coverage_gate
from .run_fingerprint import FingerprintError, manifest_fingerprint, format_fingerprint
from .run_fingerprint_explain import (explain_fingerprint_difference, format_fingerprint_explanation,
                                      redact_fingerprint_explanation)


def _validation_summary_data(report: Report, exit_code: int) -> dict:
    """Count-oriented validate summary omitting direct manifest/finding details.
    Category keys come from supplied code/severity values. In-tree producers
    use declared enums; this helper does not validate category provenance.
    by_severity and by_code list only values that occurred (count > 0), with
    keys sorted lexicographically; no findings gives {} for both."""
    codes = report.codes()
    severities = Counter(f.severity.value for f in report.findings)
    return {
        "summary_version": 1,
        "total_findings": len(report.findings),
        "by_severity": {s: severities[s] for s in sorted(severities)},
        "by_code": {code: codes[code] for code in sorted(codes)},
        "exit_code": exit_code,
    }


def _validation_summary_json(report: Report, exit_code: int) -> str:
    return json.dumps(_validation_summary_data(report, exit_code), indent=2)


def _load_or_report(target: Path, as_json: bool, redact_manifest_path: bool = False,
                    summary_only: bool = False):
    try:
        return load_manifest(target)
    except ManifestLoadError as exc:
        if as_json and summary_only:
            # Redaction does not alter summary fields; category keys retain supplied values.
            report = Report(manifest_path=Path(target), findings=exc.findings)
            print(_validation_summary_json(report, EXIT_INVALID))
        elif as_json:
            report = Report(manifest_path=Path(target), findings=exc.findings)
            data = json.loads(to_json(report))
            data["exit_code"] = EXIT_INVALID
            if redact_manifest_path:
                data["manifest"] = "<redacted>"
                for finding in data["findings"]:
                    finding["path"] = "<redacted>"
                    if finding["code"] == "manifest-not-found":
                        finding["message"] = "Manifest file was not found."
                        finding["hint"] = "Check that the specified manifest file exists and is accessible."
                    elif finding["code"] == "manifest-unreadable":
                        finding["message"] = "Could not read manifest as UTF-8 JSON."
                        if finding.get("hint") is not None:
                            finding["hint"] = "Check that the manifest is accessible and contains valid UTF-8 JSON."
            print(json.dumps(data, indent=2))
        else:
            for f in exc.findings:
                print(f"ERROR {f.code.value}: {f.message}")
                if f.hint:
                    print(f"      hint: {f.hint}")
            print(f"INVALID: manifest could not be checked (exit {EXIT_INVALID})")
        return None


# One fixed, value-free line for a cooperating writer that lost the admission
# lock. It matches the S4-1 core's "busy" reason text exactly.
BUSY_TEXT = (
    "ERROR (BUSY): another RunEvidence writer is updating the manifest. "
    "Nothing was written."
)

# The lock is mandatory for every real write: an unavailable lock is a refusal,
# never a silently unlocked write. The line is fixed and carries no path,
# reason, OS text, ID or value.
LOCK_UNAVAILABLE_TEXT = (
    "ERROR (LOCK_UNAVAILABLE): the manifest folder could not be locked. "
    "Nothing was written."
)


class _WriterBusy(Exception):
    """Another cooperating RunEvidence writer holds the folder admission lock."""


class _WriterLockUnavailable(Exception):
    """The admission lock could not be taken for a reason other than contention."""


def _directory_flock_available() -> bool:
    """Whether this host can take a POSIX directory ``flock``.

    This is the exact scope of the S4-2a admission guarantee: POSIX
    ``fcntl.flock`` plus ``O_DIRECTORY``. It is a cheap capability probe and
    deliberately never imports the publication module. Hosts without it
    (Windows, or any POSIX host lacking ``fcntl.flock``) keep the pre-retrofit
    unlocked writer behavior and are outside the guarantee.
    """
    if os.name != "posix" or not hasattr(os, "O_DIRECTORY"):
        return False
    try:
        import fcntl
    except ImportError:
        return False
    return hasattr(fcntl, "flock")


def _writer_target_folder_exists(target) -> bool:
    """Cheap path-shape check before admission; never reads the manifest.

    Mirrors ``load_manifest``: apply the one-level directory rule, then require
    the containing folder to exist. A false result keeps the pre-retrofit
    missing/non-directory error and never admits an unknown path. A true result
    still goes through admission before any load.
    """
    try:
        manifest = resolve_manifest_path(Path(target))
        if manifest.is_dir():
            # The loader would try to open this directory as a file and fail;
            # keep that legacy refusal instead of locking its parent.
            return False
        return manifest.parent.is_dir()
    except (OSError, RuntimeError, TypeError, ValueError):
        return False


@contextmanager
def _writer_lock(target, *, enabled: bool = True, follow_manifest_symlink: bool = True):
    """Hold the S4-1 folder admission lock around one built-in write.

    Acquisition happens before the command loads the manifest and release after
    it saves. ``enabled=False`` is the dry-run branch: no import, no resolve, no
    lock and no fsync.

    Yields the manifest path the command must load and save. On an admitted
    write that is the *resolved* target under the locked folder (following the
    loader's nested-directory rule), never the caller's alias, so a later alias
    retarget cannot move the load or save outside the lock. ``init`` passes
    ``follow_manifest_symlink=False`` so it locks and writes the folder entry it
    names. Without a lock (dry run, or a host without directory flock) it
    yields the caller's original target and behavior is unchanged.

    The guarantee is POSIX-flock-only. Where ``_directory_flock_available()``
    is false the command runs unlocked, exactly as before the retrofit: no
    BUSY, no LOCK_UNAVAILABLE and no serialization claim. Where it is true,
    failure is closed: only actual contention raises ``_WriterBusy`` (BUSY); an
    import failure, directory open failure, flock error or resolution failure
    raises ``_WriterLockUnavailable`` and the command refuses before load or
    write. On Linux with ``fcntl.flock`` the unlocked branch is unreachable.
    Nothing else from the publication module is reachable here.
    """
    if not enabled:
        yield target
        return
    if not _directory_flock_available():
        # Legacy behavior on hosts without POSIX directory flock. Not serialized.
        yield target
        return
    try:
        from .directory_publication import (
            DirectoryPublicationError,
            builtin_writer_admission_lock,
        )
    except Exception:
        raise _WriterLockUnavailable() from None
    handle = builtin_writer_admission_lock(
        target, follow_manifest_symlink=follow_manifest_symlink
    )
    try:
        locked = handle.__enter__()
    except DirectoryPublicationError as exc:
        if exc.reason == "busy":
            raise _WriterBusy() from None
        raise _WriterLockUnavailable() from None
    except Exception:
        raise _WriterLockUnavailable() from None
    try:
        yield locked.target
    finally:
        handle.__exit__(None, None, None)


def _busy_failure() -> int:
    """Print the fixed busy line for init/add/rehash and refuse with exit 2."""
    print(BUSY_TEXT)
    return EXIT_INVALID


def _lock_unavailable_failure() -> int:
    """Print the fixed unavailable-lock line and refuse with exit 2."""
    print(LOCK_UNAVAILABLE_TEXT)
    return EXIT_INVALID


def cmd_validate(args: argparse.Namespace) -> int:
    summary_only = getattr(args, "summary_only", False)
    loaded = _load_or_report(args.path, args.json, args.redact_manifest_path, summary_only)
    if loaded is None:
        return EXIT_INVALID
    report = run_checks(loaded, enable_directory_artifacts=getattr(args, "directory_artifacts", False))
    if args.json and summary_only:
        print(_validation_summary_json(report, report.exit_code(args.strict)))
    elif args.json:
        output = to_json(report, args.strict)
        if args.redact_manifest_path:
            data = json.loads(output)
            data["manifest"] = "<redacted>"
            output = json.dumps(data, indent=2)
        print(output)
    else:
        print(f"Checking {loaded.path}")
        print(format_findings(report, args.strict))
    return report.exit_code(args.strict)


def cmd_validate_batch(args: argparse.Namespace) -> int:
    """Counts-only summaries for several manifests, checked one at a time.

    Each input gets the summary that 'validate PATH --json --summary-only'
    (with the same --strict) gives it on its own: one load and one ordinary
    validate check run per input, in input order, duplicates included. A
    manifest load error becomes that input's exit 2 summary and the batch
    continues. The process exits with the highest individual exit code.
    --redact-manifest-path replaces each echoed input operand with "<redacted>".
    Summary and aggregate category keys retain supplied code/severity values."""
    if not args.json:
        print("ERROR: validate-batch requires --json", file=sys.stderr)
        return EXIT_INVALID
    results = []
    severities: Counter = Counter()
    codes: Counter = Counter()
    for target in args.manifests:
        try:
            loaded = load_manifest(Path(target))
        except ManifestLoadError as exc:
            # Same report and exit code as _load_or_report gives standalone validate.
            report = Report(manifest_path=Path(target), findings=exc.findings)
            exit_code = EXIT_INVALID
        else:
            report = run_checks(
                loaded, enable_directory_artifacts=args.directory_artifacts
            )
            exit_code = report.exit_code(args.strict)
        summary = _validation_summary_data(report, exit_code)
        severities.update(summary["by_severity"])
        codes.update(summary["by_code"])
        results.append({
            "manifest": "<redacted>" if args.redact_manifest_path else target,
            "summary": summary,
        })
    exit_code = max(result["summary"]["exit_code"] for result in results)
    data = {
        "batch_version": 1,
        "results": results,
        "aggregate": {
            "total_manifests": len(results),
            "total_findings": sum(result["summary"]["total_findings"] for result in results),
            "by_severity": {s: severities[s] for s in sorted(severities) if severities[s] > 0},
            "by_code": {c: codes[c] for c in sorted(codes) if codes[c] > 0},
            "exit_code": exit_code,
        },
    }
    print(json.dumps(data, indent=2))
    return exit_code


def cmd_inspect(args: argparse.Namespace) -> int:
    loaded = _load_or_report(args.path, args.json, args.redact_manifest_path)
    if loaded is None:
        return EXIT_INVALID
    report = run_checks(loaded, include_untracked=True,
                        enable_directory_artifacts=getattr(args, "directory_artifacts", False))
    if args.json:
        output = to_json(report, args.strict, manifest=loaded.manifest)
        if args.redact_manifest_path:
            data = json.loads(output)
            data["manifest"] = "<redacted>"
            output = json.dumps(data, indent=2)
        print(output)
    elif args.markdown:
        print(format_markdown(loaded.path, loaded.manifest, report, args.strict))
    else:
        print(f"Manifest: {loaded.path}")
        print(format_overview(loaded.manifest))
        print("\nFindings:")
        print(format_findings(report, args.strict))
    return report.exit_code(args.strict)



def cmd_compare(args: argparse.Namespace) -> int:
    """Compare the two runs' recorded values without checking artifact files."""
    loaded = _load_or_report(args.path, args.json)
    if loaded is None:
        return EXIT_INVALID
    try:
        comparison = compare_runs(
            loaded.manifest, args.base, args.target,
            manifest_path=str(loaded.path), latest=args.latest,
        )
    except ComparisonError as exc:
        if args.json:
            print(json.dumps({"ok": False, "exit_code": EXIT_INVALID, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}")
        return EXIT_INVALID
    if args.json:
        print(json.dumps(comparison, indent=2, ensure_ascii=True, allow_nan=False))
    else:
        print(format_comparison(comparison))
    return EXIT_OK


def cmd_comparability(args: argparse.Namespace) -> int:
    """Compare recorded conditions across two manifests (no artifact checks)."""
    if getattr(args, "preflight", False):
        if args.json:
            print(run_comparability.PREFLIGHT_JSON_REFUSAL)
            return EXIT_INVALID
        outcome = run_comparability.preflight_paths(
            args.baseline,
            args.target,
            baseline_result_id=args.baseline_result,
            target_result_id=args.target_result,
        )
        print(outcome.text)
        return outcome.exit_code
    if args.baseline_result is None or args.target_result is None:
        outcome = run_comparability.invalid_arguments()
        if args.json:
            print(
                json.dumps(
                    outcome.document, indent=2, ensure_ascii=True, allow_nan=False
                )
            )
        else:
            print(outcome.text)
        return outcome.exit_code
    outcome = run_comparability.compare_paths(
        args.baseline,
        args.target,
        baseline_result_id=args.baseline_result,
        target_result_id=args.target_result,
    )
    if args.json:
        # Build the complete document before printing, so a serialization
        # failure can never leave a partial result on stdout.
        output = json.dumps(
            outcome.document, indent=2, ensure_ascii=True, allow_nan=False
        )
        print(output)
    else:
        print(outcome.text)
    return outcome.exit_code


def cmd_assess(args: argparse.Namespace) -> int:
    from . import run_assess

    outcome = run_assess.assess(
        args.baseline,
        args.target,
        baseline_result_id=args.baseline_result,
        target_result_id=args.target_result,
        directory_artifacts=args.directory_artifacts,
    )
    if args.json:
        print(run_assess.assess_json(outcome))
    else:
        print(outcome.text)
    return outcome.exit_code


def cmd_leaderboard(args: argparse.Namespace) -> int:
    """Rank recorded metric values without opening any referenced artifact."""
    loaded = _load_or_report(args.path, args.json)
    if loaded is None:
        return EXIT_INVALID
    try:
        ranked = rank_runs(
            loaded.manifest, args.metric, args.direction,
            top=args.top, manifest_path=str(loaded.path), latest=args.latest,
        )
    except LeaderboardError as exc:
        if args.json:
            print(json.dumps({"ok": False, "exit_code": EXIT_INVALID, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}")
        return EXIT_INVALID
    if args.json:
        print(json.dumps(ranked, indent=2, ensure_ascii=True, allow_nan=False))
    else:
        print(format_leaderboard(ranked))
    return EXIT_OK



def cmd_stats(args: argparse.Namespace) -> int:
    """Summarize recorded metric rows without reading any artifact."""
    loaded = _load_or_report(args.path, args.json)
    if loaded is None:
        return EXIT_INVALID
    data = stats_manifest(
        loaded.manifest,
        manifest_path=str(loaded.path),
        metric=args.metric,
        latest=args.latest,
    )
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=True, allow_nan=False))
    else:
        print(format_stats(data))
    return EXIT_OK


def cmd_matrix(args: argparse.Namespace) -> int:
    """Display recorded metric values across runs without artifact checks."""
    loaded = _load_or_report(args.path, args.json)
    if loaded is None:
        return EXIT_INVALID
    try:
        data = metric_matrix(
            loaded.manifest, manifest_path=str(loaded.path),
            selected_runs=args.run, selected_metrics=args.metric, latest=args.latest,
        )
    except MatrixError as exc:
        if args.json:
            print(json.dumps({"ok": False, "exit_code": EXIT_INVALID, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}")
        return EXIT_INVALID
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=True, allow_nan=False))
    else:
        print(format_metric_matrix(data))
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    """Show or author one recorded run without opening artifact files."""
    if args.add or args.edit or args.remove:
        if args.remove:
            return _author_manifest(
                args, lambda raw: authoring.remove_run(raw, args.id), f"remove run {args.id}"
            )
        entry = _read_authoring_entry(args)
        if entry is None:
            return EXIT_INVALID
        if args.add:
            return _author_manifest(
                args,
                lambda raw: authoring.add_run(raw, entry),
                f"add run {entry.get('id')}",
            )
        entry_id = _authoring_entry_id(args, entry)
        if entry_id is None:
            return EXIT_INVALID
        return _author_manifest(
            args,
            lambda raw: authoring.edit_run(raw, entry_id, entry),
            f"edit run {entry_id}",
        )
    if args.id is None:
        print("ERROR: run needs --id, or --add/--edit/--remove. Nothing was written.")
        return EXIT_INVALID
    loaded = _load_or_report(args.path, args.json)
    if loaded is None:
        return EXIT_INVALID
    try:
        details = run_details(loaded.manifest, args.id, manifest_path=str(loaded.path))
    except RunDetailsError as exc:
        if args.json:
            print(json.dumps({"ok": False, "exit_code": EXIT_INVALID, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}")
        return EXIT_INVALID
    if args.json:
        print(json.dumps(details, ensure_ascii=True, allow_nan=False, indent=2))
    else:
        print(format_run_details(details))
    return EXIT_OK


def cmd_db_export(args: argparse.Namespace) -> int:
    """Export one validated manifest as a derived SQLite query cache."""
    loaded = _load_or_report(args.path, args.json)
    if loaded is None:
        return EXIT_INVALID
    try:
        data = export_sqlite(loaded, args.output, force=args.force)
    except (OSError, ValueError, sqlite3.Error) as exc:
        if args.json:
            print(json.dumps({"ok": False, "exit_code": EXIT_INVALID, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}\nNothing was written.")
        return EXIT_INVALID
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"Wrote {data['path']} ({data['counts']['runs']} runs, {data['counts']['results']} results).")
        print("JSON remains the authority; this database is a derived query cache.")
    return EXIT_OK


def cmd_db_check(args: argparse.Namespace) -> int:
    loaded = _load_or_report(args.path, args.json)
    if loaded is None:
        return EXIT_INVALID
    data = check_sqlite(loaded, args.db)
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        if data["error"] if "error" in data else False:
            print(f"ERROR: {data['error']}")
        elif data["ok"]:
            print("OK: database matches the manifest snapshot (exit 0)")
        else:
            for item in data["mismatches"]:
                print(f"DRIFT {item['table']}: {item}")
            print("FAIL: database differs from the manifest (exit 1)")
    return data["exit_code"]


def cmd_db_query(args: argparse.Namespace) -> int:
    data = query_sqlite(args.db, args.sql, limit=args.limit)
    print(json.dumps(data, indent=2))
    return data["exit_code"]


def cmd_db_import(args: argparse.Namespace) -> int:
    try:
        data = import_sqlite(args.db, args.output, force=args.force)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"ERROR: {exc}\nNothing was written.")
        return EXIT_INVALID
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        print(f"Wrote {data['path']} ({data['counts']['runs']} runs).")
    return EXIT_OK


def cmd_runs(args: argparse.Namespace) -> int:
    """List recorded runs without reading any artifact."""
    loaded = _load_or_report(args.path, args.json)
    if loaded is None:
        return EXIT_INVALID
    data = runs_manifest(
        loaded.manifest,
        manifest_path=str(loaded.path),
        include_metadata=args.metadata,
        include_summaries=args.summaries,
    )
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=True, allow_nan=False))
    else:
        print(format_runs(data))
    return EXIT_OK


def cmd_claim(args: argparse.Namespace) -> int:
    """Show recorded support and contradiction links for one evidence claim."""
    loaded = _load_or_report(args.path, args.json)
    if loaded is None:
        return EXIT_INVALID
    try:
        details = claim_details(loaded.manifest, args.id, manifest_path=str(loaded.path))
    except ClaimDetailsError as exc:
        if args.json:
            print(json.dumps({"ok": False, "exit_code": EXIT_INVALID, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}")
        return EXIT_INVALID
    if args.json:
        print(json.dumps(details, ensure_ascii=True, allow_nan=False, indent=2))
    else:
        print(format_claim_details(details))
    return EXIT_OK



def cmd_lineage(args: argparse.Namespace) -> int:
    """Trace one claim's recorded upstream dependencies without artifact I/O."""
    loaded = _load_or_report(args.path, args.json)
    if loaded is None:
        return EXIT_INVALID
    try:
        details = claim_lineage(loaded.manifest, args.id, manifest_path=str(loaded.path))
    except LineageError as exc:
        if args.json:
            print(json.dumps({"ok": False, "exit_code": EXIT_INVALID, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}")
        return EXIT_INVALID
    if args.json:
        print(json.dumps(details, ensure_ascii=True, allow_nan=False, indent=2))
    else:
        print(format_claim_lineage(details))
    return EXIT_OK


def cmd_impact(args: argparse.Namespace) -> int:
    """Project possible claim impact through recorded provenance, without artifact I/O."""
    loaded = _load_or_report(args.path, args.json)
    if loaded is None:
        return EXIT_INVALID
    try:
        data = claim_impact(loaded.manifest, args.id, manifest_path=str(loaded.path))
    except ImpactError as exc:
        if args.json:
            print(json.dumps({"ok": False, "exit_code": EXIT_INVALID, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}")
        return EXIT_INVALID
    if args.json:
        print(json.dumps(data, ensure_ascii=True, allow_nan=False, indent=2))
    else:
        print(format_claim_impact(data))
    return EXIT_OK


def cmd_coverage(args: argparse.Namespace) -> int:
    """Summarize recorded claim associations and provenance review flags."""
    loaded = _load_or_report(args.path, args.json)
    if loaded is None:
        return EXIT_INVALID
    try:
        data = claim_coverage(loaded.manifest, manifest_path=str(loaded.path),
                              only_flagged=args.only_flagged)
    except CoverageError as exc:
        if args.json:
            print(json.dumps({"ok": False, "exit_code": EXIT_INVALID, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}")
        return EXIT_INVALID
    if args.json:
        print(json.dumps(data, ensure_ascii=True, allow_nan=False, indent=2))
    else:
        print(format_claim_coverage(data))
    return EXIT_OK



def cmd_coverage_gate(args: argparse.Namespace) -> int:
    """Apply a read-only CI structural-coverage policy to one manifest."""
    loaded = _load_or_report(args.path, args.json)
    if loaded is None:
        return EXIT_INVALID
    try:
        data = evaluate_coverage_gate(
            loaded.manifest, manifest_path=str(loaded.path), selected_flags=args.flag,
        )
    except CoverageGateError as exc:
        if args.json:
            print(json.dumps({"ok": False, "exit_code": EXIT_INVALID, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}")
        return EXIT_INVALID
    if args.json:
        print(json.dumps(data, ensure_ascii=True, allow_nan=False, indent=2))
    else:
        print(format_coverage_gate(data))
    return data["gate"]["exit_code"]


def cmd_fingerprint(args: argparse.Namespace) -> int:
    """Hash recorded manifest meaning; optionally check a pinned v1 digest for CI."""
    # Reject malformed pins before attempting manifest I/O. SHA spelling is the
    # complete 64-hex digest, with no whitespace, prefix, or truncation.
    expected = args.expect
    if expected is not None:
        if re.fullmatch(r"[0-9a-fA-F]{64}", expected) is None:
            if args.json:
                print(json.dumps({"ok": False, "exit_code": EXIT_INVALID,
                                  "error": "--expect requires exactly 64 hexadecimal SHA-256 characters"}, indent=2))
            else:
                print("ERROR: --expect requires exactly 64 hexadecimal SHA-256 characters")
            return EXIT_INVALID
        expected = expected.lower()
    loaded = _load_or_report(args.path, args.json)
    if loaded is None:
        return EXIT_INVALID
    try:
        data = manifest_fingerprint(loaded.manifest, manifest_path=str(loaded.path))
    except FingerprintError as exc:
        if args.json:
            print(json.dumps({"ok": False, "exit_code": EXIT_INVALID, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}")
        return EXIT_INVALID
    code = EXIT_OK
    if expected is not None:
        actual = data["fingerprint"]["digest"]
        matched = hmac.compare_digest(actual, expected)
        code = EXIT_OK if matched else 1
        data["verification"] = {
            "expected_digest": expected,
            "actual_digest": actual,
            "matched": matched,
            "exit_code": code,
            "note": "Recorded manifest meaning only; not artifact integrity or scientific validation.",
        }
    if args.json:
        print(json.dumps(data, ensure_ascii=True, allow_nan=False, indent=2))
    else:
        view = format_fingerprint(data)
        if expected is not None:
            view += ("\nExpected recorded SHA-256: " + expected
                     + ("\nFingerprint check: MATCH (exit 0)" if code == EXIT_OK
                        else "\nFingerprint check: MISMATCH (exit 1)"))
        print(view)
    return code



def cmd_fingerprint_compare(args: argparse.Namespace) -> int:
    """CI-check recorded semantic equality across two valid manifest snapshots.

    This is not artifact integrity or scientific validation. Both manifests
    must be independently schema-loadable and unambiguous under fingerprint v1.
    """
    if args.show_notes and not args.explain:
        message = "--show-notes requires --explain and may reveal sensitive relationship annotations"
        if args.json:
            print(json.dumps({"ok": False, "exit_code": EXIT_INVALID, "error": message}, indent=2))
        else:
            print("ERROR: " + message)
        return EXIT_INVALID
    before = _load_or_report(args.before, args.json)
    if before is None:
        return EXIT_INVALID
    after = _load_or_report(args.after, args.json)
    if after is None:
        return EXIT_INVALID
    try:
        first = manifest_fingerprint(before.manifest, manifest_path=str(before.path))
        second = manifest_fingerprint(after.manifest, manifest_path=str(after.path))
    except FingerprintError as exc:
        if args.json:
            print(json.dumps({"ok": False, "exit_code": EXIT_INVALID, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}")
        return EXIT_INVALID
    a = first["fingerprint"]
    b = second["fingerprint"]
    # Refuse accidentally comparing unlike formats even if future code changes.
    if a["algorithm"] != b["algorithm"] or a["semantic_format"] != b["semantic_format"]:
        if args.json:
            print(json.dumps({"ok": False, "exit_code": EXIT_INVALID,
                              "error": "fingerprint algorithms or semantic formats differ"}, indent=2))
        else:
            print("ERROR: fingerprint algorithms or semantic formats differ")
        return EXIT_INVALID
    matched = hmac.compare_digest(a["digest"], b["digest"])
    code = EXIT_OK if matched else 1
    explanation = None
    if args.explain:
        try:
            explanation = explain_fingerprint_difference(before.manifest, after.manifest)
        except FingerprintError as exc:
            if args.json:
                print(json.dumps({"ok": False, "exit_code": EXIT_INVALID, "error": str(exc)}, indent=2))
            else:
                print(f"ERROR: {exc}")
            return EXIT_INVALID
        # Structural comparison must explain every observed v1 mismatch.
        changed = bool(explanation["summary"]["object_changes"] or explanation["summary"]["relationship_changes"])
        if changed == matched:
            message = "fingerprint v1 equality and its structural explanation disagree"
            if args.json:
                print(json.dumps({"ok": False, "exit_code": EXIT_INVALID, "error": message}, indent=2))
            else:
                print("ERROR: " + message)
            return EXIT_INVALID
        # Apply privacy policy only after structural agreement is established;
        # redaction never participates in fingerprint or equality decisions.
        explanation = redact_fingerprint_explanation(explanation, show_notes=args.show_notes)
    note = ("Equality of recorded manifest assertions under the same fingerprint v1 format only. "
            "No artifact contents were checked, and this is not scientific validation. "
            "Run 'runevidence validate' separately.")
    if args.explain:
        note += (" Raw relationship notes explicitly exposed by --show-notes; treat output as sensitive."
                 if args.show_notes else
                 " Relationship note content is redacted by default; object IDs and paths may still be sensitive.")
    if args.json:
        payload = {
            "before_manifest": first["manifest"], "after_manifest": second["manifest"],
            "before_fingerprint": a, "after_fingerprint": b,
            "comparison": {"matched": matched, "exit_code": code, "note": note},
        }
        if explanation is not None:
            payload["explanation"] = explanation
        print(json.dumps(payload, ensure_ascii=True, allow_nan=False, indent=2))
    else:
        # Reuse the existing bounded, JSON-escaped text renderer on each path.
        print("BEFORE RECORD:\n" + format_fingerprint(first) + "\n"
              + "AFTER RECORD:\n" + format_fingerprint(second) + "\n"
              + ("Recorded fingerprint comparison: MATCH (exit 0)" if matched
                 else "Recorded fingerprint comparison: DIFFERENT (exit 1)") + "\n"
              + note
               + ("\n" + format_fingerprint_explanation(explanation)
                  if explanation is not None else ""))
    return code


def cmd_coverage_diff(args: argparse.Namespace) -> int:
    """Compare recorded structural review flags across two manifest snapshots."""
    before = _load_or_report(args.before, args.json)
    if before is None:
        return EXIT_INVALID
    after = _load_or_report(args.after, args.json)
    if after is None:
        return EXIT_INVALID
    try:
        data = compare_claim_coverage(
            before.manifest, after.manifest,
            before_path=str(before.path), after_path=str(after.path),
            only_regressions=args.only_regressions,
        )
    except CoverageDiffError as exc:
        if args.json:
            print(json.dumps({"ok": False, "exit_code": EXIT_INVALID, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}")
        return EXIT_INVALID
    if args.json:
        print(json.dumps(data, ensure_ascii=True, allow_nan=False, indent=2))
    else:
        print(format_coverage_diff(data))
    return EXIT_OK



def cmd_review_diff(args: argparse.Namespace) -> int:
    """Unified recorded claim review queue for two manifest snapshots."""
    before = _load_or_report(args.before, args.json)
    if before is None:
        return EXIT_INVALID
    after = _load_or_report(args.after, args.json)
    if after is None:
        return EXIT_INVALID
    try:
        data = compare_claim_review(
            before.manifest, after.manifest,
            before_path=str(before.path), after_path=str(after.path),
            only_new_flags=args.only_new_flags,
        )
    except ReviewDiffError as exc:
        if args.json:
            print(json.dumps({"ok": False, "exit_code": EXIT_INVALID, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}")
        return EXIT_INVALID
    if args.json:
        print(json.dumps(data, ensure_ascii=True, allow_nan=False, indent=2))
    else:
        print(format_claim_review(data))
    return EXIT_OK


def cmd_impact_diff(args: argparse.Namespace) -> int:
    """Compare recorded snapshots, finding claims potentially needing re-review."""
    before = _load_or_report(args.before, args.json)
    if before is None:
        return EXIT_INVALID
    after = _load_or_report(args.after, args.json)
    if after is None:
        return EXIT_INVALID
    try:
        result = compare_claim_impact(
            before.manifest, after.manifest,
            before_path=str(before.path), after_path=str(after.path),
        )
    except ImpactDiffError as exc:
        if args.json:
            print(json.dumps({"ok": False, "exit_code": EXIT_INVALID, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}")
        return EXIT_INVALID
    if args.json:
        print(json.dumps(result, ensure_ascii=True, allow_nan=False, indent=2))
    else:
        print(format_impact_diff(result))
    return EXIT_OK


def cmd_review_gate(args: argparse.Namespace) -> int:
    """Apply a selected CI policy to recorded review changes, without artifact I/O."""
    before = _load_or_report(args.before, args.json)
    if before is None:
        return EXIT_INVALID
    after = _load_or_report(args.after, args.json)
    if after is None:
        return EXIT_INVALID
    try:
        data = evaluate_review_gate(
            before.manifest, after.manifest,
            before_path=str(before.path), after_path=str(after.path), policy=args.policy,
        )
    except ReviewGateError as exc:
        if args.json:
            print(json.dumps({"ok": False, "exit_code": EXIT_INVALID, "error": str(exc)}, indent=2))
        else:
            print(f"ERROR: {exc}")
        return EXIT_INVALID
    if args.json:
        print(json.dumps(data, ensure_ascii=True, allow_nan=False, indent=2))
    else:
        print(format_review_gate(data))
    return data["gate"]["exit_code"]


def _accepts_keyword(adapter, name: str) -> bool:
    """True when extract names an explicit keyword parameter."""
    try:
        signature = inspect.signature(adapter.extract)
    except (TypeError, ValueError):
        return False
    parameter = signature.parameters.get(name)
    return parameter is not None and parameter.kind in (
        inspect.Parameter.KEYWORD_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )


def _accepts_tag_filter(adapter) -> bool:
    return _accepts_keyword(adapter, "tag_filter")


def _authoring_lock_failure(args: argparse.Namespace, code: str, message: str) -> int:
    if args.json:
        print(json.dumps({"format": "runevidence-authoring-v1", "ok": False, "code": code, "error": message, "exit_code": EXIT_INVALID}))
    else:
        print(message)
    return EXIT_INVALID


def _author_manifest(args: argparse.Namespace, mutate, summary: str) -> int:
    try:
        with _writer_lock(args.path, enabled=not args.dry_run) as manifest_path:
            loaded = _load_or_report(manifest_path, args.json)
            if loaded is None:
                return EXIT_INVALID
            raw = load_raw(loaded)
            try:
                mutate(raw)
            except authoring.AuthoringError as exc:
                if args.json:
                    print(json.dumps({"format": "runevidence-authoring-v1", "ok": False, "error": str(exc), "exit_code": EXIT_INVALID}))
                else:
                    print(f"ERROR: {exc}\nNothing was written.")
                return EXIT_INVALID
            except (KeyError, TypeError, AttributeError) as exc:
                message = f"invalid authoring payload: {exc}"
                if args.json:
                    print(json.dumps({"format": "runevidence-authoring-v1", "ok": False, "error": message, "exit_code": EXIT_INVALID}))
                else:
                    print(f"ERROR: {message}\nNothing was written.")
                return EXIT_INVALID
            if args.dry_run:
                if args.json:
                    print(json.dumps({"format": "runevidence-authoring-v1", "ok": True, "changed": summary, "dry_run": True, "written": False, "exit_code": EXIT_OK}))
                else:
                    print(f"Would {summary}.")
                return EXIT_OK
            try:
                save_raw(raw, loaded.path, expected_bytes=loaded.source_bytes)
            except OSError as exc:
                if args.json:
                    print(json.dumps({"format": "runevidence-authoring-v1", "ok": False, "error": str(exc), "exit_code": EXIT_INVALID}))
                else:
                    print(f"ERROR: {exc}\nNothing was written.")
                return EXIT_INVALID
            if args.json:
                print(json.dumps({"format": "runevidence-authoring-v1", "ok": True, "changed": summary, "dry_run": False, "written": True, "exit_code": EXIT_OK}))
            else:
                print(f"Wrote {loaded.path}\nNext: runevidence validate {args.path}")
            return EXIT_OK
    except _WriterBusy:
        return _authoring_lock_failure(args, "BUSY", BUSY_TEXT)
    except _WriterLockUnavailable:
        return _authoring_lock_failure(args, "LOCK_UNAVAILABLE", LOCK_UNAVAILABLE_TEXT)


def _read_authoring_entry(args: argparse.Namespace) -> dict | None:
    """Read a --file payload, reporting a bad file like any other authoring refusal."""
    try:
        return authoring.read_json_file(Path(args.file))
    except authoring.AuthoringError as exc:
        if args.json:
            print(json.dumps({"format": "runevidence-authoring-v1", "ok": False, "error": str(exc), "exit_code": EXIT_INVALID}))
        else:
            print(f"ERROR: {exc}\nNothing was written.")
        return None


def _authoring_entry_id(args: argparse.Namespace, entry: dict) -> str | None:
    entry_id = entry.get("id")
    if isinstance(entry_id, str) and entry_id:
        return entry_id
    message = "the file needs a non-empty 'id' for this action"
    if args.json:
        print(json.dumps({"format": "runevidence-authoring-v1", "ok": False, "error": message, "exit_code": EXIT_INVALID}))
    else:
        print(f"ERROR: {message}\nNothing was written.")
    return None


def cmd_result(args: argparse.Namespace) -> int:
    if args.action == "remove":
        return _author_manifest(args, lambda raw: authoring.remove_result(raw, args.id), f"remove result {args.id}")
    entry = _read_authoring_entry(args)
    if entry is None:
        return EXIT_INVALID
    if args.action == "add":
        return _author_manifest(args, lambda raw: authoring.add_result(raw, entry), f"add result {entry.get('id')}")
    entry_id = _authoring_entry_id(args, entry)
    if entry_id is None:
        return EXIT_INVALID
    return _author_manifest(args, lambda raw: authoring.edit_result(raw, entry_id, entry), f"edit result {entry.get('id')}")


def cmd_evidence(args: argparse.Namespace) -> int:
    if args.action == "remove":
        return _author_manifest(args, lambda raw: authoring.remove_evidence(raw, args.id), f"remove claim {args.id}")
    entry = _read_authoring_entry(args)
    if entry is None:
        return EXIT_INVALID
    if args.action == "add":
        return _author_manifest(args, lambda raw: authoring.add_evidence(raw, entry), f"add claim {entry.get('id')}")
    entry_id = _authoring_entry_id(args, entry)
    if entry_id is None:
        return EXIT_INVALID
    return _author_manifest(args, lambda raw: authoring.edit_evidence(raw, entry_id, entry), f"edit claim {entry.get('id')}")


def cmd_artifact(args: argparse.Namespace) -> int:
    if args.action == "remove":
        if not args.id:
            message = "artifact remove needs a non-empty --id"
            if args.json:
                print(json.dumps({"format": "runevidence-authoring-v1", "ok": False, "error": message, "exit_code": EXIT_INVALID}))
            else:
                print(f"ERROR: {message}\nNothing was written.")
            return EXIT_INVALID
        return _author_manifest(
            args,
            lambda raw: authoring.remove_artifact(raw, args.id),
            f"remove artifact {args.id}",
        )
    entry = _read_authoring_entry(args)
    if entry is None:
        return EXIT_INVALID
    entry_id = _authoring_entry_id(args, entry)
    if entry_id is None:
        return EXIT_INVALID
    return _author_manifest(
        args,
        lambda raw: authoring.edit_artifact(raw, entry_id, entry),
        f"edit artifact {entry_id}",
    )


def cmd_relationship(args: argparse.Namespace) -> int:
    if args.action == "remove":
        return _author_manifest(args, lambda raw: authoring.remove_relationship(raw, args.index), f"remove relationship {args.index}")
    entry = _read_authoring_entry(args)
    if entry is None:
        return EXIT_INVALID
    return _author_manifest(args, lambda raw: authoring.add_relationship(raw, entry), "add relationship")


def cmd_capture(args: argparse.Namespace) -> int:
    loaded = _load_or_report(args.path, args.json)
    if loaded is None:
        return EXIT_INVALID
    try:
        result = capture.capture(loaded, args.run, dry_run=args.dry_run)
    except capture.CaptureError as exc:
        if args.json:
            print(json.dumps({"format": "runevidence-capture-v1", "ok": False, "error": str(exc), "exit_code": EXIT_INVALID}))
        else:
            print(f"ERROR: {exc}\nNothing was written.")
        return EXIT_INVALID
    document = {
        "format": "runevidence-capture-v1",
        "ok": True,
        "run": result.run_id,
        "source_revision": result.source_revision,
        "environment": result.environment,
        "dry_run": result.dry_run,
        "written": result.written,
        "exit_code": EXIT_OK,
    }
    if args.json:
        print(json.dumps(document, indent=2))
    else:
        print(f"{'Would record' if result.dry_run else 'Recorded'} {result.source_revision} and an environment snapshot for {result.run_id}.")
    return EXIT_OK


def cmd_init(args: argparse.Namespace) -> int:
    root: Path = args.path.resolve()
    if not root.is_dir():
        print(f"ERROR: {root} is not a folder")
        return EXIT_INVALID
    out = root / MANIFEST_FILENAME
    try:
        # The lock covers the existence checks, any backup and the save. init
        # locks and writes the folder entry it names: a symlinked manifest is
        # replaced in <root> with its backup in <root>, never written through
        # to the link target (the HEAD behavior).
        with _writer_lock(out, follow_manifest_symlink=False) as manifest_path:
            return _init_under_lock(args, root, manifest_path)
    except _WriterBusy:
        return _busy_failure()
    except _WriterLockUnavailable:
        return _lock_unavailable_failure()


INIT_0_2_REFUSAL = (
    "ERROR: the existing manifest declares schema 0.2. init --force would regenerate it as "
    "schema 0.1 and cannot carry over first-class values. Nothing was written. Move the "
    "manifest aside, run init, then runevidence migrate --to 0.2 and reapply the "
    "first-class values."
)
INIT_0_3_REFUSAL = (
    "ERROR: the existing manifest declares schema 0.3. init --force would regenerate it as "
    "schema 0.1 and cannot carry over first-class values or locators. Nothing was written. "
    "Move the manifest aside, run init, then runevidence migrate --to 0.3 and reapply the "
    "first-class values."
)
INIT_0_4_REFUSAL = (
    "ERROR: the existing manifest declares schema 0.4. init --force would regenerate it as "
    "schema 0.1 and cannot carry over first-class values or locators. Nothing was written. "
    "Move the manifest aside, run init, then runevidence migrate --to 0.4 and reapply the "
    "first-class values and locators."
)
INIT_0_5_REFUSAL = (
    "ERROR: the existing manifest declares schema 0.5. init --force would regenerate the "
    "manifest and cannot carry over first-class values or locators. Nothing was written. "
    "Move the manifest aside, run init, then run runevidence migrate PATH --to 0.5 (unless "
    "the adapter writes 0.5 itself) and reapply the first-class values and locators."
)
INIT_0_6_REFUSAL = (
    "ERROR: the existing manifest declares schema 0.6. init --force would regenerate the "
    "manifest and cannot carry over first-class values or locators. Nothing was written. "
    "Move the manifest aside, run init, then run runevidence migrate PATH --to 0.6 (unless "
    "the adapter writes 0.6 itself) and reapply the first-class values and locators."
)
INIT_0_7_REFUSAL = (
    "ERROR: the existing manifest declares schema 0.7. init --force would regenerate the "
    "manifest and cannot carry over first-class values or locators. Nothing was written. "
    "Move the manifest aside, run init, then run runevidence migrate PATH --to 0.7 (unless "
    "the adapter writes 0.7 itself) and reapply the first-class values and locators."
)


INIT_0_11_REFUSAL = (
    "ERROR: the existing manifest declares schema 0.11. init --force would regenerate the "
    "manifest and cannot carry over first-class values or locators. Nothing was written. "
    "Move the manifest aside, run init, then run runevidence migrate PATH --to 0.11 (unless "
    "the adapter writes 0.11 itself) and reapply the first-class values and locators."
)


INIT_0_10_REFUSAL = (
    "ERROR: the existing manifest declares schema 0.10. init --force would regenerate the "
    "manifest and cannot carry over first-class values or locators. Nothing was written. "
    "Move the manifest aside, run init, then run runevidence migrate PATH --to 0.10 (unless "
    "the adapter writes 0.10 itself) and reapply the first-class values and locators."
)


INIT_0_9_REFUSAL = (
    "ERROR: the existing manifest declares schema 0.9. init --force would regenerate the "
    "manifest and cannot carry over first-class values or locators. Nothing was written. "
    "Move the manifest aside, run init, then run runevidence migrate PATH --to 0.9 (unless "
    "the adapter writes 0.9 itself) and reapply the first-class values and locators."
)


INIT_0_8_REFUSAL = (
    "ERROR: the existing manifest declares schema 0.8. init --force would regenerate the "
    "manifest and cannot carry over first-class values or locators. Nothing was written. "
    "Move the manifest aside, run init, then run runevidence migrate PATH --to 0.8 (unless "
    "the adapter writes 0.8 itself) and reapply the first-class values and locators."
)


def _declared_manifest_version(path: Path):
    """Read only the declared schema_version, without validating the manifest."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    if isinstance(document, dict):
        version = document.get("schema_version")
        if isinstance(version, str):
            return version
    return None


def _init_under_lock(args: argparse.Namespace, root: Path, out: Path) -> int:
    if out.exists() and not args.force:
        print(f"ERROR: {out} already exists. Use --force to overwrite it.")
        return EXIT_INVALID
    declared = _declared_manifest_version(out) if out.exists() else None
    if out.exists() and args.force and declared in (
        "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8", "0.9", "0.10", "0.11"
    ):
        # Refuse before adapter discovery, backup creation or any write: a raw
        # 0.2/0.3/0.4/0.5 declaration means hand-entered data (or a would-be
        # downgrade) is at risk. An unparseable manifest keeps the legacy
        # backup-and-replace path below.
        if declared == "0.11":
            print(INIT_0_11_REFUSAL)
        elif declared == "0.10":
            print(INIT_0_10_REFUSAL)
        elif declared == "0.9":
            print(INIT_0_9_REFUSAL)
        elif declared == "0.8":
            print(INIT_0_8_REFUSAL)
        elif declared == "0.7":
            print(INIT_0_7_REFUSAL)
        elif declared == "0.6":
            print(INIT_0_6_REFUSAL)
        elif declared == "0.5":
            print(INIT_0_5_REFUSAL)
        elif declared == "0.4":
            print(INIT_0_4_REFUSAL)
        elif declared == "0.3":
            print(INIT_0_3_REFUSAL)
        else:
            print(INIT_0_2_REFUSAL)
        return EXIT_INVALID
    try:
        adapter = get_adapter(args.adapter)
    except KeyError as exc:
        print(f"ERROR: {exc.args[0]}")
        return EXIT_INVALID
    name = args.name or root.name
    include_strings = bool(getattr(args, "include_strings", False))
    if include_strings:
        if not getattr(adapter, "supports_include_strings", False):
            print(
                f"ERROR: adapter {adapter.name!r} does not support --include-strings. "
                "Nothing was written."
            )
            return EXIT_INVALID
        if not _accepts_keyword(adapter, "include_strings"):
            print(
                f"ERROR: adapter {adapter.name!r} cannot apply --include-strings. "
                "Nothing was written."
            )
            return EXIT_INVALID
    include_histograms = bool(getattr(args, "include_histograms", False))
    if include_histograms:
        if not getattr(adapter, "supports_include_histograms", False):
            print(
                f"ERROR: adapter {adapter.name!r} does not support --include-histograms. "
                "Nothing was written."
            )
            return EXIT_INVALID
        if not _accepts_keyword(adapter, "include_histograms"):
            print(
                f"ERROR: adapter {adapter.name!r} cannot apply --include-histograms. "
                "Nothing was written."
            )
            return EXIT_INVALID
    include_images = bool(getattr(args, "include_images", False))
    if include_images:
        if not getattr(adapter, "supports_include_images", False):
            print(
                f"ERROR: adapter {adapter.name!r} does not support --include-images. "
                "Nothing was written."
            )
            return EXIT_INVALID
        if not _accepts_keyword(adapter, "include_images"):
            print(
                f"ERROR: adapter {adapter.name!r} cannot apply --include-images. "
                "Nothing was written."
            )
            return EXIT_INVALID
    include_audio = bool(getattr(args, "include_audio", False))
    if include_audio:
        if not getattr(adapter, "supports_include_audio", False):
            print(
                f"ERROR: adapter {adapter.name!r} does not support --include-audio. "
                "Nothing was written."
            )
            return EXIT_INVALID
        if not _accepts_keyword(adapter, "include_audio"):
            print(
                f"ERROR: adapter {adapter.name!r} cannot apply --include-audio. "
                "Nothing was written."
            )
            return EXIT_INVALID
    include_tensors = bool(getattr(args, "include_tensors", False))
    if include_tensors:
        if not getattr(adapter, "supports_include_tensors", False):
            print(
                f"ERROR: adapter {adapter.name!r} does not support --include-tensors. "
                "Nothing was written."
            )
            return EXIT_INVALID
        if not _accepts_keyword(adapter, "include_tensors"):
            print(
                f"ERROR: adapter {adapter.name!r} cannot apply --include-tensors. "
                "Nothing was written."
            )
            return EXIT_INVALID
    include_tf2_histograms = bool(getattr(args, "include_tf2_histograms", False))
    if include_tf2_histograms:
        if not getattr(adapter, "supports_include_tf2_histograms", False):
            print(
                f"ERROR: adapter {adapter.name!r} does not support --include-tf2-histograms. "
                "Nothing was written."
            )
            return EXIT_INVALID
        if not _accepts_keyword(adapter, "include_tf2_histograms"):
            print(
                f"ERROR: adapter {adapter.name!r} cannot apply --include-tf2-histograms. "
                "Nothing was written."
            )
            return EXIT_INVALID
    include_tf2_media = bool(getattr(args, "include_tf2_media", False))
    if include_tf2_media:
        if not getattr(adapter, "supports_include_tf2_media", False):
            print(
                f"ERROR: adapter {adapter.name!r} does not support --include-tf2-media. "
                "Nothing was written."
            )
            return EXIT_INVALID
        if not _accepts_keyword(adapter, "include_tf2_media"):
            print(
                f"ERROR: adapter {adapter.name!r} cannot apply --include-tf2-media. "
                "Nothing was written."
            )
            return EXIT_INVALID
    patterns = list(getattr(args, "tags", None) or [])
    tag_filter = None
    if patterns:
        if not getattr(adapter, "supports_tag_filters", False):
            print(
                f"ERROR: adapter {adapter.name!r} does not support --tags. Nothing was written."
            )
            return EXIT_INVALID
        if not _accepts_tag_filter(adapter):
            print(
                f"ERROR: adapter {adapter.name!r} cannot apply --tags. Nothing was written."
            )
            return EXIT_INVALID
        try:
            tag_filter = TagFilter(patterns)
        except InvalidTagPattern:
            print("ERROR: invalid --tags pattern. Nothing was written.")
            return EXIT_INVALID
    try:
        discovery = adapter.discover(root)
        extra = {}
        if tag_filter is not None:
            extra["tag_filter"] = tag_filter
        if include_strings:
            extra["include_strings"] = True
        if include_histograms:
            extra["include_histograms"] = True
        if include_images:
            extra["include_images"] = True
        if include_audio:
            extra["include_audio"] = True
        if include_tensors:
            extra["include_tensors"] = True
        if include_tf2_media:
            extra["include_tf2_media"] = True
        if include_tf2_histograms:
            extra["include_tf2_histograms"] = True
        manifest = adapter.extract(
            root,
            discovery,
            project_id=args.id or valid_slug(name).lower(),
            project_name=name,
            **extra,
        )
    except TagPatternTooExpensive:
        print("ERROR: --tags pattern is too expensive. Nothing was written.")
        return EXIT_INVALID
    except AdapterError as exc:
        print(f"ERROR: adapter {adapter.name!r} cannot read {root}: {exc}")
        for line in getattr(exc, "tag_summary", ()):
            print(f"  {line}")
        return EXIT_INVALID
    except ValidationError as exc:
        print(f"ERROR: adapter {adapter.name!r} produced an invalid manifest: {_validation_summary(exc)}")
        print("Nothing was written.")
        return EXIT_INVALID
    if args.volatile and _event_artifact_is_volatile(manifest, args.volatile):
        # The frozen Adapter.extract signature has no volatile channel, so the
        # refusal belongs here: before _mark_volatile, any backup or any write.
        print(
            f"ERROR: adapter {adapter.name!r} cannot read {root}: "
            "an event artifact cannot be volatile; nothing was written"
        )
        return EXIT_INVALID
    if args.volatile:
        manifest = _mark_volatile(manifest, args.volatile)
    backup: Optional[Path] = None
    if out.exists():
        try:
            backup = _backup_existing(out, manifest)
        except OSError as exc:
            print(f"ERROR: could not back up the existing manifest: {exc}")
            print(f"Nothing was written; {out} is unchanged.")
            return EXIT_INVALID
    try:
        save_manifest(manifest, out)
    except OSError as exc:
        print(f"ERROR: could not write {out}: {exc}")
        if backup is not None:
            print(f"{out} was not replaced. Its previous contents are also saved in {backup}.")
        return EXIT_INVALID
    if tag_filter is not None:
        for pattern in tag_filter.unmatched_patterns():
            kind = (
                "summary"
                if (
                    include_strings
                    or include_histograms
                    or include_images
                    or include_audio
                    or include_tensors
                    or include_tf2_media
                )
                else "scalar"
            )
            print(
                "WARNING: --tags pattern "
                f"{ascii(pattern)} matches no representable supported {kind} tag"
            )
    volatile = sum(a.volatile for a in manifest.artifacts)
    print(
        f"Wrote {out}\n"
        f"  {len(manifest.runs)} runs, {len(manifest.artifacts)} artifacts, "
        f"{len(manifest.results)} results"
        + (f" ({volatile} volatile)" if volatile else "")
        + f" (adapter: {adapter.name})\n"
        "Next: add results and evidence claims, then run:\n"
        f"  runevidence validate {args.path}"
    )
    return EXIT_OK


_MIGRATE_RESULT_FORMAT = "runevidence-migrate-result-v1"
_MIGRATE_ERROR_FORMAT = "runevidence-migrate-error-v1"

# One fixed line per closed code. Paths, submitted values, IDs, loader or OS
# text and tracebacks never enter this table or its output.
_MIGRATE_ERROR_TEXT = {
    "INVALID_ARGUMENTS": (
        "ERROR (INVALID_ARGUMENTS): the migrate command line is invalid. "
        "Nothing was written."
    ),
    "UNSUPPORTED_TARGET": (
        "ERROR (UNSUPPORTED_TARGET): only --to 0.2, --to 0.3, --to 0.4, --to 0.5, "
        "--to 0.6, --to 0.7, --to 0.8, --to 0.9, --to 0.10 or --to 0.11 is supported; downgrade and other "
        "versions are not available. Nothing was written."
    ),
    "RESERVED_PATH": (
        "ERROR (RESERVED_PATH): the target resolves to a backup name; choose the "
        "manifest, not a backup. Nothing was written."
    ),
    "MANIFEST_LOAD_FAILED": (
        "ERROR (MANIFEST_LOAD_FAILED): the manifest could not be loaded and "
        "validated. Nothing was written."
    ),
    "SERIALIZATION_FAILED": (
        "ERROR (SERIALIZATION_FAILED): the migrated manifest could not be "
        "serialized safely. Nothing was written."
    ),
    "READ_FAILED": (
        "ERROR (READ_FAILED): the manifest could not be re-read before backup. "
        "Nothing was written."
    ),
    "STALE_MANIFEST": (
        "ERROR (STALE_MANIFEST): the bytes observed at the stale check differ "
        "from the loaded snapshot; nothing was replaced."
    ),
    "WRITE_FAILED": (
        "ERROR (WRITE_FAILED): the migration could not be written; the manifest "
        "was not replaced."
    ),
    "LOCATOR_MIGRATION_UNSUPPORTED": (
        "ERROR (LOCATOR_MIGRATION_UNSUPPORTED): the manifest contains a locator "
        "that schema {version} cannot accept or verify. Nothing was written."
    ),
    "DOWNGRADE_UNSUPPORTED": (
        "ERROR (DOWNGRADE_UNSUPPORTED): downgrade is not supported. "
        "Nothing was written."
    ),
    "INTERNAL_ERROR": (
        "ERROR (INTERNAL_ERROR): the migration failed internally; the manifest "
        "was not replaced."
    ),
    "BUSY": BUSY_TEXT,
    "LOCK_UNAVAILABLE": LOCK_UNAVAILABLE_TEXT,
}


def _migrate_failure(code: str, as_json: bool, *, target: str | None = None) -> int:
    """Emit exactly one fixed document for a refused migration."""
    if code not in _MIGRATE_ERROR_TEXT:
        # Defensive: a code outside the closed catalogue is never echoed.
        code = "INTERNAL_ERROR"
    if as_json:
        document = {
            "format": _MIGRATE_ERROR_FORMAT,
            "ok": False,
            "exit_code": EXIT_INVALID,
            "error": {"code": code},
        }
        print(json.dumps(document, indent=2, ensure_ascii=True, allow_nan=False))
    else:
        if code == "LOCATOR_MIGRATION_UNSUPPORTED":
            version = target if target in TARGET_VERSIONS else "0.3"
            print(_MIGRATE_ERROR_TEXT[code].format(version=version))
        else:
            print(_MIGRATE_ERROR_TEXT[code])
    return EXIT_INVALID


def _migrate_manifest_path(path: Path) -> Path:
    """Best-effort resolved manifest path for text output only."""
    try:
        return resolve_manifest_path(Path(path)).resolve()
    except (OSError, RuntimeError):
        # A successful migration already resolved and loaded this path; this
        # fallback only prevents a post-success rendering crash.
        return Path(path)


def _migrate_result_document(result) -> dict:
    return {
        "format": _MIGRATE_RESULT_FORMAT,
        "ok": True,
        "from": result.from_version,
        "to": result.to_version,
        "changed": result.changed,
        "dry_run": result.dry_run,
        "backup": result.backup,
    }


def cmd_migrate(args: argparse.Namespace) -> int:
    """Declare an existing 0.1-0.5 manifest as 0.2, 0.3, 0.4, 0.5 or 0.6 additively."""
    if args.to not in TARGET_VERSIONS:
        return _migrate_failure("UNSUPPORTED_TARGET", args.json)
    if not args.dry_run and not _writer_target_folder_exists(args.path):
        # Pre-admission path-shape failure: render the same closed refusal a
        # load would produce, and never write even if the path appears later.
        try:
            migrate_manifest(args.path, dry_run=True, target_version=args.to)
        except MigrationError as exc:
            return _migrate_failure(exc.code, args.json, target=args.to)
        except Exception:
            return _migrate_failure("INTERNAL_ERROR", args.json)
        return _migrate_failure("LOCK_UNAVAILABLE", args.json)
    manifest_path = args.path
    try:
        # A dry run takes no lock; a real migration holds it from before the
        # load through the replacement and backup. The admitted path is the
        # resolved target under the locked folder, not the caller's alias.
        with _writer_lock(args.path, enabled=not args.dry_run) as manifest_path:
            result = migrate_manifest(
                manifest_path, dry_run=args.dry_run, target_version=args.to
            )
    except _WriterBusy:
        return _migrate_failure("BUSY", args.json)
    except _WriterLockUnavailable:
        return _migrate_failure("LOCK_UNAVAILABLE", args.json)
    except MigrationError as exc:
        return _migrate_failure(exc.code, args.json, target=args.to)
    except Exception:
        return _migrate_failure("INTERNAL_ERROR", args.json)
    resolved = _migrate_manifest_path(manifest_path)
    if args.json:
        # Build the complete document before printing; no partial output.
        print(json.dumps(_migrate_result_document(result), indent=2,
                         ensure_ascii=True, allow_nan=False))
    elif not result.changed:
        print(f"{resolved} already declares schema {result.to_version}. Nothing was written.")
    elif result.dry_run:
        print(f"Would declare schema {result.to_version} for {resolved} "
              f"({result.from_version} -> {result.to_version}). "
              "Dry run: nothing was written.")
    else:
        backup = result.backup_path if result.backup_path is not None else "none"
        print(f"Wrote {resolved} (schema {result.from_version} -> {result.to_version}). "
              f"Backup: {backup}.")
    return EXIT_OK


_CONDITIONS_RESULT_FORMAT = "runevidence-conditions-result-v1"
_CONDITIONS_ERROR_FORMAT = "runevidence-conditions-error-v1"

# One fixed line per closed code. Paths, run IDs, field names, submitted values,
# loader or OS text and tracebacks never enter this table or its output.
_CONDITIONS_ERROR_TEXT = {
    "INVALID_ARGUMENTS": (
        "ERROR (INVALID_ARGUMENTS): the conditions command line is invalid. "
        "Nothing was written."
    ),
    "UNSUPPORTED_VERSION": (
        "ERROR (UNSUPPORTED_VERSION): the conditions command requires an explicit "
        "schema 0.2 or later manifest. A schema 0.1 manifest can be migrated with "
        "runevidence migrate PATH --to 0.2. Nothing was written."
    ),
    "RESERVED_PATH": (
        "ERROR (RESERVED_PATH): the target resolves to a backup name; choose the "
        "manifest, not a backup. Nothing was written."
    ),
    "MANIFEST_LOAD_FAILED": (
        "ERROR (MANIFEST_LOAD_FAILED): the manifest could not be loaded and "
        "validated. Nothing was written."
    ),
    "RUN_SELECTION_INVALID": (
        "ERROR (RUN_SELECTION_INVALID): --run must match exactly one existing run "
        "ID. Nothing was written."
    ),
    "PATCH_INVALID": (
        "ERROR (PATCH_INVALID): the patch is not a valid conditions patch. "
        "Nothing was written."
    ),
    "SELECTOR_INVALID": (
        "ERROR (SELECTOR_INVALID): a declared run condition selector is invalid. "
        "Nothing was written."
    ),
    "SERIALIZATION_FAILED": (
        "ERROR (SERIALIZATION_FAILED): the updated manifest could not be "
        "serialized safely. Nothing was written."
    ),
    "READ_FAILED": (
        "ERROR (READ_FAILED): a required local input could not be read. "
        "Nothing was written."
    ),
    "STALE_MANIFEST": (
        "ERROR (STALE_MANIFEST): the bytes observed at the stale check differ "
        "from the loaded snapshot; nothing was replaced."
    ),
    "WRITE_FAILED": (
        "ERROR (WRITE_FAILED): the updated manifest could not be written; the "
        "manifest was not replaced."
    ),
    "INTERNAL_ERROR": (
        "ERROR (INTERNAL_ERROR): the conditions update failed internally; the "
        "manifest was not replaced."
    ),
    "BUSY": BUSY_TEXT,
    "LOCK_UNAVAILABLE": LOCK_UNAVAILABLE_TEXT,
}

def _conditions_failure(code: str, as_json: bool, *, version: str | None = None) -> int:
    """Emit exactly one fixed document for a refused conditions update."""
    if code not in _CONDITIONS_ERROR_TEXT:
        # Defensive: a code outside the closed catalogue is never echoed.
        code = "INTERNAL_ERROR"
    if as_json:
        document = {
            "format": _CONDITIONS_ERROR_FORMAT,
            "ok": False,
            "exit_code": EXIT_INVALID,
            "error": {"code": code},
        }
        print(json.dumps(document, indent=2, ensure_ascii=True, allow_nan=False))
    else:
        print(_CONDITIONS_ERROR_TEXT[code])
    return EXIT_INVALID


def _conditions_manifest_path(path: Path) -> Path:
    """Best-effort resolved manifest path for text output only."""
    try:
        return resolve_manifest_path(Path(path)).resolve()
    except (OSError, RuntimeError):
        # A successful update already resolved and loaded this path; this
        # fallback only prevents a post-success rendering crash.
        return Path(path)


def _conditions_result_document(result) -> dict:
    return {
        "format": _CONDITIONS_RESULT_FORMAT,
        "ok": True,
        "run": result.run_id,
        "changed": result.changed,
        "dry_run": result.dry_run,
        "backup": result.backup,
        "set": list(result.set_fields),
        "cleared": list(result.cleared_fields),
        "set_count": len(result.set_fields),
        "cleared_count": len(result.cleared_fields),
    }


def _conditions_request(args: argparse.Namespace):
    """Validate grammar-only conditions arguments before the core runs.

    Returns ``(patch_path, clears)`` or ``None`` for INVALID_ARGUMENTS. Missing
    ``--run``, a repeated ``--patch``, neither operation, and an unknown or
    duplicated ``--clear`` field are command-line errors. The patch value stays
    the raw option string: ``-`` is stdin, ``./-`` is a file.
    """
    if args.run is None:
        return None
    patch_values = args.patch
    if patch_values is not None and len(patch_values) > 1:
        return None
    patch_path = patch_values[0] if patch_values else None
    clears = args.clear or []
    seen: set[str] = set()
    for field in clears:
        if field not in CONDITIONS_FIELDS or field in seen:
            return None
        seen.add(field)
    if patch_path is None and not clears:
        return None
    return patch_path, tuple(clears)


def cmd_conditions(args: argparse.Namespace) -> int:
    """Set or clear recorded run conditions on a schema 0.2 manifest."""
    request = _conditions_request(args)
    if request is None:
        return _conditions_failure("INVALID_ARGUMENTS", args.json)
    patch_path, clears = request
    if not args.dry_run and not _writer_target_folder_exists(args.path):
        # Pre-admission path-shape failure: render the same closed refusal a
        # load would produce, and never write even if the path appears later.
        try:
            author_conditions(
                args.path,
                run_id=args.run,
                patch_path=patch_path,
                clears=clears,
                dry_run=True,
            )
        except ConditionsError as exc:
            return _conditions_failure(exc.code, args.json, version=exc.version)
        except Exception:
            return _conditions_failure("INTERNAL_ERROR", args.json)
        return _conditions_failure("LOCK_UNAVAILABLE", args.json)
    manifest_path = args.path
    try:
        # A dry run takes no lock; a real update holds it from before the load
        # through the replacement and backup. The admitted path is the resolved
        # target under the locked folder, not the caller's alias.
        with _writer_lock(args.path, enabled=not args.dry_run) as manifest_path:
            result = author_conditions(
                manifest_path,
                run_id=args.run,
                patch_path=patch_path,
                clears=clears,
                dry_run=args.dry_run,
            )
    except _WriterBusy:
        return _conditions_failure("BUSY", args.json)
    except _WriterLockUnavailable:
        return _conditions_failure("LOCK_UNAVAILABLE", args.json)
    except ConditionsError as exc:
        return _conditions_failure(exc.code, args.json, version=exc.version)
    except Exception:
        return _conditions_failure("INTERNAL_ERROR", args.json)
    resolved = _conditions_manifest_path(manifest_path)
    sets = ", ".join(result.set_fields) if result.set_fields else "none"
    cleared = ", ".join(result.cleared_fields) if result.cleared_fields else "none"
    if args.json:
        # Build the complete document before printing; no partial output.
        print(json.dumps(_conditions_result_document(result), indent=2,
                         ensure_ascii=True, allow_nan=False))
    elif not result.changed:
        if result.dry_run:
            print(f"{resolved} already matches the requested state for run "
                  f"{result.run_id}. Dry run: nothing was written.")
        else:
            print(f"{resolved} already matches the requested state for run "
                  f"{result.run_id}. Nothing was written.")
    elif result.dry_run:
        print(f"Would update {resolved} for run {result.run_id}\n"
              f"(set: {sets}; cleared: {cleared}). "
              "Dry run: nothing was written.")
    else:
        backup = result.backup_path if result.backup_path is not None else "none"
        print(f"Wrote {resolved} for run {result.run_id}\n"
              f"(set: {sets}; cleared: {cleared}).\n"
              f"Backup: {backup}")
    return EXIT_OK


def _event_artifact_is_volatile(manifest: Manifest, patterns: list[str]) -> bool:
    """True when a --volatile glob targets a regular-file event artifact.

    Only a schema 0.5 manifest has TensorBoard event artifacts; generic and
    0.1-0.4 init are untouched. The check is by path, not by whether a result
    points at the artifact, so an empty or fully filtered event file is still
    protected.
    """
    if not isinstance(
        manifest,
        (Manifest05, Manifest06, Manifest07, Manifest08, Manifest09, Manifest10),
    ):
        return False
    return any(
        getattr(artifact, "artifact_type", "file") == "file"
        and _tensorboard_path_matches(artifact.path)
        and any(glob_match(artifact.path, pattern) for pattern in patterns)
        for artifact in manifest.artifacts
    )


def _mark_volatile(manifest: Manifest, patterns: list[str]) -> Manifest:
    for pattern in patterns:
        if not any(glob_match(a.path, pattern) for a in manifest.artifacts):
            print(f"WARNING: --volatile {pattern!r} matches no file")
    artifacts = [
        a.model_copy(update={"volatile": True, "sha256": None, "size_bytes": None})
        if any(glob_match(a.path, p) for p in patterns)
        else a
        for a in manifest.artifacts
    ]
    return manifest.model_copy(update={"artifacts": artifacts})


def _validation_summary(exc: ValidationError) -> str:
    """One readable line for a generated manifest that fails the schema."""
    errors = exc.errors()
    shown = [
        f"{'.'.join(str(part) for part in error.get('loc', ())) or 'manifest'}: {error.get('msg', 'invalid')}"
        for error in errors[:3]
    ]
    return "; ".join(shown) + (f" (and {len(errors) - 3} more)" if len(errors) > 3 else "")


def _short(digest: Optional[str]) -> str:
    return digest[:12] + "..." if digest else "none"


def _locator_refresh_unsupported(exc: Exception) -> int:
    detail = str(exc) or "rehash cannot refresh an artifact read through a structured locator."
    print(f"ERROR (LOCATOR_REFRESH_UNSUPPORTED): {detail} Nothing was written.")
    return EXIT_INVALID


def _edit_error(exc: Exception) -> int:
    if getattr(exc, "code", None) == "LOCATOR_REFRESH_UNSUPPORTED":
        return _locator_refresh_unsupported(exc)
    print(f"ERROR: {exc}\nNothing was written.")
    return EXIT_INVALID


def _finish_edit(args: argparse.Namespace, raw: dict, loaded: LoadedManifest, summary: str) -> int:
    if args.dry_run:
        print(summary)
        print("Dry run: nothing was written.")
        return EXIT_OK
    try:
        save_raw(raw, loaded.path, expected_bytes=loaded.source_bytes)
    except OSError as exc:
        return _edit_error(exc)
    print(summary)
    print(f"Wrote {loaded.path}\nNext: runevidence validate {args.path}")
    return EXIT_OK


# --- S4-2b public directory authoring (Linux/ext4, text only) ----------------
#
# The public path composes the accepted S4-1 publication core. It takes the
# S4-1 admission lock (the same exclusive folder flock as S4-2a) directly and
# loads under that held handle; it must not nest the S4-2a writer lock, which
# would self-lock. Shape checks run before any lock or scan.

_DIRECTORY_ADD_SHAPE = (
    "ERROR: --directory on add requires exactly one folder (DIR) and --id ID."
)
_DIRECTORY_REHASH_SHAPE = (
    "ERROR: --directory on rehash requires exactly one exact directory artifact ID."
)
_DIRECTORY_VOLATILE_SHAPE = (
    "ERROR: --volatile and --no-volatile are not available with --directory."
)
_DIRECTORY_ALL_SHAPE = "ERROR: --all is not available with --directory."
_DIRECTORY_SELECTOR_SHAPE = (
    "ERROR: --directory on rehash accepts an exact artifact ID (bare or id:ID), "
    "not a selector or glob."
)


class _CountingIdAction(argparse.Action):
    """Store the last --id value and count occurrences.

    The existing plain-file add keeps argparse's last-wins behavior; the
    directory add additionally requires exactly one occurrence.
    """

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, values)
        namespace._id_count = getattr(namespace, "_id_count", 0) + 1


def _directory_shape(message: str) -> None:
    print(message)


def _directory_failure(reason: str, *, phase: str = "before-cell", note=None) -> int:
    """Print exactly one fixed, value-free refusal line and exit 2."""
    if reason == "scan-failed":
        reason = "recheck-failed"
    if reason == "busy":
        return _busy_failure()
    if reason == "lock-failed":
        return _lock_unavailable_failure()
    # These two must render on a host where the S4-1 module cannot even import.
    if reason == "unsupported-platform":
        print(
            "ERROR (UNSUPPORTED_PLATFORM): directory publication is not supported "
            "on this platform. Nothing was written."
        )
        return EXIT_INVALID
    if reason == "unsupported-filesystem":
        print(
            "ERROR (UNSUPPORTED_FILESYSTEM): the manifest folder is not on a "
            "release-qualified filesystem. Nothing was written."
        )
        return EXIT_INVALID
    from . import directory_publication as dp

    try:
        error = dp.DirectoryPublicationError(reason, phase=phase, note=note)
    except Exception:
        error = dp.DirectoryPublicationError("INTERNAL_ERROR", phase=phase)
    print(str(error))
    return EXIT_INVALID


def _directory_exception(exc: Exception) -> int:
    try:
        from . import directory_publication as dp
        from .directory_authoring import DirectoryAuthoringError
    except Exception:
        return _directory_failure("unsupported-platform")

    if isinstance(exc, dp.DirectoryPublicationError):
        return _directory_failure(exc.reason, phase=exc.phase, note=exc.note)
    if isinstance(exc, DirectoryAuthoringError):
        return _directory_failure(exc.reason)
    if isinstance(exc, (OSError, ValueError, ValidationError)):
        return _directory_failure("recheck-failed")
    return _directory_failure("INTERNAL_ERROR", phase="before-cell")


def _directory_lexical(path) -> Path:
    """CWD-based lexical absolute path; never realpath a symlink."""
    return Path(os.path.abspath(os.fspath(path)))


def _directory_manifest_root(path):
    """Resolve the manifest target/root with stats only (no load, no lock)."""
    from . import directory_publication as dp

    try:
        return dp._resolve_writer_folder(path)
    except Exception:
        return None


def _directory_fs_refusal(paths) -> Optional[str]:
    """Read-only release-policy preflight, before any lock or scan."""
    if not sys.platform.startswith("linux"):
        return "unsupported-platform"
    try:
        from . import directory_scanner as ds
    except Exception:
        return "unsupported-platform"
    try:
        policy = ds._SCAN_POLICY.get()
        table = ds._mountinfo()
        for path in paths:
            parts = ds._root_components(os.fspath(path))
            ds._preflight_mounts(parts, policy, table)
    except ds.DirectoryScanError as exc:
        if exc.reason in ("unsupported-platform", "unsupported-filesystem"):
            return exc.reason
        # A scan-level refusal (symlink member, unsafe path, unreadable tree)
        # is not a filesystem-capability problem; keep the fixed path reason.
        return "invalid-path"
    except Exception:
        # A hostile or unrepresentable path (for example NUL or extreme depth)
        # is an invalid path, not an unsupported filesystem.
        return "invalid-path"
    return None


def _directory_manifest_fallback(path) -> None:
    """Sanitized refusal when the manifest target cannot be resolved.

    The shared load reporter can print OS error text and an absolute path for
    hostile paths (for example ENAMETOOLONG). The directory command never uses
    it: a plainly absent target is ``not-found``; anything else is
    ``invalid-path``. Both are fixed, value-free lines.
    """
    absent = False
    try:
        operand = Path(path)
        candidate = operand / MANIFEST_FILENAME if operand.is_dir() else operand
    except (OSError, RuntimeError, TypeError, ValueError):
        candidate = None
    if candidate is not None:
        try:
            candidate.lstat()
        except FileNotFoundError:
            absent = True
        except (OSError, RuntimeError, TypeError, ValueError):
            absent = False
        else:
            try:
                candidate.stat()
            except FileNotFoundError:
                # A symlink whose target (or target folder) is missing is a
                # dangling manifest, not an unusable path.
                absent = True
            except (OSError, RuntimeError, TypeError, ValueError):
                absent = False
    _directory_failure("not-found" if absent else "invalid-path")


def _directory_recorded(root: Path, directory: Path):
    """Return ``(reason, manifest-relative path)``; reason None on success."""
    try:
        relative = directory.relative_to(root)
    except ValueError:
        return "outside-root", None
    text = relative.as_posix()
    if text in ("", "."):
        return "invalid-path", None
    return None, text


def _human_size(size) -> str:
    if size is None:
        return "none"
    if size < 1024:
        return f"{size} B"
    try:
        value = float(size)
    except OverflowError:
        # A hand-edited manifest can carry an integer too large for a float.
        # Printing must never fail after a successful publication; show the
        # exact byte count without converting it.
        return f"{size} B"
    for unit in ("KiB", "MiB", "GiB", "TiB"):
        value /= 1024.0
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
    return f"{value:.1f} TiB"


def _print_directory_result(args: argparse.Namespace, result, *, dry_run: bool) -> int:
    # Render the complete response before emitting anything. A formatting
    # failure after a real publication must not leave partial success output
    # in front of the phase-3 INTERNAL_ERROR line.
    lines: list[str] = []
    if result.status == "unchanged":
        lines.append(
            f"  {result.artifact_id}: unchanged "
            f"(sha256 {_short(result.new_sha256)}, {_count(result.members, 'member')})"
        )
        if dry_run:
            lines.append("Dry run: nothing was written.")
        print("\n".join(lines))
        return EXIT_OK
    old_short = _short(result.old_sha256)
    new_short = _short(result.new_sha256)
    if result.operation == "add":
        if dry_run:
            lines.append(
                f"  {result.artifact_id}: would record sha256 {new_short}, "
                f"{_count(result.members, 'member')}"
            )
        else:
            lines.append(
                f"  {result.artifact_id}: {result.path}  sha256 {new_short}  "
                f"{_count(result.members, 'member')}  {_human_size(result.new_size)}"
            )
    elif dry_run:
        lines.append(
            f"  {result.artifact_id}: would change sha256 {old_short} -> {new_short}, "
            f"{_human_size(result.old_size)} -> {_human_size(result.new_size)}, "
            f"{_count(result.members, 'member')}"
        )
    else:
        lines.append(
            f"  {result.artifact_id}: sha256 {old_short} -> {new_short}, "
            f"{_human_size(result.old_size)} -> {_human_size(result.new_size)}, "
            f"{_count(result.members, 'member')}"
        )
    if dry_run:
        lines.append("Dry run: nothing was written.")
        print("\n".join(lines))
        return EXIT_OK
    lines.append(
        f"{'Added' if result.operation == 'add' else 'Re-recorded'} 1 directory artifact"
    )
    lines.append(f"Wrote {result.manifest_path}")
    if result.backup_path is not None:
        lines.append(f"Saved the previous manifest to {result.backup_path}")
    for warning in result.warnings:
        if warning == "durability-unknown":
            lines.append(
                "WARNING (DURABILITY_UNKNOWN): the manifest was written, but its "
                "durability is not proven."
            )
        elif warning == "cleanup-warning":
            lines.append(
                "WARNING (CLEANUP_WARNING): a temporary copy could not be removed; "
                "the published backup is complete."
            )
    lines.append(f"Next: runevidence validate {args.path} --directory-artifacts")
    print("\n".join(lines))
    return EXIT_OK


def _directory_load_dry_run(path):
    """Load for a dry run without the shared, OS-text-printing reporter.

    The legacy ``_load_or_report`` prints a server-style manifest-unreadable
    line with the absolute path and OS error text, which the directory command
    must never expose. A dry-run load failure becomes ``not-found`` when the
    manifest path does not exist, ``invalid-path`` for a special file (FIFO,
    directory) and ``invalid-manifest`` otherwise.
    """
    try:
        manifest = resolve_manifest_path(Path(path)).resolve()
    except FileNotFoundError:
        _directory_failure("not-found")
        return None
    except (OSError, RuntimeError, TypeError, ValueError):
        _directory_failure("invalid-manifest")
        return None
    try:
        # O_NONBLOCK: a FIFO swapped in after resolution must not hang the dry
        # run. The fstat check requires a regular file.
        fd = os.open(
            manifest,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOCTTY,
        )
    except FileNotFoundError:
        _directory_failure("not-found")
        return None
    except PermissionError:
        _directory_failure("invalid-manifest")
        return None
    except (OSError, TypeError, ValueError):
        _directory_failure("invalid-path")
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            _directory_failure("invalid-path")
            return None
        with os.fdopen(fd, "rb", closefd=False) as handle:
            source = handle.read()
    except (OSError, ValueError):
        _directory_failure("invalid-manifest")
        return None
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    try:
        return parse_manifest_source(manifest, source)
    except Exception:
        _directory_failure("invalid-manifest")
        return None


def _directory_manifest_usable(target) -> bool:
    """Fixed refusal when the manifest cannot be read at all.

    The accepted core maps an unreadable source to ``stale-source`` and a
    missing one to ``not-found``; before admission the command can tell the two
    apart cheaply and report a truthful, value-free reason. This is a read-only
    pre-check, not a lock or an authorization: the core still re-checks the
    source under the held handle and reports ``stale-source`` if it changes.
    """
    try:
        Path(target).lstat()
    except FileNotFoundError:
        _directory_failure("not-found")
        return False
    except (OSError, RuntimeError, TypeError, ValueError):
        _directory_failure("invalid-path")
        return False
    try:
        # O_NONBLOCK: opening a FIFO must not wait for a writer if another
        # process swaps the manifest between resolution and this pre-check.
        fd = os.open(
            target,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOCTTY,
        )
    except FileNotFoundError:
        _directory_failure("not-found")
        return False
    except PermissionError:
        _directory_failure("permission-denied")
        return False
    except (OSError, TypeError, ValueError):
        _directory_failure("invalid-path")
        return False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            _directory_failure("invalid-path")
            return False
    except OSError:
        _directory_failure("invalid-path")
        return False
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    return True


def _directory_prepare_add(args: argparse.Namespace):
    if not sys.platform.startswith("linux"):
        _directory_failure("unsupported-platform")
        return None
    if args.run and args.no_run:
        print("ERROR: use --run or --no-run, not both")
        return None
    if args.volatile:
        _directory_shape(_DIRECTORY_VOLATILE_SHAPE)
        return None
    if not args.id or getattr(args, "_id_count", 0) != 1 or len(args.files) != 1:
        _directory_shape(_DIRECTORY_ADD_SHAPE)
        return None
    resolved = _directory_manifest_root(args.path)
    if resolved is None:
        _directory_manifest_fallback(args.path)
        return None
    _target, root = resolved
    directory = _directory_lexical(args.files[0])
    reason, _recorded = _directory_recorded(root, directory)
    if reason is not None:
        _directory_failure(reason)
        return None
    if not directory.is_dir():
        _directory_failure("invalid-path")
        return None
    fs_reason = _directory_fs_refusal([root, directory])
    if fs_reason is not None:
        _directory_failure(fs_reason)
        return None
    if not _directory_manifest_usable(_target):
        return None
    return root, directory


def _directory_prepare_rehash(args: argparse.Namespace):
    if not sys.platform.startswith("linux"):
        _directory_failure("unsupported-platform")
        return None
    if args.all:
        _directory_shape(_DIRECTORY_ALL_SHAPE)
        return None
    if args.volatile is not None:
        _directory_shape(_DIRECTORY_VOLATILE_SHAPE)
        return None
    if len(args.targets) != 1:
        _directory_shape(_DIRECTORY_REHASH_SHAPE)
        return None
    target = args.targets[0]
    if ":" in target:
        if not target.startswith("id:"):
            _directory_shape(_DIRECTORY_SELECTOR_SHAPE)
            return None
        artifact_id = target[3:]
    else:
        artifact_id = target
    if (
        not artifact_id
        or artifact_id in (".", "..")
        or "/" in artifact_id
        or "\\" in artifact_id
        or any(character in artifact_id for character in "*?[")
    ):
        _directory_shape(_DIRECTORY_SELECTOR_SHAPE)
        return None
    resolved = _directory_manifest_root(args.path)
    if resolved is None:
        _directory_manifest_fallback(args.path)
        return None
    _target, root = resolved
    fs_reason = _directory_fs_refusal([root])
    if fs_reason is not None:
        _directory_failure(fs_reason)
        return None
    if not _directory_manifest_usable(_target):
        return None
    return artifact_id


def _directory_add_checked(
    args: argparse.Namespace, loaded: LoadedManifest, locked, directory: Path
) -> int:
    from . import directory_publication as dp
    from .directory_authoring import propose_add_directory

    reason, recorded = _directory_recorded(loaded.root, directory)
    if reason is not None:
        return _directory_failure(reason)
    fs_reason = _directory_fs_refusal([loaded.root, directory])
    if fs_reason is not None:
        return _directory_failure(fs_reason)
    proposal = propose_add_directory(
        loaded,
        load_raw(loaded),
        path=recorded,
        artifact_id=args.id,
        kind=args.kind,
        run_id=args.run,
    )
    result = dp.publish_directory_proposal(
        locked, proposal, dry_run=args.dry_run, loaded=loaded
    )
    try:
        return _print_directory_result(args, result, dry_run=args.dry_run)
    except Exception:
        # Output happens after a completed publication. A failure here must
        # never claim that nothing was written; use the result's own phase.
        return _directory_failure(
            "INTERNAL_ERROR", phase=getattr(result, "phase", "at-replace")
        )


def _directory_rehash_checked(
    args: argparse.Namespace, loaded: LoadedManifest, locked, artifact_id: str
) -> int:
    from . import directory_publication as dp
    from .directory_authoring import propose_rehash_directory

    artifact = next(
        (entry for entry in loaded.manifest.artifacts if entry.id == artifact_id), None
    )
    if artifact is not None and getattr(artifact, "artifact_type", "file") == "directory":
        parts = artifact.path.split("/")
        if not artifact.path.startswith("/") and ".." not in parts:
            directory = loaded.root.absolute().joinpath(*parts)
            fs_reason = _directory_fs_refusal([loaded.root, directory])
            if fs_reason is not None:
                return _directory_failure(fs_reason)
    proposal = propose_rehash_directory(
        loaded, load_raw(loaded), artifact_id=artifact_id
    )
    result = dp.publish_directory_proposal(
        locked, proposal, dry_run=args.dry_run, loaded=loaded
    )
    try:
        return _print_directory_result(args, result, dry_run=args.dry_run)
    except Exception:
        # Output happens after a completed publication. A failure here must
        # never claim that nothing was written; use the result's own phase.
        return _directory_failure(
            "INTERNAL_ERROR", phase=getattr(result, "phase", "at-replace")
        )


def _cmd_add_directory(args: argparse.Namespace) -> int:
    try:
        prepared = _directory_prepare_add(args)
    except Exception:
        # Preparation stats paths and can fail on a hostile path (for example
        # ENAMETOOLONG). Keep the sanitized fixed line: no traceback, path or
        # strerror text, and nothing was written.
        return _directory_failure("invalid-path")
    if prepared is None:
        return EXIT_INVALID
    _root, directory = prepared
    from . import directory_publication as dp
    from .directory_authoring import DirectoryAuthoringError

    try:
        if args.dry_run:
            loaded = _directory_load_dry_run(args.path)
            if loaded is None:
                return EXIT_INVALID
            return _directory_add_checked(args, loaded, None, directory)
        with dp.manifest_admission_lock(args.path) as locked:
            loaded = dp.load_manifest_under_lock(locked, args.path)
            dp.preflight_directory_publication(locked)
            return _directory_add_checked(args, loaded, locked, directory)
    except (dp.DirectoryPublicationError, DirectoryAuthoringError,
            OSError, ValueError, ValidationError) as exc:
        return _directory_exception(exc)
    except Exception:
        return _directory_failure("INTERNAL_ERROR", phase="before-cell")


def _cmd_rehash_directory(args: argparse.Namespace) -> int:
    try:
        artifact_id = _directory_prepare_rehash(args)
    except Exception:
        # See _cmd_add_directory: preparation is path/stat-only and must stay
        # inside the sanitized boundary.
        return _directory_failure("invalid-path")
    if artifact_id is None:
        return EXIT_INVALID
    from . import directory_publication as dp
    from .directory_authoring import DirectoryAuthoringError

    try:
        if args.dry_run:
            loaded = _directory_load_dry_run(args.path)
            if loaded is None:
                return EXIT_INVALID
            return _directory_rehash_checked(args, loaded, None, artifact_id)
        with dp.manifest_admission_lock(args.path) as locked:
            loaded = dp.load_manifest_under_lock(locked, args.path)
            dp.preflight_directory_publication(locked)
            return _directory_rehash_checked(args, loaded, locked, artifact_id)
    except (dp.DirectoryPublicationError, DirectoryAuthoringError,
            OSError, ValueError, ValidationError) as exc:
        return _directory_exception(exc)
    except Exception:
        return _directory_failure("INTERNAL_ERROR", phase="before-cell")


_REFRESH_USAGE = "--refresh-locators accepts only exact artifact IDs"


def _refresh_targets(targets: list[str]) -> list[str] | None:
    parsed: list[str] = []
    seen: set[str] = set()
    for raw in targets:
        if raw.startswith(("run:", "path:", "folder:", "glob:")) or raw == "id:":
            print(f"ERROR: {_REFRESH_USAGE}")
            return None
        value = raw[3:] if raw.startswith("id:") else raw
        if not value:
            print(f"ERROR: {_REFRESH_USAGE}")
            return None
        if value not in seen:
            seen.add(value)
            parsed.append(value)
    return parsed


def _cmd_refresh_locators(args: argparse.Namespace) -> int:
    if args.all:
        print("ERROR: --refresh-locators requires explicit artifact targets; --all is not accepted")
        return EXIT_INVALID
    if args.volatile is not None or getattr(args, "directory", False):
        print(
            "ERROR: --refresh-locators cannot be combined with --volatile, "
            "--no-volatile or --directory"
        )
        return EXIT_INVALID
    if not args.targets:
        print("ERROR: --refresh-locators needs at least one artifact target")
        return EXIT_INVALID
    targets = _refresh_targets(args.targets)
    if targets is None:
        return EXIT_INVALID
    if not args.dry_run and not _writer_target_folder_exists(args.path):
        loaded = _load_or_report(args.path, False)
        if loaded is None:
            return EXIT_INVALID
        return _lock_unavailable_failure()
    try:
        with _writer_lock(args.path, enabled=not args.dry_run) as manifest_path:
            return _refresh_under_lock(args, manifest_path, targets)
    except _WriterBusy:
        return _busy_failure()
    except _WriterLockUnavailable:
        return _lock_unavailable_failure()


def _refresh_under_lock(
    args: argparse.Namespace, manifest_path, targets: list[str]
) -> int:
    loaded = _load_or_report(manifest_path, False)
    if loaded is None:
        return EXIT_INVALID
    raw = load_raw(loaded)
    try:
        plan = plan_refresh(loaded, raw, targets)
    except (EditError, OSError, ValidationError) as exc:
        return _edit_error(exc)

    changed = plan.changed_files
    verified = [item for item in plan.files if not item.changed]
    for item in changed:
        if item.old_sha256 == item.new_sha256 and item.old_size != item.new_size:
            old_size = "none" if item.old_size is None else item.old_size
            print(f"  {item.id}: size {old_size} -> {item.new_size} bytes; sha256 unchanged")
            continue
        line = f"  {item.id}: sha256 {_short(item.old_sha256)} -> {_short(item.new_sha256)}"
        if item.old_size != item.new_size:
            old_size = "none" if item.old_size is None else item.old_size
            line += f", {old_size} -> {item.new_size} bytes"
        print(line)
    for item in verified:
        print(f"  {item.id}: verified; bytes and locators match")
    for note in plan.notes:
        print(f"  NOTE {note}")
    if plan.claims:
        print(f"Re-read these claims; they rest on what changed: {', '.join(plan.claims)}")
    if changed:
        prefix = "Would re-record" if args.dry_run else "Re-recorded"
        if verified:
            action = "verify" if args.dry_run else "verified"
            summary = (
                f"{prefix} {_count(len(changed), 'file')}"
                f" and {action} {_count(len(verified), 'file')}"
            )
        else:
            summary = f"{prefix} {_count(len(changed), 'file')}"
        return _finish_edit(args, raw, loaded, summary)
    prefix = "Would verify" if args.dry_run else "Verified"
    print(f"{prefix} {_count(len(verified), 'file')}; no changes.")
    return EXIT_OK


def cmd_rehash(args: argparse.Namespace) -> int:
    if getattr(args, "refresh_locators", False):
        return _cmd_refresh_locators(args)
    if getattr(args, "directory", False):
        return _cmd_rehash_directory(args)
    if args.targets and args.all:
        print("ERROR: name targets or pass --all, not both")
        return EXIT_INVALID
    if not args.targets and not args.all:
        print("ERROR: name what to re-record (artifact IDs, run IDs, paths or globs), or pass --all for everything")
        return EXIT_INVALID
    if args.all and args.volatile is not None:
        print("ERROR: --volatile and --no-volatile need explicit targets, not --all")
        return EXIT_INVALID
    if not args.dry_run and not _writer_target_folder_exists(args.path):
        # Pre-admission path-shape failure: keep the legacy missing/unreadable
        # report and never write, even if the path appears later.
        loaded = _load_or_report(args.path, False)
        if loaded is None:
            return EXIT_INVALID
        return _lock_unavailable_failure()
    try:
        # A dry run takes no lock; a real edit holds it from before the load
        # through the save. The admitted path is the resolved target under the
        # locked folder, not the caller's alias.
        with _writer_lock(args.path, enabled=not args.dry_run) as manifest_path:
            return _rehash_under_lock(args, manifest_path)
    except _WriterBusy:
        return _busy_failure()
    except _WriterLockUnavailable:
        return _lock_unavailable_failure()


def _rehash_under_lock(args: argparse.Namespace, manifest_path) -> int:
    loaded = _load_or_report(manifest_path, False)
    if loaded is None:
        return EXIT_INVALID
    raw = load_raw(loaded)
    try:
        if args.all:
            indexes = list(range(len(loaded.manifest.artifacts)))
        else:
            indexes = select_artifacts(loaded, args.targets)
        plan = plan_rehash(loaded, raw, indexes, volatile=args.volatile)
    except (EditError, OSError, ValidationError) as exc:
        return _edit_error(exc)

    changed = plan.changed_files
    for f in changed:
        if f.volatile:
            what = "volatile, unused digest cleared" if f.was_volatile else "now volatile (existence only)"
        elif f.was_volatile and not f.volatile:
            what = f"hash-checked again, sha256 {_short(f.new_sha256)}"
        else:
            what = f"sha256 {_short(f.old_sha256)} -> {_short(f.new_sha256)}"
            if f.old_size != f.new_size:
                what += f", {f.old_size} -> {f.new_size} bytes"
        print(f"  {f.id}: {what}")
    for v in plan.values:
        print(f"  result {v.result_id} ({v.name}): {v.old!r} -> {v.new!r}")
    for note in plan.notes:
        print(f"  NOTE {note}")
    unchanged = len(plan.files) - len(changed)
    summary = (
        f"{'Would re-record' if args.dry_run else 'Re-recorded'} {_count(len(changed), 'file')}"
        + (f" and {_count(len(plan.values), 'result value')}" if plan.values else "")
        + (f" ({unchanged} already up to date)" if unchanged else "")
    )
    if plan.claims:
        print(f"Re-read these claims; they rest on what changed: {', '.join(plan.claims)}")
    if changed or plan.values:
        return _finish_edit(args, raw, loaded, summary)
    print(summary)
    return EXIT_OK


def cmd_add(args: argparse.Namespace) -> int:
    if getattr(args, "directory", False):
        return _cmd_add_directory(args)
    if args.run and args.no_run:
        print("ERROR: use --run or --no-run, not both")
        return EXIT_INVALID
    if not args.dry_run and not _writer_target_folder_exists(args.path):
        # Pre-admission path-shape failure: keep the legacy missing/unreadable
        # report and never write, even if the path appears later.
        loaded = _load_or_report(args.path, False)
        if loaded is None:
            return EXIT_INVALID
        return _lock_unavailable_failure()
    try:
        # A dry run takes no lock; a real edit holds it from before the load
        # through the save. The admitted path is the resolved target under the
        # locked folder, not the caller's alias.
        with _writer_lock(args.path, enabled=not args.dry_run) as manifest_path:
            return _add_under_lock(args, manifest_path)
    except _WriterBusy:
        return _busy_failure()
    except _WriterLockUnavailable:
        return _lock_unavailable_failure()


def _add_under_lock(args: argparse.Namespace, manifest_path) -> int:
    loaded = _load_or_report(manifest_path, False)
    if loaded is None:
        return EXIT_INVALID
    raw = load_raw(loaded)
    try:
        plan = plan_add(
            loaded,
            raw,
            args.files,
            art_id=args.id,
            kind=args.kind,
            run_id=args.run,
            infer_run=not args.no_run,
            volatile=args.volatile,
        )
    except (EditError, OSError, ValidationError) as exc:
        return _edit_error(exc)

    for a in plan.added:
        detail = "volatile (existence only)" if a.get("volatile") else f"sha256 {_short(a['sha256'])}"
        if a["run_id"]:
            detail += f", run {a['run_id']}"
            if a["id"] in plan.inferred_runs:
                detail += " (from neighbouring files; change with --run or --no-run)"
        print(f"  {a['id']}: {a['path']}  {detail}")
    if plan.skipped:
        print(f"  ({_count(len(plan.skipped), 'file')} inside already tracked, left as is)")
    if not plan.added:
        print("Nothing new to add.")
        return EXIT_OK
    summary = f"{'Would add' if args.dry_run else 'Added'} {_count(len(plan.added), 'artifact')}"
    return _finish_edit(args, raw, loaded, summary)


def _backup_existing(out: Path, new: Manifest) -> Path:
    """``init --force`` regenerates from disk, which drops everything written
    by hand. Keep the old file and say exactly what the new one lacks.

    Every backup is a new generation: runevidence.json.bak first, then
    runevidence.json.bak.2, .bak.3, ... An earlier backup is never
    overwritten or deleted, so repeated runs cannot destroy the only copy of
    a hand-edited manifest. If the newest backup already holds exactly the
    current bytes, it is reused instead of writing a duplicate. Raises
    OSError (before the manifest is touched) if the backup cannot be made."""
    data = out.read_bytes()
    newest_gen, newest = _newest_backup(out.parent)
    if newest is not None and _reusable_backup(newest, out, data):
        backup = newest
        print(f"The previous manifest is already saved in {backup}")
    else:
        name = BACKUP_FILENAME if newest_gen == 0 else f"{BACKUP_FILENAME}.{newest_gen + 1}"
        backup = out.with_name(name)
        _write_new_file(backup, data)
        print(f"Saved the previous manifest to {backup}")
    try:
        old = load_manifest(out).manifest
    except ManifestLoadError:
        return backup
    if old.schema_version == "0.2":
        # Fixed, value-free warning: emitted only after the backup succeeded and
        # the previous manifest was successfully loaded and identified as 0.2.
        print(
            "WARNING: the existing manifest declares schema 0.2. Regeneration writes schema 0.1\n"
            "and does not carry over first-class conditions, dataset_id or metric_definition.\n"
            "The backup keeps the previous manifest."
        )
    lost, renamed = _lost_on_regenerate(old, new)
    if lost:
        print(f"  WARNING: the new manifest does not contain its {', '.join(lost)}; copy them back from the backup.")
    if renamed:
        examples = ", ".join(f"{a!r} is now {b!r}" for a, b in renamed[:3])
        print(f"  NOTE: {_count(len(renamed), 'artifact')} got a new ID ({examples}); update references when copying back.")
    return backup


def _newest_backup(folder: Path) -> tuple[int, Optional[Path]]:
    """Highest backup generation present in ``folder`` (0 if none) and its path."""
    best, path = 0, None
    for name in os.listdir(folder):
        gen = backup_generation(name)
        if gen is not None and gen > best:
            best, path = gen, folder / name
    return best, path


def _reusable_backup(candidate: Path, target: Path, data: bytes) -> bool:
    """Whether the newest generation may be reused instead of writing another.

    A reused generation must be a regular file (not a symlink), a distinct
    file rather than the target or a hardlink/same-file alias, and hold exactly
    the current target bytes. Any failed or unavailable check falls through to
    creating the next generation; the existing file is never touched.
    """
    try:
        link_stat = os.lstat(candidate)
    except OSError:
        return False
    if not stat.S_ISREG(link_stat.st_mode) or stat.S_ISLNK(link_stat.st_mode):
        return False
    # True means the target itself or an alias; None means identity cannot be
    # established. Neither is safe to reuse.
    if _same_file(candidate, target) is not False:
        return False
    try:
        return candidate.read_bytes() == data
    except OSError:
        return False


def _same_file(candidate: Path, target: Path) -> bool | None:
    """True/False for same-file, or None when no identity check is available."""
    samefile = getattr(os.path, "samefile", None)
    if samefile is not None:
        try:
            return samefile(candidate, target)
        except OSError:
            pass
    try:
        candidate_stat = os.stat(candidate)
        target_stat = os.stat(target)
    except (OSError, AttributeError):
        return None
    return (candidate_stat.st_dev, candidate_stat.st_ino) == (
        target_stat.st_dev,
        target_stat.st_ino,
    )


def _write_new_file(path: Path, data: bytes) -> None:
    """Create ``path`` with ``data``, refusing to replace anything already
    there (even a file created since its name was chosen). A partly written
    file is removed, and the result is read back before it is trusted."""
    with open(path, "xb") as fh:
        try:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        except BaseException:
            fh.close()
            path.unlink(missing_ok=True)
            raise
    if path.read_bytes() != data:
        raise OSError(f"backup {path} does not match the manifest it should copy")


def _count(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _changed(old: object, new: object, fields: tuple[str, ...]) -> list[str]:
    """Fields set on ``old`` that ``new`` does not carry over."""
    return [f for f in fields if getattr(old, f) and getattr(old, f) != getattr(new, f, None)]


def _lost_on_regenerate(old: Manifest, new: Manifest) -> tuple[list[str], list[tuple[str, str]]]:
    """What a regenerated manifest lacks compared with the hand-edited one,
    plus artifacts whose ID changed (references to them would dangle)."""
    new_runs = {r.id: r for r in new.runs}
    new_arts = {a.path: a for a in new.artifacts}
    runs_lost = sum(r.id not in new_runs for r in old.runs)
    runs_edited = sum(
        bool(_changed(r, new_runs[r.id], ("name", "started_at", "params", "metadata")))
        for r in old.runs
        if r.id in new_runs
    )
    annotated, renamed = 0, []
    volatile_lost = 0
    for a in old.artifacts:
        b = new_arts.get(a.path)
        if b is None:
            continue
        annotated += bool(_changed(a, b, ("run_id", "kind", "metadata")))
        volatile_lost += a.volatile and not b.volatile
        if a.id != b.id:
            renamed.append((a.id, b.id))
    project = _changed(old.project, new.project, ("description", "tags", "metadata"))
    lost = [
        _count(len(old.results), "result") if old.results else "",
        _count(len(old.evidence), "claim") if old.evidence else "",
        _count(len(old.relationships), "relationship") if old.relationships else "",
        _count(runs_lost, "run") if runs_lost else "",
        f"name/params/metadata on {_count(runs_edited, 'run')}" if runs_edited else "",
        f"run_id/kind/metadata on {_count(annotated, 'artifact')}" if annotated else "",
        f"volatile flag on {_count(volatile_lost, 'artifact')}" if volatile_lost else "",
        f"project {'/'.join(project)}" if project else "",
    ]
    return [item for item in lost if item], renamed


def cmd_schema(args: argparse.Namespace) -> int:
    text = json.dumps(json_schema(), indent=2) + "\n"
    if args.output:
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"Wrote {args.output}")
    else:
        sys.stdout.write(text)
    return EXIT_OK


def cmd_adapters(args: argparse.Namespace) -> int:
    adapters = available_adapters()
    if args.json:
        print(json.dumps(
            {
                "format": "runevidence-adapters-v1",
                "version": "0.1",
                "adapters": [
                    {
                        "name": name,
                        "description": cls.description,
                        "module": cls.__module__,
                    }
                    for name, cls in sorted(adapters.items())
                ],
                "exit_code": EXIT_OK,
            },
            indent=2,
            sort_keys=False,
        ))
        return EXIT_OK
    for name, cls in sorted(adapters.items()):
        print(f"{name:<12} {cls.description}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="runevidence",
        description=(
            "Check that the evidence in an experiment folder still holds. Offline. "
            "init, migrate, conditions, rehash and add write the manifest; schema -o exports a schema."
        ),
        epilog=(
            "Exit codes: 0 ok, 1 evidence problems found, 2 could not check (bad manifest or arguments). "
            "A clean result means the folder matches its manifest; it does not show that results "
            "are scientifically true, valid or fresh."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def add_check_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("path", type=Path, help=f"experiment folder (containing {MANIFEST_FILENAME}) or manifest file")
        p.add_argument("--json", action="store_true", help="machine-readable output")
        p.add_argument("--redact-manifest-path", action="store_true",
                       help="redact manifest path fields in JSON output (not full sanitization; requires --json)")
        p.add_argument("--strict", action="store_true", help="treat warnings as failures (exit 1)")
        p.add_argument("--directory-artifacts", action="store_true",
                       help="experimental read-only directory-artifact checking")

    p = sub.add_parser("validate", help="check files, hashes and evidence links; print problems only")
    add_check_args(p)
    p.add_argument("--summary-only", action="store_true",
                   help="requires --json; print only finding counts by severity and code, plus the exit code")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("validate-batch",
                       help="requires --json; validate several manifests in order and print each "
                            "counts-only summary plus an aggregate (exit: highest individual code)")
    p.add_argument("manifests", nargs="+", metavar="MANIFEST",
                   help=f"experiment folder (containing {MANIFEST_FILENAME}) or manifest file; repeats are checked again")
    p.add_argument("--json", action="store_true", help="machine-readable batch summary (required)")
    p.add_argument("--redact-manifest-path", action="store_true",
                   help="replace each echoed input path with \"<redacted>\" (requires --json)")
    p.add_argument("--strict", action="store_true", help="treat warnings as failures (exit 1)")
    p.add_argument("--directory-artifacts", action="store_true",
                   help="experimental read-only directory-artifact checking")
    p.set_defaults(func=cmd_validate_batch)

    p = sub.add_parser("inspect", help="summarize the manifest, then run every check (incl. untracked files)")
    add_check_args(p)
    p.add_argument("--markdown", action="store_true", help="print a shareable Markdown report")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("compare", help="compare recorded parameters and results of two runs (no artifact checks)")
    p.add_argument("path", type=Path, help=f"experiment folder or {MANIFEST_FILENAME} file")
    p.add_argument("--base", required=True, metavar="RUN_ID", help="baseline run ID")
    p.add_argument("--target", required=True, metavar="RUN_ID", help="target run ID")
    p.add_argument(
        "--latest",
        action="store_true",
        help="compare the highest '<name> @ <step>' value per run",
    )
    p.add_argument("--json", action="store_true", help="machine-readable comparison")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("comparability", help="experimental read-only recorded-condition comparison (no artifact checks)")
    p.add_argument("baseline", type=Path, help="baseline experiment folder or manifest JSON file")
    p.add_argument("target", type=Path, help="target experiment folder or manifest JSON file")
    p.add_argument("--baseline-result", metavar="ID", help="exact baseline result ID")
    p.add_argument("--target-result", metavar="ID", help="exact target result ID")
    p.add_argument(
        "--preflight",
        action="store_true",
        help="read-only readiness listing/pair preflight (text only; no comparison)",
    )
    p.add_argument("--json", action="store_true", help="machine-readable comparison envelope")
    p.set_defaults(func=cmd_comparability)

    p = sub.add_parser(
        "assess",
        help="compare recorded conditions and artifact/value integrity together (read-only)",
    )
    p.add_argument("baseline", type=Path, help="baseline experiment folder or manifest JSON file")
    p.add_argument("target", type=Path, help="target experiment folder or manifest JSON file")
    p.add_argument("--baseline-result", required=True, metavar="ID")
    p.add_argument("--target-result", required=True, metavar="ID")
    p.add_argument("--directory-artifacts", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_assess)

    p = sub.add_parser("leaderboard", help="rank runs by a recorded numeric metric (no artifact checks)")
    p.add_argument("path", type=Path, help=f"experiment folder or {MANIFEST_FILENAME} file")
    p.add_argument("--metric", required=True, metavar="NAME", help="recorded metric name")
    p.add_argument("--direction", choices=("max", "min"), required=True,
                   help="whether a larger (max) or smaller (min) recorded value ranks first")
    p.add_argument("--top", type=int, default=10, metavar="N", help="show 1 to 1000 ranked runs (default: 10)")
    p.add_argument(
        "--latest",
        action="store_true",
        help="rank the highest '<name> @ <step>' value per run",
    )
    p.add_argument("--json", action="store_true", help="machine-readable leaderboard")
    p.set_defaults(func=cmd_leaderboard)

    p = sub.add_parser("stats", help="summarize recorded metrics with counts and distribution statistics")
    p.add_argument("path", type=Path, help=f"experiment folder or {MANIFEST_FILENAME} file")
    p.add_argument("--metric", metavar="NAME", help="include only this metric")
    p.add_argument(
        "--latest",
        action="store_true",
        help="summarize the highest '<name> @ <step>' value per run",
    )
    p.add_argument("--json", action="store_true", help="machine-readable statistics")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("matrix", help="view recorded metrics side by side for selected runs (no artifact checks)")
    p.add_argument("path", type=Path, help=f"experiment folder or {MANIFEST_FILENAME} file")
    p.add_argument("--run", action="append", metavar="RUN_ID", help="include this run (repeatable; default: all)")
    p.add_argument("--metric", action="append", metavar="NAME", help="include this metric (repeatable; default: all)")
    p.add_argument(
        "--latest",
        action="store_true",
        help="show the highest '<name> @ <step>' value per run",
    )
    p.add_argument("--json", action="store_true", help="machine-readable metric matrix")
    p.set_defaults(func=cmd_matrix)

    p = sub.add_parser("run", help="show or author one recorded run")
    p.add_argument("path", type=Path, help=f"experiment folder or {MANIFEST_FILENAME} file")
    p.add_argument("--id", metavar="RUN_ID", help="run ID to inspect or remove")
    actions = p.add_mutually_exclusive_group()
    actions.add_argument("--add", action="store_true", help="add the run in --file")
    actions.add_argument("--edit", action="store_true", help="edit the run in --file")
    actions.add_argument("--remove", action="store_true", help="remove --id")
    p.add_argument("--file", help="one JSON object; see docs/authoring-files.md")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true", help="machine-readable run detail")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("db-export", help="export a validated manifest to a derived SQLite cache")
    p.add_argument("path", type=Path, help=f"experiment folder or {MANIFEST_FILENAME} file")
    p.add_argument("--output", type=Path, required=True, help="SQLite file to write")
    p.add_argument("--force", action="store_true", help="replace an existing output file")
    p.add_argument("--json", action="store_true", help="machine-readable summary")
    p.set_defaults(func=cmd_db_export)

    p = sub.add_parser("db-check", help="compare a derived SQLite cache with the manifest")
    p.add_argument("path", type=Path, help=f"experiment folder or {MANIFEST_FILENAME} file")
    p.add_argument("--db", type=Path, required=True, help="SQLite cache to compare")
    p.add_argument("--json", action="store_true", help="machine-readable result")
    p.set_defaults(func=cmd_db_check)

    p = sub.add_parser("db-query", help="run one read-only query against a derived SQLite cache")
    p.add_argument("--db", type=Path, required=True, help="SQLite cache to query")
    p.add_argument("--sql", required=True, help="SELECT or WITH statement")
    p.add_argument("--limit", type=int, default=1000, help="maximum rows (default: 1000)")
    p.set_defaults(func=cmd_db_query)

    p = sub.add_parser("db-import", help="import a derived SQLite manifest cache to JSON")
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_db_import)

    p = sub.add_parser("runs", help="list recorded runs with params and conditions")
    p.add_argument("path", type=Path, help=f"experiment folder or {MANIFEST_FILENAME} file")
    p.add_argument("--metadata", action="store_true", help="include run metadata")
    p.add_argument("--summaries", action="store_true", help="include whitelisted adapter summaries")
    p.add_argument("--json", action="store_true", help="machine-readable run inventory")
    p.set_defaults(func=cmd_runs)

    p = sub.add_parser("claim", help="show recorded support and contradiction targets for one evidence claim (no artifact checks)")
    p.add_argument("path", type=Path, help=f"experiment folder or {MANIFEST_FILENAME} file")
    p.add_argument("--id", required=True, metavar="CLAIM_ID", help="evidence claim ID to inspect")
    p.add_argument("--json", action="store_true", help="machine-readable claim detail")
    p.set_defaults(func=cmd_claim)

    p = sub.add_parser("lineage", help="trace a claim's recorded upstream provenance (no artifact checks)")
    p.add_argument("path", type=Path, help=f"experiment folder or {MANIFEST_FILENAME} file")
    p.add_argument("--id", required=True, metavar="CLAIM_ID", help="evidence claim ID to trace")
    p.add_argument("--json", action="store_true", help="machine-readable recorded dependency graph")
    p.set_defaults(func=cmd_lineage)

    p = sub.add_parser("impact", help="find claims potentially affected by a recorded object (no artifact checks)")
    p.add_argument("path", type=Path, help=f"experiment folder or {MANIFEST_FILENAME} file")
    p.add_argument("--id", required=True, metavar="OBJECT_ID", help="recorded run, result or artifact ID")
    p.add_argument("--json", action="store_true", help="machine-readable potential impact and example paths")
    p.set_defaults(func=cmd_impact)

    p = sub.add_parser("coverage", help="review structural claim support/contradiction and provenance gaps (no artifact checks)")
    p.add_argument("path", type=Path, help=f"experiment folder or {MANIFEST_FILENAME} file")
    p.add_argument("--json", action="store_true", help="complete machine-readable review queue")
    p.add_argument("--only-flagged", action="store_true", help="show only claims with structural review flags")
    p.set_defaults(func=cmd_coverage)

    p = sub.add_parser("coverage-gate", help="gate selected structural coverage flags in one recorded manifest (no artifact checks)")
    p.add_argument("path", type=Path, help=f"experiment folder or {MANIFEST_FILENAME} file")
    p.add_argument("--flag", action="append", choices=(
        "no_resolved_support", "contradictions_recorded", "dual_stance",
        "unresolved_direct_reference", "unsupported_direct_kind",
        "unresolved_provenance", "invalid_provenance_kind", "provenance_cycle",
    ), help="review flag that triggers the gate (repeatable; default: any flag)")
    p.add_argument("--json", action="store_true", help="complete machine-readable gate and unfiltered coverage")
    p.set_defaults(func=cmd_coverage_gate)

    p = sub.add_parser("fingerprint", help="hash normalized recorded assertions (not artifact contents)")
    p.add_argument("path", type=Path, help=f"experiment folder or {MANIFEST_FILENAME} file")
    p.add_argument("--json", action="store_true", help="complete machine-readable manifest fingerprint")
    p.add_argument("--expect", metavar="SHA256", help="CI check against an exact 64-hex recorded v1 fingerprint (0 match, 1 mismatch, 2 invalid)")
    p.set_defaults(func=cmd_fingerprint)

    p = sub.add_parser("fingerprint-compare", help="CI-check recorded semantic equality across two manifests (0 same, 1 different, 2 invalid)")
    p.add_argument("before", type=Path, help="baseline experiment folder or manifest JSON file")
    p.add_argument("after", type=Path, help="candidate experiment folder or manifest JSON file")
    p.add_argument("--json", action="store_true", help="machine-readable full digests and equality verdict")
    p.add_argument("--explain", action="store_true", help="show deterministic recorded changes, with relationship notes redacted by default")
    p.add_argument("--show-notes", action="store_true", help="requires --explain; include raw relationship notes in output (may reveal sensitive content)")
    p.set_defaults(func=cmd_fingerprint_compare)

    p = sub.add_parser("coverage-diff", help="compare structural claim review flags between recorded manifests (no artifact checks)")
    p.add_argument("before", type=Path, help="earlier experiment folder or manifest JSON file")
    p.add_argument("after", type=Path, help="later experiment folder or manifest JSON file")
    p.add_argument("--json", action="store_true", help="complete machine-readable coverage flag changes")
    p.add_argument("--only-regressions", action="store_true",
                   help="show only new review flags on surviving claims or newly added flagged claims")
    p.set_defaults(func=cmd_coverage_diff)

    p = sub.add_parser("review-diff", help="unify recorded claim impact and structural flag changes (no artifact checks)")
    p.add_argument("before", type=Path, help="earlier experiment folder or manifest JSON file")
    p.add_argument("after", type=Path, help="later experiment folder or manifest JSON file")
    p.add_argument("--json", action="store_true", help="complete machine-readable review queue")
    p.add_argument("--only-new-flags", action="store_true", help="show only candidates introducing structural review flags")
    p.set_defaults(func=cmd_review_diff)

    p = sub.add_parser("impact-diff", help="compare recorded manifest snapshots and find claims for review (no artifact checks)")
    p.add_argument("before", type=Path, help="earlier experiment folder or manifest JSON file")
    p.add_argument("after", type=Path, help="later experiment folder or manifest JSON file")
    p.add_argument("--json", action="store_true", help="complete machine-readable change impact")
    p.set_defaults(func=cmd_impact_diff)

    p = sub.add_parser("review-gate", help="CI policy gate for recorded review changes (no artifact checks)")
    p.add_argument("before", type=Path, help="earlier experiment folder or manifest JSON file")
    p.add_argument("after", type=Path, help="later experiment folder or manifest JSON file")
    p.add_argument("--policy", choices=("new-flags", "new-flags-or-removals", "any-review"),
                   default="new-flags", help="review condition triggering exit 1 (default: new-flags)")
    p.add_argument("--json", action="store_true", help="complete machine-readable gate and unfiltered review")
    p.set_defaults(func=cmd_review_gate)

    p = sub.add_parser("result", help="add, edit or remove a recorded result")
    p.add_argument("action", choices=("add", "edit", "remove"))
    p.add_argument("path", type=Path)
    p.add_argument("--id")
    p.add_argument("--file", help="one JSON object; see docs/authoring-files.md")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_result)

    p = sub.add_parser("evidence", help="add, edit or remove a claim/evidence entry")
    p.add_argument("action", choices=("add", "edit", "remove"))
    p.add_argument("path", type=Path)
    p.add_argument("--id")
    p.add_argument("--file", help="one JSON object; see docs/authoring-files.md")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_evidence)

    p = sub.add_parser("artifact", help="edit metadata or remove a recorded artifact")
    p.add_argument("action", choices=("edit", "remove"))
    p.add_argument("path", type=Path)
    p.add_argument("--id")
    p.add_argument("--file", help="one JSON object; see docs/authoring-files.md")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_artifact)

    p = sub.add_parser("relationship", help="add or remove a recorded relationship")
    p.add_argument("action", choices=("add", "remove"))
    p.add_argument("path", type=Path)
    p.add_argument("--file", help="one JSON object; see docs/authoring-files.md")
    p.add_argument("--index", type=int)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_relationship)

    p = sub.add_parser("init", help=f"scan a folder and write a starter {MANIFEST_FILENAME}")
    p.add_argument("path", type=Path, nargs="?", default=Path("."), help="experiment folder (default: current)")
    p.add_argument("--adapter", default="generic", help="how to read the folder (default: generic)")
    p.add_argument("--name", help="project name (default: folder name)")
    p.add_argument("--id", help="project id (default: derived from name)")
    p.add_argument("--force", action="store_true", help=f"overwrite an existing {MANIFEST_FILENAME}")
    p.add_argument(
        "--include-histograms",
        action="store_true",
        help=(
            "also record legacy TensorBoard histograms as schema 0.7 results "
            "(opt-in; implies schema 0.7 unless a media or tensor flag selects a "
            "later schema)"
        ),
    )
    p.add_argument(
        "--include-images",
        action="store_true",
        help=(
            "also record legacy TensorBoard image summaries as schema 0.8 results "
            "(opt-in; implies schema 0.8 unless a tensor or TF2 media flag selects a "
            "later schema)"
        ),
    )
    p.add_argument(
        "--include-audio",
        action="store_true",
        help=(
            "also record legacy TensorBoard audio summaries as schema 0.8 results "
            "(opt-in; implies schema 0.8 unless a tensor or TF2 media flag selects a "
            "later schema)"
        ),
    )
    p.add_argument(
        "--include-tf2-media",
        action="store_true",
        help=(
            "also record TensorFlow 2 image and audio string summaries as schema 0.10 "
            "results (opt-in; implies schema 0.10)"
        ),
    )
    p.add_argument(
        "--include-tf2-histograms",
        action="store_true",
        help=(
            "also record TensorFlow 2 [k,3] histograms as schema 0.11 results "
            "(opt-in; implies schema 0.11)"
        ),
    )
    p.add_argument(
        "--include-tensors",
        action="store_true",
        help=(
            "also record numeric TensorBoard tensors as schema 0.9 results "
            "(opt-in; implies schema 0.9 unless --include-tf2-media selects 0.10)"
        ),
    )
    p.add_argument(
        "--include-strings",
        action="store_true",
        help=(
            "also record scalar-shaped TensorBoard string tensors as schema 0.6 results "
            "(opt-in; default output stays schema 0.5)"
        ),
    )
    p.add_argument(
        "--tags",
        action="append",
        metavar="PATTERN",
        help=(
            "only record scalars whose tag matches this full-string glob (repeatable; "
            "quote it so the shell does not expand the wildcards; * and ? wildcards, "
            "backslash escapes, no [...] classes, unlike --volatile; "
            "use --tags=PATTERN for a leading '-')"
        ),
    )
    p.add_argument(
        "--volatile",
        action="append",
        metavar="GLOB",
        help="mark matching files existence-only, e.g. '*.log' (repeatable; * also crosses folders)",
    )
    p.set_defaults(func=cmd_init)

    p = sub.add_parser(
        "migrate",
        help="declare an existing manifest as schema 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.10 or 0.11 without changing any recorded value (explicit, no inference)",
    )
    p.add_argument("path", type=Path, help=f"experiment folder (containing {MANIFEST_FILENAME}) or manifest file")
    p.add_argument("--to", required=True, metavar="VERSION", help="target schema version (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.10 or 0.11)")
    p.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    p.add_argument("--json", action="store_true", help="machine-readable result")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser(
        "conditions",
        help="set or clear recorded run conditions on a schema 0.2 or later manifest from a local JSON patch (no condition values on the command line)",
    )
    p.add_argument("path", type=Path, help=f"experiment folder (containing {MANIFEST_FILENAME}) or manifest file")
    p.add_argument("--run", required=True, metavar="RUN_ID", help="exactly one run ID whose conditions are edited")
    p.add_argument("--patch", action="append", metavar="FILE",
                   help="one strict JSON conditions patch file, or - for stdin (at most one)")
    p.add_argument("--clear", action="append", metavar="FIELD",
                   help="remove one recorded condition field (repeatable)")
    p.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    p.add_argument("--json", action="store_true", help="machine-readable result")
    p.set_defaults(func=cmd_conditions)

    def add_edit_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("path", type=Path, help=f"experiment folder (containing {MANIFEST_FILENAME}) or manifest file")

    p = sub.add_parser("capture", help="record a run's git source revision and environment snapshot")
    p.add_argument("path", type=Path)
    p.add_argument("--run", required=True, metavar="RUN_ID")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_capture)

    p = sub.add_parser("rehash", help="accept intended changes: re-record digests (and result values) of chosen files")
    add_edit_args(p)
    p.add_argument(
        "targets",
        nargs="*",
        metavar="TARGET",
        help=(
            "artifact ID, run ID (all its files), file/folder path or glob; ambiguous targets are refused. "
            "Choose explicitly with id:ID, run:ID, path:PATH, folder:PATH or glob:PATTERN"
        ),
    )
    p.add_argument("--all", action="store_true", help="re-record every artifact in the manifest")
    vol = p.add_mutually_exclusive_group()
    vol.add_argument(
        "--volatile",
        dest="volatile",
        action="store_const",
        const=True,
        help="mark the targets volatile: from now on only check that they exist",
    )
    vol.add_argument(
        "--no-volatile",
        dest="volatile",
        action="store_const",
        const=False,
        help="make volatile targets hash-checked again",
    )
    p.add_argument(
        "--directory",
        action="store_true",
        help="re-record exactly one directory artifact by exact ID (Linux/ext4 only; text output)",
    )
    p.add_argument(
        "--refresh-locators",
        action="store_true",
        help=(
            "verify recorded schema-0.5 TensorBoard scalar locators and re-record only "
            "artifact bytes; explicit exact IDs; values must already match"
        ),
    )
    p.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    p.set_defaults(func=cmd_rehash, volatile=None)

    p = sub.add_parser("add", help="track new files without re-running init")
    add_edit_args(p)
    p.add_argument(
        "files",
        nargs="+",
        type=Path,
        metavar="FILE",
        help="file or folder to add, relative to the current folder; a folder adds every untracked file inside",
    )
    p.add_argument(
        "--id",
        action=_CountingIdAction,
        help="artifact ID (one file only; default: derived from the path)",
    )
    p.add_argument("--kind", help="free-form label, e.g. report, log, checkpoint")
    p.add_argument("--run", help="run that produced the file (default: inferred from neighbouring files)")
    p.add_argument("--no-run", action="store_true", help="do not attach the file to any run")
    p.add_argument("--volatile", action="store_true", help="check only that the file exists, not its bytes")
    p.add_argument(
        "--directory",
        action="store_true",
        help="record exactly one folder as a directory artifact (Linux/ext4 only; text output)",
    )
    p.add_argument("--dry-run", action="store_true", help="show what would be added, write nothing")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("schema", help="print the manifest JSON Schema")
    p.add_argument("--output", "-o", type=Path, help="write to a file instead of stdout")
    p.set_defaults(func=cmd_schema)

    p = sub.add_parser("adapters", help="list installed adapters")
    p.add_argument("--json", action="store_true", help="machine-readable adapter list")
    p.set_defaults(func=cmd_adapters)
    return parser


def _comparability_main(raw: list[str]) -> int:
    """Sanitized argument boundary for recognized comparability invocations.

    ``--json`` is taken from the raw argv, so a grammar failure still emits the
    requested document kind. Parser diagnostics are captured and discarded
    before argparse can write them: a recognized failure yields exactly the
    fixed INVALID_ARGUMENTS document, never a diagnostic or a submitted token.
    ``--help`` keeps its normal stdout behavior and exit code 0.
    """
    preflight = "--preflight" in raw
    if preflight and "--json" in raw and "--help" not in raw:
        print(run_comparability.PREFLIGHT_JSON_REFUSAL)
        return EXIT_INVALID
    as_json = "--json" in raw and not preflight
    parser = build_parser()
    captured = io.StringIO()
    try:
        with redirect_stderr(captured):
            args = parser.parse_args(raw)
    except SystemExit as exc:
        code = int(exc.code or 0)
        if code == 0:
            return 0
        if preflight:
            print(run_comparability.PREFLIGHT_INVALID_ARGUMENTS)
            return EXIT_INVALID
        outcome = run_comparability.invalid_arguments()
        if as_json:
            print(json.dumps(outcome.document, indent=2, ensure_ascii=True, allow_nan=False))
        else:
            print(outcome.text)
        return 2
    return args.func(args)


def _migrate_main(raw: list[str]) -> int:
    """Sanitized argument boundary for recognized migrate invocations.

    Mirrors ``_comparability_main``: ``--json`` comes from the raw argv so a
    grammar failure still emits the requested document kind, and parser
    diagnostics are captured and discarded. A recognized failure yields exactly
    the fixed INVALID_ARGUMENTS document, never a diagnostic or a submitted
    token. ``--help`` keeps its normal stdout behavior and exit code 0.
    """
    as_json = "--json" in raw
    parser = build_parser()
    captured = io.StringIO()
    try:
        with redirect_stderr(captured):
            args = parser.parse_args(raw)
    except SystemExit as exc:
        code = int(exc.code or 0)
        if code == 0:
            return 0
        return _migrate_failure("INVALID_ARGUMENTS", as_json)
    return args.func(args)


def _conditions_main(raw: list[str]) -> int:
    """Sanitized argument boundary for recognized conditions invocations.

    Mirrors ``_migrate_main``: ``--json`` comes from the raw argv so a grammar
    failure still emits the requested document kind, and parser diagnostics are
    captured and discarded. A recognized failure yields exactly the fixed
    INVALID_ARGUMENTS document, never a diagnostic or a submitted token.
    ``--help`` keeps its normal stdout behavior and exit code 0.
    """
    as_json = "--json" in raw
    parser = build_parser()
    captured = io.StringIO()
    try:
        with redirect_stderr(captured):
            args = parser.parse_args(raw)
    except SystemExit as exc:
        code = int(exc.code or 0)
        if code == 0:
            return 0
        return _conditions_failure("INVALID_ARGUMENTS", as_json)
    return args.func(args)


def _broken_pipe() -> int:
    """Leave quietly when the reader closes the pipe.

    Redirect stdout to the null device before returning so the interpreter's
    final flush cannot raise a second BrokenPipeError.
    """
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        os.close(devnull)
    except (OSError, ValueError):
        pass
    try:
        sys.stdout.close()
    except (OSError, ValueError):
        pass
    return EXIT_FINDINGS


def main(argv: Optional[Sequence[str]] = None) -> int:
    if argv is None and hasattr(signal, "SIGPIPE"):
        try:
            signal.signal(signal.SIGPIPE, signal.SIG_DFL)
        except (OSError, ValueError):
            pass
    try:
        return _main(argv)
    except BrokenPipeError:
        return _broken_pipe()


def _main(argv: Optional[Sequence[str]] = None) -> int:
    # Claims and paths may contain any Unicode; never crash on a legacy console.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] == "comparability":
        return _comparability_main(raw)
    if raw and raw[0] == "migrate":
        return _migrate_main(raw)
    if raw and raw[0] == "conditions":
        return _conditions_main(raw)
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse exits 2 on bad usage, 0 on --help
        return int(exc.code or 0)
    if getattr(args, "redact_manifest_path", False) and not args.json:
        print("ERROR: --redact-manifest-path requires --json", file=sys.stderr)
        return EXIT_INVALID
    if getattr(args, "summary_only", False) and not args.json:
        print("ERROR: --summary-only requires --json", file=sys.stderr)
        return EXIT_INVALID
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
