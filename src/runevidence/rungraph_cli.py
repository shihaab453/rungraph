"""The ``rungraph`` command: read-only workspace composition."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

from .workspace import (
    WORKSPACE_FILENAME,
    WORKSPACE_FORMAT,
    WORKSPACE_VERSION,
    WorkspaceError,
    discover,
    load_workspace,
)
from .workspace_consumers import (
    compare_data,
    impact_data,
    leaderboard_data,
    matrix_data,
    report_data,
)
from .workspace_graph import build_graph, claim_states, run_member_checks

EXIT_OK = 0
EXIT_EVIDENCE = 1
EXIT_INVALID = 2


def _print_json(data: dict) -> None:
    print(json.dumps(data, indent=2, sort_keys=False))


def _error(command: str, code: str, message: str) -> int:
    _print_json(
        {
            "format": "rungraph-error",
            "version": "0.1",
            "command": command,
            "error": {"code": code, "message": message},
            "exit_code": EXIT_INVALID,
        }
    )
    return EXIT_INVALID


def cmd_discover(args: argparse.Namespace) -> int:
    result = discover(Path(args.root))
    data = {
        "format": "rungraph-discover",
        "version": "0.1",
        "root": str(result.root),
        "members": [
            {
                "path": item.path,
                "id": item.id,
                "stable": item.stable,
                "loads": item.loads,
                "schema_version": item.schema_version,
                "duplicate": item.duplicate,
                "error": item.error,
            }
            for item in result.members
        ],
        "errors": result.errors,
        "exit_code": result.exit_code,
    }
    if args.json:
        _print_json(data)
    else:
        for item in result.members:
            print(f"{item.path}  {item.id}" + ("" if item.loads else "  (unreadable)"))
        for error in result.errors:
            print(f"ERROR {error}")
    return result.exit_code


def cmd_init(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    result = discover(root)
    if result.exit_code != 0 and not args.force:
        return _error("init", "discovery", "; ".join(result.errors) or "no manifests found")
    if not result.members:
        return _error("init", "discovery", "no manifests found")
    path = root / WORKSPACE_FILENAME
    if path.exists() and not args.force:
        return _error("init", "exists", f"{WORKSPACE_FILENAME} already exists; use --force")
    document = {
        "format": WORKSPACE_FORMAT,
        "version": WORKSPACE_VERSION,
        "id": args.id or _slug(root.name) or "workspace",
        "name": args.name or root.name or "workspace",
        "members": [
            {"id": item.id, "path": item.path}
            for item in result.members
            if not item.duplicate
        ],
    }
    if args.dry_run:
        if args.json:
            _print_json({"format": "rungraph-init", "version": "0.1", "path": str(path), "members": document["members"], "backup": None, "written": False, "exit_code": 0})
        else:
            print(f"Would write {path} with {len(document['members'])} members.")
        return EXIT_OK
    backup = None
    if path.exists():
        backup = path.with_name(path.name + ".bak")
        index = 2
        while backup.exists():
            backup = path.with_name(f"{path.name}.bak.{index}")
            index += 1
        path.replace(backup)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    data = {
        "format": "rungraph-init",
        "version": "0.1",
        "path": str(path),
        "members": document["members"],
        "backup": str(backup) if backup else None,
        "written": True,
        "exit_code": 0,
    }
    if args.json:
        _print_json(data)
    else:
        print(f"Wrote {path} ({len(document['members'])} members).")
    return EXIT_OK


def _loaded(args: argparse.Namespace):
    loaded = load_workspace(Path(args.root))
    graph = build_graph(loaded)
    checks = run_member_checks(loaded, directory_artifacts=getattr(args, "directory_artifacts", False))
    states = claim_states(loaded, graph, checks["findings"])
    return loaded, graph, checks, states


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        loaded, graph, checks, states = _loaded(args)
    except WorkspaceError as exc:
        return _error("validate", "workspace", str(exc))
    data = report_data(loaded, graph, states, checks, directory_artifacts=args.directory_artifacts)
    data["format"] = "rungraph-validate"
    if args.json:
        _print_json(data)
    else:
        for member in data["members"]:
            print(f"{member['id']} ({member['path']}): {member['counts']['error']} errors, {member['counts']['warning']} warnings")
            if member["load_error"]:
                print(f"  ERROR {member['load_error']}")
        for claim in data["claims"]:
            print(f"claim {claim['id']}: {', '.join(claim['labels'])}")
        print(f"{'FAIL' if data['exit_code'] else 'OK'}: exit {data['exit_code']}")
    if args.strict and data["exit_code"] == 0:
        warnings = sum(member["counts"]["warning"] for member in data["members"])
        if warnings:
            return EXIT_EVIDENCE
    return data["exit_code"]


def cmd_graph(args: argparse.Namespace) -> int:
    try:
        loaded, graph, checks, states = _loaded(args)
    except WorkspaceError as exc:
        return _error("graph", "workspace", str(exc))
    if args.format == "dot":
        print("digraph rungraph {")
        for edge in graph.edges:
            print(f'  "{edge.source}" -> "{edge.target}" [label="{edge.relation}"];')
        print("}")
        return EXIT_OK
    data = {
        "format": "rungraph-graph",
        "version": "0.1",
        "nodes": [
            {"id": node.id, "kind": node.kind, "member": node.member, "status": node.status, "label": node.label}
            for node in graph.nodes.values()
        ],
        "edges": [
            {"source": edge.source, "target": edge.target, "relation": edge.relation, "origin": edge.origin}
            for edge in graph.edges
        ],
        "context": graph.context,
        "cycles": graph.cycles,
        "unresolved": graph.unresolved,
        "exit_code": EXIT_EVIDENCE if args.fail_on_unresolved and graph.unresolved else EXIT_OK,
    }
    _print_json(data)
    return data["exit_code"]


def cmd_report(args: argparse.Namespace) -> int:
    try:
        loaded, graph, checks, states = _loaded(args)
    except WorkspaceError as exc:
        return _error("report", "workspace", str(exc))
    data = report_data(loaded, graph, states, checks, directory_artifacts=args.directory_artifacts)
    if args.json:
        _print_json(data)
    else:
        print("Current evidence:")
        for member in data["members"]:
            print(f"  {member['id']}: {member['counts']['error']} errors, {member['counts']['warning']} warnings")
        print("Claims:")
        for claim in data["claims"]:
            print(f"  {claim['id']}: {', '.join(claim['labels'])}")
        print("Impact:")
        for item in data["impact"]:
            print(f"  {item['code']} {item['node']}: {', '.join(item['claims']) or 'none'}")
        print(f"exit {data['exit_code']}")
    return data["exit_code"]


def cmd_impact(args: argparse.Namespace) -> int:
    try:
        loaded, graph, checks, states = _loaded(args)
    except WorkspaceError as exc:
        return _error("impact", "workspace", str(exc))
    data = impact_data(loaded, graph, states, args.source)
    _print_json(data)
    return data["exit_code"]


def cmd_compare(args: argparse.Namespace) -> int:
    try:
        loaded = load_workspace(Path(args.root))
    except WorkspaceError as exc:
        return _error("compare", "workspace", str(exc))
    data = compare_data(loaded, args.metric, args.run)
    _print_json(data)
    return data["exit_code"]


def cmd_matrix(args: argparse.Namespace) -> int:
    try:
        loaded = load_workspace(Path(args.root))
    except WorkspaceError as exc:
        return _error("matrix", "workspace", str(exc))
    _print_json(matrix_data(loaded))
    return EXIT_OK


def cmd_leaderboard(args: argparse.Namespace) -> int:
    try:
        loaded = load_workspace(Path(args.root))
    except WorkspaceError as exc:
        return _error("leaderboard", "workspace", str(exc))
    data = leaderboard_data(loaded, args.metric, args.direction, args.top)
    _print_json(data)
    return data["exit_code"]


def _slug(text: str) -> str:
    out = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in text).strip("-.")
    return out or "workspace"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rungraph", description="Offline workspace composition over RunEvidence manifests.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("discover", help="list manifests under a root without writing")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("init", help="write rungraph.json from discovery")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--id")
    p.add_argument("--name")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("validate", help="validate every member and the workspace layer")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--json", action="store_true")
    p.add_argument("--strict", action="store_true")
    p.add_argument("--directory-artifacts", action="store_true")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("graph", help="print the workspace graph")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--format", choices=("json", "dot"), default="json")
    p.add_argument("--fail-on-unresolved", action="store_true")
    p.set_defaults(func=cmd_graph)

    p = sub.add_parser("report", help="consolidated evidence and claim report")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--json", action="store_true")
    p.add_argument("--strict", action="store_true")
    p.add_argument("--directory-artifacts", action="store_true")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("impact", help="claims affected by one workspace node")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--from", dest="source", required=True)
    p.set_defaults(func=cmd_impact)

    p = sub.add_parser("compare", help="recorded values for one metric across members")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--metric", required=True)
    p.add_argument("--run")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("matrix", help="recorded values as a member x metric matrix")
    p.add_argument("root", nargs="?", default=".")
    p.set_defaults(func=cmd_matrix)

    p = sub.add_parser("leaderboard", help="recorded numeric values ranked across members")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--metric", required=True)
    p.add_argument("--direction", choices=("max", "min"), required=True)
    p.add_argument("--top", type=int, default=10)
    p.set_defaults(func=cmd_leaderboard)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except WorkspaceError as exc:
        return _error(args.command, "workspace", str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
