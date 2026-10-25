"""Run every check against a loaded manifest and collect one report."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..findings import Finding, Severity
from ..manifest import LoadedManifest
from .files import check_files
from .graph import check_graph
from .results import check_results

__all__ = ["Report", "run_checks"]

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_INVALID = 2


@dataclass
class Report:
    manifest_path: Path
    findings: list[Finding] = field(default_factory=list)

    def count(self, severity: Severity) -> int:
        return sum(1 for f in self.findings if f.severity is severity)

    @property
    def errors(self) -> int:
        return self.count(Severity.error)

    @property
    def warnings(self) -> int:
        return self.count(Severity.warning)

    def exit_code(self, strict: bool = False) -> int:
        if self.errors or (strict and self.warnings):
            return EXIT_FINDINGS
        return EXIT_OK

    def codes(self) -> Counter:
        return Counter(f.code.value for f in self.findings)


def run_checks(loaded: LoadedManifest, include_untracked: bool = False) -> Report:
    files = check_files(loaded, include_untracked=include_untracked)
    result_findings, mismatched = check_results(loaded.manifest, files.ok_paths, files.broken)
    graph_findings = check_graph(loaded.manifest, files.broken | mismatched)
    findings = sorted(files.findings + result_findings + graph_findings, key=Finding.sort_key)
    return Report(manifest_path=loaded.path, findings=findings)
