"""RE-10: validate verifies a locator-less MLflow metric summary against its file."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from runevidence.adapters import Summary
from runevidence.cli import main
from runevidence.models import MANIFEST_FILENAME

METRIC = b"1700000000000 0.5 3\n"


def make_manifest(
    root: Path,
    *,
    value: float = 0.5,
    step: int = 3,
    timestamp: int = 1700000000000,
    volatile: bool = False,
    claim: bool = False,
    run_metadata: dict | None = None,
) -> Path:
    rel = "mlruns/1/abc/metrics/acc"
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(METRIC)
    artifact: dict = {
        "id": "f",
        "path": rel,
        "sha256": hashlib.sha256(METRIC).hexdigest(),
        "size_bytes": len(METRIC),
        "kind": "metrics",
        "run_id": "r",
    }
    if volatile:
        artifact.update(volatile=True, sha256=None, size_bytes=None)
    doc: dict = {
        "schema_version": "0.1",
        "project": {"id": "p", "name": "p"},
        "runs": [{"id": "r", "metadata": run_metadata or {"mlflow": {}}}],
        "artifacts": [artifact],
        "results": [
            {
                "id": "m",
                "run_id": "r",
                "name": "acc",
                "value": value,
                "artifact_id": "f",
                "metadata": {"step": step, "timestamp_ms": timestamp},
            }
        ],
    }
    if claim:
        doc["evidence"] = [
            {
                "id": "claim-acc",
                "claim": "accuracy is 0.5",
                "supports": ["m"],
                "contradicts": [],
                "source": "README.md",
                "metadata": {},
            }
        ]
    (root / MANIFEST_FILENAME).write_text(json.dumps(doc), encoding="utf-8")
    return root


def test_matching_summary_is_clean(tmp_path, capsys):
    root = make_manifest(tmp_path)
    assert main(["validate", str(root), "--strict"]) == 0
    assert "0 errors, 0 warnings" in capsys.readouterr().out


def test_value_mismatch_is_an_error_and_puts_the_claim_at_risk(tmp_path, capsys):
    root = make_manifest(tmp_path, value=0.1, claim=True)
    assert main(["validate", str(root), "--strict"]) == 1
    out = capsys.readouterr().out
    assert "result-value-mismatch [m]" in out
    assert "records 0.1 at step 3" in out and "contains 0.5 at step 3" in out
    assert "claim-at-risk [claim-acc]" in out


def test_step_only_mismatch_is_an_error(tmp_path, capsys):
    root = make_manifest(tmp_path, step=2)
    assert main(["validate", str(root), "--strict"]) == 1
    assert "result-value-mismatch [m]" in capsys.readouterr().out


def test_timestamp_only_mismatch_is_an_error(tmp_path, capsys):
    root = make_manifest(tmp_path, timestamp=1)
    assert main(["validate", str(root), "--strict"]) == 1
    assert "result-value-mismatch [m]" in capsys.readouterr().out


def test_volatile_summary_warns_and_strict_fails(tmp_path, capsys):
    root = make_manifest(tmp_path, volatile=True)
    assert main(["validate", str(root)]) == 0
    out = capsys.readouterr().out
    assert "result-unverified [m]" in out
    assert "volatile" in out
    assert main(["validate", str(root), "--strict"]) == 1


def test_adapter_absent_warns_instead_of_passing_silently(tmp_path, capsys, monkeypatch):
    root = make_manifest(tmp_path, value=0.1)
    monkeypatch.setattr("runevidence.summaries.summary_claimants", lambda *a, **k: ([], "absent"))
    assert main(["validate", str(root)]) == 0
    out = capsys.readouterr().out
    assert "result-unverified [m]" in out
    assert "no installed adapter can verify it" in out
    assert main(["validate", str(root), "--strict"]) == 1


class _RaisingOwner:
    def read_summary(self, data):
        raise ZeroDivisionError


class _MalformedOwner:
    def __init__(self, summary):
        self.summary = summary

    def read_summary(self, data):
        return self.summary


class _ExplodingValue:
    @property
    def value(self):
        raise RuntimeError("boom")

    step = 3
    timestamp = 1


@pytest.mark.parametrize(
    "owner",
    [
        _RaisingOwner(),
        _MalformedOwner(Summary("0.9", 3, 1)),
        _MalformedOwner(Summary(float("nan"), 3, 1)),
        _MalformedOwner(Summary(10**400, 3, 1)),
        _MalformedOwner(Summary(0.9, 10**5000, 1)),
        _MalformedOwner(Summary(0.9, True, 1)),
        _MalformedOwner(_ExplodingValue()),
        _MalformedOwner({"value": 0.9, "step": 3, "timestamp": 1}),
    ],
    ids=["raises", "string-value", "nan", "huge-int", "huge-step", "bool-step", "raising-property", "dict"],
)
def test_a_broken_adapter_warns_instead_of_crashing(tmp_path, capsys, monkeypatch, owner):
    root = make_manifest(tmp_path, value=0.1)
    monkeypatch.setattr("runevidence.summaries.summary_claimants", lambda *a, **k: ([owner], "ok"))
    assert main(["validate", str(root)]) == 0
    assert "result-unverified [m]" in capsys.readouterr().out
    assert main(["validate", str(root), "--strict"]) == 1


class _ReadOnceValue:
    def __init__(self):
        self.calls = 0

    @property
    def value(self):
        self.calls += 1
        if self.calls > 1:
            raise RuntimeError("read twice")
        return 0.5

    step = 3
    timestamp = 1700000000000


def test_the_summary_attributes_are_read_only_once(tmp_path, capsys, monkeypatch):
    root = make_manifest(tmp_path)
    owner = _MalformedOwner(_ReadOnceValue())
    monkeypatch.setattr("runevidence.summaries.summary_claimants", lambda *a, **k: ([owner], "ok"))

    assert main(["validate", str(root), "--strict"]) == 0
    assert "0 errors, 0 warnings" in capsys.readouterr().out


def test_two_claimants_warn(tmp_path, capsys, monkeypatch):
    root = make_manifest(tmp_path, value=0.1)

    class First:
        pass

    class Second:
        pass

    monkeypatch.setattr(
        "runevidence.summaries.summary_claimants", lambda *a, **k: ([First(), Second()], "ambiguous")
    )
    assert main(["validate", str(root)]) == 0
    assert "more than one installed adapter" in capsys.readouterr().out


def test_unreadable_history_warns(tmp_path, capsys):
    root = make_manifest(
        tmp_path,
        run_metadata={
            "mlflow": {
                "unreadable": ["mlruns/1/abc/metrics/loss"],
                "unreadable_reasons": {"mlruns/1/abc/metrics/loss": "over-cap"},
            }
        },
    )
    assert main(["validate", str(root)]) == 0
    out = capsys.readouterr().out
    assert "was not recorded as a result (over-cap)" in out
    assert main(["validate", str(root), "--strict"]) == 1


def test_digest_race_is_unverifiable(tmp_path, capsys, monkeypatch):
    root = make_manifest(tmp_path, value=0.1)
    monkeypatch.setattr(
        "runevidence.summaries.read_bounded",
        lambda *a, **k: (b"1 0.5 3\n", "ok"),
    )
    assert main(["validate", str(root)]) == 0
    out = capsys.readouterr().out
    assert "changed while it was being verified" in out
    assert main(["validate", str(root), "--strict"]) == 1


def test_project_level_unreadable_history_warns(tmp_path, capsys):
    root = make_manifest(tmp_path)
    doc = json.loads((root / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    doc["project"]["metadata"] = {
        "mlflow": {
            "unreadable": ["mlruns/1/abc/metrics/loss"],
            "unreadable_reasons": {"mlruns/1/abc/metrics/loss": "over-cap"},
        }
    }
    (root / MANIFEST_FILENAME).write_text(json.dumps(doc), encoding="utf-8")

    assert main(["validate", str(root)]) == 0
    out = capsys.readouterr().out
    assert "result-unverified" in out
    assert "was not recorded as a result (over-cap)" in out
    assert main(["validate", str(root), "--strict"]) == 1


def test_changed_file_reports_the_hash_before_the_summary(tmp_path, capsys):
    root = make_manifest(tmp_path, value=0.1)
    with (root / "mlruns/1/abc/metrics/acc").open("ab") as handle:
        handle.write(b"1700000001000 0.75 4\n")
    assert main(["validate", str(root), "--strict"]) == 1
    out = capsys.readouterr().out
    assert "hash-mismatch [f]" in out
    assert "result-value-mismatch" not in out
