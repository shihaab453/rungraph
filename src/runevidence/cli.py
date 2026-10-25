"""Command line interface.

Exit codes (stable, safe to use in CI):
  0  no errors (warnings are allowed unless --strict)
  1  the evidence has problems: errors found, or warnings with --strict
  2  could not check at all: manifest missing, unreadable or schema-invalid,
     bad arguments, or init refused to overwrite or could not read the folder;
     for rehash and add: the change could not be made, and nothing was written

validate, inspect and adapters never write. schema -o writes a schema file.
init, rehash and add write the manifest; init --force also keeps a backup.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from pydantic import ValidationError

from . import __version__
from .adapters import AdapterError, available_adapters, get_adapter
from .adapters.generic import slug
from .checks import EXIT_INVALID, EXIT_OK, Report, run_checks
from .edit import EditError, glob_match, plan_add, plan_rehash, select_artifacts
from .manifest import ManifestLoadError, load_manifest, load_raw, save_manifest, save_raw
from .models import BACKUP_FILENAME, MANIFEST_FILENAME, Manifest, json_schema
from .report import format_findings, format_overview, to_json


def _load_or_report(target: Path, as_json: bool):
    try:
        return load_manifest(target)
    except ManifestLoadError as exc:
        if as_json:
            report = Report(manifest_path=Path(target), findings=exc.findings)
            data = json.loads(to_json(report))
            data["exit_code"] = EXIT_INVALID
            print(json.dumps(data, indent=2))
        else:
            for f in exc.findings:
                print(f"ERROR {f.code.value}: {f.message}")
                if f.hint:
                    print(f"      hint: {f.hint}")
            print(f"INVALID: manifest could not be checked (exit {EXIT_INVALID})")
        return None


def cmd_validate(args: argparse.Namespace) -> int:
    loaded = _load_or_report(args.path, args.json)
    if loaded is None:
        return EXIT_INVALID
    report = run_checks(loaded)
    if args.json:
        print(to_json(report, args.strict))
    else:
        print(f"Checking {loaded.path}")
        print(format_findings(report, args.strict))
    return report.exit_code(args.strict)


def cmd_inspect(args: argparse.Namespace) -> int:
    loaded = _load_or_report(args.path, args.json)
    if loaded is None:
        return EXIT_INVALID
    report = run_checks(loaded, include_untracked=True)
    if args.json:
        print(to_json(report, args.strict, manifest=loaded.manifest))
    else:
        print(f"Manifest: {loaded.path}")
        print(format_overview(loaded.manifest))
        print("\nFindings:")
        print(format_findings(report, args.strict))
    return report.exit_code(args.strict)


def cmd_init(args: argparse.Namespace) -> int:
    root: Path = args.path.resolve()
    if not root.is_dir():
        print(f"ERROR: {root} is not a folder")
        return EXIT_INVALID
    out = root / MANIFEST_FILENAME
    if out.exists() and not args.force:
        print(f"ERROR: {out} already exists. Use --force to overwrite it.")
        return EXIT_INVALID
    try:
        adapter = get_adapter(args.adapter)
    except KeyError as exc:
        print(f"ERROR: {exc.args[0]}")
        return EXIT_INVALID
    name = args.name or root.name
    try:
        discovery = adapter.discover(root)
        manifest = adapter.extract(root, discovery, project_id=args.id or slug(name).lower(), project_name=name)
    except AdapterError as exc:
        print(f"ERROR: adapter {adapter.name!r} cannot read {root}: {exc}")
        return EXIT_INVALID
    if args.volatile:
        manifest = _mark_volatile(manifest, args.volatile)
    if out.exists():
        _backup_existing(out, manifest)
    save_manifest(manifest, out)
    volatile = sum(a.volatile for a in manifest.artifacts)
    print(
        f"Wrote {out}\n"
        f"  {len(manifest.runs)} runs, {len(manifest.artifacts)} artifacts"
        + (f" ({volatile} volatile)" if volatile else "")
        + f" (adapter: {adapter.name})\n"
        "Next: add results and evidence claims, then run:\n"
        f"  runevidence validate {args.path}"
    )
    return EXIT_OK


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


def _short(digest: Optional[str]) -> str:
    return digest[:12] + "..." if digest else "none"


def _edit_error(exc: Exception) -> int:
    print(f"ERROR: {exc}\nNothing was written.")
    return EXIT_INVALID


def _finish_edit(args: argparse.Namespace, raw: dict, path: Path, summary: str) -> int:
    if args.dry_run:
        print(summary)
        print("Dry run: nothing was written.")
        return EXIT_OK
    try:
        save_raw(raw, path)
    except OSError as exc:
        return _edit_error(exc)
    print(summary)
    print(f"Wrote {path}\nNext: runevidence validate {args.path}")
    return EXIT_OK


def cmd_rehash(args: argparse.Namespace) -> int:
    if args.targets and args.all:
        print("ERROR: name targets or pass --all, not both")
        return EXIT_INVALID
    if not args.targets and not args.all:
        print("ERROR: name what to re-record (artifact IDs, run IDs, paths or globs), or pass --all for everything")
        return EXIT_INVALID
    if args.all and args.volatile is not None:
        print("ERROR: --volatile and --no-volatile need explicit targets, not --all")
        return EXIT_INVALID
    loaded = _load_or_report(args.path, False)
    if loaded is None:
        return EXIT_INVALID
    raw = load_raw(loaded.path)
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
        return _finish_edit(args, raw, loaded.path, summary)
    print(summary)
    return EXIT_OK


def cmd_add(args: argparse.Namespace) -> int:
    if args.run and args.no_run:
        print("ERROR: use --run or --no-run, not both")
        return EXIT_INVALID
    loaded = _load_or_report(args.path, False)
    if loaded is None:
        return EXIT_INVALID
    raw = load_raw(loaded.path)
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
    return _finish_edit(args, raw, loaded.path, summary)


def _backup_existing(out: Path, new: Manifest) -> None:
    """``init --force`` regenerates from disk, which drops everything written
    by hand. Keep the old file and say exactly what the new one lacks."""
    backup = out.with_name(BACKUP_FILENAME)
    backup.write_bytes(out.read_bytes())
    print(f"Saved the previous manifest to {backup}")
    try:
        old = load_manifest(out).manifest
    except ManifestLoadError:
        return
    lost, renamed = _lost_on_regenerate(old, new)
    if lost:
        print(f"  WARNING: the new manifest does not contain its {', '.join(lost)}; copy them back from the backup.")
    if renamed:
        examples = ", ".join(f"{a!r} is now {b!r}" for a, b in renamed[:3])
        print(f"  NOTE: {_count(len(renamed), 'artifact')} got a new ID ({examples}); update references when copying back.")


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
    for name, cls in sorted(available_adapters().items()):
        print(f"{name:<12} {cls.description}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="runevidence",
        description=(
            "Check that the evidence in an experiment folder still holds. Offline. "
            "init, rehash and add write the manifest; schema -o exports a schema."
        ),
        epilog="Exit codes: 0 ok, 1 evidence problems found, 2 could not check (bad manifest or arguments).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    def add_check_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("path", type=Path, help=f"experiment folder (containing {MANIFEST_FILENAME}) or manifest file")
        p.add_argument("--json", action="store_true", help="machine-readable output")
        p.add_argument("--strict", action="store_true", help="treat warnings as failures (exit 1)")

    p = sub.add_parser("validate", help="check files, hashes and evidence links; print problems only")
    add_check_args(p)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("inspect", help="summarize the manifest, then run every check (incl. untracked files)")
    add_check_args(p)
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("init", help=f"scan a folder and write a starter {MANIFEST_FILENAME}")
    p.add_argument("path", type=Path, nargs="?", default=Path("."), help="experiment folder (default: current)")
    p.add_argument("--adapter", default="generic", help="how to read the folder (default: generic)")
    p.add_argument("--name", help="project name (default: folder name)")
    p.add_argument("--id", help="project id (default: derived from name)")
    p.add_argument("--force", action="store_true", help=f"overwrite an existing {MANIFEST_FILENAME}")
    p.add_argument(
        "--volatile",
        action="append",
        metavar="GLOB",
        help="mark matching files existence-only, e.g. '*.log' (repeatable; * also crosses folders)",
    )
    p.set_defaults(func=cmd_init)

    def add_edit_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("path", type=Path, help=f"experiment folder (containing {MANIFEST_FILENAME}) or manifest file")

    p = sub.add_parser("rehash", help="accept intended changes: re-record digests (and result values) of chosen files")
    add_edit_args(p)
    p.add_argument(
        "targets",
        nargs="*",
        metavar="TARGET",
        help="artifact ID, run ID (all its files), file or folder path, or glob such as 'runs/*/train.log'",
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
    p.add_argument("--id", help="artifact ID (one file only; default: derived from the path)")
    p.add_argument("--kind", help="free-form label, e.g. report, log, checkpoint")
    p.add_argument("--run", help="run that produced the file (default: inferred from neighbouring files)")
    p.add_argument("--no-run", action="store_true", help="do not attach the file to any run")
    p.add_argument("--volatile", action="store_true", help="check only that the file exists, not its bytes")
    p.add_argument("--dry-run", action="store_true", help="show what would be added, write nothing")
    p.set_defaults(func=cmd_add)

    p = sub.add_parser("schema", help="print the manifest JSON Schema")
    p.add_argument("--output", "-o", type=Path, help="write to a file instead of stdout")
    p.set_defaults(func=cmd_schema)

    p = sub.add_parser("adapters", help="list installed adapters")
    p.set_defaults(func=cmd_adapters)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    # Claims and paths may contain any Unicode; never crash on a legacy console.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse exits 2 on bad usage, 0 on --help
        return int(exc.code or 0)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
