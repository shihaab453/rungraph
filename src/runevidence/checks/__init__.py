"""Run every check against a loaded manifest and collect one report."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ..findings import Finding, Severity
from ..manifest import LoadedManifest
from ..models import (
    SCHEMA_VERSION_0_3,
    SCHEMA_VERSION_0_4,
    SCHEMA_VERSION_0_5,
    SCHEMA_VERSION_0_6,
    SCHEMA_VERSION_0_7,
    CsvCellLocator,
    TensorboardHistogramLocator,
    TensorboardLocator,
    TensorboardStringLocator,
)
from .conditions import check_conditions
from .files import check_files
from .graph import check_graph
from .results import (
    _tensorboard_path_matches,
    check_results,
    check_targeted_results,
)

__all__ = ["Report", "run_checks"]

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_INVALID = 2

# Per-manifest csv-cell eligibility caps (RFC section 5.3). Counted without
# reading an artifact: a manifest-eligible entry is a csv-cell locator on an
# existing non-directory artifact whose path ends ".csv", with a recorded
# sha256 and not volatile. Runtime file states do not reduce the count.
CSV_CELL_MAX_ENTRIES = 10_000
CSV_CELL_MAX_HEADER_BYTES = 16 * 1024 * 1024

# Per-manifest TensorBoard eligibility caps (RFC section 6). The same
# manifest-eligible predicate applies: a non-directory artifact whose basename
# contains ".tfevents", with a recorded sha256 and not volatile.
TENSORBOARD_MAX_ENTRIES = 10_000
TENSORBOARD_MAX_TAG_BYTES = 16 * 1024 * 1024

_LOCATOR_SCHEMAS = frozenset(
    {
        SCHEMA_VERSION_0_3,
        SCHEMA_VERSION_0_4,
        SCHEMA_VERSION_0_5,
        SCHEMA_VERSION_0_6,
        SCHEMA_VERSION_0_7,
    }
)


def _csv_cell_cap(
    manifest,
) -> tuple[bool, frozenset[str]]:
    """Classify csv-cell eligibility without reads; emit nothing.

    Returns ``(capped, eligible_artifact_ids)``. Only schema 0.4 and 0.5 can
    carry a csv-cell locator, so every earlier schema is uncapped with no
    eligible artifacts. Header metadata is the UTF-8 byte length of each
    eligible locator's recorded header.
    """
    if manifest.schema_version not in (
        SCHEMA_VERSION_0_4,
        SCHEMA_VERSION_0_5,
        SCHEMA_VERSION_0_6,
        SCHEMA_VERSION_0_7,
    ):
        return False, frozenset()
    eligible_artifacts = {
        artifact.id
        for artifact in manifest.artifacts
        if artifact.artifact_type != "directory"
        and artifact.path.replace("\\", "/").lower().endswith(".csv")
        and artifact.sha256 is not None
        and not artifact.volatile
    }
    entries = 0
    header_bytes = 0
    with_entries: set[str] = set()
    for result in manifest.results:
        locator = result.locator
        if not isinstance(locator, CsvCellLocator):
            continue
        if result.artifact_id not in eligible_artifacts:
            continue
        entries += 1
        header_bytes += len(locator.header.encode("utf-8"))
        with_entries.add(result.artifact_id)
    capped = (
        entries > CSV_CELL_MAX_ENTRIES
        or header_bytes > CSV_CELL_MAX_HEADER_BYTES
    )
    return capped, frozenset(with_entries)


def _tensorboard_cap(
    manifest,
) -> tuple[bool, frozenset[str]]:
    """Classify TensorBoard eligibility without reads; emit nothing.

    Returns ``(capped, eligible_artifact_ids)``. Only schema 0.5 can carry a
    tensorboard locator. Tag metadata is the UTF-8 byte length of each eligible
    locator's recorded tag.
    """
    if manifest.schema_version not in (
        SCHEMA_VERSION_0_5,
        SCHEMA_VERSION_0_6,
        SCHEMA_VERSION_0_7,
    ):
        return False, frozenset()
    eligible_artifacts = {
        artifact.id
        for artifact in manifest.artifacts
        if artifact.artifact_type != "directory"
        and _tensorboard_path_matches(artifact.path)
        and artifact.sha256 is not None
        and not artifact.volatile
    }
    entries = 0
    tag_bytes = 0
    with_entries: set[str] = set()
    for result in manifest.results:
        locator = result.locator
        if not isinstance(
            locator,
            (
                TensorboardLocator,
                TensorboardStringLocator,
                TensorboardHistogramLocator,
            ),
        ):
            continue
        if result.artifact_id not in eligible_artifacts:
            continue
        entries += 1
        tag_bytes += len(locator.tag.encode("utf-8"))
        with_entries.add(result.artifact_id)
    capped = (
        entries > TENSORBOARD_MAX_ENTRIES
        or tag_bytes > TENSORBOARD_MAX_TAG_BYTES
    )
    return capped, frozenset(with_entries)


def _tensorboard_snapshot_ids(manifest) -> frozenset[str]:
    """Artifacts whose shared snapshot cap is raised to 128 MiB.

    The raise applies only when a 0.5 tensorboard locator targets a
    non-directory artifact whose basename contains ".tfevents". A
    kind-mismatched locator never raises it, and runtime digest/volatile state
    is irrelevant here (a directory is never read as a snapshot).
    """
    if manifest.schema_version not in (
        SCHEMA_VERSION_0_5,
        SCHEMA_VERSION_0_6,
        SCHEMA_VERSION_0_7,
    ):
        return frozenset()
    paths = {
        artifact.id: artifact.path
        for artifact in manifest.artifacts
        if artifact.artifact_type != "directory"
    }
    return frozenset(
        result.artifact_id
        for result in manifest.results
        if isinstance(
            result.locator,
            (
                TensorboardLocator,
                TensorboardStringLocator,
                TensorboardHistogramLocator,
            ),
        )
        and result.artifact_id is not None
        and result.artifact_id in paths
        and _tensorboard_path_matches(paths[result.artifact_id])
    )


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
    has_locators = manifest.schema_version in _LOCATOR_SCHEMAS
    artifact_ids = {artifact.id for artifact in manifest.artifacts}
    targeted_ids = {
        result.artifact_id
        for result in manifest.results
        if has_locators and result.locator is not None and result.artifact_id is not None
    }
    targeted_result_ids = {
        result.id
        for result in manifest.results
        if has_locators and result.locator is not None
    }
    unverified = {
        result.id
        for result in manifest.results
        if has_locators and result.locator is None
    }
    dangling_targeted = {
        result.id
        for result in manifest.results
        if has_locators
        and result.locator is not None
        and result.artifact_id not in artifact_ids
    }

    result_broken: set[str] = set(dangling_targeted)
    csv_cell_capped, csv_cell_eligible_artifacts = _csv_cell_cap(manifest)
    tensorboard_capped, tensorboard_eligible_artifacts = _tensorboard_cap(manifest)
    capped_artifacts: set[str] = set()

    def _targeted_callback(artifact, state, out) -> None:
        target_findings, target_broken, capped_artifact = check_targeted_results(
            manifest,
            artifact,
            state,
            csv_cell_capped=csv_cell_capped,
            csv_cell_eligible=artifact.id in csv_cell_eligible_artifacts,
            tensorboard_capped=tensorboard_capped,
            tensorboard_eligible=artifact.id in tensorboard_eligible_artifacts,
        )
        out.findings.extend(target_findings)
        result_broken.update(target_broken)
        if capped_artifact:
            # A capped artifact is broken at the artifact level (RFC section 8),
            # even though no per-result value finding was emitted. Other kinds
            # on the same artifact may still have verified.
            out.broken.add(artifact.id)
            capped_artifacts.add(artifact.id)

    files = check_files(
        loaded,
        include_untracked=include_untracked,
        enable_directory_artifacts=enable_directory_artifacts,
        targeted_ids=targeted_ids,
        on_targeted=_targeted_callback,
        tensorboard_snapshot_ids=_tensorboard_snapshot_ids(manifest),
    )

    result_findings, mismatched = check_results(
        manifest,
        files.readable_paths,
        enable_directory_artifacts=enable_directory_artifacts,
        skip_result_ids=targeted_result_ids,
    )
    graph_findings = check_graph(
        manifest,
        files.broken | mismatched | result_broken,
        unverified=unverified,
        capped=capped_artifacts,
    )
    condition_findings = check_conditions(loaded.manifest)
    findings = sorted(
        files.findings + result_findings + graph_findings + condition_findings,
        key=Finding.sort_key,
    )
    return Report(manifest_path=loaded.path, findings=findings)
