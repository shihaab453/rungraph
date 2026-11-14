"""Reference validation for schema 0.2 run-condition selectors.

Every declared selector is checked during validate/inspect, including runs that
no comparison selects. Findings are fixed text: no artifact ID, run ID,
selector value, path, metadata or recorded value is echoed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from runevidence.checks import run_checks
from runevidence.checks.conditions import check_conditions
from runevidence.cli import main
from runevidence.findings import Code, Severity
from runevidence.manifest import load_manifest

MANIFEST = "runevidence.json"
REPO = Path(__file__).resolve().parents[1]
DIGEST = hashlib.sha256(b"data").hexdigest()

CONDITION_CODES = frozenset(
    {
        Code.condition_reference_missing,
        Code.condition_reference_wrong_kind,
        Code.condition_reference_unsupported,
        Code.condition_reference_not_consumed,
    }
)


# ------------------------------------------------------------------ helpers


def file_artifact(ident: str, path: str, *, kind: str | None = "dataset", **extra) -> dict:
    artifact = {"id": ident, "path": path, "sha256": DIGEST}
    if kind is not None:
        artifact["kind"] = kind
    artifact.update(extra)
    return artifact


def directory_artifact(ident: str, path: str, *, kind: str | None = "checkpoint") -> dict:
    artifact = {
        "id": ident,
        "path": path,
        "artifact_type": "directory",
        "digest_format": "runevidence-directory-v1",
    }
    if kind is not None:
        artifact["kind"] = kind
    return artifact


def run(ident: str, *, dataset: str | None = None, checkpoint: str | None = None) -> dict:
    conditions = {}
    if dataset is not None:
        conditions["evaluation_dataset_artifact_id"] = dataset
    if checkpoint is not None:
        conditions["model_checkpoint_artifact_id"] = checkpoint
    entry = {"id": ident}
    if conditions:
        entry["conditions"] = conditions
    return entry


def input_to(source: str, target: str) -> dict:
    return {"source": source, "target": target, "type": "input_to"}


def make_doc(*, runs: list[dict], artifacts: list[dict] | None = None,
             relationships: list[dict] | None = None) -> dict:
    return {
        "schema_version": "0.2",
        "project": {"id": "proj", "name": "Project"},
        "runs": runs,
        "artifacts": artifacts or [],
        "results": [],
        "evidence": [],
        "relationships": relationships or [],
    }


def write_experiment(root: Path, doc: dict) -> Path:
    for artifact in doc["artifacts"]:
        target = root / artifact["path"]
        if artifact.get("artifact_type") == "directory":
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"data")
    (root / MANIFEST).write_text(
        json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return root


def condition_findings(report) -> list:
    return [finding for finding in report.findings if finding.code in CONDITION_CODES]


def only_condition(report):
    [finding] = condition_findings(report)
    return finding


# ------------------------------------------------------------ invalid selectors


def test_missing_artifact_is_reported_with_fixed_text(tmp_path):
    root = write_experiment(
        tmp_path,
        make_doc(runs=[run("run-a", dataset="ghost")]),
    )
    loaded = load_manifest(root)

    [finding] = check_conditions(loaded.manifest)
    assert finding.code is Code.condition_reference_missing
    assert finding.severity is Severity.error
    assert finding.subject is None
    assert finding.path is None
    assert finding.message == (
        "A run condition selector refers to an artifact that is not in the manifest."
    )
    assert finding.hint == "Add the artifact to the manifest or correct the selector."

    # The same finding reaches the shared report, not just the direct check.
    assert only_condition(run_checks(loaded)) == finding


@pytest.mark.parametrize("kind", ["config", None])
def test_wrong_kind_artifact_is_reported(tmp_path, kind):
    root = write_experiment(
        tmp_path,
        make_doc(
            runs=[run("run-a", dataset="data")],
            artifacts=[file_artifact("data", "data.csv", kind=kind)],
            relationships=[input_to("data", "run-a")],
        ),
    )

    finding = only_condition(run_checks(load_manifest(root)))
    assert finding.code is Code.condition_reference_wrong_kind
    assert finding.severity is Severity.error
    assert finding.subject is None
    assert finding.path is None


@pytest.mark.parametrize(
    ("selector", "kind"),
    [("dataset", "dataset"), ("checkpoint", "checkpoint")],
)
def test_directory_artifact_is_unsupported(tmp_path, selector, kind):
    root = write_experiment(
        tmp_path,
        make_doc(
            runs=[run("run-a", **{selector: "dir-artifact"})],
            artifacts=[directory_artifact("dir-artifact", "dir-artifact", kind=kind)],
            relationships=[input_to("dir-artifact", "run-a")],
        ),
    )

    finding = only_condition(run_checks(load_manifest(root)))
    assert finding.code is Code.condition_reference_unsupported
    assert finding.severity is Severity.error
    assert finding.subject is None
    assert finding.path is None


def test_artifact_not_consumed_by_the_run_is_reported(tmp_path):
    root = write_experiment(
        tmp_path,
        make_doc(
            runs=[run("run-a", dataset="data")],
            artifacts=[file_artifact("data", "data.csv")],
        ),
    )

    finding = only_condition(run_checks(load_manifest(root)))
    assert finding.code is Code.condition_reference_not_consumed
    assert finding.severity is Severity.error
    assert finding.subject is None
    assert finding.path is None


def test_produced_by_or_other_run_attachment_does_not_count_as_consumed(tmp_path):
    root = write_experiment(
        tmp_path,
        make_doc(
            runs=[run("run-a", dataset="data"), run("run-b")],
            artifacts=[file_artifact("data", "data.csv", run_id="run-a")],
            relationships=[input_to("data", "run-b")],
        ),
    )

    finding = only_condition(run_checks(load_manifest(root)))
    assert finding.code is Code.condition_reference_not_consumed


@pytest.mark.parametrize(
    ("field", "artifact_id", "kind"),
    [
        ("dataset", "checkpoint", "checkpoint"),
        ("checkpoint", "data", "dataset"),
    ],
)
def test_each_selector_requires_its_own_kind(tmp_path, field, artifact_id, kind):
    root = write_experiment(
        tmp_path,
        make_doc(
            runs=[run("run-a", **{field: artifact_id})],
            artifacts=[file_artifact(artifact_id, "artifact.bin", kind=kind)],
            relationships=[input_to(artifact_id, "run-a")],
        ),
    )

    finding = only_condition(run_checks(load_manifest(root)))
    assert finding.code is Code.condition_reference_wrong_kind


# -------------------------------------------------------------- valid selectors


def test_both_selectors_valid_have_no_findings(tmp_path):
    root = write_experiment(
        tmp_path,
        make_doc(
            runs=[run("run-a", dataset="data", checkpoint="checkpoint")],
            artifacts=[
                file_artifact("data", "data.csv"),
                file_artifact("checkpoint", "checkpoint.pt", kind="checkpoint"),
            ],
            relationships=[
                input_to("data", "run-a"),
                input_to("checkpoint", "run-a"),
            ],
        ),
    )
    loaded = load_manifest(root)

    assert check_conditions(loaded.manifest) == []
    assert run_checks(loaded).findings == []


def test_duplicate_input_to_edges_still_count_as_consumed(tmp_path):
    root = write_experiment(
        tmp_path,
        make_doc(
            runs=[run("run-a", dataset="data")],
            artifacts=[file_artifact("data", "data.csv")],
            relationships=[
                input_to("data", "run-a"),
                input_to("data", "run-a"),
                input_to("data", "run-a"),
            ],
        ),
    )

    assert check_conditions(load_manifest(root).manifest) == []


# ------------------------------------------------------- scope and multiple runs


def test_unselected_run_selectors_are_checked(tmp_path):
    # Nothing compares these runs; validate must still resolve both selectors.
    root = write_experiment(
        tmp_path,
        make_doc(
            runs=[
                run("run-a", dataset="data", checkpoint="checkpoint"),
                run("run-b", dataset="ghost"),
            ],
            artifacts=[
                file_artifact("data", "data.csv"),
                file_artifact("checkpoint", "checkpoint.pt", kind="checkpoint"),
            ],
            relationships=[
                input_to("data", "run-a"),
                input_to("checkpoint", "run-a"),
            ],
        ),
    )

    [finding] = check_conditions(load_manifest(root).manifest)
    assert finding.code is Code.condition_reference_missing


def test_multiple_runs_and_selectors_report_one_finding_each(tmp_path):
    root = write_experiment(
        tmp_path,
        make_doc(
            runs=[
                run("run-a", dataset="ghost", checkpoint="checkpoint"),
                run("run-b", dataset="data"),
            ],
            artifacts=[
                file_artifact("data", "data.csv", kind="config"),
                file_artifact("checkpoint", "checkpoint.pt", kind="checkpoint"),
            ],
            relationships=[
                input_to("checkpoint", "run-a"),
                input_to("data", "run-b"),
            ],
        ),
    )

    findings = check_conditions(load_manifest(root).manifest)
    assert [finding.code for finding in findings] == [
        Code.condition_reference_missing,
        Code.condition_reference_wrong_kind,
    ]
    assert all(finding.severity is Severity.error for finding in findings)


def test_condition_findings_merge_and_sort_with_existing_findings(tmp_path):
    root = write_experiment(
        tmp_path,
        make_doc(
            runs=[run("run-a", dataset="data"), run("run-b", dataset="ghost")],
            artifacts=[
                file_artifact("data", "data.csv"),
                # no sha256 -> existing warning
                {"id": "loose", "path": "loose.bin", "kind": "dataset"},
            ],
            relationships=[input_to("data", "run-a")],
        ),
    )

    report = run_checks(load_manifest(root))
    assert [finding.code for finding in report.findings] == [
        Code.condition_reference_missing,
        Code.no_digest,
    ]


# ------------------------------------------------------------------ CLI surface


def test_validate_and_inspect_surface_the_condition_finding(tmp_path, capsys):
    root = write_experiment(
        tmp_path,
        make_doc(
            runs=[run("run-a", dataset="data")],
            artifacts=[file_artifact("data", "data.csv")],
        ),
    )

    assert main(["validate", str(root), "--json"]) == 1
    validated = json.loads(capsys.readouterr().out)
    assert [finding["code"] for finding in validated["findings"]] == [
        "condition-reference-not-consumed"
    ]
    assert validated["counts"]["error"] == 1

    assert main(["inspect", str(root), "--json"]) == 1
    inspected = json.loads(capsys.readouterr().out)
    assert [finding["code"] for finding in inspected["findings"]] == [
        "condition-reference-not-consumed"
    ]
    assert inspected["counts"]["error"] == 1


def test_valid_manifest_still_exits_zero(tmp_path, capsys):
    root = write_experiment(
        tmp_path,
        make_doc(
            runs=[run("run-a", dataset="data", checkpoint="checkpoint")],
            artifacts=[
                file_artifact("data", "data.csv"),
                file_artifact("checkpoint", "checkpoint.pt", kind="checkpoint"),
            ],
            relationships=[
                input_to("data", "run-a"),
                input_to("checkpoint", "run-a"),
            ],
        ),
    )

    assert main(["validate", str(root), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["findings"] == []
    assert report["exit_code"] == 0


def test_validate_and_inspect_json_do_not_leak_raw_ids_or_paths(tmp_path, capsys):
    marker = "private_selector_8642"
    root = write_experiment(
        tmp_path,
        make_doc(
            runs=[run(f"run-{marker}", dataset=f"artifact-{marker}")],
            artifacts=[
                file_artifact(
                    f"artifact-{marker}",
                    f"secret-{marker}/data.csv",
                    kind="config",
                    metadata={"marker": marker},
                )
            ],
            relationships=[input_to(f"artifact-{marker}", f"run-{marker}")],
        ),
    )

    assert main(["validate", str(root), "--json"]) == 1
    validated = json.loads(capsys.readouterr().out)
    assert [finding["code"] for finding in validated["findings"]] == [
        "condition-reference-wrong-kind"
    ]
    assert marker not in json.dumps(validated["findings"])

    assert main(["inspect", str(root), "--json"]) == 1
    inspected = json.loads(capsys.readouterr().out)
    assert [finding["code"] for finding in inspected["findings"]] == [
        "condition-reference-wrong-kind"
    ]
    assert marker not in json.dumps(inspected["findings"])


# --------------------------------------------------------- existing examples


def test_schema_0_1_example_has_no_condition_findings():
    root = REPO / "examples" / "comparability-recorded" / "baseline.json"
    loaded = load_manifest(root)

    assert check_conditions(loaded.manifest) == []
    assert run_checks(loaded).findings == []


@pytest.mark.parametrize(
    "name",
    [
        "baseline.json",
        "target-comparable.json",
        "target-drifted.json",
        "target-insufficient.json",
    ],
)
def test_schema_0_2_example_still_validates_clean(name):
    root = REPO / "examples" / "comparability-recorded-0.2" / name
    loaded = load_manifest(root)

    assert check_conditions(loaded.manifest) == []
    assert run_checks(loaded).findings == []
