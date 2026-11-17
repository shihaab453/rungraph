"""Turn reports into text for people or JSON for machines."""

from __future__ import annotations

import json
from typing import Optional

from .checks import Report
from .findings import Finding, Severity
from .histogram_display import value_display
from .models import Manifest

_LABEL = {Severity.error: "ERROR", Severity.warning: "WARN", Severity.info: "INFO"}


def format_finding(f: Finding) -> str:
    subject = f" [{f.subject}]" if f.subject else ""
    line = f"{_LABEL[f.severity]:<5} {f.code.value}{subject}: {f.message}"
    if f.hint:
        line += f"\n      hint: {f.hint}"
    return line


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def format_summary_line(report: Report, strict: bool = False) -> str:
    code = report.exit_code(strict)
    status = "OK" if code == 0 else "FAIL"
    parts = [_plural(report.errors, "error"), _plural(report.warnings, "warning")]
    infos = report.count(Severity.info)
    if infos:
        parts.append(f"{infos} info")
    return f"{status}: {', '.join(parts)} (exit {code})"


def format_findings(report: Report, strict: bool = False, show_info: bool = True) -> str:
    lines = [format_finding(f) for f in report.findings if show_info or f.severity is not Severity.info]
    lines.append(format_summary_line(report, strict))
    return "\n".join(lines)


# Keep human-facing output bounded per run; machine-readable inspect JSON
# retains every adapter-reported entry. These are display-only observations.
_UNREADABLE_TEXT_LIMIT = 20
_UNREADABLE_NAMESPACES = ("hydra", "mlflow", "sacred")


def _adapter_unreadable(manifest: Manifest) -> list[tuple[int, dict[str, str]]]:
    """Collect recognized adapter metadata without interpreting arbitrary shapes.

    The namespaces below are the adapters that record unreadable inputs. They
    are listed in display order, so a manifest's own key order cannot change
    the report.
    """
    entries: list[tuple[int, dict[str, str]]] = []
    for run_index, run in enumerate(manifest.runs):
        metadata = run.metadata
        if not isinstance(metadata, dict):
            continue
        for adapter in _UNREADABLE_NAMESPACES:
            details = metadata.get(adapter)
            if not isinstance(details, dict):
                continue
            paths = details.get("unreadable")
            if not isinstance(paths, list):
                continue
            raw_reasons = details.get("unreadable_reasons")
            reasons = raw_reasons if isinstance(raw_reasons, dict) else {}
            for path in paths:
                if not isinstance(path, str):
                    continue
                entry = {"run": run.id, "adapter": adapter, "path": path}
                reason = reasons.get(path)
                if isinstance(reason, str):
                    entry["reason"] = reason
                entries.append((run_index, entry))
    return entries


def _display_metadata_text(value: str) -> str:
    """Prevent terminal controls/newlines in manifest metadata spoofing lines."""
    return value if value.isprintable() else json.dumps(value, ensure_ascii=True)


def format_overview(manifest: Manifest) -> str:
    p = manifest.project
    lines = [f"Project: {p.name} ({p.id})"]
    if p.description:
        lines.append(f"  {p.description}")

    produced: dict[str, int] = {}
    for a in manifest.artifacts:
        if a.run_id:
            produced[a.run_id] = produced.get(a.run_id, 0) + 1
    counted: dict[str, int] = {}
    for r in manifest.results:
        counted[r.run_id] = counted.get(r.run_id, 0) + 1

    lines.append(f"\nRuns ({len(manifest.runs)}):")
    for run in manifest.runs:
        params = ", ".join(f"{k}={v}" for k, v in run.params.items())
        lines.append(
            f"  {run.id}: {_plural(produced.get(run.id, 0), 'artifact')}, {_plural(counted.get(run.id, 0), 'result')}"
            + (f"  [{params}]" if params else "")
        )

    unreadable = _adapter_unreadable(manifest)
    if unreadable:
        lines.append(f"\nInputs the adapter could not read ({len(unreadable)}):")
        totals: dict[int, int] = {}
        for run_index, _ in unreadable:
            totals[run_index] = totals.get(run_index, 0) + 1
        shown: dict[int, int] = {}
        for run_index, entry in unreadable:
            count = shown.get(run_index, 0)
            if count >= _UNREADABLE_TEXT_LIMIT:
                continue
            lines.append(
                f"  {entry['run']} ({entry['adapter']}): {_display_metadata_text(entry['path'])}"
            )
            if "reason" in entry:
                lines.append(f"    {_display_metadata_text(entry['reason'])}")
            shown[run_index] = count + 1
            if shown[run_index] == _UNREADABLE_TEXT_LIMIT and totals[run_index] > count + 1:
                lines.append(f"    and {totals[run_index] - _UNREADABLE_TEXT_LIMIT} more")

    lines.append(f"\nArtifacts ({len(manifest.artifacts)}):")
    for a in manifest.artifacts:
        digest = "(volatile)" if a.volatile else a.sha256[:12] if a.sha256 else "(no sha256)"
        kind = f" {a.kind}" if a.kind else ""
        lines.append(f"  {a.id}: {a.path}  {digest}{kind}")

    if manifest.results:
        lines.append(f"\nResults ({len(manifest.results)}):")
        for r in manifest.results:
            lines.append(f"  {r.id}: {r.run_id} {r.name} = {value_display(r.value) or repr(r.value)}")

    if manifest.evidence:
        lines.append(f"\nEvidence ({len(manifest.evidence)}):")
        for e in manifest.evidence:
            lines.append(f"  {e.id}: {e.claim}")
            if e.supports:
                lines.append(f"    supports: {', '.join(e.supports)}")
            if e.contradicts:
                lines.append(f"    contradicts: {', '.join(e.contradicts)}")
    return "\n".join(lines)


def to_json(report: Report, strict: bool = False, manifest: Optional[Manifest] = None) -> str:
    data = {
        "manifest": str(report.manifest_path),
        "ok": report.exit_code(strict) == 0,
        "exit_code": report.exit_code(strict),
        "counts": {s.value: report.count(s) for s in Severity},
        "findings": [f.model_dump(mode="json") for f in report.findings],
    }
    if manifest is not None:
        data["summary"] = {
            "project": manifest.project.id,
            "runs": len(manifest.runs),
            "artifacts": len(manifest.artifacts),
            "results": len(manifest.results),
            "evidence": len(manifest.evidence),
        }
        unreadable = _adapter_unreadable(manifest)
        if unreadable:
            data["summary"]["adapter_unreadable"] = [entry for _, entry in unreadable]
    return json.dumps(data, indent=2)
