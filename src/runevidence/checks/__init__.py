"""Run every check against a loaded manifest and collect one report."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..findings import Finding, Severity
from ..manifest import LoadedManifest
from .conditions import check_conditions
from .files import check_files
from .graph import check_graph
from .results import check_results, check_targeted_results

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


def run_checks(
    loaded: LoadedManifest, include_untracked: bool = False, *, enable_directory_artifacts: bool = False
) -> Report:
    """``enable_directory_artifacts`` is the public read-only opt-in behind
    ``--directory-artifacts`` on ``validate`` and ``inspect``. It scans
    recorded folders and never writes or authors a directory artifact."""
    manifest = loaded.manifest
    is_0_3 = manifest.schema_version == "0.3"
    artifact_ids = {artifact.id for artifact in manifest.artifacts}
    targeted_ids = {
        result.artifact_id
        for result in manifest.results
        if is_0_3 and result.locator is not None and result.artifact_id is not None
    }
    targeted_result_ids = {
        result.id
        for result in manifest.results
        if is_0_3 and result.locator is not None
    }
    unverified = {
        result.id
        for result in manifest.results
        if is_0_3 and result.locator is None
    }
    dangling_targeted = {
        result.id
        for result in manifest.results
        if is_0_3
        and result.locator is not None
        and result.artifact_id not in artifact_ids
    }

    result_broken: set[str] = set(dangling_targeted)

    def _targeted_callback(artifact, state, out) -> None:
        target_findings, target_broken = check_targeted_results(manifest, artifact, state)
        out.findings.extend(target_findings)
        result_broken.update(target_broken)

    files = check_files(
        loaded,
        include_untracked=include_untracked,
        enable_directory_artifacts=enable_directory_artifacts,
        targeted_ids=targeted_ids,
        on_targeted=_targeted_callback,
    )

    result_findings, mismatched = check_results(
        manifest,
        files.readable_paths,
        enable_directory_artifacts=enable_directory_artifacts,
        skip_result_ids=targeted_result_ids,
    )
    graph_findings = check_graph(
        manifest, files.broken | mismatched | result_broken, unverified=unverified
    )
    condition_findings = check_conditions(loaded.manifest)
    findings = sorted(
        files.findings + result_findings + graph_findings + condition_findings,
        key=Finding.sort_key,
    )
    return Report(manifest_path=loaded.path, findings=findings)
