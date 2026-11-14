"""One schema 0.5 load smoke per manifest-reading CLI route (slice D phase 4c).

Each manifest-reading route must accept a valid 0.5 manifest through the
dispatcher instead of failing the load (exit 2). ``fingerprint-compare`` is the
documented exception: fingerprint v1 still refuses every schema other than 0.1
through ``manifest_fingerprint``'s guard, so an otherwise valid 0.5 manifest is
a refusal, not a crash. The 26-route parser surface is unchanged.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from runevidence.cli import main
from runevidence.manifest import dump_manifest
from runevidence.models import Manifest05

import tensorboard_builder as tb

EVENT = tb.records(
    tb.event(file_version=b"brain.Event:2"),
    tb.scalar_event(b"score", 0.5, step=1),
)
TAG = "score"


def document() -> dict:
    digest = hashlib.sha256(EVENT).hexdigest()
    locator = {"kind": "tensorboard", "tag": TAG, "step": 1, "occurrence": 0}
    return {
        "schema_version": "0.5",
        "project": {"id": "proj", "name": "Proj"},
        "runs": [{"id": "run-a", "name": "A"}, {"id": "run-b", "name": "B"}],
        "artifacts": [
            {
                "id": "art",
                "path": "events.tfevents.1",
                "sha256": digest,
                "size_bytes": len(EVENT),
            }
        ],
        "results": [
            {
                "id": "ra",
                "run_id": "run-a",
                "name": "score",
                "value": 0.5,
                "artifact_id": "art",
                "locator": locator,
            },
            {
                "id": "rb",
                "run_id": "run-b",
                "name": "score",
                "value": 0.5,
                "artifact_id": "art",
                "locator": dict(locator),
            },
        ],
        "evidence": [
            {"id": "claim", "claim": "claim text", "supports": ["ra"]}
        ],
    }


def experiment(tmp_path, name: str = "exp") -> str:
    folder = tmp_path / name
    folder.mkdir()
    (folder / "events.tfevents.1").write_bytes(EVENT)
    (folder / "runevidence.json").write_text(
        dump_manifest(Manifest05.model_validate(document())),
        encoding="utf-8",
        newline="\n",
    )
    return str(folder)


def call(argv, capsys) -> tuple[int, str]:
    code = main(argv)
    captured = capsys.readouterr()
    assert captured.err == ""
    return code, captured.out


def test_minimal_05_manifest_is_clean(tmp_path, capsys):
    folder = experiment(tmp_path)
    code, output = call(["validate", folder, "--json"], capsys)
    assert code == 0
    payload = json.loads(output)
    assert payload["ok"] is True
    assert payload["findings"] == []


@pytest.mark.parametrize(
    "route,extra",
    [
        ("validate", []),
        ("inspect", []),
        ("compare", ["--base", "run-a", "--target", "run-b"]),
        ("leaderboard", ["--metric", "score", "--direction", "max"]),
        ("matrix", []),
        ("impact", ["--id", "art"]),
        ("coverage", []),
        ("coverage-gate", []),
        ("coverage-diff", None),
        ("review-diff", None),
        ("impact-diff", None),
        ("review-gate", None),
    ],
)
def test_manifest_reading_routes_load_05(tmp_path, capsys, route, extra):
    folder = experiment(tmp_path, "before")
    if extra is None:
        argv = [route, folder, experiment(tmp_path, "after"), "--json"]
    else:
        argv = [route, folder, *extra, "--json"]
    code, output = call(argv, capsys)
    assert code == 0
    # A load failure would be exit 2 and this fixed JSON error shape.
    payload = json.loads(output)
    assert payload.get("ok") is not False
    assert "error" not in payload


def test_fingerprint_compare_refuses_05_through_the_01_guard(tmp_path, capsys):
    before = experiment(tmp_path, "before")
    after = experiment(tmp_path, "after")
    code, output = call(
        ["fingerprint-compare", before, after, "--json"], capsys
    )
    assert code == 2
    payload = json.loads(output)
    assert payload["ok"] is False
    assert payload["exit_code"] == 2
    assert (
        payload["error"]
        == "fingerprint v1 only accepts manifest schema 0.1; "
        "explicit version decision required"
    )
