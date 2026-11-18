"""The ``rungraph`` command: read-only workspace composition."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Optional, Sequence

from .workspace import (
    WORKSPACE_FILENAME,
    WORKSPACE_FORMAT,
    WORKSPACE_VERSION,
    Workspace,
    WorkspaceError,
    apply_patch,
    discover,
    load_workspace,
    save_workspace,
    workspace_lock,
)
from .workspace_consumers import (
    compare_data,
    graph_data,
    impact_data,
    leaderboard_data,
    matrix_data,
    report_data,
    runs_data,
    stats_data,
)
from .workspace_db import check_workspace, export_workspace, import_workspace
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
    check_results = {}
    if args.check:
        from .manifest import load_manifest
        from .checks import run_checks

        for item in result.members:
            if not item.loads:
                continue
            try:
                report = run_checks(
                    load_manifest(result.root / item.path / item.manifest),
                    enable_directory_artifacts=args.directory_artifacts,
                )
            except Exception:
                continue
            check_results[item.path] = {
                "errors": sum(1 for finding in report.findings if finding.severity.value == "error"),
                "warnings": sum(1 for finding in report.findings if finding.severity.value != "error"),
            }
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
                "check": check_results.get(item.path),
            }
            for item in result.members
        ],
        "errors": result.errors,
        "exit_code": 1 if any(item["errors"] for item in check_results.values()) else result.exit_code,
    }
    exit_code = data["exit_code"]
    if args.json:
        _print_json(data)
    else:
        for item in result.members:
            print(f"{item.path}  {item.id}" + ("" if item.loads else "  (unreadable)"))
        for error in result.errors:
            print(f"ERROR {error}")
    return exit_code


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
            {"id": item.id, "path": item.path, "manifest": item.manifest}
            for item in result.members
            if not item.duplicate
        ],
    }
    try:
        workspace = Workspace.model_validate(document)
    except Exception as exc:
        return _error("init", "invalid", f"invalid workspace document: {exc}")
    if args.dry_run:
        if args.json:
            _print_json({"format": "rungraph-init", "version": "0.1", "path": str(path), "members": document["members"], "backup": None, "written": False, "exit_code": 0})
        else:
            print(f"Would write {path} with {len(document['members'])} members.")
        return EXIT_OK
    backup = None
    try:
        with workspace_lock(root):
            if path.exists():
                if not args.force:
                    return _error("init", "exists", f"{WORKSPACE_FILENAME} already exists; use --force")
                backup = path.with_name(path.name + ".bak")
                index = 2
                while backup.exists():
                    backup = path.with_name(f"{path.name}.bak.{index}")
                    index += 1
                shutil.copy2(path, backup)
                expected = path.read_bytes()
            else:
                expected = None
            # An atomic replace under the workspace lock: a failed write leaves
            # the previous document (and its copy) in place.
            save_workspace(workspace, path, expected)
    except WorkspaceError as exc:
        return _error("init", "workspace", str(exc))
    except OSError as exc:
        return _error("init", "workspace", f"cannot write {path}: {exc.strerror or exc}")
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
    states = claim_states(loaded, graph, checks["findings"], checks.get("failed", frozenset()))
    return loaded, graph, checks, states


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        loaded, graph, checks, states = _loaded(args)
    except WorkspaceError as exc:
        return _error("validate", "workspace", str(exc))
    data = report_data(
        loaded, graph, states, checks,
        directory_artifacts=args.directory_artifacts,
    )
    data["format"] = "rungraph-validate"
    if args.strict and data["exit_code"] == 0:
        warnings = sum(member["counts"]["warning"] for member in data["members"])
        if warnings:
            data["exit_code"] = EXIT_EVIDENCE
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
        return EXIT_EVIDENCE if args.fail_on_unresolved and graph.unresolved else EXIT_OK
    data = graph_data(loaded, graph, detail=args.detail)
    data["exit_code"] = EXIT_EVIDENCE if args.fail_on_unresolved and graph.unresolved else EXIT_OK
    _print_json(data)
    return data["exit_code"]


_REPORT_TEXT_FINDINGS = 10
_REPORT_TEXT_CLAIM = 200


def _report_text(value: object, limit: int = _REPORT_TEXT_CLAIM) -> str:
    if isinstance(value, (dict, list)):
        escaped = json.dumps(value, ensure_ascii=True)
    else:
        escaped = json.dumps(str(value), ensure_ascii=True)[1:-1]
    if len(escaped) > limit:
        escaped = escaped[:limit] + "..."
    return escaped


from .markdown_text import md_text as _md

def _markdown_report(data: dict, document) -> str:
    lines = [f"# {_md(document.name)} ({_md(document.id)})", ""]
    policy = data["check_policy"]
    lines.append(
        "**Directory artifacts:** "
        + ("enabled" if policy.get("directory_artifacts") else "disabled")
    )
    lines.append(f"**Exit:** {data['exit_code']}")
    lines += ["", "## Members", ""]
    if not data["members"]:
        lines.append("None.")
    for member in data["members"]:
        lines.append(f"### {_md(member['id'])} ({_md(member['path'])})")
        lines.append("")
        schema = member["schema_version"] or "unreadable"
        lines.append(f"- Schema: {_md(schema)}")
        lines.append(
            f"- {member['counts']['error']} errors, "
            f"{member['counts']['warning']} warnings"
        )
        if member["load_error"]:
            lines.append(f"- ERROR: {_md(member['load_error'])}")
        for finding in member["findings"][:_REPORT_TEXT_FINDINGS]:
            subject = f" [{_md(finding['subject'], 80)}]" if finding["subject"] else ""
            lines.append(
                f"- {finding['severity'].upper()} `{finding['code']}`{subject}: "
                f"{_md(finding['message'])}"
            )
        hidden = len(member["findings"]) - _REPORT_TEXT_FINDINGS
        if hidden > 0:
            lines.append(f"- and {hidden} more findings (see `--json`)")
        lines.append("")
    lines += ["## Claims", ""]
    if not data["claims"]:
        lines.append("None.")
    else:
        lines.append("| Claim | Owner | Labels | Text |")
        lines.append("| --- | --- | --- | --- |")
        for claim in data["claims"]:
            lines.append(
                f"| `{_md(claim['id'])}` | {_md(claim['member'] or 'workspace')} | "
                f"{_md(', '.join(claim['labels']))} | {_md(claim['text'])} |"
            )
    lines += ["", "## Impact", ""]
    if not data["impact"]:
        lines.append("None.")
    else:
        lines.append("| Finding | Node | Claims |")
        lines.append("| --- | --- | --- |")
        for item in data["impact"]:
            lines.append(
                f"| `{_md(item['code'])}` | `{_md(item['node'])}` | "
                f"{_md(', '.join(item['claims']) or 'none')} |"
            )
    baseline = data.get("baseline")
    if baseline is not None:
        lines += ["", "## Baseline", ""]
        for member in baseline["members"]:
            detail = member["status"]
            if any(member.get(key) for key in ("added", "removed", "changed")):
                detail += (
                    f" (+{len(member.get('added', []))} -{len(member.get('removed', []))}"
                    f" ~{len(member.get('changed', []))})"
                )
            if member.get("schema_changed"):
                detail += ", schema changed"
            lines.append(f"- `{_md(member['id'])}`: {_md(detail)}")
        if baseline["removed_members"]:
            lines.append(
                "- Removed members: " + _md(", ".join(baseline["removed_members"]))
            )
        for kind in ("claims", "relationships"):
            changes = baseline[kind]
            if any(changes.values()):
                lines.append(
                    f"- {kind}: +{len(changes['added'])} -{len(changes['removed'])}"
                    f" ~{len(changes['changed'])}"
                )
    lines += ["", "> Recorded assertions only; this is not a scientific validity claim.", ""]
    return "\n".join(lines)


def cmd_report(args: argparse.Namespace) -> int:
    try:
        loaded, graph, checks, states = _loaded(args)
    except WorkspaceError as exc:
        return _error("report", "workspace", str(exc))
    data = report_data(
        loaded, graph, states, checks,
        directory_artifacts=args.directory_artifacts,
        baseline_root=args.baseline,
    )
    if args.strict and data["exit_code"] == 0:
        warnings = sum(member["counts"]["warning"] for member in data["members"])
        if warnings:
            data["exit_code"] = EXIT_EVIDENCE
    if args.json:
        _print_json(data)
    elif args.markdown:
        print(_markdown_report(data, loaded.document))
    else:
        policy = data["check_policy"]
        print(
            "Policy: directory artifacts "
            + ("enabled" if policy.get("directory_artifacts") else "disabled")
        )
        print("Current evidence:")
        for member in data["members"]:
            print(
                f"  {member['id']} ({member['path']}): "
                f"{member['counts']['error']} errors, {member['counts']['warning']} warnings"
            )
            if member["load_error"]:
                print(f"    ERROR workspace: {_report_text(member['load_error'])}")
            for finding in member["findings"][:_REPORT_TEXT_FINDINGS]:
                subject = f" [{_report_text(finding['subject'], 80)}]" if finding["subject"] else ""
                print(
                    f"    {finding['severity'].upper()} {finding['code']}{subject}: "
                    f"{_report_text(finding['message'])}"
                )
            hidden = len(member["findings"]) - _REPORT_TEXT_FINDINGS
            if hidden > 0:
                print(f"    and {hidden} more findings (see --json)")
        print("Claims:")
        for claim in data["claims"]:
            owner = claim["member"] or "workspace"
            print(
                f"  {claim['id']} [{owner}]: {', '.join(claim['labels'])} "
                f'- "{_report_text(claim["text"])}"'
            )
        print("Impact:")
        for item in data["impact"]:
            print(f"  {item['code']} {item['node']}: {', '.join(item['claims']) or 'none'}")
        baseline = data.get("baseline")
        if baseline is not None:
            print(f"Baseline ({args.baseline}):")
            for member in baseline["members"]:
                detail = member["status"]
                counts = [
                    f"+{len(member.get('added', []))}",
                    f"-{len(member.get('removed', []))}",
                    f"~{len(member.get('changed', []))}",
                ]
                if any(
                    member.get(key)
                    for key in ("added", "removed", "changed")
                ) or member.get("schema_changed"):
                    detail += f" ({' '.join(counts)}"
                    if member.get("schema_changed"):
                        detail += ", schema changed"
                    detail += ")"
                print(f"  {member['id']}: {detail}")
            if baseline["removed_members"]:
                print(f"  removed members: {', '.join(baseline['removed_members'])}")
            for kind in ("claims", "relationships"):
                changes = baseline[kind]
                if any(changes.values()):
                    print(
                        f"  {kind}: +{len(changes['added'])} -{len(changes['removed'])}"
                        f" ~{len(changes['changed'])}"
                    )
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


def _format_value(value: object) -> str:
    if isinstance(value, (dict, list)):
        return _report_text(value, 120)
    return _report_text(value, 120)


def _format_compare(data: dict) -> str:
    latest = " (latest step per run)" if data.get("latest") else ""
    lines = [f'Metric: {data["metric"]}{latest}', f'Rows ({len(data["rows"])}):']
    for row in data["rows"]:
        if row["value"] is None:
            shown = "missing"
        elif not row["numeric"]:
            shown = f"{_format_value(row['value'])} (non-numeric)"
        else:
            shown = _format_value(row["value"])
        lines.append(f'  {row["member"]}/{row["run"]}/{row["result"]}: {shown}')
    lines.append(f'Missing: {data["missing"]}. Non-numeric: {data["non_numeric"]}.')
    if data["comparability"]:
        lines.append("Comparability:")
        for pair in data["comparability"]:
            if "baseline" in pair:
                lines.append(
                    f'  {pair["baseline"]["member"]}#{pair["baseline"]["result"]} -> '
                    f'{pair["target"]["member"]}#{pair["target"]["result"]}: {pair["conditions"]}'
                )
            else:
                lines.append(f'  {pair["conditions"]}')
    return "\n".join(lines)


def _format_matrix(data: dict) -> str:
    lines = [f'Metrics: {", ".join(data["metrics"]) or "none"}']
    for cell in data["cells"]:
        if cell["state"] == "value":
            shown = _format_value(cell["value"])
            if "step" in cell:
                shown += f' (step {cell["step"]})'
        else:
            shown = cell["state"]
        lines.append(f'  {cell["member"]}/{cell["run"]} {cell["metric"]}: {shown}')
    lines.append(
        f'Missing: {data["missing"]}. Non-numeric: {data["non_numeric"]}. '
        f'Ambiguous: {data["ambiguous"]}.'
    )
    return "\n".join(lines)


def _format_leaderboard(data: dict) -> str:
    latest = " (latest step per run)" if data.get("latest") else ""
    lines = [f'Metric: {data["metric"]} ({data["direction"]}){latest}', "Ranking:"]
    for row in data["rows"]:
        lines.append(
            f'  {row["rank"]:>3}. {_format_value(row["value"])}: '
            f'{row["member"]}/{row["run"]}'
        )
    if not data["rows"]:
        lines.append("  none")
    lines.append(
        f'Missing: {data["excluded"]["missing"]}. '
        f'Non-numeric: {data["excluded"]["non_numeric"]}.'
    )
    return "\n".join(lines)


def _format_stats(data: dict) -> str:
    selected = data.get("metric")
    latest = " (latest step per run)" if data.get("latest") else ""
    lines = [f'Metric: {selected}{latest}' if selected else f"All metrics{latest}"]
    for entry in data["metrics"]:
        lines.append(
            f'  {entry["name"]}: {entry["rows"]} rows, {entry["numeric"]} numeric, '
            f'{entry["missing"]} missing, {entry["non_numeric"]} non-numeric, '
            f'{entry["ambiguous"]} ambiguous'
        )
        if entry["numeric"]:
            lines.append(
                f'    min {_format_value(entry["min"])}  max {_format_value(entry["max"])}  '
                f'mean {_format_value(entry["mean"])}  median {_format_value(entry["median"])}  '
                f'stddev {_format_value(entry["stddev"])}'
            )
    if not data["metrics"]:
        lines.append("  none")
    return "\n".join(lines)


def cmd_compare(args: argparse.Namespace) -> int:
    try:
        loaded = load_workspace(Path(args.root))
    except WorkspaceError as exc:
        return _error("compare", "workspace", str(exc))
    data = compare_data(loaded, args.metric, args.run, latest=args.latest)
    if args.text:
        print(_format_compare(data))
    else:
        _print_json(data)
    return data["exit_code"]


def cmd_matrix(args: argparse.Namespace) -> int:
    try:
        loaded = load_workspace(Path(args.root))
    except WorkspaceError as exc:
        return _error("matrix", "workspace", str(exc))
    data = matrix_data(loaded, latest=args.latest)
    if args.text:
        print(_format_matrix(data))
    else:
        _print_json(data)
    return EXIT_OK


def cmd_db_export(args: argparse.Namespace) -> int:
    try:
        loaded = load_workspace(Path(args.root))
        data = export_workspace(loaded, args.output, force=args.force)
    except (WorkspaceError, OSError, ValueError) as exc:
        return _error("db-export", "workspace", str(exc))
    if args.json:
        _print_json(data)
    else:
        print(f"Wrote {data['path']} ({data['counts']['members']} members).")
    return EXIT_OK


def cmd_db_check(args: argparse.Namespace) -> int:
    try:
        loaded = load_workspace(Path(args.root))
    except WorkspaceError as exc:
        return _error("db-check", "workspace", str(exc))
    data = check_workspace(loaded, args.db)
    if args.json:
        _print_json(data)
    else:
        print("OK: workspace cache matches (exit 0)" if data["ok"] else "FAIL: cache drift (exit 1)")
    return data["exit_code"]


def cmd_db_import(args: argparse.Namespace) -> int:
    try:
        data = import_workspace(args.db, args.output, force=args.force)
    except (OSError, ValueError) as exc:
        return _error("db-import", "workspace", str(exc))
    if args.json:
        _print_json(data)
    else:
        print(f"Wrote {data['path']} ({data['counts']['members']} members).")
    return EXIT_OK


def cmd_runs(args: argparse.Namespace) -> int:
    try:
        loaded = load_workspace(Path(args.root))
    except WorkspaceError as exc:
        return _error("runs", "workspace", str(exc))
    if args.member is not None and args.member not in loaded.members:
        return _error("runs", "member", f"unknown member {args.member!r}")
    data = runs_data(
        loaded,
        args.member,
        include_metadata=args.metadata,
        include_summaries=args.summaries,
    )
    if args.json:
        _print_json(data)
        return EXIT_OK
    print(f"Runs ({len(data['runs'])}):")
    for row in data["runs"]:
        name = f" ({row['name']})" if row["name"] else ""
        print(
            f"  {row['member']} / {row['run']}{name}: "
            f"{row['counts']['artifacts']} artifacts, {row['counts']['results']} results"
        )
        if row["params"]:
            print(f"    params: {_report_text(row['params'], 240)}")
        if row["conditions"] is not None:
            print(f"    conditions: {_report_text(row['conditions'], 240)}")
        if row["started_at"]:
            print(f"    started: {_report_text(row['started_at'], 80)}")
        if args.metadata:
            print(f"    metadata: {_report_text(row['metadata'], 240)}")
        if args.summaries and row.get("summaries"):
            print(f"    summaries: {_report_text(row['summaries'], 240)}")
    return EXIT_OK


def cmd_stats(args: argparse.Namespace) -> int:
    try:
        loaded = load_workspace(Path(args.root))
    except WorkspaceError as exc:
        return _error("stats", "workspace", str(exc))
    data = stats_data(loaded, args.metric, latest=args.latest)
    if args.text:
        print(_format_stats(data))
    else:
        _print_json(data)
    return EXIT_OK


def cmd_leaderboard(args: argparse.Namespace) -> int:
    try:
        loaded = load_workspace(Path(args.root))
    except WorkspaceError as exc:
        return _error("leaderboard", "workspace", str(exc))
    data = leaderboard_data(loaded, args.metric, args.direction, args.top, latest=args.latest)
    if args.text:
        print(_format_leaderboard(data))
    else:
        _print_json(data)
    return data["exit_code"]


def _author(args: argparse.Namespace, operations: list[dict], command: str) -> int:
    try:
        loaded = load_workspace(Path(args.root))
        document = apply_patch(
            loaded,
            {
                "format": "rungraph-patch",
                "version": "0.1",
                "expected_sha256": args.expected_sha256,
                "operations": operations,
            },
            expected_sha256=args.expected_sha256,
            dry_run=args.dry_run,
        )
    except WorkspaceError as exc:
        return _error(command, "refused", str(exc))
    data = {
        "format": f"rungraph-{command}",
        "version": "0.1",
        "changed": operations,
        "path": str(loaded.path),
        "dry_run": args.dry_run,
        "written": not args.dry_run,
        "exit_code": 0,
    }
    if args.json:
        _print_json(data)
    else:
        print(("Would apply" if args.dry_run else "Applied") + f" {len(operations)} operation(s).")
    return EXIT_OK


def _read_workspace_entry(
    args: argparse.Namespace, command: str, *, code: str = "file"
) -> tuple[dict | None, int | None]:
    """Read a --file payload, returning (entry, failure)."""
    try:
        data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, _error(command, code, str(exc))
    if not isinstance(data, dict):
        return None, _error(command, code, "the file must contain one JSON object")
    return data, None


def cmd_patch(args: argparse.Namespace) -> int:
    patch, failure = _read_workspace_entry(args, "patch", code="patch")
    if failure is not None:
        return failure
    if patch.get("format") != "rungraph-patch" or patch.get("version") != "0.1":
        return _error("patch", "patch", "patch must be a rungraph-patch 0.1 document")
    if args.expected_sha256 is None:
        args.expected_sha256 = patch.get("expected_sha256")
    return _author(args, patch.get("operations", []), "patch")


def cmd_member(args: argparse.Namespace) -> int:
    if args.action == "add":
        member = {"id": args.id, "path": args.path}
        if args.manifest:
            member["manifest"] = args.manifest
        if args.role:
            member["role"] = args.role
        operations = [{"op": "add-member", "member": member}]
    elif args.action == "remove":
        operations = [{"op": "remove-member", "id": args.id}]
    elif args.action == "move":
        operations = [{"op": "move-member", "id": args.id, "path": args.path}]
    else:
        operations = [{"op": "rename-member", "id": args.id, "new_id": args.new_id}]
    return _author(args, operations, "member")


def cmd_claim(args: argparse.Namespace) -> int:
    if args.action == "remove":
        operations = [{"op": "remove-claim", "id": args.id}]
    else:
        claim, failure = _read_workspace_entry(args, "claim")
        if failure is not None:
            return failure
        claim_id = claim.get("id")
        if not isinstance(claim_id, str) or not claim_id:
            return _error("claim", "file", "the file needs a non-empty 'id'")
        operations = [
            {"op": "add-claim" if args.action == "add" else "edit-claim", "id": claim_id, "claim": claim}
        ]
    return _author(args, operations, "claim")


def cmd_relationship(args: argparse.Namespace) -> int:
    if args.action == "remove":
        operations = [{"op": "remove-relationship", "id": args.id}]
    else:
        relationship, failure = _read_workspace_entry(args, "relationship")
        if failure is not None:
            return failure
        operations = [{"op": "add-relationship", "relationship": relationship}]
    return _author(args, operations, "relationship")


def _slug(text: str) -> str:
    out = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in text).strip("-.")
    return out or "workspace"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rungraph", description="Offline workspace composition over RunEvidence manifests.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("discover", help="list manifests under a root without writing")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--check", action="store_true")
    p.add_argument("--directory-artifacts", action="store_true")
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
    p.add_argument(
        "--detail",
        action="store_true",
        help="include recorded object data on JSON nodes",
    )
    p.add_argument("--fail-on-unresolved", action="store_true")
    p.set_defaults(func=cmd_graph)

    p = sub.add_parser("report", help="consolidated evidence and claim report")
    p.add_argument("root", nargs="?", default=".")
    output = p.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true")
    output.add_argument("--markdown", action="store_true", help="print a Markdown report")
    p.add_argument("--strict", action="store_true")
    p.add_argument("--directory-artifacts", action="store_true")
    p.add_argument("--baseline")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("db-export", help="export a workspace to a derived SQLite cache")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_db_export)

    p = sub.add_parser("db-check", help="compare a workspace SQLite cache with the document")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_db_check)

    p = sub.add_parser("db-import", help="import a workspace SQLite cache to JSON")
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_db_import)

    p = sub.add_parser("runs", help="list recorded runs across members")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--member")
    p.add_argument("--metadata", action="store_true")
    p.add_argument("--summaries", action="store_true", help="include whitelisted adapter summaries")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_runs)

    p = sub.add_parser("impact", help="claims affected by one workspace node")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--from", dest="source", required=True)
    p.set_defaults(func=cmd_impact)

    p = sub.add_parser("compare", help="recorded values for one metric across members")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--metric", required=True)
    p.add_argument("--run")
    p.add_argument(
        "--latest",
        action="store_true",
        help="keep the highest '<name> @ <step>' value per run",
    )
    p.add_argument("--text", action="store_true", help="human-readable output instead of JSON")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("matrix", help="recorded values as a member x metric matrix")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument(
        "--latest",
        action="store_true",
        help="keep the highest '<name> @ <step>' value per run",
    )
    p.add_argument("--text", action="store_true", help="human-readable output instead of JSON")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_matrix)

    p = sub.add_parser("stats", help="per-metric count and distribution statistics")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--metric")
    p.add_argument(
        "--latest",
        action="store_true",
        help="keep the highest '<name> @ <step>' value per run",
    )
    p.add_argument("--text", action="store_true", help="human-readable output instead of JSON")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("leaderboard", help="recorded numeric values ranked across members")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--metric", required=True)
    p.add_argument("--direction", choices=("max", "min"), required=True)
    p.add_argument("--top", type=int, default=10)
    p.add_argument(
        "--latest",
        action="store_true",
        help="keep the highest '<name> @ <step>' value per run",
    )
    p.add_argument("--text", action="store_true", help="human-readable output instead of JSON")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_leaderboard)

    p = sub.add_parser("patch", help="apply an all-or-nothing workspace patch")
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--file", required=True, help="one JSON object; see docs/authoring-files.md")
    p.add_argument("--expected-sha256")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_patch)

    p = sub.add_parser("member", help="add, remove, move or rename a member")
    p.add_argument("action", choices=("add", "remove", "move", "rename"))
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--id")
    p.add_argument("--path")
    p.add_argument("--manifest")
    p.add_argument("--role")
    p.add_argument("--new-id")
    p.add_argument("--expected-sha256")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_member)

    p = sub.add_parser("claim", help="add, edit or remove a workspace claim")
    p.add_argument("action", choices=("add", "edit", "remove"))
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--id")
    p.add_argument("--file", help="one JSON object; see docs/authoring-files.md")
    p.add_argument("--expected-sha256")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_claim)

    p = sub.add_parser("relationship", help="add or remove a workspace relationship")
    p.add_argument("action", choices=("add", "remove"))
    p.add_argument("root", nargs="?", default=".")
    p.add_argument("--id")
    p.add_argument("--file", help="one JSON object; see docs/authoring-files.md")
    p.add_argument("--expected-sha256")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_relationship)
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
