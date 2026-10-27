"""Regressions for bugs found by dogfooding on a Hydra-style learning-rate
sweep with a preprocessing stage and a diverged job
(examples/dogfood-mlp-sweep, 2026-10-25)."""

import json
import math
import shutil
from pathlib import Path

import pytest

from runevidence.checks import run_checks
from runevidence.checks.results import values_match
from runevidence.cli import main
from runevidence.findings import Code
from runevidence.manifest import load_manifest

from conftest import REPO

DOGFOOD = REPO / "examples" / "dogfood-mlp-sweep"
MANIFEST = "runevidence.json"


@pytest.fixture
def sweep(tmp_path: Path) -> Path:
    dest = tmp_path / "sweep"
    shutil.copytree(DOGFOOD, dest)
    return dest


def job_dir(root: Path, job: int) -> Path:
    [sweep_dir] = (root / "multirun").glob("*/*")
    return sweep_dir / str(job)


def at_risk(root: Path) -> dict[str, str]:
    report = run_checks(load_manifest(root))
    return {f.subject: f.message for f in report.findings if f.code is Code.claim_at_risk}


def edit(root: Path, fn) -> None:
    path = root / MANIFEST
    doc = json.loads(path.read_text(encoding="utf-8"))
    fn(doc)
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def test_committed_mlp_dogfood_example_is_clean():
    # Includes a result whose recorded and on-disk value are both NaN.
    report = run_checks(load_manifest(DOGFOOD), include_untracked=True)
    assert report.findings == []


# B6: a diverged run's NaN metric was reported as a value mismatch.


@pytest.mark.parametrize(
    "recorded, actual, expected",
    [
        (math.nan, math.nan, True),
        (math.nan, 1.0, False),
        (1.0, math.nan, False),
        (math.nan, None, False),
        (math.inf, math.inf, True),
        (math.inf, -math.inf, False),
        (0.1 + 0.2, 0.3, True),
    ],
)
def test_values_match_handles_non_finite_floats(recorded, actual, expected):
    assert values_match(recorded, actual) is expected


def test_nan_result_still_detects_a_real_change(sweep):
    metrics = job_dir(sweep, 2) / "metrics.json"
    metrics.write_text(metrics.read_text(encoding="utf-8").replace("NaN", "1.5"), encoding="utf-8")
    report = run_checks(load_manifest(sweep))
    [mismatch] = [f for f in report.findings if f.code is Code.result_value_mismatch]
    assert mismatch.subject == "job2-final-loss"


# B7: a change two lineage steps upstream (raw data -> processed data ->
# training run) did not put claims about the trained models at risk.


def test_raw_data_change_puts_downstream_claims_at_risk(sweep):
    raw = sweep / "data" / "raw" / "diabetes.csv"
    raw.write_bytes(raw.read_bytes().replace(b"\n59,", b"\n60,", 1))
    risks = at_risk(sweep)
    assert set(risks) == {"claim-best-lr", "claim-lr-diverges"}
    assert "data-raw-diabetes.csv (via " in risks["claim-best-lr"]


def test_direct_dependencies_print_without_a_chain(sweep):
    metrics = job_dir(sweep, 1) / "metrics.json"
    metrics.write_text(metrics.read_text(encoding="utf-8").replace("44.62", "43.1"), encoding="utf-8")
    message = at_risk(sweep)["claim-best-lr"]
    assert "via" not in message
    assert "job1-val-rmse" in message


def test_upstream_of_another_job_does_not_leak_into_unrelated_claim(sweep):
    # job0's metrics feed the sweep summary that claim-best-lr cites, but
    # nothing claim-lr-diverges rests on.
    (job_dir(sweep, 0) / "metrics.json").unlink()
    assert set(at_risk(sweep)) == {"claim-best-lr"}


def test_derivation_cycle_does_not_hang_the_lineage_walk(sweep):
    def cycle(doc):
        doc["relationships"].append(
            {"source": "data-raw-diabetes.csv", "target": "data-processed-train.csv", "type": "derived_from"}
        )

    edit(sweep, cycle)
    raw = sweep / "data" / "raw" / "diabetes.csv"
    raw.write_bytes(raw.read_bytes() + b"\n")
    report = run_checks(load_manifest(sweep))
    found = {f.code for f in report.findings}
    assert Code.derivation_cycle in found
    assert Code.claim_at_risk in found


def test_run_derived_from_run_carries_its_inputs(experiment):
    # generic-experiment: tuned is fine-tuned from baseline, whose train data breaks.
    def lineage(doc):
        doc["relationships"] = [
            r for r in doc["relationships"] if not (r["type"] == "input_to" and r["target"] == "run-tuned")
        ]
        doc["relationships"].append({"source": "run-tuned", "target": "run-baseline", "type": "derived_from"})
        doc["evidence"] = [e for e in doc["evidence"] if e["id"] == "claim-tuned-perfect"]

    edit(experiment, lineage)
    (experiment / "data" / "train.csv").write_text("changed\n", encoding="utf-8")
    message = at_risk(experiment)["claim-tuned-perfect"]
    assert "train-data (via " in message
    assert "run-baseline" in message


# B8: init --force warned only about results, claims and relationships, but
# also dropped hand-written runs, artifact annotations and project details.


def test_init_force_reports_every_kind_of_hand_written_data(sweep, capsys):
    assert main(["init", str(sweep), "--force"]) == 0
    out = capsys.readouterr().out
    assert "6 results, 2 claims, 20 relationships, 5 runs" in out
    assert "run_id/kind/metadata on 32 artifacts" in out
    assert "project description" in out


def test_init_force_notes_artifacts_whose_id_changes(tmp_path, capsys):
    root = tmp_path / "sk"
    shutil.copytree(REPO / "examples" / "dogfood-sklearn", root)
    assert main(["init", str(root), "--force"]) == 0
    assert "'report' is now 'REPORT.md'" in capsys.readouterr().out


def test_init_force_reports_edited_params_on_surviving_runs(tmp_path, capsys):
    (tmp_path / "runs" / "a").mkdir(parents=True)
    (tmp_path / "runs" / "a" / "m.json").write_text("{}", encoding="utf-8")
    assert main(["init", str(tmp_path)]) == 0
    edit(tmp_path, lambda doc: doc["runs"][0]["params"].update(lr=0.1))
    capsys.readouterr()
    assert main(["init", str(tmp_path), "--force"]) == 0
    out = capsys.readouterr().out
    assert "name/params/metadata on 1 run" in out
    assert "WARNING" in out
