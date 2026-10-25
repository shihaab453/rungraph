"""Command line interface.

Exit codes (stable, safe to use in CI):
  0  no errors (warnings are allowed unless --strict)
  1  the evidence has problems: errors found, or warnings with --strict
  2  could not check at all: manifest missing, unreadable or schema-invalid,
     bad arguments, or init refused to overwrite
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import __version__
from .adapters import available_adapters, get_adapter
from .adapters.generic import slug
from .checks import EXIT_INVALID, EXIT_OK, Report, run_checks
from .manifest import ManifestLoadError, load_manifest, save_manifest
from .models import MANIFEST_FILENAME, json_schema
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
    discovery = adapter.discover(root)
    manifest = adapter.extract(root, discovery, project_id=args.id or slug(name), project_name=name)
    save_manifest(manifest, out)
    print(
        f"Wrote {out}\n"
        f"  {len(manifest.runs)} runs, {len(manifest.artifacts)} artifacts (adapter: {adapter.name})\n"
        "Next: add results and evidence claims, then run:\n"
        f"  runevidence validate {args.path}"
    )
    return EXIT_OK


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
        description="Check that the evidence in an experiment folder still holds. Offline and read-only.",
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
    p.set_defaults(func=cmd_init)

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
